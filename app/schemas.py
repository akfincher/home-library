from pydantic import BaseModel, Field


class Named(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class ShelfIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    person_id: int | None = None


class Pin(BaseModel):
    pin: str = Field(max_length=64)
    current: str = Field("", max_length=64)


class BookIn(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    authors: str = Field("", max_length=500)
    isbn: str = Field("", max_length=20)
    cover: str = Field("", max_length=1000)
    shelf_id: int | None = None
    person_id: int | None = None
    wishlist: bool = False


class BookPatch(BaseModel):
    """Every field is optional; only the ones sent are changed."""
    title: str | None = Field(None, min_length=1, max_length=500)
    authors: str | None = Field(None, max_length=500)
    isbn: str | None = Field(None, max_length=20)
    cover: str | None = Field(None, max_length=1000)
    shelf_id: int | None = None
    person_id: int | None = None
    wishlist: bool | None = None
    restricted: bool | None = None
    rating: int | None = Field(None, ge=0, le=5)
    spicy: int | None = Field(None, ge=0, le=5)
    review: str | None = Field(None, max_length=20000)
    ar_level: float | None = Field(None, ge=0, le=20)
    ar_points: float | None = Field(None, ge=0, le=500)
