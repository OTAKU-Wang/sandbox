"""Cross-Space Connector — bridges external trusted data spaces.

A connector represents a remote trusted data space that can:
1. Browse the local data product catalog
2. Create cross-space contracts
3. Proxy sandbox sessions for data product usage
"""
import uuid
import secrets
from datetime import datetime
from enum import Enum

from sqlalchemy import String, DateTime, ForeignKey, func, JSON, Text, Boolean, Index
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


class ConnectorStatus(str, Enum):
    PENDING = "pending"          # Awaiting verification
    ACTIVE = "active"            # Verified and operational
    SUSPENDED = "suspended"      # Temporarily disabled
    REVOKED = "revoked"          # Permanently disabled


class Connector(Base):
    """A registered external trusted data space."""
    __tablename__ = "connectors"
    __table_args__ = (
        Index("ix_connectors_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    space_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)  # External space identifier
    space_name: Mapped[str] = mapped_column(String(255), nullable=False)
    space_url: Mapped[str] = mapped_column(String(512), nullable=False)  # Base URL of remote space
    description: Mapped[str | None] = mapped_column(Text)

    # Authentication
    api_key_hash: Mapped[str] = mapped_column(String(64), nullable=False)  # SHA-256 of the API key
    api_key_prefix: Mapped[str] = mapped_column(String(8), nullable=False)  # First 8 chars for identification

    # Capabilities
    supported_protocols: Mapped[list] = mapped_column(JSON, nullable=False, default=lambda: ["catalog", "contract", "sandbox-proxy"])
    max_concurrent_sessions: Mapped[int] = mapped_column(default=5)
    allowed_product_types: Mapped[list] = mapped_column(JSON, nullable=False, default=lambda: ["structured", "semi-structured", "unstructured"])

    # Status
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=ConnectorStatus.PENDING.value)
    is_healthy: Mapped[bool] = mapped_column(Boolean, default=True)
    last_heartbeat: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Metadata
    registered_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    extra_config: Mapped[dict | None] = mapped_column("metadata", JSON)  # Extra config (certificates, IP whitelist, etc.)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ConnectorSession(Base):
    """A proxied sandbox session from an external space."""
    __tablename__ = "connector_sessions"
    __table_args__ = (
        Index("ix_connector_sessions_connector", "connector_id"),
        Index("ix_connector_sessions_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    connector_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("connectors.id"), nullable=False, index=True)
    contract_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("contracts.id"), nullable=False)
    sandbox_session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("sandbox_sessions.id"), nullable=True)

    # Remote identity
    remote_user_id: Mapped[str] = mapped_column(String(255), nullable=False)  # User ID in the remote space
    remote_session_id: Mapped[str | None] = mapped_column(String(255))  # Session ID in the remote space

    # Status
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("data_products.id"), nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


def generate_api_key() -> tuple[str, str]:
    """Generate a connector API key. Returns (full_key, sha256_hash)."""
    full_key = f"cds-conn-{secrets.token_urlsafe(32)}"
    import hashlib
    key_hash = hashlib.sha256(full_key.encode()).hexdigest()
    return full_key, key_hash
