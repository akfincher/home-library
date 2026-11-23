# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Aaron Fincher. See LICENSE.
"""Searching the library by ISBN (typed or scanned), including other editions
of the same book."""
import pytest
from fastapi.testclient import TestClient

from app import metadata
from app.main import create_app
from conftest import log_in_as_owner

HOBBIT_PB = "9780547928227"   # paperback
HOBBIT_HB = "9780618260300"   # hardcover
HOBBIT_HB_10 = "0618260307"   # the same hardcover, 10-digit form


@pytest.fixture
def c(tmp_path, monkeypatch):
    online = {}
    monkeypatch.setattr(metadata, "lookup", lambda isbn, problems=None: online.get(metadata.to_isbn13(isbn)))
    with TestClient(create_app(str(tmp_path / "library.db"))) as client:
        client.online = online
        yield log_in_as_owner(client)


def add(c, **book):
    shelf = c.get("/api/shelves").json()[0]["id"]
    return c.post("/api/books", json={"shelf_id": shelf, **book}).json()["id"]


def test_exact_match_and_other_edition(c):
    pb = add(c, title="The Hobbit", authors="J.R.R. Tolkien", isbn=HOBBIT_PB, pages=300)
    hb = add(c, title="The Hobbit: or There and Back Again", authors="Tolkien, J. R. R.", isbn="978-0-618-26030-0")
    add(c, title="The Hobbit", authors="Someone Else", isbn="9781111111111")  # same title, different author
    add(c, title="Dune", authors="Frank Herbert", isbn="9780441013593")
    r = c.get(f"/api/books/by-isbn/{HOBBIT_PB}").json()
    assert [b["id"] for b in r["matches"]] == [pb]
    assert [b["id"] for b in r["editions"]] == [hb]
    assert r["book"]["title"] == "The Hobbit"


def test_ten_digit_isbn_finds_the_thirteen_digit_copy(c):
    hb = add(c, title="The Hobbit", authors="J.R.R. Tolkien", isbn="978-0-618-26030-0")
    assert [b["id"] for b in c.get(f"/api/books/by-isbn/{HOBBIT_HB_10}").json()["matches"]] == [hb]


def test_not_in_library_but_another_edition_is(c):
    pb = add(c, title="The Hobbit", authors="J.R.R. Tolkien", isbn=HOBBIT_PB)
    c.online[HOBBIT_HB] = {"title": "The Hobbit", "authors": "J. R. R. Tolkien", "isbn": HOBBIT_HB}
    r = c.get(f"/api/books/by-isbn/{HOBBIT_HB}").json()
    assert r["matches"] == []
    assert [b["id"] for b in r["editions"]] == [pb]
    assert r["book"]["authors"] == "J. R. R. Tolkien"


def test_not_in_library_at_all(c):
    add(c, title="Dune", authors="Frank Herbert", isbn="9780441013593")
    r = c.get(f"/api/books/by-isbn/{HOBBIT_HB}").json()
    assert r == {"isbn": HOBBIT_HB, "book": None, "matches": [], "editions": []}
    assert c.get("/api/books/by-isbn/12345").status_code == 400


def test_restricted_books_stay_hidden(c):
    unlocked = {"X-Unlock": c.post("/api/pin", json={"pin": "2468"}).json()["token"]}
    bid = add(c, title="The Hobbit", isbn=HOBBIT_PB)
    c.patch(f"/api/books/{bid}", json={"restricted": True}, headers=unlocked)
    assert c.get(f"/api/books/by-isbn/{HOBBIT_PB}").json()["matches"] == []
    assert len(c.get(f"/api/books/by-isbn/{HOBBIT_PB}", headers=unlocked).json()["matches"]) == 1
