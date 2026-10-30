"""Longitudinal history, active salts, interaction alerts, acknowledgement, DetectedIssue, privacy."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.db.models import InteractionAlert, PatientMedication
from app.db.session import get_sessionmaker
from app.fhir.validator import validation_issues

AS_OF = "2026-10-30"


@pytest.fixture
def clinician(as_role: Callable[[str], TestClient]) -> TestClient:
    return as_role("clinician")


def _alerts(c: TestClient, patient: str, *codes: str) -> dict[str, Any]:
    out: dict[str, Any] = c.get(
        f"/patients/{patient}/interactions", params={"candidate": list(codes), "as_of": AS_OF}
    ).json()
    return out


def _synthetic_bundle(patient_id: str) -> dict[str, Any]:
    from app.db.seed import load_bundle

    patients = load_bundle(use_embedded=True)["synthetic_patients"]["patients"]
    bundle: dict[str, Any] = next(p for p in patients if p["id"] == patient_id)["bundle"]
    return bundle


def test_synthetic_patients_and_timeline(clinician: TestClient) -> None:
    patients = {p["id"]: p for p in clinician.get("/patients").json()}
    assert len(patients) == 10 and all(
        p["is_synthetic"] and "TEST" in p["abha_number"] for p in patients.values()
    )
    tl = clinician.get("/patients/test-patient-03/medications/timeline", params={"as_of": AS_OF}).json()
    by_text = {i["text"]: i for i in tl["items"]}
    assert (
        by_text["Brufen 400 TDS"]["status"] == "stopped" and by_text["Brufen 400 TDS"]["active_now"] is False
    )
    assert by_text["Folitrax 2.5"]["active_now"] is True
    assert by_text["Folitrax 2.5"]["resolution"]["kind"] == "brand"
    assert {a["name"] for a in by_text["Folitrax 2.5"]["agents"]} == {"Methotrexate"}


def test_active_set_includes_constituents_with_paths(clinician: TestClient) -> None:
    s = clinician.get("/patients/test-patient-08/active-salts", params={"as_of": AS_OF}).json()
    assert {h["name"] for h in s["herbs"]} == {"Haritaki", "Bibhitaki", "Amalaki"}
    tannin = next(c for c in s["constituents"] if c["name"] == "Tannins (emblicanin)")
    assert tannin["from"][0]["path"].endswith("→ contains herb Amalaki → constituent Tannins (emblicanin)")
    stopped = clinician.get("/patients/test-patient-06/active-salts", params={"as_of": AS_OF}).json()
    assert stopped["salts"] == []  # metformin was stopped in 2024


def test_multihop_herb_drug_alert_with_citation(clinician: TestClient) -> None:
    out = _alerts(clinician, "test-patient-01", "SAMPLE-MED-A008")
    curcumin = [a for a in out["alerts"] if "Curcumin" in a["agents"]]
    assert {tuple(sorted(a["agents"])) for a in curcumin} == {
        ("Curcumin", "Glimepiride"),
        ("Curcumin", "Metformin"),
    }
    a = next(x for x in curcumin if "Glimepiride" in x["agents"])
    assert a["kind"] == "herb-drug" and a["severity"] == "moderate" and a["evidence_level"] == "theoretical"
    assert a["path"].startswith("Nisha Amalaki Churna → contains herb Haridra → constituent Curcumin × ")
    assert (
        a["citation"]
        and "interactions@SAMPLE-1" in a["dataset"]
        and a["other_last_active"].startswith("active")
    )
    assert a["id"]  # persisted for acknowledgement


def test_stopped_medication_never_alerts(clinician: TestClient) -> None:
    out = _alerts(clinician, "test-patient-06", "SAMPLE-MED-A008")
    assert out["alerts"] == [] and out["active_medications"] == []
    assert out["message"].startswith("No interaction found in the loaded dataset (interactions@SAMPLE-1)")
    past = clinician.get(
        "/patients/test-patient-06/interactions",
        params={"candidate": "SAMPLE-MED-A008", "as_of": "2024-01-01"},
    ).json()
    assert [sorted(a["agents"]) for a in past["alerts"]] == [["Curcumin", "Metformin"]]  # it was active then


def test_drug_drug_and_duplicate_alerts(clinician: TestClient) -> None:
    major = _alerts(clinician, "test-patient-03", "SAMPLE-MED-B014")["alerts"]
    assert [(a["kind"], a["severity"], sorted(a["agents"])) for a in major] == [
        ("drug-drug", "major", ["Mefenamic acid", "Methotrexate"])
    ]
    dup = _alerts(clinician, "test-patient-05", "SAMPLE-MED-B003")["alerts"]
    assert dup[0]["kind"] == "duplicate" and dup[0]["agents"] == ["Paracetamol", "Paracetamol"]
    assert "Compositions:" in dup[0]["citation"]


def test_free_text_import_resolves_or_flags(clinician: TestClient) -> None:
    text = "Crocin 500\nTab Metformin 500mg BD\nMulethi churna\nSyp Xyzol 5 ml TDS\n"
    r = clinician.post(
        "/patients/test-patient-09/medications/import",
        json={"format": "text", "text": text, "start_date": "2026-10-01"},
    )
    try:
        assert r.status_code == 201
        body = r.json()
        assert [i["resolution"]["kind"] for i in body["items"]] == ["brand", "salt", "herb", "unresolved"]
        assert (
            body["unresolved"] == 1
            and body["items"][3]["resolution"]["label"] == "Unrecognised — please verify"
        )
        audit = clinician.get("/audit", params={"action": "history_import"}).json()["items"][0]
        assert audit["after"]["imported"] == 4 and "Crocin" not in str(
            audit["after"]
        )  # counts only, no plaintext
    finally:
        ids = [i["id"] for i in r.json()["items"]]
        with get_sessionmaker()() as s:
            s.execute(delete(PatientMedication).where(PatientMedication.id.in_(ids)))
            s.commit()


def test_fhir_bundle_import_is_idempotent_and_checks_subject(clinician: TestClient) -> None:
    bundle = _synthetic_bundle("test-patient-01")
    again = clinician.post(
        "/patients/test-patient-01/medications/import", json={"format": "fhir", "bundle": bundle}
    )
    assert again.json()["imported"] == 0 and again.json()["skipped_duplicates"] == 2
    wrong = clinician.post(
        "/patients/test-patient-02/medications/import", json={"format": "fhir", "bundle": bundle}
    )
    assert wrong.json()["imported"] == 0 and all("not this patient" in i for i in wrong.json()["issues"])
    bad = clinician.post(
        "/patients/test-patient-01/medications/import",
        json={"format": "fhir", "bundle": {"resourceType": "Bundle"}},
    )
    assert bad.status_code == 422


def test_acknowledge_override_and_detected_issue(clinician: TestClient) -> None:
    alert = _alerts(clinician, "test-patient-04", "SAMPLE-MED-S006")["alerts"][0]
    assert sorted(alert["agents"]) == ["Glycyrrhizin", "Hydrochlorothiazide"]
    url = f"/interactions/{alert['id']}/acknowledge"
    assert clinician.post(url, json={"action": "override", "reason": ""}).status_code == 422
    ok = clinician.post(
        url, json={"action": "override", "reason": "Potassium monitored by nephrology"}
    ).json()
    assert ok["status"] == "overridden" and ok["decided_by"] == "clinician"
    assert clinician.post(url, json={"action": "acknowledge", "reason": "again"}).status_code == 409
    issues = clinician.get("/fhir/DetectedIssue", params={"patient": "test-patient-04"}).json()
    di = next(e["resource"] for e in issues["entry"] if e["resource"]["id"] == f"alert-{alert['id']}")
    assert di["code"]["coding"][0]["code"] == "DRG" and di["severity"] == "moderate"
    assert di["mitigation"][0]["action"]["text"].startswith("overridden")
    assert [i for i in validation_issues(di) if i["severity"] == "error"] == []
    events = clinician.get("/audit", params={"action": "override"}).json()["items"]
    assert events[0]["entity_id"] == str(alert["id"])
    with get_sessionmaker()() as s:
        s.execute(delete(InteractionAlert).where(InteractionAlert.patient_id == "test-patient-04"))
        s.commit()


def test_timeline_fhir_export_and_existing_interactions(clinician: TestClient) -> None:
    b = clinician.get("/fhir/Patient/test-patient-10/MedicationStatement").json()
    assert b["type"] == "collection" and len(b["entry"]) == 2
    assert [i for i in validation_issues(b) if i["severity"] == "error"] == []
    ms = b["entry"][0]["resource"]
    assert ms["contained"][0]["ingredient"][0]["itemCodeableConcept"]["text"] == "Methotrexate"
    tl = clinician.get("/patients/test-patient-10/medications/timeline", params={"as_of": AS_OF}).json()
    assert [sorted(a["agents"]) for a in tl["existing_alerts"]] == [["Methotrexate", "Pantoprazole"]]


def test_check_endpoint_with_patient_and_between_candidates(as_role: Callable[[str], TestClient]) -> None:
    viewer, clinician = as_role("viewer"), as_role("clinician")
    both = viewer.post(
        "/medications/check", json={"medication_codes": ["SAMPLE-MED-B004", "SAMPLE-MED-B002"]}
    ).json()
    assert [sorted(a["agents"]) for a in both["interactions"]["alerts"]] == [["Ibuprofen", "Methotrexate"]]
    forbidden = viewer.post(
        "/medications/check", json={"medication_codes": ["SAMPLE-MED-B001"], "patient_id": "test-patient-05"}
    )
    assert forbidden.status_code == 403
    with_patient = clinician.post(
        "/medications/check", json={"medication_codes": ["SAMPLE-MED-B003"], "patient_id": "test-patient-05"}
    ).json()
    assert with_patient["interactions"]["alerts"][0]["kind"] == "duplicate"


def test_patient_data_is_access_controlled(as_role: Callable[[str], TestClient]) -> None:
    for role in ("viewer", "coder"):
        c = as_role(role)
        assert c.get("/patients").status_code == 403
        assert c.get("/patients/test-patient-01/medications/timeline").status_code == 403
        assert c.get("/fhir/DetectedIssue", params={"patient": "test-patient-01"}).status_code == 403


def test_delete_history_then_restore(clinician: TestClient) -> None:
    _alerts(clinician, "test-patient-08", "SAMPLE-MED-B016")
    r = clinician.delete("/patients/test-patient-08/medications").json()
    assert r["deleted_medications"] == 1 and r["deleted_alerts"] == 2
    assert clinician.get("/patients/test-patient-08/medications/timeline").json()["items"] == []
    clinician.post(
        "/patients/test-patient-08/medications/import",
        json={"format": "fhir", "bundle": _synthetic_bundle("test-patient-08")},
    )
    with get_sessionmaker()() as s:
        assert s.scalar(select(PatientMedication.id).where(PatientMedication.patient_id == "test-patient-08"))


def test_medication_eval_endpoint(clinician: TestClient) -> None:
    r = clinician.get("/eval/medications").json()
    assert r["line_resolution"]["accuracy"] == 1.0
    det = r["interaction_detection"]
    assert det["precision"] == 1.0 and det["recall"] == 1.0
    assert det["safety"] == {"alerts_without_citation": 0, "no_interaction_without_dataset_caveat": 0}
