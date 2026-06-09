"""KMS models — DEK lifecycle, key distribution, and key audit."""
import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import String, DateTime, Integer, Text, ForeignKey, func, JSON, Index
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


class DistributionStatus(str, Enum):
    """Key distribution status."""
    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"
    COMPLETED = "completed"


class DEKStatus(str, Enum):
    """Data Encryption Key status."""
    ACTIVE = "active"
    ROTATED = "rotated"
    REVOKED = "revoked"
    DESTROYED = "destroyed"
    SUSPENDED = "suspended"


# Unified status alias (merges KeyMetadata.KeyStatus)
KeyStatus = DEKStatus

class KeyType(str, Enum):
    """Key type classification (merged from key_metadata)."""
    DEK = "dek"
    SESSION = "session"
    KEK = "kek"


class KeyAlgorithm(str, Enum):
    """Supported key algorithms."""
    SM4_GCM = "SM4-GCM"
    AES256_GCM = "AES-256-GCM"


class DataEncryptionKey(Base):
    """Data Encryption Key (DEK) lifecycle model.

    DEKs are used to encrypt data products. Each DEK has a limited
    usage count and can be rotated or revoked.
    """
    __tablename__ = "data_encryption_keys"
    __table_args__ = (
        Index("ix_dek_product_status", "product_id", "status"),
        Index("ix_dek_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    key_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    key_type: Mapped[str] = mapped_column(String(32), nullable=False, default=KeyType.DEK.value)  # Merged from KeyMetadata
    product_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("data_products.id"), nullable=True, index=True)  # Nullable for session keys
    session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("sandbox_sessions.id"), nullable=True)  # Merged from KeyMetadata
    key_version: Mapped[int] = mapped_column(Integer, default=1)
    algorithm: Mapped[str] = mapped_column(String(32), nullable=False, default=KeyAlgorithm.SM4_GCM.value)
    encrypted_key: Mapped[str | None] = mapped_column(Text, nullable=True)  # Nullable for session keys
    encryption_cert_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("certificates.id"), nullable=True, index=True,
    )  # Certificate whose public key SM2-encrypts this DEK
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=DEKStatus.ACTIVE.value)
    max_usage: Mapped[int] = mapped_column(Integer, default=10000)  # Max usage count
    usage_count: Mapped[int] = mapped_column(Integer, default=0)
    rotated_to: Mapped[str | None] = mapped_column(String(255))  # New key_id after rotation
    destroy_reason: Mapped[str | None] = mapped_column(Text)  # Merged from KeyMetadata
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # Merged from KeyMetadata
    destroyed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # Merged from KeyMetadata
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class KeyAuditLog(Base):
    """Audit log for all key operations."""
    __tablename__ = "key_audit_logs"
    __table_args__ = (
        Index("ix_key_audit_key_id", "key_id"),
        Index("ix_key_audit_operation", "operation"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    key_id: Mapped[str] = mapped_column(String(255), nullable=False)
    operation: Mapped[str] = mapped_column(String(32), nullable=False)  # create, use, rotate, revoke, destroy
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    session_id: Mapped[str | None] = mapped_column(String(255))
    detail: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class KeyDistribution(Base):
    """Key distribution record — tracks DEK distribution to TEE sandboxes (P0-9).

    Records the TEE attestation proof, session key encryption,
    and distribution lifecycle for audit and compliance.
    """
    __tablename__ = "key_distributions"
    __table_args__ = (
        Index("ix_key_dist_key_id", "key_id"),
        Index("ix_key_dist_session", "session_id"),
        Index("ix_key_dist_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    key_id: Mapped[str] = mapped_column(String(255), nullable=False)
    session_id: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=DistributionStatus.ACTIVE.value)

    # TEE attestation proof (P0-9)
    tee_quote_hash: Mapped[str | None] = mapped_column(String(128))  # SM3 hash of raw quote
    tee_mrenclave: Mapped[str | None] = mapped_column(String(128))  # Measurement from TEE
    tee_type: Mapped[str | None] = mapped_column(String(32))  # sgx, sev_snp, firecracker, etc.
    attestation_status: Mapped[str | None] = mapped_column(String(32))  # verified, failed

    # Session key encryption
    session_key_enc: Mapped[str | None] = mapped_column(Text)  # Session key encrypted for transport

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# Compatibility alias — KeyMetadata is now DataEncryptionKey (P1-10 model dedup)
KeyMetadata = DataEncryptionKey
