"""Sandbox Task model — tasks executed within sandbox sessions."""
import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import String, DateTime, Integer, Text, ForeignKey, func, JSON, Numeric, Index
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


class TaskType(str, Enum):
    """Types of sandbox tasks."""
    QUERY = "query"
    TRAIN = "train"
    ANALYZE = "analyze"
    EXPORT = "export"
    CUSTOM = "custom"


class TaskStatus(str, Enum):
    """Task lifecycle status."""
    PENDING = "pending"
    CODE_SCANNING = "code_scanning"
    RUNNING = "running"
    OUTPUT_REVIEW = "output_review"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


class SandboxTask(Base):
    """Task executed within a sandbox session.

    Lifecycle: pending → code_scanning → running → output_review → completed/failed
    """
    __tablename__ = "sandbox_tasks"
    __table_args__ = (
        Index("ix_sandbox_tasks_session_status", "session_id", "status"),
        Index("ix_sandbox_tasks_user", "user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("sandbox_sessions.id"), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    task_type: Mapped[str] = mapped_column(String(32), nullable=False, default=TaskType.QUERY.value)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=TaskStatus.PENDING.value)
    # Gap A4: declared purpose, validated against the governing contract's
    # purpose limitation at task creation/submission.
    purpose: Mapped[str | None] = mapped_column(String(64))

    # Code/execution
    code_hash: Mapped[str | None] = mapped_column(String(64))  # SM3 hash of submitted code
    code_content: Mapped[str | None] = mapped_column(Text)  # Encrypted code content
    language: Mapped[str | None] = mapped_column(String(32))  # python, sql, r

    # Resource usage
    resource_usage: Mapped[dict | None] = mapped_column(JSON)  # {cpu_seconds, memory_mb, gpu_seconds}
    dp_epsilon_used: Mapped[float | None] = mapped_column(Numeric(10, 4))  # DP budget consumed
    output_rows: Mapped[int] = mapped_column(Integer, default=0)

    # Timing
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=3600)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(Text)

    # Audit
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
