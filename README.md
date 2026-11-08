# Home Library

A free, self-hosted web app for cataloging a home bookshelf. One container, SQLite storage, works in any phone or desktop browser.

## Features
Shelves, add books by ISBN scan or by hand (details from Open Library), search across all shelves, wish list, reviews, 1-5 ratings, spicy rating, AR level/points (manual entry), and a restricted section hidden behind a PIN that unlocks for 15 minutes (`UNLOCK_SECONDS` to change).

## Run with Docker
Replace `YOURNAME` in `docker-compose.yml` with the GitHub account that publishes the image, then:

    docker compose up -d

Open http://localhost:8000. Data is stored in `./data`.

## Run on TrueNAS
1. Create a dataset for the app's data.
2. Apps > Discover Apps > Install via YAML, then paste the contents of `docker-compose.yml`, using a host path to your dataset for the volume and an unused host port (for example `30080:8000`).

## Build it yourself
    docker build -t home-library .

## Notes
- Camera scanning only works over HTTPS (or localhost). Put a reverse proxy with a certificate in front of the app; manual ISBN entry works either way.
- There are no user accounts, so keep it on your home network or behind a VPN.
- Licensed under the MIT License.
