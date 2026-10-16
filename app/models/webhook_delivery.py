"""WebhookDelivery Model — 单次推送投递记录（含重试与死信）."""
import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import String, DateTime, Text, ForeignKey, func, JSON, Integer, Index
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


class DeliveryStatus(str, Enum):
    PENDING = "pending"
    DELIVERING = "delivering"
    DELIVERED = "delivered"
    FAILED = "failed"
    DEAD = "dead"


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"
    __table_args__ = (
        Index("ix_webhook_deliveries_sub_status", "subscription_id", "status"),
        Index("ix_webhook_deliveries_status_retry", "status", "next_retry_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    subscription_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("webhook_subscriptions.id"), nullable=True, index=True
    )

    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    event_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    status: Mapped[str] = mapped_column(String(16), nullable=False, default=DeliveryStatus.PENDING.value)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=6, server_default="6")
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    signature: Mapped[str | None] = mapped_column(String(128))
    response_status: Mapped[int | None] = mapped_column(Integer)
    response_snippet: Mapped[str | None] = mapped_column(Text)
    last_error: Mapped[str | None] = mapped_column(String(512))
    duration_ms: Mapped[int | None] = mapped_column(Integer)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
