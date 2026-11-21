# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Aaron Fincher. See LICENSE.
import pytest
from fastapi.testclient import TestClient

from app import config, db, reset_password
from app.main import create_app
from conftest import OWNER, log_in_as_owner

MEMBER = {"username": "mia", "password": "purple unicorn 7"}


@pytest.fixture
def app_path(tmp_path):
    return str(tmp_path / "library.db")


@pytest.fixture
def c(app_path):
    with TestClient(create_app(app_path)) as client:
        yield client


def another(c):
    """A second browser/phone against the same app, with its own cookies."""
    return TestClient(c.app)


def add_member(owner):
    assert owner.post("/api/members", json=MEMBER).status_code == 200
    member = another(owner)
    assert member.post("/api/auth/login", json=MEMBER).status_code == 200
    return member


# First-time setup

def test_everything_needs_setup_then_login(c):
    assert c.get("/api/auth/status").json() == {"setup_needed": True, "signup_open": False, "notice": None, "user": None}
    assert c.get("/api/books").status_code == 401
    assert c.get("/").status_code == 200 and c.get("/static/manifest.json").status_code == 200
    assert c.get("/api/about").status_code == 200

    r = c.post("/api/auth/setup", json=OWNER)
    me = r.json()["user"]
    assert me["site_admin"] and me["library"]["role"] == "admin" and me["library"]["name"] == "Home Library"
    assert c.get("/api/books").status_code == 200  # setup logs you in
    assert c.post("/api/auth/setup", json={"username": "intruder", "password": "x" * 12}).status_code == 409


def test_setup_checks_username_and_password(c):
    assert c.post("/api/auth/setup", json={"username": "me", "password": "short"}).status_code == 400
    assert c.post("/api/auth/setup", json={"username": "two words", "password": "long enough!"}).status_code == 400
    assert c.get("/api/auth/status").json()["setup_needed"] is True


# Logging in and out

def test_login_and_logout(c):
    log_in_as_owner(c)
    phone = another(c)
    assert phone.get("/api/books").status_code == 401
    r = phone.post("/api/auth/login", json={"username": "OWNER", "password": OWNER["password"]})  # any case
    assert r.status_code == 200
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie and "max-age=2592000" in cookie
    assert phone.get("/api/books").status_code == 200
    phone.post("/api/auth/logout")
    assert phone.get("/api/books").status_code == 401
    assert c.get("/api/books").status_code == 200  # other devices stay logged in


def test_wrong_logins_look_the_same_and_get_throttled(c):
    log_in_as_owner(c)
    x = another(c)
    wrong_pw = x.post("/api/auth/login", json={"username": "owner", "password": "nope nope"})
    no_user = x.post("/api/auth/login", json={"username": "ghost", "password": "nope nope"})
    assert wrong_pw.status_code == no_user.status_code == 401
    assert wrong_pw.json() == no_user.json() == {"detail": "Wrong username or password."}
    for _ in range(4):
        x.post("/api/auth/login", json={"username": "owner", "password": "nope nope"})
    r = x.post("/api/auth/login", json=OWNER)  # even the right password waits now
    assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0


def test_only_a_hash_of_the_session_is_stored(c):
    log_in_as_owner(c)
    token = c.cookies.get("hl_session")
    stored = [r["token_hash"] for r in db.rows("SELECT token_hash FROM sessions")]
    assert token and token not in stored and len(stored) == 1
    assert "correct horse" not in db.one("SELECT password FROM users")["password"]


def test_requests_from_other_sites_are_refused(c):
    log_in_as_owner(c)
    evil = c.post("/api/shelves", json={"name": "x"}, headers={"Origin": "https://evil.example"})
    assert evil.status_code == 403
    same = c.post("/api/shelves", json={"name": "x"}, headers={"Origin": "http://testserver"})
    assert same.status_code == 200


# Owners and members

def test_members_use_the_library_but_not_owner_settings(c):
    log_in_as_owner(c)
    member = add_member(c)
    assert member.post("/api/shelves", json={"name": "Mine"}).status_code == 200
    assert member.post("/api/books", json={"title": "Dune", "wishlist": True}).status_code == 200
    for method, path, body in [("get", "/api/members", None), ("get", "/api/export.json", None),
                               ("get", "/api/server/accounts", None), ("get", "/api/backup.db", None),
                               ("put", "/api/sources/google", {"key": "x"}), ("post", "/api/pin", {"pin": "1234"}),
                               ("post", "/api/organize/apply", {"rule": "genre"}),
                               ("post", "/api/shelves/delete-empty", None), ("post", "/api/import", {})]:
        r = getattr(member, method)(path, **({"json": body} if body is not None else {}))
        assert r.status_code == 403, (path, r.status_code)
    assert member.post("/api/organize/preview", json={"rule": "genre"}).status_code == 200


def test_owner_manages_members(c):
    log_in_as_owner(c)
    pid = c.post("/api/people", json={"name": "Mia"}).json()["id"]
    assert c.post("/api/members", json={**MEMBER, "person_id": pid}).status_code == 200
    assert c.post("/api/members", json={**MEMBER, "username": "MIA"}).status_code == 400  # taken, any case
    assert c.post("/api/members", json={"username": "kid", "password": "short"}).status_code == 400
    members = c.get("/api/members").json()
    assert [(m["username"], m["role"], m["person"]) for m in members] == [("mia", "member", "Mia"), ("owner", "admin", None)]
    assert "password" not in members[0] and members[0]["password_resettable"]

    member = another(c)
    member.post("/api/auth/login", json=MEMBER)
    assert member.get("/api/auth/status").json()["user"]["library"]["person_id"] == pid
    uid = members[0]["id"]
    c.patch(f"/api/members/{uid}", json={"password": "a brand new one"})
    assert member.get("/api/books").status_code == 401  # a reset logs them out
    assert another(c).post("/api/auth/login", json={"username": "mia", "password": "a brand new one"}).status_code == 200

    c.delete(f"/api/people/{pid}")
    assert c.get("/api/members").json()[0]["person_id"] is None
    assert c.delete(f"/api/members/{uid}").status_code == 200
    # Removed from the library, but the account still exists and can log in to an empty account.
    gone = another(c)
    gone.post("/api/auth/login", json={"username": "mia", "password": "a brand new one"})
    assert gone.get("/api/auth/status").json()["user"]["library"] is None
    assert gone.get("/api/books").status_code == 409


def test_there_is_always_an_owner(c):
    log_in_as_owner(c)
    me = c.get("/api/auth/status").json()["user"]["id"]
    assert c.patch(f"/api/members/{me}", json={"role": "member"}).status_code == 400
    assert c.delete(f"/api/members/{me}").status_code == 400
    assert c.post("/api/libraries/current/leave").status_code == 400
    c.post("/api/members", json={**MEMBER, "role": "admin"})
    assert c.patch(f"/api/members/{me}", json={"role": "member"}).status_code == 200


def test_change_own_password(c):
    log_in_as_owner(c)
    phone = another(c)
    phone.post("/api/auth/login", json=OWNER)
    assert c.post("/api/auth/password", json={"current": "wrong one", "password": "new password 1"}).status_code == 403
    assert c.post("/api/auth/password", json={"current": OWNER["password"], "password": "new"}).status_code == 400
    assert c.post("/api/auth/password", json={"current": OWNER["password"], "password": "new password 1"}).status_code == 200
    assert c.get("/api/books").status_code == 200  # this device stays in
    assert phone.get("/api/books").status_code == 401  # the others are logged out


def test_restore_keeps_accounts(c):
    log_in_as_owner(c)
    data = c.get("/api/export.json").json()
    assert "users" not in data and "sessions" not in data
    c.post("/api/import", json=data)
    assert c.get("/api/books").status_code == 200


# Locked out

def test_reset_password_command(c, app_path, monkeypatch, capsys):
    log_in_as_owner(c)
    monkeypatch.setattr(config, "DB_PATH", app_path)
    assert reset_password.main(["reset_password"]) == 0
    assert "owner  (owner)" in capsys.readouterr().out
    assert reset_password.main(["reset_password", "nobody"]) == 1
    capsys.readouterr()
    assert reset_password.main(["reset_password", "owner"]) == 0
    new = capsys.readouterr().out.split("New password for owner: ")[1].split()[0]
    assert c.get("/api/books").status_code == 401  # old sessions ended
    assert another(c).post("/api/auth/login", json={"username": "owner", "password": new}).status_code == 200
