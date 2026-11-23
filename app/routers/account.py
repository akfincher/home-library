# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Aaron Fincher. See LICENSE.
"""Logging in and out, first-time setup, signing up, and changing your own password."""
import sqlite3
import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .. import auth, db
from . import invites

router = APIRouter(prefix="/api/auth")


class Credentials(BaseModel):
    username: str = Field(max_length=100)
    password: str = Field(max_length=500)


class Theme(BaseModel):
    theme: str = Field(max_length=30)


@router.put("/theme")
def set_theme(b: Theme, user: dict = Depends(auth.current_user)):
    """Your theme, kept with your account so it follows you to every device."""
    if b.theme not in auth.THEMES:
        raise HTTPException(400, "There's no theme with that name.")
    db.run("UPDATE users SET theme=? WHERE id=?", (b.theme, user["id"]))
    return {"ok": True}


class NewPassword(BaseModel):
    current: str = Field(max_length=500)
    password: str = Field(max_length=500)


def _me(token: str) -> dict:
    return auth.public(auth.user_for(token))


@router.get("/status")
def status(request: Request):
    """Public: whether the first account still needs creating, whether sign-up
    (with an invite code) is possible, and who's logged in (with their libraries)."""
    from .server import current_notice
    user = auth.user_for(request.cookies.get(auth.COOKIE))
    return {"setup_needed": not auth.has_users(), "signup_open": auth.has_users(),
            "notice": current_notice(), "user": auth.public(user) if user else None}


@router.post("/setup")
def setup(b: Credentials, request: Request, response: Response):
    """Creates the first account. It owns the first library and runs the server.
    Only works while there are no accounts."""
    username = auth.clean_username(b.username)
    auth.validate_password(b.password)
    uid = auth.create_first_owner(username, b.password)
    token = auth.start_session(uid, 1)
    auth.set_cookie(response, request, token)
    return {"user": _me(token)}


class SignUp(Credentials):
    token: str = Field("", max_length=100)  # the secret from the personal sign-up link
    code: str = Field("", max_length=40)  # an older typed invite code
    library: str = Field("", max_length=100)


def reset() -> None:
    invites.guesses.reset()
    forgot_tries.reset()


@router.post("/signup")
def signup(b: SignUp, request: Request, response: Response):
    """Creates an account with its own library. Needs the secret from a personal
    invitation link, sent by someone the server's administrator allows to invite people."""
    if not auth.has_users():
        raise HTTPException(409, "Create the first account instead.")
    secret = (b.token or b.code).strip()
    if not secret:
        raise HTTPException(403, "You need an invitation to create an account.")
    username = auth.clean_username(b.username)
    auth.validate_password(b.password)
    try:
        with db.connect() as c:
            uid = c.execute("INSERT INTO users(username, password, role) VALUES(?,?,'member')",
                            (username, auth.hash_password(b.password))).lastrowid
            # In the same transaction: a wrong code means no account, and a code can't be used twice.
            invite = invites.redeem(c, secret, uid)
            # Invited to join a library (say, a shared classroom): they start out in it.
            joined = invite["library_id"] and c.execute("SELECT 1 FROM libraries WHERE id=?",
                                                        (invite["library_id"],)).fetchone()
            if joined:
                c.execute("INSERT INTO memberships(library_id, user_id, role) VALUES(?,?,?)",
                          (invite["library_id"], uid, invite["role"] or "member"))
    except sqlite3.IntegrityError:
        raise HTTPException(400, "That username is already taken.")
    lib_id = invite["library_id"] if joined else auth.new_library(b.library.strip() or f"{username}'s Library", uid)
    token = auth.start_session(uid, lib_id)
    auth.set_cookie(response, request, token)
    return {"user": _me(token)}


@router.post("/login")
def login(b: Credentials, request: Request, response: Response):
    user = auth.login(b.username, b.password)
    token = auth.start_session(user["id"])
    auth.set_cookie(response, request, token)
    return {"user": _me(token)}


@router.post("/logout")
def logout(request: Request, response: Response):
    auth.end_session(request.cookies.get(auth.COOKIE))
    response.delete_cookie(auth.COOKIE, path="/")
    return {"ok": True}


@router.post("/password")
def change_password(b: NewPassword, request: Request, user: dict = Depends(auth.current_user)):
    """Changes your own password and logs out your other devices."""
    lim = auth.limiter(user["username"])
    lim.check()
    if not auth.check_password(b.current, user["password"]):
        lim.fail()
        raise HTTPException(403, "Your current password is wrong.")
    lim.success()
    auth.validate_password(b.password)
    if b.password == b.current:
        raise HTTPException(400, "Choose a password different from the one you have now.")
    db.run("UPDATE users SET password=?, must_change=0 WHERE id=?", (auth.hash_password(b.password), user["id"]))
    auth.end_all_sessions(user["id"], except_token=request.cookies.get(auth.COOKIE))
    return {"ok": True}


class Forgot(BaseModel):
    email: str = Field(max_length=200)


# Counted across everyone: a few free tries, then a doubling wait, so the form
# can't be used to test lots of addresses.
forgot_tries = auth.Limiter("tries")


@router.post("/forgot")
def forgot(b: Forgot):
    """Public. Checks there's an account with that email address and asks the
    server's administrator to reset its password. It doesn't reset anything
    itself: an email address isn't a secret, so knowing one mustn't be enough
    to take over someone's account."""
    forgot_tries.check()
    user = db.one("SELECT id FROM users WHERE email=? AND email!=''", (b.email.strip().lower(),))
    if not user:
        forgot_tries.fail()
        raise HTTPException(404, "No account uses that email address. Check it, or ask the person who runs this library.")
    forgot_tries.success()
    if not db.one("SELECT 1 FROM reset_requests WHERE user_id=?", (user["id"],)):
        db.run("INSERT INTO reset_requests(user_id, requested_at) VALUES(?,?)", (user["id"], time.time()))
    return {"ok": True}
