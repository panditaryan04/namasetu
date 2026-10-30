"""Background tasks. In LOCAL mode they run eagerly in-process (see celery_app.py)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from celery import Task

from app.core.config import SAMPLE_DIR, get_settings
from app.db.models import SOURCE_ICD11, SOURCE_NAMASTE, SYSTEM_BIO, SYSTEM_NAMASTE, SYSTEM_TM2
from app.db.session import get_sessionmaker
from app.ingest.icd11_client import get_icd11_client
from app.ingest.namaste_loader import NamasteColumnMap, load_namaste_file
from app.ingest.release import ReleaseResult, ingest_concepts, ingest_synonyms
from app.ingest.synonyms_loader import load_synonyms_file
from app.workers.celery_app import celery_app


def _summary(result: ReleaseResult) -> dict[str, Any]:
    return {
        "release_id": result.release.id,
        "release": result.release.tag,
        "status": "unchanged" if result.skipped else "ingested",
        "row_count": result.release.row_count,
        "diff": {k: len(v) if isinstance(v, list) else v for k, v in result.diff.items()},
    }


@celery_app.task(name="namasetu.ping")
def ping() -> str:
    return "pong"


@celery_app.task(bind=True, name="namasetu.ingest_icd11")
def ingest_icd11(self: Task, actor: str = "system") -> dict[str, Any]:
    settings = get_settings()
    client = get_icd11_client(settings, SAMPLE_DIR)

    def progress(done: int, message: str) -> None:
        self.update_state(state="PROGRESS", meta={"done": done, "message": message})

    snap = client.fetch(progress)
    with get_sessionmaker()() as session:
        result = ingest_concepts(
            session,
            source=SOURCE_ICD11,
            version=snap.release,
            systems=[SYSTEM_TM2, SYSTEM_BIO],
            concepts=snap.tm2 + snap.bio,
            links=snap.links,
            report=snap.report,
            is_sample=snap.is_sample,
            actor=actor,
        )
        session.commit()
        return {**_summary(result), "mode": snap.mode}


@celery_app.task(name="namasetu.ingest_namaste")
def ingest_namaste(
    path: str, version: str, column_map_path: str | None = None, actor: str = "system"
) -> dict[str, Any]:
    cmap = NamasteColumnMap.from_yaml(Path(column_map_path)) if column_map_path else None
    concepts, report = load_namaste_file(Path(path), cmap)
    with get_sessionmaker()() as session:
        result = ingest_concepts(
            session,
            source=SOURCE_NAMASTE,
            version=version,
            systems=[SYSTEM_NAMASTE],
            concepts=concepts,
            report=report,
            is_sample=all(c.code.startswith("SAMPLE-") for c in concepts),
            actor=actor,
        )
        session.commit()
        return {**_summary(result), "rejected_rows": len(report.rejected)}


@celery_app.task(name="namasetu.ingest_synonyms")
def ingest_synonyms_file(path: str, actor: str = "system") -> dict[str, Any]:
    synonyms, report = load_synonyms_file(Path(path))
    with get_sessionmaker()() as session:
        result = ingest_synonyms(
            session, synonyms, report, is_sample=synonyms.version.startswith("SAMPLE"), actor=actor
        )
        session.commit()
        return _summary(result)


@celery_app.task(name="namasetu.ingest_medication_table")
def ingest_medication_table(table: str, path: str, version: str, actor: str = "system") -> dict[str, Any]:
    """Replace one table (formulations|drugs|brands|links) of the medication bundle and re-ingest."""
    import csv
    import io

    from app.medication.ingest import BUNDLE_KEYS, current_bundle, ingest_medications

    if table not in BUNDLE_KEYS:
        raise ValueError(f"unknown medication table {table}")
    rows = list(csv.DictReader(io.StringIO(Path(path).read_text(encoding="utf-8-sig"))))
    with get_sessionmaker()() as session:
        bundle = dict(current_bundle(session) or {k: [] for k in BUNDLE_KEYS})
        bundle[table] = rows
        result = ingest_medications(
            session, bundle, version=version, is_sample=version.startswith("SAMPLE"), actor=actor
        )
        session.commit()
        return _summary(result)


@celery_app.task(name="namasetu.run_eval")
def run_eval(actor: str = "system") -> dict[str, Any]:
    """Full evaluation (intercomparison, ablation, robustness, calibration, safety); stored as an EvalRun."""
    from app.eval.harness import run_all, store
    from app.main import create_app

    with get_sessionmaker()() as session:
        out = run_all(session, create_app())
        run = store(session, out, actor)
        return {"eval_run_id": run.id, "safety_ok": run.safety_ok, "summary": run.summary}


@celery_app.task(name="namasetu.build_export")
def build_export_task(job_id: int) -> dict[str, Any]:
    from app.db.models import ExportJob
    from app.workflow.exports import build_export, job_view

    with get_sessionmaker()() as session:
        job = session.get(ExportJob, job_id)
        if job is None:
            raise ValueError(f"export job {job_id} not found")
        try:
            build_export(session, job)
        except Exception as exc:
            session.rollback()
            job = session.get(ExportJob, job_id)
            assert job is not None
            job.status, job.error = "failed", str(exc)[:500]
            session.commit()
            raise
        session.commit()
        return job_view(job)
