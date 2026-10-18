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


_JSON_PRIMITIVES = (str, int, float, bool, type(None))


def _json_safe(value, _depth: int = 0):
    """Coerce anything ``JSONResponse`` cannot serialize into a short string.

    A pydantic v2 error dict carries the offending ``input`` — for a body that
    failed to parse that is the RAW REQUEST BYTES, and for a form field it can
    be an ``UploadFile``/exception object living in ``ctx``. Serializing those
    raised ``TypeError: Object of type bytes is not JSON serializable`` inside
    the handler, which turned every client-side 422 into a server-side 500
    (observed in the dev lane on ``POST /api/v1/auth/login``).
    """
    if isinstance(value, _JSON_PRIMITIVES):
        return value
    if _depth >= 3:
        return str(value)[:200]
    if isinstance(value, dict):
        return {str(k): _json_safe(v, _depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(v, _depth + 1) for v in list(value)[:20]]
    return str(value)[:200]


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
        # Some FastAPI/pydantic versions put raw exception objects into ``ctx``
        # and the undecoded request BYTES into ``input`` — sanitize both so the
        # response stays JSON-serializable across versions.
        errors = []
        for err in exc.errors()[:20]:
            err = dict(err)
            if "ctx" in err:
                err["ctx"] = _json_safe(err["ctx"])
            if "input" in err:
                err["input"] = _json_safe(err["input"])
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
