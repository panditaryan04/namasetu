"""Evaluation runs, the dashboard and exports.

- POST /eval/run (admin) runs the full harness as a background task; GET /eval/runs, /eval/runs/latest,
  /eval/runs/{id} read stored runs. Every number shown in the UI comes from a stored run.
- GET /dashboard: KPIs computed from the database and the loaded index, plus the latest eval summary.
- GET /export/{json|csv|pdf}?kind= returns small exports directly; larger ones become a background job
  (POST /export/jobs, GET /export/jobs/{id}, GET /export/jobs/{id}/download).
"""

from __future__ import annotations

import statistics
import threading
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.common import DbSession
from app.api.search import Engine
from app.core.ratelimit import rate_limit
from app.core.rbac import Admin, Reader
from app.db.models import (
    SYSTEM_BIO,
    SYSTEM_NAMASTE,
    SYSTEM_TM2,
    ConceptLink,
    EvalRun,
    ExportJob,
    Mapping,
    ReviewItem,
)
from app.engine.gate import band
from app.engine.service import MappingEngine
from app.eval.harness import latest_run, run_view
from app.workers.celery_app import celery_app
from app.workflow.exports import MEDIA_TYPES, ROWS, ExportFormat, ExportKind, header, job_view, render

router = APIRouter(tags=["evaluation"])
Heavy = Depends(rate_limit("heavy"))
EXPORT_SYNC_LIMIT = 5000  # rows; above this GET /export/{fmt} answers 202 with a background job

# Figures quoted in the team's SIH25026 pitch deck. Shown labelled as such; NOT computed by this app.
MAPPING_GAP = {
    "label": "Official figures as cited in the SIH25026 pitch deck — not computed by NamaSetu",
    "source": "Team Byte Strong pitch deck (SIH25026, Ministry of Ayush problem statement)",
    "bars": [
        {"key": "national_ayush_codes", "label": "National Ayush (NAMASTE) codes", "value": 7340},
        {"key": "formally_mapped", "label": "Formally mapped", "value": 1941},
        {"key": "tm2_categories", "label": "ICD-11 TM2 categories", "value": 529},
    ],
}


# ------------------------------------------------------------------ evaluation runs


class EvalRunAccepted(BaseModel):
    task_id: str
    state: str
    eval_run_id: int | None = None


@router.post("/eval/run", response_model=EvalRunAccepted, status_code=202, dependencies=[Heavy])
def start_eval(user: Admin) -> EvalRunAccepted:
    from app.workers.tasks import run_eval

    task = run_eval.delay(actor=user.username)
    result = task.result if task.ready() and isinstance(task.result, dict) else None
    return EvalRunAccepted(
        task_id=task.id, state=task.state, eval_run_id=result["eval_run_id"] if result else None
    )


@router.get("/eval/tasks/{task_id}", response_model=EvalRunAccepted)
def eval_task(task_id: str, _: Reader) -> EvalRunAccepted:
    res = celery_app.AsyncResult(task_id)
    result = res.result if res.successful() and isinstance(res.result, dict) else None
    return EvalRunAccepted(
        task_id=task_id, state=res.state, eval_run_id=result["eval_run_id"] if result else None
    )


@router.get("/eval/runs")
def list_eval_runs(_: Reader, db: DbSession, limit: int = 20) -> list[dict[str, Any]]:
    rows = db.scalars(select(EvalRun).order_by(EvalRun.id.desc()).limit(min(max(limit, 1), 100)))
    return [run_view(r, full=False) for r in rows]


@router.get("/eval/runs/latest")
def latest_eval_run(_: Reader, db: DbSession) -> dict[str, Any]:
    run = latest_run(db)
    if run is None:
        raise HTTPException(404, "no evaluation run yet: POST /eval/run (admin) or run scripts/run_eval.py")
    return run_view(run)


@router.get("/eval/runs/{run_id}")
def get_eval_run(run_id: int, _: Reader, db: DbSession) -> dict[str, Any]:
    run = db.get(EvalRun, run_id)
    if run is None:
        raise HTTPException(404, f"evaluation run {run_id} not found")
    return run_view(run)


# ------------------------------------------------------------------ dashboard

_band_cache: dict[str, Any] = {}
_band_lock = threading.Lock()


def band_distribution(engine: MappingEngine) -> dict[str, int]:
    """Band of the top suggestion for every NAMASTE concept; cached per index / ranker / thresholds."""
    key = (engine.index.version_key, engine.ranker.trained_at, engine.thresholds.high, engine.thresholds.low)
    with _band_lock:
        if _band_cache.get("key") == key:
            return dict(_band_cache["value"])
    counts: Counter[str] = Counter()
    for rec in engine.index.namaste:
        ranked = engine._rank(engine.matcher.profile_for_concept(rec))
        counts[band(ranked[0].confidence, engine.thresholds) if ranked else "low"] += 1
    value = {b: counts.get(b, 0) for b in ("high", "medium", "low")}
    with _band_lock:
        _band_cache.update(key=key, value=value)
    return value


def _throughput(db: Session, days: int = 14) -> list[dict[str, Any]]:
    since = datetime.now(UTC) - timedelta(days=days - 1)
    start = since.date()
    created = Counter(
        d.date() for d in db.scalars(select(ReviewItem.created_at).where(ReviewItem.created_at >= start))
    )
    decided = Counter(
        d.date()
        for d in db.scalars(select(ReviewItem.decided_at).where(ReviewItem.decided_at >= start))
        if d is not None
    )
    return [
        {
            "date": (start + timedelta(days=i)).isoformat(),
            "queued": created[start + timedelta(days=i)],
            "decided": decided[start + timedelta(days=i)],
        }
        for i in range(days)
    ]


def _hours(a: datetime, b: datetime) -> float:
    a = a if a.tzinfo else a.replace(tzinfo=UTC)
    b = b if b.tzinfo else b.replace(tzinfo=UTC)
    return (b - a).total_seconds() / 3600


@router.get("/dashboard")
def dashboard(_: Reader, engine: Engine, db: DbSession) -> dict[str, Any]:
    idx = engine.index
    approved = {
        m.source_code
        for m in db.scalars(
            select(Mapping).where(
                Mapping.status == "approved",
                Mapping.needs_review.is_(False),
                Mapping.source_system == SYSTEM_NAMASTE,
                Mapping.target_system == SYSTEM_TM2,
            )
        )
    }
    bands = band_distribution(engine)
    queue: dict[str, int] = {
        s: n for s, n in db.execute(select(ReviewItem.status, func.count()).group_by(ReviewItem.status))
    }
    decided = db.execute(
        select(ReviewItem.created_at, ReviewItem.decided_at).where(ReviewItem.decided_at.is_not(None))
    ).all()
    links: dict[str, int] = {
        s: n
        for s, n in db.execute(
            select(ConceptLink.status, func.count())
            .where(ConceptLink.target_system == SYSTEM_BIO)
            .group_by(ConceptLink.status)
        )
    }
    run = latest_run(db)
    return {
        "is_sample": idx.is_sample,
        "dataset_releases": idx.releases,
        "kpis": {
            "namaste_codes": len(idx.namaste),
            "tm2_codes": len(idx.tm2_targets),
            "biomedicine_codes": sum(1 for (s, _) in idx.concepts if s == SYSTEM_BIO),
            "mapped_approved": len(approved),
            "pending_review": queue.get("pending", 0),
            "no_match": bands["low"],
            "approved_biomedical_links": links.get("approved", 0),
            "pending_biomedical_links": links.get("pending", 0),
        },
        "band_distribution": bands,
        "band_note": "Band of the top suggestion for every NAMASTE concept, incl. Stage-0 approved mappings.",
        "queue": {
            "by_status": queue,
            "median_hours_to_decision": (
                round(statistics.median(_hours(c, d) for c, d in decided), 2) if decided else None
            ),
            "throughput": _throughput(db),
        },
        "mapping_gap": MAPPING_GAP,
        "latest_eval": run_view(run, full=False) if run else None,
    }


# ------------------------------------------------------------------ exports


class ExportRequest(BaseModel):
    kind: ExportKind
    format: ExportFormat


def _download(kind: str, fmt: str, filename: str, content: bytes) -> Response:
    return Response(
        content,
        media_type=MEDIA_TYPES[fmt],
        headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"},
    )


def _queue_export(db: Session, kind: str, fmt: str, actor: str) -> ExportJob:
    from app.workers.tasks import build_export_task

    job = ExportJob(created_by=actor, kind=kind, format=fmt, status="queued")
    db.add(job)
    db.commit()
    task = build_export_task.delay(job.id)
    db.refresh(job)
    if job.task_id is None:
        job.task_id = task.id
        db.commit()
    return job


@router.post("/export/jobs", status_code=202, dependencies=[Heavy])
def create_export_job(req: ExportRequest, user: Reader, db: DbSession) -> dict[str, Any]:
    return job_view(_queue_export(db, req.kind, req.format, user.username))


@router.get("/export/jobs")
def list_export_jobs(user: Reader, db: DbSession) -> list[dict[str, Any]]:
    q = select(ExportJob).order_by(ExportJob.id.desc()).limit(50)
    if user.role != "admin":
        q = q.where(ExportJob.created_by == user.username)
    return [job_view(j) for j in db.scalars(q)]


def _own_job(db: Session, job_id: int, username: str, role: str) -> ExportJob:
    job = db.get(ExportJob, job_id)
    if job is None or (role != "admin" and job.created_by != username):
        raise HTTPException(404, f"export job {job_id} not found")
    return job


@router.get("/export/jobs/{job_id}")
def get_export_job(job_id: int, user: Reader, db: DbSession) -> dict[str, Any]:
    return job_view(_own_job(db, job_id, user.username, user.role))


@router.get("/export/jobs/{job_id}/download")
def download_export(job_id: int, user: Reader, db: DbSession) -> Response:
    job = _own_job(db, job_id, user.username, user.role)
    if job.status != "done" or job.content is None or job.filename is None:
        raise HTTPException(409, f"export job {job_id} is {job.status}")
    return _download(job.kind, job.format, job.filename, job.content)


# Declared last so that /export/jobs is not captured by /export/{fmt}.
@router.get("/export/{fmt}", summary="Export mappings, review decisions or the audit ledger")
def export_now(fmt: ExportFormat, kind: ExportKind, user: Reader, db: DbSession) -> Response:
    rows = ROWS[kind](db)
    if len(rows) > EXPORT_SYNC_LIMIT:
        job = _queue_export(db, kind, fmt, user.username)
        return JSONResponse(job_view(job), status_code=202)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return _download(kind, fmt, f"namasetu-{kind}-{stamp}.{fmt}", render(kind, fmt, header(db, kind), rows))
