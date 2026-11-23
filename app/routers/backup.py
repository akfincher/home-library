# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Aaron Fincher. See LICENSE.
"""Export, backup and restore of the current library. These include restricted
books, so they need the PIN once one is set. The whole-database file holds
every library on the server, so only the server's administrator can download it."""
import csv
import io
import os
import sqlite3
import tempfile
import time

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.background import BackgroundTask

from .. import auth, db
from ..changelog import log
from ..context import lib
from ..security import require_admin

router = APIRouter(prefix="/api")
OWNER = [Depends(auth.require_owner), Depends(require_admin)]

# 2: rating/spicy 0 is a real rating and null means not rated. In version 1, 0 meant not rated.
EXPORT_VERSION = 2
COLUMNS = {
    "people": ["id", "name"],
    "shelves": ["id", "name", "person_id", "sort"],
    "books": ["id", "isbn", "title", "authors", "cover", "shelf_id", "person_id", "wishlist", "restricted",
              "rating", "spicy", "review", "ar_level", "ar_points", "subtitle", "description", "publisher",
              "published", "pages", "subjects", "series", "series_index", "language", "position"],
}
READ_COLUMNS = ["book_id", "user_id", "status", "page", "started_on", "finished_on", "updated_at"]
LOAN_COLUMNS = ["book_id", "borrower", "note", "lent_at", "due", "returned_at"]
CSV_COLUMNS = ["title", "subtitle", "authors", "series", "series_index", "isbn", "publisher", "published", "pages",
               "subjects", "language", "shelf", "person", "wishlist", "restricted", "rating", "spicy", "ar_level",
               "ar_points", "review", "description"]


def _attachment(ext: str) -> dict:
    return {"Content-Disposition": f'attachment; filename="home-library-{time.strftime("%Y-%m-%d")}.{ext}"'}


@router.get("/export.json", dependencies=OWNER)
def export_json():
    data = {"app": "home-library", "version": EXPORT_VERSION, "exported_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    for table, cols in COLUMNS.items():
        data[table] = db.rows(f"SELECT {', '.join(cols)} FROM {table} WHERE library_id=? ORDER BY id", (lib(),))
    data["loans"] = db.rows(f"SELECT {', '.join(LOAN_COLUMNS)} FROM loans WHERE library_id=? ORDER BY id", (lib(),))
    data["reads"] = db.rows(f"SELECT {', '.join(READ_COLUMNS)} FROM reads WHERE library_id=? ORDER BY id", (lib(),))
    return JSONResponse(data, headers=_attachment("json"))


@router.get("/export.csv", dependencies=OWNER)
def export_csv():
    rows = db.rows("SELECT b.*, s.name AS shelf, p.name AS person FROM books b "
                   "LEFT JOIN shelves s ON s.id=b.shelf_id LEFT JOIN people p ON p.id=b.person_id "
                   "WHERE b.library_id=? ORDER BY b.title COLLATE NOCASE", (lib(),))
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=CSV_COLUMNS, extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
    # The BOM makes Excel read the file as UTF-8.
    return Response("﻿" + out.getvalue(), media_type="text/csv; charset=utf-8", headers=_attachment("csv"))


@router.get("/backup.db", dependencies=[Depends(auth.require_site_admin)])
def backup_db():
    fd, tmp = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db.backup_to(tmp)
    return FileResponse(tmp, media_type="application/vnd.sqlite3", headers=_attachment("db"),
                        background=BackgroundTask(os.remove, tmp))


def _insert(c, table: str, row: dict) -> int:
    keys = list(row)
    return c.execute(f"INSERT INTO {table}({', '.join(keys)}) VALUES({', '.join('?' * len(keys))})",
                     [row[k] for k in keys]).lastrowid


@router.post("/import", dependencies=OWNER)
def import_json(data: dict):
    """Replaces this library's people, shelves and books with a JSON export.
    Everything gets new ids, so a backup can be restored into any library. A copy
    of the database is saved next to it first."""
    if data.get("app") != "home-library" or data.get("version") not in (1, EXPORT_VERSION):
        raise HTTPException(400, "That file isn't a Home Library backup this version can read.")
    for table in COLUMNS:
        if not isinstance(data.get(table), list) or not all(isinstance(r, dict) for r in data[table]):
            raise HTTPException(400, f"The backup's {table} list is missing or damaged.")
    db.backup_to(f"{db.path()}.pre-import-{db.stamp()}.bak")
    library = lib()
    try:
        with db.connect() as c:
            c.execute("UPDATE memberships SET person_id=NULL WHERE library_id=?", (library,))
            for table in ("reads", "loans", "books", "shelves", "people"):
                c.execute(f"DELETE FROM {table} WHERE library_id=?", (library,))
            # Only the columns the file has, so backups from older versions get the defaults.
            pick = lambda table, r: {k: r[k] for k in COLUMNS[table] if k in r and k != "id"}
            people = {r.get("id"): _insert(c, "people", {**pick("people", r), "library_id": library})
                      for r in data["people"]}
            shelves, books = {}, {}
            for r in data["shelves"]:
                row = {**pick("shelves", r), "library_id": library, "person_id": people.get(r.get("person_id"))}
                shelves[r.get("id")] = _insert(c, "shelves", row)
            for r in data["books"]:
                row = {**pick("books", r), "library_id": library,
                       "shelf_id": shelves.get(r.get("shelf_id")), "person_id": people.get(r.get("person_id"))}
                if data["version"] == 1:
                    row.update({k: None for k in ("rating", "spicy") if not r.get(k)})
                books[r.get("id")] = _insert(c, "books", row)
            # Loans arrived later, so older backups simply have none.
            for r in data.get("loans") or []:
                if isinstance(r, dict) and books.get(r.get("book_id")) and r.get("borrower") and r.get("lent_at"):
                    _insert(c, "loans", {**{k: r.get(k) for k in LOAN_COLUMNS if k in r}, "library_id": library,
                                         "book_id": books[r["book_id"]], "due": r.get("due") or ""})
            # Each person's reading, kept for people who are members of this library.
            members = {m[0] for m in c.execute("SELECT user_id FROM memberships WHERE library_id=?", (library,))}
            for r in data.get("reads") or []:
                if (isinstance(r, dict) and books.get(r.get("book_id")) and r.get("user_id") in members
                        and r.get("status") in ("reading", "finished", "dnf")):
                    _insert(c, "reads", {**{k: r.get(k) for k in READ_COLUMNS if k in r}, "library_id": library,
                                         "book_id": books[r["book_id"]], "updated_at": r.get("updated_at") or 0})
    except sqlite3.Error as e:
        raise HTTPException(400, f"The backup couldn't be restored, so nothing was changed: {e}")
    log(f"Restored a backup ({len(data['books'])} books, {len(data['shelves'])} shelves)")
    return {table: len(data[table]) for table in COLUMNS}
