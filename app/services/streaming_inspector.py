"""Streaming Inspection Proxy — SS-05 §5 application output review.

Provides:
1. Streaming HTTP response interception and PII scanning
2. Rolling window aggregation to detect distributed PII leakage
3. Auto-redaction of detected PII (non-blocking for app availability)
4. Session-level circuit breaker on excessive PII density
5. Audit logging of all redaction events

Architecture (SS-05 §5):
- mitmproxy-in-TEE: intercepts app's outbound HTTP responses
- 4KB chunk-based scanning: fast regex PII detection
- Rolling window: prevents slow-leak PII attacks
- Non-blocking: auto-redact and continue (app availability > strict blocking)

Supports:
- Chinese ID card, phone, bank card, email, IPv4 PII patterns
- Configurable PII density threshold for circuit breaker
- Audit trail for all redaction events
"""
import logging
import re
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import AsyncIterator, Iterator

logger = logging.getLogger(__name__)


class PIIType(str, Enum):
    """Types of PII detected."""
    CHINESE_ID = "chinese_id"
    PHONE = "phone"
    EMAIL = "email"
    BANK_CARD = "bank_card"
    IPV4 = "ipv4"
    PASSPORT = "passport"
    LICENSE_PLATE = "license_plate"
    NAME_CN = "name_cn"


class InspectionDecision(str, Enum):
    """Inspection decision for output."""
    ALLOW = "allow"
    ALLOW_WITH_MODIFICATION = "allow_with_modification"
    BLOCK = "block"


@dataclass
class PIIHit:
    """A single PII detection hit."""
    pii_type: PIIType
    value: str
    start: int
    end: int
    redacted_value: str = ""


@dataclass
class ChunkInspectionResult:
    """Result of inspecting a single chunk."""
    chunk_index: int
    pii_hits: list[PIIHit]
    redacted: bool = False
    original_size: int = 0
    output_size: int = 0
    # OCR-A105: 非 UTF-8 字节流不再被 errors="ignore" 静默吃掉——该 chunk 原样返回并置位，
    # 由调用方（输出代理）fail-closed 阻断。
    undecodable: bool = False


@dataclass
class RedactionEvent:
    """Audit log entry for a redaction event."""
    event_id: str
    session_id: str
    chunk_index: int
    pii_type: PIIType
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class WindowCheckResult:
    """Result of rolling window aggregation check."""
    session_id: str
    window_seconds: int
    total_chunks: int
    total_pii_hits: int
    pii_density: float
    threshold: float
    exceeded: bool
    action: str  # "continue" or "suspend"


# PII regex patterns
_PII_PATTERNS: dict[PIIType, re.Pattern] = {
    PIIType.CHINESE_ID: re.compile(r"(?<!\d)\d{6}(19|20)\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])\d{3}[\dXx](?!\d)"),
    PIIType.PHONE: re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    PIIType.EMAIL: re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"),
    PIIType.BANK_CARD: re.compile(r"(?<!\d)\d{16,19}(?!\d)"),
    PIIType.IPV4: re.compile(r"(?<!\d)(?:\d{1,3}\.){3}\d{1,3}(?!\d)"),
    PIIType.LICENSE_PLATE: re.compile(r"[京津沪渝冀豫云辽黑湘皖鲁新苏浙赣鄂桂甘晋蒙陕吉闽贵粤川青藏琼宁][A-HJ-NP-Z][A-HJ-NP-Z0-9]{4,5}[A-HJ-NP-Z0-9挂学警港澳]"),
}


def pii_regex_scan(text: str) -> list[PIIHit]:
    """Fast regex-based PII scanning.

    Args:
        text: Text to scan

    Returns:
        List of PII hits with positions
    """
    hits = []
    for pii_type, pattern in _PII_PATTERNS.items():
        for match in pattern.finditer(text):
            hits.append(PIIHit(
                pii_type=pii_type,
                value=match.group(),
                start=match.start(),
                end=match.end(),
            ))
    return hits


# OCR-A105：chunk 边界保留带。PII 正则最长匹配 19 字符（bank_card），再加一位负向预查
# （(?!\d) 需要看到下一个字符才能定局）；64 个字符的保留带覆盖上界并留足余量。
_BOUNDARY_CARRY = 64


def _is_plausible_partial_tail(tail: bytes) -> bool:
    """tail 是否可能是「被流边界截断的合法多字节序列」——而非永远解不出来的坏字节。

    只有以合法首字节（0xC2..0xF4）开始、其余全是连续字节（0x80..0xBF）、且长度不足
    该序列声明长度的残留才允许扣留到下块；0xFF/孤立连续字节等坏字节必须立即判
    undecodable（扣留它们只会把阻断拖到 flush，且掩盖真坏数据）。
    """
    if not tail:
        return True
    b0 = tail[0]
    if 0xC2 <= b0 <= 0xDF:
        need = 2
    elif 0xE0 <= b0 <= 0xEF:
        need = 3
    elif 0xF0 <= b0 <= 0xF4:
        need = 4
    else:
        return False
    if len(tail) >= need:
        return False  # 长度已够却解不出 = 坏字节，不是截断
    return all(0x80 <= b <= 0xBF for b in tail[1:])


def _decode_utf8_prefix(buf: bytes) -> tuple[str, bytes]:
    """尽量解码 buf；末尾不完整的多字节序列原样留到返回值第二段（OCR-A105，零丢失）。

    截尾式重试只针对「末尾残留不完整序列」这一合法流式形态（最多 3 个后续字节，
    且必须通过 :func:`_is_plausible_partial_tail`）；中段坏字节与永远解不出的尾字节
    都抛 UnicodeDecodeError，交由调用方按 undecodable fail-closed 处理，绝不
    errors="ignore" 静默丢字节。
    """
    for trim in range(0, 4):
        head = buf[: len(buf) - trim] if trim else buf
        tail = buf[len(head):]
        try:
            text = head.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if _is_plausible_partial_tail(tail):
            return text, tail
        break
    raise UnicodeDecodeError("utf-8", buf, 0, min(len(buf), 1), "invalid UTF-8 beyond a trailing partial sequence")


def auto_redact(text: str, hits: list[PIIHit]) -> str:
    """Auto-redact PII in text.

    Args:
        text: Original text
        hits: List of PII hits to redact

    Returns:
        Text with PII replaced by redaction markers
    """
    if not hits:
        return text

    # Sort by position descending to replace from end
    sorted_hits = sorted(hits, key=lambda h: h.start, reverse=True)
    result = text
    for hit in sorted_hits:
        # Generate redaction marker: keep first/last char for context
        if len(hit.value) <= 4:
            redacted = "*" * len(hit.value)
        else:
            redacted = hit.value[0] + "*" * (len(hit.value) - 2) + hit.value[-1]
        hit.redacted_value = redacted
        result = result[:hit.start] + redacted + result[hit.end:]
    return result


class StreamingInspector:
    """Streaming HTTP response inspection proxy.

    Intercepts application output in 4KB chunks, scans for PII,
    auto-redacts, and monitors rolling window PII density.

    Usage:
        inspector = StreamingInspector(session_id="sess-1")
        async for output_chunk in inspector.inspect(response_stream):
            send_to_client(output_chunk)
        if inspector.window_exceeded:
            suspend_session(session_id)
    """

    def __init__(
        self,
        session_id: str,
        chunk_size: int = 4096,
        window_seconds: int = 60,
        pii_density_threshold: float = 0.01,  # 1% PII density triggers circuit breaker
    ):
        self._session_id = session_id
        self._chunk_size = chunk_size
        self._window_seconds = window_seconds
        self._pii_density_threshold = pii_density_threshold
        self._chunk_index = 0
        self._total_pii_hits = 0
        self._total_chars = 0
        self._audit_log: list[RedactionEvent] = []
        self._window_hits: deque[tuple[float, int]] = deque()  # (timestamp, pii_count)
        self._window_exceeded = False
        # OCR-A105 流式状态：_pending 持有末尾不完整的 UTF-8 字节；_carry 持有已解码但
        # 因边界匹配未定局而未外发的文本。不变式：carry 之前的文本已全部外发（且已定稿
        # 脱敏），carry 字节永远不可能在未定局状态下外发。
        self._pending: bytes = b""
        self._carry: str = ""

    def inspect_chunk(self, data: bytes, final: bool = False) -> tuple[bytes, ChunkInspectionResult]:
        """Inspect a single chunk of data (OCR-A105 修复版).

        与旧实现的三点差异（都是原缺陷的反面）：
        1. 不再 ``errors="ignore"``——非 UTF-8 字节流整 chunk 原样返回并标记
           ``undecodable``，由调用方 fail-closed；末尾不完整多字节序列缓存到下个 chunk
           拼接（多字节字符不再被静默丢弃、破损 body 不再被写回）。
        2. 边界保留带：每次只外发 ``_BOUNDARY_CARRY`` 之前的文本，跳 chunk 边界的 PII
           （含负向预查未定局的尾部）在下一轮扫描中定局后才外发，永不逃逸。
        3. 流尾必须 ``flush()``：drain 保留带，尾部 PII 不漏。

        Args:
            data: Raw bytes from response stream
            final: True 表示流已结束（drain pending+carry 并定局所有匹配）

        Returns:
            Tuple of (output_bytes, inspection_result) — output 可能少于输入（保留带），
            字节层面除脱敏处外无损。
        """
        result = ChunkInspectionResult(
            chunk_index=self._chunk_index,
            pii_hits=[],
            original_size=len(data),
        )
        buf = self._pending + data
        self._pending = b""
        try:
            if final:
                # 流已结束仍剩不完整序列 = 真坏数据：原样交回，fail-closed 交给调用方
                if buf:
                    text = buf.decode("utf-8")  # 允许 raise → undecodable
                else:
                    text = ""
            else:
                text, self._pending = _decode_utf8_prefix(buf)
        except UnicodeDecodeError:
            result.undecodable = True
            result.output_size = len(buf)
            self._chunk_index += 1
            return buf, result

        scan_text = self._carry + text
        hits = pii_regex_scan(scan_text)
        cut = len(scan_text) if final else max(0, len(scan_text) - _BOUNDARY_CARRY)
        # 只有完全落在外发区内的匹配才定局；跨过保留带的（或因尾部预查未定局的）留到下轮
        resolved = [h for h in hits if h.end <= cut]
        out_text = auto_redact(scan_text[:cut], resolved)
        self._carry = scan_text[cut:]

        if resolved:
            result.redacted = True
            for hit in resolved:
                self._audit_log.append(RedactionEvent(
                    event_id=f"redact-{uuid.uuid4().hex[:8]}",
                    session_id=self._session_id,
                    chunk_index=self._chunk_index,
                    pii_type=hit.pii_type,
                ))
            now = time.monotonic()
            self._window_hits.append((now, len(resolved)))

        result.pii_hits = resolved
        output = out_text.encode("utf-8")
        result.output_size = len(output)

        self._total_pii_hits += len(resolved)
        self._total_chars += len(out_text)
        self._chunk_index += 1

        return output, result

    def flush(self) -> tuple[bytes, ChunkInspectionResult]:
        """Drain the boundary carry (and any pending bytes) — 流的每次调用序列都必须以它收尾。"""
        return self.inspect_chunk(b"", final=True)

    def check_window(self) -> WindowCheckResult:
        """Check rolling window PII density.

        Returns:
            WindowCheckResult with density and action
        """
        now = time.monotonic()
        cutoff = now - self._window_seconds

        # Remove expired entries
        while self._window_hits and self._window_hits[0][0] < cutoff:
            self._window_hits.popleft()

        total_hits = sum(count for _, count in self._window_hits)
        # Estimate total chars in window (approximate)
        window_chars = max(self._total_chars, 1)
        density = total_hits / window_chars if window_chars > 0 else 0.0

        exceeded = density > self._pii_density_threshold
        if exceeded:
            self._window_exceeded = True

        return WindowCheckResult(
            session_id=self._session_id,
            window_seconds=self._window_seconds,
            total_chunks=len(self._window_hits),
            total_pii_hits=total_hits,
            pii_density=density,
            threshold=self._pii_density_threshold,
            exceeded=exceeded,
            action="suspend" if exceeded else "continue",
        )

    @property
    def window_exceeded(self) -> bool:
        """Whether the rolling window PII density threshold was exceeded."""
        return self._window_exceeded

    @property
    def stats(self) -> dict:
        """Get inspection statistics."""
        return {
            "session_id": self._session_id,
            "chunks_inspected": self._chunk_index,
            "total_pii_hits": self._total_pii_hits,
            "total_chars": self._total_chars,
            "pii_density": self._total_pii_hits / max(self._total_chars, 1),
            "window_exceeded": self._window_exceeded,
            "audit_events": len(self._audit_log),
        }

    def get_audit_log(self, limit: int = 100) -> list[RedactionEvent]:
        """Get redaction audit log."""
        return self._audit_log[-limit:]


class StreamingInspectorPool:
    """Pool of streaming inspectors for multiple sessions."""

    def __init__(self):
        self._inspectors: dict[str, StreamingInspector] = {}

    def get_or_create(
        self,
        session_id: str,
        chunk_size: int = 4096,
        window_seconds: int = 60,
        pii_density_threshold: float = 0.01,
    ) -> StreamingInspector:
        """Get existing inspector or create new one."""
        if session_id not in self._inspectors:
            self._inspectors[session_id] = StreamingInspector(
                session_id=session_id,
                chunk_size=chunk_size,
                window_seconds=window_seconds,
                pii_density_threshold=pii_density_threshold,
            )
        return self._inspectors[session_id]

    def remove(self, session_id: str) -> bool:
        """Remove inspector for session."""
        return self._inspectors.pop(session_id, None) is not None

    def get_all_stats(self) -> list[dict]:
        """Get stats for all active inspectors."""
        return [insp.stats for insp in self._inspectors.values()]

    @property
    def active_sessions(self) -> int:
        return len(self._inspectors)


# Singleton
inspector_pool = StreamingInspectorPool()
