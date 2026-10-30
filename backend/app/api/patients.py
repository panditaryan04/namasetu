"""Patient medication history, active salts, interaction alerts (clinician / admin; SYNTHETIC patients)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import delete, func, select

from app.api.common import DbSession
from app.api.medications import Catalog
from app.core.rbac import PatientAccess
from app.db.models import InteractionAlert, InteractionRule, Patient, PatientMedication
from app.ledger.hash_chain import append_event
from app.medication.constituents import interaction_dataset_tag
from app.medication.history import ImportResult, Resolution, import_bundle, import_text
from app.medication.interactions import (
    AckRequest,
    AlertOut,
    CheckOut,
    acknowledge,
    check,
    constituents_by_ingredient,
    find_alerts,
    history_holders,
    is_active,
)

router = APIRouter(tags=["patients"])
PURPOSES = Literal["TREAT", "ETREAT", "HPAYMT", "PUBHLTH", "HRESCH"]


def _patient(db: DbSession, patient_id: str) -> Patient:
    p = db.get(Patient, patient_id)
    if p is None:
        raise HTTPException(status_code=404, detail=f"patient {patient_id} not found")
    return p


class PatientOut(BaseModel):
    id: str
    display: str
    abha_number: str
    is_synthetic: bool
    medications: int
    active: int


class ImportRequest(BaseModel):
    format: Literal["fhir", "text"]
    bundle: dict[str, Any] | None = None
    text: str | None = Field(None, max_length=10_000)
    start_date: date | None = None
    status: Literal["active", "stopped", "unknown"] = "active"
    purpose_of_use: PURPOSES = "TREAT"

    @model_validator(mode="after")
    def _payload(self) -> ImportRequest:
        if self.format == "fhir" and not self.bundle:
            raise ValueError("format 'fhir' needs a 'bundle'")
        if self.format == "text" and not (self.text and self.text.strip()):
            raise ValueError("format 'text' needs 'text' (one medication per line)")
        return self


class TimelineAgent(BaseModel):
    type: str
    name: str
    path: str


class TimelineItem(BaseModel):
    id: int
    text: str
    status: str
    active_now: bool
    period_start: date | None
    period_end: date | None
    encounter_id: str | None
    source_system: str
    resolution: Resolution
    agents: list[TimelineAgent]


class Timeline(BaseModel):
    patient: PatientOut
    as_of: date
    items: list[TimelineItem]
    existing_alerts: list[AlertOut]
    dataset: str | None


def _patient_out(db: DbSession, p: Patient, as_of: date) -> PatientOut:
    rows = list(db.scalars(select(PatientMedication).where(PatientMedication.patient_id == p.id)))
    return PatientOut(
        id=p.id,
        display=p.display,
        abha_number=p.abha_number,
        is_synthetic=p.is_synthetic,
        medications=len(rows),
        active=sum(is_active(r, as_of) for r in rows),
    )


def _today() -> date:
    return datetime.now(UTC).date()


@router.get("/patients", response_model=list[PatientOut])
def patients(_: PatientAccess, db: DbSession) -> list[PatientOut]:
    return [_patient_out(db, p, _today()) for p in db.scalars(select(Patient).order_by(Patient.id))]


@router.post("/patients/{patient_id}/medications/import", response_model=ImportResult, status_code=201)
def import_history(
    patient_id: str, req: ImportRequest, user: PatientAccess, catalog: Catalog, db: DbSession
) -> ImportResult:
    patient = _patient(db, patient_id)
    try:
        if req.format == "fhir":
            assert req.bundle is not None
            result = import_bundle(
                db,
                catalog,
                patient,
                req.bundle,
                actor=user.username,
                role=user.role,
                purpose=req.purpose_of_use,
            )
        else:
            assert req.text is not None
            result = import_text(
                db,
                catalog,
                patient,
                req.text,
                start=req.start_date or _today(),
                status=req.status,
                actor=user.username,
                role=user.role,
                purpose=req.purpose_of_use,
            )
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.commit()
    return result


@router.get("/patients/{patient_id}/medications/timeline", response_model=Timeline)
def timeline(
    patient_id: str, _: PatientAccess, catalog: Catalog, db: DbSession, as_of: date | None = None
) -> Timeline:
    patient = _patient(db, patient_id)
    as_of = as_of or _today()
    constituents = constituents_by_ingredient(db)
    holders = {
        h.history_id: h
        for h in history_holders(db, catalog, constituents, patient.id, as_of, active_only=False)
    }
    items = []
    for pm in db.scalars(
        select(PatientMedication)
        .where(PatientMedication.patient_id == patient.id)
        .order_by(PatientMedication.period_start, PatientMedication.id)
    ):
        h = holders.get(pm.id)
        label = h.label if h else pm.text
        items.append(
            TimelineItem(
                id=pm.id,
                text=pm.text,
                status=pm.status,
                active_now=is_active(pm, as_of),
                period_start=pm.period_start,
                period_end=pm.period_end,
                encounter_id=pm.encounter_id,
                source_system=pm.source_system,
                resolution=Resolution(
                    kind=pm.resolution_kind,
                    medication_code=pm.medication_code,
                    ingredient_ids=list(pm.ingredient_ids),
                    label=label,
                    note=pm.resolution_note,
                ),
                agents=[
                    TimelineAgent(type=a.type, name=a.name, path=" → ".join(a.path))
                    for a in (h.agents if h else [])
                ],
            )
        )
    # Interactions already present among the currently active medications (not persisted; informational).
    rules = list(db.scalars(select(InteractionRule)))
    dataset = interaction_dataset_tag(db)
    history = db.scalars(select(PatientMedication).where(PatientMedication.patient_id == patient.id))
    active_ids = {pm.id for pm in history if is_active(pm, as_of)}
    active = [h for h in holders.values() if h.history_id in active_ids]
    existing: list[AlertOut] = []
    for i, h in enumerate(active):
        existing += find_alerts(rules, dataset, h, active[i + 1 :], as_of)
    return Timeline(
        patient=_patient_out(db, patient, as_of),
        as_of=as_of,
        items=items,
        existing_alerts=existing,
        dataset=dataset,
    )


class ActiveSet(BaseModel):
    patient_id: str
    as_of: date
    salts: list[dict[str, Any]]
    herbs: list[dict[str, Any]]
    constituents: list[dict[str, Any]]


@router.get("/patients/{patient_id}/active-salts", response_model=ActiveSet)
def active_salts(
    patient_id: str, _: PatientAccess, catalog: Catalog, db: DbSession, as_of: date | None = None
) -> ActiveSet:
    patient = _patient(db, patient_id)
    as_of = as_of or _today()
    groups: dict[str, dict[str, dict[str, Any]]] = {"salt": {}, "herb": {}, "constituent": {}}
    for h in history_holders(db, catalog, constituents_by_ingredient(db), patient.id, as_of):
        for a in h.agents:
            entry = groups[a.type].setdefault(a.key, {"name": a.name, "from": [], "since": None})
            entry["from"].append({"medication": h.label, "path": " → ".join(a.path), "status": h.status})
            if h.period_start and (entry["since"] is None or h.period_start.isoformat() < entry["since"]):
                entry["since"] = h.period_start.isoformat()

    def sort(d: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(d.values(), key=lambda e: str(e["name"]))

    return ActiveSet(
        patient_id=patient.id,
        as_of=as_of,
        salts=sort(groups["salt"]),
        herbs=sort(groups["herb"]),
        constituents=sort(groups["constituent"]),
    )


@router.get("/patients/{patient_id}/interactions", response_model=CheckOut)
def patient_interactions(
    patient_id: str,
    user: PatientAccess,
    catalog: Catalog,
    db: DbSession,
    candidate: Annotated[list[str], Query(min_length=1, max_length=20)],
    as_of: date | None = None,
) -> CheckOut:
    """Check candidate medication(s) against the patient's active salts / constituents; alerts persist."""
    patient = _patient(db, patient_id)
    unknown = [c for c in candidate if catalog.get(c) is None]
    if unknown:
        raise HTTPException(status_code=404, detail=f"unknown medication(s): {', '.join(unknown)}")
    out = check(db, catalog, candidate, patient=patient, as_of=as_of or _today(), persist=True)
    append_event(
        db,
        actor=user.username,
        role=user.role,
        action="interaction_check",
        entity_type="Patient",
        entity_id=patient.id,
        after={"candidates": candidate, "alerts": len(out.alerts), "dataset": out.dataset},
        purpose_of_use="TREAT",
    )
    db.commit()
    return out


@router.post("/interactions/{alert_id}/acknowledge", response_model=AlertOut)
def acknowledge_alert(alert_id: int, req: AckRequest, user: PatientAccess, db: DbSession) -> AlertOut:
    try:
        alert = acknowledge(db, alert_id, req, user)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409 if "already" in str(exc) else 422, detail=str(exc)) from exc
    db.commit()
    return AlertOut(
        **{
            **alert.payload,
            "id": alert.id,
            "status": alert.status,
            "decided_by": alert.decided_by,
            "decision_reason": alert.decision_reason,
        }
    )


@router.delete("/patients/{patient_id}/medications")
def delete_history(patient_id: str, user: PatientAccess, db: DbSession) -> dict[str, int]:
    """Right to erasure for the medication history (and its alerts) of one patient."""
    patient = _patient(db, patient_id)
    n_meds = (
        db.scalar(
            select(func.count())
            .select_from(PatientMedication)
            .where(PatientMedication.patient_id == patient.id)
        )
        or 0
    )
    n_alerts = (
        db.scalar(
            select(func.count())
            .select_from(InteractionAlert)
            .where(InteractionAlert.patient_id == patient.id)
        )
        or 0
    )
    db.execute(delete(InteractionAlert).where(InteractionAlert.patient_id == patient.id))
    db.execute(delete(PatientMedication).where(PatientMedication.patient_id == patient.id))
    append_event(
        db,
        actor=user.username,
        role=user.role,
        action="history_delete",
        entity_type="Patient",
        entity_id=patient.id,
        after={"medications": n_meds, "alerts": n_alerts},
        purpose_of_use="TREAT",
    )
    db.commit()
    return {"deleted_medications": n_meds, "deleted_alerts": n_alerts}
