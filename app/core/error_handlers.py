"""Global exception handlers (W2, parity plan).

Replaces the bare 500 handler in main.py: every error response carries
``{code, message, detail, request_id}`` (+ ``Retry-After`` when applicable),
StarletteHTTPExceptions keep backward compatibility via the ``detail`` field,
and unknown exceptions log server-side with the request_id for correlation.
"""
from __future__ import annotations

import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.errors import CDSError

logger = logging.getLogger(__name__)


def _request_id(request: Request) -> str:
    rid = getattr(getattr(request, "state", None), "request_id", None)
    return rid or uuid.uuid4().hex[:16]


def _error_body(code: str, message: str, detail: dict | None, request_id: str) -> dict:
    body = {"code": code, "message": message, "request_id": request_id}
    if detail:
        body["detail"] = detail
    return body


def _error_headers(request_id: str, retry_after: int | None = None) -> dict:
    """Error responses always self-identify their request_id — an unhandled
    exception bypasses RequestIDMiddleware's response path, so the handler
    must set the header itself."""
    headers = {"X-Request-ID": request_id}
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    return headers


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(CDSError)
    async def cds_error_handler(request: Request, exc: CDSError):
        request_id = _request_id(request)
        logger.error(
            "CDSError %s: %s", exc.code, exc.message,
            extra={"request_id": request_id, "detail": exc.detail},
        )
        headers = _error_headers(request_id, exc.retry_after)
        return JSONResponse(
            status_code=exc.http_status,
            content=_error_body(exc.code, exc.message, exc.detail, request_id),
            headers=headers,
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        """Keep the legacy ``detail`` field for backward compatibility."""
        request_id = _request_id(request)
        detail = exc.detail if isinstance(exc.detail, (str, dict, list)) else str(exc.detail)
        body = _error_body(f"HTTP_{exc.status_code}", str(detail), None, request_id)
        body["detail"] = detail  # legacy field — existing clients assert on it
        headers = _error_headers(request_id)
        headers.update(getattr(exc, "headers", None) or {})
        return JSONResponse(status_code=exc.status_code, content=body, headers=headers)

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        request_id = _request_id(request)
        # Some FastAPI versions put raw exception objects into ctx — sanitize
        # so the response stays JSON-serializable across versions.
        errors = []
        for err in exc.errors()[:20]:
            err = dict(err)
            ctx = err.get("ctx")
            if isinstance(ctx, dict):
                err["ctx"] = {k: str(v) for k, v in ctx.items()}
            errors.append(err)
        body = _error_body("VALIDATION_ERROR", "Request validation failed", None, request_id)
        body["detail"] = errors  # legacy FastAPI 422 shape: detail is the error list
        return JSONResponse(status_code=422, content=body, headers=_error_headers(request_id))

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        request_id = _request_id(request)
        logger.error(
            "Unhandled exception: %s", exc, exc_info=True, extra={"request_id": request_id}
        )
        return JSONResponse(
            status_code=500,
            content=_error_body("INTERNAL_ERROR", "Internal server error", None, request_id),
            headers=_error_headers(request_id),
        )
