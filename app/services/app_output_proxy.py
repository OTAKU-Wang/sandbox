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

import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.services.streaming_inspector import (
    ChunkInspectionResult,
    InspectionDecision,
    StreamingInspectorPool,
    inspector_pool,
    pii_regex_scan,
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


def _blocked_body(reason: str, message: str) -> bytes:
    return json.dumps(
        {"error": "blocked", "reason": reason, "message": message},
        ensure_ascii=False,
    ).encode("utf-8")


@dataclass
class ProxyDecision:
    """OCR-A105：把「审一个 body」从 mitmproxy 钩子里抽成纯函数结果，

    ``action`` ∈ {pass, redact, block}：
    - pass   —— 审过无 PII，字节原样（identity，绝不重编码）；
    - redact —— 文本型且检出已定稿脱敏，body 为脱敏后字节；
    - block  —— 熔断 / 非文本或未声明类型命中 PII / 字节流不可解码（无法证明无 PII，
      fail-closed 整包阻断，绝不写回受损/未审 body）。
    """
    action: str
    body: bytes
    reason: str = ""


class CDSOutputInspector:
    """mitmproxy addon that inspects HTTP responses for PII leakage.

    For every HTTP response flowing through the proxy the addon:

    1. Determines the sandbox *session_id* from the client address.
    2. Inspects **every** body regardless of Content-Type (OCR-A105: 头缺失/未列举
       不再免检)——chunked into 4 KB blocks with boundary-carry decoding.
    3. Feeds each block through :py:meth:`StreamingInspector.inspect_chunk` and
       drains the boundary carry with ``flush()``.
    4. Text-type bodies with PII are replaced with the redacted version; binary /
       undeclared / undecodable bodies with PII are blocked outright (fail-closed,
       永不写回受损或未审 body).
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
        proxy is running in upstream or regular mode.  决策本体在
        :py:meth:`inspect_body`（纯函数，无 mitmproxy 也能测）；这里只做落装。
        """
        if not _MITMPROXY_AVAILABLE or mhttp is None:
            return

        response = flow.response
        if response is None:
            return

        body: bytes = response.get_content()
        if not body:
            return

        session_id = self._resolve_session_id(flow)
        content_type = response.headers.get("content-type", "")
        decision = self.inspect_body(body, session_id, content_type)

        if decision.action == "pass":
            return
        if decision.action == "redact":
            response.set_content(decision.body)
            logger.info(
                "Redacted PII in response for session %s (%d -> %d bytes)",
                session_id, len(body), len(decision.body),
            )
            return

        # block
        response.set_content(_blocked_body(
            decision.reason, f"Response blocked: {decision.reason}."))
        response.status_code = 403
        response.headers["content-type"] = "application/json"
        response.headers["x-cds-output-inspection"] = f"blocked; reason={decision.reason}"
        logger.warning(
            "Blocked response for session %s (reason=%s)", session_id, decision.reason)

    # ------------------------------------------------------------------
    # Inspection core (OCR-A105)
    # ------------------------------------------------------------------

    def inspect_body(self, body: bytes, session_id: str, content_type: str) -> ProxyDecision:
        """审查一个完整响应体，返回 :py:class:`ProxyDecision`。

        两条真值的修复落点（docs/ocr-review-2026-10-18 §A105）：

        1. **Content-Type 不再是免检金牌**。旧实现「头缺失/未列举就直接 return」，
           一个 ``image/png`` 头就绕过全部泄露控制。现在**所有响应都过字节级 PII
           扫描**（latin-1 是 1↔1 无损映射，PII 模式全在 ASCII，对任意字节序列可用）；
           文本型命中走脱敏改写，非文本/未声明类型命中整包阻断（不对二进制做改写），
           头缺失按未声明处理——没有任何头部值能换来零审查。
        2. **不再逐 chunk 独立 ``errors="ignore"`` 解码**。分块交给修好的
           :class:`StreamingInspector`（增量 UTF-8 + 边界保留带 + flush），多字节
           字符不再被切块吞掉、跳块边界 PII 必中；任何不可解码的字节流一律
           ``block``（fail-closed），全有全无式写回受损 body 的旧路彻底封死。
        """
        text_like = _is_text_content(content_type)

        if not text_like:
            # 未声明/未列举/二进制：字节级无损扫描；命中→阻断（不改写），干净→原样放行
            hits = pii_regex_scan(body.decode("latin-1"))
            if hits:
                logger.warning(
                    "PII found in %r body for session %s (%d hit(s)) -- blocked, not redacted",
                    content_type or "<missing content-type>", session_id, len(hits))
                return ProxyDecision("block", body, reason="pii_in_non_text_body")
            return ProxyDecision("pass", body)

        inspector = self._pool.get_or_create(session_id)
        redacted_chunks: list[bytes] = []
        all_hits: list[ChunkInspectionResult] = []

        for offset in range(0, len(body), self._chunk_size):
            chunk = body[offset : offset + self._chunk_size]
            output_bytes, result = inspector.inspect_chunk(chunk)
            if result.undecodable:
                logger.warning(
                    "Undecodable UTF-8 body for session %s at offset %d -- fail-closed block",
                    session_id, offset)
                return ProxyDecision("block", body, reason="output_undecodable")
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
                return ProxyDecision("block", body, reason="pii_circuit_breaker")

        # 流尾定局：保留带里可能藏着最后一枚 PII（A105 事实 1 的尾段形态）
        tail_bytes, tail_result = inspector.flush()
        if tail_result.undecodable:
            logger.warning("Undecodable stream tail for session %s -- fail-closed block", session_id)
            return ProxyDecision("block", body, reason="output_undecodable")
        redacted_chunks.append(tail_bytes)
        if tail_result.pii_hits:
            all_hits.append(tail_result)

        if all_hits:
            redacted_body = b"".join(redacted_chunks)
            logger.info(
                "Redacted PII in response for session %s (%d hit(s), %d -> %d bytes)",
                session_id,
                sum(len(r.pii_hits) for r in all_hits),
                len(body),
                len(redacted_body),
            )
            return ProxyDecision("redact", redacted_body)
        # 未命中且全程无损：字节原样放行，不做任何重编码
        return ProxyDecision("pass", body)


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
    """Return ``True`` if *content_type* declares redactable text.

    OCR-A105：这只是「可否做文本级脱敏改写」的分类器，**不是免检开关**——
    返回 False 的响应（含头缺失）仍然要过字节级 PII 扫描，命中即阻断。
    """
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
