"""Application Output Proxy -- mitmproxy addon for streaming output review.

Intercepts HTTP/HTTPS responses from sandbox sessions, feeds them through
the StreamingInspector for PII detection, and blocks/redacts sensitive content.

Architecture (SS-05 section 5):
- Runs inside the TEE alongside the application
- Acts as an upstream proxy for outbound HTTP traffic from sandbox sessions
- Each response is chunked into 4KB blocks for incremental PII scanning
- Detected PII is auto-redacted; excessive PII density triggers circuit-breaker block
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.services.streaming_inspector import (
    ChunkInspectionResult,
    InspectionDecision,
    StreamingInspectorPool,
    inspector_pool,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Graceful import of mitmproxy -- the addon can be inspected/tested even when
# mitmproxy is not installed in the current environment.
# ---------------------------------------------------------------------------
try:
    from mitmproxy import http as mhttp  # type: ignore[import-untyped]

    _MITMPROXY_AVAILABLE = True
except ImportError:
    mhttp = None  # type: ignore[assignment]
    _MITMPROXY_AVAILABLE = False
    logger.warning(
        "mitmproxy is not installed -- CDSOutputInspector addon will be "
        "registered but cannot intercept traffic.  Install with: pip install mitmproxy"
    )

if TYPE_CHECKING:  # pragma: no cover -- only for static analysis
    from mitmproxy import http as mhttp  # noqa: F811

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_CHUNK_SIZE = 4096  # 4 KB per inspection chunk
_BLOCKED_RESPONSE_BODY = (
    b'{"error":"blocked","reason":"pii_circuit_breaker",'
    b'"message":"Response blocked: excessive PII density detected."}'
)


class CDSOutputInspector:
    """mitmproxy addon that inspects HTTP responses for PII leakage.

    For every HTTP response flowing through the proxy the addon:

    1. Determines the sandbox *session_id* from the client address.
    2. Chunks the response body into 4 KB blocks.
    3. Feeds each block through :py:meth:`StreamingInspector.inspect_chunk`.
    4. If PII is found the response body is replaced with the redacted version.
    5. After each chunk the rolling-window density is checked.  If the circuit
       breaker fires the entire response is replaced with an error payload and
       the HTTP status is set to ``403``.
    """

    def __init__(
        self,
        pool: StreamingInspectorPool | None = None,
        chunk_size: int = _CHUNK_SIZE,
    ) -> None:
        self._pool: StreamingInspectorPool = pool or inspector_pool
        self._chunk_size = chunk_size
        # Maps "client_ip:client_port" -> session_id
        self._session_map: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Session management helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _client_key(flow: mhttp.HTTPFlow) -> str:  # type: ignore[name-defined]
        """Derive a stable key from the client's (ip, port) tuple."""
        addr = flow.client_conn.address
        if addr:
            return f"{addr[0]}:{addr[1]}"
        return "unknown:0"

    def bind_session(self, client_key: str, session_id: str) -> None:
        """Explicitly map a client address to a sandbox session id.

        Call this from the CDS control-plane when a sandbox session is created
        so that the proxy can attribute traffic to the correct session.
        """
        self._session_map[client_key] = session_id
        logger.info("Bound client %s to session %s", client_key, session_id)

    def unbind_session(self, client_key: str) -> str | None:
        """Remove a client-to-session mapping.  Returns the removed session id."""
        return self._session_map.pop(client_key, None)

    def _resolve_session_id(self, flow: mhttp.HTTPFlow) -> str:  # type: ignore[name-defined]
        """Return the session id for *flow*, generating one if unmapped."""
        key = self._client_key(flow)
        session_id = self._session_map.get(key)
        if session_id is None:
            # Auto-assign a synthetic session id so traffic is still inspected
            session_id = f"auto-{key}"
            self._session_map[key] = session_id
            logger.debug("Auto-assigned session %s for unmapped client %s", session_id, key)
        return session_id

    # ------------------------------------------------------------------
    # mitmproxy hook
    # ------------------------------------------------------------------

    def response(self, flow: mhttp.HTTPFlow) -> None:  # type: ignore[name-defined]
        """Intercept every HTTP response and inspect for PII.

        This is called by mitmproxy for both HTTP and HTTPS flows when the
        proxy is running in upstream or regular mode.
        """
        if not _MITMPROXY_AVAILABLE or mhttp is None:
            return

        response = flow.response
        if response is None:
            return

        # Only inspect text-like content types to avoid breaking binaries
        content_type = response.headers.get("content-type", "")
        if not _is_text_content(content_type):
            return

        body: bytes = response.get_content()
        if not body:
            return

        session_id = self._resolve_session_id(flow)
        inspector = self._pool.get_or_create(session_id)

        # Chunk the body and inspect each chunk
        redacted_chunks: list[bytes] = []
        all_hits: list[ChunkInspectionResult] = []
        circuit_breaker_tripped = False

        for offset in range(0, len(body), self._chunk_size):
            chunk = body[offset : offset + self._chunk_size]
            output_bytes, result = inspector.inspect_chunk(chunk)
            redacted_chunks.append(output_bytes)
            if result.pii_hits:
                all_hits.append(result)

            # Check rolling window after every chunk
            window = inspector.check_window()
            if window.exceeded:
                logger.warning(
                    "Circuit breaker tripped for session %s -- PII density %.4f exceeds threshold %.4f",
                    session_id,
                    window.pii_density,
                    window.threshold,
                )
                circuit_breaker_tripped = True
                break

        if circuit_breaker_tripped:
            # Block the response entirely
            response.set_content(_BLOCKED_RESPONSE_BODY)
            response.status_code = 403
            response.headers["content-type"] = "application/json"
            logger.warning("Blocked response for session %s (circuit breaker)", session_id)
            return

        # If any PII was found, replace the body with the redacted version
        if all_hits:
            redacted_body = b"".join(redacted_chunks)
            response.set_content(redacted_body)
            logger.info(
                "Redacted PII in response for session %s (%d hit(s), %d -> %d bytes)",
                session_id,
                sum(len(r.pii_hits) for r in all_hits),
                len(body),
                len(redacted_body),
            )


# ---------------------------------------------------------------------------
# Content-type heuristic
# ---------------------------------------------------------------------------
_TEXT_PREFIXES = (
    "text/",
    "application/json",
    "application/xml",
    "application/xhtml",
    "application/javascript",
    "application/x-javascript",
    "application/ecmascript",
    "application/x-ecmascript",
    "application/graphql",
    "application/ld+json",
    "application/health+json",
    "application/vnd.api+json",
)


def _is_text_content(content_type: str) -> bool:
    """Return ``True`` if *content_type* is likely text-based and safe to inspect."""
    ct = content_type.split(";")[0].strip().lower()
    return any(ct.startswith(prefix) for prefix in _TEXT_PREFIXES)


# ---------------------------------------------------------------------------
# Public helper: create an addon instance suitable for mitmdump / API usage
# ---------------------------------------------------------------------------

def create_addon(
    pool: StreamingInspectorPool | None = None,
    chunk_size: int = _CHUNK_SIZE,
) -> CDSOutputInspector:
    """Factory that returns a ``CDSOutputInspector`` addon instance.

    Use this when launching mitmproxy programmatically::

        from mitmproxy.options import Options
        from mitmproxy.tools.dump import DumpMaster

        addon = create_addon()
        # ... configure master and run
    """
    return CDSOutputInspector(pool=pool, chunk_size=chunk_size)
