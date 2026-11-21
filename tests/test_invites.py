# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Aaron Fincher. See LICENSE.
"""Sign-up only with an invite code from someone allowed to invite people."""
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


def signup(c, code, who=GUEST):
    return TestClient(c.app).post("/api/auth/signup", json={**who, "code": code})


def wife_id(c):
    return next(a["id"] for a in c.get("/api/server/accounts").json() if a["username"] == "wife")


def test_no_code_no_account(c):
    assert signup(c, "").status_code == 403
    assert signup(c, "ABCD-EFGH-JKMN").status_code == 403
    assert db.one("SELECT COUNT(*) AS n FROM users")["n"] == 1  # nothing was created


def test_only_allowed_accounts_can_invite(c):
    wife = as_wife(c)
    assert wife.post("/api/invites", json={}).status_code == 403
    assert wife.get("/api/auth/status").json()["user"]["can_invite"] is False
    assert c.patch(f"/api/server/accounts/{wife_id(c)}", json={"can_invite": True}).status_code == 200
    assert wife.get("/api/auth/status").json()["user"]["can_invite"] is True
    code = wife.post("/api/invites", json={"note": "Sarah from book club"}).json()["code"]
    assert len(code) == 14 and code.count("-") == 2
    assert wife.patch(f"/api/server/accounts/{wife_id(c)}", json={"can_invite": True}).status_code == 403  # admin only

    r = signup(c, code.lower().replace("-", " "))  # forgiving about case and spacing
    assert r.status_code == 200
    accounts = {a["username"]: a for a in c.get("/api/server/accounts").json()}
    assert db.one("SELECT invited_by FROM users WHERE username='sarah'")["invited_by"] == accounts["wife"]["id"]

    listed = wife.get("/api/invites").json()
    assert [(i["note"], i["status"], i["used_by"], i["hint"]) for i in listed] == [
        ("Sarah from book club", "used", "sarah", code[-4:])]
    assert code not in str(db.rows("SELECT * FROM invites"))  # only a hash is kept


def test_a_code_works_once(c):
    code = c.post("/api/invites", json={}).json()["code"]
    assert signup(c, code).status_code == 200
    assert signup(c, code, {"username": "second", "password": "another password"}).status_code == 403


def test_bad_username_keeps_the_code_usable(c):
    code = c.post("/api/invites", json={}).json()["code"]
    assert signup(c, code, {"username": "owner", "password": "long enough pw"}).status_code == 400  # taken
    assert signup(c, code).status_code == 200


def test_cancelled_and_expired_codes_fail(c):
    cancelled = c.post("/api/invites", json={}).json()["code"]
    iid = c.get("/api/invites").json()[0]["id"]
    assert c.delete(f"/api/invites/{iid}").status_code == 200
    assert signup(c, cancelled).status_code == 403
    expired = c.post("/api/invites", json={}).json()["code"]
    db.run("UPDATE invites SET expires_at=? WHERE hint=?", (time.time() - 1, expired[-4:]))
    assert signup(c, expired).status_code == 403
    assert [i["status"] for i in c.get("/api/invites").json()] == ["expired", "cancelled"]


def test_people_only_see_and_cancel_their_own_codes(c):
    wife = as_wife(c)
    c.patch(f"/api/server/accounts/{wife_id(c)}", json={"can_invite": True})
    mine = c.post("/api/invites", json={"note": "mine"}).json()
    wife.post("/api/invites", json={"note": "hers"})
    assert [i["note"] for i in wife.get("/api/invites").json()] == ["hers"]
    assert {i["note"] for i in c.get("/api/invites").json()} == {"mine", "hers"}  # the admin sees all
    my_id = next(i["id"] for i in c.get("/api/invites").json() if i["note"] == "mine")
    assert wife.delete(f"/api/invites/{my_id}").status_code == 404
    assert signup(c, mine["code"]).status_code == 200


def test_guessing_codes_gets_throttled(c):
    good = c.post("/api/invites", json={}).json()["code"]
    for _ in range(5):
        assert signup(c, "WRNG-CODE-XXXX").status_code == 403
    assert signup(c, good).status_code == 429  # even a real code waits now


def test_deleting_an_inviter_cancels_their_unused_codes(c):
    wife = as_wife(c)
    c.patch(f"/api/server/accounts/{wife_id(c)}", json={"can_invite": True})
    code = wife.post("/api/invites", json={}).json()["code"]
    c.delete(f"/api/members/{wife_id(c)}")
    assert c.delete(f"/api/server/accounts/{wife_id(c)}").status_code == 200
    assert signup(c, code).status_code == 403
