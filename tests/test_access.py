# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Aaron Fincher. See LICENSE.
"""Identity and access management: viewers, app-made passwords that must be
changed, password resets, "forgot password" requests, and library access."""
import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import create_app
from conftest import log_in_as_owner


@pytest.fixture
def c(tmp_path):
    with TestClient(create_app(str(tmp_path / "library.db"))) as client:
        yield log_in_as_owner(client)


def new_account(c, username="friend", **extra):
    r = c.post("/api/server/accounts", json={"username": username, **extra})
    assert r.status_code == 200, r.text
    return r.json()


def log_in(c, username, password, new_password=None):
    """Logs in; if a new password is given, sets it (as the app requires after a reset)."""
    client = TestClient(c.app)
    assert client.post("/api/auth/login", json={"username": username, "password": password}).status_code == 200
    if new_password:
        assert client.post("/api/auth/password", json={"current": password, "password": new_password}).status_code == 200
    return client


def home_id(c):
    return c.get("/api/auth/status").json()["user"]["library"]["id"]


# App-made passwords

def test_new_account_gets_a_simple_password_to_change(c):
    r = new_account(c, email="friend@example.com")
    assert len(r["password"]) == 8 and r["password"].isalnum()
    assert not set(r["password"]) & set("0O1lI")  # nothing easy to misread
    friend = log_in(c, "friend", r["password"])
    me = friend.get("/api/auth/status").json()["user"]
    assert me["must_change"] is True
    assert friend.get("/api/books").status_code == 403  # nothing else works first
    assert friend.post("/api/auth/password", json={"current": r["password"], "password": r["password"]}).status_code == 400
    assert friend.post("/api/auth/password", json={"current": r["password"], "password": "my own password"}).status_code == 200
    assert friend.get("/api/auth/status").json()["user"]["must_change"] is False
    assert friend.get("/api/books").status_code == 200


def test_admin_resets_a_password(c):
    r = new_account(c)
    friend = log_in(c, "friend", r["password"], "my own password")
    reset = c.post(f"/api/server/accounts/{r['id']}/password").json()
    assert reset["username"] == "friend" and len(reset["password"]) == 8
    assert friend.get("/api/books").status_code == 401  # logged out everywhere
    again = log_in(c, "friend", reset["password"])
    assert again.get("/api/auth/status").json()["user"]["must_change"] is True
    me = c.get("/api/auth/status").json()["user"]["id"]
    assert c.post(f"/api/server/accounts/{me}/password").status_code == 400  # not your own this way


# Forgot password

def test_forgot_password_asks_the_admin(c):
    r = new_account(c, email="friend@example.com")
    log_in(c, "friend", r["password"], "my own password")
    anyone = TestClient(c.app)
    assert anyone.post("/api/auth/forgot", json={"email": "nobody@example.com"}).status_code == 404
    assert anyone.post("/api/auth/forgot", json={"email": "Friend@Example.com"}).status_code == 200
    anyone.post("/api/auth/forgot", json={"email": "friend@example.com"})  # asking twice makes one request
    [req] = c.get("/api/server/reset-requests").json()
    assert (req["username"], req["email"]) == ("friend", "friend@example.com")
    assert next(a for a in c.get("/api/server/accounts").json() if a["username"] == "friend")["wants_reset"] == 1
    # Asking doesn't change anything by itself: the old password still works.
    log_in(c, "friend", "my own password")
    c.post(f"/api/server/accounts/{r['id']}/password")
    assert c.get("/api/server/reset-requests").json() == []  # handled


def test_forgot_password_is_throttled(c):
    anyone = TestClient(c.app)
    for _ in range(5):
        assert anyone.post("/api/auth/forgot", json={"email": "guess@example.com"}).status_code == 404
    assert anyone.post("/api/auth/forgot", json={"email": "guess@example.com"}).status_code == 429


def test_dismissing_a_reset_request(c):
    new_account(c, email="friend@example.com")
    TestClient(c.app).post("/api/auth/forgot", json={"email": "friend@example.com"})
    rid = c.get("/api/server/reset-requests").json()[0]["id"]
    assert c.delete(f"/api/server/reset-requests/{rid}").status_code == 200
    assert c.get("/api/server/reset-requests").json() == []


# Viewers

def test_viewers_can_look_but_not_change(c):
    shelf = c.get("/api/shelves").json()[0]["id"]
    bid = c.post("/api/books", json={"title": "Dune", "shelf_id": shelf}).json()["id"]
    r = new_account(c, own_library=False, library_id=home_id(c), role="viewer")
    viewer = log_in(c, "friend", r["password"], "my own password")
    assert viewer.get("/api/auth/status").json()["user"]["library"]["role"] == "viewer"
    assert [b["title"] for b in viewer.get("/api/books").json()] == ["Dune"]
    assert viewer.get(f"/api/books/{bid}").status_code == 200
    for method, path, body in [("post", "/api/books", {"title": "Nope", "wishlist": True}),
                               ("patch", f"/api/books/{bid}", {"rating": 1}), ("delete", f"/api/books/{bid}", None),
                               ("post", "/api/shelves", {"name": "Mine"}), ("put", f"/api/shelves/{shelf}/sort", {"sort": "author"}),
                               ("post", f"/api/books/{bid}/lend", {"borrower": "x"}), ("post", "/api/people", {"name": "x"})]:
        resp = getattr(viewer, method)(path, **({"json": body} if body is not None else {}))
        assert resp.status_code == 403, (path, resp.status_code)
        assert resp.json()["detail"] == "You can look at this library, but not change it."
    # Their own reading is theirs to keep.
    rid = viewer.post(f"/api/books/{bid}/start").json()["id"]
    assert viewer.patch(f"/api/reads/{rid}", json={"page": 10}).status_code == 200
    # And they can start a library of their own.
    assert viewer.post("/api/libraries", json={"name": "My books"}).status_code == 200


# Giving and taking away access

def test_granting_and_revoking_library_access(c):
    r = new_account(c)
    home = home_id(c)
    assert c.put(f"/api/server/accounts/{r['id']}/libraries/{home}", json={"role": "viewer"}).status_code == 200
    acct = next(a for a in c.get("/api/server/accounts").json() if a["username"] == "friend")
    assert {(l["name"], l["role"]) for l in acct["libraries"]} == {("Home Library", "viewer"), ("friend's Library", "admin")}
    c.put(f"/api/server/accounts/{r['id']}/libraries/{home}", json={"role": "member"})  # change role
    acct = next(a for a in c.get("/api/server/accounts").json() if a["username"] == "friend")
    assert ("Home Library", "member") in {(l["name"], l["role"]) for l in acct["libraries"]}
    assert c.delete(f"/api/server/accounts/{r['id']}/libraries/{home}").status_code == 200
    # The only owner of a library can't be demoted or removed.
    own = next(l["id"] for l in acct["libraries"] if l["role"] == "admin")
    assert c.put(f"/api/server/accounts/{r['id']}/libraries/{own}", json={"role": "viewer"}).status_code == 400
    assert c.delete(f"/api/server/accounts/{r['id']}/libraries/{own}").status_code == 400
    names = [l["name"] for l in c.get("/api/server/libraries").json()]
    assert names == ["friend's Library", "Home Library"]


def test_admin_can_change_an_accounts_email(c):
    r = new_account(c, email="old@example.com")
    assert c.patch(f"/api/server/accounts/{r['id']}", json={"email": "New@Example.com"}).status_code == 200
    assert db.one("SELECT email FROM users WHERE id=?", (r["id"],))["email"] == "new@example.com"
    new_account(c, "other")
    other = next(a["id"] for a in c.get("/api/server/accounts").json() if a["username"] == "other")
    assert c.patch(f"/api/server/accounts/{other}", json={"email": "new@example.com"}).status_code == 400  # taken


def test_inviting_into_any_library(c):
    r = new_account(c)
    friends_lib = next(a for a in c.get("/api/server/accounts").json() if a["username"] == "friend")["libraries"][0]["id"]
    # The administrator can invite into any library, even one they're not in.
    out = c.post("/api/invites", json={"email": "guest@example.com", "library_id": friends_lib, "role": "viewer"}).json()
    assert out["library"] == "friend's Library"
    guest = TestClient(c.app)
    guest.post("/api/auth/signup", json={"username": "guest", "password": "guest's password", "token": out["token"]})
    assert guest.get("/api/auth/status").json()["user"]["library"]["role"] == "viewer"
    # Someone allowed to invite can only invite into libraries they own.
    db.run("UPDATE users SET can_invite=1 WHERE username='friend'")
    friend = log_in(c, "friend", r["password"], "my own password")
    assert friend.post("/api/invites", json={"email": "x@example.com", "library_id": home_id(c)}).status_code == 403
    assert friend.post("/api/invites", json={"email": "x@example.com", "library_id": friends_lib}).status_code == 200
