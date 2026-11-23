# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Aaron Fincher. See LICENSE.
"""For the server's administrator: every account on the server, which libraries
each can use and how, password resets, and the maintenance notice."""
import sqlite3
import time
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import auth, context, db
from . import invites

router = APIRouter(prefix="/api/server", dependencies=[Depends(auth.require_site_admin)])


Role = Literal["admin", "member", "viewer"]


class AccountPatch(BaseModel):
    can_invite: bool | None = None
    email: str | None = Field(None, max_length=200)


class NewAccount(BaseModel):
    username: str = Field(max_length=100)
    password: str = Field("", max_length=500)  # blank: the app makes up an 8-character one
    email: str = Field("", max_length=200)
    own_library: bool = True  # give them a library of their own
    join_current: bool = False  # add them to the library you're working in
    library_id: int | None = None  # or to any library on the server
    role: Role = "member"  # their role in that library
    can_invite: bool = False


def _email(raw: str, uid: int | None = None) -> str:
    email = (raw or "").strip().lower()
    if email and not invites.EMAIL.match(email):
        raise HTTPException(400, "That doesn't look like an email address.")
    if email and db.one("SELECT 1 FROM users WHERE email=? AND id IS NOT ?", (email, uid)):
        raise HTTPException(400, "Someone else already uses that email address.")
    return email


def _library(lid: int) -> dict:
    row = db.one("SELECT * FROM libraries WHERE id=?", (lid,))
    if not row:
        raise HTTPException(404, "Library not found")
    return row


def _account(uid: int) -> dict:
    row = db.one("SELECT * FROM users WHERE id=?", (uid,))
    if not row:
        raise HTTPException(404, "Account not found")
    return row


def _other_owners(lid: int, uid: int) -> int:
    return db.one("SELECT COUNT(*) AS n FROM memberships WHERE library_id=? AND role='admin' AND user_id!=?",
                  (lid, uid))["n"]


@router.post("/accounts")
def add_account(b: NewAccount):
    """Creates an account directly, without an invitation. Its password (made up
    by the app unless one is given) has to be changed at the first login."""
    username = auth.clean_username(b.username)
    password = b.password or auth.temporary_password()
    auth.validate_password(password)
    email = _email(b.email)
    join = b.library_id or (context.lib() if b.join_current else None)
    if join:
        _library(join)
    if not (b.own_library or join):
        raise HTTPException(400, "Give them their own library, or add them to an existing one (or both).")
    try:
        uid = db.run("INSERT INTO users(username, password, role, email, can_invite, must_change) "
                     "VALUES(?,?,'member',?,?,1)",
                     (username, auth.hash_password(password), email, int(b.can_invite))).lastrowid
    except sqlite3.IntegrityError:
        raise HTTPException(400, "That username is already taken.")
    if join:
        db.run("INSERT INTO memberships(library_id, user_id, role) VALUES(?,?,?)", (join, uid, b.role))
    if b.own_library:
        auth.new_library(f"{username}'s Library", uid)
    return {"id": uid, "password": password}


@router.post("/accounts/{uid}/password")
def reset_password(uid: int, me: dict = Depends(auth.current_user)):
    """Gives an account a new 8-character password, which has to be changed at
    the next login. Logs it out everywhere and closes any reset request."""
    account = _account(uid)
    if uid == me["id"]:
        raise HTTPException(400, "Change your own password from your account instead.")
    return {"username": account["username"], "password": auth.set_temporary_password(uid)}


@router.get("/reset-requests")
def reset_requests():
    """People who used "Forgot password?" and are waiting for a new password."""
    return db.rows("SELECT r.id, r.user_id, r.requested_at, u.username, u.email FROM reset_requests r "
                   "JOIN users u ON u.id=r.user_id ORDER BY r.requested_at")


@router.delete("/reset-requests/{rid}")
def dismiss_request(rid: int):
    db.run("DELETE FROM reset_requests WHERE id=?", (rid,))
    return {"ok": True}


@router.get("/libraries")
def libraries():
    """Every library on the server, for giving accounts access to them."""
    return db.rows("SELECT l.id, l.name, (SELECT GROUP_CONCAT(u.username, ', ') FROM memberships m "
                   "JOIN users u ON u.id=m.user_id WHERE m.library_id=l.id AND m.role='admin') AS owners "
                   "FROM libraries l ORDER BY l.name COLLATE NOCASE")


class Access(BaseModel):
    role: Role


@router.put("/accounts/{uid}/libraries/{lid}")
def grant(uid: int, lid: int, b: Access):
    """Gives an account access to a library: owner, member, or viewer (can look
    but not change anything). Changes their role if they already have access."""
    account, library = _account(uid), _library(lid)
    current = db.one("SELECT role FROM memberships WHERE library_id=? AND user_id=?", (lid, uid))
    if current and current["role"] == "admin" and b.role != "admin" and not _other_owners(lid, uid):
        raise HTTPException(400, f"“{library['name']}” needs at least one owner.")
    db.run("INSERT INTO memberships(library_id, user_id, role) VALUES(?,?,?) "
           "ON CONFLICT(library_id, user_id) DO UPDATE SET role=excluded.role", (lid, uid, b.role))
    return {"ok": True, "username": account["username"]}


@router.delete("/accounts/{uid}/libraries/{lid}")
def revoke(uid: int, lid: int):
    library = _library(lid)
    current = db.one("SELECT role FROM memberships WHERE library_id=? AND user_id=?", (lid, uid))
    if not current:
        raise HTTPException(404, "They don't have access to that library.")
    if current["role"] == "admin" and not _other_owners(lid, uid):
        raise HTTPException(400, f"They're the only owner of “{library['name']}”. Make someone else an owner first.")
    db.run("DELETE FROM memberships WHERE library_id=? AND user_id=?", (lid, uid))
    return {"ok": True}


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
    """Every account, with the libraries it can use (and its role in each)."""
    rows = db.rows("SELECT u.id, u.username, u.email, u.site_admin, u.can_invite, u.must_change, "
                   "EXISTS(SELECT 1 FROM reset_requests r WHERE r.user_id=u.id) AS wants_reset "
                   "FROM users u ORDER BY u.username COLLATE NOCASE")
    access = db.rows("SELECT m.user_id, l.id, l.name, m.role FROM memberships m JOIN libraries l ON l.id=m.library_id "
                     "ORDER BY l.name COLLATE NOCASE")
    for r in rows:
        r["libraries"] = [{"id": a["id"], "name": a["name"], "role": a["role"]} for a in access if a["user_id"] == r["id"]]
        r["library_names"] = ", ".join(a["name"] for a in r["libraries"])
    return rows


@router.patch("/accounts/{uid}")
def update_account(uid: int, b: AccountPatch):
    """Lets an account invite people (or stops it), or changes its email address."""
    _account(uid)
    if b.can_invite is not None:
        db.run("UPDATE users SET can_invite=? WHERE id=?", (int(b.can_invite), uid))
    if b.email is not None:
        db.run("UPDATE users SET email=? WHERE id=?", (_email(b.email, uid), uid))
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
        c.execute("DELETE FROM reset_requests WHERE user_id=?", (uid,))
        c.execute("UPDATE invites SET revoked=1 WHERE created_by=? AND used_by IS NULL", (uid,))
        c.execute("DELETE FROM users WHERE id=?", (uid,))
    return {"ok": True}
