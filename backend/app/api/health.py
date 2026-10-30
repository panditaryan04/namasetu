"""GET /health — liveness plus the active runtime modes shown in the UI header."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import func, select

from app.core.config import APP_VERSION, get_settings
from app.db.models import DatasetRelease
from app.db.session import check_database, get_sessionmaker
from app.engine.service import engine_holder

router = APIRouter(tags=["health"])


class ComponentStatus(BaseModel):
    ok: bool
    mode: str
    detail: str


class IndexStatus(BaseModel):
    loaded: bool
    detail: str
    ranker: str | None = None
    semantic_method: str | None = None
    thresholds: dict[str, float] | None = None


class Modes(BaseModel):
    DB_MODE: Literal["postgres", "sqlite-local"]
    TASK_MODE: Literal["celery-redis", "eager-local"]
    SEMANTIC_MODE: Literal["full", "onnx-int8", "off"]
    ICD11_MODE: Literal["live", "mock"]
    ABHA_MODE: Literal["live", "mock"]
    DATA_MODE: Literal["sample", "official"]


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    app: str
    version: str
    environment: str
    database: ComponentStatus
    redis: ComponentStatus
    index: IndexStatus
    modes: Modes
    dataset_versions: dict[str, str]
    disclaimer: str


DISCLAIMER = (
    "Research / decision-support prototype. Not a certified medical device. "
    "Mappings require clinician validation."
)


def _dataset_versions() -> tuple[dict[str, str], bool]:
    """Latest release tag per source, and whether any of them is SAMPLE data (or nothing is loaded)."""
    try:
        with get_sessionmaker()() as session:
            latest = select(func.max(DatasetRelease.id)).group_by(DatasetRelease.source).scalar_subquery()
            rows = session.scalars(select(DatasetRelease).where(DatasetRelease.id.in_(latest))).all()
    except Exception:  # noqa: BLE001 - schema may not be migrated yet
        return {}, True
    versions = {r.source: r.version for r in sorted(rows, key=lambda r: r.source)}
    return versions, (not rows) or any(r.is_sample for r in rows)


def _index_status() -> IndexStatus:
    engine = engine_holder.current
    if engine is None:
        return IndexStatus(loaded=False, detail=engine_holder.last_error or "not built yet")
    idx = engine.index
    sem = engine.semantic_status
    return IndexStatus(
        loaded=True,
        detail=(
            f"{len(idx.namaste)} NAMASTE / {len(idx.tm2_targets)} TM2 concepts; "
            f"semantic: {sem.method}{' (degraded: ' + sem.detail + ')' if sem.degraded else ''}"
        ),
        ranker=engine.ranker.version,
        semantic_method=sem.method,
        thresholds={"high": engine.thresholds.high, "low": engine.thresholds.low},
    )


def _check_redis(url: str | None) -> ComponentStatus:
    if not url:
        return ComponentStatus(
            ok=True, mode="eager-local", detail="Redis not configured; tasks run in-process"
        )
    try:
        import redis

        client = redis.Redis.from_url(url, socket_connect_timeout=1, socket_timeout=1)
        client.ping()
        return ComponentStatus(ok=True, mode="redis", detail="ok")
    except Exception as exc:  # noqa: BLE001 - health check reports any failure
        return ComponentStatus(ok=False, mode="redis", detail=type(exc).__name__)


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    settings = get_settings()
    db_ok, db_detail = check_database()
    redis_status = _check_redis(settings.redis_url)
    versions, is_sample = _dataset_versions() if db_ok else ({}, True)

    return HealthResponse(
        status="ok" if db_ok and redis_status.ok else "degraded",
        app=settings.app_name,
        version=APP_VERSION,
        environment=settings.app_env,
        database=ComponentStatus(ok=db_ok, mode=settings.db_mode, detail=db_detail),
        redis=redis_status,
        index=_index_status(),
        modes=Modes(
            DB_MODE=settings.db_mode,
            TASK_MODE=settings.task_mode,
            SEMANTIC_MODE=settings.semantic_mode,
            ICD11_MODE=settings.effective_icd11_mode,
            ABHA_MODE=settings.effective_abha_mode,
            DATA_MODE="sample" if is_sample else "official",
        ),
        dataset_versions=versions,
        disclaimer=DISCLAIMER,
    )
