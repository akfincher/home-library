# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Aaron Fincher. See LICENSE.
from typing import Literal

from pydantic import BaseModel, Field

# "custom" is the order the books were arranged in by hand, to match the real shelf.
SortName = Literal["title", "author", "series", "added", "custom"]
RuleName = Literal["genre", "author", "series", "level", "person"]


class ShelfSort(BaseModel):
    sort: SortName


class ShelfOrder(BaseModel):
    book_ids: list[int] = Field(max_length=5000)


class OrganizeIn(BaseModel):
    rule: RuleName
    person_id: int | None = None  # only that person's books; None means every book on a shelf
    min_size: int = Field(3, ge=1, le=50)  # smaller groups are put together on an "Other" shelf


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
    subtitle: str = Field("", max_length=500)
    description: str = Field("", max_length=20000)
    publisher: str = Field("", max_length=300)
    published: str = Field("", max_length=50)
    pages: int | None = Field(None, ge=1, le=100000)
    subjects: str = Field("", max_length=1000)
    series: str = Field("", max_length=300)
    series_index: float | None = Field(None, ge=0, le=10000)
    language: str = Field("", max_length=20)


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
    subtitle: str | None = Field(None, max_length=500)
    description: str | None = Field(None, max_length=20000)
    publisher: str | None = Field(None, max_length=300)
    published: str | None = Field(None, max_length=50)
    pages: int | None = Field(None, ge=1, le=100000)
    subjects: str | None = Field(None, max_length=1000)
    series: str | None = Field(None, max_length=300)
    series_index: float | None = Field(None, ge=0, le=10000)
    language: str | None = Field(None, max_length=20)
