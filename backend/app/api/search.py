"""Autocomplete across English / IAST / Devanagari / Hinglish (script auto-detected)."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.common import DbSession
from app.core.ratelimit import rate_limit
from app.core.rbac import Reader
from app.engine.explain import QueryInfo
from app.engine.service import MappingEngine, Suggestion, engine_holder

router = APIRouter(prefix="/search", tags=["search"])


def get_engine(db: DbSession) -> MappingEngine:
    try:
        return engine_holder.get(db)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"mapping engine unavailable: {exc}") from exc


Engine = Annotated[MappingEngine, Depends(get_engine)]


class AutocompleteResponse(BaseModel):
    query: QueryInfo
    items: list[Suggestion]


@router.get(
    "/autocomplete", response_model=AutocompleteResponse, dependencies=[Depends(rate_limit("search"))]
)
def autocomplete(
    _: Reader,
    engine: Engine,
    q: Annotated[str, Query(min_length=1, max_length=200)],
    limit: Annotated[int, Query(ge=1, le=25)] = 10,
    script: Annotated[
        Literal["auto", "english", "iast", "devanagari", "hinglish"] | None,
        Query(description="Optional hint; the script is detected from the text either way."),
    ] = None,
) -> AutocompleteResponse:
    surface = engine.index.make_surface(q)
    return AutocompleteResponse(
        query=QueryInfo(text=q, normalised=surface.norm.key, script=surface.norm.script),
        items=engine.suggest(q, limit=limit),
    )


__all__ = ["Engine", "Session", "get_engine", "router"]
