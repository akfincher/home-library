# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Aaron Fincher. See LICENSE.
"""Your libraries: list them, switch between them, make new ones, rename, leave
or delete them. One account can belong to several libraries (say, home and a
classroom) and switch without logging out."""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .. import auth, db
from ..changelog import log
from ..context import lib, library

router = APIRouter(prefix="/api/libraries")
MAX_OWNED = 20  # libraries one account can create, to stop runaway sign-ups filling the server


class LibraryName(BaseModel):
    name: str = Field(max_length=100)


def _clean(name: str) -> str:
    name = name.strip()
    if not name:
        raise HTTPException(400, "Give the library a name.")
    return name


@router.get("")
def mine(user: dict = Depends(auth.current_user)):
    return auth.libraries_of(user["id"])


@router.post("")
def create(b: LibraryName, request: Request, user: dict = Depends(auth.current_user)):
    """A new library you own, with a Main shelf. You switch to it straight away."""
    owned = db.one("SELECT COUNT(*) AS n FROM memberships WHERE user_id=? AND role='admin'", (user["id"],))["n"]
    if owned >= MAX_OWNED:
        raise HTTPException(400, f"You already own {MAX_OWNED} libraries.")
    lib_id = auth.new_library(_clean(b.name), user["id"])
    auth.switch_library(request.cookies.get(auth.COOKIE), lib_id)
    return {"id": lib_id}


@router.post("/{lib_id}/switch")
def switch(lib_id: int, request: Request, user: dict = Depends(auth.current_user)):
    if not any(l["id"] == lib_id for l in auth.libraries_of(user["id"])):
        raise HTTPException(404, "You're not a member of that library.")
    auth.switch_library(request.cookies.get(auth.COOKIE), lib_id)
    return {"ok": True}


@router.patch("/current", dependencies=[Depends(auth.require_owner)])
def rename(b: LibraryName):
    name, old = _clean(b.name), library()["name"]
    db.run("UPDATE libraries SET name=? WHERE id=?", (name, lib()))
    if name != old:
        log(f"Renamed the library from “{old}” to “{name}”", admin=True)
    return {"ok": True}


@router.post("/current/leave")
def leave(user: dict = Depends(auth.current_user)):
    """Leave the current library. Its books stay with the other members."""
    current = library()
    if current["role"] == "admin" and not db.one(
            "SELECT 1 FROM memberships WHERE library_id=? AND role='admin' AND user_id!=?", (current["id"], user["id"])):
        raise HTTPException(400, "You're its only owner. Make someone else an owner first, or delete the library.")
    db.run("DELETE FROM memberships WHERE library_id=? AND user_id=?", (current["id"], user["id"]))
    log(f"{user['username']} left the library", admin=True)
    return {"ok": True}


@router.delete("/current", dependencies=[Depends(auth.require_owner)])
def delete(b: LibraryName):
    """Deletes the current library and everything in it. To make sure, the
    library's exact name has to be sent back."""
    current = library()
    if b.name.strip() != current["name"]:
        raise HTTPException(400, "Type the library's name exactly to delete it.")
    db.backup_to(f"{db.path()}.pre-delete-library-{db.stamp()}.bak")
    with db.connect() as c:
        for table in ("reads", "loans", "books", "shelves", "people", "changes", "memberships"):
            c.execute(f"DELETE FROM {table} WHERE library_id=?", (current["id"],))
        c.execute("DELETE FROM libraries WHERE id=?", (current["id"],))
    return {"ok": True}
