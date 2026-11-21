# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Aaron Fincher. See LICENSE.
"""Each library's history of changes and who made them (the last 50).

Entries about restricted books are hidden unless the restricted section is
unlocked, and entries about members and settings ("admin" entries) are only
shown to the library's owners.
"""
import time

from . import context, db

KEEP = 50  # entries kept per library; older ones are removed


def q(title: str) -> str:
    """A title in curly quotes, for summaries."""
    return f"“{title}”"


def log(summary: str, book: dict | None = None, restricted: bool = False, admin: bool = False) -> None:
    current = context.LIBRARY.get()
    if not current:
        return
    user = context.USER.get() or {}
    restricted = restricted or bool(book and book.get("restricted"))
    db.run("INSERT INTO changes(at, user_id, username, summary, book_id, restricted, admin, library_id) "
           "VALUES(?,?,?,?,?,?,?,?)",
           (time.time(), user.get("id"), user.get("username", ""), summary,
            book.get("id") if book else None, int(restricted), int(admin), current["id"]))
    db.run("DELETE FROM changes WHERE library_id=? AND id NOT IN "
           "(SELECT id FROM changes WHERE library_id=? ORDER BY id DESC LIMIT ?)", (current["id"], current["id"], KEEP))


def entries(is_owner: bool, is_unlocked: bool) -> list[dict]:
    sql = ("SELECT c.id, c.at, c.username, c.summary, c.admin FROM changes c "
           "LEFT JOIN books b ON b.id=c.book_id WHERE c.library_id=?")
    if not is_owner:
        sql += " AND c.admin=0"
    if not is_unlocked:
        sql += " AND c.restricted=0 AND COALESCE(b.restricted, 0)=0"
    return db.rows(sql + " ORDER BY c.id DESC LIMIT ?", (context.lib(), KEEP))
