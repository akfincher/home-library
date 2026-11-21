# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Aaron Fincher. See LICENSE.
"""A history of changes to the library and who made them, shown in Settings.

Entries about restricted books are hidden unless the restricted section is
unlocked, and entries about accounts and settings ("admin" entries) are only
shown to owners.
"""
import time
from contextvars import ContextVar

from . import db

# The logged-in user for the request being handled (set by the login middleware).
CURRENT_USER: ContextVar[dict | None] = ContextVar("current_user", default=None)
KEEP = 10000  # entries kept; older ones are removed


def q(title: str) -> str:
    """A title in curly quotes, for summaries."""
    return f"“{title}”"


def log(summary: str, book: dict | None = None, restricted: bool = False, admin: bool = False) -> None:
    user = CURRENT_USER.get() or {}
    restricted = restricted or bool(book and book.get("restricted"))
    cur = db.run("INSERT INTO changes(at, user_id, username, summary, book_id, restricted, admin) VALUES(?,?,?,?,?,?,?)",
                 (time.time(), user.get("id"), user.get("username", ""), summary,
                  book.get("id") if book else None, int(restricted), int(admin)))
    if cur.lastrowid % 200 == 0:
        db.run("DELETE FROM changes WHERE id <= ?", (cur.lastrowid - KEEP,))


def entries(is_owner: bool, is_unlocked: bool, before_id: int | None = None, limit: int = 50) -> list[dict]:
    sql = ("SELECT c.id, c.at, c.username, c.summary, c.admin FROM changes c "
           "LEFT JOIN books b ON b.id=c.book_id WHERE 1=1")
    args = []
    if not is_owner:
        sql += " AND c.admin=0"
    if not is_unlocked:
        sql += " AND c.restricted=0 AND COALESCE(b.restricted, 0)=0"
    if before_id:
        sql += " AND c.id < ?"
        args.append(before_id)
    return db.rows(sql + " ORDER BY c.id DESC LIMIT ?", [*args, limit])
