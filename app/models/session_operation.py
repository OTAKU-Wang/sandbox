"""W11: long-running session operations (snapshots / rollbacks).

Async operation model (CubeSandbox C5 alignment): long workspace operations
can return 202 + operation_id; clients poll GET /operations/{op_id} for
status/progress/result.
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, Index, JSON, String, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class SessionOperation(Base):
    __tablename__ = "session_operations"
    __table_args__ = (
        Index("ix_session_operations_session_created", "session_id", "created_at"),
        # A82: the rollback exclusivity gate was SELECT-then-INSERT, so two
        # concurrent rollbacks of one session both passed it. The predicate stays
        # scoped to the exclusive op type and to the in-flight statuses only —
        # snapshot/template_build must stay concurrent, and a finished rollback
        # must not block the next one. Enforcing it here (not in the service)
        # is what makes the race window fail closed.
        Index(
            "uq_session_ops_rollback_inflight",
            "session_id",
            "op_type",
            unique=True,
            postgresql_where=text("op_type = 'rollback' AND status IN ('pending', 'running')"),
            sqlite_where=text("op_type = 'rollback' AND status IN ('pending', 'running')"),
        ),
    )

    op_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    # snapshot | rollback | template_seed
    op_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # pending | running | succeeded | failed
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    progress: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    error: Mapped[str | None] = mapped_column(Text)
    # e.g. {"snapshot_id": "..."} on success
    result_ref: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # A82 lease: `started_at` is when the row entered `running`, `finished_at` when
    # it reached a terminal status. A row left `running` by a restarted process is
    # only reclaimable (and only provably stale) against one of these timestamps;
    # without them the rollback gate refuses that session forever.
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
