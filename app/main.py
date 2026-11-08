import hashlib, hmac, json, os, secrets, sqlite3, time, urllib.request
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

DB = os.environ.get("DB_PATH", "/data/library.db")
UNLOCK_SECONDS = int(os.environ.get("UNLOCK_SECONDS", "900"))
app = FastAPI(title="Home Library")


def db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


os.makedirs(os.path.dirname(DB) or ".", exist_ok=True)
with db() as c:
    c.executescript("""
    CREATE TABLE IF NOT EXISTS shelves(id INTEGER PRIMARY KEY, name TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS books(id INTEGER PRIMARY KEY, isbn TEXT DEFAULT '', title TEXT NOT NULL,
      authors TEXT DEFAULT '', cover TEXT DEFAULT '', shelf_id INTEGER, wishlist INTEGER DEFAULT 0,
      restricted INTEGER DEFAULT 0, rating INTEGER DEFAULT 0, spicy INTEGER DEFAULT 0,
      review TEXT DEFAULT '', ar_level REAL, ar_points REAL);
    CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY, v TEXT);""")


def get(k):
    r = db().execute("SELECT v FROM settings WHERE k=?", (k,)).fetchone()
    return r["v"] if r else None


def put(k, v):
    with db() as c:
        c.execute("INSERT OR REPLACE INTO settings VALUES(?,?)", (k, v))


SECRET = get("secret")
if not SECRET:
    SECRET = secrets.token_hex(32)
    put("secret", SECRET)


def sign(exp):
    return hmac.new(SECRET.encode(), str(exp).encode(), "sha256").hexdigest()


def unlocked(tok):
    try:
        exp, sig = (tok or "").split(".")
        return hmac.compare_digest(sig, sign(exp)) and int(exp) > time.time()
    except ValueError:
        return False


def pin_hash(pin, salt):
    return hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt), 100_000).hex()


def check_pin(pin):
    stored = get("pin")
    if not stored:
        return False
    salt, h = stored.split(":")
    return hmac.compare_digest(h, pin_hash(pin, salt))


class Pin(BaseModel):
    pin: str
    current: str = ""


class Named(BaseModel):
    name: str


class NewBook(BaseModel):
    title: str
    authors: str = ""
    isbn: str = ""
    cover: str = ""
    shelf_id: int | None = None
    wishlist: int = 0


FIELDS = {"title", "authors", "shelf_id", "wishlist", "restricted", "rating", "spicy", "review", "ar_level", "ar_points"}


def owned(bid, tok):
    r = db().execute("SELECT * FROM books WHERE id=?", (bid,)).fetchone()
    if not r:
        raise HTTPException(404, "Book not found")
    if r["restricted"] and not unlocked(tok):
        raise HTTPException(403, "Unlock the restricted section first")
    return dict(r)


@app.get("/")
def index():
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "index.html"))


@app.get("/api/pin-status")
def pin_status():
    return {"set": bool(get("pin"))}


@app.post("/api/pin")
def set_pin(b: Pin):
    if get("pin") and not check_pin(b.current):
        raise HTTPException(403, "Wrong current PIN")
    if len(b.pin) < 4:
        raise HTTPException(400, "Use at least 4 characters")
    salt = secrets.token_hex(8)
    put("pin", f"{salt}:{pin_hash(b.pin, salt)}")
    return {"ok": True}


@app.post("/api/unlock")
def unlock(b: Pin):
    if not check_pin(b.pin):
        raise HTTPException(403, "Wrong PIN")
    exp = int(time.time()) + UNLOCK_SECONDS
    return {"token": f"{exp}.{sign(exp)}"}


@app.get("/api/shelves")
def shelves(x_unlock: str = Header(None)):
    extra = "" if unlocked(x_unlock) else " AND b.restricted=0"
    q = (f"SELECT s.id, s.name, COUNT(b.id) AS count FROM shelves s LEFT JOIN books b "
         f"ON b.shelf_id=s.id{extra} GROUP BY s.id ORDER BY s.name COLLATE NOCASE")
    return [dict(r) for r in db().execute(q)]


@app.post("/api/shelves")
def add_shelf(b: Named):
    with db() as c:
        cur = c.execute("INSERT INTO shelves(name) VALUES(?)", (b.name.strip(),))
    return {"id": cur.lastrowid}


@app.delete("/api/shelves/{sid}")
def del_shelf(sid: int):
    with db() as c:
        if c.execute("SELECT 1 FROM books WHERE shelf_id=?", (sid,)).fetchone():
            raise HTTPException(400, "Move or delete the books on this shelf first")
        c.execute("DELETE FROM shelves WHERE id=?", (sid,))
    return {"ok": True}


@app.get("/api/books")
def books(shelf_id: int | None = None, wishlist: int | None = None, q: str = "", x_unlock: str = Header(None)):
    sql, args = "SELECT b.*, s.name AS shelf FROM books b LEFT JOIN shelves s ON s.id=b.shelf_id WHERE 1=1", []
    if shelf_id is not None:
        sql += " AND b.shelf_id=?"
        args.append(shelf_id)
    if wishlist is not None:
        sql += " AND b.wishlist=?"
        args.append(wishlist)
    if q:
        sql += " AND (b.title LIKE ? OR b.authors LIKE ?)"
        args += [f"%{q}%"] * 2
    if not unlocked(x_unlock):
        sql += " AND b.restricted=0"
    return [dict(r) for r in db().execute(sql + " ORDER BY b.title COLLATE NOCASE", args)]


@app.post("/api/books")
def add_book(b: NewBook):
    with db() as c:
        cur = c.execute("INSERT INTO books(isbn,title,authors,cover,shelf_id,wishlist) VALUES(?,?,?,?,?,?)",
                        (b.isbn, b.title.strip(), b.authors, b.cover, b.shelf_id, b.wishlist))
    return {"id": cur.lastrowid}


@app.get("/api/books/{bid}")
def get_book(bid: int, x_unlock: str = Header(None)):
    return owned(bid, x_unlock)


@app.patch("/api/books/{bid}")
def patch_book(bid: int, body: dict, x_unlock: str = Header(None)):
    owned(bid, x_unlock)
    sets = {k: v for k, v in body.items() if k in FIELDS}
    if sets:
        with db() as c:
            c.execute(f"UPDATE books SET {', '.join(k + '=?' for k in sets)} WHERE id=?", [*sets.values(), bid])
    return {"ok": True}


@app.delete("/api/books/{bid}")
def del_book(bid: int, x_unlock: str = Header(None)):
    owned(bid, x_unlock)
    with db() as c:
        c.execute("DELETE FROM books WHERE id=?", (bid,))
    return {"ok": True}


@app.get("/api/lookup/{isbn}")
def lookup(isbn: str):
    isbn = "".join(ch for ch in isbn if ch.isdigit() or ch in "xX")
    try:
        url = f"https://openlibrary.org/api/books?bibkeys=ISBN:{isbn}&format=json&jscmd=data"
        with urllib.request.urlopen(url, timeout=8) as r:
            d = json.load(r).get(f"ISBN:{isbn}")
    except Exception:
        d = None
    if not d:
        raise HTTPException(404, "No match found")
    return {"isbn": isbn, "title": d.get("title", ""),
            "authors": ", ".join(a["name"] for a in d.get("authors", [])),
            "cover": d.get("cover", {}).get("medium", "")}
