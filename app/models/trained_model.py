"""N5: trained inference models + inference usage metering (spec N5).

trained_models  — one row per registered inference model (lifecycle:
                  registered → revoked); artifact stored encrypted in
                  storage_service, referenced by artifact_ref.
inference_usage — one row per invoke for token/output metering.
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class TrainedModel(Base):
    __tablename__ = "trained_models"
    __table_args__ = (
        Index("ix_trained_models_status", "status"),
        Index("ix_trained_models_product", "product_id"),
    )

    model_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    # Data product this model serves — a buyer must hold a contract covering
    # product_id to invoke it. Null = no contract gate (internal models).
    product_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    task_id: Mapped[str | None] = mapped_column(String(255), nullable=True)  # source training task
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    format: Mapped[str] = mapped_column(String(32), nullable=False)  # onnx/pickle/safetensors
    artifact_ref: Mapped[str] = mapped_column(String(512), nullable=False)  # storage object name
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="registered")
    watermark_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class InferenceUsage(Base):
    __tablename__ = "inference_usage"
    __table_args__ = (
        Index("ix_inference_usage_model", "model_id"),
        Index("ix_inference_usage_contract", "contract_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    model_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    contract_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    task_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    epsilon_consumed: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="succeeded")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
