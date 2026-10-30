"""Evaluation harness, safety checks (incl. that each check really fires), dashboard and exports."""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.session import get_sessionmaker
from app.engine.service import build_engine
from app.eval import safety
from app.eval.report import markdown, readme_block
from app.main import create_app
from app.medication.catalog import build_catalog


@pytest.fixture(scope="module")
def eval_run() -> Iterator[dict[str, Any]]:
    """One full evaluation through the API (Celery runs eagerly in LOCAL mode)."""
    from tests.conftest import auth_header

    with TestClient(create_app(), headers=auth_header("admin")) as c:
        r = c.post("/eval/run")
        assert r.status_code == 202, r.text
        run_id = r.json()["eval_run_id"]
        assert run_id is not None
        yield c.get(f"/eval/runs/{run_id}").json()


def test_eval_run_is_stored_with_versions_and_passes_safety(eval_run: dict[str, Any]) -> None:
    assert eval_run["safety_ok"] is True, eval_run["results"]["safety"]["examples"]
    assert eval_run["model_version"].startswith("ranker@")
    assert any(r.startswith("namaste@") for r in eval_run["dataset_releases"])
    assert any(r.startswith("medications@") for r in eval_run["dataset_releases"])
    res = eval_run["results"]
    assert set(res["intercomparison"]["systems"]) == {
        "Exact",
        "Lexical",
        "Transliteration + Lexical",
        "Semantic",
        "Hybrid",
    }
    assert {"no transliteration", "no hierarchy", "no semantic", "no char n-gram", "no root terms"} <= set(
        res["ablation"]
    )
    assert {"English", "IAST", "Devanagari", "Hinglish", "Typo (1 edit)", "Typo (2 edits)"} <= set(
        res["robustness"]
    )
    assert res["safety"]["scope"]["below_high_attempts"] > 0  # the refusal check is not vacuous
    assert all(v["errors"] == 0 for v in res["safety"]["fhir_conformance"].values())
    assert res["medication"]["brands"]["not in catalogue"]["false_resolutions"] == 0
    assert res["medication"]["interactions"]["by_kind"]["duplicate"]["true_positives"] >= 5
    # The report renders from the stored run.
    assert "## 5. Safety checks" in markdown(eval_run)
    assert "EVAL:START" in readme_block(eval_run)


def test_eval_endpoints_and_permissions(
    eval_run: dict[str, Any], as_role: Callable[[str], TestClient]
) -> None:
    viewer = as_role("viewer")
    assert viewer.post("/eval/run").status_code == 403
    runs = viewer.get("/eval/runs").json()
    assert runs and "results" not in runs[0]
    assert viewer.get("/eval/runs/latest").json()["id"] >= eval_run["id"]
    assert viewer.get("/eval/runs/999999").status_code == 404


class _DoseOut(BaseModel):
    dose: str
    frequency: str


def test_each_safety_check_fires_when_violated(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inject one violation per check family and confirm the counter goes above zero."""
    with get_sessionmaker()() as session:
        engine = build_engine(session, save_artifact=False)
        catalog = build_catalog(session)
        first = next(iter(catalog.by_code.values())).view
        first.source.title = " "  # an uncited medication
        first.disclaimer = "take freely"

        real_content = safety._content

        def leaky_content(s: Session, e: Any, cm_id: str) -> dict[str, Any]:
            cm = real_content(s, e, cm_id)
            if "namaste" in cm_id and cm.get("group"):
                cm["group"][0]["element"][0]["target"].append({"code": "SAMPLE-TM2-NOT-APPROVED"})
            return cm

        def permissive_build(*args: Any, **kwargs: Any) -> dict[str, Any]:
            return {"resourceType": "Condition"}

        real_check = safety.check

        def uncited_check(*args: Any, **kwargs: Any) -> Any:
            out = real_check(*args, **kwargs)
            for a in out.alerts:
                a.citation = ""
            if not out.alerts:
                out.message = "No interactions."
            return out

        monkeypatch.setattr(safety, "_content", leaky_content)
        monkeypatch.setattr(safety, "check", uncited_check)
        app = FastAPI()

        @app.get("/bad", response_model=_DoseOut)
        def bad() -> _DoseOut:  # pragma: no cover - only its schema matters
            return _DoseOut(dose="1 tab", frequency="BD")

        # Conditions built for approved mappings must still work; only the probe of non-high candidates is
        # made permissive, so wrap build_condition: approved requests go to the real builder.
        real_build = safety.build_condition

        def build(session: Session, engine: Any, req: Any, actor: str) -> dict[str, Any]:
            return permissive_build() if req.tm2_code else real_build(session, engine, req, actor)

        monkeypatch.setattr(safety, "build_condition", build)
        out = safety.safety_checks(session, engine, catalog, app)

    checks = out["checks"]
    for name in (
        "low_or_unapproved_in_conceptmap",
        "below_high_band_emitted_without_review",
        "medication_without_citation",
        "medication_without_disclaimer",
        "dose_fields_in_openapi",
        "no_interaction_without_caveat",
        "alert_without_citation",
        "fhir_conformance_errors",
    ):
        assert checks[name] > 0, name
    assert out["ok"] is False
    assert any("dose" in e for e in out["examples"]["dose_fields_in_openapi"])


def test_dose_field_pattern_allows_dosage_form() -> None:
    assert not safety.DOSE_FIELD.search("dosage_form")
    for bad in ("dose", "max_dose", "dosing", "regimen", "frequency", "dosage", "dosageInstruction"):
        assert safety.DOSE_FIELD.search(bad), bad


def test_dashboard(client: TestClient) -> None:
    d = client.get("/dashboard").json()
    k = d["kpis"]
    assert k["namaste_codes"] > 0 and k["tm2_codes"] > 0
    assert sum(d["band_distribution"].values()) == k["namaste_codes"]
    assert k["no_match"] == d["band_distribution"]["low"]
    gap = d["mapping_gap"]
    assert [b["value"] for b in gap["bars"]] == [7340, 1941, 529]
    assert "not computed by NamaSetu" in gap["label"]
    assert len(d["queue"]["throughput"]) == 14
    assert d["is_sample"] is True


def test_exports_sync(client: TestClient) -> None:
    r = client.get("/export/csv", params={"kind": "mappings"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    lines = r.text.splitlines()
    assert lines[0].startswith("# title:") and any(x.startswith("# dataset_releases:") for x in lines)
    assert any("Not a certified medical device" in x for x in lines[:6])
    table = list(csv.DictReader(io.StringIO("\n".join(x for x in lines if not x.startswith("#")))))
    assert table and {"source_code", "target_code", "status"} <= set(table[0])

    j = client.get("/export/json", params={"kind": "audit"}).json()
    assert j["header"]["dataset_releases"]["namaste"] and j["rows"][0]["hash"]
    assert j["header"]["sample"].startswith("SAMPLE")

    pdf = client.get("/export/pdf", params={"kind": "decisions"})
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    assert client.get("/export/xml", params={"kind": "audit"}).status_code == 422
    assert client.get("/export/json", params={"kind": "patients"}).status_code == 422


def test_export_jobs_are_private_to_their_creator(as_role: Callable[[str], TestClient]) -> None:
    coder, viewer = as_role("coder"), as_role("viewer")
    job = coder.post("/export/jobs", json={"kind": "mappings", "format": "pdf"}).json()
    assert job["status"] == "done" and job["download_url"] == f"/export/jobs/{job['id']}/download"
    dl = coder.get(job["download_url"])
    assert dl.status_code == 200 and dl.content.startswith(b"%PDF") and len(dl.content) == job["size"]
    assert any(j["id"] == job["id"] for j in coder.get("/export/jobs").json())
    assert viewer.get(f"/export/jobs/{job['id']}").status_code == 404
    assert viewer.get(job["download_url"]).status_code == 404
    assert all(j["id"] != job["id"] for j in viewer.get("/export/jobs").json())
    assert json.loads(coder.get("/export/json", params={"kind": "mappings"}).text)["rows"]
