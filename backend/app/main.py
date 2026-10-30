"""NamaSetu API entrypoint."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import (
    admin,
    audit,
    auth,
    evaluation,
    fhir,
    health,
    map,
    medications,
    patients,
    review,
    search,
)
from app.core.config import APP_VERSION, get_settings
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging
from app.core.middleware import RequestContextMiddleware
from app.db.session import get_sessionmaker
from app.engine.service import CodeNotInDatasetError, engine_holder

logger = logging.getLogger("namasetu.app")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    # Load the index, semantic vectors and ranker once at startup (not on the first user request).
    try:
        with get_sessionmaker()() as session:
            engine_holder.get(session)
    except Exception as exc:  # noqa: BLE001 - the API still serves /health and reports the reason
        logger.warning("engine warm-up failed: %s", exc)
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    insecure_default = settings.jwt_secret == "change-me-in-env"  # noqa: S105 - sentinel comparison
    if settings.app_env == "production" and insecure_default:
        raise RuntimeError("JWT_SECRET must be set in production")
    configure_logging(settings.log_level)

    app = FastAPI(
        lifespan=lifespan,
        title="NamaSetu API",
        version=APP_VERSION,
        description=(
            "NAMASTE ↔ WHO ICD-11 (TM2 + Biomedicine) terminology bridge with FHIR R4 output. "
            "Research / decision-support prototype — not a certified medical device."
        ),
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in settings.frontend_origin.split(",") if o.strip()],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
    )
    app.add_middleware(RequestContextMiddleware)
    register_error_handlers(app)

    @app.exception_handler(CodeNotInDatasetError)
    async def code_guard(_: Request, exc: CodeNotInDatasetError) -> JSONResponse:
        # Safety net: a code outside the ingested datasets is never returned to a caller.
        logger.error("blocked code outside dataset: %s", exc)
        return JSONResponse(
            status_code=500,
            content={
                "error": {"code": "code_validation_failed", "message": "Internal code validation failed"}
            },
        )

    app.include_router(health.router)
    app.include_router(admin.router)
    app.include_router(audit.router)
    app.include_router(search.router)
    app.include_router(map.router)
    app.include_router(fhir.public_router)
    app.include_router(fhir.router)
    app.include_router(auth.router)
    app.include_router(review.router)
    app.include_router(medications.router)
    app.include_router(patients.router)
    app.include_router(evaluation.router)
    return app


app = create_app()
