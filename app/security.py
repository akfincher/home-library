# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Aaron Fincher. See LICENSE.
"""Restricted-section PIN, unlock tokens and wrong-PIN throttling."""
import hashlib
import hmac
import secrets
import threading
import time

from fastapi import Header, HTTPException

from . import config, db

_cache: dict = {}


def reset() -> None:
    _cache.clear()
    limiter.reset()


def _secret() -> str:
    if "secret" not in _cache:
        s = db.get_setting("secret")
        if not s:
            s = secrets.token_hex(32)
            db.put_setting("secret", s)
        _cache["secret"] = s
    return _cache["secret"]


def pin_is_set() -> bool:
    return bool(db.get_setting("pin"))


def _hash(pin: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt), 200_000).hex()


def check_pin(pin: str) -> bool:
    stored = db.get_setting("pin")
    if not stored:
        return False
    salt, h = stored.split(":")
    return hmac.compare_digest(h, _hash(pin, salt))


def save_pin(pin: str) -> None:
    salt = secrets.token_hex(16)
    db.put_setting("pin", f"{salt}:{_hash(pin, salt)}")


def _sign(exp: str) -> str:
    # The stored PIN hash is part of the message, so changing the PIN
    # invalidates every unlock token issued before.
    msg = f"{exp}:{db.get_setting('pin') or ''}"
    return hmac.new(_secret().encode(), msg.encode(), "sha256").hexdigest()


def make_token() -> str:
    exp = str(int(time.time()) + config.UNLOCK_SECONDS)
    return f"{exp}.{_sign(exp)}"


def is_unlocked(tok: str | None) -> bool:
    if not tok or not pin_is_set():
        return False
    try:
        exp, sig = tok.split(".")
        return int(exp) > time.time() and hmac.compare_digest(sig, _sign(exp))
    except ValueError:
        return False


class Limiter:
    """Counts wrong PINs across all clients. After PIN_FREE_TRIES misses, each
    further miss doubles the wait, up to PIN_MAX_WAIT seconds. A right PIN resets it.
    It is global rather than per address because behind a reverse proxy every
    request arrives from the same address anyway."""

    def __init__(self):
        self._lock = threading.Lock()
        self.reset()

    def reset(self):
        self.failures = 0
        self.locked_until = 0.0

    def check(self):
        left = self.locked_until - time.time()
        if left > 0:
            wait = int(left) + 1
            raise HTTPException(429, f"Too many wrong PINs. Try again in {wait} seconds.",
                                headers={"Retry-After": str(wait)})

    def fail(self):
        with self._lock:
            self.failures += 1
            over = self.failures - config.PIN_FREE_TRIES
            if over >= 0:
                self.locked_until = time.time() + min(30 * 2 ** over, config.PIN_MAX_WAIT)

    def success(self):
        with self._lock:
            self.reset()


limiter = Limiter()


def unlocked(x_unlock: str | None = Header(None)) -> bool:
    return is_unlocked(x_unlock)


def require_admin(x_unlock: str | None = Header(None)) -> None:
    """For actions that could reveal or destroy restricted books. Open to anyone
    until a PIN is set, since a book can only be marked restricted once a PIN exists."""
    if pin_is_set() and not is_unlocked(x_unlock):
        raise HTTPException(403, "Enter your PIN first (the lock button at the top).")
