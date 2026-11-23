# Home Library

A free, self-hosted web app for cataloging a home bookshelf. One container, SQLite storage, works in any phone or desktop browser.

## Features
- Shelves you name and arrange, each either shared or belonging to one person in the household
- Arrange a shelf to match your real one: drag books into order (or use the arrows), and new books you add go on the end, so scanning a shelf left to right recreates it. Each shelf remembers how it's sorted: My order, title, author, series or newest
- Or let the app organize: put shelved books onto shelves by genre, author's last name, series, reading level or whose book it is. You see the result before anything moves, small groups share an "Other" shelf, the wish list is left alone, and a copy of the database is saved first
- People: add everyone in the house, then pick a person at the top to see only their shelves, wish list and books. Books that belong to someone else are hidden; books that belong to nobody in particular are shown to everyone
- A new library starts with a **Main** shelf, and Add book puts books there unless you choose another
- Add books by scanning the ISBN barcode, typing the ISBN, or searching by title and author, then picking the right book. A complete ISBN is looked up as soon as it's scanned or typed
- **Add several books in a row**: pick the shelf and person once, then scan book after book. Each is looked up and added straight away with a soft chime and a green outline around the camera, the camera stays on, each ISBN is only added once per batch (a repeat gets a red outline instead), and every book can be undone. Books that can't be found are added with their ISBN to fix later
- **Change history** (Settings > Change history): the last 50 changes to a library, who made them and exactly what changed, e.g. “rating from 0 stars to 5 stars”
- **Several libraries on one server**: keep separate libraries (home, a classroom, a book club), switch between them by tapping the library's name at the top, and share any of them with other accounts. Each library's books, shelves, people, PIN and history are private to its members
- Book details filled in automatically: cover, subtitle, description, publisher, date, pages, subjects and language, from ISBNdb (with a paid API key), Open Library and Google Books. Each source only fills what the earlier ones left blank. Series and anything else can be edited by hand, and *Settings > Fill in missing covers & details* looks up every book with an ISBN again and fills in only what's blank (cover, description, pages, publisher, subjects), never touching what you typed. Handy after adding an API key, or for books added before their details could be found
- Search across every shelf and the wish list by title, author, series, subject, publisher or ISBN; sort shelves by title, author, series or newest
- **Search by barcode**: tap *Scan* on the Search screen and point the camera at a book's barcode. A good scan gives the same chime and green flash as adding books, and searches by that ISBN straight away (typing an ISBN works too, with or without dashes, 10 or 13 digits). Besides the exact copy, it shows any **other edition** of the same book already in your library (same title and author, but a different cover, page count or format), so you can tell you have it before buying it again. If it's not in your library at all, *Add it* opens Add book with the details looked up
- **Reading** (its own tab, and private to each person): tap *Start reading* on a book and update the page you're on as you go, with a progress bar. *Finished it* moves it to the books you've finished this year; *Didn't finish* keeps a list of books you stopped, and where, which you can pick back up. Books read in the past can be added with *I've read it* and a date
- **Your year in books** (Reading > report): books finished, pages read, an estimate of words (at 250 a page), your average rating of the books you finished, books per month, top genres, your longest book, and the list, for any year
- **Themes**: 20 looks to choose from in Settings (Classic, Library, Parchment, Floral, Garden, Geometric, Steampunk, Ocean, Midnight, Sunshine, Autumn, Winter, Retro '70s, Arcade, Coffee House, Lavender, Nautical, Galaxy, Desert, Typewriter), each with its own colours, fonts and background pattern. Your choice is saved with your account, so it follows you to every device
- **Maintenance notice**: the server's administrator can put up a message for everyone (also shown on the login page), for example before an update, and choose when it disappears
- **Lending**: lend a book to someone with an optional due date and note. It shows *On loan* on the shelf, and *Shelves > On loan* lists everything that's out, overdue first, with *It's back* to check it in. Recent returns are kept, and lending shows in the change history
- Wish list, reviews, 0-5 star ratings and spice ratings (0 is "no spice", different from not rated; changed only in Edit book details, so a stray tap can't change them), AR level/points (manual entry)
- Titles sort without a leading "The", "A" or "An", so The Hunger Games is under H
- A restricted section hidden behind a PIN that unlocks for 15 minutes (`UNLOCK_SECONDS` to change). After 5 wrong PINs the app makes you wait, doubling each time up to 15 minutes. Changing the PIN locks every device that had it open.
- Backups: download a library as JSON (and restore it later, into the same or another library) or as a spreadsheet (CSV), from Settings. The server's administrator can also download the whole database

## On your phone
The app is designed for phones first. Open it in your phone's browser, then add it to your home screen (iPhone: Share > Add to Home Screen; Android: menu > Add to Home screen / Install app). It then opens full screen like an app, with its own icon.

## Accounts, libraries and sharing
Everyone logs in with their own account. The first account comes from `OWNER_USERNAME` / `OWNER_PASSWORD` when you deploy (see above); without those, the first visit asks you to create it. That account owns the first library and is the server's **administrator**.

- **Libraries.** Every account can have several libraries and switch between them without logging out: tap the library's name at the top of the screen. Create one there too, for example a classroom library alongside your home library.
- **Identity & access management** (*Settings > Your account*) is where everything about accounts lives: your own password and logging out, inviting people, and, for the administrator, creating accounts, resetting passwords and choosing who can use which library.
- **Roles in a library.** **Owners** can do everything, including setting its restricted-section PIN, organizing it, renaming or deleting it, and backing it up or restoring it. **Members** can add, edit, arrange and search books and shelves. **Viewers** can look at every shelf and book but change nothing (the app hides the editing buttons, and the server refuses changes); they can still keep track of their own reading, and start a library of their own. Use it to let a grandparent or a friend browse your shelves.
- **Settings** is grouped into *Your account*, *This library* (everything that belongs to the library you're in, with a switcher) and *Server* (for the administrator), each section opening and closing on tap.
- **Inviting people.** Nobody can sign up unless they're invited. The administrator can always invite people, and can allow other accounts to. Enter the person's email address (and their name if you like), choose whether they get their own new library or access to one of yours (as an owner, member or viewer), and tap *Invite*. That makes a personal sign-up link; *Email it* opens your own email app with the message written and addressed, or use *Share* or *Copy link*. If that email address already has an account, they're simply given access. Each link works once, expires after 14 days, and can be cancelled; inviting the same address again replaces the old link. Only a fingerprint of each link's secret is stored, and guessing is throttled. Cancelled and expired invitations can be removed from the list.

  The app doesn't send email itself (that would need a mail server), so it can't confirm someone owns an address. The link is what controls access: only the person you send it to can use it.
- **Creating accounts.** The administrator can also create an account directly. The app makes up a simple 8-character password (no easily confused characters like 0/O or 1/l) to give to the person; they have to choose their own password the first time they log in, before they can do anything else.
- **Managing accounts.** Tap *Manage* on an account to change its email address, let it invite people, give it access to any library as an owner, member or viewer (or take that away), make it a new password, or remove it. A library always keeps at least one owner.
- **Forgot password?** The login page has a link for it. The person enters the email address on their account; if one matches, the administrator sees a reset request in *Identity & access management* and can make them a new password (which they then change at their next login) or dismiss it. Nothing changes until the administrator acts, so knowing someone's email address isn't enough to take over their account. Requests are throttled.
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
Copyright (c) 2026 Aaron Fincher.

Home Library is released under the [MIT License](LICENSE): you can use, copy, change and share it, including in other projects, as long as the copyright and license notice stay with it. It comes with no warranty. `SOURCE_URL` sets where the Source code link in Settings points, if you run your own fork.
