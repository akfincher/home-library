# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Aaron Fincher. See LICENSE.
"""Currently reading, finished, did not finish, the yearly report, themes and
the maintenance notice."""
import time
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from conftest import log_in_as_owner

THIS_YEAR = date.today().year


@pytest.fixture
def c(tmp_path):
    with TestClient(create_app(str(tmp_path / "library.db"))) as client:
        yield log_in_as_owner(client)


def book(c, title, pages=None, subjects="", rating=None):
    bid = c.post("/api/books", json={"title": title, "shelf_id": c.get("/api/shelves").json()[0]["id"],
                                      "subjects": subjects, **({"pages": pages} if pages else {})}).json()["id"]
    if rating is not None:
        c.patch(f"/api/books/{bid}", json={"rating": rating})
    return bid


def titles(c, status, year=None):
    return [r["title"] for r in c.get(f"/api/reads?status={status}" + (f"&year={year}" if year else "")).json()]


# Reading

def test_reading_a_book_page_by_page(c):
    bid = book(c, "Dune", pages=600)
    rid = c.post(f"/api/books/{bid}/start").json()["id"]
    assert c.post(f"/api/books/{bid}/start").status_code == 400  # already reading
    assert titles(c, "reading") == ["Dune"]
    assert c.patch(f"/api/reads/{rid}", json={"page": 150}).status_code == 200
    me = c.get(f"/api/books/{bid}").json()
    assert (me["reading"]["status"], me["reading"]["page"], me["times_finished"]) == ("reading", 150, 0)
    assert c.patch(f"/api/reads/{rid}", json={"page": 601}).status_code == 400  # past the last page

    c.patch(f"/api/reads/{rid}", json={"status": "finished"})
    me = c.get(f"/api/books/{bid}").json()
    assert (me["reading"]["status"], me["reading"]["page"], me["times_finished"]) == ("finished", 600, 1)
    assert me["reading"]["finished_on"] == date.today().isoformat()
    assert titles(c, "reading") == [] and titles(c, "finished", THIS_YEAR) == ["Dune"]

    # Reading it again is a new entry.
    c.post(f"/api/books/{bid}/start")
    assert titles(c, "reading") == ["Dune"] and c.get(f"/api/books/{bid}").json()["times_finished"] == 1


def test_did_not_finish(c):
    bid = book(c, "Ulysses", pages=730)
    rid = c.post(f"/api/books/{bid}/start").json()["id"]
    c.patch(f"/api/reads/{rid}", json={"page": 85})
    c.patch(f"/api/reads/{rid}", json={"status": "dnf"})
    assert titles(c, "dnf") == ["Ulysses"] and titles(c, "reading") == []
    assert c.get(f"/api/books/{bid}").json()["reading"]["page"] == 85  # where you stopped
    c.patch(f"/api/reads/{rid}", json={"status": "reading"})  # picked it back up
    assert titles(c, "reading") == ["Ulysses"] and c.get(f"/api/books/{bid}").json()["reading"]["finished_on"] == ""


def test_marking_books_read_in_the_past(c):
    old = book(c, "Old Favourite", pages=300)
    c.post(f"/api/books/{old}/finished", json={"finished_on": "2024-06-01"})
    assert titles(c, "finished", 2024) == ["Old Favourite"]
    assert titles(c, "finished", THIS_YEAR) == []
    assert c.post(f"/api/books/{old}/finished", json={"finished_on": "2024-02-30"}).status_code == 400
    rid = c.get("/api/reads?status=finished").json()[0]["id"]
    assert c.delete(f"/api/reads/{rid}").status_code == 200
    assert titles(c, "finished") == []


def test_reading_is_personal(c):
    bid = book(c, "Dune")
    c.post(f"/api/books/{bid}/start")
    c.post("/api/members", json={"username": "wife", "password": "her own password"})
    wife = TestClient(c.app)
    wife.post("/api/auth/login", json={"username": "wife", "password": "her own password"})
    assert wife.get("/api/reads?status=reading").json() == []
    assert wife.get(f"/api/books/{bid}").json()["reading"] is None
    rid = c.get("/api/reads?status=reading").json()[0]["id"]
    assert wife.patch(f"/api/reads/{rid}", json={"page": 3}).status_code == 404
    assert wife.delete(f"/api/reads/{rid}").status_code == 404


def test_reading_survives_backup_and_restore(c):
    bid = book(c, "Dune", pages=600)
    rid = c.post(f"/api/books/{bid}/start").json()["id"]
    c.patch(f"/api/reads/{rid}", json={"page": 42})
    c.post("/api/import", json=c.get("/api/export.json").json())
    [r] = c.get("/api/reads?status=reading").json()
    assert (r["title"], r["page"]) == ("Dune", 42)


def test_deleting_a_book_removes_its_reading(c):
    bid = book(c, "Dune")
    c.post(f"/api/books/{bid}/start")
    c.delete(f"/api/books/{bid}")
    assert c.get("/api/reads?status=reading").json() == []


# The yearly report

def test_report(c):
    a = book(c, "Dune", pages=600, subjects="Science fiction, Fiction", rating=5)
    b = book(c, "Foundation", pages=250, subjects="Science fiction, Classics", rating=3)
    n = book(c, "No Pages", subjects="Poetry")
    d = book(c, "Ulysses", pages=730)
    for bid in (a, b, n):
        c.post(f"/api/books/{bid}/finished", json={"finished_on": f"{THIS_YEAR}-03-0{bid % 9 + 1}"})
    c.post(f"/api/books/{b}/finished", json={"finished_on": "2020-01-01"})  # another year: not counted
    rid = c.post(f"/api/books/{d}/start").json()["id"]
    c.patch(f"/api/reads/{rid}", json={"page": 100, "status": "dnf"})

    r = c.get(f"/api/reports?year={THIS_YEAR}").json()
    assert (r["finished"], r["did_not_finish"]) == (3, 1)
    assert r["pages"] == 600 + 250 + 100 and r["books_without_page_count"] == 1
    assert r["words_estimate"] == 950 * 250
    assert r["average_rating"] == 4.0 and r["rated"] == 2
    assert r["genres"][0] == {"name": "Science fiction", "count": 2}
    assert "Fiction" not in [g["name"] for g in r["genres"]]  # too broad to be a genre
    assert r["months"][2] == 3 and sum(r["months"]) == 3
    assert r["longest"] == {"title": "Dune", "pages": 600}
    assert r["years"][:1] == [THIS_YEAR] and 2020 in r["years"]
    assert c.get("/api/reports?year=2020").json()["finished"] == 1


def test_empty_report(c):
    r = c.get("/api/reports").json()
    assert (r["year"], r["finished"], r["pages"], r["average_rating"], r["longest"]) == (THIS_YEAR, 0, 0, None, None)


# Themes and the maintenance notice

def test_theme_follows_the_account(c):
    assert c.get("/api/auth/status").json()["user"]["theme"] == "classic"
    assert c.put("/api/auth/theme", json={"theme": "steampunk"}).status_code == 200
    assert c.put("/api/auth/theme", json={"theme": "neon-glitter"}).status_code == 400
    phone = TestClient(c.app)
    phone.post("/api/auth/login", json={"username": "owner", "password": "correct horse battery"})
    assert phone.get("/api/auth/status").json()["user"]["theme"] == "steampunk"


def test_maintenance_notice(c):
    assert c.get("/api/auth/status").json()["notice"] is None
    until = time.time() + 3600
    r = c.put("/api/server/notice", json={"message": "Updating tonight at 9 pm, back by 9:15.", "until": until})
    assert r.status_code == 200
    anyone = TestClient(c.app)  # shown even on the login page
    notice = anyone.get("/api/auth/status").json()["notice"]
    assert notice["message"] == "Updating tonight at 9 pm, back by 9:15." and notice["until"] == until

    c.put("/api/server/notice", json={"message": "Already over", "until": time.time() - 1})
    assert anyone.get("/api/auth/status").json()["notice"] is None  # expired
    c.put("/api/server/notice", json={"message": ""})
    assert anyone.get("/api/auth/status").json()["notice"] is None

    c.post("/api/members", json={"username": "wife", "password": "her own password"})
    wife = TestClient(c.app)
    wife.post("/api/auth/login", json={"username": "wife", "password": "her own password"})
    assert wife.put("/api/server/notice", json={"message": "hi"}).status_code == 403
