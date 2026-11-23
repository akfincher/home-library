# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Aaron Fincher. See LICENSE.
"""Sign-up only through a personal invitation link, sent to an email address
by someone allowed to invite people."""
import time

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import create_app
from conftest import log_in_as_owner

WIFE = {"username": "wife", "password": "her own password"}
GUEST = {"username": "sarah", "password": "sarah's password"}


@pytest.fixture
def c(tmp_path):
    with TestClient(create_app(str(tmp_path / "library.db"))) as client:
        yield log_in_as_owner(client)


def as_wife(c):
    c.post("/api/members", json=WIFE)
    wife = TestClient(c.app)
    wife.post("/api/auth/login", json=WIFE)
    return wife


def invite(client, email="Sarah@Example.com", note=""):
    r = client.post("/api/invites", json={"email": email, "note": note})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def signup(c, token, who=GUEST):
    return TestClient(c.app).post("/api/auth/signup", json={**who, "token": token})


def wife_id(c):
    return next(a["id"] for a in c.get("/api/server/accounts").json() if a["username"] == "wife")


def test_no_invitation_no_account(c):
    assert signup(c, "").status_code == 403
    assert signup(c, "made-up-token").status_code == 403
    assert db.one("SELECT COUNT(*) AS n FROM users")["n"] == 1  # nothing was created


def test_inviting_by_email(c):
    token = invite(c, note="Sarah from book club")
    assert len(token) >= 20
    anyone = TestClient(c.app)
    assert anyone.get(f"/api/invites/check?token={token}").json() == {"email": "sarah@example.com", "library": None}
    assert signup(c, token).status_code == 200
    assert db.one("SELECT email FROM users WHERE username='sarah'")["email"] == "sarah@example.com"
    assert anyone.get(f"/api/invites/check?token={token}").status_code == 404  # used up
    listed = c.get("/api/invites").json()
    assert [(i["email"], i["note"], i["status"], i["used_by"]) for i in listed] == [
        ("sarah@example.com", "Sarah from book club", "used", "sarah")]
    assert token not in str(db.rows("SELECT * FROM invites"))  # only a hash is kept


def test_email_rules(c):
    assert c.post("/api/invites", json={"email": "not an email"}).status_code == 400
    signup(c, invite(c))
    assert c.post("/api/invites", json={"email": "SARAH@example.com"}).status_code == 400  # already has an account


def test_inviting_again_replaces_the_old_link(c):
    first = invite(c)
    second = invite(c)
    assert signup(c, first).status_code == 403
    assert signup(c, second).status_code == 200


def test_only_allowed_accounts_can_invite(c):
    wife = as_wife(c)
    assert wife.post("/api/invites", json={"email": "a@b.co"}).status_code == 403
    assert wife.get("/api/auth/status").json()["user"]["can_invite"] is False
    assert c.patch(f"/api/server/accounts/{wife_id(c)}", json={"can_invite": True}).status_code == 200
    assert wife.get("/api/auth/status").json()["user"]["can_invite"] is True
    token = invite(wife)
    assert wife.patch(f"/api/server/accounts/{wife_id(c)}", json={"can_invite": True}).status_code == 403  # admin only
    assert signup(c, token).status_code == 200
    assert db.one("SELECT invited_by FROM users WHERE username='sarah'")["invited_by"] == wife_id(c)
    invited = TestClient(c.app)
    invited.post("/api/auth/login", json=GUEST)
    assert invited.post("/api/invites", json={"email": "x@y.co"}).status_code == 403  # can't pass it on


def test_a_link_works_once(c):
    token = invite(c)
    assert signup(c, token).status_code == 200
    assert signup(c, token, {"username": "second", "password": "another password"}).status_code == 403


def test_bad_username_keeps_the_link_usable(c):
    token = invite(c)
    assert signup(c, token, {"username": "owner", "password": "long enough pw"}).status_code == 400  # taken
    assert signup(c, token).status_code == 200


def test_cancelled_and_expired_links_fail(c):
    cancelled = invite(c, "a@example.com")
    iid = c.get("/api/invites").json()[0]["id"]
    assert c.delete(f"/api/invites/{iid}").status_code == 200
    assert signup(c, cancelled).status_code == 403
    expired = invite(c, "b@example.com")
    db.run("UPDATE invites SET expires_at=? WHERE email='b@example.com'", (time.time() - 1,))
    assert signup(c, expired).status_code == 403
    assert [i["status"] for i in c.get("/api/invites").json()] == ["expired", "cancelled"]


def test_people_only_see_and_cancel_their_own_invites(c):
    wife = as_wife(c)
    c.patch(f"/api/server/accounts/{wife_id(c)}", json={"can_invite": True})
    mine = invite(c, "mine@example.com")
    invite(wife, "hers@example.com")
    assert [i["email"] for i in wife.get("/api/invites").json()] == ["hers@example.com"]
    assert {i["email"] for i in c.get("/api/invites").json()} == {"mine@example.com", "hers@example.com"}
    my_id = next(i["id"] for i in c.get("/api/invites").json() if i["email"] == "mine@example.com")
    assert wife.delete(f"/api/invites/{my_id}").status_code == 404
    assert signup(c, mine).status_code == 200


def test_guessing_links_gets_throttled(c):
    good = invite(c)
    for _ in range(5):
        assert signup(c, "guess-guess-guess").status_code == 403
    assert signup(c, good).status_code == 429  # even a real link waits now


def test_deleting_an_inviter_cancels_their_unused_invites(c):
    wife = as_wife(c)
    c.patch(f"/api/server/accounts/{wife_id(c)}", json={"can_invite": True})
    token = invite(wife)
    c.delete(f"/api/members/{wife_id(c)}")
    assert c.delete(f"/api/server/accounts/{wife_id(c)}").status_code == 200
    assert signup(c, token).status_code == 403
