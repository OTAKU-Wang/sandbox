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

A127 修复轮（2026-10-30，本模块全仓零生产导入方——唯一的 ``stream_audit``
命中是 audit.py 里一个同名局部函数；三条均 LATENT，接线即高）：
① 并发：实例态（窗口/计数器/回调）曾被模块级单例暴露且全程无锁。现在所有
   状态变更走一把 ``threading.Lock``：chunk_index 分配+窗口 append+驱逐+统计
   在同一临界区内原子完成（计数器不再可能重复/跳号），回调以快照触发，注册
   与触发并发不再撕裂。单例保留（仓内约定），但类本身已是线程安全的；多副本
   语义仍属接线决策。
② 隐私：``ChunkResult.pii_found`` 的 ``PIIMatch.value`` 曾是未脱敏 PII 明文，
   且随窗口整段保留——与同模块产出 ``redacted_data`` 的意图自相矛盾。窗口与
   返回值现在只携带 ``[REDACTED:<type>]`` 形态的 value（类型/偏移/置信度保留，
   告警与统计不需要明文）。
③ 量纲：台账原文（density=total_pii/total_chunks 被拿去比占比阈值 0.1）在本
   基线已不存在——代码分别以 avg-per-chunk 比 ``alert_threshold``、以
   chunks_with_pii/total_chunks 比 ``pii_density_threshold``，量纲各自一致
   （如实更正）。真正成立的症状是样本下限缺失：默认阈值 0.1 下，新窗口的第
   一个含 PII chunk 就是 1/1 > 0.1，告警几乎常开。修复：占比告警要求窗口
   至少 ``min_window_chunks``（默认 3）个样本；统计新增 ``chunks_with_pii``
   与 ``pii_chunk_ratio`` 明示口径。
"""
import logging
import threading
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone, timedelta
from collections import deque

from app.services.pii_ner import pii_ner_service, PIIMatch, PIIType

logger = logging.getLogger(__name__)


def _redacted_match(m: PIIMatch) -> PIIMatch:
    """Copy a match with its plaintext value replaced by the redaction token.

    A127② — same token format pii_ner uses inside ``redacted_text``
    (``[REDACTED:<type>]``), so downstream consumers can pattern-match one
    vocabulary. Type/offset/confidence survive; the PII itself never leaves
    ``process_chunk`` and is never retained in the window.
    """
    return replace(m, value=f"[REDACTED:{m.pii_type.value}]")


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
    """Result of auditing a single chunk.

    ``pii_found`` carries redaction-token values only (A127②) — this object
    is retained in the rolling window, so plaintext PII must never ride in.
    """
    chunk_index: int
    pii_found: list[PIIMatch]
    pii_count: int
    pii_types: dict[str, int]  # type → count
    redacted_data: str


@dataclass
class WindowStats:
    """Rolling window statistics.

    Units (A127③, pinned): ``pii_density`` is detections per chunk
    (compared against ``alert_threshold``); ``pii_chunk_ratio`` is the share
    of chunks containing at least one detection (compared against
    ``pii_density_threshold`` — the field name predates this split and is
    kept for API stability). The two thresholds never see each other's
    dimension.
    """
    window_minutes: int
    total_chunks: int
    total_pii: int
    pii_density: float  # detections per chunk (average)
    type_distribution: dict[str, int]
    alert_triggered: bool = False
    chunks_with_pii: int = 0
    pii_chunk_ratio: float = 0.0  # chunks with ≥1 detection / total chunks


class StreamAuditAgent:
    """Real-time PII scanning with rolling window aggregation.

    Thread-safe for concurrent ``process_chunk`` / ``on_alert`` /
    ``get_window_stats`` (A127①): window mutation, chunk-index allocation,
    eviction and stats recomputation happen under one lock; alert callbacks
    fire outside the lock against a snapshot, so a callback may register
    another callback or query stats without deadlocking or mutating the
    list being iterated.

    Usage:
        agent = StreamAuditAgent(window_minutes=5, alert_threshold=3)
        result = agent.process_chunk(AuditChunk(data="text with 13800138000"))
        stats = agent.get_window_stats()
    """

    def __init__(
        self,
        window_minutes: int = 5,
        alert_threshold: float = 3.0,  # max PII detections per chunk, averaged in window
        pii_density_threshold: float = 0.1,  # max ratio of chunks carrying PII
        enable_ner: bool = True,
        min_window_chunks: int = 3,  # A127③: sample floor before ratio alerts fire
    ):
        self._window_minutes = window_minutes
        self._alert_threshold = alert_threshold
        self._pii_density_threshold = pii_density_threshold
        self._enable_ner = enable_ner
        self._min_window_chunks = max(1, int(min_window_chunks))

        # Rolling window: deque of (timestamp, chunk_result)
        self._window: deque[tuple[datetime, ChunkResult]] = deque()
        self._chunk_counter = 0
        self._callbacks: list = []
        # A127①: one lock for all instance state. Detection itself stays
        # outside the critical section (pure CPU); only index+append+evict+
        # stats are serialised, which is where the duplication/gap races were.
        self._lock = threading.Lock()

    def on_alert(self, callback) -> None:
        """Register an alert callback.

        Callback signature: callback(stats: WindowStats) -> None
        """
        with self._lock:
            self._callbacks.append(callback)

    def process_chunk(self, chunk: AuditChunk) -> ChunkResult:
        """Process a single data chunk for PII.

        Args:
            chunk: The data chunk to audit

        Returns:
            ChunkResult with detected PII (values redaction-tokened, A127②)
        """
        # Detect PII (outside the lock — pure function of chunk.data)
        detection = pii_ner_service.detect(chunk.data)
        matches = detection.matches
        redacted = detection.redacted_text if matches else chunk.data

        # Count by type
        type_counts: dict[str, int] = {}
        for m in matches:
            type_counts[m.pii_type.value] = type_counts.get(m.pii_type.value, 0) + 1

        result = ChunkResult(
            chunk_index=-1,  # assigned atomically below
            pii_found=[_redacted_match(m) for m in matches],  # A127②
            pii_count=len(matches),
            pii_types=type_counts,
            redacted_data=redacted,
        )

        # A127①: index allocation, window append and eviction are ONE atomic
        # step — chunk indices are dense (0,1,2,...) under any interleaving.
        with self._lock:
            result.chunk_index = self._chunk_counter
            self._chunk_counter += 1

            now = datetime.now(timezone.utc)
            self._window.append((now, result))

            cutoff = now - timedelta(minutes=self._window_minutes)
            while self._window and self._window[0][0] < cutoff:
                self._window.popleft()

            stats = self._window_stats_locked()
            callbacks = list(self._callbacks) if stats.alert_triggered else []

        if stats.alert_triggered:
            self._fire_alerts(stats, callbacks)

        return result

    def process_batch(self, chunks: list[AuditChunk]) -> list[ChunkResult]:
        """Process multiple chunks."""
        return [self.process_chunk(c) for c in chunks]

    def get_window_stats(self) -> WindowStats:
        """Get current rolling window statistics (lock-protected snapshot)."""
        with self._lock:
            return self._window_stats_locked()

    def _window_stats_locked(self) -> WindowStats:
        """Recompute stats. Caller must hold ``self._lock``."""
        if not self._window:
            return WindowStats(
                window_minutes=self._window_minutes,
                total_chunks=0,
                total_pii=0,
                pii_density=0.0,
                type_distribution={},
                alert_triggered=False,
                chunks_with_pii=0,
                pii_chunk_ratio=0.0,
            )

        total_chunks = len(self._window)
        total_pii = sum(r.pii_count for _, r in self._window)
        chunks_with_pii = sum(1 for _, r in self._window if r.pii_count > 0)

        # Aggregate type distribution
        type_dist: dict[str, int] = {}
        for _, r in self._window:
            for t, c in r.pii_types.items():
                type_dist[t] = type_dist.get(t, 0) + c

        pii_density = total_pii / total_chunks
        pii_ratio = chunks_with_pii / total_chunks

        # A127③: the ratio dimension only means anything with a sample; on a
        # fresh window a single PII chunk is 1/1 and used to fire the alert
        # near-constantly at the default 0.1 threshold.
        sample_ready = total_chunks >= self._min_window_chunks
        alert_triggered = sample_ready and (
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
            chunks_with_pii=chunks_with_pii,
            pii_chunk_ratio=pii_ratio,
        )

    def _fire_alerts(self, stats: WindowStats, callbacks: list | None = None) -> None:
        """Fire alert callbacks against a snapshot — outside the lock, so a
        callback may ``on_alert``/``get_window_stats`` re-entrantly (A127①)."""
        if callbacks is None:
            with self._lock:
                callbacks = list(self._callbacks)
        for cb in callbacks:
            try:
                cb(stats)
            except Exception as e:
                logger.error(f"Alert callback error: {e}")

    def reset(self) -> None:
        """Clear window and reset state."""
        with self._lock:
            self._window.clear()
            self._chunk_counter = 0

    @property
    def window_size(self) -> int:
        """Current number of chunks in the window."""
        with self._lock:
            return len(self._window)


# Singleton — the class is thread-safe now (A127①), but window/alert state is
# still per-process; multi-replica alerting is a wiring decision, not implied.
stream_audit_agent = StreamAuditAgent()
