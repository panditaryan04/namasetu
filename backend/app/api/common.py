"""Shared API models: pagination envelope."""

from __future__ import annotations

from typing import Annotated, Generic, TypeVar

from fastapi import Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.session import get_db

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


class PageParams(BaseModel):
    limit: int
    offset: int


def page_params(limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0)) -> PageParams:
    return PageParams(limit=limit, offset=offset)


DbSession = Annotated[Session, Depends(get_db)]
Pagination = Annotated[PageParams, Depends(page_params)]
