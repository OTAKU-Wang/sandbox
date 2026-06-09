"""Tests for Training Audit Log (S6-5)."""
import pytest

from app.services.training_audit import (
    TrainingAuditLog, AuditEventType, AuditEntry, training_audit,
)


@pytest.fixture
def audit():
    return TrainingAuditLog()


class TestRecordEvent:
    def test_record_creates_entry(self, audit):
        entry = audit.record(AuditEventType.JOB_CREATED, "job-1", actor="user@example.com")
        assert entry is not None
        assert entry.event_type == AuditEventType.JOB_CREATED
        assert entry.job_id == "job-1"
        assert entry.actor == "user@example.com"
        assert len(entry.entry_id) == 16
        assert len(entry.entry_hash) == 64

    def test_record_with_details(self, audit):
        details = {"learning_rate": 0.001, "epochs": 10}
        entry = audit.record(AuditEventType.CONFIG_VALIDATED, "job-2", details=details)
        assert entry.details == details

    def test_record_increments_count(self, audit):
        assert audit.count() == 0
        audit.record(AuditEventType.JOB_CREATED, "job-1")
        assert audit.count() == 1
        audit.record(AuditEventType.JOB_STARTED, "job-1")
        assert audit.count() == 2


class TestHashChain:
    def test_first_entry_prev_hash_is_zero(self, audit):
        entry = audit.record(AuditEventType.JOB_CREATED, "job-1")
        assert entry.prev_hash == "0" * 64

    def test_chain_links_correctly(self, audit):
        e1 = audit.record(AuditEventType.JOB_CREATED, "job-1")
        e2 = audit.record(AuditEventType.JOB_STARTED, "job-1")
        e3 = audit.record(AuditEventType.JOB_COMPLETED, "job-1")
        assert e2.prev_hash == e1.entry_hash
        assert e3.prev_hash == e2.entry_hash

    def test_verify_chain_valid(self, audit):
        audit.record(AuditEventType.JOB_CREATED, "job-1")
        audit.record(AuditEventType.JOB_STARTED, "job-1")
        audit.record(AuditEventType.JOB_COMPLETED, "job-1")
        assert audit.verify_chain() is True

    def test_verify_chain_empty(self, audit):
        assert audit.verify_chain() is True

    def test_verify_chain_tampered(self, audit):
        audit.record(AuditEventType.JOB_CREATED, "job-1")
        audit.record(AuditEventType.JOB_STARTED, "job-1")
        # Tamper with first entry
        audit._entries[0].entry_hash = "tampered" + "0" * 56
        assert audit.verify_chain() is False


class TestJobHistory:
    def test_get_job_history(self, audit):
        audit.record(AuditEventType.JOB_CREATED, "job-1")
        audit.record(AuditEventType.JOB_STARTED, "job-1")
        audit.record(AuditEventType.JOB_CREATED, "job-2")
        history = audit.get_job_history("job-1")
        assert len(history) == 2
        assert all(e.job_id == "job-1" for e in history)

    def test_get_job_history_empty(self, audit):
        assert audit.get_job_history("nonexistent") == []


class TestAllEntries:
    def test_get_all_entries(self, audit):
        audit.record(AuditEventType.JOB_CREATED, "job-1")
        audit.record(AuditEventType.JOB_CREATED, "job-2")
        entries = audit.get_all_entries()
        assert len(entries) == 2


class TestEventTypes:
    def test_all_event_types_storable(self, audit):
        for event_type in AuditEventType:
            entry = audit.record(event_type, f"job-{event_type.value}")
            assert entry.event_type == event_type
        assert audit.count() == len(AuditEventType)


class TestSingleton:
    def test_singleton_exists(self):
        assert training_audit is not None
        assert isinstance(training_audit, TrainingAuditLog)
