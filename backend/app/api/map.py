"""POST /map and /map/batch: ranked, explained, gated NAMASTE -> ICD-11 TM2 (+ biomedicine) candidates."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator

from app.api.common import DbSession
from app.api.search import Engine
from app.core.ratelimit import rate_limit
from app.core.rbac import Reader
from app.engine.ranker import RankerArtifact
from app.engine.service import MapResult
from app.ledger.hash_chain import append_event
from app.workflow.review import route_if_medium

router = APIRouter(tags=["map"])
Limited = Depends(rate_limit("map"))


class MapRequest(BaseModel):
    text: str | None = Field(None, min_length=1, max_length=200, description="Term in any script")
    code: str | None = Field(None, min_length=1, max_length=64, description="NAMASTE code")
    top_k: int = Field(5, ge=1, le=10)

    @model_validator(mode="after")
    def _one_of(self) -> MapRequest:
        if not self.text and not self.code:
            raise ValueError("provide 'text' or 'code'")
        return self


class MapResponse(MapResult):
    review_item_id: int | None = Field(None, description="Set when a MEDIUM result was routed to review")


class BatchRequest(BaseModel):
    items: list[MapRequest] = Field(min_length=1, max_length=100)


class BatchItem(BaseModel):
    request: MapRequest
    result: MapResult | None = None
    error: str | None = None


class BatchResponse(BaseModel):
    results: list[BatchItem]
    summary: dict[str, int]


def _summary(result: MapResult) -> dict[str, Any]:
    return {
        "normalised": result.query.normalised,
        "resolved": result.resolved.code if result.resolved else None,
        "status": result.status,
        "candidates": [
            {"tm2": c.tm2.code, "band": c.band, "confidence": c.confidence, "relationship": c.relationship}
            for c in result.candidates
        ],
        "model_version": result.model_version,
    }


def _run(engine: Engine, req: MapRequest) -> MapResult:
    try:
        return engine.map(text=req.text, code=req.code, top_k=req.top_k)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"NAMASTE code {req.code} not found") from exc


@router.post("/map", response_model=MapResponse, dependencies=[Limited])
def map_term(req: MapRequest, user: Reader, engine: Engine, db: DbSession) -> MapResponse:
    result = _run(engine, req)
    # Stage 6: a MEDIUM result for a resolved NAMASTE concept goes to the clinician review queue.
    item = route_if_medium(db, engine, result, user)
    append_event(
        db,
        actor=user.username,
        role=user.role,
        action="map",
        entity_type="MapQuery",
        entity_id=result.resolved.code if result.resolved else "free-text",
        after=_summary(result),
    )
    db.commit()
    return MapResponse(**result.model_dump(), review_item_id=item.id if item else None)


@router.post("/map/batch", response_model=BatchResponse, dependencies=[Limited])
def map_batch(req: BatchRequest, user: Reader, engine: Engine, db: DbSession) -> BatchResponse:
    items: list[BatchItem] = []
    for r in req.items:
        try:
            items.append(BatchItem(request=r, result=_run(engine, r)))
        except HTTPException as exc:
            items.append(BatchItem(request=r, error=str(exc.detail)))
    summary = {"total": len(items), "errors": sum(1 for i in items if i.error)}
    for status in ("suggested", "review", "no_match"):
        summary[status] = sum(1 for i in items if i.result and i.result.status == status)
    append_event(
        db,
        actor=user.username,
        role=user.role,
        action="map_batch",
        entity_type="MapQuery",
        entity_id=f"batch:{len(items)}",
        after=summary,
    )
    db.commit()
    return BatchResponse(results=items, summary=summary)


@router.get("/eval/ranker", response_model=RankerArtifact, tags=["eval"])
def ranker_artifact(_: Reader, engine: Engine) -> RankerArtifact:
    """Ranker weights, calibrated thresholds and the reliability (calibration) table."""
    return engine.ranker
