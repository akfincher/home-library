# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Aaron Fincher. See LICENSE.
"""Who belongs to the current library. Owners of the library only."""
import sqlite3
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import auth, db
from ..changelog import log
from ..context import lib
from .people import ensure_person

router = APIRouter(prefix="/api/members", dependencies=[Depends(auth.require_owner)])


class NewMember(BaseModel):
    username: str = Field(max_length=100)
    password: str = Field("", max_length=500)  # blank: add an existing account; otherwise create one
    role: Literal["admin", "member", "viewer"] = "member"
    person_id: int | None = None


class MemberPatch(BaseModel):
    role: Literal["admin", "member", "viewer"] | None = None
    person_id: int | None = None
    password: str | None = Field(None, max_length=500)


def _member(uid: int) -> dict:
    m = db.one("SELECT u.*, m.role AS lib_role, m.person_id AS lib_person FROM memberships m "
               "JOIN users u ON u.id=m.user_id WHERE m.library_id=? AND m.user_id=?", (lib(), uid))
    if not m:
        raise HTTPException(404, "That account isn't in this library")
    return m


def _other_owners(uid: int) -> int:
    return db.one("SELECT COUNT(*) AS n FROM memberships WHERE library_id=? AND role='admin' AND user_id!=?",
                  (lib(), uid))["n"]


def _only_here(uid: int) -> bool:
    """True if the account belongs to this library and no other, so this
    library's owners may set its password."""
    return db.one("SELECT COUNT(*) AS n FROM memberships WHERE user_id=?", (uid,))["n"] == 1


@router.get("")
def members():
    rows = db.rows("SELECT u.id, u.username, m.role, m.person_id, p.name AS person FROM memberships m "
                   "JOIN users u ON u.id=m.user_id LEFT JOIN people p ON p.id=m.person_id "
                   "WHERE m.library_id=? ORDER BY u.username COLLATE NOCASE", (lib(),))
    for r in rows:
        r["password_resettable"] = _only_here(r["id"])
    return rows


@router.post("")
def add_member(b: NewMember):
    """With a password, creates a new account in this library. Without one, adds
    an existing account (someone who signed up, or a member of another library)."""
    username = auth.clean_username(b.username)
    ensure_person(b.person_id)
    if b.password:
        auth.validate_password(b.password)
        try:
            uid = db.run("INSERT INTO users(username, password, role) VALUES(?,?,'member')",
                         (username, auth.hash_password(b.password))).lastrowid
        except sqlite3.IntegrityError:
            raise HTTPException(400, "That username is already taken. To add that person, leave the password blank.")
        created = True
    else:
        user = db.one("SELECT id FROM users WHERE username=?", (username,))
        if not user:
            raise HTTPException(404, "There's no account with that username. Give a password to create one.")
        uid, created = user["id"], False
    try:
        db.run("INSERT INTO memberships(library_id, user_id, role, person_id) VALUES(?,?,?,?)",
               (lib(), uid, b.role, b.person_id))
    except sqlite3.IntegrityError:
        raise HTTPException(400, "That account is already in this library.")
    role = "an owner" if b.role == "admin" else "a member"
    log(f"{'Created' if created else 'Added'} the account {username} as {role}", admin=True)
    return {"id": uid}


@router.patch("/{uid}")
def update_member(uid: int, b: MemberPatch):
    """Change someone's role or linked person here, or set a new password for an
    account that only belongs to this library (which logs it out everywhere)."""
    m = _member(uid)
    sets = {k: v for k, v in b.model_dump(exclude_unset=True).items() if not (k == "role" and v is None)}
    changes = []
    if "role" in sets and sets["role"] != m["lib_role"]:
        if sets["role"] == "member" and not _other_owners(uid):
            raise HTTPException(400, "The library needs at least one owner.")
        db.run("UPDATE memberships SET role=? WHERE library_id=? AND user_id=?", (sets["role"], lib(), uid))
        changes.append(f"made {'an owner' if sets['role'] == 'admin' else 'a member'}")
    if "person_id" in sets and sets["person_id"] != m["lib_person"]:
        ensure_person(sets["person_id"])
        db.run("UPDATE memberships SET person_id=? WHERE library_id=? AND user_id=?", (sets["person_id"], lib(), uid))
        changes.append("linked person changed")
    if sets.get("password"):
        if not _only_here(uid):
            raise HTTPException(403, f"{m['username']} also belongs to other libraries, so only they can change "
                                     "their password.")
        auth.validate_password(sets["password"])
        db.run("UPDATE users SET password=? WHERE id=?", (auth.hash_password(sets["password"]), uid))
        auth.end_all_sessions(uid)
        changes.append("password reset")
    if changes:
        log(f"Changed {m['username']}'s account: {', '.join(changes)}", admin=True)
    return {"ok": True}


@router.delete("/{uid}")
def remove_member(uid: int):
    """Takes someone out of this library. Their account stays, along with any other libraries they're in."""
    m = _member(uid)
    if m["lib_role"] == "admin" and not _other_owners(uid):
        raise HTTPException(400, "The library needs at least one owner.")
    db.run("DELETE FROM memberships WHERE library_id=? AND user_id=?", (lib(), uid))
    log(f"Removed {m['username']} from the library", admin=True)
    return {"ok": True}
