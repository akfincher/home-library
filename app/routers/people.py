from fastapi import APIRouter, HTTPException

from .. import db
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
    return {"id": db.run("INSERT INTO people(name) VALUES(?)", (name,)).lastrowid}


@router.patch("/{pid}")
def rename_person(pid: int, b: Named):
    name = b.name.strip()
    if not name:
        raise HTTPException(400, "Enter a name")
    if not db.run("UPDATE people SET name=? WHERE id=?", (name, pid)).rowcount:
        raise HTTPException(404, "Person not found")
    return {"ok": True}


@router.delete("/{pid}")
def remove_person(pid: int):
    """Their shelves and books stay, and become shared."""
    with db.connect() as c:
        c.execute("UPDATE shelves SET person_id=NULL WHERE person_id=?", (pid,))
        c.execute("UPDATE books SET person_id=NULL WHERE person_id=?", (pid,))
        if not c.execute("DELETE FROM people WHERE id=?", (pid,)).rowcount:
            raise HTTPException(404, "Person not found")
    return {"ok": True}
