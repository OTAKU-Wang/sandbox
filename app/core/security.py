"""Security middleware and utilities — OWASP Top 10 hardening."""
import base64
import json
import time
import uuid
import logging
from collections import defaultdict
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# W3: atomic fixed-window counter shared by all app replicas.
_RATE_LIMIT_LUA = """
local n = redis.call('INCR', KEYS[1])
if n == 1 then redis.call('EXPIRE', KEYS[1], 60) end
return n
"""

_WINDOW_SECONDS = 60


def _unverified_jwt_sub(authorization: str) -> str | None:
    """Extract the JWT subject without verification.

    Used ONLY as a rate-limit bucket key: a forged token merely moves the
    caller into its own (forged) bucket — it grants nothing. The middleware
    layer has no DB dependency and must not verify signatures.
    """
    if not authorization.startswith("Bearer "):
        return None
    token = authorization[len("Bearer "):].strip()
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        padded = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
    except Exception:
        return None
    sub = payload.get("sub") if isinstance(payload, dict) else None
    return str(sub) if sub else None


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Rate limiting with a Redis fixed window (W3) and per-scope buckets.

    Scopes: ``auth`` (login endpoint, per-IP, strictest), ``user``
    (authenticated requests, keyed by JWT sub), ``ip`` (anonymous). Redis
    outage falls back to the legacy in-process window (fail-open) with a
    throttled WARNING so the degradation is never silent.
    """

    _REDIS_WARNING_INTERVAL = 30.0

    def __init__(self, app, requests_per_minute: int | None = None):
        super().__init__(app)
        self._local: dict[str, list[float]] = defaultdict(list)
        self._last_redis_warning = 0.0
        self._default_limit = requests_per_minute

    def _resolve_scope(self, request: Request) -> tuple[str, str, int]:
        settings = get_settings()
        client_ip = request.client.host if request.client else "unknown"
        path = request.url.path.rstrip("/")
        limits = {
            "auth": settings.RATE_LIMIT_AUTH_PER_MINUTE,
            "user": settings.RATE_LIMIT_PER_MINUTE_USER,
            "ip": settings.RATE_LIMIT_PER_MINUTE_IP,
        }
        if path.endswith("/api/v1/auth/login"):
            return "auth", client_ip, limits["auth"]
        sub = _unverified_jwt_sub(request.headers.get("Authorization", ""))
        if sub:
            return "user", sub, limits["user"]
        return "ip", client_ip, self._default_limit or limits["ip"]

    def _reject(self, request: Request, scope: str, retry_after: int) -> Response:
        from app.core.metrics import RATE_LIMITED

        RATE_LIMITED.labels(scope=scope).inc()
        request_id = getattr(request.state, "request_id", None) or uuid.uuid4().hex[:16]
        return Response(
            content=json.dumps({
                "code": "RATE_LIMITED",
                "message": "Rate limit exceeded. Try again later.",
                "detail": {"scope": scope},
                "request_id": request_id,
            }),
            status_code=429,
            media_type="application/json",
            headers={"Retry-After": str(max(1, retry_after)), "X-Request-ID": request_id},
        )

    async def _redis_hit(self, request: Request, scope: str, identifier: str, limit: int) -> tuple[bool, int] | None:
        """Count one request in the Redis window. None = Redis unavailable."""
        from app.core.redis import get_redis

        key = f"cds:rl:{scope}:{identifier}:{int(time.time()) // _WINDOW_SECONDS}"
        try:
            redis = await get_redis()
            count = int(await redis.eval(_RATE_LIMIT_LUA, 1, key))
        except Exception as e:
            now = time.monotonic()
            if now - self._last_redis_warning > self._REDIS_WARNING_INTERVAL:
                self._last_redis_warning = now
                logger.warning(
                    "[RATE-LIMIT] Redis unavailable, failing open to in-process "
                    "window (%s). Rate limiting is per-replica until Redis recovers.",
                    e,
                )
            return None
        retry_after = _WINDOW_SECONDS - int(time.time()) % _WINDOW_SECONDS
        return count > limit, retry_after

    def _local_hit(self, scope: str, identifier: str, limit: int) -> tuple[bool, int]:
        bucket_key = f"{scope}:{identifier}"
        now = time.time()
        window_start = now - _WINDOW_SECONDS
        self._local[bucket_key] = [t for t in self._local[bucket_key] if t > window_start]
        if len(self._local[bucket_key]) >= limit:
            return True, _WINDOW_SECONDS - int(now) % _WINDOW_SECONDS
        self._local[bucket_key].append(now)
        return False, 0

    async def dispatch(self, request: Request, call_next):
        settings = get_settings()
        if not settings.RATE_LIMIT_ENABLED:
            return await call_next(request)

        scope, identifier, limit = self._resolve_scope(request)
        result = await self._redis_hit(request, scope, identifier, limit)
        if result is None:
            limited, retry_after = self._local_hit(scope, identifier, limit)
        else:
            limited, retry_after = result

        if limited:
            return self._reject(request, scope, retry_after)
        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to all responses."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'"
        return response


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    """Limit request body size to prevent DoS via large payloads."""

    def __init__(self, app, max_size_bytes: int = 10 * 1024 * 1024):  # 10MB default
        super().__init__(app)
        self.max_size_bytes = max_size_bytes

    async def dispatch(self, request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > self.max_size_bytes:
            return Response(
                content='{"detail":"Request body too large"}',
                status_code=413,
                media_type="application/json",
            )
        return await call_next(request)


def setup_security(app: FastAPI, allowed_origins: list[str] | None = None, enable_rate_limit: bool = True):
    """Apply all security middleware to the FastAPI app."""
    # CORS — restrict origins in production
    origins = allowed_origins or ["*"]
    use_credentials = origins != ["*"]
    if origins == ["*"]:
        import logging
        logging.getLogger(__name__).warning(
            "[SECURITY] CORS wildcard '*' — set CORS_ALLOWED_ORIGINS in production"
        )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=use_credentials,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )

    # Security headers
    app.add_middleware(SecurityHeadersMiddleware)

    # Rate limiting (disabled in tests; per-scope limits from Settings)
    if enable_rate_limit:
        app.add_middleware(RateLimitMiddleware)

    # Request size limit
    app.add_middleware(RequestSizeLimitMiddleware, max_size_bytes=10 * 1024 * 1024)
