# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Aaron Fincher. See LICENSE.
"""Export, backup and restore. All of these include restricted books, so they
need the PIN once one is set."""
import csv
import io
import os
import sqlite3
import tempfile
import time

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.background import BackgroundTask

from .. import db
from .. import auth
from ..changelog import log
from ..security import require_admin

router = APIRouter(prefix="/api", dependencies=[Depends(auth.require_owner), Depends(require_admin)])

# 2: rating/spicy 0 is a real rating and null means not rated. In version 1, 0 meant not rated.
EXPORT_VERSION = 2
COLUMNS = {
    "people": ["id", "name"],
    "shelves": ["id", "name", "person_id", "sort"],
    "books": ["id", "isbn", "title", "authors", "cover", "shelf_id", "person_id", "wishlist", "restricted",
              "rating", "spicy", "review", "ar_level", "ar_points", "subtitle", "description", "publisher",
              "published", "pages", "subjects", "series", "series_index", "language", "position"],
}
CSV_COLUMNS = ["title", "subtitle", "authors", "series", "series_index", "isbn", "publisher", "published", "pages",
               "subjects", "language", "shelf", "person", "wishlist", "restricted", "rating", "spicy", "ar_level",
               "ar_points", "review", "description"]


def _attachment(ext: str) -> dict:
    return {"Content-Disposition": f'attachment; filename="home-library-{time.strftime("%Y-%m-%d")}.{ext}"'}


@router.get("/export.json")
def export_json():
    data = {"app": "home-library", "version": EXPORT_VERSION, "exported_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    for table, cols in COLUMNS.items():
        data[table] = db.rows(f"SELECT {', '.join(cols)} FROM {table} ORDER BY id")
    return JSONResponse(data, headers=_attachment("json"))


@router.get("/export.csv")
def export_csv():
    rows = db.rows("SELECT b.*, s.name AS shelf, p.name AS person FROM books b "
                   "LEFT JOIN shelves s ON s.id=b.shelf_id LEFT JOIN people p ON p.id=b.person_id "
                   "ORDER BY b.title COLLATE NOCASE")
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=CSV_COLUMNS, extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
    # The BOM makes Excel read the file as UTF-8.
    return Response("﻿" + out.getvalue(), media_type="text/csv; charset=utf-8", headers=_attachment("csv"))


@router.get("/backup.db")
def backup_db():
    fd, tmp = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db.backup_to(tmp)
    return FileResponse(tmp, media_type="application/vnd.sqlite3", headers=_attachment("db"),
                        background=BackgroundTask(os.remove, tmp))


@router.post("/import")
def import_json(data: dict):
    """Replaces everything with the contents of a JSON export. A copy of the
    current database is saved next to it first."""
    if data.get("app") != "home-library" or data.get("version") not in (1, EXPORT_VERSION):
        raise HTTPException(400, "That file isn't a Home Library backup this version can read.")
    for table in COLUMNS:
        if not isinstance(data.get(table), list) or not all(isinstance(r, dict) for r in data[table]):
            raise HTTPException(400, f"The backup's {table} list is missing or damaged.")
    db.backup_to(f"{db.path()}.pre-import-{db.stamp()}.bak")
    try:
        with db.connect() as c:
            for table in ("books", "shelves", "people"):
                c.execute(f"DELETE FROM {table}")
            for table, cols in COLUMNS.items():
                for r in data[table]:
                    if table == "books" and data["version"] == 1:
                        r = {**r, **{k: None for k in ("rating", "spicy") if not r.get(k)}}
                    # Only the columns the file has, so backups from older versions get the defaults.
                    keys = [k for k in cols if k in r]
                    c.execute(f"INSERT INTO {table}({', '.join(keys)}) VALUES({', '.join('?' * len(keys))})",
                              [r[k] for k in keys])
    except sqlite3.Error as e:
        raise HTTPException(400, f"The backup couldn't be restored, so nothing was changed: {e}")
    # Accounts aren't in backups; unlink any from people that no longer exist.
    db.run("UPDATE users SET person_id=NULL WHERE person_id NOT IN (SELECT id FROM people)")
    log(f"Restored a backup ({len(data['books'])} books, {len(data['shelves'])} shelves)")
    return {table: len(data[table]) for table in COLUMNS}
