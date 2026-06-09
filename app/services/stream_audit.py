"""Streaming Audit Agent — real-time PII scanning with rolling window aggregation.

Provides:
1. Streaming PII detection on chunks of data
2. Rolling window aggregation for rate-based anomaly detection
3. Integration with pii_ner_service for detection
4. Callback-based alerting when thresholds are exceeded

Architecture:
- Chunks are processed incrementally (no full dataset in memory)
- Rolling window tracks detection counts over configurable time periods
- Alerts fire when PII density exceeds threshold in a window
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from collections import deque

from app.services.pii_ner import pii_ner_service, PIIMatch, PIIType

logger = logging.getLogger(__name__)


@dataclass
class AuditChunk:
    """A chunk of data to audit."""
    data: str
    source: str = ""  # e.g., "output", "query_result", "file"
    session_id: str | None = None
    user_id: str | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class ChunkResult:
    """Result of auditing a single chunk."""
    chunk_index: int
    pii_found: list[PIIMatch]
    pii_count: int
    pii_types: dict[str, int]  # type → count
    redacted_data: str


@dataclass
class WindowStats:
    """Rolling window statistics."""
    window_minutes: int
    total_chunks: int
    total_pii: int
    pii_density: float  # pii per chunk
    type_distribution: dict[str, int]
    alert_triggered: bool = False


class StreamAuditAgent:
    """Real-time PII scanning with rolling window aggregation.

    Usage:
        agent = StreamAuditAgent(window_minutes=5, alert_threshold=3)
        result = agent.process_chunk(AuditChunk(data="text with 13800138000"))
        stats = agent.get_window_stats()
    """

    def __init__(
        self,
        window_minutes: int = 5,
        alert_threshold: float = 3.0,  # max PII per chunk average in window
        pii_density_threshold: float = 0.1,  # max ratio of chunks with PII
        enable_ner: bool = True,
    ):
        self._window_minutes = window_minutes
        self._alert_threshold = alert_threshold
        self._pii_density_threshold = pii_density_threshold
        self._enable_ner = enable_ner

        # Rolling window: deque of (timestamp, chunk_result)
        self._window: deque[tuple[datetime, ChunkResult]] = deque()
        self._chunk_counter = 0
        self._callbacks: list = []

    def on_alert(self, callback) -> None:
        """Register an alert callback.

        Callback signature: callback(stats: WindowStats) -> None
        """
        self._callbacks.append(callback)

    def process_chunk(self, chunk: AuditChunk) -> ChunkResult:
        """Process a single data chunk for PII.

        Args:
            chunk: The data chunk to audit

        Returns:
            ChunkResult with detected PII
        """
        # Detect PII
        result = pii_ner_service.detect(chunk.data)
        matches = result.matches
        redacted = result.redacted_text if matches else chunk.data

        # Count by type
        type_counts: dict[str, int] = {}
        for m in matches:
            type_counts[m.pii_type.value] = type_counts.get(m.pii_type.value, 0) + 1

        result = ChunkResult(
            chunk_index=self._chunk_counter,
            pii_found=matches,
            pii_count=len(matches),
            pii_types=type_counts,
            redacted_data=redacted,
        )
        self._chunk_counter += 1

        # Add to rolling window
        now = datetime.now(timezone.utc)
        self._window.append((now, result))

        # Evict old entries
        cutoff = now - timedelta(minutes=self._window_minutes)
        while self._window and self._window[0][0] < cutoff:
            self._window.popleft()

        # Check if alert should fire
        stats = self.get_window_stats()
        if stats.alert_triggered:
            self._fire_alerts(stats)

        return result

    def process_batch(self, chunks: list[AuditChunk]) -> list[ChunkResult]:
        """Process multiple chunks."""
        return [self.process_chunk(c) for c in chunks]

    def get_window_stats(self) -> WindowStats:
        """Get current rolling window statistics."""
        if not self._window:
            return WindowStats(
                window_minutes=self._window_minutes,
                total_chunks=0,
                total_pii=0,
                pii_density=0.0,
                type_distribution={},
                alert_triggered=False,
            )

        total_chunks = len(self._window)
        total_pii = sum(r.pii_count for _, r in self._window)
        chunks_with_pii = sum(1 for _, r in self._window if r.pii_count > 0)

        # Aggregate type distribution
        type_dist: dict[str, int] = {}
        for _, r in self._window:
            for t, c in r.pii_types.items():
                type_dist[t] = type_dist.get(t, 0) + c

        pii_density = total_pii / total_chunks if total_chunks > 0 else 0.0
        pii_ratio = chunks_with_pii / total_chunks if total_chunks > 0 else 0.0

        alert_triggered = (
            pii_density > self._alert_threshold
            or pii_ratio > self._pii_density_threshold
        )

        return WindowStats(
            window_minutes=self._window_minutes,
            total_chunks=total_chunks,
            total_pii=total_pii,
            pii_density=pii_density,
            type_distribution=type_dist,
            alert_triggered=alert_triggered,
        )

    def _fire_alerts(self, stats: WindowStats) -> None:
        """Fire registered alert callbacks."""
        for cb in self._callbacks:
            try:
                cb(stats)
            except Exception as e:
                logger.error(f"Alert callback error: {e}")

    def reset(self) -> None:
        """Clear window and reset state."""
        self._window.clear()
        self._chunk_counter = 0

    @property
    def window_size(self) -> int:
        """Current number of chunks in the window."""
        return len(self._window)


# Singleton
stream_audit_agent = StreamAuditAgent()
