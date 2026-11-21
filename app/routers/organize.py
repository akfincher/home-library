"""Lets the app arrange books into shelves by a rule (genre, author, series,
reading level or owner). Preview first, then apply. Applying saves a copy of
the database first and only ever moves books; nothing is deleted."""
import re
from collections import Counter

from fastapi import APIRouter, Depends

from .. import db
from ..schemas import OrganizeIn
from ..security import require_admin, unlocked
from .people import ensure_person

router = APIRouter(prefix="/api/organize")

OTHER = "Other"
# Subjects so broad they'd put nearly everything on one shelf.
GENERIC = {"fiction", "general", "nonfiction", "non fiction", "literature", "novel", "novels", "books",
           "english fiction", "american fiction", "fiction in english", "large type books"}
LETTERS = ["A–C", "D–F", "G–I", "J–L", "M–O", "P–R", "S–U", "V–Z"]
LEVELS = [(0, 2, "Reading level 0–1.9"), (2, 4, "Reading level 2–3.9"), (4, 6, "Reading level 4–5.9"),
          (6, 8, "Reading level 6–7.9"), (8, 100, "Reading level 8+")]
# How each kind of shelf the organizer makes is sorted.
SHELF_SORT = {"genre": "author", "author": "author", "series": "series", "level": "title", "person": "title"}
# Groups that aren't a real category, listed last.
LEFTOVERS = {OTHER, "No genre", "No author", "Not in a series", "No reading level", "Shared"}


def _subjects(b) -> list[str]:
    return [s.strip() for s in (b["subjects"] or "").split(",") if s.strip()]


def _surname(authors: str) -> str:
    first = (authors or "").split(",")[0].strip()
    words = [w for w in first.split() if w.rstrip(".").lower() not in ("jr", "sr", "ii", "iii")]
    return words[-1] if words else ""


def _sort_key(sort: str):
    def key(b):
        title = (b["title"] or "").lower()
        if sort == "author":
            return (_surname(b["authors"]).lower(), (b["authors"] or "").lower(), title)
        if sort == "series":
            return (not b["series"], (b["series"] or "").lower(), b["series_index"] or 0, title)
        return (title,)
    return key


def _books(o: OrganizeIn, is_unlocked: bool) -> list[dict]:
    """Every book on a shelf (not the wish list) that the organizer may move."""
    sql = "SELECT b.*, p.name AS person FROM books b LEFT JOIN people p ON p.id=b.person_id WHERE b.shelf_id IS NOT NULL AND b.wishlist=0"
    args = []
    if o.person_id is not None:
        sql += " AND b.person_id=?"
        args.append(o.person_id)
    if not is_unlocked:
        sql += " AND b.restricted=0"
    return db.rows(sql, args)


def _group(o: OrganizeIn, books: list[dict]) -> dict[int, tuple[str, int | None]]:
    """book id -> (shelf name, shelf owner) for the chosen rule."""
    owner = o.person_id
    out = {}
    if o.rule == "genre":
        # Each book goes under its subject that the most books share, so shelves fill up.
        counts = Counter(s.lower() for b in books for s in set(_subjects(b)))
        for b in books:
            subs = [s for s in _subjects(b) if s.lower() not in GENERIC] or _subjects(b)
            out[b["id"]] = (max(subs, key=lambda s: counts[s.lower()]) if subs else "No genre", owner)
    elif o.rule == "author":
        for b in books:
            letter = _surname(b["authors"])[:1].upper()
            name = next((r for r in LETTERS if r[0] <= letter <= r[-1]), "#") if letter else "No author"
            out[b["id"]] = (name, owner)
    elif o.rule == "series":
        for b in books:
            out[b["id"]] = (b["series"] or "Not in a series", owner)
    elif o.rule == "level":
        for b in books:
            lvl = b["ar_level"]
            name = next((n for lo, hi, n in LEVELS if lvl is not None and lo <= lvl < hi), "No reading level")
            out[b["id"]] = (name, owner)
    else:  # person
        for b in books:
            out[b["id"]] = (f"{b['person']}'s books", b["person_id"]) if b["person_id"] else ("Shared", None)
    # Too-small groups share one shelf, except for the "person" rule, where every person gets theirs.
    if o.rule != "person":
        sizes = Counter(out.values())
        out = {bid: (g if sizes[g] >= o.min_size or g[0] in LEFTOVERS else (OTHER, g[1])) for bid, g in out.items()}
    return out


def _plan(o: OrganizeIn, is_unlocked: bool):
    ensure_person(o.person_id)
    books = _books(o, is_unlocked)
    mapping = _group(o, books)
    groups = {}
    for b in books:
        groups.setdefault(mapping[b["id"]], []).append(b)
    order = sorted(groups, key=lambda g: (g[0] in LEFTOVERS, g[0].lower()))
    return books, [(g, groups[g]) for g in order]


@router.post("/preview")
def preview(o: OrganizeIn, is_unlocked: bool = Depends(unlocked)):
    books, groups = _plan(o, is_unlocked)
    shelf_of = {(s["name"].lower(), s["person_id"]): s["id"] for s in db.rows("SELECT * FROM shelves")}
    moves = sum(1 for (name, owner), bs in groups for b in bs
                if b["shelf_id"] != shelf_of.get((name.lower(), owner)))
    return {"total": len(books), "moves": moves,
            "groups": [{"name": name, "count": len(bs),
                        "titles": [b["title"] for b in sorted(bs, key=_sort_key(SHELF_SORT[o.rule]))[:3]]}
                       for (name, _), bs in groups]}


@router.post("/apply", dependencies=[Depends(require_admin)])
def apply(o: OrganizeIn, is_unlocked: bool = Depends(unlocked)):
    books, groups = _plan(o, is_unlocked)
    if not books:
        return {"moved": 0, "shelves": 0, "empty_shelves": 0}
    db.backup_to(f"{db.path()}.pre-organize-{db.stamp()}.bak")
    sort = SHELF_SORT[o.rule]
    moved = 0
    with db.connect() as c:
        for (name, owner), bs in groups:
            row = c.execute("SELECT id FROM shelves WHERE name=? COLLATE NOCASE AND person_id IS ?",
                            (name, owner)).fetchone()
            sid = row[0] if row else c.execute("INSERT INTO shelves(name, person_id, sort) VALUES(?,?,?)",
                                               (name, owner, sort)).lastrowid
            c.execute("UPDATE shelves SET sort=? WHERE id=?", (sort, sid))
            ids = {b["id"] for b in bs}
            # Books already on this shelf that aren't being organized keep their places at the front.
            start = c.execute(f"SELECT COALESCE(MAX(position), 0) FROM books WHERE shelf_id=? "
                              f"AND id NOT IN ({','.join('?' * len(ids))})", (sid, *ids)).fetchone()[0]
            for i, b in enumerate(sorted(bs, key=_sort_key(sort))):
                moved += b["shelf_id"] != sid
                c.execute("UPDATE books SET shelf_id=?, position=? WHERE id=?", (sid, start + i + 1, b["id"]))
        empty = c.execute("SELECT COUNT(*) FROM shelves WHERE id NOT IN "
                          "(SELECT shelf_id FROM books WHERE shelf_id IS NOT NULL)").fetchone()[0]
    return {"moved": moved, "shelves": len(groups), "empty_shelves": empty}
