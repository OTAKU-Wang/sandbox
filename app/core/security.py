"""Security middleware and utilities — OWASP Top 10 hardening."""
import time
import logging
from collections import defaultdict
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Rate limiting by IP address. Prevents brute force attacks on auth endpoints."""

    def __init__(self, app, requests_per_minute: int = 60):
        super().__init__(app)
        self.requests_per_minute = requests_per_minute
        self._requests: dict[str, list[float]] = defaultdict(list)

    async def dispatch(self, request: Request, call_next):
        client_ip = request.client.host if request.client else "unknown"
        now = time.time()
        window_start = now - 60

        # Clean old entries
        self._requests[client_ip] = [
            t for t in self._requests[client_ip] if t > window_start
        ]

        if len(self._requests[client_ip]) >= self.requests_per_minute:
            return Response(
                content='{"detail":"Rate limit exceeded. Try again later."}',
                status_code=429,
                media_type="application/json",
            )

        self._requests[client_ip].append(now)
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

    # Rate limiting (disabled in tests)
    if enable_rate_limit:
        app.add_middleware(RateLimitMiddleware, requests_per_minute=60)

    # Request size limit
    app.add_middleware(RequestSizeLimitMiddleware, max_size_bytes=10 * 1024 * 1024)
