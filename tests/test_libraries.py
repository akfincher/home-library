# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Aaron Fincher. See LICENSE.
"""Several libraries on one server: sign-up, switching, sharing, and keeping
each library's books away from everyone else."""
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import create_app
from conftest import OWNER, log_in_as_owner

FRIEND = {"username": "jo", "password": "jo's own password", "library": "Jo's Books"}


@pytest.fixture
def c(tmp_path):
    with TestClient(create_app(str(tmp_path / "library.db"))) as client:
        yield log_in_as_owner(client)


def open_signups(c):
    """Sign-up needs an invite code now; kept so the library tests read the same."""


def sign_up(c, who=FRIEND):
    token = c.post("/api/invites", json={"email": f"{who['username']}@example.com"}).json()["token"]
    friend = TestClient(c.app)
    r = friend.post("/api/auth/signup", json={**who, "token": token})
    assert r.status_code == 200, r.text
    return friend


def me(client):
    return client.get("/api/auth/status").json()["user"]


# Signing up

def test_signing_up_with_an_invite(c):
    friend = sign_up(c)
    them = me(friend)
    assert them["library"]["name"] == "Jo's Books" and them["library"]["role"] == "admin"
    assert them["site_admin"] is False and them["can_invite"] is False
    assert [s["name"] for s in friend.get("/api/shelves").json()] == ["Main"]
    assert friend.get("/api/server/accounts").status_code == 403  # owns their library, not the server
    default = sign_up(c, {"username": "sam", "password": "sam's password!"})
    assert me(default)["library"]["name"] == "sam's Library"


# Keeping libraries apart

def test_one_library_cannot_see_or_touch_another(c):
    open_signups(c)
    main = c.get("/api/shelves").json()[0]["id"]
    pid = c.post("/api/people", json={"name": "Mia"}).json()["id"]
    bid = c.post("/api/books", json={"title": "Our Secret Diary", "shelf_id": main, "person_id": pid}).json()["id"]
    friend = sign_up(c)

    assert friend.get("/api/books").json() == []
    assert friend.get("/api/books?q=diary").json() == []
    assert [s["name"] for s in friend.get("/api/shelves").json()] == ["Main"]
    assert friend.get("/api/people").json() == []
    assert friend.get(f"/api/books/{bid}").status_code == 404
    assert friend.patch(f"/api/books/{bid}", json={"title": "Mine now"}).status_code == 404
    assert friend.delete(f"/api/books/{bid}").status_code == 404
    assert friend.put(f"/api/shelves/{main}", json={"name": "x"}).status_code == 404
    assert friend.delete(f"/api/shelves/{main}").status_code == 404
    assert friend.put(f"/api/shelves/{main}/order", json={"book_ids": [bid]}).status_code == 404
    assert friend.post("/api/books", json={"title": "Sneaky", "shelf_id": main}).status_code == 400
    assert friend.patch(f"/api/people/{pid}", json={"name": "x"}).status_code == 404
    assert friend.post("/api/books", json={"title": "x", "wishlist": True, "person_id": pid}).status_code == 400
    assert friend.post("/api/organize/preview", json={"rule": "genre"}).json()["total"] == 0
    assert "Diary" not in friend.get("/api/export.json").text
    assert "Diary" not in friend.get("/api/export.csv").text
    assert not any("Diary" in e["summary"] for e in friend.get("/api/changes").json())
    assert [m["username"] for m in friend.get("/api/members").json()] == ["jo"]
    assert friend.post("/api/shelves/delete-empty").json() == {"deleted": 1}  # only their own empty Main

    assert c.get(f"/api/books/{bid}").json()["title"] == "Our Secret Diary"  # untouched
    assert [s["name"] for s in c.get("/api/shelves").json()] == ["Main"]


def test_restoring_a_backup_only_replaces_your_own_library(c):
    open_signups(c)
    c.post("/api/books", json={"title": "Home Book", "shelf_id": c.get("/api/shelves").json()[0]["id"]})
    backup = c.get("/api/export.json").json()
    friend = sign_up(c)
    assert friend.post("/api/import", json=backup).status_code == 200
    assert [b["title"] for b in friend.get("/api/books").json()] == ["Home Book"]
    friend.post("/api/import", json={**backup, "books": []})
    assert friend.get("/api/books").json() == []
    assert [b["title"] for b in c.get("/api/books").json()] == ["Home Book"]  # the original is untouched


def test_each_library_has_its_own_pin(c):
    open_signups(c)
    h = {"X-Unlock": c.post("/api/pin", json={"pin": "1234"}).json()["token"]}
    secret = c.post("/api/books", json={"title": "Secret", "wishlist": True}).json()["id"]
    c.patch(f"/api/books/{secret}", json={"restricted": True}, headers=h)
    friend = sign_up(c)
    assert friend.get("/api/pin-status").json()["set"] is False
    assert friend.post("/api/unlock", json={"pin": "1234"}).status_code == 403  # not their PIN
    assert friend.get("/api/books", headers=h).json() == []  # another library's unlock does nothing here
    friend_pin = {"X-Unlock": friend.post("/api/pin", json={"pin": "9999"}).json()["token"]}
    assert c.get(f"/api/books/{secret}", headers=friend_pin).status_code == 404  # nor the other way round


# Several libraries for one person

def test_create_and_switch_between_libraries(c):
    home = me(c)["library"]["id"]
    c.post("/api/books", json={"title": "Home Book", "wishlist": True})
    classroom = c.post("/api/libraries", json={"name": "Room 12"}).json()["id"]
    assert me(c)["library"]["name"] == "Room 12"  # switched straight away
    assert c.get("/api/books").json() == []
    c.post("/api/books", json={"title": "Class Book", "wishlist": True})
    assert [l["name"] for l in me(c)["libraries"]] == ["Home Library", "Room 12"]

    c.post(f"/api/libraries/{home}/switch")
    assert [b["title"] for b in c.get("/api/books").json()] == ["Home Book"]
    c.post(f"/api/libraries/{classroom}/switch")
    assert [b["title"] for b in c.get("/api/books").json()] == ["Class Book"]


def test_each_device_remembers_its_own_library(c):
    phone = TestClient(c.app)
    phone.post("/api/auth/login", json=OWNER)
    c.post("/api/libraries", json={"name": "Room 12"})
    assert me(c)["library"]["name"] == "Room 12"
    assert me(phone)["library"]["name"] == "Home Library"


def test_sharing_a_library_with_someone(c):
    open_signups(c)
    friend = sign_up(c)
    classroom = c.post("/api/libraries", json={"name": "Room 12"}).json()["id"]
    c.post("/api/books", json={"title": "Class Book", "wishlist": True})
    assert c.post("/api/members", json={"username": "nobody"}).status_code == 404
    assert c.post("/api/members", json={"username": "jo"}).status_code == 200  # no password: add their account
    assert c.post("/api/members", json={"username": "jo"}).status_code == 400  # already in

    assert [l["name"] for l in me(friend)["libraries"]] == ["Jo's Books", "Room 12"]
    friend.post(f"/api/libraries/{classroom}/switch")
    assert [b["title"] for b in friend.get("/api/books").json()] == ["Class Book"]
    assert friend.get("/api/members").status_code == 403  # a member here, not an owner

    # Jo has their own library too, so Room 12's owner can't change Jo's password.
    jo = next(m for m in c.get("/api/members").json() if m["username"] == "jo")
    assert jo["password_resettable"] is False
    assert c.patch(f"/api/members/{jo['id']}", json={"password": "taken over!"}).status_code == 403

    assert friend.post("/api/libraries/current/leave").status_code == 200
    assert [l["name"] for l in me(friend)["libraries"]] == ["Jo's Books"]
    assert me(friend)["library"]["name"] == "Jo's Books"  # moved back to a library they're in


def test_rename_and_delete_a_library(c):
    c.post("/api/libraries", json={"name": "Room 12"})
    c.post("/api/books", json={"title": "Class Book", "wishlist": True})
    assert c.patch("/api/libraries/current", json={"name": "Room 14"}).status_code == 200
    assert me(c)["library"]["name"] == "Room 14"
    assert c.request("DELETE", "/api/libraries/current", json={"name": "room 14"}).status_code == 400
    assert c.request("DELETE", "/api/libraries/current", json={"name": "Room 14"}).status_code == 200
    assert [l["name"] for l in me(c)["libraries"]] == ["Home Library"]
    assert db.one("SELECT COUNT(*) AS n FROM books WHERE title='Class Book'")["n"] == 0


def test_members_cannot_rename_or_delete(c):
    c.post("/api/members", json={"username": "kid", "password": "kid's password"})
    kid = TestClient(c.app)
    kid.post("/api/auth/login", json={"username": "kid", "password": "kid's password"})
    assert kid.patch("/api/libraries/current", json={"name": "Mine"}).status_code == 403
    assert kid.request("DELETE", "/api/libraries/current", json={"name": "Home Library"}).status_code == 403
    assert kid.post("/api/libraries", json={"name": "Kid's own"}).status_code == 200  # but can start their own


# The server's administrator

def test_admin_manages_accounts_on_the_server(c):
    open_signups(c)
    friend = sign_up(c)
    accounts = {a["username"]: a for a in c.get("/api/server/accounts").json()}
    assert accounts["jo"]["library_names"] == "Jo's Books" and accounts["owner"]["site_admin"] == 1
    jo = accounts["jo"]["id"]
    # Jo is the only owner of Jo's Books, so the account can't just vanish.
    assert c.delete(f"/api/server/accounts/{jo}").status_code == 400
    assert c.delete(f"/api/server/accounts/{accounts['owner']['id']}").status_code == 400  # not yourself
    friend.request("DELETE", "/api/libraries/current", json={"name": "Jo's Books"})
    assert c.delete(f"/api/server/accounts/{jo}").status_code == 200
    assert friend.get("/api/books").status_code == 401


# Upgrading

def test_upgrade_puts_everything_in_one_library(tmp_path):
    path = str(tmp_path / "old.db")
    con = sqlite3.connect(path)
    for sql in db.MIGRATIONS[:6]:
        con.executescript(sql)
    con.execute("PRAGMA user_version=6")
    from app import auth
    con.execute("INSERT INTO users(username, password, role) VALUES('aaron', ?, 'admin'), ('kid', ?, 'member')",
                (auth.hash_password("aaron's password"), auth.hash_password("kid's password")))
    con.execute("INSERT INTO settings VALUES('pin', 'salt:hash')")
    con.execute("INSERT INTO books(title, wishlist) VALUES('Old Book', 1)")
    con.commit()
    con.close()
    with TestClient(create_app(path)) as c:
        c.post("/api/auth/login", json={"username": "aaron", "password": "aaron's password"})
        them = me(c)
        assert them["site_admin"] and them["library"] == {"id": 1, "name": "Home Library", "role": "admin", "person_id": None}
        assert [b["title"] for b in c.get("/api/books").json()] == ["Old Book"]
        assert c.get("/api/pin-status").json()["set"] is True  # the PIN moved onto the library
        kid = TestClient(c.app)
        kid.post("/api/auth/login", json={"username": "kid", "password": "kid's password"})
        assert me(kid)["library"]["role"] == "member" and not me(kid)["site_admin"]
