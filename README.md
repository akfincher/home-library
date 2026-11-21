# Home Library

A free, self-hosted web app for cataloging a home bookshelf. One container, SQLite storage, works in any phone or desktop browser.

## Features
- Shelves you name and arrange, each either shared or belonging to one person in the household
- Arrange a shelf to match your real one: drag books into order (or use the arrows), and new books you add go on the end, so scanning a shelf left to right recreates it. Each shelf remembers how it's sorted: My order, title, author, series or newest
- Or let the app organize: put shelved books onto shelves by genre, author's last name, series, reading level or whose book it is. You see the result before anything moves, small groups share an "Other" shelf, the wish list is left alone, and a copy of the database is saved first
- People: add everyone in the house, then pick a person at the top to see only their shelves and wish list
- Add books by scanning the ISBN barcode, typing the ISBN, or searching by title and author, then picking the right book
- Book details filled in automatically: cover, subtitle, description, publisher, date, pages, subjects and language, from ISBNdb (with a paid API key), Open Library and Google Books. Each source only fills what the earlier ones left blank. Series and anything else can be edited by hand, and Settings can fill in blanks for books already in your library without touching what you typed
- Search across every shelf and the wish list by title, author, series, subject, publisher or ISBN; sort shelves by title, author, series or newest
- Wish list, reviews, 1-5 ratings, spicy rating, AR level/points (manual entry)
- A restricted section hidden behind a PIN that unlocks for 15 minutes (`UNLOCK_SECONDS` to change). After 5 wrong PINs the app makes you wait, doubling each time up to 15 minutes. Changing the PIN locks every device that had it open.
- Backups: download everything as JSON (and restore it later), as a spreadsheet (CSV), or as the raw database file, from Settings

## On your phone
The app is designed for phones first. Open it in your phone's browser, then add it to your home screen (iPhone: Share > Add to Home Screen; Android: menu > Add to Home screen / Install app). It then opens full screen like an app, with its own icon.

## Accounts and logging in
Everyone logs in with their own account. The first time you open the app it asks you to create the **owner** account; after that, owners add accounts for everyone else in Settings.

- **Members** can add, edit, arrange and search books and shelves.
- **Owners** can also add and remove accounts, set the restricted-section PIN, add API keys, organize the whole library and download or restore backups.
- An account can be linked to a person, so it starts out showing that person's books.
- You stay logged in on each device for 30 days after you last used it. Changing your password logs out your other devices.
- After 5 wrong passwords for an account, the app makes you wait before trying again, doubling each time up to 15 minutes.

**Locked out?** Open a shell in the container (on TrueNAS: Apps > home-library > the shell icon next to the container) and run:

    python -m app.reset_password            # lists the accounts
    python -m app.reset_password USERNAME   # gives that account a new random password

**Reaching it from outside your home:** put it behind a reverse proxy with HTTPS, such as Nginx Proxy Manager, on its own subdomain. Login cookies are then sent only over HTTPS. Create the owner account before you open it to the internet, because until then anyone who finds it could create it.

## Run with Docker

    docker compose up -d

Open http://localhost:8000. Data is stored in `./data`.

## Run on TrueNAS
First create a dataset for the app's data (Datasets > Add Dataset), for example `apps/home-library`. Its path will be `/mnt/YOURPOOL/apps/home-library`. Then use either way below.

**Install via YAML** (Apps > Discover Apps > ⋮ > Install via YAML), with your pool name in place of `YOURPOOL`:

```yaml
services:
  library:
    image: ghcr.io/akfincher/home-library:latest
    ports:
      - "30080:8000"
    volumes:
      - /mnt/YOURPOOL/apps/home-library:/data
    restart: unless-stopped
```

**Or the Custom App form** (Apps > Discover Apps > Custom App):

| Setting | Value |
|---|---|
| Image > Repository | `ghcr.io/akfincher/home-library` (without `:latest`) |
| Image > Tag | `latest` |
| Network > Port Forwarding | Container Port `8000`, Host Port `30080`, TCP |
| Storage | Host Path `/mnt/YOURPOOL/apps/home-library` mounted at `/data` |

Then open `http://<truenas-ip>:30080`. Note it is `http://`, not `https://`.

Common problems:
- *invalid reference format*: the Repository field also has `:latest` on the end, so the image name became `...:latest:latest`.
- *Running, but the page won't connect*: the port numbers are swapped. On the app's page, Workloads > Ports should read `0.0.0.0:30080 : 8000`.
- *Library empty after an update*: Storage was left as temporary storage instead of a Host Path.

**HTTPS (needed for live barcode scanning on phones):** put a reverse proxy in front, such as the Nginx Proxy Manager app: add a Proxy Host forwarding to `http://<truenas-ip>:30080` and request a certificate on its SSL tab. Tailscale Serve also works and keeps the app private to your devices. Don't expose the app to the internet (for example through a public Cloudflare Tunnel) without a login in front of it, because it has no accounts of its own.

## Book details sources
- **Open Library**: free, always on.
- **Google Books**: free, but without a key it shares one small daily allowance with every other app that has no key, which often runs out. Create a free API key in Google Cloud Console (turn on the Books API for it) and paste it in Settings.
- **ISBNdb**: paid, knows the most books. Paste your API key in Settings and it's asked first.

Keys can also be set with the `ISBNDB_API_KEY` and `GOOGLE_BOOKS_API_KEY` environment variables, which take priority over Settings. Keys are never included in JSON or CSV exports.

## Upgrading
Pull the new image and restart the container. The database upgrades itself on startup, and saves a copy of the old one first as `library.db.pre-vN-<date>.bak` in the data folder. Once the new version is working you can delete that file. Restoring a JSON backup also saves a `library.db.pre-import-<date>.bak` first.

## Develop

    python -m venv .venv
    .venv/bin/pip install -r requirements-dev.txt     # Windows: .venv\Scripts\pip
    DB_PATH=./data/library.db .venv/bin/uvicorn app.main:app --reload
    .venv/bin/pytest

Code layout: `app/db.py` (database and migrations), `app/security.py` (PIN and unlocking), `app/routers/` (one file per part of the API), `app/static/index.html` (the whole web page). To change the database, add a new entry to the end of `MIGRATIONS` in `app/db.py`; never edit one that has shipped.

## Build it yourself
    docker build -t home-library .

## Notes
- Camera scanning only works over HTTPS (or localhost). Put a reverse proxy with a certificate in front of the app; manual ISBN entry works either way.

## License
Copyright (C) 2026 Aaron Fincher.

Home Library is free software: you can redistribute it and/or modify it under the terms of the [GNU Affero General Public License](LICENSE) as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later version. It is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

In short: you can use, change and share it, but if you share a modified version, or let other people use a modified version over a network, you must make your source code available under the same license. If you run a modified copy, set the `SOURCE_URL` environment variable to where your changes can be downloaded; the app links to it from Settings.
