"""Pipeline Task model — persistent task tracking for data pipelines."""
import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import String, DateTime, Integer, Text, ForeignKey, func, JSON, Index
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


class PipelineTaskStatus(str, Enum):
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PipelineTaskType(str, Enum):
    OCR = "ocr"
    ASR = "asr"
    VIDEO = "video"
    DOCUMENT = "document"
    DICOM = "dicom"
    CDC_SYNC = "cdc_sync"  # CDC data synchronization


class PipelineTask(Base):
    """Persistent pipeline task for async data processing."""
    __tablename__ = "pipeline_tasks"
    __table_args__ = (
        Index("ix_pipeline_tasks_status", "status"),
        Index("ix_pipeline_tasks_user_status", "user_id", "status"),
        Index("ix_pipeline_tasks_created", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    task_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=PipelineTaskStatus.PENDING.value)
    priority: Mapped[int] = mapped_column(Integer, default=0)  # Higher = more urgent

    # Input/Output
    input_path: Mapped[str] = mapped_column(Text, nullable=False)
    output_path: Mapped[str | None] = mapped_column(Text)
    options: Mapped[dict | None] = mapped_column(JSON)

    # Results
    result: Mapped[dict | None] = mapped_column(JSON)
    error_message: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int | None] = mapped_column(Integer)

    # Association
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("sandbox_sessions.id"))

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
