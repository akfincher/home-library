# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Aaron Fincher. See LICENSE.
"""The current library's books."""
from fastapi import APIRouter, Depends, HTTPException

from .. import db, metadata, security
from ..changelog import log, q
from ..context import lib
from ..schemas import BookIn, BookPatch
from ..security import unlocked
from .people import ensure_person

router = APIRouter(prefix="/api/books")

# Each book comes with who has it on loan, if anyone.
SELECT = ("SELECT b.*, s.name AS shelf, p.name AS person, l.borrower AS on_loan_to, l.due AS loan_due, "
          "l.lent_at AS loan_since FROM books b "
          "LEFT JOIN shelves s ON s.id=b.shelf_id LEFT JOIN people p ON p.id=b.person_id "
          "LEFT JOIN loans l ON l.book_id=b.id AND l.returned_at IS NULL")
TEXT = {"title", "authors", "isbn", "cover", "review", "subtitle", "description", "publisher", "published",
        "subjects", "series", "language"}
# Columns a lookup can fill in. Only empty ones are ever filled, so nothing typed by hand is overwritten.
FILLABLE = ["subtitle", "authors", "cover", "description", "publisher", "published", "pages", "subjects",
            "language"]
SEARCHED = ["title", "subtitle", "authors", "isbn", "series", "subjects", "publisher"]
# How fields are named in the change history.
LABELS = {"authors": "author", "spicy": "spice", "ar_level": "AR level", "ar_points": "AR points",
          "series_index": "number in series", "isbn": "ISBN", "cover": "cover image", "pages": "page count"}
# Long fields are described as added / edited / removed rather than quoted.
LONG = {"review", "description", "subjects", "cover"}


def _where(shelf_id, wishlist) -> str:
    if wishlist:
        return "the wish list"
    s = db.one("SELECT name FROM shelves WHERE id=?", (shelf_id,)) if shelf_id else None
    return q(s["name"]) if s else "no shelf"


def _stars(v) -> str:
    return "not rated" if v is None else f"{v} star{'' if v == 1 else 's'}"


def _spice(v) -> str:
    return "not rated" if v is None else "no spice" if v == 0 else f"{v} pepper{'' if v == 1 else 's'}"


def _person(pid) -> str:
    p = db.one("SELECT name FROM people WHERE id=?", (pid,)) if pid else None
    return p["name"] if p else "nobody in particular"


def _short(v) -> str:
    if v in (None, ""):
        return "blank"
    v = str(v)
    return q(v if len(v) <= 60 else v[:57] + "…")


def describe(field: str, old, new) -> str:
    """One change in words, e.g. 'rating from 0 stars to 5 stars'."""
    label = LABELS.get(field, field.replace("_", " "))
    if field == "rating":
        return f"rating from {_stars(old)} to {_stars(new)}"
    if field == "spicy":
        return f"spice from {_spice(old)} to {_spice(new)}"
    if field == "person_id":
        return f"whose book from {_person(old)} to {_person(new)}"
    if field == "restricted":
        return "moved into the restricted section" if new else "moved out of the restricted section"
    if field in LONG:
        return f"added the {label}" if not old else f"removed the {label}" if not new else f"edited the {label}"
    if field in TEXT:
        return f"{label} from {_short(old)} to {_short(new)}"
    return f"{label} from {'blank' if old is None else old} to {'blank' if new is None else new}"


def _visible(bid: int, is_unlocked: bool) -> dict:
    r = db.one(SELECT + " WHERE b.id=? AND b.library_id=?", (bid, lib()))
    # A restricted book answers "not found" while locked, so its existence isn't revealed.
    if not r or (r["restricted"] and not is_unlocked):
        raise HTTPException(404, "Book not found")
    return r


def _end_of(sid: int | None):
    """The position just after the last book on a shelf, so new arrivals go at the end."""
    if sid is None:
        return None
    return (db.one("SELECT MAX(position) AS m FROM books WHERE shelf_id=?", (sid,))["m"] or 0) + 1


def _shelf(sid: int | None) -> dict | None:
    if sid is None:
        return None
    s = db.one("SELECT * FROM shelves WHERE id=? AND library_id=?", (sid, lib()))
    if not s:
        raise HTTPException(400, "That shelf doesn't exist")
    return s


@router.get("")
def books(shelf_id: int | None = None, wishlist: bool | None = None, person_id: int | None = None,
          shared: bool = False, q: str = "", is_unlocked: bool = Depends(unlocked)):
    """With person_id, only that person's books; add shared=true to include
    books that belong to nobody in particular."""
    sql, args = SELECT + " WHERE b.library_id=?", [lib()]
    if shelf_id is not None:
        sql += " AND b.shelf_id=?"
        args.append(shelf_id)
    if wishlist is not None:
        sql += " AND b.wishlist=?"
        args.append(int(wishlist))
    if person_id is not None:
        sql += " AND (b.person_id=? OR b.person_id IS NULL)" if shared else " AND b.person_id=?"
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
                person_id=b.person_id if b.person_id is not None else (shelf or {}).get("person_id"),
                position=_end_of(shelf["id"] if shelf else None),
                # Not rated yet (0 stars and "no spice" are real ratings, set by hand).
                rating=None, spicy=None, library_id=lib())
    cur = db.run(f"INSERT INTO books({', '.join(data)}) VALUES({', '.join('?' * len(data))})", list(data.values()))
    log(f"Added {q(data['title'])} to {_where(data['shelf_id'], data['wishlist'])}", {"id": cur.lastrowid})
    return {"id": cur.lastrowid}


@router.get("/{bid}")
def get_book(bid: int, is_unlocked: bool = Depends(unlocked)):
    from .reading import mine_for  # (reading builds on this module)
    return {**_visible(bid, is_unlocked), **mine_for(bid)}


@router.patch("/{bid}")
def patch_book(bid: int, body: BookPatch, is_unlocked: bool = Depends(unlocked)):
    book = _visible(bid, is_unlocked)
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
    if "shelf_id" in sets and sets["shelf_id"] != book["shelf_id"]:
        sets["position"] = _end_of(sets["shelf_id"])
    for k, v in sets.items():
        if k in ("wishlist", "restricted") and v is not None:
            sets[k] = int(v)
        elif k in TEXT:
            sets[k] = (v or "").strip()
    changed = [k for k, v in sets.items() if k != "position" and book.get(k) != v]
    if sets:
        db.run(f"UPDATE books SET {', '.join(k + '=?' for k in sets)} WHERE id=?", [*sets.values(), bid])
    if changed:
        title = q(sets.get("title") or book["title"])
        hidden = bool(book["restricted"] or sets.get("restricted"))
        if "shelf_id" in changed or "wishlist" in changed:
            log(f"Moved {title} from {_where(book['shelf_id'], book['wishlist'])} to "
                f"{_where(sets.get('shelf_id', book['shelf_id']), sets.get('wishlist', book['wishlist']))}",
                book, restricted=hidden)
        rest = [describe(k, book.get(k), sets[k]) for k in changed if k not in ("shelf_id", "wishlist")]
        if rest:
            log(f"Changed {title}: {'; '.join(rest)}", book, restricted=hidden)
    return {"ok": True}


@router.delete("/{bid}")
def delete_book(bid: int, is_unlocked: bool = Depends(unlocked)):
    book = _visible(bid, is_unlocked)
    db.run("DELETE FROM loans WHERE book_id=?", (bid,))
    db.run("DELETE FROM reads WHERE book_id=?", (bid,))
    db.run("DELETE FROM books WHERE id=?", (bid,))
    log(f"Deleted {q(book['title'])} from {_where(book['shelf_id'], book['wishlist'])}", book)
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
        log(f"Filled in details for {q(book['title'])}: {', '.join(LABELS.get(k, k) for k in sets)}", book)
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
    batch = db.rows("SELECT * FROM books WHERE library_id=? AND id>? AND isbn!='' AND (cover='' OR description='' "
                    f"OR pages IS NULL OR publisher='' OR subjects=''){hide} ORDER BY id LIMIT ?",
                    (lib(), after_id, BATCH))
    filled = sum(1 for b in batch if _fill(b))
    return {"checked": len(batch), "filled": filled, "last_id": batch[-1]["id"] if batch else after_id,
            "done": len(batch) < BATCH}
