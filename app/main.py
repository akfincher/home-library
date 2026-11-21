# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Aaron Fincher. See LICENSE.
import os
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from fastapi import FastAPI, Header, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import auth, changelog, config, context, db, security, storage
from .routers import (account, backup, books, invites, libraries, loans, lookup, members, organize, people, pin,
                      server, shelves)

STATIC = os.path.join(os.path.dirname(__file__), "static")
# API paths anyone may use without logging in.
PUBLIC_API = {"/api/auth/status", "/api/auth/login", "/api/auth/setup", "/api/auth/signup", "/api/about",
              "/api/invites/check"}
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


def create_app(db_path: str | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app):
        db.init(db_path or config.DB_PATH)
        security.reset()
        auth.reset()
        account.reset()
        problem = auth.create_owner_from_env()
        if problem:
            print(f"Home Library: {problem}", flush=True)
        yield

    app = FastAPI(title="Home Library", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def require_login(request: Request, call_next):
        # Refuse changes sent from another website (cross-site request forgery).
        origin = request.headers.get("origin")
        if request.method in UNSAFE and origin:
            hosts = {request.headers.get("host"), request.headers.get("x-forwarded-host")}
            if urlsplit(origin).netloc not in hosts:
                return JSONResponse({"detail": "Request from another site refused."}, 403)
        path = request.url.path
        if path.startswith("/api/") and path not in PUBLIC_API:
            user = await run_in_threadpool(auth.user_for, request.cookies.get(auth.COOKIE))
            if not user:
                return JSONResponse({"detail": "Please log in."}, 401)
            request.state.user = user
            context.USER.set(user)
            context.LIBRARY.set(user["library"])
        return await call_next(request)

    for r in (account, invites, members, libraries, server, pin, people, shelves, books, loans, lookup, backup,
              organize):
        app.include_router(r.router)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(os.path.join(STATIC, "index.html"), headers={"Cache-Control": "no-cache"})

    @app.get("/api/about")
    def about():
        return {"license": "AGPL-3.0-or-later", "source": config.SOURCE_URL}

    @app.get("/api/changes")
    def changes(x_unlock: str | None = Header(None)):
        """The current library's last 50 changes, newest first."""
        return changelog.entries(context.library()["role"] == "admin", security.is_unlocked(x_unlock))

    @app.get("/api/storage")
    def storage_status():
        """Whether the data folder survives the container being replaced."""
        return {"status": storage.status(db.path())}

    @app.get("/healthz", include_in_schema=False)
    def healthz():
        db.one("SELECT 1")
        return {"ok": True}

    return app


app = create_app()
