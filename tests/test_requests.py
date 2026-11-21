# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Aaron Fincher. See LICENSE.
"""Main shelf, owner account at deploy, ratings, change history, person filter."""
import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import create_app
from conftest import OWNER, log_in_as_owner

MEMBER = {"username": "mia", "password": "purple unicorn 7"}


@pytest.fixture
def c(tmp_path):
    with TestClient(create_app(str(tmp_path / "library.db"))) as client:
        yield log_in_as_owner(client)


def member_of(c):
    c.post("/api/members", json=MEMBER)
    m = TestClient(c.app)
    m.post("/api/auth/login", json=MEMBER)
    return m


def history(client, headers=None):
    return [(e["username"], e["summary"]) for e in client.get("/api/changes", headers=headers).json()]


# Starting out

def test_new_library_starts_with_a_main_shelf(c):
    assert [s["name"] for s in c.get("/api/shelves").json()] == ["Main"]


def test_owner_account_can_come_from_the_deployment(tmp_path, monkeypatch):
    monkeypatch.setenv("OWNER_USERNAME", "aaron")
    monkeypatch.setenv("OWNER_PASSWORD", "from the compose file")
    with TestClient(create_app(str(tmp_path / "a.db"))) as c:
        assert c.get("/api/auth/status").json()["setup_needed"] is False
        assert c.post("/api/auth/login", json={"username": "aaron", "password": "from the compose file"}).status_code == 200
    # Later restarts with a changed password don't touch the existing account.
    monkeypatch.setenv("OWNER_PASSWORD", "something else entirely")
    with TestClient(create_app(str(tmp_path / "a.db"))) as c:
        assert c.post("/api/auth/login", json={"username": "aaron", "password": "from the compose file"}).status_code == 200


def test_too_short_deployment_password_is_ignored(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("OWNER_USERNAME", "aaron")
    monkeypatch.setenv("OWNER_PASSWORD", "short")
    with TestClient(create_app(str(tmp_path / "b.db"))) as c:
        assert c.get("/api/auth/status").json()["setup_needed"] is True
    assert "OWNER_PASSWORD not used" in capsys.readouterr().out


# Ratings

def test_not_rated_is_different_from_zero(c):
    bid = c.post("/api/books", json={"title": "Dune", "wishlist": True}).json()["id"]
    b = c.get(f"/api/books/{bid}").json()
    assert (b["rating"], b["spicy"]) == (None, None)
    c.patch(f"/api/books/{bid}", json={"rating": 0, "spicy": 0})
    b = c.get(f"/api/books/{bid}").json()
    assert (b["rating"], b["spicy"]) == (0, 0)
    c.patch(f"/api/books/{bid}", json={"rating": None})
    assert c.get(f"/api/books/{bid}").json()["rating"] is None


def test_old_backups_treat_zero_as_not_rated(c):
    old = {"app": "home-library", "version": 1, "people": [], "shelves": [],
           "books": [{"id": 1, "title": "A", "rating": 0, "spicy": 0}, {"id": 2, "title": "B", "rating": 4, "spicy": 2}]}
    c.post("/api/import", json=old)
    got = {b["title"]: (b["rating"], b["spicy"]) for b in c.get("/api/books").json()}
    assert got == {"A": (None, None), "B": (4, 2)}
    new = c.get("/api/export.json").json()
    assert new["version"] == 2
    c.patch("/api/books/1", json={"rating": 0})
    c.post("/api/import", json=c.get("/api/export.json").json())
    assert c.get("/api/books/1").json()["rating"] == 0  # a real 0 survives a new-style round trip


def test_upgrade_turns_old_zero_ratings_into_not_rated(tmp_path):
    path = str(tmp_path / "old.db")
    import sqlite3
    con = sqlite3.connect(path)
    for sql in db.MIGRATIONS[:5]:
        con.executescript(sql)
    con.execute(f"PRAGMA user_version={5}")
    con.execute("INSERT INTO shelves(name) VALUES('Hall')")
    con.execute("INSERT INTO books(title, rating, spicy) VALUES('Unrated', 0, 0), ('Rated', 3, 1)")
    con.commit()
    con.close()
    with TestClient(create_app(path)) as c:
        log_in_as_owner(c)
        got = {b["title"]: (b["rating"], b["spicy"]) for b in c.get("/api/books").json()}
        assert got == {"Unrated": (None, None), "Rated": (3, 1)}
        assert [s["name"] for s in c.get("/api/shelves").json()] == ["Hall"]  # no Main added to an existing library


# Change history

def test_history_records_who_did_what(c):
    member = member_of(c)
    main = c.get("/api/shelves").json()[0]["id"]
    bid = member.post("/api/books", json={"title": "Dune", "shelf_id": main}).json()["id"]
    den = c.post("/api/shelves", json={"name": "Den"}).json()["id"]
    member.patch(f"/api/books/{bid}", json={"rating": 5, "review": "Great", "shelf_id": den})
    member.patch(f"/api/books/{bid}", json={"rating": 5})  # nothing actually changed
    member.patch(f"/api/books/{bid}", json={"rating": 0, "spicy": 2, "title": "Dune (1965)"})
    c.delete(f"/api/books/{bid}")
    assert history(c)[:6] == [
        ("owner", "Deleted “Dune (1965)” from “Den”"),
        ("mia", "Changed “Dune (1965)”: title from “Dune” to “Dune (1965)”; rating from 5 stars to 0 stars; "
                "spice from not rated to 2 peppers"),
        ("mia", "Changed “Dune”: rating from not rated to 5 stars; added the review"),
        ("mia", "Moved “Dune” from “Main” to “Den”"),
        ("owner", "Created shelf “Den”"),
        ("mia", "Added “Dune” to “Main”"),
    ]


def test_account_changes_only_shown_to_owners(c):
    member = member_of(c)
    member.post("/api/shelves", json={"name": "Kids"})
    assert ("owner", "Created the account mia as a member") in history(c)
    assert history(member) == [("mia", "Created shelf “Kids”")]


def test_restricted_books_hidden_from_history_while_locked(c):
    h = {"X-Unlock": c.post("/api/pin", json={"pin": "1234"}).json()["token"]}
    bid = c.post("/api/books", json={"title": "Secret", "wishlist": True}).json()["id"]
    c.patch(f"/api/books/{bid}", json={"restricted": True}, headers=h)
    c.patch(f"/api/books/{bid}", json={"review": "shh"}, headers=h)
    locked = [s for _, s in history(c)]
    unlocked = [s for _, s in history(c, h)]
    assert not any("Secret" in s for s in locked)  # even the "Added" from before it was restricted
    assert "Changed “Secret”: added the review" in unlocked and "Added “Secret” to the wish list" in unlocked


def test_history_keeps_the_last_50(c):
    for i in range(60):
        c.post("/api/people", json={"name": f"P{i}"})
    entries = c.get("/api/changes").json()
    assert len(entries) == 50 and entries[0]["summary"] == "Added P59 to the household"
    assert db.one("SELECT COUNT(*) AS n FROM changes")["n"] == 50  # the older ones are gone, not just hidden


def test_organize_and_restore_are_recorded(c):
    c.post("/api/books", json={"title": "Dune", "shelf_id": 1, "subjects": "Science fiction"})
    c.post("/api/organize/apply", json={"rule": "genre", "min_size": 1})
    c.post("/api/import", json=c.get("/api/export.json").json())
    summaries = [s for _, s in history(c)]
    assert summaries[0].startswith("Restored a backup")
    assert "Organized the library by genre: 1 book moved onto 1 shelf" in summaries


# Choosing a person

def test_a_person_sees_their_books_and_shared_ones_only(c):
    mia = c.post("/api/people", json={"name": "Mia"}).json()["id"]
    sam = c.post("/api/people", json={"name": "Sam"}).json()["id"]
    main = c.get("/api/shelves").json()[0]["id"]
    for title, who in [("Hers", mia), ("His", sam), ("Ours", None)]:
        c.post("/api/books", json={"title": title, "shelf_id": main, "person_id": who})
    titles = lambda url: sorted(b["title"] for b in c.get(url).json())
    assert titles(f"/api/books?shelf_id={main}") == ["Hers", "His", "Ours"]  # Everyone
    assert titles(f"/api/books?shelf_id={main}&person_id={mia}&shared=true") == ["Hers", "Ours"]
    assert titles(f"/api/books?person_id={mia}") == ["Hers"]  # without shared: only theirs
    counts = {s["name"]: s["count"] for s in c.get(f"/api/shelves?person_id={mia}").json()}
    assert counts == {"Main": 2}


def test_titles_sort_without_the_a_an():
    from app.routers.organize import sort_title
    titles = ["The Hunger Games", "A Wrinkle in Time", "Dune", "An Ember in the Ashes", "Theory of Everything"]
    assert sorted(titles, key=sort_title) == [
        "Dune", "An Ember in the Ashes", "The Hunger Games", "Theory of Everything", "A Wrinkle in Time"]
