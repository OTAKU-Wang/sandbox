"""App Credential Model — application credentials for contract gateway access.

Each credential binds an app (consumer) to a contract, enabling secure
data product access via app_id + app_secret authentication.
"""
import uuid
import secrets
from datetime import datetime
from enum import Enum

from sqlalchemy import String, DateTime, Text, ForeignKey, func, JSON, Integer, BigInteger, Boolean, Index
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


class CredentialStatus(str, Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    REVOKED = "revoked"
    EXPIRED = "expired"


class AppCredential(Base):
    __tablename__ = "app_credentials"
    __table_args__ = (
        Index("ix_app_credentials_contract", "contract_id", "status"),
        Index("ix_app_credentials_consumer", "consumer_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    app_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    app_secret_hash: Mapped[str] = mapped_column(String(128), nullable=False)  # SM3(app_secret)
    contract_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("contracts.id"), nullable=False)
    consumer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=CredentialStatus.ACTIVE.value)

    # Rate limiting
    rate_limit: Mapped[int] = mapped_column(Integer, default=100)  # requests/minute
    quota_rows: Mapped[int] = mapped_column(BigInteger, default=1000000)  # rows/day
    quota_bytes: Mapped[int] = mapped_column(BigInteger, default=1073741824)  # 1GB/day

    # Security
    allowed_ips: Mapped[list | None] = mapped_column(JSON)  # IP whitelist, null = any
    description: Mapped[str | None] = mapped_column(Text)

    # Lifecycle
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime)
    revoke_reason: Mapped[str | None] = mapped_column(String(255))

    @staticmethod
    def generate_app_id() -> str:
        """Generate a unique app_id."""
        return f"app_{secrets.token_hex(16)}"

    @staticmethod
    def generate_app_secret() -> str:
        """Generate a random app_secret."""
        return secrets.token_hex(32)
