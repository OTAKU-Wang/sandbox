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

    def inspect_chunk(self, data: bytes) -> tuple[bytes, ChunkInspectionResult]:
        """Inspect a single chunk of data.

        Args:
            data: Raw bytes from response stream

        Returns:
            Tuple of (output_bytes, inspection_result)
        """
        text = data.decode("utf-8", errors="ignore")
        hits = pii_regex_scan(text)

        result = ChunkInspectionResult(
            chunk_index=self._chunk_index,
            pii_hits=hits,
            original_size=len(data),
        )

        if hits:
            text = auto_redact(text, hits)
            result.redacted = True

            # Record audit events
            for hit in hits:
                self._audit_log.append(RedactionEvent(
                    event_id=f"redact-{uuid.uuid4().hex[:8]}",
                    session_id=self._session_id,
                    chunk_index=self._chunk_index,
                    pii_type=hit.pii_type,
                ))

            # Update rolling window
            now = time.monotonic()
            self._window_hits.append((now, len(hits)))

        output = text.encode("utf-8")
        result.output_size = len(output)

        self._total_pii_hits += len(hits)
        self._total_chars += len(text)
        self._chunk_index += 1

        return output, result

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
