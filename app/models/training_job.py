"""Training Job model — persistent tracking for AI training sessions."""
import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import String, DateTime, Integer, Text, ForeignKey, func, JSON, Index
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


class TrainingJobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TrainingJobType(str, Enum):
    LLM_SFT = "llm_sft"
    VISION = "vision"
    MULTIMODAL = "multimodal"


class TrainingJob(Base):
    """Persistent training job for AI model fine-tuning."""
    __tablename__ = "training_jobs"
    __table_args__ = (
        Index("ix_training_jobs_status", "status"),
        Index("ix_training_jobs_user_status", "user_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    job_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=TrainingJobStatus.PENDING.value)

    # Configuration
    config: Mapped[dict | None] = mapped_column(JSON)  # SFTConfig, model params, etc.
    base_model: Mapped[str | None] = mapped_column(String(128))
    dataset_path: Mapped[str | None] = mapped_column(Text)

    # Results
    metrics: Mapped[dict | None] = mapped_column(JSON)  # loss, accuracy, MIA score, etc.
    output_path: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)

    # Association
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("sandbox_sessions.id"))

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
