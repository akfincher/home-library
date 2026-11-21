# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Aaron Fincher. See LICENSE.
"""For the server's administrator: every account on the server, and which of
them may create invite codes."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import auth, db

router = APIRouter(prefix="/api/server", dependencies=[Depends(auth.require_site_admin)])


class AccountPatch(BaseModel):
    can_invite: bool


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
        c.execute("UPDATE invites SET revoked=1 WHERE created_by=? AND used_by IS NULL", (uid,))
        c.execute("DELETE FROM users WHERE id=?", (uid,))
    return {"ok": True}
