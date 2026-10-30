"""FHIR AuditEvent (a view over the hash-chained ledger) and stored Provenance resources."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import AuditEvent, FhirProvenance
from app.fhir.common import base_url, new_id, now_iso
from app.fhir.validator import validate

# Ledger action -> FHIR AuditEvent.action (C create, R read, U update, D delete, E execute).
ACTION_CODE = {
    "ingest": "C",
    "publish": "C",
    "approve": "U",
    "reject": "U",
    "edit": "U",
    "fhir_export": "C",
    "map": "E",
    "map_batch": "E",
    "search": "E",
    "bundle_ingest": "C",
    "crosswalk_import": "C",
}
ACT_REASON = "http://terminology.hl7.org/CodeSystem/v3-ActReason"
PROVENANCE_ACTIVITY = "http://terminology.hl7.org/CodeSystem/v3-DataOperation"
ACTIVITY_CODE = {"publish": "CREATE", "build": "CREATE", "approve": "UPDATE", "import": "CREATE"}


def audit_event_resource(ev: AuditEvent) -> dict[str, Any]:
    res: dict[str, Any] = {
        "resourceType": "AuditEvent",
        "id": str(ev.id),
        "type": {
            "system": "http://terminology.hl7.org/CodeSystem/audit-event-type",
            "code": "rest",
            "display": "RESTful Operation",
        },
        "subtype": [{"system": f"{base_url()}/CodeSystem/namasetu-audit-action", "code": ev.action}],
        "action": ACTION_CODE.get(ev.action, "E"),
        "recorded": ev.ts,
        "outcome": "0",
        "agent": [{"who": {"display": ev.actor}, "requestor": True, "role": [{"text": ev.role}]}],
        "source": {"site": "NamaSetu", "observer": {"display": "NamaSetu terminology service"}},
        "entity": [
            {
                "what": {"display": f"{ev.entity_type}/{ev.entity_id}"},
                "detail": [
                    {"type": "ledger-hash", "valueString": ev.hash},
                    {"type": "ledger-prev-hash", "valueString": ev.prev_hash},
                    {"type": "request-id", "valueString": ev.request_id},
                ],
            }
        ],
    }
    if ev.purpose_of_use:
        res["purposeOfEvent"] = [{"coding": [{"system": ACT_REASON, "code": ev.purpose_of_use}]}]
    return res


def list_audit_events(
    session: Session, count: int, offset: int, action: str | None
) -> tuple[list[AuditEvent], int]:
    q = select(AuditEvent)
    if action:
        q = q.where(AuditEvent.action == action)
    total = session.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = session.scalars(q.order_by(AuditEvent.id.desc()).limit(count).offset(offset)).all()
    return list(rows), total


def record_provenance(
    session: Session,
    *,
    target: str,
    activity: str,
    agent: str,
    entities: list[str],
    reason: str | None = None,
    reason_code: str | None = None,
    role: str = "author",
) -> dict[str, Any]:
    """Store a Provenance whose target is `target` (e.g. "Condition/abc") and return it."""
    recorded = now_iso()
    pid = new_id()
    target_ref: dict[str, Any] = (
        {"reference": target}
        if target.split("/")[0] in {"Condition", "ConceptMap", "Bundle"}
        else {
            "identifier": {
                "system": f"{base_url()}/{target.split('/')[0]}",
                "value": target.split("/", 1)[-1],
            },
            "display": target,
        }
    )
    res: dict[str, Any] = {
        "resourceType": "Provenance",
        "id": pid,
        "target": [target_ref],
        "recorded": recorded,
        "activity": {
            "coding": [{"system": PROVENANCE_ACTIVITY, "code": ACTIVITY_CODE.get(activity, "CREATE")}],
            "text": activity,
        },
        "agent": [
            {
                "type": {
                    "coding": [
                        {
                            "system": "http://terminology.hl7.org/CodeSystem/provenance-participant-type",
                            "code": role,
                        }
                    ]
                },
                "who": {"display": agent},
            }
        ],
        "entity": [{"role": "source", "what": {"display": e}} for e in entities],
    }
    if reason or reason_code:
        res["reason"] = [{"text": reason or reason_code}]
        if reason_code:
            res["reason"][0]["coding"] = [{"system": ACT_REASON, "code": reason_code}]
    validate(res)
    session.add(FhirProvenance(id=pid, target=target, recorded=recorded, content=res))
    return res


def list_provenance(
    session: Session, target: str | None, count: int, offset: int
) -> tuple[list[dict[str, Any]], int]:
    q = select(FhirProvenance)
    if target:
        q = q.where(FhirProvenance.target.startswith(target))
    total = session.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = session.scalars(q.order_by(FhirProvenance.recorded.desc()).limit(count).offset(offset)).all()
    return [r.content for r in rows], total
