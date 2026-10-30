"""Safety invariants (every count must be 0) and in-process FHIR conformance counts.

Everything is computed by exercising the real builders against the current database. Builders that write
(Condition provenance, interaction alerts) run in a dry-run transaction that is rolled back afterwards.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from typing import Any

from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    SYSTEM_BIO,
    SYSTEM_NAMASTE,
    SYSTEM_TM2,
    ConceptLink,
    InteractionAlert,
    Mapping,
    MedicationLink,
    Patient,
)
from app.engine.gate import band
from app.engine.service import MappingEngine
from app.fhir.codesystem import all_codesystems
from app.fhir.common import FHIRError
from app.fhir.conceptmap_builder import CONCEPTMAPS, _content
from app.fhir.condition_builder import ConditionBuildRequest, PatientRef, build_condition
from app.fhir.validator import validation_issues
from app.fhir.valueset import VALUESETS, valueset
from app.medication.catalog import DISCLAIMER, MedicationCatalog
from app.medication.fhir_medication import medication_knowledge_resource, medication_resource
from app.medication.interactions import check, detected_issue

# Property names that would carry a dose regimen. "dosage_form" (tablet, churna) is a form, not a dose.
DOSE_FIELD = re.compile(
    r"(^|_)(dose|doses|dosing|regimen|frequency|posology)($|_)|^dosage$|dosageinstruction", re.I
)
EVAL_PATIENT = PatientRef(
    abha_number="91-0000-0000-0000", display="Evaluation (synthetic)", id="eval-dry-run"
)


@contextmanager
def dry_run(session: Session) -> Iterator[Session]:
    """Builders call session.commit(); turn that into flush and roll everything back at the end."""
    original = session.commit
    session.commit = session.flush  # type: ignore[method-assign]
    try:
        yield session
    finally:
        session.rollback()
        session.commit = original  # type: ignore[method-assign]


def _approved_bio_pairs(session: Session) -> set[tuple[str, str]]:
    rows = session.scalars(
        select(ConceptLink).where(ConceptLink.status == "approved", ConceptLink.target_system == SYSTEM_BIO)
    )
    return {(lk.source_code, lk.target_code) for lk in rows}


def _approved_mappings(session: Session) -> list[Mapping]:
    return list(
        session.scalars(
            select(Mapping)
            .where(
                Mapping.status == "approved",
                Mapping.needs_review.is_(False),
                Mapping.source_system == SYSTEM_NAMASTE,
                Mapping.target_system == SYSTEM_TM2,
            )
            .order_by(Mapping.source_code)
        )
    )


def _openapi_dose_fields(app: FastAPI) -> list[str]:
    found: list[str] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for k in node.get("properties", {}):
                if DOSE_FIELD.search(k):
                    found.append(f"{path}.{k}")
            for k, v in node.items():
                walk(v, f"{path}.{k}" if k != "properties" else path)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")

    walk(app.openapi().get("components", {}).get("schemas", {}), "schemas")
    for p, ops in app.openapi().get("paths", {}).items():
        for op in ops.values():
            for prm in op.get("parameters", []) if isinstance(op, dict) else []:
                if DOSE_FIELD.search(prm.get("name", "")):
                    found.append(f"{p}?{prm['name']}")
    return sorted(set(found))


def _resource_dose_keys(res: Any) -> int:
    if isinstance(res, dict):
        return sum(bool(DOSE_FIELD.search(k)) + _resource_dose_keys(v) for k, v in res.items())
    if isinstance(res, list):
        return sum(_resource_dose_keys(v) for v in res)
    return 0


def safety_checks(
    session: Session, engine: MappingEngine, catalog: MedicationCatalog, app: FastAPI
) -> dict[str, Any]:
    idx = engine.index
    approved_bio = _approved_bio_pairs(session)
    checks: Counter[str] = Counter()
    examples: dict[str, list[str]] = {}

    def fail(name: str, example: str) -> None:
        checks[name] += 1
        examples.setdefault(name, [])
        if len(examples[name]) < 5:
            examples[name].append(example)

    # 1. Every code the mapper emits, over the whole NAMASTE dataset, exists in an ingested dataset;
    #    biomedical links shown as "approved" really are approved.
    mapped = 0
    for rec in idx.namaste:
        if rec.is_category:
            continue
        mapped += 1
        for c in engine.map(code=rec.code, top_k=10).candidates:
            if not idx.is_valid(SYSTEM_TM2, c.tm2.code):
                fail("codes_outside_dataset", f"{rec.code}->{c.tm2.code}")
            b = c.biomedicine
            if b.code is not None and not idx.is_valid(SYSTEM_BIO, b.code):
                fail("codes_outside_dataset", f"{c.tm2.code}->{b.code}")
            if b.code and b.status == "approved" and (c.tm2.code, b.code) not in approved_bio:
                fail("unapproved_link_shown_as_approved", f"{c.tm2.code}->{b.code}")
            if b.code and b.relationship != "relatedto" and b.source != "clinician":
                fail("biomedical_link_not_relatedto", f"{c.tm2.code}->{b.code}")

    # 2. ConceptMaps contain only approved, non-review mappings / approved biomedical links.
    approved_pairs = {(m.source_code, m.target_code) for m in _approved_mappings(session)}
    conceptmaps = {cm_id: _content(session, engine, cm_id) for cm_id in CONCEPTMAPS}
    for cm_id, cm in conceptmaps.items():
        allowed = approved_pairs if "namaste" in cm_id else approved_bio
        for grp in cm.get("group", []):
            for el in grp["element"]:
                for t in el["target"]:
                    if (el["code"], t["code"]) not in allowed:
                        fail("low_or_unapproved_in_conceptmap", f"{cm_id}:{el['code']}->{t['code']}")

    # 3. Conditions: built for every approved mapping (dry run); biomedical codings only from approved links;
    #    a MEDIUM-band suggestion must be refused even when the caller claims confirmation.
    conditions: list[dict[str, Any]] = []
    with dry_run(session):
        for m in _approved_mappings(session):
            cond = build_condition(
                session,
                engine,
                ConditionBuildRequest(namaste_code=m.source_code, patient=EVAL_PATIENT),
                "eval",
            )
            conditions.append(cond)
            codings = cond["code"]["coding"]
            tm2 = next(cd["code"] for cd in codings if idx.is_valid(SYSTEM_TM2, cd["code"]))
            for cd in codings:
                if idx.is_valid(SYSTEM_BIO, cd["code"]) and (tm2, cd["code"]) not in approved_bio:
                    fail("unapproved_bio_link_in_condition", f"{m.source_code}:{cd['code']}")
        # Probe the top-5 ranked candidates of every concept that are NOT high band (medium and low) and
        # are not the approved target: each must be refused even with confirm_suggestion=true.
        approved_target = {m.source_code: m.target_code for m in _approved_mappings(session)}
        attempts = 0
        for rec in idx.namaste:
            if rec.is_category:
                continue
            for cand in engine._rank(engine.matcher.profile_for_concept(rec))[:5]:
                cand_band = band(cand.confidence, engine.thresholds)
                if cand_band == "high" or approved_target.get(rec.code) == cand.target.code:
                    continue
                attempts += 1
                req = ConditionBuildRequest(
                    namaste_code=rec.code,
                    tm2_code=cand.target.code,
                    confirm_suggestion=True,
                    patient=EVAL_PATIENT,
                )
                try:
                    build_condition(session, engine, req, "eval")
                    fail(
                        "below_high_band_emitted_without_review",
                        f"{rec.code}->{cand.target.code} ({cand_band})",
                    )
                except FHIRError:
                    pass

    # 4. Medications: cited, disclaimed, no dose regimen anywhere (API schema or FHIR resource).
    med_resources: list[dict[str, Any]] = []
    for med in catalog.by_code.values():
        v = med.view
        if not v.active:
            continue
        if not v.source.title.strip():
            fail("medication_without_citation", v.code)
        if v.disclaimer != DISCLAIMER:
            fail("medication_without_disclaimer", v.code)
        med_resources.append(medication_resource(v))
        med_resources.append(medication_knowledge_resource(v, [], idx.is_sample))
    for r in med_resources:
        if _resource_dose_keys(r):
            fail("dose_fields_in_fhir_medication", f"{r['resourceType']}/{r['id']}")
    for f in _openapi_dose_fields(app):
        fail("dose_fields_in_openapi", f)
    for lk in session.scalars(select(MedicationLink).where(MedicationLink.status == "approved")):
        if not lk.source_title.strip():
            fail("medication_link_without_citation", str(lk.id))
        if lk.reviewer is None and not lk.is_sample:
            fail("medication_link_not_human_approved", str(lk.id))

    # 5. Interaction alerts: cited, with severity + evidence; "no interaction" always caveated; stopped
    #    medicines never alert (checked by the labelled cases in eval.medication).
    #    DetectedIssue resources are built in a dry run.
    detected: list[dict[str, Any]] = []
    with dry_run(session):
        patients = list(session.scalars(select(Patient).order_by(Patient.id)))
        for p in patients:
            for code in list(catalog.by_code)[:40]:
                out = check(session, catalog, [code], patient=p, as_of=date.today(), persist=True)
                for a in out.alerts:
                    if not a.citation.strip():
                        fail("alert_without_citation", f"{p.id}+{code}")
                    if not a.severity:
                        fail("alert_without_severity", f"{p.id}+{code}")
                if not out.alerts and "not proof of safety" not in out.message:
                    fail("no_interaction_without_caveat", f"{p.id}+{code}")
        session.flush()
        detected = [detected_issue(a) for a in session.scalars(select(InteractionAlert))]

    # FHIR conformance (in-process structural + terminology-binding validation).
    resources: list[dict[str, Any]] = [
        *all_codesystems(idx),
        *(valueset(engine, vs) for vs in VALUESETS),
        *conceptmaps.values(),
        *conditions,
        *med_resources,
        *detected,
    ]
    conformance: dict[str, dict[str, int]] = {}
    for r in resources:
        row = conformance.setdefault(r["resourceType"], {"resources": 0, "errors": 0, "warnings": 0})
        row["resources"] += 1
        for i in validation_issues(r):
            if i["severity"] in {"error", "fatal"}:
                row["errors"] += 1
                fail(
                    "fhir_conformance_errors", f"{r['resourceType']}/{r.get('id')}: {i['diagnostics'][:120]}"
                )
            elif i["severity"] == "warning":
                row["warnings"] += 1

    names = [
        "codes_outside_dataset",
        "low_or_unapproved_in_conceptmap",
        "below_high_band_emitted_without_review",
        "unapproved_bio_link_in_condition",
        "unapproved_link_shown_as_approved",
        "biomedical_link_not_relatedto",
        "medication_without_citation",
        "medication_without_disclaimer",
        "medication_link_without_citation",
        "medication_link_not_human_approved",
        "dose_fields_in_openapi",
        "dose_fields_in_fhir_medication",
        "alert_without_citation",
        "alert_without_severity",
        "no_interaction_without_caveat",
        "fhir_conformance_errors",
    ]
    return {
        "checks": {n: checks.get(n, 0) for n in names},
        "ok": all(checks.get(n, 0) == 0 for n in names),
        "examples": examples,
        "scope": {
            "namaste_concepts_mapped": mapped,
            "approved_mappings": len(approved_pairs),
            "conditions_built": len(conditions),
            "below_high_attempts": attempts,
            "medications": len(med_resources) // 2,
            "interaction_checks": len(patients) * min(40, len(catalog.by_code)),
        },
        "fhir_conformance": conformance,
    }
