"""Consistent error envelope: {"error": {"code", "message", "details", "request_id"}}."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import request_id_var

logger = logging.getLogger("namasetu.errors")


class ErrorBody(BaseModel):
    code: str
    message: str
    details: Any | None = None
    request_id: str


class ErrorEnvelope(BaseModel):
    error: ErrorBody


def _envelope(status: int, code: str, message: str, details: Any = None) -> JSONResponse:
    body = ErrorEnvelope(
        error=ErrorBody(code=code, message=message, details=details, request_id=request_id_var.get())
    )
    return JSONResponse(status_code=status, content=body.model_dump())


def _is_fhir(request: Request) -> bool:
    return request.url.path.startswith("/fhir")


def _outcome(
    status: int,
    code: str,
    diagnostics: str,
    extra: list[dict[str, Any]] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    from app.fhir.common import FHIRResponse, issue, operation_outcome

    issues = [issue("error", code, diagnostics)] + (extra or [])
    return FHIRResponse(operation_outcome(issues), status_code=status, headers=headers)


FHIR_ISSUE_CODE = {401: "login", 403: "forbidden", 404: "not-found", 429: "throttled"}


def register_error_handlers(app: FastAPI) -> None:
    from app.fhir.common import FHIRError, issue
    from app.fhir.validator import FHIRValidationError

    @app.exception_handler(FHIRError)
    async def fhir_error(_: Request, exc: FHIRError) -> JSONResponse:
        from app.fhir.common import FHIRResponse, operation_outcome

        return FHIRResponse(
            operation_outcome([issue(exc.severity, exc.code, exc.diagnostics)]), status_code=exc.status
        )

    @app.exception_handler(FHIRValidationError)
    async def fhir_invalid_output(_: Request, exc: FHIRValidationError) -> JSONResponse:
        # Our own output failed validation: refuse to send it.
        logger.error("refused to emit invalid FHIR: %s", exc)
        return _outcome(500, "exception", "Generated resource failed FHIR validation", exc.issues)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        headers = dict(exc.headers) if exc.headers else None
        if _is_fhir(request):
            code = FHIR_ISSUE_CODE.get(exc.status_code, "processing")
            return _outcome(exc.status_code, code, str(exc.detail), headers=headers)
        resp = _envelope(exc.status_code, f"http_{exc.status_code}", str(exc.detail))
        if headers:
            resp.headers.update(headers)
        return resp

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # ctx may hold the raw exception from a validator; render it as text so the envelope is valid JSON.
        details = jsonable_encoder(exc.errors(), custom_encoder={Exception: str})
        if _is_fhir(request):
            extra = [
                issue("error", "invalid", f"{'.'.join(map(str, d['loc']))}: {d['msg']}") for d in details
            ]
            return _outcome(422, "invalid", "Request validation failed", extra)
        return _envelope(422, "validation_error", "Request validation failed", details)

    @app.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error")
        return _envelope(500, "internal_error", "Internal server error")
