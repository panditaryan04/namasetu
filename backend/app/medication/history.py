"""Patient medication history: import (FHIR Bundle or free text) and deterministic resolution.

Resolution order for a free-text line: brand -> product / formulation name -> salt or herb name ->
"Unrecognised — please verify". Dosage-form words, strengths and frequency codes are stripped for matching
only; the raw line is stored (for the clinician) but never logged, and nothing is ever stored or shown as
dosing advice.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Patient, PatientMedication
from app.engine.transliterator import fold
from app.fhir.validator import validation_issues
from app.ledger.hash_chain import append_event
from app.medication.brand_resolver import clean_line, resolve_brand
from app.medication.catalog import MedicationCatalog
from app.medication.constituents import db_dictionary

UNRESOLVED = "Unrecognised — please verify"
STATUS_MAP = {
    "active": "active",
    "intended": "unknown",
    "on-hold": "unknown",
    "unknown": "unknown",
    "completed": "stopped",
    "stopped": "stopped",
    "not-taken": "stopped",
    "entered-in-error": "stopped",
    "cancelled": "stopped",
    "draft": "unknown",
}
_UNIT_TOKENS = {"mg", "mcg", "g", "ml", "iu"}


class Resolution(BaseModel):
    kind: Literal["brand", "product", "formulation", "salt", "herb", "unresolved"]
    medication_code: str | None = None
    ingredient_ids: list[int] = []
    label: str
    note: str | None = None


def _name_key(text: str) -> tuple[str, list[str]]:
    tokens, strengths = clean_line(text)
    words = [t for t in tokens if t not in strengths and t not in _UNIT_TOKENS]
    return "".join(words), [re.sub(r"[a-z]+$", "", s) for s in strengths]


def _product_keys(catalog: MedicationCatalog) -> dict[str, list[str]]:
    cached: dict[str, list[str]] | None = getattr(catalog, "_product_keys", None)
    if cached is not None:
        return cached
    keys: dict[str, list[str]] = {}
    for code, rec in catalog.by_code.items():
        if not rec.view.active:
            continue
        for name in filter(None, [rec.view.name, rec.view.name_iast, rec.view.name_devanagari]):
            k, _ = _name_key(name)
            if k and code not in keys.setdefault(k, []):
                keys[k].append(code)
    catalog._product_keys = keys  # type: ignore[attr-defined]
    return keys


def resolve_line(session: Session, catalog: MedicationCatalog, text: str) -> Resolution:
    brand = resolve_brand(catalog, text)
    if brand.found and brand.medication_code:
        view = catalog.by_code[brand.medication_code].view
        return Resolution(
            kind="brand",
            medication_code=view.code,
            ingredient_ids=[c.ingredient_id for c in view.composition],
            label=f"{brand.brand} → {view.name}",
            note=brand.strength_note,
        )
    key, numbers = _name_key(text)
    if not key:
        return Resolution(kind="unresolved", label=UNRESOLVED)
    codes = _product_keys(catalog).get(key, [])
    if len(codes) > 1 and numbers:  # disambiguate by the printed strength ("Paracetamol 650")
        by_strength = [
            c
            for c in codes
            if any(
                n in (comp.strength or "") for comp in catalog.by_code[c].view.composition for n in numbers
            )
        ]
        codes = by_strength or codes
    if len(codes) == 1:
        view = catalog.by_code[codes[0]].view
        kind: Literal["product", "formulation"] = "product" if view.kind == "biomedical" else "formulation"
        return Resolution(
            kind=kind,
            medication_code=view.code,
            ingredient_ids=[c.ingredient_id for c in view.composition],
            label=view.name,
        )
    # Salt / herb level (e.g. "Tab Hydrochlorothiazide 12.5 mg OD", or an ambiguous generic).
    dictionary, by_key = db_dictionary(session)
    ing_key = dictionary.resolve(
        " ".join(t for t in clean_line(text)[0] if t not in _UNIT_TOKENS and not t[0].isdigit())
    )
    if ing_key is not None:
        ing = by_key[ing_key]
        kind2: Literal["salt", "herb"] = "salt" if ing.kind == "salt" else "herb"
        note = f"{len(codes)} products match; recorded at {kind2} level" if len(codes) > 1 else None
        return Resolution(kind=kind2, ingredient_ids=[ing.id], label=ing.canonical_name, note=note)
    return Resolution(kind="unresolved", label=UNRESOLVED)


class ImportedItem(BaseModel):
    id: int
    text: str
    status: str
    resolution: Resolution


class ImportResult(BaseModel):
    patient_id: str
    imported: int
    skipped_duplicates: int
    unresolved: int
    items: list[ImportedItem]
    issues: list[str]


def _date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)[:10]).date()
    except ValueError:
        return None


def _store(
    session: Session,
    catalog: MedicationCatalog,
    patient: Patient,
    *,
    text: str,
    status: str,
    start: date | None,
    end: date | None,
    encounter: str | None,
    source_system: str,
    source_ref: str | None,
    actor: str,
    recorded: date | None,
) -> PatientMedication:
    res = resolve_line(session, catalog, text)
    pm = PatientMedication(
        patient_id=patient.id,
        encounter_id=encounter,
        recorded_date=recorded,
        period_start=start,
        period_end=end,
        status=status,
        source_system=source_system,
        source_ref=source_ref,
        text=text[:256],
        resolution_kind=res.kind,
        medication_code=res.medication_code,
        ingredient_ids=res.ingredient_ids,
        resolution_note=res.note or (res.label if res.kind == "unresolved" else None),
        imported_by=actor,
    )
    session.add(pm)
    session.flush()
    return pm


def _audit(
    session: Session, patient: Patient, result: ImportResult, actor: str, role: str, purpose: str, fmt: str
) -> None:
    # Counts only: the medication text itself is never written to the ledger or logs.
    append_event(
        session,
        actor=actor,
        role=role,
        action="history_import",
        entity_type="Patient",
        entity_id=patient.id,
        after={
            "format": fmt,
            "imported": result.imported,
            "unresolved": result.unresolved,
            "skipped_duplicates": result.skipped_duplicates,
        },
        purpose_of_use=purpose,
    )


def import_bundle(
    session: Session,
    catalog: MedicationCatalog,
    patient: Patient,
    bundle: dict[str, Any],
    *,
    actor: str,
    role: str,
    purpose: str,
) -> ImportResult:
    if bundle.get("resourceType") != "Bundle":
        raise ValueError("body must be a FHIR Bundle")
    issues = [i["diagnostics"] for i in validation_issues(bundle) if i["severity"] in {"error", "fatal"}]
    if issues:
        raise ValueError("invalid Bundle: " + "; ".join(issues[:5]))
    resources = [e.get("resource") or {} for e in bundle.get("entry", [])]
    # The Bundle's Patient must carry this patient's ABHA identifier (TEST identifiers in the sandbox).
    bundle_patients = {
        r.get("id"): r
        for r in resources
        if r.get("resourceType") == "Patient"
        and any(i.get("value") == patient.abha_number for i in r.get("identifier", []))
    }
    seen = {
        pm.source_ref
        for pm in session.scalars(select(PatientMedication).where(PatientMedication.patient_id == patient.id))
    }
    result = ImportResult(
        patient_id=patient.id, imported=0, skipped_duplicates=0, unresolved=0, items=[], issues=[]
    )
    for r in resources:
        rt = r.get("resourceType")
        if rt not in {"MedicationStatement", "MedicationRequest"}:
            continue
        subject = (r.get("subject") or {}).get("reference", "")
        if subject.split("/")[-1] not in bundle_patients:
            result.issues.append(f"{rt}/{r.get('id')}: subject is not this patient; skipped")
            continue
        ref = f"{rt}/{r.get('id')}"
        if ref in seen:
            result.skipped_duplicates += 1
            continue
        concept = r.get("medicationCodeableConcept") or {}
        text = concept.get("text") or next(
            (c.get("display") for c in concept.get("coding", []) if c.get("display")), ""
        )
        if not text:
            result.issues.append(f"{ref}: no medication text or display; skipped")
            continue
        period = r.get("effectivePeriod") or {}
        start = _date(period.get("start") or r.get("effectiveDateTime") or r.get("authoredOn"))
        end = _date(period.get("end"))
        context = (r.get("context") or r.get("encounter") or {}).get("reference")
        pm = _store(
            session,
            catalog,
            patient,
            text=text,
            status=STATUS_MAP.get(r.get("status", ""), "unknown"),
            start=start,
            end=end,
            encounter=context.split("/")[-1] if context else None,
            source_system=f"fhir:{bundle.get('id') or 'bundle'}",
            source_ref=ref,
            actor=actor,
            recorded=_date(r.get("dateAsserted") or r.get("authoredOn")),
        )
        result.imported += 1
        result.unresolved += pm.resolution_kind == "unresolved"
        result.items.append(_item(session, catalog, pm))
    _audit(session, patient, result, actor, role, purpose, "fhir")
    return result


def import_text(
    session: Session,
    catalog: MedicationCatalog,
    patient: Patient,
    text: str,
    *,
    start: date | None,
    status: str,
    actor: str,
    role: str,
    purpose: str,
) -> ImportResult:
    result = ImportResult(
        patient_id=patient.id, imported=0, skipped_duplicates=0, unresolved=0, items=[], issues=[]
    )
    for line in text.splitlines():
        if not line.strip():
            continue
        pm = _store(
            session,
            catalog,
            patient,
            text=line.strip(),
            status=status,
            start=start,
            end=None,
            encounter=None,
            source_system="free-text",
            source_ref=None,
            actor=actor,
            recorded=start,
        )
        result.imported += 1
        result.unresolved += pm.resolution_kind == "unresolved"
        result.items.append(_item(session, catalog, pm))
    _audit(session, patient, result, actor, role, purpose, "text")
    return result


def _item(session: Session, catalog: MedicationCatalog, pm: PatientMedication) -> ImportedItem:
    label = UNRESOLVED
    if pm.medication_code and (view := catalog.get(pm.medication_code)):
        label = view.name
    elif pm.ingredient_ids:
        label = ", ".join(
            catalog.ingredients[i].canonical_name for i in pm.ingredient_ids if i in catalog.ingredients
        )
    return ImportedItem(
        id=pm.id,
        text=pm.text,
        status=pm.status,
        resolution=Resolution(
            kind=pm.resolution_kind,
            medication_code=pm.medication_code,
            ingredient_ids=list(pm.ingredient_ids),
            label=label,
            note=pm.resolution_note,
        ),
    )


def fold_text(text: str) -> str:
    return fold(text)
