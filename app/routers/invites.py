# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Aaron Fincher. See LICENSE.
"""Invitations: the only way to sign up. The server's administrator, and the
accounts they allow to invite people, invite someone by email address. That
makes a personal sign-up link, sent from the inviter's own email app. Each link
works once, expires after INVITE_DAYS, and can be cancelled. Only a hash of the
secret in each link is stored."""
import hashlib
import re
import secrets
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import auth, db
from ..security import Limiter

router = APIRouter(prefix="/api/invites")
INVITE_DAYS = 14
# Wrong codes are counted together: 5 free, then a doubling wait, so codes can't be guessed.
guesses = Limiter("invite codes")


def _normal(code: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (code or "").upper())


def _hash(code: str) -> str:
    """Older typed codes are compared without dashes or case."""
    return hashlib.sha256(_normal(code).encode()).hexdigest()


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.strip().encode()).hexdigest()


EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _find(c, token: str):
    """The invite for a link's secret (or an older typed code), if it can still be used."""
    row = c.execute("SELECT * FROM invites WHERE code_hash IN (?,?)", (_hash_token(token), _hash(token))).fetchone()
    if not row or row["used_by"] or row["revoked"] or row["expires_at"] < time.time():
        return None
    return row


def can_invite(user: dict) -> bool:
    return bool(user["site_admin"] or user["can_invite"])


def require_inviter(user: dict = Depends(auth.current_user)) -> dict:
    if not can_invite(user):
        raise HTTPException(403, "Your account can't invite people. Ask the server's administrator.")
    return user


def redeem(c, code: str, user_id: int) -> None:
    """Marks a code used by a new account, inside the sign-up's transaction.
    Raises 403 if it isn't a valid, unused, unexpired code."""
    guesses.check()
    row = _find(c, code)
    if not row:
        guesses.fail()
        raise HTTPException(403, "That invitation isn't valid. It may have been used already, cancelled or expired.")
    guesses.success()
    c.execute("UPDATE invites SET used_by=?, used_at=? WHERE id=?", (user_id, time.time(), row["id"]))
    c.execute("UPDATE users SET invited_by=?, email=? WHERE id=?", (row["created_by"], row["email"], user_id))


class NewInvite(BaseModel):
    email: str = Field(max_length=200)
    note: str = Field("", max_length=100)  # who it's for, e.g. "Sarah"


@router.post("")
def create(b: NewInvite, user: dict = Depends(require_inviter)):
    """Invites someone by email. Returns the secret for their personal sign-up
    link; it's shown this once, so the inviter can send it. Inviting the same
    address again cancels the earlier, unused link."""
    email = b.email.strip().lower()
    if not EMAIL.match(email):
        raise HTTPException(400, "That doesn't look like an email address.")
    if db.one("SELECT 1 FROM users WHERE email=?", (email,)):
        raise HTTPException(400, "Someone with that email address already has an account.")
    token = secrets.token_urlsafe(18)
    now = time.time()
    with db.connect() as c:
        c.execute("UPDATE invites SET revoked=1 WHERE email=? AND used_by IS NULL", (email,))
        c.execute("INSERT INTO invites(code_hash, hint, email, note, created_by, created_at, expires_at) "
                  "VALUES(?,?,?,?,?,?,?)", (_hash_token(token), token[-4:], email, b.note.strip(), user["id"], now,
                                            now + INVITE_DAYS * 86400))
    return {"token": token, "email": email, "expires_at": now + INVITE_DAYS * 86400}


@router.get("/check")
def check(token: str):
    """Public: who a sign-up link is for, so the sign-up page can show it."""
    guesses.check()
    with db.connect() as c:
        row = _find(c, token)
    if not row:
        guesses.fail()
        raise HTTPException(404, "This invitation link isn't valid. It may have been used already, cancelled or expired.")
    return {"email": row["email"]}


@router.get("")
def mine(user: dict = Depends(require_inviter)):
    """Your codes (all codes, for the administrator), newest first, with what happened to each."""
    sql = ("SELECT i.id, i.hint, i.email, i.note, i.created_at, i.expires_at, i.used_at, i.revoked, c.username AS created_by, "
           "u.username AS used_by FROM invites i JOIN users c ON c.id=i.created_by LEFT JOIN users u ON u.id=i.used_by")
    rows = db.rows(sql + ("" if user["site_admin"] else " WHERE i.created_by=?") + " ORDER BY i.id DESC LIMIT 100",
                   () if user["site_admin"] else (user["id"],))
    now = time.time()
    for r in rows:
        r["status"] = ("used" if r["used_at"] else "cancelled" if r["revoked"]
                       else "expired" if r["expires_at"] < now else "waiting")
    return rows


@router.delete("/{iid}")
def cancel(iid: int, user: dict = Depends(require_inviter)):
    row = db.one("SELECT * FROM invites WHERE id=?", (iid,))
    if not row or not (user["site_admin"] or row["created_by"] == user["id"]):
        raise HTTPException(404, "Invite not found")
    if row["used_by"]:
        raise HTTPException(400, "That code has already been used.")
    db.run("UPDATE invites SET revoked=1 WHERE id=?", (iid,))
    return {"ok": True}
