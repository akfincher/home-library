# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Aaron Fincher. See LICENSE.
"""Who is making the current request, and which library they're working in.

The login middleware sets these once per request; everything that reads or
changes library data asks `lib()` for the library, so one library can never
see or touch another's books.
"""
from contextvars import ContextVar

from fastapi import HTTPException

USER: ContextVar[dict | None] = ContextVar("user", default=None)
LIBRARY: ContextVar[dict | None] = ContextVar("library", default=None)


def lib() -> int:
    """The current library's id."""
    current = LIBRARY.get()
    if not current:
        raise HTTPException(409, "You're not in a library yet. Create one in Settings, or ask an owner to add you.")
    return current["id"]


def library() -> dict:
    """The current library: id, name, and your role and linked person in it."""
    lib()
    return LIBRARY.get()
