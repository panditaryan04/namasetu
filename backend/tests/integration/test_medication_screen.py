"""POST /medications/screen: formulations screened against medicines listed by salt (no stored patient)."""

from __future__ import annotations

from collections.abc import Callable

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db.models import InteractionAlert
from app.db.session import get_sessionmaker


def _alert_rows() -> int:
    with get_sessionmaker()() as s:
        return int(s.scalar(select(func.count()).select_from(InteractionAlert)) or 0)


def test_screen_returns_cited_alerts_without_persisting(as_role: Callable[[str], TestClient]) -> None:
    c = as_role("viewer")
    before = _alert_rows()
    r = c.post(
        "/medications/screen",
        json={
            "candidate_codes": ["SAMPLE-MED-A008", "NOPE-1"],  # Nisha Amalaki Churna (Haridra -> curcumin)
            "active": [
                {"label": "Amaryl 1 (Hospital B)", "salts": ["Glimepiride"]},
                {"label": "Glycomet 500", "salts": ["metformin"]},  # case-insensitive
                {"label": "Mystery tablet", "salts": ["Unobtainium"]},
            ],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    pairs = {(a["agents"][0], a["agents"][1], a["severity"], a["other_medication"]) for a in body["alerts"]}
    assert ("Curcumin", "Glimepiride", "moderate", "Amaryl 1 (Hospital B)") in pairs
    assert ("Curcumin", "Metformin", "minor", "Glycomet 500") in pairs
    assert all(a["kind"] == "herb-drug" and a["citation"] and a["id"] is None for a in body["alerts"])
    assert body["unknown_codes"] == ["NOPE-1"]
    assert body["unresolved_salts"] == ["Unobtainium"]
    assert body["patient_id"] is None
    assert _alert_rows() == before  # nothing persisted


def test_screen_never_says_no_interactions(as_role: Callable[[str], TestClient]) -> None:
    body = (
        as_role("viewer")
        .post(
            "/medications/screen",
            json={
                "candidate_codes": ["SAMPLE-MED-A002"],
                "active": [{"label": "X", "salts": ["Amlodipine"]}],
            },
        )
        .json()
    )
    assert body["alerts"] == []
    assert "Absence of data is not proof of safety" in body["message"]


def test_screen_needs_sign_in_and_a_candidate(anon: TestClient, as_role: Callable[[str], TestClient]) -> None:
    assert anon.post("/medications/screen", json={"candidate_codes": ["SAMPLE-MED-A008"]}).status_code == 401
    assert as_role("viewer").post("/medications/screen", json={"candidate_codes": []}).status_code == 422
