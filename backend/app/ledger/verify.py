"""Recompute the audit hash chain and report the first broken link."""

from __future__ import annotations

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AuditEvent
from app.ledger.hash_chain import GENESIS_HASH, HASHED_FIELDS, compute_hash


class ChainVerification(BaseModel):
    ok: bool
    checked: int
    first_broken_id: int | None = None
    reason: str | None = None
    head_hash: str | None = None


def verify_chain(session: Session, batch_size: int = 1000) -> ChainVerification:
    prev_hash = GENESIS_HASH
    checked = 0
    last_id = 0
    while True:
        rows = session.scalars(
            select(AuditEvent).where(AuditEvent.id > last_id).order_by(AuditEvent.id).limit(batch_size)
        ).all()
        if not rows:
            break
        for ev in rows:
            if ev.prev_hash != prev_hash:
                return ChainVerification(
                    ok=False, checked=checked, first_broken_id=ev.id, reason="prev_hash does not match"
                )
            fields = {name: getattr(ev, name) for name in HASHED_FIELDS}
            if compute_hash(fields, ev.prev_hash) != ev.hash:
                return ChainVerification(
                    ok=False, checked=checked, first_broken_id=ev.id, reason="content hash mismatch"
                )
            prev_hash = ev.hash
            checked += 1
            last_id = ev.id
    return ChainVerification(ok=True, checked=checked, head_hash=None if checked == 0 else prev_hash)
