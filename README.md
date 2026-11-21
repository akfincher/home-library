# Home Library

A free, self-hosted web app for cataloging a home bookshelf. One container, SQLite storage, works in any phone or desktop browser.

## Features
- Shelves you name and arrange, each either shared or belonging to one person in the household
- People: add everyone in the house, then pick a person at the top to see only their shelves and wish list
- Add books by scanning the ISBN barcode or by hand (details from Open Library)
- Search across every shelf and the wish list by title, author or ISBN
- Wish list, reviews, 1-5 ratings, spicy rating, AR level/points (manual entry)
- A restricted section hidden behind a PIN that unlocks for 15 minutes (`UNLOCK_SECONDS` to change). After 5 wrong PINs the app makes you wait, doubling each time up to 15 minutes. Changing the PIN locks every device that had it open.
- Backups: download everything as JSON (and restore it later), as a spreadsheet (CSV), or as the raw database file, from Settings

## On your phone
The app is designed for phones first. Open it in your phone's browser, then add it to your home screen (iPhone: Share > Add to Home Screen; Android: menu > Add to Home screen / Install app). It then opens full screen like an app, with its own icon.

## Run with Docker

    docker compose up -d

Open http://localhost:8000. Data is stored in `./data`.

## Run on TrueNAS
1. Create a dataset for the app's data.
2. Apps > Discover Apps > Install via YAML, then paste the contents of `docker-compose.yml`, using a host path to your dataset for the volume and an unused host port (for example `30080:8000`).

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
- People are profiles, not logins: anyone who can open the app can see and edit everything outside the restricted section. Keep it on your home network or behind a VPN.
- Licensed under the MIT License.
