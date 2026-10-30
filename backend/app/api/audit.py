"""Audit ledger endpoints. Read-only; role checks are added with RBAC in Milestone 5."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import func, select

from app.api.common import DbSession, Page, Pagination
from app.core.rbac import Reader
from app.db.models import AuditEvent
from app.ledger.verify import ChainVerification, verify_chain

router = APIRouter(prefix="/audit", tags=["audit"])


class AuditEventOut(BaseModel):
    id: int
    ts: str
    actor: str
    role: str
    action: str
    entity_type: str
    entity_id: str
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    purpose_of_use: str | None
    request_id: str
    prev_hash: str
    hash: str


@router.get("", response_model=Page[AuditEventOut])
def list_events(_: Reader, page: Pagination, db: DbSession, action: str | None = None) -> Page[AuditEventOut]:
    query = select(AuditEvent)
    if action:
        query = query.where(AuditEvent.action == action)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(query.order_by(AuditEvent.id.desc()).limit(page.limit).offset(page.offset)).all()
    items = [AuditEventOut.model_validate(r, from_attributes=True) for r in rows]
    return Page(items=items, total=total, limit=page.limit, offset=page.offset)


@router.get("/verify", response_model=ChainVerification)
def verify(_: Reader, db: DbSession) -> ChainVerification:
    return verify_chain(db)
