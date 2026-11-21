# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Aaron Fincher. See LICENSE.
from fastapi import APIRouter, Depends, HTTPException

from .. import auth, config, security
from ..changelog import log
from ..schemas import Pin

router = APIRouter(prefix="/api")


@router.get("/pin-status")
def pin_status():
    return {"set": security.pin_is_set(), "unlock_seconds": config.UNLOCK_SECONDS}


def _verify(pin: str) -> None:
    security.limiter.check()
    if not security.check_pin(pin):
        security.limiter.fail()
        raise HTTPException(403, "Wrong PIN")
    security.limiter.success()


@router.post("/pin", dependencies=[Depends(auth.require_owner)])
def set_pin(b: Pin):
    """Set the first PIN, or change it (needs the current one). Returns a fresh
    unlock token, because changing the PIN logs out every open session."""
    had_pin = security.pin_is_set()
    if had_pin:
        _verify(b.current)
    if len(b.pin) < 4:
        raise HTTPException(400, "Use at least 4 characters")
    security.save_pin(b.pin)
    log(f"{'Changed' if had_pin else 'Set'} the restricted-section PIN", admin=True)
    return {"token": security.make_token()}


@router.post("/unlock")
def unlock(b: Pin):
    _verify(b.pin)
    return {"token": security.make_token()}
