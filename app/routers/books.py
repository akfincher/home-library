from fastapi import APIRouter, Depends, HTTPException

from .. import db, metadata, security
from ..schemas import BookIn, BookPatch
from ..security import unlocked
from .people import ensure_person

router = APIRouter(prefix="/api/books")

SELECT = ("SELECT b.*, s.name AS shelf, p.name AS person FROM books b "
          "LEFT JOIN shelves s ON s.id=b.shelf_id LEFT JOIN people p ON p.id=b.person_id")
TEXT = {"title", "authors", "isbn", "cover", "review", "subtitle", "description", "publisher", "published",
        "subjects", "series", "language"}
# Columns a lookup can fill in. Only empty ones are ever filled, so nothing typed by hand is overwritten.
FILLABLE = ["subtitle", "authors", "cover", "description", "publisher", "published", "pages", "subjects",
            "language"]
SEARCHED = ["title", "subtitle", "authors", "isbn", "series", "subjects", "publisher"]


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
    # Every word has to appear somewhere in the book's details, in any order.
    for word in q.split()[:8]:
        sql += " AND (" + " OR ".join(f"b.{c} LIKE ?" for c in SEARCHED) + ")"
        args += [f"%{word}%"] * len(SEARCHED)
    if not is_unlocked:
        sql += " AND b.restricted=0"
    return db.rows(sql + " ORDER BY b.title COLLATE NOCASE", args)


@router.post("")
def add_book(b: BookIn):
    shelf = None if b.wishlist else _shelf(b.shelf_id)
    ensure_person(b.person_id)
    data = b.model_dump()
    data.update(title=b.title.strip(), authors=b.authors.strip(), isbn=b.isbn.strip(), wishlist=int(b.wishlist),
                shelf_id=shelf["id"] if shelf else None,
                # A book added to someone's shelf belongs to them unless told otherwise.
                person_id=b.person_id if b.person_id is not None else (shelf or {}).get("person_id"))
    cur = db.run(f"INSERT INTO books({', '.join(data)}) VALUES({', '.join('?' * len(data))})", list(data.values()))
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
    for k, v in sets.items():
        if k in ("wishlist", "restricted") and v is not None:
            sets[k] = int(v)
        elif k in TEXT:
            sets[k] = (v or "").strip()
    if sets:
        db.run(f"UPDATE books SET {', '.join(k + '=?' for k in sets)} WHERE id=?", [*sets.values(), bid])
    return {"ok": True}


@router.delete("/{bid}")
def delete_book(bid: int, is_unlocked: bool = Depends(unlocked)):
    _visible(bid, is_unlocked)
    db.run("DELETE FROM books WHERE id=?", (bid,))
    return {"ok": True}


def _fill(book: dict) -> list[str]:
    """Looks the book up by ISBN and fills in its empty details. Returns what was filled."""
    isbn = metadata.clean_isbn(book["isbn"])
    found = metadata.lookup(isbn) if isbn else None
    if not found:
        return []
    sets = {k: found[k] for k in FILLABLE if found.get(k) and not book.get(k)}
    if sets:
        db.run(f"UPDATE books SET {', '.join(k + '=?' for k in sets)} WHERE id=?", [*sets.values(), book["id"]])
    return list(sets)


@router.post("/{bid}/fill")
def fill_book(bid: int, is_unlocked: bool = Depends(unlocked)):
    book = _visible(bid, is_unlocked)
    if not metadata.clean_isbn(book["isbn"]):
        raise HTTPException(400, "This book has no ISBN to look up.")
    return {"filled": _fill(book)}


BATCH = 5


@router.post("/fill-missing")
def fill_missing(after_id: int = 0, is_unlocked: bool = Depends(unlocked)):
    """Fills in details for the next few books that have an ISBN but are missing
    something. Call again with the returned last_id until done is true."""
    hide = "" if is_unlocked else " AND restricted=0"
    batch = db.rows("SELECT * FROM books WHERE id>? AND isbn!='' AND (cover='' OR description='' OR pages IS NULL "
                    f"OR publisher='' OR subjects=''){hide} ORDER BY id LIMIT ?", (after_id, BATCH))
    filled = sum(1 for b in batch if _fill(b))
    return {"checked": len(batch), "filled": filled, "last_id": batch[-1]["id"] if batch else after_id,
            "done": len(batch) < BATCH}
