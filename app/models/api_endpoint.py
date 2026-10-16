"""ApiEndpoint Model — 合约下的 OpenAPI 交付端点（成果交付通道之二）."""
import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import String, DateTime, ForeignKey, func, JSON, Integer, Index
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


class ApiEndpointStatus(str, Enum):
    ENABLED = "enabled"
    DISABLED = "disabled"


class ApiEndpoint(Base):
    __tablename__ = "api_endpoints"
    __table_args__ = (
        Index("ix_api_endpoints_contract_status", "contract_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    contract_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("contracts.id"), nullable=False, index=True)
    workload_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("workloads.id"))

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    method: Mapped[str] = mapped_column(String(8), nullable=False, default="POST", server_default="POST")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=ApiEndpointStatus.ENABLED.value)

    request_schema: Mapped[dict | None] = mapped_column(JSON)
    response_schema: Mapped[dict | None] = mapped_column(JSON)
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=60, server_default="60")

    rate_limit_per_minute: Mapped[int] = mapped_column(Integer, nullable=False, default=60, server_default="60")
    quota_total: Mapped[int | None] = mapped_column(Integer)
    calls_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_called_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
