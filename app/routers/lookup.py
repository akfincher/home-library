import json
import urllib.request

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api")


@router.get("/lookup/{isbn}")
def lookup(isbn: str):
    isbn = "".join(ch for ch in isbn if ch.isdigit() or ch in "xX")
    if not isbn:
        raise HTTPException(400, "Enter an ISBN")
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
