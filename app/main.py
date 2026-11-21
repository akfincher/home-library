import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import config, db, security
from .routers import backup, books, lookup, people, pin, shelves

STATIC = os.path.join(os.path.dirname(__file__), "static")


def create_app(db_path: str | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app):
        db.init(db_path or config.DB_PATH)
        security.reset()
        yield

    app = FastAPI(title="Home Library", lifespan=lifespan)
    for r in (pin, people, shelves, books, lookup, backup):
        app.include_router(r.router)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(os.path.join(STATIC, "index.html"))

    @app.get("/healthz", include_in_schema=False)
    def healthz():
        db.one("SELECT 1")
        return {"ok": True}

    return app


app = create_app()
