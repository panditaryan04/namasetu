"""API tests against the session app DB (migrated + seeded from embedded_db.json)."""

from __future__ import annotations

import subprocess
import sys

from fastapi.testclient import TestClient

from app.core.config import BACKEND_ROOT


def test_health_reports_dataset_versions(client: TestClient) -> None:
    body = client.get("/health").json()
    assert body["dataset_versions"] == {
        "gold": "SAMPLE-1",
        "icd11": "SAMPLE-1",
        "namaste": "SAMPLE-1",
        "synonyms": "SAMPLE-1",
        "medications": "SAMPLE-1",
        "interactions": "SAMPLE-1",
    }
    assert body["modes"]["DATA_MODE"] == "sample"


def test_list_and_get_releases(client: TestClient) -> None:
    page = client.get("/admin/releases", params={"limit": 2}).json()
    assert page["total"] == 6 and page["limit"] == 2 and len(page["items"]) == 2
    assert page["items"][0]["tag"] == "interactions@SAMPLE-1"  # newest first
    namaste = client.get("/admin/releases", params={"source": "namaste"}).json()["items"][0]
    assert namaste["is_sample"] and namaste["diff_summary"]["added"] == 64

    detail = client.get(f"/admin/releases/{namaste['id']}").json()
    assert detail["report"]["accepted"] == 64 and detail["report"]["rejected"] == []

    diff = client.get(f"/admin/releases/{namaste['id']}/diff").json()
    assert diff["release"] == "namaste@SAMPLE-1" and diff["previous"] is None
    assert "SAMPLE-AYU-0001" in diff["diff"]["added"]


def test_release_404_uses_error_envelope(client: TestClient) -> None:
    resp = client.get("/admin/releases/9999")
    assert resp.status_code == 404
    assert resp.json()["error"]["message"] == "release 9999 not found"


def test_pagination_is_validated(client: TestClient) -> None:
    assert client.get("/admin/releases", params={"limit": 0}).status_code == 422


def test_audit_list_and_verify(client: TestClient) -> None:
    page = client.get("/audit", params={"action": "ingest"}).json()
    assert page["total"] == 6
    assert page["items"][0]["entity_id"] == "interactions@SAMPLE-1"
    verify = client.get("/audit/verify").json()
    assert verify["ok"] is True and verify["checked"] >= 4


def test_embedded_seed_is_in_sync_with_sample_files() -> None:
    proc = subprocess.run(
        [sys.executable, str(BACKEND_ROOT / "scripts" / "build_embedded_db.py"), "--check"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout
