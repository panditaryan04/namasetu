"""Run the whole evaluation and store it as an EvalRun (with dataset releases + model version)."""

from __future__ import annotations

import time
from typing import Any

from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import EvalRun
from app.engine.service import build_engine
from app.eval.calibration import calibration
from app.eval.intercomparison import ablation, engine_for, gold_rows, intercomparison, robustness
from app.eval.medication import brand_resolution, ingredient_names, interaction_detection, line_resolution
from app.eval.safety import safety_checks
from app.medication.catalog import build_catalog
from app.workflow.exports import header


def run_all(session: Session, app: FastAPI) -> dict[str, Any]:
    t0 = time.perf_counter()
    test, all_gold = gold_rows(session, "test"), gold_rows(session, None)
    timings: dict[str, float] = {}

    def timed(name: str, fn: Any, *args: Any) -> Any:
        t = time.perf_counter()
        out = fn(*args)
        timings[name] = round(time.perf_counter() - t, 2)
        return out

    production = build_engine(session, save_artifact=False)
    catalog = build_catalog(session)
    results: dict[str, Any] = {
        "split": {
            "test": len(test),
            "train": sum(g.split == "train" for g in all_gold),
            "note": "Stage-0 crosswalk baseline disabled for every system; the ranker never sees TEST rows.",
        },
        "intercomparison": timed("intercomparison", intercomparison, session, test),
        "ablation": timed("ablation", ablation, session, test),
        "robustness": timed("robustness", robustness, session, test),
        "robustness_all_gold": timed("robustness_all_gold", robustness, session, all_gold),
        "calibration": timed("calibration", calibration, engine_for(session), test),
        "safety": timed("safety", safety_checks, session, production, catalog, app),
        "medication": {
            "brands": timed("brands", brand_resolution, catalog),
            "ingredient_names": timed("ingredient_names", ingredient_names, session, catalog),
            "line_resolution": timed("line_resolution", line_resolution, session, catalog),
            "interactions": timed("interactions", interaction_detection, session, catalog),
        },
    }
    med_safety = results["medication"]["interactions"]["safety"]
    results["safety"]["checks"].update({f"labelled_cases_{k}": v for k, v in med_safety.items()})
    results["safety"]["ok"] = all(v == 0 for v in results["safety"]["checks"].values())
    results["timings_s"] = timings
    hybrid = results["intercomparison"]["systems"]["Hybrid"]
    meds = results["medication"]
    summary = {
        "test_queries": len(test),
        "hybrid_top1": hybrid["top1"],
        "hybrid_top5": hybrid["top5"],
        "hybrid_mrr": hybrid["mrr"],
        "high_band_precision": hybrid.get("high_band_precision"),
        "review_queue_rate": hybrid.get("review_queue_rate"),
        "exact_top1": results["intercomparison"]["systems"]["Exact"]["top1"],
        "ece": results["calibration"]["test_expected_calibration_error"],
        "safety_failures": sum(results["safety"]["checks"].values()),
        "fhir_resources": sum(r["resources"] for r in results["safety"]["fhir_conformance"].values()),
        "fhir_errors": sum(r["errors"] for r in results["safety"]["fhir_conformance"].values()),
        "brand_typo_accuracy": meds["brands"]["typo (1 edit)"]["accuracy"],
        "brand_false_resolutions": meds["brands"]["not in catalogue"]["false_resolutions"],
        "line_resolution_accuracy": meds["line_resolution"]["accuracy"],
        "interaction_precision": meds["interactions"]["precision"],
        "interaction_recall": meds["interactions"]["recall"],
        "semantic_method": results["intercomparison"]["semantic_method"],
        "is_sample": production.index.is_sample,
    }
    return {
        "summary": summary,
        "results": results,
        "dataset_releases": [f"{k}@{v}" for k, v in header(session, "mappings")["dataset_releases"].items()],
        "model_version": production.ranker.version,
        "safety_ok": results["safety"]["ok"],
        "duration_s": round(time.perf_counter() - t0, 2),
    }


def store(session: Session, out: dict[str, Any], actor: str) -> EvalRun:
    run = EvalRun(
        created_by=actor,
        dataset_releases=out["dataset_releases"],
        model_version=out["model_version"],
        duration_s=out["duration_s"],
        safety_ok=out["safety_ok"],
        summary=out["summary"],
        results=out["results"],
    )
    session.add(run)
    session.commit()
    return run


def latest_run(session: Session) -> EvalRun | None:
    return session.scalars(select(EvalRun).order_by(EvalRun.id.desc()).limit(1)).first()


def run_view(run: EvalRun, full: bool = True) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": run.id,
        "created_at": run.created_at.isoformat(),
        "created_by": run.created_by,
        "dataset_releases": run.dataset_releases,
        "model_version": run.model_version,
        "duration_s": run.duration_s,
        "safety_ok": run.safety_ok,
        "summary": run.summary,
    }
    if full:
        out["results"] = run.results
    return out
