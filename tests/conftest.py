# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Aaron Fincher. See LICENSE.
OWNER = {"username": "owner", "password": "correct horse battery"}


def log_in_as_owner(client):
    """Creates the owner account (which also logs the client in)."""
    r = client.post("/api/auth/setup", json=OWNER)
    assert r.status_code == 200, r.text
    return client
