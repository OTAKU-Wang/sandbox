"""Certificate model — SM2 PKI certificates for identity and signing."""
import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import String, DateTime, Text, ForeignKey, func, Index, Boolean
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


class CertType(str, Enum):
    """Certificate types per GM/T 0015."""
    IDENTITY = "identity"       # 身份证书
    SIGNING = "signing"         # 签名证书
    ENCRYPTION = "encryption"   # 加密证书
    PLATFORM = "platform"       # 平台证书


class CertStatus(str, Enum):
    """Certificate lifecycle status."""
    ACTIVE = "active"
    REVOKED = "revoked"
    EXPIRED = "expired"
    SUSPENDED = "suspended"


class Certificate(Base):
    """SM2 X.509v3 certificate for identity and non-repudiation.

    Stores the certificate, its chain, and revocation status.
    Per GM/T 0015 and SS-01 spec requirements.
    """
    __tablename__ = "certificates"
    __table_args__ = (
        Index("ix_certificates_user_status", "user_id", "status"),
        Index("ix_certificates_serial", "serial_number", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    cert_type: Mapped[str] = mapped_column(String(32), nullable=False, default=CertType.IDENTITY.value)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=CertStatus.ACTIVE.value)

    # Certificate data (PEM encoded)
    serial_number: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    subject: Mapped[str] = mapped_column(String(512), nullable=False)  # DN
    issuer: Mapped[str] = mapped_column(String(512), nullable=False)   # Issuer DN
    public_key: Mapped[str] = mapped_column(Text, nullable=False)      # SM2 public key (hex)
    certificate_pem: Mapped[str] = mapped_column(Text, nullable=False) # Full cert PEM

    # Chain
    parent_cert_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("certificates.id"))
    is_ca: Mapped[bool] = mapped_column(Boolean, default=False)

    # Validity
    not_before: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    not_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Revocation
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoke_reason: Mapped[str | None] = mapped_column(String(255))

    # Audit
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
