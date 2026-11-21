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
from ..security import require_admin

router = APIRouter(prefix="/api", dependencies=[Depends(require_admin)])

EXPORT_VERSION = 1
COLUMNS = {
    "people": ["id", "name"],
    "shelves": ["id", "name", "person_id"],
    "books": ["id", "isbn", "title", "authors", "cover", "shelf_id", "person_id", "wishlist", "restricted",
              "rating", "spicy", "review", "ar_level", "ar_points"],
}


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
    rows = db.rows("SELECT b.title, b.authors, b.isbn, s.name AS shelf, p.name AS person, b.wishlist, "
                   "b.restricted, b.rating, b.spicy, b.ar_level, b.ar_points, b.review FROM books b "
                   "LEFT JOIN shelves s ON s.id=b.shelf_id LEFT JOIN people p ON p.id=b.person_id "
                   "ORDER BY b.title COLLATE NOCASE")
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=["title", "authors", "isbn", "shelf", "person", "wishlist", "restricted",
                                        "rating", "spicy", "ar_level", "ar_points", "review"])
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
    if data.get("app") != "home-library" or data.get("version") != EXPORT_VERSION:
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
                    c.execute(f"INSERT INTO {table}({', '.join(cols)}) VALUES({', '.join('?' * len(cols))})",
                              [r.get(k) for k in cols])
    except sqlite3.Error as e:
        raise HTTPException(400, f"The backup couldn't be restored, so nothing was changed: {e}")
    return {table: len(data[table]) for table in COLUMNS}
