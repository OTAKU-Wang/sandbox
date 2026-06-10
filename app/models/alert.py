import uuid
from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import DateTime, Index, Integer, JSON, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AlertStatus(str, Enum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


class AlertNotificationStatus(str, Enum):
    NOT_CONFIGURED = "not_configured"
    PENDING = "pending"
    DELIVERED = "delivered"
    FAILED = "failed"


class AlertRecord(Base):
    """Persistent alert center record."""

    __tablename__ = "alert_records"
    __table_args__ = (
        Index("ix_alert_records_status_severity", "status", "severity"),
        Index("ix_alert_records_type_status", "alert_type", "status"),
        Index("ix_alert_records_last_seen", "last_seen_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    dedup_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)

    alert_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=AlertStatus.OPEN.value, index=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)

    user_id: Mapped[str | None] = mapped_column(String(64), index=True)
    session_id: Mapped[str | None] = mapped_column(String(64), index=True)
    resource_type: Mapped[str | None] = mapped_column(String(64), index=True)
    resource_id: Mapped[str | None] = mapped_column(String(255), index=True)
    alert_metadata: Mapped[dict] = mapped_column("metadata", JSON, nullable=False, default=dict)

    occurrence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    acknowledged_by: Mapped[str | None] = mapped_column(String(64))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by: Mapped[str | None] = mapped_column(String(64))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_note: Mapped[str | None] = mapped_column(Text)

    notification_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=AlertNotificationStatus.NOT_CONFIGURED.value,
    )
    notification_results: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
