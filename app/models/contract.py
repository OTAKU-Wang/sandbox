import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import String, DateTime, Text, ForeignKey, func, JSON, Numeric, Integer, Index
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


class ContractStatus(str, Enum):
    DRAFT = "draft"
    NEGOTIATING = "negotiating"
    SIGNED = "signed"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    COMPLETED = "completed"
    BREACHED = "breached"
    VIOLATED = "violated"
    TERMINATED = "terminated"
    ARCHIVED = "archived"


class ContractType(str, Enum):
    DATA_QUERY = "data_query"
    MODEL_TRAINING = "model_training"
    DATA_APPLICATION = "data_application"
    API_SERVICE = "api_service"
    JOINT_COMPUTE = "joint_compute"
    PRODUCT_DEV = "product_dev"
    DATA_MODELING = "data_modeling"


class Contract(Base):
    __tablename__ = "contracts"
    __table_args__ = (
        Index("ix_contracts_provider_status", "provider_id", "status"),
        Index("ix_contracts_buyer_status", "buyer_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    contract_no: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    contract_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=ContractStatus.DRAFT.value)

    # Parties
    provider_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    buyer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)

    # Terms
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    terms: Mapped[dict | None] = mapped_column(JSON)  # Contract terms as JSON
    product_ids: Mapped[list] = mapped_column(JSON, nullable=False)  # 1:N list of data_product UUIDs
    allowed_sandbox_levels: Mapped[str | None] = mapped_column(String(128))  # e.g. "L2,L3"
    allowed_sandbox_modes: Mapped[list | None] = mapped_column(JSON)  # e.g. ["query","train","develop"]
    allowed_operations: Mapped[str | None] = mapped_column(Text)  # e.g. "read,analyze,train"
    max_duration_hours: Mapped[int] = mapped_column(Integer, default=24)
    dp_epsilon_budget: Mapped[float | None] = mapped_column(Numeric(10, 4))  # Differential privacy budget
    max_output_rows: Mapped[int] = mapped_column(Integer, default=10000)  # Max rows in query output
    allowed_output_formats: Mapped[str | None] = mapped_column(String(256), default="csv,json")  # e.g. "csv,json,parquet"
    inspection_rule_set: Mapped[dict | None] = mapped_column(JSON)  # Inspection rules config

    # Signatures
    provider_signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    buyer_signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provider_signature: Mapped[str | None] = mapped_column(Text)  # SM2 signature (hex)
    buyer_signature: Mapped[str | None] = mapped_column(Text)  # SM2 signature (hex)
    platform_signature: Mapped[str | None] = mapped_column(Text)  # Platform witness SM2 signature (hex)
    platform_signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Blockchain
    blockchain_tx_hash: Mapped[str | None] = mapped_column(String(255))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
