# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Aaron Fincher. See LICENSE.
"""SQLite access and schema migrations.

Migrations run in order on startup. The database's PRAGMA user_version records
how many have been applied, so add new ones to the end of MIGRATIONS and never
edit one that has already shipped.
"""
import os
import sqlite3
import time
from contextlib import contextmanager

_path: str | None = None

MIGRATIONS = [
    # 1: the original schema. IF NOT EXISTS so databases made before
    # migrations existed pass through unchanged.
    """
    CREATE TABLE IF NOT EXISTS shelves(id INTEGER PRIMARY KEY, name TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS books(id INTEGER PRIMARY KEY, isbn TEXT DEFAULT '', title TEXT NOT NULL,
      authors TEXT DEFAULT '', cover TEXT DEFAULT '', shelf_id INTEGER, wishlist INTEGER DEFAULT 0,
      restricted INTEGER DEFAULT 0, rating INTEGER DEFAULT 0, spicy INTEGER DEFAULT 0,
      review TEXT DEFAULT '', ar_level REAL, ar_points REAL);
    CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY, v TEXT);
    """,
    # 2: people in the household, who can own shelves and books.
    """
    CREATE TABLE people(id INTEGER PRIMARY KEY, name TEXT NOT NULL);
    ALTER TABLE shelves ADD COLUMN person_id INTEGER REFERENCES people(id);
    ALTER TABLE books ADD COLUMN person_id INTEGER REFERENCES people(id);
    CREATE INDEX books_shelf ON books(shelf_id);
    CREATE INDEX books_person ON books(person_id);
    """,
    # 3: richer book details, filled in from ISBNdb / Open Library / Google Books.
    """
    ALTER TABLE books ADD COLUMN subtitle TEXT DEFAULT '';
    ALTER TABLE books ADD COLUMN description TEXT DEFAULT '';
    ALTER TABLE books ADD COLUMN publisher TEXT DEFAULT '';
    ALTER TABLE books ADD COLUMN published TEXT DEFAULT '';
    ALTER TABLE books ADD COLUMN pages INTEGER;
    ALTER TABLE books ADD COLUMN subjects TEXT DEFAULT '';
    ALTER TABLE books ADD COLUMN series TEXT DEFAULT '';
    ALTER TABLE books ADD COLUMN series_index REAL;
    ALTER TABLE books ADD COLUMN language TEXT DEFAULT '';
    """,
    # 4: books in the same order as on the real shelf, and each shelf remembers how it's sorted.
    """
    ALTER TABLE books ADD COLUMN position REAL;
    ALTER TABLE shelves ADD COLUMN sort TEXT DEFAULT 'title';
    CREATE INDEX books_shelf_position ON books(shelf_id, position);
    """,
]


def init(path: str) -> None:
    global _path
    _path = path
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    migrate()


def path() -> str:
    assert _path, "db.init() has not run"
    return _path


@contextmanager
def connect():
    c = sqlite3.connect(path(), timeout=10)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    except BaseException:
        c.rollback()
        raise
    finally:
        c.close()


def rows(sql, args=()):
    with connect() as c:
        return [dict(r) for r in c.execute(sql, args)]


def one(sql, args=()):
    with connect() as c:
        r = c.execute(sql, args).fetchone()
        return dict(r) if r else None


def run(sql, args=()):
    with connect() as c:
        return c.execute(sql, args)


def get_setting(k):
    r = one("SELECT v FROM settings WHERE k=?", (k,))
    return r["v"] if r else None


def put_setting(k, v):
    run("INSERT OR REPLACE INTO settings VALUES(?,?)", (k, v))


def delete_setting(k):
    run("DELETE FROM settings WHERE k=?", (k,))


def backup_to(dest: str) -> None:
    src = sqlite3.connect(path())
    out = sqlite3.connect(dest)
    try:
        src.backup(out)
    finally:
        out.close()
        src.close()


def stamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def migrate() -> None:
    c = sqlite3.connect(path(), isolation_level=None)
    try:
        version = c.execute("PRAGMA user_version").fetchone()[0]
        if version > len(MIGRATIONS):
            raise RuntimeError(
                f"The database is from a newer version of Home Library (schema {version}). "
                "Upgrade the app, or restore an older backup.")
        if version == len(MIGRATIONS):
            return
        has_tables = c.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0] > 0
        if has_tables:
            # Keep a copy of the database as it was before this upgrade.
            backup_to(f"{path()}.pre-v{len(MIGRATIONS)}-{stamp()}.bak")
        for n, sql in enumerate(MIGRATIONS[version:], start=version + 1):
            try:
                c.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version={n};\nCOMMIT;")
            except Exception:
                if c.in_transaction:
                    c.execute("ROLLBACK")
                raise
    finally:
        c.close()
