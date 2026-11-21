import io
import urllib.error

import pytest
from fastapi.testclient import TestClient

from app import metadata
from app.main import create_app

ISBN = "9780441172719"

OPEN_LIBRARY = {f"ISBN:{ISBN}": {
    "title": "Dune", "authors": [{"name": "Frank Herbert"}], "publishers": [{"name": "Ace"}],
    "publish_date": "1990", "number_of_pages": 535, "cover": {"medium": "https://covers.example/dune-M.jpg"},
    "subjects": [{"name": "Science fiction"}, {"name": "fiction, science fiction, general"}, {"name": "Deserts"}]}}
GOOGLE = {"items": [{"volumeInfo": {
    "title": "Dune", "authors": ["Frank Herbert"], "publisher": "Penguin", "publishedDate": "2005-08-02",
    "description": "<p>Set on the desert planet <b>Arrakis</b>&hellip;</p>", "pageCount": 600,
    "categories": ["Fiction"], "language": "en",
    "industryIdentifiers": [{"type": "ISBN_13", "identifier": ISBN}],
    "imageLinks": {"thumbnail": "http://books.google.example/dune?zoom=1&edge=curl"}}}]}
ISBNDB = {"book": {"title": "Dune (Deluxe Edition)", "authors": ["Herbert, Frank", "Brian Herbert"], "isbn13": ISBN,
                   "image": "https://images.isbndb.example/dune.jpg", "synopsis": "ISBNdb synopsis",
                   "pages": 896, "publisher": "Ace", "date_published": "2019", "subjects": ["Fiction"]}}


class FakeWeb:
    """Answers get_json by URL. Values that are exceptions get raised."""

    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def __call__(self, url, headers=None, timeout=8):
        self.calls.append((url, headers or {}))
        for part, answer in self.routes.items():
            if part in url:
                if isinstance(answer, Exception):
                    raise answer
                return answer
        return {}


def http_error(code):
    return urllib.error.HTTPError("https://x", code, "err", {}, io.BytesIO(b""))


@pytest.fixture
def web(monkeypatch):
    def install(routes):
        fake = FakeWeb(routes)
        monkeypatch.setattr(metadata, "get_json", fake)
        return fake
    monkeypatch.delenv("ISBNDB_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_BOOKS_API_KEY", raising=False)
    return install


@pytest.fixture
def c(tmp_path):
    with TestClient(create_app(str(tmp_path / "library.db"))) as client:
        yield client


def test_isbn_cleaning():
    assert metadata.clean_isbn("978-0-441-17271-9") == ISBN
    assert metadata.clean_isbn("0-441-17271-7") == "0441172717"
    assert metadata.clean_isbn("044117271x") == "044117271X"
    assert metadata.clean_isbn("12345") == ""
    assert metadata.clean_isbn("Dune") == ""
    assert metadata.to_isbn13("0441172717") == ISBN


def test_lookup_merges_free_sources(c, web):
    web({"openlibrary.org/api/books": OPEN_LIBRARY, "googleapis.com": GOOGLE})
    d = c.get("/api/lookup/0441172717").json()
    # Open Library comes first, Google Books fills its gaps.
    assert d["title"] == "Dune" and d["publisher"] == "Ace" and d["pages"] == 535
    assert d["description"] == "Set on the desert planet Arrakis…"
    assert d["subjects"] == "Science fiction, Fiction, General, Deserts"
    assert d["language"] == "en"
    assert d["isbn"] == ISBN  # stored in 13-digit form
    assert d["sources"] == ["Open Library", "Google Books"]


def test_isbndb_used_first_when_key_set(c, web, monkeypatch):
    fake = web({"isbndb.com": ISBNDB, "openlibrary.org/api/books": OPEN_LIBRARY, "googleapis.com": GOOGLE})
    assert not any("isbndb" in u for u, _ in fake.calls)
    monkeypatch.setenv("ISBNDB_API_KEY", "secret-key")
    d = c.get(f"/api/lookup/{ISBN}").json()
    assert d["title"] == "Dune (Deluxe Edition)" and d["pages"] == 896
    assert d["authors"] == "Frank Herbert, Brian Herbert"
    assert d["sources"][0] == "ISBNdb"
    assert fake.calls[0][1]["Authorization"] == "secret-key"


def test_a_failing_source_is_skipped(c, web):
    web({"openlibrary.org": TimeoutError("down"), "googleapis.com": GOOGLE})
    assert c.get(f"/api/lookup/{ISBN}").json()["publisher"] == "Penguin"


def test_lookup_errors(c, web):
    web({})
    assert c.get("/api/lookup/12345").status_code == 400
    assert c.get(f"/api/lookup/{ISBN}").status_code == 404


def test_title_search(c, web):
    web({"googleapis.com": GOOGLE, "search.json": {"docs": [
        {"title": "Dune", "author_name": ["Frank Herbert"], "isbn": ["B0ASIN1234", "9788497596824", "0441172717"]},  # same as Google's
        {"title": "Dune Messiah", "author_name": ["Frank Herbert"], "cover_i": 42, "first_publish_year": 1969}]}})
    results = c.get("/api/lookup?q=dune herbert").json()["results"]
    assert [(r["title"], r["source"]) for r in results] == [("Dune", "Google Books"), ("Dune Messiah", "Open Library")]
    assert results[0]["cover"] == "https://books.google.example/dune?zoom=1"
    assert results[1]["cover"].endswith("/b/id/42-M.jpg")
    assert c.get("/api/lookup?q=d").status_code == 400


def test_isbndb_key_is_checked_before_saving(c, web):
    fake = web({"isbndb.com": http_error(401)})
    assert c.put("/api/sources/isbndb", json={"key": "bad"}).status_code == 400
    assert c.get("/api/sources").json()[0] == {"id": "isbndb", "name": "ISBNdb", "enabled": False, "needs_key": True,
                                               "key_source": ""}

    fake.routes["isbndb.com"] = ISBNDB
    assert c.put("/api/sources/isbndb", json={"key": "good"}).status_code == 200
    assert c.get("/api/sources").json()[0]["key_source"] == "settings"
    # The key is never sent back, and isn't in exports.
    assert "good" not in c.get("/api/sources").text
    assert "good" not in c.get("/api/export.json").text

    assert c.put("/api/sources/isbndb", json={"key": ""}).status_code == 200
    assert c.get("/api/sources").json()[0]["enabled"] is False


def test_isbndb_key_needs_pin_once_set(c, web):
    web({"isbndb.com": ISBNDB})
    c.post("/api/pin", json={"pin": "1234"})
    assert c.put("/api/sources/isbndb", json={"key": "good"}).status_code == 403


def test_new_fields_saved_and_searched(c):
    bid = c.post("/api/books", json={"title": "Dune Messiah", "wishlist": True, "series": "Dune",
                                      "series_index": 2, "subjects": "Science fiction", "pages": 256}).json()["id"]
    b = c.get(f"/api/books/{bid}").json()
    assert (b["series"], b["series_index"], b["pages"], b["description"]) == ("Dune", 2, 256, "")
    assert [x["id"] for x in c.get("/api/books?q=science").json()] == [bid]
    assert [x["id"] for x in c.get("/api/books?q=fiction dune").json()] == [bid]  # words in any order
    assert c.get("/api/books?q=fiction hobbit").json() == []
    assert c.patch(f"/api/books/{bid}", json={"pages": 0}).status_code == 422
    c.patch(f"/api/books/{bid}", json={"subtitle": None, "series": "  Dune Chronicles "})
    b = c.get(f"/api/books/{bid}").json()
    assert (b["subtitle"], b["series"]) == ("", "Dune Chronicles")


def test_fill_only_fills_blanks(c, web):
    web({"openlibrary.org/api/books": OPEN_LIBRARY, "googleapis.com": GOOGLE})
    bid = c.post("/api/books", json={"title": "My Dune", "isbn": ISBN, "wishlist": True,
                                      "publisher": "Typed by hand"}).json()["id"]
    filled = c.post(f"/api/books/{bid}/fill").json()["filled"]
    assert "publisher" not in filled and "description" in filled
    b = c.get(f"/api/books/{bid}").json()
    assert (b["title"], b["publisher"], b["pages"]) == ("My Dune", "Typed by hand", 535)

    nobook = c.post("/api/books", json={"title": "No ISBN", "wishlist": True}).json()["id"]
    assert c.post(f"/api/books/{nobook}/fill").status_code == 400


def test_fill_missing_walks_the_library_in_batches(c, web):
    web({"openlibrary.org/api/books": OPEN_LIBRARY, "googleapis.com": GOOGLE})
    ids = [c.post("/api/books", json={"title": f"Book {i}", "isbn": ISBN, "wishlist": True}).json()["id"]
           for i in range(7)]
    c.post("/api/books", json={"title": "No ISBN", "wishlist": True})
    first = c.post("/api/books/fill-missing").json()
    assert (first["checked"], first["filled"], first["done"]) == (5, 5, False)
    second = c.post(f"/api/books/fill-missing?after_id={first['last_id']}").json()
    assert (second["checked"], second["done"]) == (2, True)
    assert all(c.get(f"/api/books/{i}").json()["pages"] == 535 for i in ids)
    assert c.post("/api/books/fill-missing").json()["checked"] == 0  # nothing left missing


def test_old_backups_restore_with_new_fields_blank(c):
    old = {"app": "home-library", "version": 1, "people": [], "shelves": [],
           "books": [{"id": 1, "title": "Dune", "isbn": "", "wishlist": 1}]}
    assert c.post("/api/import", json=old).status_code == 200
    b = c.get("/api/books/1").json()
    assert (b["subtitle"], b["series"], b["pages"]) == ("", "", None)
    assert "Dune" in c.get("/api/export.csv").text


def test_google_key_is_used_and_checked(c, web):
    fake = web({"googleapis.com": http_error(400)})
    assert c.put("/api/sources/google", json={"key": "bad"}).status_code == 400
    fake.routes["googleapis.com"] = GOOGLE
    assert c.put("/api/sources/google", json={"key": "g-key"}).status_code == 200
    google = next(s for s in c.get("/api/sources").json() if s["id"] == "google")
    assert google["enabled"] and google["key_source"] == "settings"
    c.get(f"/api/lookup/{ISBN}")
    assert "key=g-key" in [u for u, _ in fake.calls if "googleapis" in u][-1]
    assert c.put("/api/sources/openlibrary", json={"key": "x"}).status_code == 404


def test_google_out_of_quota_while_checking_key(c, web):
    web({"googleapis.com": http_error(429)})
    r = c.put("/api/sources/google", json={"key": "g-key"})
    assert r.status_code == 502 and "Try again later" in r.json()["detail"]


def test_not_found_says_which_sources_could_not_be_asked(c, web):
    web({"openlibrary.org/api/books": {}, "googleapis.com": http_error(429)})
    r = c.get(f"/api/lookup/{ISBN}")
    assert r.status_code == 404
    assert r.json()["detail"].startswith("No match found. Google Books couldn't be asked")
    assert "free Google Books key in Settings" in r.json()["detail"]


def test_not_found_plain_when_every_source_answered(c, web):
    web({"openlibrary.org/api/books": {}, "googleapis.com": {}})
    assert c.get(f"/api/lookup/{ISBN}").json()["detail"] == "No match found."


def test_search_reports_problems(c, web):
    web({"googleapis.com": http_error(429), "search.json": TimeoutError("down")})
    r = c.get("/api/lookup?q=dune").json()
    assert r["results"] == []
    assert len(r["problems"]) == 2 and "Open Library couldn't be reached" in r["problems"]
