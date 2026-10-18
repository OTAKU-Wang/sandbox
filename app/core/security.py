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
    _MAX_LOCAL_BUCKETS = 4096

    def __init__(self, app, requests_per_minute: int | None = None):
        super().__init__(app)
        self._local: dict[str, list[float]] = defaultdict(list)
        self._last_redis_warning = 0.0
        self._default_limit = requests_per_minute

    def _resolve_scopes(self, request: Request) -> list[tuple[str, str, int]]:
        """Buckets a request must pass. All of them are enforced (A45).

        A JWT whose signature was never verified only earns the caller its own
        ``user`` bucket; the per-IP bucket stays enforced alongside it, so
        forging ``sub`` values cannot turn the per-IP ceiling into a
        per-forged-identity ceiling.
        """
        settings = get_settings()
        client_ip = request.client.host if request.client else "unknown"
        path = request.url.path.rstrip("/")
        limits = {
            "auth": settings.RATE_LIMIT_AUTH_PER_MINUTE,
            "user": settings.RATE_LIMIT_PER_MINUTE_USER,
            "ip": settings.RATE_LIMIT_PER_MINUTE_IP,
        }
        ip_limit = self._default_limit or limits["ip"]
        if path.endswith(("/api/v1/auth/login", "/api/v1/auth/register",
                          "/api/v1/auth/login/cert", "/api/v1/auth/login/cert/challenge")):
            # CR-2026-10-15 M1: the cert-login pair used to fall into the loose
            # per-IP bucket, so the strict auth bucket never covered it.
            return [("auth", client_ip, limits["auth"])]
        sub = _unverified_jwt_sub(request.headers.get("Authorization", ""))
        if sub:
            return [("user", sub, limits["user"]), ("ip", client_ip, ip_limit)]
        return [("ip", client_ip, ip_limit)]

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
        live = [t for t in self._local[bucket_key] if t > window_start]
        if len(live) >= limit:
            self._local[bucket_key] = live
            return True, _WINDOW_SECONDS - int(now) % _WINDOW_SECONDS
        live.append(now)
        self._local[bucket_key] = live
        if len(self._local) > self._MAX_LOCAL_BUCKETS:
            self._prune_local(now)
        return False, 0

    def _prune_local(self, now: float) -> None:
        """Keep the Redis-outage fallback from growing one key per identity (A46)."""
        stale = [k for k, ts in self._local.items() if not ts or ts[-1] <= now - _WINDOW_SECONDS]
        for key in stale:
            del self._local[key]
        overflow = len(self._local) - self._MAX_LOCAL_BUCKETS
        if overflow > 0:
            for key in list(self._local)[:overflow]:
                del self._local[key]

    async def dispatch(self, request: Request, call_next):
        settings = get_settings()
        if not settings.RATE_LIMIT_ENABLED:
            return await call_next(request)

        for scope, identifier, limit in self._resolve_scopes(request):
            result = await self._redis_hit(request, scope, identifier, limit)
            if result is None:
                limited, retry_after = self._local_hit(scope, identifier, limit)
            else:
                limited, retry_after = result

            if limited:
                return self._reject(request, scope, retry_after)
        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to all responses.

    /api/jupyter/* 是唯一需要被前端 iframe 同源嵌入的前缀（JupyterLab 开发环，见
    app/api/jupyter_proxy.py）：两条部署道（vite :5199 与 nginx :3000 的 /api 代理）
    浏览器视角都与页面同源，故该前缀用 SAMEORIGIN + CSP frame-ancestors 'self'；
    其余路径维持 DENY。安全边界不变：一次性票据 + 会话目录前缀限制 + 终端接口镜像内关闭。
    """

    EMBED_PREFIX = "/api/jupyter/"

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        embedded = request.url.path.startswith(self.EMBED_PREFIX)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN" if embedded else "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        csp = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'"
        if embedded:
            csp += "; frame-ancestors 'self'"
        response.headers["Content-Security-Policy"] = csp
        return response


class RequestSizeLimitMiddleware:
    """ASGI body-size gate covering declared and streamed bodies (A47).

    ``Content-Length`` is checked up front; a ``Transfer-Encoding: chunked``
    request declares nothing, so its bytes are counted as they stream and the
    connection is dropped once the cap is passed. Counting happens without
    buffering, so the cap holds without paying for it in memory.
    """

    def __init__(self, app, max_size_bytes: int = 10 * 1024 * 1024):  # 10MB default
        self.app = app
        self.max_size_bytes = max_size_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        headers = {}
        for raw_name, raw_value in scope.get("headers", []):
            headers[raw_name.decode("latin-1").lower()] = raw_value.decode("latin-1")

        declared = headers.get("content-length")
        if declared is not None:
            try:
                size = int(declared)
            except ValueError:
                return await self._reject(send, 400, "Invalid Content-Length")
            if size > self.max_size_bytes:
                return await self._reject(send, 413, "Request body too large")
            return await self.app(scope, receive, send)

        if headers.get("transfer-encoding", "").lower() != "chunked":
            return await self.app(scope, receive, send)

        consumed = 0

        async def counting_receive():
            nonlocal consumed
            message = await receive()
            if message["type"] == "http.request":
                consumed += len(message.get("body", b""))
                if consumed > self.max_size_bytes:
                    return {"type": "http.disconnect"}
            return message

        return await self.app(scope, counting_receive, send)

    async def _reject(self, send, status: int, detail: str) -> None:
        body = json.dumps({"detail": detail}).encode("utf-8")
        await send({
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("latin-1")),
            ],
        })
        await send({"type": "http.response.body", "body": body})


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
        # PZ-16 ③：跨域部署下浏览器默认只暴露 CORS-safelisted 响应头，必须显式 expose
        # X-CDS-Output-Review，前端才看得见下载内容是否被输出审查 REDACTED（否则是静默
        # 泄露失败）；Content-Disposition 供审计导出/交付包下载读取服务端文件名。
        expose_headers=[
            "X-CDS-Output-Review",
            "Content-Disposition",
            "Content-Length",
            "Content-Type",
        ],
    )

    # Security headers
    app.add_middleware(SecurityHeadersMiddleware)

    # Rate limiting (disabled in tests; per-scope limits from Settings)
    if enable_rate_limit:
        app.add_middleware(RateLimitMiddleware)

    # Request size limit
    app.add_middleware(RequestSizeLimitMiddleware, max_size_bytes=10 * 1024 * 1024)
