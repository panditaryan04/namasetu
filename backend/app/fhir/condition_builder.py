"""Tri-coded Condition builder (POST /fhir/Condition/$build).

code.coding = NAMASTE + ICD-11 TM2 (+ ICD-11 biomedicine only when an approved link exists).
The TM2 coding must come from the Approved Mapping Store, or be a HIGH-band suggestion that the
clinician explicitly confirms. Medium/low suggestions are refused: they belong in the review queue.
Base FHIR R4 Condition. The NRCeS ABDM Condition profile is not claimed because it closes code.coding to
ICD-10 + SNOMED CT (ndhm.in 6.5.0), which excludes NAMASTE and ICD-11; see docs/standards.md.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import SYSTEM_BIO, SYSTEM_NAMASTE, SYSTEM_TM2, ConceptLink, Mapping
from app.engine.service import MappingEngine
from app.fhir.audit_provenance import record_provenance
from app.fhir.common import ABHA_SYSTEM, FHIRError, base_url, new_id, system_uri
from app.fhir.validator import validate
from app.ledger.hash_chain import append_event

CLINICAL = "http://terminology.hl7.org/CodeSystem/condition-clinical"
VERIFICATION = "http://terminology.hl7.org/CodeSystem/condition-ver-status"
CATEGORY = "http://terminology.hl7.org/CodeSystem/condition-category"


class PatientRef(BaseModel):
    abha_number: str = Field(min_length=4, max_length=32, description="ABHA number (TEST values in sandbox)")
    abha_address: str | None = Field(None, max_length=64)
    display: str | None = Field(None, max_length=128)
    id: str | None = Field(None, max_length=64, pattern=r"^[A-Za-z0-9\-.]{1,64}$")


class ConditionBuildRequest(BaseModel):
    namaste_code: str = Field(min_length=1, max_length=64)
    tm2_code: str | None = Field(None, max_length=64, description="Defaults to the approved mapping")
    confirm_suggestion: bool = Field(
        False, description="Clinician confirms a HIGH-band (non-approved) suggestion"
    )
    include_biomedicine: bool = True
    patient: PatientRef
    encounter_id: str | None = Field(None, max_length=64, pattern=r"^[A-Za-z0-9\-.]{1,64}$")
    recorded_date: str | None = Field(None, description="ISO date/dateTime; defaults to now")
    clinical_status: Literal["active", "recurrence", "relapse", "inactive", "remission", "resolved"] = (
        "active"
    )
    verification_status: Literal["unconfirmed", "provisional", "differential", "confirmed"] = "confirmed"
    category: Literal["encounter-diagnosis", "problem-list-item"] = "encounter-diagnosis"
    purpose_of_use: Literal["TREAT", "ETREAT", "HPAYMT", "PUBHLTH", "HRESCH"] = "TREAT"
    note: str | None = Field(None, max_length=1000)


def _approved_mapping(session: Session, namaste_code: str) -> Mapping | None:
    return session.scalars(
        select(Mapping)
        .where(
            Mapping.status == "approved",
            Mapping.needs_review.is_(False),
            Mapping.source_system == SYSTEM_NAMASTE,
            Mapping.source_code == namaste_code,
            Mapping.target_system == SYSTEM_TM2,
        )
        .order_by(Mapping.version.desc(), Mapping.id.desc())
        .limit(1)
    ).first()


def _approved_bio(session: Session, tm2_code: str) -> ConceptLink | None:
    return session.scalars(
        select(ConceptLink)
        .where(
            ConceptLink.source_code == tm2_code,
            ConceptLink.status == "approved",
            ConceptLink.target_system == SYSTEM_BIO,
        )
        .order_by(ConceptLink.origin, ConceptLink.target_code)
        .limit(1)
    ).first()


def build_condition(
    session: Session,
    engine: MappingEngine,
    req: ConditionBuildRequest,
    actor: str = "anonymous",
    role: str = "public",
) -> dict[str, Any]:
    idx = engine.index
    namaste = idx.get(SYSTEM_NAMASTE, req.namaste_code)
    if namaste is None or namaste.is_category:
        raise FHIRError(404, f"NAMASTE code {req.namaste_code} not found", "not-found")

    mapping = _approved_mapping(session, namaste.code)
    provenance_sources: list[str]
    if mapping is not None and (req.tm2_code in (None, mapping.target_code)):
        tm2_code, tm2_basis = (
            mapping.target_code,
            f"approved mapping {mapping.id} v{mapping.version} ({mapping.reviewer})",
        )
        provenance_sources = [f"Mapping/{mapping.id}"]
    elif req.tm2_code is None:
        raise FHIRError(
            422,
            f"No approved TM2 mapping for {namaste.code}; choose a suggestion or send it to review",
            "business-rule",
        )
    else:
        result = engine.map(code=namaste.code, top_k=10)
        cand = next((c for c in result.candidates if c.tm2.code == req.tm2_code), None)
        if cand is None or cand.band != "high":
            band = cand.band if cand else "not suggested"
            raise FHIRError(
                422,
                f"TM2 {req.tm2_code} is {band}; only approved or HIGH-band confirmed codes can be emitted. "
                "Send it to the review queue.",
                "business-rule",
            )
        if not req.confirm_suggestion:
            raise FHIRError(
                422,
                "HIGH-band suggestion requires confirm_suggestion=true (clinician decision)",
                "business-rule",
            )
        tm2_code = cand.tm2.code
        tm2_basis = (
            f"clinician-confirmed high-band suggestion ({cand.confidence:.2f}, {result.model_version})"
        )
        provenance_sources = [f"suggestion:{result.model_version}"]
    tm2 = idx.get(SYSTEM_TM2, tm2_code)
    if tm2 is None:
        raise FHIRError(
            500, "approved TM2 code is not in the dataset", "exception"
        )  # never emit unknown codes

    sample = idx.is_sample
    codings: list[dict[str, Any]] = [
        {
            "system": system_uri(SYSTEM_NAMASTE, sample),
            "version": idx.release_version("namaste") or "unknown",
            "code": namaste.code,
            "display": namaste.display,
        },
        {"system": system_uri(SYSTEM_TM2, sample), "code": tm2.code, "display": tm2.display},
    ]
    bio_note = "no approved biomedical link"
    if req.include_biomedicine and (link := _approved_bio(session, tm2.code)) is not None:
        bio = idx.get(SYSTEM_BIO, link.target_code)
        if bio is not None:
            codings.append(
                {"system": system_uri(SYSTEM_BIO, sample), "code": bio.code, "display": bio.display}
            )
            bio_note = f"biomedical link from {link.origin} (relatedto; supports claims, insurer decides)"

    cid = new_id()
    patient_id = req.patient.id or f"abha-{''.join(ch for ch in req.patient.abha_number if ch.isalnum())}"
    recorded = req.recorded_date or datetime.now(UTC).isoformat(timespec="seconds")
    condition: dict[str, Any] = {
        "resourceType": "Condition",
        "id": cid,
        "meta": {},
        "clinicalStatus": {"coding": [{"system": CLINICAL, "code": req.clinical_status}]},
        "verificationStatus": {"coding": [{"system": VERIFICATION, "code": req.verification_status}]},
        "category": [{"coding": [{"system": CATEGORY, "code": req.category}]}],
        "code": {"coding": codings, "text": namaste.display},
        "subject": {
            "reference": f"Patient/{patient_id}",
            "identifier": {"system": ABHA_SYSTEM, "value": req.patient.abha_number},
            **({"display": req.patient.display} if req.patient.display else {}),
        },
        "recordedDate": recorded,
    }
    if not sample:
        condition.pop("meta")
    else:
        condition["meta"]["tag"] = [
            {
                "system": f"{base_url()}/CodeSystem/namasetu-tags",
                "code": "sample-data",
                "display": "SAMPLE — illustrative, not official",
            }
        ]
    if req.encounter_id:
        condition["encounter"] = {"reference": f"Encounter/{req.encounter_id}"}
    notes = [f"TM2 basis: {tm2_basis}; {bio_note}."]
    if req.note:
        notes.insert(0, req.note)
    condition["note"] = [{"text": n} for n in notes]
    validate(condition)

    record_provenance(
        session,
        target=f"Condition/{cid}",
        activity="build",
        agent=actor,
        entities=[*provenance_sources, *idx.releases],
        reason=f"purpose-of-use {req.purpose_of_use}",
        reason_code=req.purpose_of_use,
    )
    append_event(
        session,
        actor=actor,
        role=role,
        action="fhir_export",
        entity_type="Condition",
        entity_id=cid,
        after={"codes": [c["code"] for c in codings], "tm2_basis": tm2_basis},
        purpose_of_use=req.purpose_of_use,
    )
    session.commit()
    return condition
