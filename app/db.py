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
    # 5: user accounts and login sessions. Only a hash of each session token is stored.
    """
    CREATE TABLE users(id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
      password TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'member', person_id INTEGER REFERENCES people(id));
    CREATE TABLE sessions(token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, expires REAL NOT NULL);
    CREATE INDEX sessions_user ON sessions(user_id);
    """,
    # 6: "not rated" (NULL) is now different from 0 stars / no spice, which used to mean the same.
    #    A history of changes and who made them. New libraries start with a Main shelf.
    """
    UPDATE books SET rating=NULL WHERE rating=0;
    UPDATE books SET spicy=NULL WHERE spicy=0;
    CREATE TABLE changes(id INTEGER PRIMARY KEY, at REAL NOT NULL, user_id INTEGER, username TEXT NOT NULL DEFAULT '',
      summary TEXT NOT NULL, book_id INTEGER, restricted INTEGER NOT NULL DEFAULT 0, admin INTEGER NOT NULL DEFAULT 0);
    CREATE INDEX changes_at ON changes(at);
    INSERT INTO shelves(name) SELECT 'Main' WHERE NOT EXISTS (SELECT 1 FROM shelves);
    """,
    # 7: many libraries on one server. Everything so far becomes library 1, its
    #    accounts become its members (owners stay owners and also run the server),
    #    and the restricted-section PIN moves onto the library.
    """
    CREATE TABLE libraries(id INTEGER PRIMARY KEY, name TEXT NOT NULL, pin TEXT);
    INSERT INTO libraries(id, name, pin) VALUES(1, 'Home Library', (SELECT v FROM settings WHERE k='pin'));
    DELETE FROM settings WHERE k='pin';
    CREATE TABLE memberships(library_id INTEGER NOT NULL, user_id INTEGER NOT NULL, role TEXT NOT NULL DEFAULT 'member',
      person_id INTEGER, PRIMARY KEY(library_id, user_id));
    INSERT INTO memberships(library_id, user_id, role, person_id) SELECT 1, id, role, person_id FROM users;
    ALTER TABLE users ADD COLUMN site_admin INTEGER NOT NULL DEFAULT 0;
    UPDATE users SET site_admin=1 WHERE role='admin';
    ALTER TABLE sessions ADD COLUMN library_id INTEGER;
    ALTER TABLE people ADD COLUMN library_id INTEGER NOT NULL DEFAULT 1;
    ALTER TABLE shelves ADD COLUMN library_id INTEGER NOT NULL DEFAULT 1;
    ALTER TABLE books ADD COLUMN library_id INTEGER NOT NULL DEFAULT 1;
    ALTER TABLE changes ADD COLUMN library_id INTEGER NOT NULL DEFAULT 1;
    CREATE INDEX people_library ON people(library_id);
    CREATE INDEX shelves_library ON shelves(library_id);
    CREATE INDEX books_library ON books(library_id);
    CREATE INDEX changes_library ON changes(library_id, id);
    CREATE INDEX memberships_user ON memberships(user_id);
    """,
    # 8: sign-up only with an invite code. Codes are single-use and expire; only a hash
    #    is stored. The administrator chooses which accounts may create them.
    """
    CREATE TABLE invites(id INTEGER PRIMARY KEY, code_hash TEXT NOT NULL UNIQUE, hint TEXT NOT NULL,
      note TEXT NOT NULL DEFAULT '', created_by INTEGER NOT NULL, created_at REAL NOT NULL, expires_at REAL NOT NULL,
      used_by INTEGER, used_at REAL, revoked INTEGER NOT NULL DEFAULT 0);
    ALTER TABLE users ADD COLUMN can_invite INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE users ADD COLUMN invited_by INTEGER;
    DELETE FROM settings WHERE k='signups';
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
