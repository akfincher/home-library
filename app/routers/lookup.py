from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import db, metadata
from ..security import require_admin

router = APIRouter(prefix="/api")


@router.get("/lookup/{isbn}")
def lookup(isbn: str):
    clean = metadata.clean_isbn(isbn)
    if not clean:
        raise HTTPException(400, "That doesn't look like an ISBN (10 or 13 digits).")
    found = metadata.lookup(clean)
    if not found:
        raise HTTPException(404, "No match found")
    return found


@router.get("/lookup")
def search(q: str = ""):
    q = q.strip()
    if len(q) < 2:
        raise HTTPException(400, "Type a title or author to search for.")
    return {"results": metadata.search(q)}


@router.get("/sources")
def sources():
    return metadata.status()


class Key(BaseModel):
    key: str = Field("", max_length=200)


@router.put("/sources/{source}", dependencies=[Depends(require_admin)])
def set_key(source: str, b: Key):
    """Checks the key with the source before saving it. An empty key removes it."""
    if source not in metadata.KEYED:
        raise HTTPException(404, "That source doesn't take a key")
    setting = metadata.KEYED[source].setting
    key = b.key.strip()
    if not key:
        db.delete_setting(setting)
        return {"ok": True}
    try:
        metadata.check_key(source, key)
    except metadata.KeyRejected as e:
        raise HTTPException(400, str(e))
    except ConnectionError as e:
        raise HTTPException(502, str(e))
    db.put_setting(setting, key)
    return {"ok": True}
