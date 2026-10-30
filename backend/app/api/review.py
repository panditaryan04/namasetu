"""Clinician review queue, Approved Mapping Store, provenance timelines."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.common import DbSession, Page, Pagination
from app.api.search import Engine
from app.core.rbac import Proposer, Reader, Reviewer
from app.db.models import AuditEvent, FhirProvenance, Mapping, ReviewItem
from app.workflow.review import (
    BioLinkRequest,
    DecisionRequest,
    WorkflowError,
    add_bio_link,
    decide,
    enqueue,
)

router = APIRouter(tags=["review"])


class ReviewSummary(BaseModel):
    id: int
    status: str
    kind: str
    source_code: str
    source_display: str | None
    discipline: str | None
    band: str
    top_confidence: float
    top_target: str | None
    top_target_display: str | None
    reason: str
    created_by: str
    created_at: datetime
    decided_by: str | None
    decided_at: datetime | None


class ReviewDetail(ReviewSummary):
    query_text: str | None
    proposed_target: str | None
    candidates: list[dict[str, Any]]
    dataset_releases: list[Any]
    ranker_version: str | None
    decision: dict[str, Any] | None
    comment: str | None
    mapping_id: int | None
    current_mapping: dict[str, Any] | None


class ProposeRequest(BaseModel):
    namaste_code: str = Field(min_length=1, max_length=64)
    tm2_code: str | None = Field(None, max_length=64)
    query_text: str | None = Field(None, max_length=200)
    comment: str | None = Field(None, max_length=2000)


class MappingOut(BaseModel):
    id: int
    source_code: str
    source_display: str | None
    target_code: str
    target_display: str | None
    relationship: str
    status: str
    version: int
    reviewer: str | None
    ranker_version: str | None
    dataset_releases: list[Any]
    needs_review: bool
    review_reason: str | None
    created_at: datetime


class TimelineEntry(BaseModel):
    ts: str
    kind: Literal["provenance", "audit"]
    action: str
    actor: str
    detail: str


class MappingDetail(MappingOut):
    history: list[MappingOut]
    timeline: list[TimelineEntry]


def _display(engine: Engine, system: str, code: str | None) -> str | None:
    rec = engine.index.get(system, code) if code else None
    return rec.display if rec else None


def _summary(engine: Engine, item: ReviewItem) -> dict[str, Any]:
    top = item.candidates[0]["tm2"] if item.candidates else {}
    return {
        "id": item.id,
        "status": item.status,
        "kind": item.kind,
        "source_code": item.source_code,
        "source_display": _display(engine, "namaste", item.source_code),
        "discipline": item.discipline,
        "band": item.band,
        "top_confidence": item.top_confidence,
        "top_target": top.get("code"),
        "top_target_display": top.get("display"),
        "reason": item.reason,
        "created_by": item.created_by,
        "created_at": item.created_at,
        "decided_by": item.decided_by,
        "decided_at": item.decided_at,
    }


def _mapping_out(engine: Engine, m: Mapping) -> MappingOut:
    return MappingOut(
        id=m.id,
        source_code=m.source_code,
        source_display=_display(engine, "namaste", m.source_code),
        target_code=m.target_code,
        target_display=_display(engine, "icd11-tm2", m.target_code),
        relationship=m.relationship,
        status=m.status,
        version=m.version,
        reviewer=m.reviewer,
        ranker_version=m.ranker_version,
        dataset_releases=m.dataset_releases,
        needs_review=m.needs_review,
        review_reason=m.review_reason,
        created_at=m.created_at,
    )


def _raise(exc: WorkflowError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.message)


# ------------------------------------------------------------------ queue


@router.get("/review/queue", response_model=Page[ReviewSummary])
def queue(
    _: Reader,
    engine: Engine,
    db: DbSession,
    page: Pagination,
    status: Literal["pending", "approved", "rejected", "all"] = "pending",
    band: Literal["high", "medium", "low"] | None = None,
    discipline: str | None = Query(None, max_length=32),
    release: str | None = Query(
        None, max_length=64, description="dataset release tag, e.g. namaste@SAMPLE-1"
    ),
) -> Page[ReviewSummary]:
    q = select(ReviewItem)
    if status != "all":
        q = q.where(ReviewItem.status == status)
    if band:
        q = q.where(ReviewItem.band == band)
    if discipline:
        q = q.where(ReviewItem.discipline == discipline)
    rows = db.scalars(
        q.order_by(ReviewItem.band.desc(), ReviewItem.top_confidence.asc(), ReviewItem.id)
    ).all()
    if release:
        rows = [r for r in rows if release in (r.dataset_releases or [])]
    items = [ReviewSummary(**_summary(engine, r)) for r in rows[page.offset : page.offset + page.limit]]
    return Page(items=items, total=len(rows), limit=page.limit, offset=page.offset)


@router.get("/review/{item_id}", response_model=ReviewDetail)
def review_detail(item_id: int, _: Reader, engine: Engine, db: DbSession) -> ReviewDetail:
    item = db.get(ReviewItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"review item {item_id} not found")
    current = db.scalars(
        select(Mapping).where(Mapping.source_code == item.source_code, Mapping.status == "approved").limit(1)
    ).first()
    return ReviewDetail(
        **_summary(engine, item),
        query_text=item.query_text,
        proposed_target=item.proposed_target,
        candidates=item.candidates,
        dataset_releases=item.dataset_releases,
        ranker_version=item.ranker_version,
        decision=item.decision,
        comment=item.comment,
        mapping_id=item.mapping_id,
        current_mapping=_mapping_out(engine, current).model_dump(mode="json") if current else None,
    )


@router.post("/review/propose", response_model=ReviewDetail, status_code=201)
def propose(req: ProposeRequest, user: Proposer, engine: Engine, db: DbSession) -> ReviewDetail:
    try:
        item = enqueue(
            db,
            engine,
            namaste_code=req.namaste_code,
            reason="proposed",
            actor=user,
            query_text=req.query_text,
            proposed_target=req.tm2_code,
        )
    except WorkflowError as exc:
        raise _raise(exc) from exc
    if req.comment and not item.comment:
        item.comment = req.comment
    db.commit()
    return review_detail(item.id, user, engine, db)


@router.post("/review/{item_id}/decision", response_model=ReviewDetail)
def decision(
    item_id: int, req: DecisionRequest, user: Reviewer, engine: Engine, db: DbSession
) -> ReviewDetail:
    try:
        decide(db, engine, item_id, req, user)
    except WorkflowError as exc:
        db.rollback()
        raise _raise(exc) from exc
    db.commit()
    return review_detail(item_id, user, engine, db)


# ------------------------------------------------------------------ Approved Mapping Store


@router.get("/mappings", response_model=Page[MappingOut])
def mappings(
    _: Reader,
    engine: Engine,
    db: DbSession,
    page: Pagination,
    source_code: str | None = Query(None, max_length=64),
    status: Literal["approved", "superseded", "all"] = "approved",
    needs_review: bool | None = None,
) -> Page[MappingOut]:
    q = select(Mapping)
    if status != "all":
        q = q.where(Mapping.status == status)
    if source_code:
        q = q.where(Mapping.source_code == source_code)
    if needs_review is not None:
        q = q.where(Mapping.needs_review.is_(needs_review))
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = db.scalars(
        q.order_by(Mapping.source_code, Mapping.version.desc()).limit(page.limit).offset(page.offset)
    )
    return Page(
        items=[_mapping_out(engine, m) for m in rows], total=total, limit=page.limit, offset=page.offset
    )


@router.get("/mappings/{mapping_id}", response_model=MappingDetail)
def mapping_detail(mapping_id: int, _: Reader, engine: Engine, db: DbSession) -> MappingDetail:
    m = db.get(Mapping, mapping_id)
    if m is None:
        raise HTTPException(status_code=404, detail=f"mapping {mapping_id} not found")
    history = db.scalars(
        select(Mapping).where(Mapping.source_code == m.source_code).order_by(Mapping.version.desc())
    ).all()
    ids = [str(h.id) for h in history]
    timeline: list[TimelineEntry] = []
    for p in db.scalars(
        select(FhirProvenance).where(FhirProvenance.target.in_([f"Mapping/{i}" for i in ids]))
    ):
        c = p.content
        timeline.append(
            TimelineEntry(
                ts=p.recorded,
                kind="provenance",
                action=c.get("activity", {}).get("text", ""),
                actor=c["agent"][0]["who"].get("display", ""),
                detail=f"{p.target}: {(c.get('reason') or [{}])[0].get('text', '')}",
            )
        )
    for ev in db.scalars(
        select(AuditEvent).where(AuditEvent.entity_type == "Mapping", AuditEvent.entity_id.in_(ids))
    ):
        timeline.append(
            TimelineEntry(
                ts=ev.ts,
                kind="audit",
                action=ev.action,
                actor=f"{ev.actor} ({ev.role})",
                detail=f"ledger #{ev.id} · hash {ev.hash[:12]}…",
            )
        )
    timeline.sort(key=lambda t: t.ts)
    out = _mapping_out(engine, m).model_dump()
    return MappingDetail(**out, history=[_mapping_out(engine, h) for h in history], timeline=timeline)


@router.post("/mappings/bio-links", status_code=201)
def bio_link(req: BioLinkRequest, user: Reviewer, engine: Engine, db: DbSession) -> dict[str, Any]:
    try:
        link = add_bio_link(db, engine, req, user)
    except WorkflowError as exc:
        db.rollback()
        raise _raise(exc) from exc
    db.commit()
    return {
        "id": link.id,
        "tm2_code": link.source_code,
        "bio_code": link.target_code,
        "relationship": link.relationship,
        "origin": link.origin,
        "status": link.status,
    }
