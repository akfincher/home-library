# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Aaron Fincher. See LICENSE.
"""Book details from outside sources.

ISBNdb is tried first when an API key is set, then Open Library, then Google
Books. Later sources only fill in what earlier ones left blank.
"""
import html
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

from . import db

USER_AGENT = "HomeLibrary/2 (self-hosted book catalog)"
FIELDS = ["title", "subtitle", "authors", "isbn", "cover", "description", "publisher", "published",
          "pages", "subjects", "series", "language"]
# Stop asking more sources once all of these are filled.
WANTED = ["title", "authors", "cover", "description", "publisher", "published", "pages", "subjects"]
TEST_ISBN = "9780441172719"  # Dune; used to check an ISBNdb key works


class KeyRejected(Exception):
    pass


def get_json(url: str, headers: dict | None = None, timeout: float = 8):
    """The one place that talks to the network (tests replace it)."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json",
                                               **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def clean_isbn(s: str) -> str:
    """Digits (and a final X) only, or '' if it can't be an ISBN."""
    s = re.sub(r"[^0-9Xx]", "", s or "").upper()
    if len(s) == 10 and re.fullmatch(r"\d{9}[\dX]", s):
        return s
    if len(s) == 13 and s.isdigit():
        return s
    return ""


def to_isbn13(isbn: str) -> str:
    if len(isbn) != 10:
        return isbn
    core = "978" + isbn[:9]
    check = (10 - sum((3 if i % 2 else 1) * int(c) for i, c in enumerate(core)) % 10) % 10
    return core + str(check)


def _text(v) -> str:
    """Plain text from a description that may contain HTML."""
    if isinstance(v, dict):
        v = v.get("value", "")
    v = re.sub(r"<\s*(br|/p)\s*/?>", "\n", str(v or ""), flags=re.I)
    v = html.unescape(re.sub(r"<[^>]+>", "", v))
    return re.sub(r"\n{3,}", "\n\n", v).strip()


def _int(v):
    try:
        n = int(v)
        return n if n > 0 else None
    except (TypeError, ValueError):
        return None


def _join(items, limit=8) -> str:
    out = []
    for i in items or []:
        name = (i.get("name") if isinstance(i, dict) else str(i)).strip()
        if name and name.lower() not in (o.lower() for o in out):
            out.append(name)
    return ", ".join(out[:limit])


def _subjects(items, limit=8) -> str:
    """Subjects joined with commas. Some sources put commas inside one subject
    ("Fiction, science fiction, general"), so those are split up first."""
    parts = []
    for i in items or []:
        name = i.get("name") if isinstance(i, dict) else str(i)
        parts += [p.strip() for p in re.split(r"[,/]", name or "")]
    out, seen = [], set()
    for p in parts:
        key = re.sub(r"[-\s]+", " ", p.lower())
        # Skip blanks, repeats and Open Library's internal tags like "nyt:fiction=2021-11-07".
        if p and key not in seen and not re.search(r"[:=]", p):
            seen.add(key)
            out.append(p[0].upper() + p[1:])
    return ", ".join(out[:limit])


def _first_last(name: str) -> str:
    """'Herbert, Frank' -> 'Frank Herbert'."""
    m = re.fullmatch(r"\s*([^,]+),\s*([^,]+?)\s*", name or "")
    return f"{m.group(2)} {m.group(1)}" if m else (name or "").strip()


def _best_isbn(isbns) -> str:
    """From all of a work's editions, prefer an English-language ISBN-13."""
    valid = [clean_isbn(i) for i in isbns or []]
    valid = [to_isbn13(i) for i in valid if i]
    return next((i for i in valid if i.startswith(("9780", "9781"))), valid[0] if valid else "")


def _https(url) -> str:
    return re.sub(r"^http://", "https://", url or "")


class Keyed:
    """A source that takes an API key, from an environment variable or from Settings."""
    env = setting = ""
    needs_key = False  # True if the source can't be used at all without one

    def __init__(self, key: str | None = None):
        self._key = key

    def key(self) -> str:
        return self._key or os.environ.get(self.env) or db.get_setting(self.setting) or ""

    def key_source(self) -> str:
        if os.environ.get(self.env):
            return "env"
        return "settings" if db.get_setting(self.setting) else ""

    def enabled(self) -> bool:
        return bool(self.key()) or not self.needs_key


class ISBNdb(Keyed):
    id, name = "isbndb", "ISBNdb"
    env, setting = "ISBNDB_API_KEY", "isbndb_key"
    needs_key = True

    def _get(self, path):
        base = os.environ.get("ISBNDB_API_URL", "https://api2.isbndb.com")
        try:
            return get_json(base + path, {"Authorization": self.key()})
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise KeyRejected("ISBNdb didn't accept the API key")
            if e.code == 404:
                return None
            raise

    @staticmethod
    def _book(b: dict) -> dict:
        return {"title": b.get("title") or b.get("title_long") or "", "authors": _join([_first_last(a) for a in b.get("authors") or []]),
                "isbn": b.get("isbn13") or b.get("isbn") or "", "cover": _https(b.get("image")),
                "description": _text(b.get("synopsis") or b.get("overview")), "publisher": b.get("publisher") or "",
                "published": str(b.get("date_published") or ""), "pages": _int(b.get("pages")),
                "subjects": _subjects(b.get("subjects")), "language": b.get("language") or ""}

    def by_isbn(self, isbn):
        d = self._get(f"/book/{isbn}")
        return self._book(d["book"]) if d and d.get("book") else None

    def search(self, q):
        d = self._get(f"/books/{urllib.parse.quote(q)}?page=1&pageSize=10") or {}
        return [self._book(b) for b in d.get("books", [])]


class OpenLibrary:
    id, name = "openlibrary", "Open Library"
    needs_key = False

    def enabled(self):
        return True

    def by_isbn(self, isbn):
        d = get_json(f"https://openlibrary.org/api/books?bibkeys=ISBN:{isbn}&format=json&jscmd=data")
        d = (d or {}).get(f"ISBN:{isbn}")
        if not d:
            return None
        return {"title": d.get("title", ""), "subtitle": d.get("subtitle", ""), "authors": _join(d.get("authors")),
                "isbn": isbn, "cover": (d.get("cover") or {}).get("medium", ""),
                "publisher": _join(d.get("publishers"), 2), "published": d.get("publish_date", ""),
                "pages": _int(d.get("number_of_pages")), "subjects": _subjects(d.get("subjects"))}

    def search(self, q):
        fields = "title,subtitle,author_name,first_publish_year,isbn,cover_i,number_of_pages_median,publisher"
        d = get_json("https://openlibrary.org/search.json?" + urllib.parse.urlencode({"q": q, "limit": 10, "fields": fields}))
        out = []
        for doc in (d or {}).get("docs", []):
            out.append({"title": doc.get("title", ""), "subtitle": doc.get("subtitle", ""),
                        "authors": _join(doc.get("author_name"), 3),
                        "isbn": _best_isbn(doc.get("isbn")),
                        "cover": f"https://covers.openlibrary.org/b/id/{doc['cover_i']}-M.jpg" if doc.get("cover_i") else "",
                        "published": str(doc.get("first_publish_year") or ""),
                        "pages": _int(doc.get("number_of_pages_median")),
                        "publisher": (doc.get("publisher") or [""])[0]})
        return out


class GoogleBooks(Keyed):
    """Works without a key, but keyless requests share one small daily quota
    with everyone else, so in practice a (free) key is needed."""
    id, name = "google", "Google Books"
    env, setting = "GOOGLE_BOOKS_API_KEY", "google_books_key"

    def _url(self, q, n):
        params = {"q": q, "maxResults": n, "printType": "books"}
        if self.key():
            params["key"] = self.key()
        return "https://www.googleapis.com/books/v1/volumes?" + urllib.parse.urlencode(params)

    def _get(self, q, n):
        try:
            return get_json(self._url(q, n)) or {}
        except urllib.error.HTTPError as e:
            if e.code in (400, 403) and self.key():
                raise KeyRejected("Google didn't accept the API key. Check that the Books API is turned on for it.")
            raise

    @staticmethod
    def _volume(v: dict) -> dict:
        info = v.get("volumeInfo", {})
        ids = {i.get("type"): i.get("identifier") for i in info.get("industryIdentifiers", [])}
        img = info.get("imageLinks", {})
        cover = _https(img.get("thumbnail") or img.get("smallThumbnail")).replace("&edge=curl", "")
        return {"title": info.get("title", ""), "subtitle": info.get("subtitle", ""),
                "authors": _join(info.get("authors")), "isbn": ids.get("ISBN_13") or ids.get("ISBN_10") or "",
                "cover": cover, "description": _text(info.get("description")),
                "publisher": info.get("publisher", ""), "published": info.get("publishedDate", ""),
                "pages": _int(info.get("pageCount")), "subjects": _subjects(info.get("categories")),
                "language": info.get("language", "")}

    def by_isbn(self, isbn):
        items = self._get(f"isbn:{isbn}", 1).get("items") or []
        return self._volume(items[0]) if items else None

    def search(self, q):
        return [self._volume(v) for v in self._get(q, 10).get("items") or []]


def sources():
    return [ISBNdb(), OpenLibrary(), GoogleBooks()]


def status():
    out = []
    for s in sources():
        row = {"id": s.id, "name": s.name, "enabled": s.enabled(), "needs_key": s.needs_key}
        if isinstance(s, Keyed):
            row["key_source"] = s.key_source()
        out.append(row)
    return out


def problem(source, err: Exception) -> str:
    """A plain-language reason a source couldn't be asked."""
    if isinstance(err, KeyRejected):
        return str(err)
    if isinstance(err, urllib.error.HTTPError) and err.code == 429:
        if isinstance(source, GoogleBooks) and not source.key():
            return ("Google Books couldn't be asked: its shared free daily limit is used up. "
                    "Adding your own free Google Books key in Settings fixes this")
        return f"{source.name} couldn't be asked: its daily limit is used up"
    return f"{source.name} couldn't be reached"


def lookup(isbn: str, problems: list | None = None) -> dict | None:
    """Everything the sources know about one ISBN, merged, or None. Reasons any
    source couldn't be asked are added to `problems`."""
    # Every source accepts the 13-digit form, and it's the one stored.
    isbn = to_isbn13(isbn)
    result, used = {}, []
    for s in sources():
        if not s.enabled():
            continue
        try:
            d = s.by_isbn(isbn)
        except Exception as e:  # one source being down or refusing shouldn't stop the others
            if problems is not None:
                problems.append(problem(s, e))
            continue
        if not d or not d.get("title"):
            continue
        used.append(s.name)
        for k in FIELDS:
            if d.get(k) and not result.get(k):
                result[k] = d[k]
        if all(result.get(k) for k in WANTED):
            break
    if not result:
        return None
    result["isbn"] = isbn
    result["description"] = result.get("description", "")[:20000]
    result["sources"] = used
    return result


def search(q: str, limit: int = 15, problems: list | None = None) -> list[dict]:
    """Candidates for a title/author search, best sources first, without repeats."""
    out, seen = [], set()
    for s in sorted(sources(), key=lambda s: ["ISBNdb", "Google Books", "Open Library"].index(s.name)):
        if not s.enabled():
            continue
        try:
            found = s.search(q)
        except Exception as e:
            if problems is not None:
                problems.append(problem(s, e))
            continue
        for r in found:
            if not r.get("title"):
                continue
            key = r.get("isbn") or (r["title"] + "|" + r.get("authors", "")).lower()
            if key in seen:
                continue
            seen.add(key)
            out.append({**r, "source": s.name})
            if len(out) >= limit:
                return out
    return out


# Sources whose key can be set in Settings, by the name used in the API path.
KEYED = {"isbndb": ISBNdb, "google": GoogleBooks}


def check_key(source: str, key: str) -> None:
    """Raises KeyRejected if the source refuses the key, ConnectionError if it can't be reached."""
    s = KEYED[source](key)
    try:
        s.by_isbn(TEST_ISBN)
    except KeyRejected:
        raise
    except Exception as e:
        raise ConnectionError(f"Couldn't reach {s.name} to check the key. Try again later.") from e
