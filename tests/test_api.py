# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Aaron Fincher. See LICENSE.
import glob
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import create_app
from conftest import log_in_as_owner


@pytest.fixture
def make_client(tmp_path):
    def make(path=None):
        return TestClient(create_app(str(path or tmp_path / "library.db")))
    return make


@pytest.fixture
def c(make_client):
    with make_client() as client:
        yield log_in_as_owner(client)


def unlock(c, pin="1234"):
    r = c.post("/api/pin", json={"pin": pin})
    assert r.status_code == 200, r.text
    return {"X-Unlock": r.json()["token"]}


def test_upgrades_a_database_from_the_original_app(tmp_path, make_client):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript(db.MIGRATIONS[0])
    old.execute("INSERT INTO shelves(name) VALUES('Living room')")
    old.execute("INSERT INTO books(title, shelf_id, rating) VALUES('Dune', 1, 5)")
    old.commit()
    old.close()

    with make_client(path) as c:
        log_in_as_owner(c)
        books = c.get("/api/books").json()
        assert [(b["title"], b["shelf"], b["rating"], b["person_id"]) for b in books] == [("Dune", "Living room", 5, None)]

    assert sqlite3.connect(path).execute("PRAGMA user_version").fetchone()[0] == len(db.MIGRATIONS)
    assert len(glob.glob(f"{path}.pre-v*.bak")) == 1


def test_fresh_database_makes_no_upgrade_backup(tmp_path, make_client):
    with make_client(tmp_path / "new.db"):
        pass
    assert glob.glob(f"{tmp_path}/*.bak") == []


def test_people_own_shelves_and_wish_lists(c):
    mia = c.post("/api/people", json={"name": "Mia"}).json()["id"]
    sam = c.post("/api/people", json={"name": "Sam"}).json()["id"]
    mias = c.post("/api/shelves", json={"name": "Mia's room", "person_id": mia}).json()["id"]
    c.post("/api/shelves", json={"name": "Sam's room", "person_id": sam})
    c.post("/api/shelves", json={"name": "Hallway"})

    assert {s["name"] for s in c.get(f"/api/shelves?person_id={mia}").json()} == {"Mia's room", "Hallway", "Main"}

    # A book on Mia's shelf belongs to Mia unless told otherwise.
    bid = c.post("/api/books", json={"title": "Matilda", "shelf_id": mias}).json()["id"]
    assert c.get(f"/api/books/{bid}").json()["person"] == "Mia"
    c.post("/api/books", json={"title": "Wanted", "wishlist": True, "person_id": sam})
    assert [b["title"] for b in c.get(f"/api/books?wishlist=1&person_id={sam}").json()] == ["Wanted"]
    assert c.get(f"/api/books?wishlist=1&person_id={mia}").json() == []

    # Removing a person keeps their things, now shared.
    assert c.delete(f"/api/people/{mia}").status_code == 200
    assert c.get(f"/api/books/{bid}").json()["person_id"] is None
    assert c.post("/api/shelves", json={"name": "x", "person_id": mia}).status_code == 400


def test_shelf_rename(c):
    sid = c.post("/api/shelves", json={"name": "Old"}).json()["id"]
    assert c.put(f"/api/shelves/{sid}", json={"name": "New"}).status_code == 200
    assert [s["name"] for s in c.get("/api/shelves").json()] == ["Main", "New"]


def test_book_patch_is_validated(c):
    bid = c.post("/api/books", json={"title": "Dune", "wishlist": True}).json()["id"]
    assert c.patch(f"/api/books/{bid}", json={"rating": 9}).status_code == 422
    assert c.patch(f"/api/books/{bid}", json={"title": "  "}).status_code == 400
    assert c.patch(f"/api/books/{bid}", json={"shelf_id": 999}).status_code == 400
    assert c.patch(f"/api/books/{bid}", json={"rating": 4, "ar_level": 5.2}).status_code == 200
    b = c.get(f"/api/books/{bid}").json()
    assert (b["rating"], b["ar_level"], b["title"]) == (4, 5.2, "Dune")


def test_wrong_pins_are_throttled(c):
    unlock(c)
    for _ in range(5):
        assert c.post("/api/unlock", json={"pin": "0000"}).status_code == 403
    r = c.post("/api/unlock", json={"pin": "0000"})
    assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0
    # Even the right PIN waits out the lockout.
    assert c.post("/api/unlock", json={"pin": "1234"}).status_code == 429


def test_changing_the_pin_ends_old_unlocks(c):
    old = unlock(c)
    c.post("/api/books", json={"title": "Secret", "wishlist": True})
    bid = c.get("/api/books").json()[0]["id"]
    c.patch(f"/api/books/{bid}", json={"restricted": True}, headers=old)
    assert c.get(f"/api/books/{bid}", headers=old).status_code == 200

    r = c.post("/api/pin", json={"pin": "5678", "current": "1234"})
    assert r.status_code == 200
    assert c.get(f"/api/books/{bid}", headers=old).status_code == 404
    assert c.get(f"/api/books/{bid}", headers={"X-Unlock": r.json()["token"]}).status_code == 200
    assert c.post("/api/pin", json={"pin": "9999", "current": "nope"}).status_code == 403


def test_restricted_books_stay_hidden(c):
    bid = c.post("/api/books", json={"title": "Secret", "wishlist": True}).json()["id"]
    assert c.patch(f"/api/books/{bid}", json={"restricted": True}).status_code == 400  # no PIN yet
    h = unlock(c)
    c.patch(f"/api/books/{bid}", json={"restricted": True}, headers=h)

    # While locked it looks like it doesn't exist, the same as a missing book.
    assert c.get(f"/api/books/{bid}").status_code == 404
    assert c.patch(f"/api/books/{bid}", json={"rating": 1}).status_code == 404
    assert c.delete(f"/api/books/{bid}").status_code == 404
    assert c.get("/api/books?q=Secret").json() == []
    assert len(c.get("/api/books?q=Secret", headers=h).json()) == 1


def test_deleting_a_shelf_needs_the_pin_once_set(c):
    sid = c.post("/api/shelves", json={"name": "Spare"}).json()["id"]
    h = unlock(c)
    assert c.delete(f"/api/shelves/{sid}").status_code == 403
    assert c.delete(f"/api/shelves/{sid}", headers=h).status_code == 200


def test_shelf_with_books_cannot_be_deleted(c):
    sid = c.post("/api/shelves", json={"name": "Full"}).json()["id"]
    c.post("/api/books", json={"title": "Dune", "shelf_id": sid})
    assert c.delete(f"/api/shelves/{sid}").status_code == 400


def test_export_and_restore_round_trip(c):
    mia = c.post("/api/people", json={"name": "Mia"}).json()["id"]
    sid = c.post("/api/shelves", json={"name": "Den", "person_id": mia}).json()["id"]
    c.post("/api/books", json={"title": "Dune", "authors": "Frank Herbert", "shelf_id": sid})
    h = unlock(c)

    assert c.get("/api/export.json").status_code == 403
    exported = c.get("/api/export.json", headers=h)
    assert "attachment" in exported.headers["content-disposition"]
    data = exported.json()
    assert "pin" not in str(data) and "secret" not in str(data)

    csv = c.get("/api/export.csv", headers=h).text
    assert "Dune,,Frank Herbert" in csv and "Den,Mia" in csv

    c.post("/api/books", json={"title": "Added later", "wishlist": True})
    r = c.post("/api/import", json=data, headers=h)
    assert r.json() == {"people": 1, "shelves": 2, "books": 1}  # Den and the starting Main shelf
    assert [b["title"] for b in c.get("/api/books").json()] == ["Dune"]
    assert c.get("/api/books").json()[0]["person"] == "Mia"
    assert len(glob.glob(f"{db.path()}.pre-import-*.bak")) == 1

    assert c.post("/api/import", json={"app": "other"}, headers=h).status_code == 400
    bad = dict(data, books=[{"id": 1}])  # no title: rejected, nothing changed
    assert c.post("/api/import", json=bad, headers=h).status_code == 400
    assert [b["title"] for b in c.get("/api/books").json()] == ["Dune"]


def test_database_backup_download(c, tmp_path):
    c.post("/api/books", json={"title": "Dune", "wishlist": True})
    r = c.get("/api/backup.db")
    assert r.status_code == 200
    out = tmp_path / "dl.db"
    out.write_bytes(r.content)
    assert sqlite3.connect(out).execute("SELECT title FROM books").fetchall() == [("Dune",)]


def test_refuses_a_database_from_a_newer_version(tmp_path, make_client):
    path = tmp_path / "future.db"
    sqlite3.connect(path).execute(f"PRAGMA user_version={len(db.MIGRATIONS) + 1}")
    with pytest.raises(RuntimeError, match="newer version"):
        with make_client(path):
            pass


def test_index_and_health(c):
    assert "Home Library" in c.get("/").text
    assert c.get("/healthz").json() == {"ok": True}


def test_home_screen_app_files(c):
    manifest = c.get("/static/manifest.json").json()
    assert manifest["display"] == "standalone"
    for icon in manifest["icons"]:
        r = c.get(icon["src"])
        assert r.status_code == 200 and r.content.startswith(b"\x89PNG")
    assert c.get("/static/apple-touch-icon.png").status_code == 200


def test_about_points_to_the_source(c, monkeypatch):
    about = c.get("/api/about").json()
    assert about == {"license": "AGPL-3.0-or-later", "source": "https://github.com/akfincher/home-library"}
