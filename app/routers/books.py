from fastapi import APIRouter, Depends, HTTPException

from .. import db, security
from ..schemas import BookIn, BookPatch
from ..security import unlocked
from .people import ensure_person

router = APIRouter(prefix="/api/books")

SELECT = ("SELECT b.*, s.name AS shelf, p.name AS person FROM books b "
          "LEFT JOIN shelves s ON s.id=b.shelf_id LEFT JOIN people p ON p.id=b.person_id")


def _visible(bid: int, is_unlocked: bool) -> dict:
    r = db.one(SELECT + " WHERE b.id=?", (bid,))
    # A restricted book answers "not found" while locked, so its existence isn't revealed.
    if not r or (r["restricted"] and not is_unlocked):
        raise HTTPException(404, "Book not found")
    return r


def _shelf(sid: int | None) -> dict | None:
    if sid is None:
        return None
    s = db.one("SELECT * FROM shelves WHERE id=?", (sid,))
    if not s:
        raise HTTPException(400, "That shelf doesn't exist")
    return s


@router.get("")
def books(shelf_id: int | None = None, wishlist: bool | None = None, person_id: int | None = None,
          q: str = "", is_unlocked: bool = Depends(unlocked)):
    sql, args = SELECT + " WHERE 1=1", []
    if shelf_id is not None:
        sql += " AND b.shelf_id=?"
        args.append(shelf_id)
    if wishlist is not None:
        sql += " AND b.wishlist=?"
        args.append(int(wishlist))
    if person_id is not None:
        sql += " AND b.person_id=?"
        args.append(person_id)
    if q.strip():
        sql += " AND (b.title LIKE ? OR b.authors LIKE ? OR b.isbn LIKE ?)"
        args += [f"%{q.strip()}%"] * 3
    if not is_unlocked:
        sql += " AND b.restricted=0"
    return db.rows(sql + " ORDER BY b.title COLLATE NOCASE", args)


@router.post("")
def add_book(b: BookIn):
    shelf = None if b.wishlist else _shelf(b.shelf_id)
    ensure_person(b.person_id)
    # A book added to someone's shelf belongs to them unless told otherwise.
    person = b.person_id if b.person_id is not None else (shelf or {}).get("person_id")
    cur = db.run("INSERT INTO books(isbn,title,authors,cover,shelf_id,wishlist,person_id) VALUES(?,?,?,?,?,?,?)",
                 (b.isbn.strip(), b.title.strip(), b.authors.strip(), b.cover,
                  shelf["id"] if shelf else None, int(b.wishlist), person))
    return {"id": cur.lastrowid}


@router.get("/{bid}")
def get_book(bid: int, is_unlocked: bool = Depends(unlocked)):
    return _visible(bid, is_unlocked)


@router.patch("/{bid}")
def patch_book(bid: int, body: BookPatch, is_unlocked: bool = Depends(unlocked)):
    _visible(bid, is_unlocked)
    sets = body.model_dump(exclude_unset=True)
    if "title" in sets and not (sets["title"] or "").strip():
        raise HTTPException(400, "Enter a title")
    if sets.get("restricted") and not security.pin_is_set():
        raise HTTPException(400, "Set a PIN for the restricted section first (Settings).")
    if "shelf_id" in sets:
        _shelf(sets["shelf_id"])
    if "person_id" in sets:
        ensure_person(sets["person_id"])
    if sets.get("wishlist"):
        sets["shelf_id"] = None
    for k in ("wishlist", "restricted"):
        if k in sets and sets[k] is not None:
            sets[k] = int(sets[k])
    if sets:
        db.run(f"UPDATE books SET {', '.join(k + '=?' for k in sets)} WHERE id=?", [*sets.values(), bid])
    return {"ok": True}


@router.delete("/{bid}")
def delete_book(bid: int, is_unlocked: bool = Depends(unlocked)):
    _visible(bid, is_unlocked)
    db.run("DELETE FROM books WHERE id=?", (bid,))
    return {"ok": True}
