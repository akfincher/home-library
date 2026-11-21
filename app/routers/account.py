# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Aaron Fincher. See LICENSE.
"""Logging in and out, first-time setup, signing up, and changing your own password."""
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .. import auth, db
from . import invites

router = APIRouter(prefix="/api/auth")


class Credentials(BaseModel):
    username: str = Field(max_length=100)
    password: str = Field(max_length=500)


class NewPassword(BaseModel):
    current: str = Field(max_length=500)
    password: str = Field(max_length=500)


def _me(token: str) -> dict:
    return auth.public(auth.user_for(token))


@router.get("/status")
def status(request: Request):
    """Public: whether the first account still needs creating, whether sign-up
    (with an invite code) is possible, and who's logged in (with their libraries)."""
    user = auth.user_for(request.cookies.get(auth.COOKIE))
    return {"setup_needed": not auth.has_users(), "signup_open": auth.has_users(),
            "user": auth.public(user) if user else None}


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
            invites.redeem(c, secret, uid)
    except sqlite3.IntegrityError:
        raise HTTPException(400, "That username is already taken.")
    lib_id = auth.new_library(b.library.strip() or f"{username}'s Library", uid)
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
    db.run("UPDATE users SET password=? WHERE id=?", (auth.hash_password(b.password), user["id"]))
    auth.end_all_sessions(user["id"], except_token=request.cookies.get(auth.COOKIE))
    return {"ok": True}
