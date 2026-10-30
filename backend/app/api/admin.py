"""Admin: dataset releases, ingest (Celery tasks with progress), thresholds, users, review backfill."""

from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.common import DbSession, Page, Pagination
from app.api.search import Engine
from app.core.config import BACKEND_ROOT
from app.core.rbac import Admin, Reader
from app.db.models import AppSetting, DatasetRelease, User
from app.ledger.hash_chain import append_event
from app.workers.celery_app import celery_app
from app.workers.tasks import ingest_icd11, ingest_medication_table, ingest_namaste, ingest_synonyms_file
from app.workflow.review import enqueue_unmapped

UPLOAD_DIR = BACKEND_ROOT / "data" / "uploads"
MAX_UPLOAD = 10 * 1024 * 1024
ALLOWED_SUFFIX = {
    "namaste": {".csv", ".xlsx"},
    "synonyms": {".yaml", ".yml"},
    "formulations": {".csv"},
    "drugs": {".csv"},
    "brands": {".csv"},
    "med-links": {".csv"},
}
MEDICATION_TABLES = {
    "formulations": "formulations",
    "drugs": "drugs",
    "brands": "brands",
    "med-links": "links",
}

router = APIRouter(prefix="/admin", tags=["admin"])


class ReleaseOut(BaseModel):
    id: int
    source: str
    version: str
    tag: str
    checksum: str
    row_count: int
    is_sample: bool
    ingested_at: datetime
    ingested_by: str
    previous_id: int | None
    diff_summary: dict[str, Any]

    @classmethod
    def of(cls, r: DatasetRelease) -> ReleaseOut:
        return cls(
            id=r.id,
            source=r.source,
            version=r.version,
            tag=r.tag,
            checksum=r.checksum,
            row_count=r.row_count,
            is_sample=r.is_sample,
            ingested_at=r.ingested_at,
            ingested_by=r.ingested_by,
            previous_id=r.previous_id,
            diff_summary={k: len(v) if isinstance(v, list) else v for k, v in (r.diff or {}).items()},
        )


class ReleaseDetail(ReleaseOut):
    report: dict[str, Any]


class ReleaseDiff(BaseModel):
    release: str
    previous: str | None
    diff: dict[str, Any]


def _get(db: Session, release_id: int) -> DatasetRelease:
    release = db.get(DatasetRelease, release_id)
    if release is None:
        raise HTTPException(status_code=404, detail=f"release {release_id} not found")
    return release


@router.get("/releases", response_model=Page[ReleaseOut])
def list_releases(_: Reader, page: Pagination, db: DbSession, source: str | None = None) -> Page[ReleaseOut]:
    query = select(DatasetRelease)
    if source:
        query = query.where(DatasetRelease.source == source)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(query.order_by(DatasetRelease.id.desc()).limit(page.limit).offset(page.offset)).all()
    return Page(items=[ReleaseOut.of(r) for r in rows], total=total, limit=page.limit, offset=page.offset)


@router.get("/releases/{release_id}", response_model=ReleaseDetail)
def get_release(release_id: int, _: Reader, db: DbSession) -> ReleaseDetail:
    r = _get(db, release_id)
    return ReleaseDetail(**ReleaseOut.of(r).model_dump(), report=r.report or {})


@router.get("/releases/{release_id}/diff", response_model=ReleaseDiff)
def get_release_diff(release_id: int, _: Reader, db: DbSession) -> ReleaseDiff:
    r = _get(db, release_id)
    diff = dict(r.diff or {})
    previous = diff.pop("previous", None)
    return ReleaseDiff(release=r.tag, previous=previous, diff=diff)


# ------------------------------------------------------------------ ingest (Celery)


class TaskOut(BaseModel):
    task_id: str
    state: str
    meta: dict[str, Any] | None = None
    result: dict[str, Any] | None = None


@router.post("/ingest/{source}", response_model=TaskOut, status_code=202)
async def ingest(
    source: Literal["namaste", "icd11", "synonyms", "formulations", "drugs", "brands", "med-links"],
    user: Admin,
    file: Annotated[UploadFile | None, File(description="CSV/XLSX (namaste) or YAML (synonyms)")] = None,
    version: Annotated[str | None, Form(max_length=64, pattern=r"^[A-Za-z0-9._\-]+$")] = None,
) -> TaskOut:
    """Start an ingest. ICD-11 needs no file (LIVE API or MOCK sample); the others take an upload."""
    if source == "icd11":
        task = ingest_icd11.delay(actor=user.username)
        return TaskOut(task_id=task.id, state=task.state)
    if file is None or not file.filename:
        raise HTTPException(status_code=422, detail=f"{source} ingest needs a file upload")
    suffix = Path(file.filename).suffix.lower()
    if suffix not in ALLOWED_SUFFIX[source]:
        raise HTTPException(status_code=422, detail=f"{source} accepts {sorted(ALLOWED_SUFFIX[source])}")
    data = await file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise HTTPException(status_code=413, detail="upload exceeds 10 MB")
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    path = UPLOAD_DIR / f"{uuid.uuid4().hex}{suffix}"  # never trust the client filename
    path.write_bytes(data)
    if source in MEDICATION_TABLES:
        if not version:
            raise HTTPException(status_code=422, detail="medication ingest needs a 'version' form field")
        task = ingest_medication_table.delay(
            MEDICATION_TABLES[source], str(path), version, actor=user.username
        )
        return TaskOut(task_id=task.id, state=task.state, result=task.result if task.ready() else None)
    if source == "namaste":
        if not version:
            raise HTTPException(status_code=422, detail="namaste ingest needs a 'version' form field")
        task = ingest_namaste.delay(str(path), version, actor=user.username)
    else:
        task = ingest_synonyms_file.delay(str(path), actor=user.username)
    return TaskOut(task_id=task.id, state=task.state, result=task.result if task.ready() else None)


@router.get("/tasks/{task_id}", response_model=TaskOut)
def task_status(task_id: str, _: Admin) -> TaskOut:
    res = celery_app.AsyncResult(task_id)
    info = res.info if isinstance(res.info, dict) else None
    if res.failed():
        return TaskOut(task_id=task_id, state=res.state, meta={"error": str(res.info)})
    return TaskOut(
        task_id=task_id,
        state=res.state,
        meta=None if res.ready() else info,
        result=res.result if res.successful() else None,
    )


# ------------------------------------------------------------------ thresholds


class ThresholdsIn(BaseModel):
    high: float = Field(ge=0.5, le=1.0)
    low: float = Field(ge=0.0, le=1.0)
    reason: str = Field(min_length=3, max_length=500)

    @model_validator(mode="after")
    def _ordered(self) -> ThresholdsIn:
        if self.low >= self.high:
            raise ValueError("low must be below high")
        return self


@router.get("/thresholds")
def get_thresholds(_: Reader, engine: Engine) -> dict[str, Any]:
    return {**engine.thresholds.model_dump(), "calibrated": engine.ranker.thresholds.model_dump()}


@router.put("/thresholds")
def put_thresholds(req: ThresholdsIn, user: Admin, db: DbSession) -> dict[str, Any]:
    row = db.get(AppSetting, "thresholds")
    before = row.value if row else None
    value = {"high": req.high, "low": req.low, "reason": req.reason}
    if row is None:
        db.add(AppSetting(key="thresholds", value=value, version=1, updated_by=user.username))
    else:
        row.value, row.version, row.updated_by = value, row.version + 1, user.username
    append_event(
        db,
        actor=user.username,
        role=user.role,
        action="edit",
        entity_type="Thresholds",
        entity_id="confidence-gate",
        before=before,
        after=value,
    )
    db.commit()
    return {"high": req.high, "low": req.low, "source": "admin"}


@router.delete("/thresholds")
def reset_thresholds(user: Admin, db: DbSession) -> dict[str, str]:
    row = db.get(AppSetting, "thresholds")
    if row is not None:
        append_event(
            db,
            actor=user.username,
            role=user.role,
            action="edit",
            entity_type="Thresholds",
            entity_id="confidence-gate",
            before=row.value,
            after={"reset": "calibrated"},
        )
        db.delete(row)
        db.commit()
    return {"source": "calibrated"}


# ------------------------------------------------------------------ users, review backfill


class UserOut(BaseModel):
    username: str
    display_name: str
    role: str
    active: bool
    abha_address: str | None


@router.get("/users", response_model=list[UserOut])
def users(_: Admin, db: DbSession) -> list[UserOut]:
    return [
        UserOut(
            username=u.username,
            display_name=u.display_name,
            role=u.role,
            active=u.active,
            abha_address=u.abha_address,
        )
        for u in db.scalars(select(User).order_by(User.username))
    ]


@router.post("/review/backfill")
def backfill(user: Admin, engine: Engine, db: DbSession) -> dict[str, int]:
    """Queue every NAMASTE code without an approved mapping (or flagged for re-review)."""
    queued = enqueue_unmapped(db, engine, actor=user.username)
    db.commit()
    return {"queued": queued}
