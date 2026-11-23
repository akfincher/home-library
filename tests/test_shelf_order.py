# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Aaron Fincher. See LICENSE.
import glob

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import create_app
from conftest import log_in_as_owner


@pytest.fixture
def c(tmp_path):
    with TestClient(create_app(str(tmp_path / "library.db"))) as client:
        yield log_in_as_owner(client)


def shelf(c, name, person_id=None):
    return c.post("/api/shelves", json={"name": name, "person_id": person_id}).json()["id"]


def book(c, sid, title, **extra):
    return c.post("/api/books", json={"title": title, "shelf_id": sid, **extra}).json()["id"]


def on_shelf(c, sid):
    """Titles in the order a "My order" shelf shows them."""
    bs = c.get(f"/api/books?shelf_id={sid}").json()
    return [b["title"] for b in sorted(bs, key=lambda b: (b["position"] is None, b["position"] or 0))]


def shelf_named(c, name):
    return next(s for s in c.get("/api/shelves").json() if s["name"] == name)


# Arranging by hand

def test_books_are_added_in_scanning_order(c):
    sid = shelf(c, "Hall")
    for t in ["Zebra", "Apple", "Mango"]:
        book(c, sid, t)
    assert on_shelf(c, sid) == ["Zebra", "Apple", "Mango"]


def test_saving_an_order_switches_the_shelf_to_it(c):
    sid = shelf(c, "Hall")
    ids = [book(c, sid, t) for t in ["A", "B", "C"]]
    assert c.put(f"/api/shelves/{sid}/order", json={"book_ids": [ids[2], ids[0], ids[1]]}).status_code == 200
    assert on_shelf(c, sid) == ["C", "A", "B"]
    assert shelf_named(c, "Hall")["sort"] == "custom"


def test_order_must_only_name_books_on_the_shelf(c):
    sid, other = shelf(c, "Hall"), shelf(c, "Den")
    a = book(c, sid, "A")
    elsewhere = book(c, other, "B")
    assert c.put(f"/api/shelves/{sid}/order", json={"book_ids": [a, elsewhere]}).status_code == 400
    assert c.put(f"/api/shelves/{sid}/order", json={"book_ids": [a, a]}).status_code == 400
    assert c.put("/api/shelves/999/order", json={"book_ids": []}).status_code == 404


def test_moved_book_goes_to_the_end(c):
    hall, den = shelf(c, "Hall"), shelf(c, "Den")
    book(c, den, "D1")
    book(c, den, "D2")
    moving = book(c, hall, "Mover")
    c.patch(f"/api/books/{moving}", json={"shelf_id": den, "rating": 3})
    assert on_shelf(c, den) == ["D1", "D2", "Mover"]
    # Saving other details doesn't move it again.
    c.patch(f"/api/books/{moving}", json={"shelf_id": den, "rating": 4})
    assert on_shelf(c, den) == ["D1", "D2", "Mover"]


def test_shelf_remembers_its_sort(c):
    sid = shelf(c, "Hall")
    assert shelf_named(c, "Hall")["sort"] == "title"
    assert c.put(f"/api/shelves/{sid}/sort", json={"sort": "series"}).status_code == 200
    assert shelf_named(c, "Hall")["sort"] == "series"
    assert c.put(f"/api/shelves/{sid}/sort", json={"sort": "colour"}).status_code == 422


def test_order_survives_backup_and_restore(c):
    sid = shelf(c, "Hall")
    ids = [book(c, sid, t) for t in ["A", "B"]]
    c.put(f"/api/shelves/{sid}/order", json={"book_ids": ids[::-1]})
    data = c.get("/api/export.json").json()
    c.post("/api/import", json=data)
    assert on_shelf(c, sid) == ["B", "A"] and shelf_named(c, "Hall")["sort"] == "custom"


# Letting the app organize

def library(c):
    old = shelf(c, "Boxes")
    book(c, old, "Dune", authors="Frank Herbert", subjects="Fiction, Science fiction", series="Dune", series_index=1)
    book(c, old, "Dune Messiah", authors="Frank Herbert", subjects="Science fiction", series="Dune", series_index=2)
    book(c, old, "Foundation", authors="Isaac Asimov", subjects="Fiction, Science fiction")
    hobbit = book(c, old, "The Hobbit", authors="J. R. R. Tolkien", subjects="Fiction, Fantasy")
    c.patch(f"/api/books/{hobbit}", json={"ar_level": 6.6})
    book(c, old, "Mistborn", authors="Brandon Sanderson Jr.", subjects="Fantasy, Magic")
    book(c, old, "Cookbook", authors="", subjects="")
    c.post("/api/books", json={"title": "Wanted", "wishlist": True, "subjects": "Fantasy"})
    return old


def test_preview_by_genre_groups_on_shared_subjects(c):
    library(c)
    p = c.post("/api/organize/preview", json={"rule": "genre", "min_size": 2}).json()
    assert p["total"] == 6 and p["moves"] == 6  # the wish list is left alone
    assert [(g["name"], g["count"]) for g in p["groups"]] == [
        ("Fantasy", 2), ("Science fiction", 3), ("No genre", 1)]
    assert p["groups"][1]["titles"] == ["Foundation", "Dune", "Dune Messiah"]  # by author surname


def test_small_groups_go_to_other(c):
    library(c)
    p = c.post("/api/organize/preview", json={"rule": "series", "min_size": 3}).json()
    assert [(g["name"], g["count"]) for g in p["groups"]] == [("Not in a series", 4), ("Other", 2)]


def test_preview_by_author_and_level(c):
    library(c)
    by_author = c.post("/api/organize/preview", json={"rule": "author", "min_size": 1}).json()
    assert [(g["name"], g["count"]) for g in by_author["groups"]] == [
        ("A–C", 1), ("G–I", 2), ("S–U", 2), ("No author", 1)]
    by_level = c.post("/api/organize/preview", json={"rule": "level", "min_size": 1}).json()
    assert [(g["name"], g["count"]) for g in by_level["groups"]] == [
        ("Reading level 6–7.9", 1), ("No reading level", 5)]


def test_apply_moves_books_and_reuses_shelves(c):
    old = library(c)
    fantasy = shelf(c, "Fantasy")
    r = c.post("/api/organize/apply", json={"rule": "genre", "min_size": 2}).json()
    assert r == {"moved": 6, "shelves": 3, "empty_shelves": 2}  # Boxes and the starting Main shelf
    names = {s["name"]: s for s in c.get("/api/shelves").json()}
    assert names["Fantasy"]["id"] == fantasy and names["Fantasy"]["count"] == 2
    assert names["Science fiction"]["sort"] == "author"
    assert on_shelf(c, names["Science fiction"]["id"]) == ["Foundation", "Dune", "Dune Messiah"]
    assert names["Boxes"]["count"] == 0
    assert len(glob.glob(f"{db.path()}.pre-organize-*.bak")) == 1

    # Doing it again changes nothing.
    assert c.post("/api/organize/preview", json={"rule": "genre", "min_size": 2}).json()["moves"] == 0
    assert c.post("/api/shelves/delete-empty").json() == {"deleted": 2}
    assert all(s["id"] != old for s in c.get("/api/shelves").json())


def test_organize_by_person_gives_each_their_shelf(c):
    mia = c.post("/api/people", json={"name": "Mia"}).json()["id"]
    sid = shelf(c, "Pile")
    book(c, sid, "Hers", person_id=mia)
    book(c, sid, "Ours")
    c.post("/api/organize/apply", json={"rule": "person"})
    shelves = {s["name"]: s for s in c.get("/api/shelves").json()}
    assert shelves["Mia's books"]["person_id"] == mia and shelves["Mia's books"]["count"] == 1
    assert shelves["Shared"]["person_id"] is None and shelves["Shared"]["count"] == 1


def test_organize_one_persons_books_onto_their_own_shelves(c):
    mia = c.post("/api/people", json={"name": "Mia"}).json()["id"]
    sid = shelf(c, "Pile")
    book(c, sid, "Hers", person_id=mia, subjects="Horses")
    book(c, sid, "Ours", subjects="Horses")
    c.post("/api/organize/apply", json={"rule": "genre", "person_id": mia, "min_size": 1})
    horses = next(s for s in c.get("/api/shelves").json() if s["name"] == "Horses")
    assert horses["person_id"] == mia and horses["count"] == 1
    assert shelf_named(c, "Pile")["count"] == 1  # the shared book stayed


def test_restricted_books_stay_put_while_locked(c):
    sid = shelf(c, "Pile")
    book(c, sid, "Open", subjects="Romance")
    secret = book(c, sid, "Secret", subjects="Romance")
    h = {"X-Unlock": c.post("/api/pin", json={"pin": "1234"}).json()["token"]}
    c.patch(f"/api/books/{secret}", json={"restricted": True}, headers=h)
    assert c.post("/api/organize/apply", json={"rule": "genre", "min_size": 1}).status_code == 403
    assert c.post("/api/organize/preview", json={"rule": "genre", "min_size": 1}).json()["total"] == 1
    c.post("/api/organize/apply", json={"rule": "genre", "min_size": 1}, headers=h)
    romance = shelf_named(c, "Romance")
    assert romance["count"] == 1  # count while locked hides the restricted book…
    assert len(c.get(f"/api/books?shelf_id={romance['id']}", headers=h).json()) == 2  # …but it moved, unlocked


def test_nothing_to_organize(c):
    assert c.post("/api/organize/apply", json={"rule": "genre"}).json() == {"moved": 0, "shelves": 0, "empty_shelves": 0}
    assert c.post("/api/organize/preview", json={"rule": "dewey"}).status_code == 422
