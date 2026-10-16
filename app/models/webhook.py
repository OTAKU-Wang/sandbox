"""WebhookSubscription Model — 结果推送订阅（成果交付通道之三）."""
import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import String, DateTime, Text, ForeignKey, func, JSON, Integer, Index
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


class WebhookStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"


class WebhookEvent(str, Enum):
    COMPUTE_RUN_SUCCEEDED = "compute_run.succeeded"
    COMPUTE_RUN_FAILED = "compute_run.failed"
    WORKLOAD_PUBLISHED = "workload.published"
    WORKLOAD_TEST_FINISHED = "workload.test_finished"


class WebhookSubscription(Base):
    __tablename__ = "webhook_subscriptions"
    __table_args__ = (
        Index("ix_webhook_subs_user_status", "user_id", "status"),
        Index("ix_webhook_subs_contract", "contract_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    contract_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("contracts.id"))

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    url: Mapped[str] = mapped_column(String(1024), nullable=False)
    # SM4-GCM 加密后的签名密钥（utils.crypto.SM4Cipher），明文只在创建/轮转时返回一次
    secret_enc: Mapped[str | None] = mapped_column(Text)
    events: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    status: Mapped[str] = mapped_column(String(16), nullable=False, default=WebhookStatus.ACTIVE.value)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(String(512))
    last_delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
