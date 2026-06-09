"""KMS Key Metadata — tracks key lifecycle for audit and compliance."""
import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import String, DateTime, Text, ForeignKey, func, JSON, Index
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


class KeyStatus(str, Enum):
    ACTIVE = "active"
    ROTATED = "rotated"
    DESTROYED = "destroyed"
    SUSPENDED = "suspended"


class KeyType(str, Enum):
    DEK = "dek"  # Data Encryption Key
    SESSION = "session"  # Sandbox session key
    KEK = "kek"  # Key Encryption Key (wraps DEKs)


class KeyMetadata(Base):
    __tablename__ = "key_metadata"
    __table_args__ = (
        Index("ix_key_metadata_status", "status"),
        Index("ix_key_metadata_product", "product_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    key_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    key_type: Mapped[str] = mapped_column(String(32), nullable=False, default=KeyType.DEK.value)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=KeyStatus.ACTIVE.value)

    # Association
    product_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("data_products.id"), nullable=True)
    session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("sandbox_sessions.id"), nullable=True)

    # Lifecycle
    algorithm: Mapped[str] = mapped_column(String(32), default="SM4-GCM")
    rotated_to: Mapped[str | None] = mapped_column(String(255))  # New key_id after rotation
    destroy_reason: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    destroyed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
