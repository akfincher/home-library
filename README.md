# Home Library

A free, self-hosted web app for cataloging a home bookshelf. One container, SQLite storage, works in any phone or desktop browser.

## Features
- Shelves you name and arrange, each either shared or belonging to one person in the household
- Arrange a shelf to match your real one: drag books into order (or use the arrows), and new books you add go on the end, so scanning a shelf left to right recreates it. Each shelf remembers how it's sorted: My order, title, author, series or newest
- Or let the app organize: put shelved books onto shelves by genre, author's last name, series, reading level or whose book it is. You see the result before anything moves, small groups share an "Other" shelf, the wish list is left alone, and a copy of the database is saved first
- People: add everyone in the house, then pick a person at the top to see only their shelves, wish list and books. Books that belong to someone else are hidden; books that belong to nobody in particular are shown to everyone
- A new library starts with a **Main** shelf, and Add book puts books there unless you choose another
- Add books by scanning the ISBN barcode, typing the ISBN, or searching by title and author, then picking the right book. A complete ISBN is looked up as soon as it's scanned or typed
- **Add several books in a row**: pick the shelf and person once, then scan book after book. Each is looked up and added straight away with a soft chime, the camera stays on, each ISBN is only added once per batch, and every book can be undone. Books that can't be found are added with their ISBN to fix later
- **Change history** (Settings > Change history): the last 50 changes to a library, who made them and exactly what changed, e.g. “rating from 0 stars to 5 stars”
- **Several libraries on one server**: keep separate libraries (home, a classroom, a book club), switch between them by tapping the library's name at the top, and share any of them with other accounts. Each library's books, shelves, people, PIN and history are private to its members
- Book details filled in automatically: cover, subtitle, description, publisher, date, pages, subjects and language, from ISBNdb (with a paid API key), Open Library and Google Books. Each source only fills what the earlier ones left blank. Series and anything else can be edited by hand, and Settings can fill in blanks for books already in your library without touching what you typed
- Search across every shelf and the wish list by title, author, series, subject, publisher or ISBN; sort shelves by title, author, series or newest
- Wish list, reviews, 0-5 star ratings and spice ratings (0 is "no spice", different from not rated; changed only in Edit book details, so a stray tap can't change them), AR level/points (manual entry)
- Titles sort without a leading "The", "A" or "An", so The Hunger Games is under H
- A restricted section hidden behind a PIN that unlocks for 15 minutes (`UNLOCK_SECONDS` to change). After 5 wrong PINs the app makes you wait, doubling each time up to 15 minutes. Changing the PIN locks every device that had it open.
- Backups: download a library as JSON (and restore it later, into the same or another library) or as a spreadsheet (CSV), from Settings. The server's administrator can also download the whole database

## On your phone
The app is designed for phones first. Open it in your phone's browser, then add it to your home screen (iPhone: Share > Add to Home Screen; Android: menu > Add to Home screen / Install app). It then opens full screen like an app, with its own icon.

## Accounts, libraries and sharing
Everyone logs in with their own account. The first account comes from `OWNER_USERNAME` / `OWNER_PASSWORD` when you deploy (see above); without those, the first visit asks you to create it. That account owns the first library and is the server's **administrator**.

- **Libraries.** Every account can have several libraries and switch between them without logging out: tap the library's name at the top of the screen. Create one there too, for example a classroom library alongside your home library.
- **Sharing.** In a library's Settings, owners add people by username. Someone who already has an account just gets the library added to their list. Giving a password instead creates a new account that only belongs to that library, handy for kids.
- **Members** can add, edit, arrange and search books and shelves. **Owners** of a library can also manage its members, set its restricted-section PIN, organize it, rename or delete it, and back it up or restore it.
- **Inviting people from outside the household.** Nobody can sign up without an **invite code**. The administrator can always create codes, and can switch on *Can invite people* for other accounts in *Settings > Server*. Anyone allowed sees *Settings > Invite people*: add a note saying who it's for, tap *Create code*, and send the code (or use *Share*). Each code works once, expires after 14 days, and can be cancelled before it's used; the list shows who used each one. Only a fingerprint of each code is stored, and wrong guesses are throttled. The new person taps *Have an invite code? Create an account* on the login page and gets their own private library, where they're the owner with the same rights you have in yours. People who sign up can't create codes themselves unless the administrator allows it.
- **The administrator** also sets the book-details API keys (shared by every library), sees and can delete every account on the server, and can download the whole database.
- An account can be linked to a person in a library, so it starts out showing that person's books.
- A library owner can only reset the password of an account that belongs to their library alone, so being in someone's classroom library never lets them take over your account.
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
    environment:
      OWNER_USERNAME: yourname
      OWNER_PASSWORD: choose-a-long-password
    restart: unless-stopped
```

`OWNER_USERNAME` and `OWNER_PASSWORD` create the owner account the first time the app starts, so it's never left open for someone else to claim. They're only used while the library has no accounts; change your password in the app afterwards (changing these values later does nothing). In the Custom App form, add them under Environment Variables.

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
