# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Aaron Fincher. See LICENSE.
"""Restricted-section PIN, unlock tokens and wrong-PIN throttling."""
import hashlib
import hmac
import secrets
import threading
import time

from fastapi import Header, HTTPException

from . import config, context, db

_cache: dict = {}


def reset() -> None:
    _cache.clear()
    _limiters.clear()


def _secret() -> str:
    if "secret" not in _cache:
        s = db.get_setting("secret")
        if not s:
            s = secrets.token_hex(32)
            db.put_setting("secret", s)
        _cache["secret"] = s
    return _cache["secret"]


def _stored(lib_id: int) -> str:
    r = db.one("SELECT pin FROM libraries WHERE id=?", (lib_id,))
    return (r or {}).get("pin") or ""


def pin_is_set(lib_id: int | None = None) -> bool:
    return bool(_stored(lib_id if lib_id is not None else context.lib()))


def _hash(pin: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt), 200_000).hex()


def check_pin(pin: str, lib_id: int | None = None) -> bool:
    stored = _stored(lib_id if lib_id is not None else context.lib())
    if not stored:
        return False
    salt, h = stored.split(":")
    return hmac.compare_digest(h, _hash(pin, salt))


def save_pin(pin: str, lib_id: int | None = None) -> None:
    salt = secrets.token_hex(16)
    db.run("UPDATE libraries SET pin=? WHERE id=?", (f"{salt}:{_hash(pin, salt)}", lib_id or context.lib()))


def _sign(exp: str, lib_id: int) -> str:
    # The library and its stored PIN hash are part of the message, so a token
    # only opens one library, and changing the PIN invalidates every earlier token.
    msg = f"{exp}:{lib_id}:{_stored(lib_id)}"
    return hmac.new(_secret().encode(), msg.encode(), "sha256").hexdigest()


def make_token(lib_id: int | None = None) -> str:
    lib_id = lib_id or context.lib()
    exp = str(int(time.time()) + config.UNLOCK_SECONDS)
    return f"{exp}.{_sign(exp, lib_id)}"


def is_unlocked(tok: str | None, lib_id: int | None = None) -> bool:
    if not tok:
        return False
    lib_id = lib_id or context.lib()
    if not _stored(lib_id):
        return False
    try:
        exp, sig = tok.split(".")
        return int(exp) > time.time() and hmac.compare_digest(sig, _sign(exp, lib_id))
    except ValueError:
        return False


class Limiter:
    """Counts wrong guesses across all clients. After PIN_FREE_TRIES misses, each
    further miss doubles the wait, up to PIN_MAX_WAIT seconds. A right PIN resets it.
    It is global rather than per address because behind a reverse proxy every
    request arrives from the same address anyway."""

    def __init__(self, what: str = "PINs"):
        self.what = what
        self._lock = threading.Lock()
        self.reset()

    def reset(self):
        self.failures = 0
        self.locked_until = 0.0

    def check(self):
        left = self.locked_until - time.time()
        if left > 0:
            wait = int(left) + 1
            raise HTTPException(429, f"Too many wrong {self.what}. Try again in {wait} seconds.",
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


_limiters: dict[int, Limiter] = {}


def limiter(lib_id: int | None = None) -> Limiter:
    """Wrong PINs are counted per library."""
    return _limiters.setdefault(lib_id or context.lib(), Limiter())


def unlocked(x_unlock: str | None = Header(None)) -> bool:
    return is_unlocked(x_unlock)


def require_admin(x_unlock: str | None = Header(None)) -> None:
    """For actions that could reveal or destroy restricted books. Open to anyone
    until a PIN is set, since a book can only be marked restricted once a PIN exists."""
    if pin_is_set() and not is_unlocked(x_unlock):
        raise HTTPException(403, "Enter your PIN first (the lock button at the top).")
