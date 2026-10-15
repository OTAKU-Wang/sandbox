"""API Key Model — platform machine credentials with functional scope domains (SS-12).

Issued by platform admins to external agent intelligences. The bare key
(``cds_ak_`` + 43-char base64url random segment) is returned EXACTLY ONCE at
create/rotate time; only its SM3 hash + a display prefix are persisted.
Lifecycle: active / disabled / revoked (terminal). Revocation is a status
flip with a forensic record — rows are never physically deleted (same
discipline as app_credentials).
"""
import base64
import secrets
import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import String, DateTime, Text, ForeignKey, func, JSON, Integer, Index
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base

KEY_PREFIX_TOKEN = "cds_ak_"


class ApiKeyStatus(str, Enum):
    ACTIVE = "active"
    DISABLED = "disabled"
    REVOKED = "revoked"


class ApiKey(Base):
    __tablename__ = "api_keys"
    __table_args__ = (
        Index("ix_api_keys_status", "status"),
        Index("ix_api_keys_issued_by", "issued_by"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    # SM3(bare key) hex — unique lookup path, the only key material at rest.
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    key_prefix: Mapped[str] = mapped_column(String(16), nullable=False)  # "cds_ak_" + 4 chars
    scopes: Mapped[list] = mapped_column(JSON, nullable=False, default=list)  # ["sessions:write", ...]
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=ApiKeyStatus.ACTIVE.value)
    rate_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=120)  # req/min
    issued_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoke_reason: Mapped[str | None] = mapped_column(String(255))

    @staticmethod
    def generate_bare_key() -> str:
        """`cds_ak_` + base64url(32 random bytes) = 43-char secret segment, 50 total."""
        secret = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii").rstrip("=")
        return f"{KEY_PREFIX_TOKEN}{secret}"

    @staticmethod
    def display_prefix(bare_key: str) -> str:
        """Display prefix: `cds_ak_` + first 4 chars of the random segment."""
        random_part = bare_key[len(KEY_PREFIX_TOKEN):]
        return f"{KEY_PREFIX_TOKEN}{random_part[:4]}"

    def is_expired(self, now: datetime | None = None) -> bool:
        """Lazy expiry check — no batch job (spec 4.3: 惰性判定)."""
        if self.expires_at is None:
            return False
        now = now or datetime.now()
        if now.tzinfo is None:
            from datetime import timezone
            now = now.replace(tzinfo=timezone.utc)
        expires_at = self.expires_at
        if expires_at.tzinfo is None:
            from datetime import timezone
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        return now >= expires_at
