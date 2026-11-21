# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Aaron Fincher. See LICENSE.
"""For when you're locked out. Run inside the container:

    python -m app.reset_password            lists the accounts
    python -m app.reset_password USERNAME   gives that account a new random password

On TrueNAS: Apps > home-library > the container's shell icon, then run the command.
"""
import secrets
import sys

from . import auth, config, db


def main(argv: list[str]) -> int:
    db.init(config.DB_PATH)
    if len(argv) < 2:
        users = db.rows("SELECT username, role FROM users ORDER BY username COLLATE NOCASE")
        if not users:
            print("There are no accounts yet. Open the app to create the owner account.")
        for u in users:
            print(f"{u['username']}  ({'owner' if u['role'] == 'admin' else 'member'})")
        print("\nTo reset one: python -m app.reset_password USERNAME")
        return 0
    user = db.one("SELECT * FROM users WHERE username=?", (argv[1],))
    if not user:
        print(f"No account called {argv[1]!r}. Run without a username to list them.")
        return 1
    password = secrets.token_urlsafe(9)
    db.run("UPDATE users SET password=? WHERE id=?", (auth.hash_password(password), user["id"]))
    auth.end_all_sessions(user["id"])
    print(f"New password for {user['username']}: {password}")
    print("Log in with it, then change it in Settings.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
