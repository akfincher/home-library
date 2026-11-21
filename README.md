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
