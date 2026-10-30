"""ConceptMaps built ONLY from the Approved Mapping Store (NAMASTE -> TM2) and approved TM2 -> biomedicine
links. Every content change is published as a new immutable version with a Provenance record."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    SYSTEM_BIO,
    SYSTEM_NAMASTE,
    SYSTEM_TM2,
    ConceptLink,
    FhirPublication,
    Mapping,
)
from app.engine.service import MappingEngine
from app.fhir.audit_provenance import record_provenance
from app.fhir.common import (
    CONCEPTMAP_NAMASTE_TM2,
    CONCEPTMAP_TM2_BIO,
    PUBLISHER,
    FHIRError,
    canonical,
    now_iso,
    parameters,
    system_uri,
)
from app.fhir.validator import validate
from app.ledger.hash_chain import append_event

CONCEPTMAPS = {
    CONCEPTMAP_NAMASTE_TM2: (
        "NAMASTE to ICD-11 TM2",
        SYSTEM_NAMASTE,
        SYSTEM_TM2,
        "namaste-ayurveda",
        "icd11-tm2",
    ),
    CONCEPTMAP_TM2_BIO: ("ICD-11 TM2 to ICD-11 biomedicine", SYSTEM_TM2, SYSTEM_BIO, "icd11-tm2", None),
}


def _namaste_elements(session: Session, engine: MappingEngine) -> list[dict[str, Any]]:
    rows = session.scalars(
        select(Mapping)
        .where(
            Mapping.status == "approved",
            Mapping.needs_review.is_(False),
            Mapping.source_system == SYSTEM_NAMASTE,
            Mapping.target_system == SYSTEM_TM2,
        )
        .order_by(Mapping.source_code, Mapping.target_code)
    ).all()
    elements: dict[str, dict[str, Any]] = {}
    for m in rows:
        src = engine.index.get(SYSTEM_NAMASTE, m.source_code)
        tgt = engine.index.get(SYSTEM_TM2, m.target_code)
        if src is None or tgt is None:  # never publish a code outside the dataset
            continue
        el = elements.setdefault(src.code, {"code": src.code, "display": src.display, "target": []})
        el["target"].append(
            {
                "code": tgt.code,
                "display": tgt.display,
                "equivalence": m.relationship,
                "comment": f"Approved by {m.reviewer or 'unknown'} (mapping {m.id} v{m.version})",
            }
        )
    return list(elements.values())


def _bio_elements(session: Session, engine: MappingEngine) -> list[dict[str, Any]]:
    rows = session.scalars(
        select(ConceptLink)
        .where(ConceptLink.status == "approved", ConceptLink.target_system == SYSTEM_BIO)
        .order_by(ConceptLink.source_code, ConceptLink.target_code)
    ).all()
    elements: dict[str, dict[str, Any]] = {}
    for lk in rows:
        src = engine.index.get(SYSTEM_TM2, lk.source_code)
        tgt = engine.index.get(SYSTEM_BIO, lk.target_code)
        if src is None or tgt is None:
            continue
        el = elements.setdefault(src.code, {"code": src.code, "display": src.display, "target": []})
        el["target"].append(
            {
                "code": tgt.code,
                "display": tgt.display,
                "equivalence": "relatedto",
                "comment": f"Source: {lk.origin}. Supports claim workflows; not a diagnosis equivalence.",
            }
        )
    return list(elements.values())


def _content(session: Session, engine: MappingEngine, cm_id: str) -> dict[str, Any]:
    title, src_sys, tgt_sys, src_vs, tgt_vs = CONCEPTMAPS[cm_id]
    sample = engine.index.is_sample
    elements = (
        _namaste_elements(session, engine)
        if cm_id == CONCEPTMAP_NAMASTE_TM2
        else _bio_elements(session, engine)
    )
    cm: dict[str, Any] = {
        "resourceType": "ConceptMap",
        "id": cm_id,
        "url": canonical("ConceptMap", cm_id),
        "name": "".join(w.capitalize() for w in cm_id.split("-")),
        "title": title + (" (SAMPLE)" if sample else ""),
        "status": "active",
        "experimental": sample,
        "publisher": PUBLISHER,
        "description": "Built only from clinician-approved / official mappings. "
        + ("SAMPLE — illustrative, not official." if sample else ""),
        "sourceCanonical": canonical("ValueSet", src_vs),
        "group": (
            [
                {
                    "source": system_uri(src_sys, sample),
                    "target": system_uri(tgt_sys, sample),
                    "element": elements,
                }
            ]
            if elements
            else []
        ),
    }
    if tgt_vs:
        cm["targetCanonical"] = canonical("ValueSet", tgt_vs)
    if not elements:
        cm.pop("group")
    return cm


def _checksum(cm: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(cm, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def latest_publication(session: Session, cm_id: str) -> FhirPublication | None:
    return session.scalars(
        select(FhirPublication)
        .where(FhirPublication.resource_type == "ConceptMap", FhirPublication.logical_id == cm_id)
        .order_by(FhirPublication.version.desc())
        .limit(1)
    ).first()


def get_conceptmap(
    session: Session, engine: MappingEngine, cm_id: str, actor: str = "system"
) -> dict[str, Any]:
    """Current published version; publishes a new version (+ Provenance, ledger) when the content changed."""
    if cm_id not in CONCEPTMAPS:
        raise FHIRError(404, f"ConceptMap/{cm_id} not found", "not-found")
    content = _content(session, engine, cm_id)
    checksum = _checksum(content)
    latest = latest_publication(session, cm_id)
    if latest is not None and latest.checksum == checksum:
        return latest.content
    version = (latest.version if latest else 0) + 1
    recorded = now_iso()
    resource = {
        **content,
        "version": str(version),
        "date": recorded,
        "meta": {"versionId": str(version), "lastUpdated": recorded},
    }
    validate(resource)
    session.add(
        FhirPublication(
            resource_type="ConceptMap",
            logical_id=cm_id,
            version=version,
            checksum=checksum,
            content=resource,
            published_by=actor,
        )
    )
    elements = sum(len(g["element"]) for g in resource.get("group", []))
    record_provenance(
        session,
        target=f"ConceptMap/{cm_id}/_history/{version}",
        activity="publish",
        agent=actor,
        entities=[f"{t}" for t in engine.index.releases],
        reason=f"Content changed: {elements} source codes",
    )
    append_event(
        session,
        actor=actor,
        role="system",
        action="publish",
        entity_type="ConceptMap",
        entity_id=f"{cm_id}|{version}",
        after={"elements": elements, "checksum": checksum},
    )
    session.commit()
    return resource


def history(session: Session, cm_id: str) -> list[dict[str, Any]]:
    rows = session.scalars(
        select(FhirPublication)
        .where(FhirPublication.resource_type == "ConceptMap", FhirPublication.logical_id == cm_id)
        .order_by(FhirPublication.version.desc())
    ).all()
    return [r.content for r in rows]


def translate(
    session: Session,
    engine: MappingEngine,
    *,
    system: str | None,
    code: str | None,
    target: str | None = None,
) -> dict[str, Any]:
    if not system or not code:
        raise FHIRError(400, "$translate requires 'system' and 'code'", "required")
    sample = engine.index.is_sample
    matches: list[dict[str, Any]] = []
    for cm_id, (_, src_sys, tgt_sys, _, _) in CONCEPTMAPS.items():
        if system_uri(src_sys, sample) != system:
            continue
        target_url = system_uri(tgt_sys, sample)
        if target and target not in {target_url, canonical("ValueSet", CONCEPTMAPS[cm_id][4] or "")}:
            continue
        cm = get_conceptmap(session, engine, cm_id)
        for g in cm.get("group", []):
            for el in g["element"]:
                if el["code"] != code:
                    continue
                for t in el["target"]:
                    matches.append(
                        {
                            "name": "match",
                            "part": [
                                {"name": "equivalence", "valueCode": t["equivalence"]},
                                {
                                    "name": "concept",
                                    "valueCoding": {
                                        "system": g["target"],
                                        "code": t["code"],
                                        "display": t["display"],
                                    },
                                },
                                {"name": "source", "valueUri": f"{cm['url']}|{cm['version']}"},
                            ],
                        }
                    )
    if matches:
        return parameters({"name": "result", "valueBoolean": True}, *matches)
    return parameters(
        {"name": "result", "valueBoolean": False},
        {"name": "message", "valueString": f"No approved mapping for {code}. Use POST /map for suggestions."},
    )
