# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Aaron Fincher. See LICENSE.
"""Lending books out and checking them back in. Everyone in a library can see
what's on loan, to whom and until when."""
import re
import time
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import db
from ..changelog import log, q
from ..context import USER, lib
from ..security import unlocked
from .books import _visible

router = APIRouter(prefix="/api")


class Lend(BaseModel):
    borrower: str = Field(max_length=100)
    due: str = Field("", max_length=10)  # YYYY-MM-DD, or blank for no due date
    note: str = Field("", max_length=500)


def _due_text(due: str) -> str:
    if not due:
        return ""
    d = date.fromisoformat(due)
    return f" (due {d.strftime('%b')} {d.day})"


@router.post("/books/{bid}/lend")
def lend(bid: int, b: Lend, is_unlocked: bool = Depends(unlocked)):
    book = _visible(bid, is_unlocked)
    borrower = b.borrower.strip()
    if not borrower:
        raise HTTPException(400, "Who are you lending it to?")
    if b.due and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", b.due):
        raise HTTPException(400, "Give the due date as a date.")
    if b.due:
        try:
            date.fromisoformat(b.due)
        except ValueError:
            raise HTTPException(400, "That due date isn't a real date.")
    if db.one("SELECT 1 FROM loans WHERE book_id=? AND returned_at IS NULL", (bid,)):
        raise HTTPException(400, "That book is already on loan. Check it back in first.")
    db.run("INSERT INTO loans(library_id, book_id, borrower, note, lent_at, due, lent_by) VALUES(?,?,?,?,?,?,?)",
           (lib(), bid, borrower, b.note.strip(), time.time(), b.due, (USER.get() or {}).get("id")))
    log(f"Lent {q(book['title'])} to {borrower}{_due_text(b.due)}", book)
    return {"ok": True}


@router.post("/books/{bid}/return")
def check_in(bid: int, is_unlocked: bool = Depends(unlocked)):
    book = _visible(bid, is_unlocked)
    loan = db.one("SELECT * FROM loans WHERE book_id=? AND returned_at IS NULL", (bid,))
    if not loan:
        raise HTTPException(400, "That book isn't on loan.")
    db.run("UPDATE loans SET returned_at=?, returned_by=? WHERE id=?",
           (time.time(), (USER.get() or {}).get("id"), loan["id"]))
    log(f"{q(book['title'])} came back from {loan['borrower']}", book)
    return {"ok": True}


@router.get("/loans")
def loans(returned: bool = False, is_unlocked: bool = Depends(unlocked)):
    """Books out on loan now, overdue first then by due date. With returned=true,
    the last 100 that came back instead."""
    hide = "" if is_unlocked else " AND b.restricted=0"
    sql = ("SELECT l.*, b.title, b.authors, b.cover, s.name AS shelf, u.username AS lent_by_name FROM loans l "
           "JOIN books b ON b.id=l.book_id LEFT JOIN shelves s ON s.id=b.shelf_id "
           f"LEFT JOIN users u ON u.id=l.lent_by WHERE l.library_id=?{hide}")
    if returned:
        return db.rows(sql + " AND l.returned_at IS NOT NULL ORDER BY l.returned_at DESC LIMIT 100", (lib(),))
    rows = db.rows(sql + " AND l.returned_at IS NULL", (lib(),))
    today = date.today().isoformat()
    for r in rows:
        r["overdue"] = bool(r["due"]) and r["due"] < today
    return sorted(rows, key=lambda r: (not r["overdue"], r["due"] or "9999", r["lent_at"]))
