"""Tests for Streaming Audit Agent — PII scanning with rolling window."""
import pytest
from datetime import datetime, timezone, timedelta

from app.services.stream_audit import (
    StreamAuditAgent, AuditChunk, ChunkResult, WindowStats,
)


@pytest.fixture
def agent():
    return StreamAuditAgent(window_minutes=5, alert_threshold=2.0, pii_density_threshold=0.5)


# ── Basic Processing ──────────────────────────────────────────────

class TestBasicProcessing:
    def test_clean_chunk(self, agent):
        chunk = AuditChunk(data="Hello, world!")
        result = agent.process_chunk(chunk)
        assert result.pii_count == 0
        assert result.pii_found == []
        assert result.redacted_data == "Hello, world!"

    def test_pii_detected(self, agent):
        chunk = AuditChunk(data="My phone is 13800138000")
        result = agent.process_chunk(chunk)
        assert result.pii_count >= 1
        assert result.redacted_data != chunk.data

    def test_chunk_counter(self, agent):
        agent.process_chunk(AuditChunk(data="a"))
        agent.process_chunk(AuditChunk(data="b"))
        assert agent._chunk_counter == 2

    def test_batch_processing(self, agent):
        chunks = [AuditChunk(data=f"chunk {i}") for i in range(5)]
        results = agent.process_batch(chunks)
        assert len(results) == 5


# ── PII Types ─────────────────────────────────────────────────────

class TestPIIDetection:
    def test_phone_detected(self, agent):
        chunk = AuditChunk(data="Call 13800138000")
        result = agent.process_chunk(chunk)
        assert "phone" in result.pii_types

    def test_email_detected(self, agent):
        chunk = AuditChunk(data="Email test@example.com")
        result = agent.process_chunk(chunk)
        assert "email" in result.pii_types

    def test_id_card_detected(self, agent):
        chunk = AuditChunk(data="ID: 110101199001011234")
        result = agent.process_chunk(chunk)
        assert "id_card" in result.pii_types

    def test_multiple_pii_types(self, agent):
        chunk = AuditChunk(data="Phone 13800138000 email test@example.com")
        result = agent.process_chunk(chunk)
        assert result.pii_count >= 2


# ── Rolling Window ────────────────────────────────────────────────

class TestRollingWindow:
    def test_window_tracks_chunks(self, agent):
        for i in range(3):
            agent.process_chunk(AuditChunk(data=f"chunk {i}"))
        assert agent.window_size == 3

    def test_window_stats_empty(self):
        agent = StreamAuditAgent()
        stats = agent.get_window_stats()
        assert stats.total_chunks == 0
        assert stats.total_pii == 0

    def test_window_stats_with_pii(self, agent):
        agent.process_chunk(AuditChunk(data="Phone 13800138000"))
        agent.process_chunk(AuditChunk(data="clean"))
        stats = agent.get_window_stats()
        assert stats.total_chunks == 2
        assert stats.total_pii >= 1
        assert stats.window_minutes == 5

    def test_type_distribution(self, agent):
        agent.process_chunk(AuditChunk(data="Phone 13800138000"))
        stats = agent.get_window_stats()
        assert "phone" in stats.type_distribution


# ── Alerting ──────────────────────────────────────────────────────

class TestAlerting:
    def test_alert_fires_on_high_density(self):
        agent = StreamAuditAgent(window_minutes=5, alert_threshold=0.5, pii_density_threshold=0.5)
        alerts = []
        agent.on_alert(lambda s: alerts.append(s))

        # Process chunks with high PII density
        for _ in range(5):
            agent.process_chunk(AuditChunk(data="Phone 13800138000"))

        assert len(alerts) >= 1

    def test_no_alert_on_clean_data(self):
        agent = StreamAuditAgent(window_minutes=5, alert_threshold=5.0, pii_density_threshold=0.9)
        alerts = []
        agent.on_alert(lambda s: alerts.append(s))

        for _ in range(5):
            agent.process_chunk(AuditChunk(data="clean data"))

        assert len(alerts) == 0

    def test_callback_receives_stats(self):
        agent = StreamAuditAgent(window_minutes=5, alert_threshold=0.1)
        received_stats = []
        agent.on_alert(lambda s: received_stats.append(s))

        agent.process_chunk(AuditChunk(data="Phone 13800138000"))

        if received_stats:
            assert isinstance(received_stats[0], WindowStats)


# ── Reset ─────────────────────────────────────────────────────────

class TestReset:
    def test_reset_clears_state(self, agent):
        agent.process_chunk(AuditChunk(data="Phone 13800138000"))
        agent.reset()
        assert agent.window_size == 0
        assert agent._chunk_counter == 0

    def test_stats_after_reset(self, agent):
        agent.process_chunk(AuditChunk(data="Phone 13800138000"))
        agent.reset()
        stats = agent.get_window_stats()
        assert stats.total_chunks == 0


# ── AuditChunk ────────────────────────────────────────────────────

class TestAuditChunk:
    def test_chunk_defaults(self):
        chunk = AuditChunk(data="test")
        assert chunk.source == ""
        assert chunk.session_id is None
        assert chunk.user_id is None
        assert isinstance(chunk.timestamp, datetime)

    def test_chunk_with_metadata(self):
        chunk = AuditChunk(data="test", source="output", session_id="s1", user_id="u1")
        assert chunk.source == "output"
        assert chunk.session_id == "s1"
        assert chunk.user_id == "u1"
