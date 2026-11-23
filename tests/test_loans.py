# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Aaron Fincher. See LICENSE.
"""Lending books out and checking them back in."""
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from conftest import log_in_as_owner


@pytest.fixture
def c(tmp_path):
    with TestClient(create_app(str(tmp_path / "library.db"))) as client:
        yield log_in_as_owner(client)


def book(c, title):
    return c.post("/api/books", json={"title": title, "shelf_id": c.get("/api/shelves").json()[0]["id"]}).json()["id"]


def test_lend_and_check_back_in(c):
    bid = book(c, "Dune")
    due = (date.today() + timedelta(days=21)).isoformat()
    r = c.post(f"/api/books/{bid}/lend", json={"borrower": "Sam", "due": due, "note": "Careful, signed"})
    assert r.status_code == 200
    b = c.get(f"/api/books/{bid}").json()
    assert (b["on_loan_to"], b["loan_due"]) == ("Sam", due)
    assert c.get("/api/books").json()[0]["on_loan_to"] == "Sam"  # shown on the shelf too
    [loan] = c.get("/api/loans").json()
    assert (loan["title"], loan["borrower"], loan["note"], loan["overdue"], loan["lent_by_name"]) == (
        "Dune", "Sam", "Careful, signed", False, "owner")
    assert c.post(f"/api/books/{bid}/lend", json={"borrower": "Jo"}).status_code == 400  # already out

    assert c.post(f"/api/books/{bid}/return").status_code == 200
    assert c.get(f"/api/books/{bid}").json()["on_loan_to"] is None
    assert c.get("/api/loans").json() == []
    assert [l["borrower"] for l in c.get("/api/loans?returned=true").json()] == ["Sam"]
    assert c.post(f"/api/books/{bid}/return").status_code == 400  # not out any more
    summaries = [e["summary"] for e in c.get("/api/changes").json()]
    d = date.fromisoformat(due)
    assert summaries[:2] == ["“Dune” came back from Sam", f"Lent “Dune” to Sam (due {d.strftime('%b')} {d.day})"]


def test_overdue_books_come_first(c):
    a, b, n = book(c, "Late"), book(c, "Later"), book(c, "No date")
    c.post(f"/api/books/{n}/lend", json={"borrower": "Ann"})
    c.post(f"/api/books/{b}/lend", json={"borrower": "Bo", "due": (date.today() + timedelta(days=5)).isoformat()})
    c.post(f"/api/books/{a}/lend", json={"borrower": "Cy", "due": (date.today() - timedelta(days=2)).isoformat()})
    out = c.get("/api/loans").json()
    assert [(l["title"], l["overdue"]) for l in out] == [("Late", True), ("Later", False), ("No date", False)]


def test_lending_checks(c):
    bid = book(c, "Dune")
    assert c.post(f"/api/books/{bid}/lend", json={"borrower": "  "}).status_code == 400
    assert c.post(f"/api/books/{bid}/lend", json={"borrower": "Sam", "due": "2026-02-30"}).status_code == 400
    assert c.post(f"/api/books/{bid}/lend", json={"borrower": "Sam", "due": "next week"}).status_code == 400
    assert c.post("/api/books/999/lend", json={"borrower": "Sam"}).status_code == 404


def test_loans_stay_inside_their_library(c):
    bid = book(c, "Dune")
    c.post(f"/api/books/{bid}/lend", json={"borrower": "Sam"})
    token = c.post("/api/invites", json={"email": "jo@example.com"}).json()["token"]
    jo = TestClient(c.app)
    jo.post("/api/auth/signup", json={"username": "jo", "password": "jo has a password", "token": token})
    assert jo.get("/api/loans").json() == []
    assert jo.post(f"/api/books/{bid}/return").status_code == 404


def test_restricted_loans_hidden_while_locked(c):
    h = {"X-Unlock": c.post("/api/pin", json={"pin": "1234"}).json()["token"]}
    bid = book(c, "Secret")
    c.patch(f"/api/books/{bid}", json={"restricted": True}, headers=h)
    c.post(f"/api/books/{bid}/lend", json={"borrower": "Sam"}, headers=h)
    assert c.get("/api/loans").json() == []
    assert len(c.get("/api/loans", headers=h).json()) == 1


def test_loans_survive_backup_and_restore(c):
    bid = book(c, "Dune")
    c.post(f"/api/books/{bid}/lend", json={"borrower": "Sam", "due": "2030-01-05"})
    c.post("/api/import", json=c.get("/api/export.json").json())
    [loan] = c.get("/api/loans").json()
    assert (loan["title"], loan["borrower"], loan["due"]) == ("Dune", "Sam", "2030-01-05")


def test_deleting_a_lent_book_removes_its_loan(c):
    bid = book(c, "Dune")
    c.post(f"/api/books/{bid}/lend", json={"borrower": "Sam"})
    c.delete(f"/api/books/{bid}")
    assert c.get("/api/loans").json() == []
