"""Watermark Record — tracks model-weight watermarks embedded in training outputs."""
import uuid
from datetime import datetime

from sqlalchemy import String, DateTime, Integer, ForeignKey, func, JSON, Index
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


class WatermarkRecord(Base):
    """Persistent record of an embedded model-weight watermark.

    Stores the metadata needed to later verify ownership of a trained model.
    """
    __tablename__ = "watermark_records"
    __table_args__ = (
        Index("ix_watermark_records_job_id", "job_id"),
        Index("ix_watermark_records_owner", "owner_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Linkage
    job_id: Mapped[str] = mapped_column(String(64), nullable=False)  # training job id
    owner_id: Mapped[str] = mapped_column(String(128), nullable=False)  # owner identifier
    model_id: Mapped[str] = mapped_column(String(128), nullable=False)  # model identifier

    # Watermark parameters (enough to reproduce verification)
    layer_positions: Mapped[dict] = mapped_column(JSON, nullable=False)  # {layer_name: [indices]}
    ordered_positions: Mapped[list] = mapped_column(JSON, nullable=False, default=list)  # [[layer, idx], ...] in embed order
    key_seed: Mapped[int] = mapped_column(Integer, nullable=False)
    num_bits: Mapped[int] = mapped_column(Integer, nullable=False)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
