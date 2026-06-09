"""Federation Trust model — persistent storage for cross-space trust relationships.

Stores bilateral trust agreements and audit entries so they survive restarts.
"""
import uuid
from datetime import datetime

from sqlalchemy import String, DateTime, Boolean, Text, func, Index, JSON
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


class FederationTrustRecord(Base):
    """Persistent record of a federation trust relationship."""
    __tablename__ = "federation_trusts"
    __table_args__ = (
        Index("ix_federation_trusts_local", "local_space_id"),
        Index("ix_federation_trusts_remote", "remote_space_id"),
        Index("ix_federation_trusts_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    local_space_id: Mapped[str] = mapped_column(String(128), nullable=False)
    local_space_name: Mapped[str] = mapped_column(String(256), nullable=False)
    local_endpoint: Mapped[str] = mapped_column(String(512), nullable=False)
    remote_space_id: Mapped[str] = mapped_column(String(128), nullable=False)
    remote_space_name: Mapped[str] = mapped_column(String(256), nullable=False)
    remote_endpoint: Mapped[str] = mapped_column(String(512), nullable=False)
    trust_level: Mapped[str] = mapped_column(String(32), nullable=False)  # TrustLevel value
    status: Mapped[str] = mapped_column(String(32), nullable=False)  # FederationStatus value
    allowed_operations_json: Mapped[str | None] = mapped_column(Text)  # JSON list
    policy_sync_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_sync: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FederationAuditRecord(Base):
    """Persistent record of a federation audit entry."""
    __tablename__ = "federation_audit_log"
    __table_args__ = (
        Index("ix_federation_audit_source", "source_space"),
        Index("ix_federation_audit_target", "target_space"),
        Index("ix_federation_audit_time", "timestamp"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entry_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source_space: Mapped[str] = mapped_column(String(128), nullable=False)
    target_space: Mapped[str] = mapped_column(String(128), nullable=False)
    operation: Mapped[str] = mapped_column(String(128), nullable=False)
    resource: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    user_id: Mapped[str | None] = mapped_column(String(128))
    details_json: Mapped[str | None] = mapped_column(Text)  # JSON dict
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
