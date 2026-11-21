from fastapi import APIRouter, Depends, HTTPException

from .. import db
from ..schemas import ShelfIn
from ..security import require_admin, unlocked
from .people import ensure_person

router = APIRouter(prefix="/api/shelves")


@router.get("")
def shelves(person_id: int | None = None, is_unlocked: bool = Depends(unlocked)):
    """With person_id, returns that person's shelves plus the shared ones."""
    hide = "" if is_unlocked else " AND b.restricted=0"
    sql = ("SELECT s.id, s.name, s.person_id, p.name AS person, COUNT(b.id) AS count FROM shelves s "
           "LEFT JOIN people p ON p.id=s.person_id "
           f"LEFT JOIN books b ON b.shelf_id=s.id{hide}")
    args = []
    if person_id is not None:
        sql += " WHERE s.person_id=? OR s.person_id IS NULL"
        args.append(person_id)
    return db.rows(sql + " GROUP BY s.id ORDER BY s.name COLLATE NOCASE", args)


def _clean(b: ShelfIn) -> str:
    name = b.name.strip()
    if not name:
        raise HTTPException(400, "Enter a shelf name")
    ensure_person(b.person_id)
    return name


@router.post("")
def add_shelf(b: ShelfIn):
    name = _clean(b)
    return {"id": db.run("INSERT INTO shelves(name, person_id) VALUES(?,?)", (name, b.person_id)).lastrowid}


@router.put("/{sid}")
def update_shelf(sid: int, b: ShelfIn):
    name = _clean(b)
    if not db.run("UPDATE shelves SET name=?, person_id=? WHERE id=?", (name, b.person_id, sid)).rowcount:
        raise HTTPException(404, "Shelf not found")
    return {"ok": True}


@router.delete("/{sid}", dependencies=[Depends(require_admin)])
def delete_shelf(sid: int):
    # Needs the PIN (once one is set): otherwise a shelf that only holds
    # restricted books would look empty yet refuse to delete, giving them away.
    with db.connect() as c:
        if c.execute("SELECT 1 FROM books WHERE shelf_id=?", (sid,)).fetchone():
            raise HTTPException(400, "Move or delete the books on this shelf first")
        if not c.execute("DELETE FROM shelves WHERE id=?", (sid,)).rowcount:
            raise HTTPException(404, "Shelf not found")
    return {"ok": True}
