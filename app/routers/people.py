# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Aaron Fincher. See LICENSE.
"""The people in the current library's household."""
from fastapi import APIRouter, HTTPException

from .. import db
from ..changelog import log
from ..context import lib
from ..schemas import Named

router = APIRouter(prefix="/api/people")


def ensure_person(pid: int | None) -> None:
    if pid is not None and not db.one("SELECT 1 FROM people WHERE id=? AND library_id=?", (pid, lib())):
        raise HTTPException(400, "That person doesn't exist")


def _get(pid: int) -> dict:
    p = db.one("SELECT * FROM people WHERE id=? AND library_id=?", (pid, lib()))
    if not p:
        raise HTTPException(404, "Person not found")
    return p


def _name(b: Named) -> str:
    name = b.name.strip()
    if not name:
        raise HTTPException(400, "Enter a name")
    return name


@router.get("")
def people():
    return db.rows("SELECT id, name FROM people WHERE library_id=? ORDER BY name COLLATE NOCASE", (lib(),))


@router.post("")
def add_person(b: Named):
    name = _name(b)
    pid = db.run("INSERT INTO people(name, library_id) VALUES(?,?)", (name, lib())).lastrowid
    log(f"Added {name} to the household")
    return {"id": pid}


@router.patch("/{pid}")
def rename_person(pid: int, b: Named):
    old, name = _get(pid), _name(b)
    db.run("UPDATE people SET name=? WHERE id=?", (name, pid))
    if old["name"] != name:
        log(f"Renamed {old['name']} to {name}")
    return {"ok": True}


@router.delete("/{pid}")
def remove_person(pid: int):
    """Their shelves and books stay, and become shared."""
    old = _get(pid)
    with db.connect() as c:
        c.execute("UPDATE shelves SET person_id=NULL WHERE person_id=?", (pid,))
        c.execute("UPDATE books SET person_id=NULL WHERE person_id=?", (pid,))
        c.execute("UPDATE memberships SET person_id=NULL WHERE person_id=?", (pid,))
        c.execute("DELETE FROM people WHERE id=?", (pid,))
    log(f"Removed {old['name']} from the household")
    return {"ok": True}
