from fastapi import APIRouter, HTTPException

from .. import config, security
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


@router.post("/pin")
def set_pin(b: Pin):
    """Set the first PIN, or change it (needs the current one). Returns a fresh
    unlock token, because changing the PIN logs out every open session."""
    if security.pin_is_set():
        _verify(b.current)
    if len(b.pin) < 4:
        raise HTTPException(400, "Use at least 4 characters")
    security.save_pin(b.pin)
    return {"token": security.make_token()}


@router.post("/unlock")
def unlock(b: Pin):
    _verify(b.pin)
    return {"token": security.make_token()}
