# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Aaron Fincher. See LICENSE.
"""Inviting someone into a particular library, tidying up old invitations,
the administrator creating accounts directly, and undoing a mistaken "I've read it"."""
import time

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import create_app
from conftest import OWNER, log_in_as_owner

TEACHER = {"username": "mr_lee", "password": "teaches year four"}


@pytest.fixture
def c(tmp_path):
    with TestClient(create_app(str(tmp_path / "library.db"))) as client:
        yield log_in_as_owner(client)


def me(client):
    return client.get("/api/auth/status").json()["user"]


def classroom(c):
    c.post("/api/libraries", json={"name": "Room 12"})
    c.post("/api/books", json={"title": "Charlotte's Web", "shelf_id": c.get("/api/shelves").json()[0]["id"]})


# Two teachers, one classroom library

def test_inviting_a_new_teacher_into_the_classroom(c):
    classroom(c)
    r = c.post("/api/invites", json={"email": "lee@school.example", "to_library": True}).json()
    assert r["library"] == "Room 12"
    assert TestClient(c.app).get(f"/api/invites/check?token={r['token']}").json() == {
        "email": "lee@school.example", "library": "Room 12"}
    lee = TestClient(c.app)
    assert lee.post("/api/auth/signup", json={**TEACHER, "token": r["token"]}).status_code == 200
    them = me(lee)
    assert them["library"]["name"] == "Room 12" and them["library"]["role"] == "member"
    assert [l["name"] for l in them["libraries"]] == ["Room 12"]  # no library of their own was made
    assert [b["title"] for b in lee.get("/api/books").json()] == ["Charlotte's Web"]
    lee.post("/api/books", json={"title": "Matilda", "shelf_id": lee.get("/api/shelves").json()[0]["id"]})
    assert {b["title"] for b in c.get("/api/books").json()} == {"Charlotte's Web", "Matilda"}  # both see both


def test_inviting_a_teacher_as_co_owner(c):
    classroom(c)
    token = c.post("/api/invites", json={"email": "lee@school.example", "to_library": True, "role": "admin"}).json()["token"]
    lee = TestClient(c.app)
    lee.post("/api/auth/signup", json={**TEACHER, "token": token})
    assert me(lee)["library"]["role"] == "admin"


def test_inviting_someone_who_already_has_an_account_just_adds_them(c):
    c.post("/api/server/accounts", json={**TEACHER, "email": "lee@school.example"})
    classroom(c)
    r = c.post("/api/invites", json={"email": "LEE@school.example", "to_library": True}).json()
    assert r == {"added": "mr_lee"}
    assert "mr_lee" in [m["username"] for m in c.get("/api/members").json()]
    assert c.post("/api/invites", json={"email": "lee@school.example", "to_library": True}).status_code == 400  # already in


def test_only_owners_invite_into_a_library(c):
    classroom(c)
    c.post("/api/members", json={"username": "helper", "password": "helps out a lot", "role": "member"})
    db.run("UPDATE users SET can_invite=1 WHERE username='helper'")
    helper = TestClient(c.app)
    helper.post("/api/auth/login", json={"username": "helper", "password": "helps out a lot"})
    assert helper.post("/api/invites", json={"email": "x@y.co", "to_library": True}).status_code == 403
    assert helper.post("/api/invites", json={"email": "x@y.co"}).status_code == 200  # their own invites are fine


# Tidying up invitations

def test_removing_old_invitations(c):
    a = c.post("/api/invites", json={"email": "a@example.com"}).json()
    c.post("/api/invites", json={"email": "b@example.com"})
    c.post("/api/invites", json={"email": "c@example.com"})
    ids = {i["email"]: i["id"] for i in c.get("/api/invites").json()}
    c.delete(f"/api/invites/{ids['a@example.com']}")  # cancelled
    db.run("UPDATE invites SET expires_at=? WHERE email='b@example.com'", (time.time() - 1,))  # expired
    assert c.post("/api/invites/clear").json() == {"removed": 2}
    assert [i["email"] for i in c.get("/api/invites").json()] == ["c@example.com"]
    assert c.delete(f"/api/invites/{ids['c@example.com']}?remove=true").status_code == 200
    assert c.get("/api/invites").json() == []
    assert TestClient(c.app).post("/api/auth/signup", json={**TEACHER, "token": a["token"]}).status_code == 403


# The administrator adding accounts directly

def test_admin_creates_an_account_with_its_own_library(c):
    r = c.post("/api/server/accounts", json={**TEACHER, "email": "Lee@School.example", "can_invite": True})
    assert r.status_code == 200
    lee = TestClient(c.app)
    lee.post("/api/auth/login", json=TEACHER)
    them = me(lee)
    assert them["library"]["name"] == "mr_lee's Library" and them["library"]["role"] == "admin" and them["can_invite"]
    assert db.one("SELECT email FROM users WHERE username='mr_lee'")["email"] == "lee@school.example"


def test_admin_adds_an_account_straight_into_this_library(c):
    classroom(c)
    c.post("/api/server/accounts", json={**TEACHER, "own_library": False, "join_current": True})
    lee = TestClient(c.app)
    lee.post("/api/auth/login", json=TEACHER)
    assert [l["name"] for l in me(lee)["libraries"]] == ["Room 12"]


def test_add_account_rules(c):
    assert c.post("/api/server/accounts", json={**TEACHER, "own_library": False}).status_code == 400  # no library
    assert c.post("/api/server/accounts", json={**TEACHER, "email": "nope"}).status_code == 400
    assert c.post("/api/server/accounts", json={"username": "owner", "password": "long enough pw"}).status_code == 400
    c.post("/api/members", json={"username": "kid", "password": "kid's password"})
    kid = TestClient(c.app)
    kid.post("/api/auth/login", json={"username": "kid", "password": "kid's password"})
    assert kid.post("/api/server/accounts", json=TEACHER).status_code == 403


# Undoing "I've read it"

def test_undoing_a_mistaken_ive_read_it(c):
    bid = c.post("/api/books", json={"title": "Dune", "wishlist": True}).json()["id"]
    c.post(f"/api/books/{bid}/finished", json={})
    r = c.get(f"/api/books/{bid}").json()
    assert r["times_finished"] == 1
    assert c.delete(f"/api/reads/{r['reading']['id']}").status_code == 200
    r = c.get(f"/api/books/{bid}").json()
    assert (r["reading"], r["times_finished"]) == (None, 0)
