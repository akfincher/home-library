import os

DB_PATH = os.environ.get("DB_PATH", "/data/library.db")
# How long the restricted section stays open after entering the PIN.
UNLOCK_SECONDS = int(os.environ.get("UNLOCK_SECONDS", "900"))
# Wrong PINs allowed before the app starts making you wait.
PIN_FREE_TRIES = 5
PIN_MAX_WAIT = 900
