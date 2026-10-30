"""Clinician in the loop: queue -> decision -> approved mapping -> ConceptMap, $translate, Condition."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.core.config import SAMPLE_DIR
from app.db.models import AppSetting, ConceptLink, GoldMapping, Mapping, ReviewItem
from app.db.session import get_sessionmaker

NAMASTE = "http://localhost:8000/fhir/CodeSystem/namaste"


@pytest.fixture
def test_code() -> Iterator[GoldMapping]:
    """A held-out gold code (no approved mapping). Workflow artefacts for it are cleaned up afterwards."""
    with get_sessionmaker()() as s:
        g = s.scalars(
            select(GoldMapping).where(GoldMapping.split == "test").order_by(GoldMapping.namaste_code)
        ).first()
    assert g is not None
    yield g
    with get_sessionmaker()() as s:
        s.execute(delete(ReviewItem).where(ReviewItem.source_code == g.namaste_code))
        s.execute(
            delete(Mapping).where(
                Mapping.source_code == g.namaste_code, Mapping.reviewer != "official-crosswalk"
            )
        )
        s.commit()


def _param(params: dict[str, Any], name: str) -> Any:
    return next(
        (p.get("valueBoolean", p.get("part")) for p in params["parameter"] if p["name"] == name), None
    )


def test_coder_proposes_clinician_approves(
    as_role: Callable[[str], TestClient], test_code: GoldMapping
) -> None:
    coder, clinician = as_role("coder"), as_role("clinician")
    assert (
        _param(
            clinician.get(
                "/fhir/ConceptMap/$translate", params={"system": NAMASTE, "code": test_code.namaste_code}
            ).json(),
            "result",
        )
        is False
    )

    item = coder.post(
        "/review/propose", json={"namaste_code": test_code.namaste_code, "query_text": "x"}
    ).json()
    assert item["status"] == "pending" and item["reason"] == "proposed" and item["candidates"]
    # Idempotent: proposing again returns the same pending item.
    assert (
        coder.post("/review/propose", json={"namaste_code": test_code.namaste_code}).json()["id"]
        == item["id"]
    )
    queue = clinician.get("/review/queue").json()["items"]
    assert item["id"] in {i["id"] for i in queue}

    decided = clinician.post(f"/review/{item['id']}/decision", json={"action": "approve"}).json()
    assert decided["status"] == "approved" and decided["decided_by"] == "clinician"
    assert decided["decision"]["override"] is False and decided["mapping_id"]
    mapping = clinician.get(f"/mappings/{decided['mapping_id']}").json()
    assert (
        mapping["reviewer"] == "clinician"
        and mapping["version"] == 1
        and mapping["ranker_version"] == "ranker@1.0.0"
    )
    actions = [t["action"] for t in mapping["timeline"]]
    assert "approve" in actions  # provenance + ledger entries

    # The approved mapping flows straight into FHIR output.
    tr = clinician.get(
        "/fhir/ConceptMap/$translate", params={"system": NAMASTE, "code": test_code.namaste_code}
    ).json()
    assert _param(tr, "result") is True
    cond = clinician.post(
        "/fhir/Condition/$build",
        json={"namaste_code": test_code.namaste_code, "patient": {"abha_number": "91-TEST-0000-0009"}},
    )
    assert cond.status_code == 201 and "approved mapping" in cond.json()["note"][-1]["text"]
    # The decision cannot be taken twice.
    assert (
        clinician.post(
            f"/review/{item['id']}/decision", json={"action": "reject", "comment": "x"}
        ).status_code
        == 409
    )


def test_override_requires_comment_and_supersedes(
    as_role: Callable[[str], TestClient], test_code: GoldMapping
) -> None:
    clinician = as_role("clinician")
    first = clinician.post("/review/propose", json={"namaste_code": test_code.namaste_code}).json()
    clinician.post(f"/review/{first['id']}/decision", json={"action": "approve"})

    second = clinician.post("/review/propose", json={"namaste_code": test_code.namaste_code}).json()
    no_comment = clinician.post(f"/review/{second['id']}/decision", json={"action": "approve_wider"})
    assert no_comment.status_code == 422 and "comment is mandatory" in no_comment.json()["error"]["message"]
    manual = clinician.post(
        f"/review/{second['id']}/decision",
        json={
            "action": "choose_other",
            "tm2_code": "SAMPLE-TM2-0223",
            "relationship": "relatedto",
            "comment": "Manual search: closer match per clinical notes",
        },
    )
    assert manual.status_code == 200 and manual.json()["decision"]["override"] is True
    detail = clinician.get(f"/mappings/{manual.json()['mapping_id']}").json()
    assert detail["version"] == 2 and detail["target_code"] == "SAMPLE-TM2-0223"
    assert [h["status"] for h in detail["history"]] == ["approved", "superseded"]
    approved = clinician.get("/mappings", params={"source_code": test_code.namaste_code}).json()
    assert approved["total"] == 1

    bad = clinician.post("/review/propose", json={"namaste_code": test_code.namaste_code}).json()
    assert (
        clinician.post(
            f"/review/{bad['id']}/decision",
            json={"action": "choose_other", "tm2_code": "SAMPLE-TM2-0100", "comment": "category"},
        ).status_code
        == 422
    )
    rejected = clinician.post(
        f"/review/{bad['id']}/decision", json={"action": "reject", "comment": "no safe match"}
    )
    assert rejected.json()["status"] == "rejected" and rejected.json()["mapping_id"] is None


def test_medium_results_are_routed_to_review(client: TestClient, test_code: GoldMapping) -> None:
    # Force everything below 0.99 into the MEDIUM band.
    assert (
        client.put(
            "/admin/thresholds", json={"high": 1.0, "low": 0.05, "reason": "test medium routing"}
        ).status_code
        == 200
    )
    try:
        assert client.get("/admin/thresholds").json()["source"] == "admin"
        r = client.post("/map", json={"code": test_code.namaste_code}).json()
        assert r["status"] == "review" and r["review_item_id"]
        item = client.get(f"/review/{r['review_item_id']}").json()
        assert item["reason"] == "medium-band" and item["band"] == "medium"
        assert client.get("/review/queue", params={"band": "medium"}).json()["total"] >= 1
    finally:
        assert client.delete("/admin/thresholds").json() == {"source": "calibrated"}
    assert client.get("/admin/thresholds").json()["source"] == "calibrated"


def test_backfill_queues_every_unmapped_code(client: TestClient) -> None:
    with get_sessionmaker()() as s:
        test_codes = {
            g.namaste_code for g in s.scalars(select(GoldMapping).where(GoldMapping.split == "test"))
        }
    try:
        queued = client.post("/admin/review/backfill").json()["queued"]
        assert queued >= len(test_codes) - 1
        pending = {
            i["source_code"] for i in client.get("/review/queue", params={"limit": 200}).json()["items"]
        }
        assert test_codes <= pending
        assert client.post("/admin/review/backfill").json()["queued"] == 0  # idempotent
    finally:
        with get_sessionmaker()() as s:
            s.execute(delete(ReviewItem))
            s.commit()


def test_clinician_bio_link_enters_conceptmap(as_role: Callable[[str], TestClient]) -> None:
    clinician = as_role("clinician")
    with get_sessionmaker()() as s:  # other tests (e.g. the RBAC matrix) may have added clinician links
        s.execute(delete(ConceptLink).where(ConceptLink.origin == "clinician"))
        s.commit()
    before = clinician.get("/fhir/ConceptMap/tm2-to-biomedicine").json()
    r = clinician.post(
        "/mappings/bio-links",
        json={
            "tm2_code": "SAMPLE-TM2-0141",
            "bio_code": "SAMPLE-BIO-0018",
            "comment": "Cough disorder in asthma presentations",
        },
    )
    assert (
        r.status_code == 201 and r.json()["relationship"] == "relatedto" and r.json()["origin"] == "clinician"
    )
    after = clinician.get("/fhir/ConceptMap/tm2-to-biomedicine").json()
    assert int(after["version"]) == int(before["version"]) + 1
    assert "SAMPLE-TM2-0141" in {e["code"] for e in after["group"][0]["element"]}
    assert (
        clinician.post(
            "/mappings/bio-links",
            json={"tm2_code": "SAMPLE-TM2-0141", "bio_code": "NOPE", "comment": "x" * 5},
        ).status_code
        == 422
    )
    with get_sessionmaker()() as s:
        s.execute(delete(ConceptLink).where(ConceptLink.origin == "clinician"))
        s.commit()


def test_audit_records_real_users(as_role: Callable[[str], TestClient]) -> None:
    coder = as_role("coder")
    coder.post("/map", json={"text": "Kasa"})
    top = coder.get("/audit", params={"action": "map"}).json()["items"][0]
    assert top["actor"] == "coder" and top["role"] == "coder"
    assert coder.get("/audit/verify").json()["ok"] is True


def test_admin_ingest_upload_runs_task(client: TestClient, tmp_path: Any) -> None:
    text = (SAMPLE_DIR / "synonyms.yaml").read_text(encoding="utf-8")
    bad = client.post("/admin/ingest/synonyms", files={"file": ("x.txt", b"nope", "text/plain")})
    assert bad.status_code == 422
    same = client.post(
        "/admin/ingest/synonyms", files={"file": ("syn.yaml", text.encode(), "application/yaml")}
    )
    assert same.status_code == 202
    task = client.get(f"/admin/tasks/{same.json()['task_id']}").json()
    assert task["state"] == "SUCCESS" and task["result"]["status"] == "unchanged"
    icd = client.post("/admin/ingest/icd11").json()
    assert client.get(f"/admin/tasks/{icd['task_id']}").json()["result"]["mode"] == "mock"
    assert (
        client.post("/admin/ingest/namaste", files={"file": ("n.csv", b"a,b\n", "text/csv")}).status_code
        == 422
    )


def test_users_listing_has_no_secrets(client: TestClient) -> None:
    users = client.get("/admin/users").json()
    assert {u["username"] for u in users} >= {"admin", "clinician", "coder", "viewer"}
    assert all("password" not in str(u).lower() and "hash" not in str(u).lower() for u in users)
    with get_sessionmaker()() as s:
        assert s.get(AppSetting, "thresholds") is None
