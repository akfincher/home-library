# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Aaron Fincher. See LICENSE.
import os

DB_PATH = os.environ.get("DB_PATH", "/data/library.db")
# Where the source code can be found; Settings links to it.
# If you run a modified version, point this at your changes.
SOURCE_URL = os.environ.get("SOURCE_URL", "https://github.com/akfincher/home-library")
# How long the restricted section stays open after entering the PIN.
UNLOCK_SECONDS = int(os.environ.get("UNLOCK_SECONDS", "900"))
# Wrong PINs allowed before the app starts making you wait.
PIN_FREE_TRIES = 5
PIN_MAX_WAIT = 900
