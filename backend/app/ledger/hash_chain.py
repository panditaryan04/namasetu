"""Append-only hash chain over the audit_event table.

hash_n = SHA-256(canonical_json(fields of event n) + prev_hash); the first prev_hash is 64 zeros.
Appends are serialised (Postgres advisory lock; SQLite has a single writer) so the chain cannot fork.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.logging import request_id_var
from app.db.models import AuditEvent

GENESIS_HASH = "0" * 64
_LEDGER_LOCK_KEY = 0x4E414D41  # "NAMA"

HASHED_FIELDS = (
    "ts",
    "actor",
    "role",
    "action",
    "entity_type",
    "entity_id",
    "before",
    "after",
    "purpose_of_use",
    "request_id",
)


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def compute_hash(fields: dict[str, Any], prev_hash: str) -> str:
    payload = canonical_json({k: fields.get(k) for k in HASHED_FIELDS}) + prev_hash
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def append_event(
    session: Session,
    *,
    actor: str,
    role: str,
    action: str,
    entity_type: str,
    entity_id: str,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    purpose_of_use: str | None = None,
) -> AuditEvent:
    """Add a chained event to the session. The caller commits, so the event shares its transaction."""
    if session.get_bind().dialect.name == "postgresql":
        session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _LEDGER_LOCK_KEY})
    session.flush()
    last = session.scalars(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(1)).first()
    prev_hash = last.hash if last else GENESIS_HASH

    fields: dict[str, Any] = {
        "ts": datetime.now(UTC).isoformat(timespec="microseconds"),
        "actor": actor,
        "role": role,
        "action": action,
        "entity_type": entity_type,
        "entity_id": entity_id,
        # Round-trip through canonical JSON so what is hashed is exactly what the JSON column stores.
        "before": json.loads(canonical_json(before)) if before is not None else None,
        "after": json.loads(canonical_json(after)) if after is not None else None,
        "purpose_of_use": purpose_of_use,
        "request_id": request_id_var.get(),
    }
    event = AuditEvent(**fields, prev_hash=prev_hash, hash=compute_hash(fields, prev_hash))
    session.add(event)
    session.flush()
    return event
