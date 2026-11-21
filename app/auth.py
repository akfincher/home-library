# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Aaron Fincher. See LICENSE.
"""User accounts: password hashing, login sessions and wrong-password throttling.

Passwords are hashed with scrypt. A session is a random token kept in an
HttpOnly cookie; the database only stores a hash of it, so a copied database
can't be used to log in.
"""
import hashlib
import hmac
import secrets
import time

from fastapi import HTTPException, Request

from . import context, db
from .security import Limiter

COOKIE = "hl_session"
SESSION_DAYS = 30
MIN_PASSWORD = 8
ROLES = ("admin", "member")
_SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1}

# One limiter per username tried: 5 free wrong passwords, then a doubling wait.
_limiters: dict[str, Limiter] = {}


def reset() -> None:
    _limiters.clear()


def limiter(username: str) -> Limiter:
    return _limiters.setdefault(username.strip().lower(), Limiter("passwords"))


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(password.encode(), salt=salt, dklen=32, **_SCRYPT)
    return f"scrypt${_SCRYPT['n']}${_SCRYPT['r']}${_SCRYPT['p']}${salt.hex()}${h.hex()}"


def check_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, h = stored.split("$")
        got = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), dklen=32, n=int(n), r=int(r), p=int(p))
        return hmac.compare_digest(got.hex(), h)
    except (ValueError, TypeError):
        return False


# Checked against when the username doesn't exist, so a wrong username takes as
# long as a wrong password and doesn't reveal which accounts exist.
_DUMMY = hash_password(secrets.token_hex(8))


def validate_password(password: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise HTTPException(400, f"Use a password of at least {MIN_PASSWORD} characters.")
    if len(password) > 200:
        raise HTTPException(400, "That password is too long.")


def clean_username(name: str) -> str:
    name = (name or "").strip()
    if not name or len(name) > 50 or any(c.isspace() for c in name):
        raise HTTPException(400, "Use a username of up to 50 characters, with no spaces.")
    return name


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def has_users() -> bool:
    return bool(db.one("SELECT 1 FROM users LIMIT 1"))


def libraries_of(user_id: int) -> list[dict]:
    return db.rows("SELECT l.id, l.name, m.role, m.person_id FROM memberships m JOIN libraries l ON l.id=m.library_id "
                   "WHERE m.user_id=? ORDER BY l.name COLLATE NOCASE, l.id", (user_id,))


def public(user: dict) -> dict:
    """What the app is told about the logged-in user."""
    return {"id": user["id"], "username": user["username"], "site_admin": bool(user["site_admin"]),
            "can_invite": bool(user["site_admin"] or user["can_invite"]),
            "library": user.get("library"), "libraries": user.get("libraries", [])}


def new_library(name: str, owner_id: int) -> int:
    """A new, empty library with a Main shelf, owned by the given account."""
    lib_id = db.run("INSERT INTO libraries(name) VALUES(?)", (name,)).lastrowid
    db.run("INSERT INTO shelves(name, library_id) VALUES('Main', ?)", (lib_id,))
    db.run("INSERT INTO memberships(library_id, user_id, role) VALUES(?,?,'admin')", (lib_id, owner_id))
    return lib_id


def create_first_owner(username: str, password: str) -> int:
    """The server's first account: it owns library 1 (made when the database was
    created) and runs the server."""
    with db.connect() as c:
        c.execute("BEGIN IMMEDIATE")  # two people racing to set up can't both win
        if c.execute("SELECT 1 FROM users").fetchone():
            raise HTTPException(409, "This library already has an owner. Log in instead.")
        uid = c.execute("INSERT INTO users(username, password, role, site_admin) VALUES(?,?,'admin',1)",
                        (username, hash_password(password))).lastrowid
        if not c.execute("SELECT 1 FROM libraries WHERE id=1").fetchone():
            c.execute("INSERT INTO libraries(id, name) VALUES(1, 'Home Library')")
        c.execute("INSERT INTO memberships(library_id, user_id, role) VALUES(1,?,'admin')", (uid,))
    return uid


def login(username: str, password: str) -> dict:
    """The user, if the password is right. Raises 429 while throttled, 401 if wrong."""
    lim = limiter(username)
    lim.check()
    user = db.one("SELECT * FROM users WHERE username=?", (username.strip(),))
    ok = check_password(password, user["password"] if user else _DUMMY)
    if not (user and ok):
        lim.fail()
        raise HTTPException(401, "Wrong username or password.")
    lim.success()
    return user


def start_session(user_id: int, library_id: int | None = None) -> str:
    token = secrets.token_urlsafe(32)
    db.run("DELETE FROM sessions WHERE expires < ?", (time.time(),))
    db.run("INSERT INTO sessions(token_hash, user_id, expires, library_id) VALUES(?,?,?,?)",
           (_token_hash(token), user_id, time.time() + SESSION_DAYS * 86400, library_id))
    return token


def switch_library(token: str, library_id: int) -> None:
    db.run("UPDATE sessions SET library_id=? WHERE token_hash=?", (library_id, _token_hash(token)))


def end_session(token: str | None) -> None:
    if token:
        db.run("DELETE FROM sessions WHERE token_hash=?", (_token_hash(token),))


def end_all_sessions(user_id: int, except_token: str | None = None) -> None:
    keep = _token_hash(except_token) if except_token else ""
    db.run("DELETE FROM sessions WHERE user_id=? AND token_hash!=?", (user_id, keep))


def user_for(token: str | None) -> dict | None:
    """The logged-in user for a session token, extending the session as it's used."""
    if not token:
        return None
    th = _token_hash(token)
    row = db.one("SELECT u.*, s.expires, s.library_id AS session_library FROM sessions s "
                 "JOIN users u ON u.id=s.user_id WHERE s.token_hash=?", (th,))
    if not row or row["expires"] < time.time():
        return None
    # Sliding expiry: anyone using the app at least once a month stays logged in.
    if row["expires"] - time.time() < (SESSION_DAYS - 1) * 86400:
        db.run("UPDATE sessions SET expires=? WHERE token_hash=?", (time.time() + SESSION_DAYS * 86400, th))
    # The library this device is working in: the one last chosen, if still a member, else the first.
    libs = libraries_of(row["id"])
    current = next((l for l in libs if l["id"] == row["session_library"]), libs[0] if libs else None)
    if current and current["id"] != row["session_library"]:
        db.run("UPDATE sessions SET library_id=? WHERE token_hash=?", (current["id"], th))
    return {**row, "library": current, "libraries": libs}


def create_owner_from_env() -> str | None:
    """On a library with no accounts yet, creates the owner account from the
    OWNER_USERNAME and OWNER_PASSWORD settings in the deployment, so the app is
    never left open for a stranger to claim. Returns a problem to report, if any."""
    import os
    name, password = os.environ.get("OWNER_USERNAME", "").strip(), os.environ.get("OWNER_PASSWORD", "")
    if not (name or password) or has_users():
        return None
    try:
        name = clean_username(name)
        validate_password(password)
    except HTTPException as e:
        return f"OWNER_USERNAME / OWNER_PASSWORD not used: {e.detail}"
    create_first_owner(name, password)
    return None


def set_cookie(response, request: Request, token: str) -> None:
    response.set_cookie(COOKIE, token, max_age=SESSION_DAYS * 86400, httponly=True, samesite="lax",
                        secure=request.url.scheme == "https", path="/")


def current_user(request: Request) -> dict:
    """Dependency: the logged-in user (the middleware has already checked there is one)."""
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(401, "Please log in.")
    return user


def require_owner(request: Request) -> dict:
    """Dependency for things only an owner of the current library may do."""
    user = current_user(request)
    if context.library()["role"] != "admin":
        raise HTTPException(403, "Only an owner of this library can do that.")
    return user


def require_site_admin(request: Request) -> dict:
    """Dependency for server-wide settings: sign-ups, API keys, whole-database backups."""
    user = current_user(request)
    if not user["site_admin"]:
        raise HTTPException(403, "Only the server's administrator can do that.")
    return user
