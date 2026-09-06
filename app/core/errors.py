"""CDS error contract (W2, parity plan).

Machine-readable error codes with precise HTTP semantics (aligned with
CubeSandbox C2): 408 sync timeout, 409 state conflict, 410 GONE for terminal
states (clients STOP retrying), 503+Retry-After for transient locks, 429 for
rate limits. Every response carries the request_id from W1 telemetry.

The full catalog with client guidance lives in ``docs/error-codes.md``.
"""
from __future__ import annotations

from typing import Any


class CDSError(Exception):
    """Base class for contract errors raised by services/handlers."""

    code: str = "INTERNAL_ERROR"
    http_status: int = 500
    retry_after: int | None = None

    def __init__(
        self,
        message: str,
        *,
        detail: dict[str, Any] | None = None,
        code: str | None = None,
        http_status: int | None = None,
        retry_after: int | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.detail = detail or {}
        if code is not None:
            self.code = code
        if http_status is not None:
            self.http_status = http_status
        if retry_after is not None:
            self.retry_after = retry_after


class SessionNotFound(CDSError):
    code = "SESSION_NOT_FOUND"
    http_status = 404


class SessionTerminated(CDSError):
    """Terminal state — clients must stop retrying (410 Gone)."""

    code = "SESSION_TERMINATED"
    http_status = 410


class SessionStateConflict(CDSError):
    code = "SESSION_STATE_CONFLICT"
    http_status = 409


class OperationLocked(CDSError):
    """Transient lock — retry after the advertised delay (503 + Retry-After)."""

    code = "OPERATION_LOCKED"
    http_status = 503
    retry_after = 2


class RateLimited(CDSError):
    code = "RATE_LIMITED"
    http_status = 429
    retry_after = 60


class ResourceExhausted(CDSError):
    code = "RESOURCE_EXHAUSTED"
    http_status = 429


class QuotaExceeded(ResourceExhausted):
    code = "QUOTA_EXCEEDED"


class RequestTimeout(CDSError):
    """Synchronous wait exceeded (408) — the operation may still finish."""

    code = "REQUEST_TIMEOUT"
    http_status = 408
