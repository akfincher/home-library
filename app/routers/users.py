# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Aaron Fincher. See LICENSE.
"""Managing accounts. Owner (admin) accounts only."""
import sqlite3
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import auth, db
from ..changelog import log
from .people import ensure_person

router = APIRouter(prefix="/api/users", dependencies=[Depends(auth.require_owner)])


class NewUser(BaseModel):
    username: str = Field(max_length=100)
    password: str = Field(max_length=500)
    role: Literal["admin", "member"] = "member"
    person_id: int | None = None


class UserPatch(BaseModel):
    role: Literal["admin", "member"] | None = None
    person_id: int | None = None
    password: str | None = Field(None, max_length=500)


def _owners_left_without(uid: int) -> int:
    return db.one("SELECT COUNT(*) AS n FROM users WHERE role='admin' AND id!=?", (uid,))["n"]


@router.get("")
def users():
    return db.rows("SELECT u.id, u.username, u.role, u.person_id, p.name AS person FROM users u "
                   "LEFT JOIN people p ON p.id=u.person_id ORDER BY u.username COLLATE NOCASE")


@router.post("")
def add_user(b: NewUser):
    username = auth.clean_username(b.username)
    auth.validate_password(b.password)
    ensure_person(b.person_id)
    try:
        uid = db.run("INSERT INTO users(username, password, role, person_id) VALUES(?,?,?,?)",
                     (username, auth.hash_password(b.password), b.role, b.person_id)).lastrowid
    except sqlite3.IntegrityError:
        raise HTTPException(400, "That username is already taken.")
    log(f"Added the {'owner' if b.role == 'admin' else 'member'} account {username}", admin=True)
    return {"id": uid}


@router.patch("/{uid}")
def update_user(uid: int, b: UserPatch):
    """Change someone's role or linked person, or set them a new password
    (which logs them out everywhere)."""
    old = db.one("SELECT * FROM users WHERE id=?", (uid,))
    if not old:
        raise HTTPException(404, "Account not found")
    sets = b.model_dump(exclude_unset=True)
    if sets.get("role") == "member" and not _owners_left_without(uid):
        raise HTTPException(400, "The library needs at least one owner account.")
    if "person_id" in sets:
        ensure_person(sets["person_id"])
    if sets.get("password") is not None:
        auth.validate_password(sets["password"])
        sets["password"] = auth.hash_password(sets["password"])
        auth.end_all_sessions(uid)
    else:
        sets.pop("password", None)
    if "role" in sets and sets["role"] is None:
        sets.pop("role")
    if sets:
        db.run(f"UPDATE users SET {', '.join(k + '=?' for k in sets)} WHERE id=?", [*sets.values(), uid])
    what = [w for k, w in (("role", "account type"), ("person_id", "linked person")) if k in sets and sets[k] != old[k]]
    if "password" in sets:
        what.append("password")
    if what:
        log(f"Changed {old['username']}'s {' and '.join(what)}", admin=True)
    return {"ok": True}


@router.delete("/{uid}")
def delete_user(uid: int, me: dict = Depends(auth.current_user)):
    if uid == me["id"]:
        raise HTTPException(400, "You can't remove your own account.")
    if not _owners_left_without(uid) and db.one("SELECT 1 FROM users WHERE id=? AND role='admin'", (uid,)):
        raise HTTPException(400, "The library needs at least one owner account.")
    old = db.one("SELECT username FROM users WHERE id=?", (uid,))
    if not old:
        raise HTTPException(404, "Account not found")
    auth.end_all_sessions(uid)
    db.run("DELETE FROM users WHERE id=?", (uid,))
    log(f"Removed the account {old['username']}", admin=True)
    return {"ok": True}
