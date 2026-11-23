# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Aaron Fincher. See LICENSE.
"""For the server's administrator: every account on the server, and which of
them may create invite codes."""
import sqlite3
import time
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import auth, context, db
from . import invites

router = APIRouter(prefix="/api/server", dependencies=[Depends(auth.require_site_admin)])


class AccountPatch(BaseModel):
    can_invite: bool


class NewAccount(BaseModel):
    username: str = Field(max_length=100)
    password: str = Field(max_length=500)
    email: str = Field("", max_length=200)
    own_library: bool = True  # give them a library of their own
    join_current: bool = False  # and/or add them to the library you're working in
    role: Literal["admin", "member"] = "member"  # their role in that library
    can_invite: bool = False


@router.post("/accounts")
def add_account(b: NewAccount):
    """Creates an account directly, without an invitation."""
    username = auth.clean_username(b.username)
    auth.validate_password(b.password)
    email = b.email.strip().lower()
    if email and not invites.EMAIL.match(email):
        raise HTTPException(400, "That doesn't look like an email address.")
    if email and db.one("SELECT 1 FROM users WHERE email=?", (email,)):
        raise HTTPException(400, "Someone with that email address already has an account.")
    if not (b.own_library or b.join_current):
        raise HTTPException(400, "Give them their own library, or add them to this one (or both).")
    try:
        uid = db.run("INSERT INTO users(username, password, role, email, can_invite) VALUES(?,?,'member',?,?)",
                     (username, auth.hash_password(b.password), email, int(b.can_invite))).lastrowid
    except sqlite3.IntegrityError:
        raise HTTPException(400, "That username is already taken.")
    if b.join_current:
        db.run("INSERT INTO memberships(library_id, user_id, role) VALUES(?,?,?)", (context.lib(), uid, b.role))
    if b.own_library:
        auth.new_library(f"{username}'s Library", uid)
    return {"id": uid}


class Notice(BaseModel):
    message: str = Field("", max_length=500)
    until: float | None = None  # when to stop showing it (seconds since 1970); None means until cleared


def current_notice() -> dict | None:
    """The maintenance notice everyone sees, if one is up and hasn't expired."""
    message = db.get_setting("notice") or ""
    until = float(db.get_setting("notice_until") or 0) or None
    if not message or (until and until < time.time()):
        return None
    return {"message": message, "until": until, "id": db.get_setting("notice_id") or ""}


@router.put("/notice")
def set_notice(b: Notice):
    """Puts up (or, with an empty message, takes down) a notice shown to everyone
    on the server, for example before an update."""
    db.put_setting("notice", b.message.strip())
    db.put_setting("notice_until", str(b.until or ""))
    db.put_setting("notice_id", str(time.time()))  # a new notice shows again even to people who hid the last one
    return {"notice": current_notice()}


@router.get("/accounts")
def accounts():
    return db.rows("SELECT u.id, u.username, u.site_admin, u.can_invite, COUNT(m.library_id) AS libraries, "
                   "GROUP_CONCAT(l.name, ', ') AS library_names FROM users u "
                   "LEFT JOIN memberships m ON m.user_id=u.id LEFT JOIN libraries l ON l.id=m.library_id "
                   "GROUP BY u.id ORDER BY u.username COLLATE NOCASE")


@router.patch("/accounts/{uid}")
def update_account(uid: int, b: AccountPatch):
    """Lets an account create invite codes, or stops it."""
    if not db.run("UPDATE users SET can_invite=? WHERE id=?", (int(b.can_invite), uid)).rowcount:
        raise HTTPException(404, "Account not found")
    return {"ok": True}


@router.delete("/accounts/{uid}")
def delete_account(uid: int, me: dict = Depends(auth.current_user)):
    """Deletes an account. Not allowed while it's the only owner of a library, so
    no library is left without someone to look after it."""
    if uid == me["id"]:
        raise HTTPException(400, "You can't delete your own account.")
    if not db.one("SELECT 1 FROM users WHERE id=?", (uid,)):
        raise HTTPException(404, "Account not found")
    sole = db.rows("SELECT l.name FROM memberships m JOIN libraries l ON l.id=m.library_id "
                   "WHERE m.user_id=? AND m.role='admin' AND NOT EXISTS (SELECT 1 FROM memberships o "
                   "WHERE o.library_id=m.library_id AND o.role='admin' AND o.user_id!=m.user_id)", (uid,))
    if sole:
        raise HTTPException(400, "They're the only owner of " + ", ".join(f"“{s['name']}”" for s in sole) +
                            ". Add another owner there first, or have them delete it.")
    auth.end_all_sessions(uid)
    with db.connect() as c:
        c.execute("DELETE FROM memberships WHERE user_id=?", (uid,))
        c.execute("DELETE FROM reads WHERE user_id=?", (uid,))
        c.execute("UPDATE invites SET revoked=1 WHERE created_by=? AND used_by IS NULL", (uid,))
        c.execute("DELETE FROM users WHERE id=?", (uid,))
    return {"ok": True}
