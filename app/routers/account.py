# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Aaron Fincher. See LICENSE.
"""Logging in and out, first-time setup, and changing your own password."""
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .. import auth, db
from ..changelog import log

router = APIRouter(prefix="/api/auth")


class Credentials(BaseModel):
    username: str = Field(max_length=100)
    password: str = Field(max_length=500)


class NewPassword(BaseModel):
    current: str = Field(max_length=500)
    password: str = Field(max_length=500)


@router.get("/status")
def status(request: Request):
    """Public: whether the owner account still needs creating, and who's logged in."""
    user = auth.user_for(request.cookies.get(auth.COOKIE))
    return {"setup_needed": not auth.has_users(), "user": auth.public(user) if user else None}


@router.post("/setup")
def setup(b: Credentials, request: Request, response: Response):
    """Creates the first account, an owner. Only works while there are no accounts."""
    username = auth.clean_username(b.username)
    auth.validate_password(b.password)
    with db.connect() as c:
        c.execute("BEGIN IMMEDIATE")  # two people racing to set up can't both win
        if c.execute("SELECT 1 FROM users").fetchone():
            raise HTTPException(409, "This library already has an owner. Log in instead.")
        uid = c.execute("INSERT INTO users(username, password, role) VALUES(?,?,'admin')",
                        (username, auth.hash_password(b.password))).lastrowid
    auth.set_cookie(response, request, auth.start_session(uid))
    return {"user": auth.public(db.one("SELECT * FROM users WHERE id=?", (uid,)))}


@router.post("/login")
def login(b: Credentials, request: Request, response: Response):
    user = auth.login(b.username, b.password)
    auth.set_cookie(response, request, auth.start_session(user["id"]))
    return {"user": auth.public(user)}


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
    log("Changed their own password", admin=True)
    return {"ok": True}
