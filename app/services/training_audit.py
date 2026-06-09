"""Training Audit — immutable audit log for ML training lifecycle.

Records every significant training event:
1. Job creation — config snapshot, requester identity
2. Training start/end — timestamps, resource allocation
3. Checkpoint saves — hash chain for integrity
4. Output inspection — result verification events
5. Error/failure events — with context

Each entry is hash-chained (SM3) to prevent tampering.
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from app.services.crypto_service import crypto_service

logger = logging.getLogger(__name__)


class AuditEventType(str, Enum):
    JOB_CREATED = "job_created"
    JOB_STARTED = "job_started"
    JOB_COMPLETED = "job_completed"
    JOB_FAILED = "job_failed"
    CHECKPOINT_SAVED = "checkpoint_saved"
    OUTPUT_INSPECTED = "output_inspected"
    CONFIG_VALIDATED = "config_validated"
    DP_BUDGET_CONSUMED = "dp_budget_consumed"


@dataclass
class AuditEntry:
    """Single audit log entry with hash chain."""
    entry_id: str
    event_type: AuditEventType
    job_id: str
    timestamp: datetime
    actor: str = ""
    details: dict = field(default_factory=dict)
    prev_hash: str = ""
    entry_hash: str = ""


class TrainingAuditLog:
    """Immutable, hash-chained audit log for training lifecycle.

    Each entry's hash includes the previous entry's hash,
    forming a tamper-evident chain.
    """

    def __init__(self):
        self._entries: list[AuditEntry] = []
        self._job_entries: dict[str, list[AuditEntry]] = {}

    def record(
        self,
        event_type: AuditEventType,
        job_id: str,
        actor: str = "",
        details: dict | None = None,
    ) -> AuditEntry:
        """Record a training audit event.

        Args:
            event_type: Type of event.
            job_id: Training job identifier.
            actor: Who triggered the event.
            details: Additional event-specific data.

        Returns:
            The created AuditEntry with computed hash.
        """
        entry_id = crypto_service.sm3_hash(
            f"{job_id}:{event_type.value}:{datetime.now(timezone.utc).isoformat()}".encode()
        )[:16]

        prev_hash = self._entries[-1].entry_hash if self._entries else "0" * 64

        entry = AuditEntry(
            entry_id=entry_id,
            event_type=event_type,
            job_id=job_id,
            timestamp=datetime.now(timezone.utc),
            actor=actor,
            details=details or {},
            prev_hash=prev_hash,
        )

        # Compute entry hash (SM3 of: entry_id + event_type + job_id + timestamp + prev_hash + details)
        hash_input = (
            f"{entry_id}:{event_type.value}:{job_id}:"
            f"{entry.timestamp.isoformat()}:{prev_hash}:"
            f"{_stable_json(details or {})}"
        )
        entry.entry_hash = crypto_service.sm3_hash(hash_input.encode())

        self._entries.append(entry)
        self._job_entries.setdefault(job_id, []).append(entry)

        logger.info(f"Audit: {event_type.value} job={job_id} actor={actor} id={entry_id}")
        return entry

    def get_job_history(self, job_id: str) -> list[AuditEntry]:
        """Get all audit entries for a specific job."""
        return list(self._job_entries.get(job_id, []))

    def get_all_entries(self) -> list[AuditEntry]:
        """Get all audit entries."""
        return list(self._entries)

    def verify_chain(self) -> bool:
        """Verify the integrity of the hash chain.

        Returns True if all entries' hashes and prev_hash links are valid.
        """
        for i, entry in enumerate(self._entries):
            # Verify prev_hash link
            if i == 0:
                if entry.prev_hash != "0" * 64:
                    return False
            else:
                if entry.prev_hash != self._entries[i - 1].entry_hash:
                    return False

            # Verify entry hash
            hash_input = (
                f"{entry.entry_id}:{entry.event_type.value}:{entry.job_id}:"
                f"{entry.timestamp.isoformat()}:{entry.prev_hash}:"
                f"{_stable_json(entry.details)}"
            )
            expected = crypto_service.sm3_hash(hash_input.encode())
            if entry.entry_hash != expected:
                return False

        return True

    def count(self) -> int:
        return len(self._entries)


def _stable_json(d: dict) -> str:
    """Deterministic JSON string for hashing."""
    import json
    return json.dumps(d, sort_keys=True, separators=(",", ":"))


# Singleton
training_audit = TrainingAuditLog()
