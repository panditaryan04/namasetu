"""Active salt & constituent set, interaction checking, alerts and their FHIR form (DetectedIssue).

- Only medications ACTIVE at `as_of` are checked (stopped ones never raise "active" alerts; status
  "unknown" is included and flagged so the clinician can verify).
- Every interaction alert comes from a cited row of the loaded interaction dataset; nothing is inferred.
- Duplicate salts are detected from the cited compositions of both products.
- No result is ever "no interactions": it is "No interaction found in the loaded dataset (vX)".
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Literal

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.rbac import Principal
from app.db.models import Constituent, InteractionAlert, InteractionRule, Patient, PatientMedication
from app.fhir.common import base_url, new_id
from app.ledger.hash_chain import append_event
from app.medication.catalog import MedicationCatalog
from app.medication.constituents import interaction_dataset_tag

AgentType = Literal["salt", "herb", "constituent"]
SEVERITY_RANK = {"major": 0, "moderate": 1, "minor": 2}
FHIR_SEVERITY = {"major": "high", "moderate": "moderate", "minor": "low"}


@dataclass(frozen=True)
class Agent:
    type: AgentType
    key: str
    name: str
    path: tuple[str, ...]


@dataclass
class Holder:
    """A medication that contributes agents: a candidate (code) or an entry of the patient's history."""

    label: str
    agents: list[Agent]
    medication_code: str | None
    history_id: int | None = None
    status: str | None = None
    period_start: date | None = None
    period_end: date | None = None
    source: str | None = None


def constituents_by_ingredient(session: Session) -> dict[int, list[Constituent]]:
    out: dict[int, list[Constituent]] = {}
    for c in session.scalars(select(Constituent)):
        out.setdefault(c.ingredient_id, []).append(c)
    return out


def agents_for(
    catalog: MedicationCatalog,
    constituents: dict[int, list[Constituent]],
    label: str,
    ingredient_ids: list[int],
) -> list[Agent]:
    out: list[Agent] = []
    for iid in dict.fromkeys(ingredient_ids):
        ing = catalog.ingredients.get(iid)
        if ing is None or ing.kind == "other":
            continue
        if ing.kind == "salt":
            out.append(Agent("salt", ing.key, ing.canonical_name, (label, f"salt {ing.canonical_name}")))
            continue
        word = "mineral" if ing.kind == "mineral" else "herb"
        out.append(
            Agent("herb", ing.key, ing.canonical_name, (label, f"contains {word} {ing.canonical_name}"))
        )
        for c in constituents.get(iid, []):
            out.append(
                Agent(
                    "constituent",
                    c.key,
                    c.name,
                    (label, f"contains {word} {ing.canonical_name}", f"constituent {c.name}"),
                )
            )
    return out


def candidate_holder(
    catalog: MedicationCatalog, constituents: dict[int, list[Constituent]], code: str
) -> Holder | None:
    view = catalog.get(code)
    if view is None:
        return None
    ids = [c.ingredient_id for c in view.composition]
    return Holder(
        label=view.name,
        agents=agents_for(catalog, constituents, view.name, ids),
        medication_code=code,
        source=view.source.title,
    )


def is_active(pm: PatientMedication, as_of: date) -> bool:
    """Active on `as_of`. A stopped medication counts only inside its recorded period (needs an end date)."""
    if pm.period_start and pm.period_start > as_of:
        return False
    if pm.period_end and pm.period_end < as_of:
        return False
    if pm.status == "stopped":
        return pm.period_end is not None  # stopped with no end date: never assume it was active
    return True


def history_holders(
    session: Session,
    catalog: MedicationCatalog,
    constituents: dict[int, list[Constituent]],
    patient_id: str,
    as_of: date,
    active_only: bool = True,
) -> list[Holder]:
    out = []
    rows = session.scalars(
        select(PatientMedication)
        .where(PatientMedication.patient_id == patient_id)
        .order_by(PatientMedication.period_start, PatientMedication.id)
    )
    for pm in rows:
        if active_only and not is_active(pm, as_of):
            continue
        view = catalog.get(pm.medication_code) if pm.medication_code else None
        label = f"{view.name if view else pm.text} ({pm.text})" if view and view.name != pm.text else pm.text
        out.append(
            Holder(
                label=label,
                agents=agents_for(catalog, constituents, label, list(pm.ingredient_ids)),
                medication_code=pm.medication_code,
                history_id=pm.id,
                status=pm.status,
                period_start=pm.period_start,
                period_end=pm.period_end,
                source=view.source.title if view else None,
            )
        )
    return out


class AlertOut(BaseModel):
    id: int | None
    kind: Literal["duplicate", "herb-drug", "drug-drug"]
    severity: str
    evidence_level: str | None
    agents: list[str]
    path: str
    mechanism: str
    management_note: str | None
    citation: str
    dataset: str | None
    candidate_code: str | None
    other_medication: str
    other_status: str | None
    other_last_active: str | None
    status: str = "open"
    decided_by: str | None = None
    decision_reason: str | None = None


class CheckOut(BaseModel):
    patient_id: str | None
    as_of: date
    candidates: list[str]
    active_medications: list[str]
    alerts: list[AlertOut]
    message: str
    dataset: str | None


def _path(a: Agent) -> str:
    return " → ".join(a.path)


def _last_active(h: Holder, as_of: date) -> str | None:
    if h.status == "active" and (h.period_end is None or h.period_end >= as_of):
        return f"active (since {h.period_start.isoformat()})" if h.period_start else "active"
    if h.period_end:
        return h.period_end.isoformat()
    return "status unknown — verify" if h.status == "unknown" else None


def find_alerts(
    rules: list[InteractionRule], dataset: str | None, candidate: Holder, others: list[Holder], as_of: date
) -> list[AlertOut]:
    index: dict[frozenset[str], InteractionRule] = {
        frozenset({r.agent_a_key, r.agent_b_key}): r for r in rules
    }
    alerts: dict[str, AlertOut] = {}
    for other in others:
        # (a) duplicate active salt
        cand_salts = {a.key: a for a in candidate.agents if a.type == "salt"}
        for b in other.agents:
            if b.type == "salt" and b.key in cand_salts:
                key = f"dup|{b.key}|{other.history_id or other.medication_code}"
                alerts.setdefault(
                    key,
                    AlertOut(
                        id=None,
                        kind="duplicate",
                        severity="moderate",
                        evidence_level=None,
                        agents=[b.name, b.name],
                        path=f"{_path(cand_salts[b.key])} × {_path(b)}",
                        mechanism=f"Same salt ({b.name}) in two products.",
                        management_note=None,
                        citation="Compositions: "
                        + f"{candidate.source or candidate.label}; {other.source or 'patient record'}",
                        dataset=None,
                        candidate_code=candidate.medication_code,
                        other_medication=other.label,
                        other_status=other.status,
                        other_last_active=_last_active(other, as_of),
                    ),
                )
        # (b) herb-drug and (c) drug-drug from the cited dataset
        for a in candidate.agents:
            for b in other.agents:
                rule = index.get(frozenset({a.key, b.key}))
                if rule is None or a.key == b.key:
                    continue
                kind: Literal["herb-drug", "drug-drug"] = (
                    "drug-drug" if a.type == b.type == "salt" else "herb-drug"
                )
                key = f"rule{rule.id}|{other.history_id or other.medication_code}"
                alerts.setdefault(
                    key,
                    AlertOut(
                        id=None,
                        kind=kind,
                        severity=rule.severity,
                        evidence_level=rule.evidence_level,
                        agents=[a.name, b.name],
                        path=f"{_path(a)} × {_path(b)}",
                        mechanism=rule.mechanism_summary,
                        management_note=rule.management_note,
                        citation=rule.citation,
                        dataset=(
                            f"{dataset} (row source {rule.source_version})"
                            if dataset
                            else rule.source_version
                        ),
                        candidate_code=candidate.medication_code,
                        other_medication=other.label,
                        other_status=other.status,
                        other_last_active=_last_active(other, as_of),
                    ),
                )
    return sorted(alerts.values(), key=lambda x: (SEVERITY_RANK.get(x.severity, 9), x.kind, x.path))


def _alert_key(patient_id: str, alert: AlertOut) -> str:
    agents = "|".join(sorted(alert.agents))
    raw = f"{patient_id}|{alert.candidate_code}|{alert.kind}|{agents}|{alert.other_medication}"
    return hashlib.sha256(raw.encode()).hexdigest()


def check(
    session: Session,
    catalog: MedicationCatalog,
    candidate_codes: list[str],
    *,
    patient: Patient | None,
    as_of: date,
    persist: bool,
) -> CheckOut:
    constituents = constituents_by_ingredient(session)
    rules = list(session.scalars(select(InteractionRule)))
    dataset = interaction_dataset_tag(session)
    candidates = [
        h for c in dict.fromkeys(candidate_codes) if (h := candidate_holder(catalog, constituents, c))
    ]
    history = history_holders(session, catalog, constituents, patient.id, as_of) if patient else []
    alerts: list[AlertOut] = []
    for i, cand in enumerate(candidates):
        # Against the patient's active set, and against the other selected candidates.
        alerts += find_alerts(rules, dataset, cand, history + candidates[i + 1 :], as_of)
    if patient is not None and persist:
        for a in alerts:
            key = _alert_key(patient.id, a)
            row = session.scalars(
                select(InteractionAlert).where(
                    InteractionAlert.patient_id == patient.id, InteractionAlert.alert_key == key
                )
            ).first()
            if row is None:
                row = InteractionAlert(
                    patient_id=patient.id,
                    candidate_code=a.candidate_code,
                    alert_key=key,
                    kind=a.kind,
                    severity=a.severity,
                    payload=a.model_dump(mode="json"),
                )
                session.add(row)
                session.flush()
                append_event(
                    session,
                    actor="system",
                    role="system",
                    action="alert",
                    entity_type="InteractionAlert",
                    entity_id=str(row.id),
                    after={
                        "patient": patient.id,
                        "kind": a.kind,
                        "severity": a.severity,
                        "agents": a.agents,
                        "citation": a.citation,
                    },
                )
            a.id, a.status, a.decided_by, a.decision_reason = (
                row.id,
                row.status,
                row.decided_by,
                row.decision_reason,
            )
    if alerts:
        message = f"{len(alerts)} alert(s) from the loaded data ({dataset or 'no interaction dataset'})."
    else:
        message = f"No interaction found in the loaded dataset ({dataset or 'none loaded'})."
        message += " Absence of data is not proof of safety."
    return CheckOut(
        patient_id=patient.id if patient else None,
        as_of=as_of,
        candidates=[c.label for c in candidates],
        active_medications=[h.label for h in history],
        alerts=alerts,
        message=message,
        dataset=dataset,
    )


def salt_index(catalog: MedicationCatalog) -> dict[str, int]:
    """Lower-cased salt names and synonyms -> ingredient id (salts only; herbs come from formulations)."""
    out: dict[str, int] = {}
    for ing in catalog.ingredients.values():
        if ing.kind != "salt":
            continue
        for name in [ing.canonical_name, *(s.get("term", "") for s in ing.synonyms)]:
            if name:
                out.setdefault(name.strip().lower(), ing.id)
    return out


def screen(
    session: Session,
    catalog: MedicationCatalog,
    candidate_codes: list[str],
    active: list[tuple[str, list[str]]],
    as_of: date,
) -> tuple[CheckOut, list[str]]:
    """Screen candidates against medicines given as (label, salt names) instead of a stored patient.

    For records held outside NamaSetu (e.g. the Care portal demo). Same cited rules as `check`; nothing
    is persisted. Salt names not in the catalogue are returned so the caller can say they were not checked.
    """
    constituents = constituents_by_ingredient(session)
    rules = list(session.scalars(select(InteractionRule)))
    dataset = interaction_dataset_tag(session)
    index = salt_index(catalog)
    unresolved: list[str] = []
    others: list[Holder] = []
    for label, salts in active:
        ids = []
        for s in salts:
            iid = index.get(s.strip().lower())
            if iid is None:
                unresolved.append(s)
            else:
                ids.append(iid)
        others.append(
            Holder(
                label=label,
                agents=agents_for(catalog, constituents, label, ids),
                medication_code=None,
                status="active",
                source="patient record",
            )
        )
    candidates = [
        h for c in dict.fromkeys(candidate_codes) if (h := candidate_holder(catalog, constituents, c))
    ]
    alerts: list[AlertOut] = []
    for i, cand in enumerate(candidates):
        alerts += find_alerts(rules, dataset, cand, others + candidates[i + 1 :], as_of)
    if alerts:
        message = f"{len(alerts)} alert(s) from the loaded data ({dataset or 'no interaction dataset'})."
    else:
        message = f"No interaction found in the loaded dataset ({dataset or 'none loaded'})."
        message += " Absence of data is not proof of safety."
    out = CheckOut(
        patient_id=None,
        as_of=as_of,
        candidates=[c.label for c in candidates],
        active_medications=[h.label for h in others],
        alerts=alerts,
        message=message,
        dataset=dataset,
    )
    return out, list(dict.fromkeys(unresolved))


class AckRequest(BaseModel):
    action: Literal["acknowledge", "override"]
    reason: str


def acknowledge(session: Session, alert_id: int, req: AckRequest, principal: Principal) -> InteractionAlert:
    alert = session.get(InteractionAlert, alert_id)
    if alert is None:
        raise LookupError(f"alert {alert_id} not found")
    if alert.status != "open":
        raise ValueError(f"alert {alert_id} is already {alert.status}")
    if len(req.reason.strip()) < 3:
        raise ValueError("a reason is mandatory")
    alert.status = "acknowledged" if req.action == "acknowledge" else "overridden"
    alert.decided_by, alert.decision_reason, alert.decided_at = (
        principal.username,
        req.reason,
        datetime.now(UTC),
    )
    append_event(
        session,
        actor=principal.username,
        role=principal.role,
        action=req.action,
        entity_type="InteractionAlert",
        entity_id=str(alert.id),
        before={"status": "open"},
        after={"status": alert.status, "reason": req.reason},
        purpose_of_use="TREAT",
    )
    return alert


# ------------------------------------------------------------------ FHIR


def _utc(dt: datetime) -> datetime:
    """SQLite returns naive datetimes; FHIR dateTime with a time part must carry a timezone."""
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def detected_issue(alert: InteractionAlert) -> dict[str, Any]:
    p = alert.payload
    duplicate = alert.kind == "duplicate"
    implicated = []
    if alert.candidate_code:
        implicated.append({"reference": f"Medication/{alert.candidate_code}"})
    implicated.append({"display": p["other_medication"]})
    res: dict[str, Any] = {
        "resourceType": "DetectedIssue",
        "id": f"alert-{alert.id}",
        "status": "final",
        "code": {
            "coding": [
                {
                    "system": "http://terminology.hl7.org/CodeSystem/v3-ActCode",
                    "code": "DUPTHPY" if duplicate else "DRG",
                    "display": "Duplicate Therapy Alert" if duplicate else "Drug Interaction Alert",
                }
            ],
            "text": f"{alert.kind}: {' × '.join(p['agents'])}",
        },
        "severity": FHIR_SEVERITY.get(alert.severity, "moderate"),
        "patient": {"reference": f"Patient/{alert.patient_id}"},
        "identifiedDateTime": _utc(alert.created_at).isoformat(timespec="seconds"),
        "implicated": implicated,
        "evidence": [
            {"detail": [{"display": f"{p['citation']}" + (f" | {p['dataset']}" if p.get("dataset") else "")}]}
        ],
        "detail": f"{p['mechanism']} Path: {p['path']}."
        + (f" Evidence level: {p['evidence_level']}." if p.get("evidence_level") else ""),
    }
    if alert.status != "open":
        res["mitigation"] = [
            {
                "action": {"text": f"{alert.status}: {alert.decision_reason}"},
                "date": _utc(alert.decided_at or datetime.now(UTC)).isoformat(timespec="seconds"),
                "author": {"display": alert.decided_by},
            }
        ]
    return res


def timeline_bundle(session: Session, catalog: MedicationCatalog, patient_id: str) -> dict[str, Any]:
    """Medication history as MedicationStatements; salts/herbs in contained Medication.ingredient."""
    status = {"active": "active", "stopped": "stopped", "unknown": "unknown"}
    entries = []
    for pm in session.scalars(
        select(PatientMedication)
        .where(PatientMedication.patient_id == patient_id)
        .order_by(PatientMedication.period_start, PatientMedication.id)
    ):
        med: dict[str, Any] = {
            "resourceType": "Medication",
            "id": "m1",
            "code": {"text": pm.text},
            "ingredient": [
                {"itemCodeableConcept": {"text": catalog.ingredients[i].canonical_name}}
                for i in pm.ingredient_ids
                if i in catalog.ingredients
            ],
        }
        if not med["ingredient"]:
            med.pop("ingredient")
        ms: dict[str, Any] = {
            "resourceType": "MedicationStatement",
            "id": f"pm-{pm.id}",
            "contained": [med],
            "status": status.get(pm.status, "unknown"),
            "medicationReference": {"reference": "#m1"},
            "subject": {"reference": f"Patient/{patient_id}"},
            "note": [
                {
                    "text": f"Resolved as {pm.resolution_kind}"
                    + (f": {pm.resolution_note}" if pm.resolution_note else "")
                }
            ],
        }
        if pm.period_start or pm.period_end:
            ms["effectivePeriod"] = {
                k: v.isoformat() for k, v in (("start", pm.period_start), ("end", pm.period_end)) if v
            }
        if pm.encounter_id:
            ms["context"] = {"reference": f"Encounter/{pm.encounter_id}"}
        entries.append({"fullUrl": f"{base_url()}/MedicationStatement/pm-{pm.id}", "resource": ms})
    return {"resourceType": "Bundle", "id": new_id(), "type": "collection", "entry": entries}
