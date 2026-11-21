# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Aaron Fincher. See LICENSE.
"""Each person's own reading: what they're reading now (and which page they're
on), what they've finished, what they didn't finish, and a yearly report.

Reading is personal: everyone in a library sees only their own. A book can be
read more than once; each read is its own entry. Dates are calendar dates
(YYYY-MM-DD), so "read in 2026" means finished in 2026.
"""
import re
import time
from collections import Counter
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import db
from ..context import USER, lib
from ..security import unlocked
from .books import _visible

router = APIRouter(prefix="/api")
WORDS_PER_PAGE = 250  # a common rough figure for a printed page; the report says it's an estimate
# Subjects too broad to count as a genre in the report.
GENERIC = {"fiction", "general", "nonfiction", "non fiction", "literature", "novel", "novels", "books",
           "english fiction", "american fiction", "fiction in english", "large type books"}
COLUMNS = ("SELECT r.*, b.title, b.authors, b.cover, b.pages, b.rating, b.subjects, b.restricted FROM reads r "
           "JOIN books b ON b.id=r.book_id")


def _me() -> int:
    return (USER.get() or {})["id"]


def _date(value: str, field: str) -> str:
    if not value:
        return ""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise HTTPException(400, f"Give the {field} as a date.")
    try:
        date.fromisoformat(value)
    except ValueError:
        raise HTTPException(400, f"That {field} isn't a real date.")
    return value


def _read(rid: int) -> dict:
    r = db.one(COLUMNS + " WHERE r.id=? AND r.user_id=? AND r.library_id=?", (rid, _me(), lib()))
    if not r:
        raise HTTPException(404, "Reading entry not found")
    return r


def mine_for(bid: int) -> dict:
    """Your current read of a book (if reading), your latest one otherwise, and how often you've finished it."""
    rows = db.rows("SELECT * FROM reads WHERE book_id=? AND user_id=? ORDER BY status='reading' DESC, id DESC",
                   (bid, _me()))
    return {"reading": rows[0] if rows else None, "times_finished": sum(r["status"] == "finished" for r in rows)}


@router.post("/books/{bid}/start")
def start(bid: int, is_unlocked: bool = Depends(unlocked)):
    _visible(bid, is_unlocked)
    if db.one("SELECT 1 FROM reads WHERE book_id=? AND user_id=? AND status='reading'", (bid, _me())):
        raise HTTPException(400, "You're already reading this one.")
    rid = db.run("INSERT INTO reads(library_id, book_id, user_id, status, started_on, updated_at) "
                 "VALUES(?,?,?,'reading',?,?)", (lib(), bid, _me(), date.today().isoformat(), time.time())).lastrowid
    return {"id": rid}


class Finished(BaseModel):
    finished_on: str = Field("", max_length=10)


@router.post("/books/{bid}/finished")
def finished_before(bid: int, b: Finished, is_unlocked: bool = Depends(unlocked)):
    """Records a book you've already read, without tracking it page by page."""
    book = _visible(bid, is_unlocked)
    when = _date(b.finished_on, "date you finished it") or date.today().isoformat()
    rid = db.run("INSERT INTO reads(library_id, book_id, user_id, status, page, finished_on, updated_at) "
                 "VALUES(?,?,?,'finished',?,?,?)", (lib(), bid, _me(), book["pages"] or 0, when, time.time())).lastrowid
    return {"id": rid}


class ReadPatch(BaseModel):
    page: int | None = Field(None, ge=0, le=100000)
    status: Literal["reading", "finished", "dnf"] | None = None
    started_on: str | None = Field(None, max_length=10)
    finished_on: str | None = Field(None, max_length=10)


@router.patch("/reads/{rid}")
def update(rid: int, b: ReadPatch, is_unlocked: bool = Depends(unlocked)):
    """Update the page you're on, or finish / stop / pick a book back up."""
    r = _read(rid)
    if r["restricted"] and not is_unlocked:
        raise HTTPException(404, "Reading entry not found")
    sets = {}
    if b.page is not None:
        if r["pages"] and b.page > r["pages"]:
            raise HTTPException(400, f"This book only has {r['pages']} pages.")
        sets["page"] = b.page
    if b.started_on is not None:
        sets["started_on"] = _date(b.started_on, "start date")
    if b.finished_on is not None:
        sets["finished_on"] = _date(b.finished_on, "date")
    if b.status and b.status != r["status"]:
        sets["status"] = b.status
        if b.status == "reading":
            sets["finished_on"] = ""
        else:
            sets.setdefault("finished_on", sets.get("finished_on") or date.today().isoformat())
            if b.status == "finished" and r["pages"]:
                sets["page"] = r["pages"]
    if sets:
        sets["updated_at"] = time.time()
        db.run(f"UPDATE reads SET {', '.join(k + '=?' for k in sets)} WHERE id=?", [*sets.values(), rid])
    return {"ok": True}


@router.delete("/reads/{rid}")
def remove(rid: int):
    _read(rid)
    db.run("DELETE FROM reads WHERE id=?", (rid,))
    return {"ok": True}


@router.get("/reads")
def reads(status: Literal["reading", "finished", "dnf"] = "reading", year: int | None = None,
          is_unlocked: bool = Depends(unlocked)):
    """Your reads in this library with one status. For finished and did-not-finish,
    `year` limits them to the ones that ended that year."""
    sql, args = COLUMNS + " WHERE r.user_id=? AND r.library_id=? AND r.status=?", [_me(), lib(), status]
    if year and status != "reading":
        sql += " AND r.finished_on LIKE ?"
        args.append(f"{year:04d}-%")
    if not is_unlocked:
        sql += " AND b.restricted=0"
    order = " ORDER BY r.updated_at DESC" if status == "reading" else " ORDER BY r.finished_on DESC, r.id DESC"
    return db.rows(sql + order, args)


def _genres(rows: list[dict], limit=8) -> list[dict]:
    counts = Counter()
    for r in rows:
        seen = set()
        for s in (r["subjects"] or "").split(","):
            s = s.strip()
            if s and s.lower() not in GENERIC and s.lower() not in seen:
                seen.add(s.lower())
                counts[s] += 1
    return [{"name": n, "count": c} for n, c in counts.most_common(limit)]


@router.get("/reports")
def report(year: int | None = None, is_unlocked: bool = Depends(unlocked)):
    """Your reading in one year: books finished and not finished, pages, an
    estimate of words, genres, your average rating, and books per month."""
    year = year or date.today().year
    hide = "" if is_unlocked else " AND b.restricted=0"
    base = COLUMNS + f" WHERE r.user_id=? AND r.library_id=?{hide}"
    done = db.rows(base + " AND r.status='finished' AND r.finished_on LIKE ? ORDER BY r.finished_on",
                   (_me(), lib(), f"{year:04d}-%"))
    dnf = db.rows(base + " AND r.status='dnf' AND r.finished_on LIKE ?", (_me(), lib(), f"{year:04d}-%"))
    years = sorted({int(r["y"]) for r in db.rows(
        "SELECT DISTINCT substr(finished_on, 1, 4) AS y FROM reads WHERE user_id=? AND library_id=? AND finished_on!=''",
        (_me(), lib()))} | {date.today().year}, reverse=True)
    pages = sum(r["pages"] or 0 for r in done) + sum(r["page"] or 0 for r in dnf)
    rated = [r["rating"] for r in done if r["rating"] is not None]
    months = [0] * 12
    for r in done:
        months[int(r["finished_on"][5:7]) - 1] += 1
    longest = max((r for r in done if r["pages"]), key=lambda r: r["pages"], default=None)
    return {
        "year": year, "years": years,
        "finished": len(done), "did_not_finish": len(dnf),
        "pages": pages, "books_without_page_count": sum(1 for r in done if not r["pages"]),
        "words_estimate": pages * WORDS_PER_PAGE, "words_per_page": WORDS_PER_PAGE,
        "average_rating": round(sum(rated) / len(rated), 1) if rated else None, "rated": len(rated),
        "genres": _genres(done), "months": months,
        "longest": {"title": longest["title"], "pages": longest["pages"]} if longest else None,
        "books": [{k: r[k] for k in ("book_id", "title", "authors", "cover", "pages", "rating", "finished_on")}
                  for r in reversed(done)],
    }
