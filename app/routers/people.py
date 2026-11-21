# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Aaron Fincher. See LICENSE.
from fastapi import APIRouter, HTTPException

from .. import db
from ..changelog import log
from ..schemas import Named

router = APIRouter(prefix="/api/people")


def ensure_person(pid: int | None) -> None:
    if pid is not None and not db.one("SELECT 1 FROM people WHERE id=?", (pid,)):
        raise HTTPException(400, "That person doesn't exist")


@router.get("")
def people():
    return db.rows("SELECT id, name FROM people ORDER BY name COLLATE NOCASE")


@router.post("")
def add_person(b: Named):
    name = b.name.strip()
    if not name:
        raise HTTPException(400, "Enter a name")
    pid = db.run("INSERT INTO people(name) VALUES(?)", (name,)).lastrowid
    log(f"Added {name} to the household")
    return {"id": pid}


@router.patch("/{pid}")
def rename_person(pid: int, b: Named):
    name = b.name.strip()
    if not name:
        raise HTTPException(400, "Enter a name")
    old = db.one("SELECT name FROM people WHERE id=?", (pid,))
    if not old:
        raise HTTPException(404, "Person not found")
    db.run("UPDATE people SET name=? WHERE id=?", (name, pid))
    if old["name"] != name:
        log(f"Renamed {old['name']} to {name}")
    return {"ok": True}


@router.delete("/{pid}")
def remove_person(pid: int):
    """Their shelves and books stay, and become shared."""
    old = db.one("SELECT name FROM people WHERE id=?", (pid,))
    with db.connect() as c:
        c.execute("UPDATE shelves SET person_id=NULL WHERE person_id=?", (pid,))
        c.execute("UPDATE books SET person_id=NULL WHERE person_id=?", (pid,))
        c.execute("UPDATE users SET person_id=NULL WHERE person_id=?", (pid,))
        if not c.execute("DELETE FROM people WHERE id=?", (pid,)).rowcount:
            raise HTTPException(404, "Person not found")
    log(f"Removed {old['name']} from the household")
    return {"ok": True}
