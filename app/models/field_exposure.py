"""Field Exposure Approval — controls which data fields buyers can access.

When a data product contains sensitive fields (PII, financial, health),
buyers must request access to specific fields. The provider approves/rejects.
This prevents unauthorized exposure of sensitive data columns.
"""
import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import String, DateTime, ForeignKey, func, JSON, Text, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


class ExposureRequestStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    REVOKED = "revoked"


class FieldSensitivity(str, Enum):
    PUBLIC = "public"           # No approval needed
    INTERNAL = "internal"       # Auto-approve for authenticated users
    SENSITIVE = "sensitive"     # Requires provider approval
    PII = "pii"                # Requires provider approval + justification
    RESTRICTED = "restricted"  # Requires admin approval


class FieldExposureRequest(Base):
    """A request from a buyer to access specific fields of a data product."""
    __tablename__ = "field_exposure_requests"
    __table_args__ = (
        Index("ix_field_exposure_product_buyer", "product_id", "buyer_id"),
        Index("ix_field_exposure_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("data_products.id"), nullable=False, index=True)
    buyer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    contract_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("contracts.id"), nullable=True)

    # Which fields the buyer wants access to
    requested_fields: Mapped[list] = mapped_column(JSON, nullable=False)  # ["field1", "field2"]
    # Business justification (required for PII fields)
    justification: Mapped[str | None] = mapped_column(Text)
    # Provider's response
    approved_fields: Mapped[list | None] = mapped_column(JSON)  # Subset of requested_fields that are approved
    rejection_reason: Mapped[str | None] = mapped_column(Text)

    status: Mapped[str] = mapped_column(String(32), nullable=False, default=ExposureRequestStatus.PENDING.value)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # Approval can expire

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    product = relationship("DataProduct")
    buyer = relationship("User", foreign_keys=[buyer_id])
    reviewer = relationship("User", foreign_keys=[reviewed_by])


class FieldVisibilityConfig(Base):
    """Per-product field visibility configuration set by the provider."""
    __tablename__ = "field_visibility_configs"
    __table_args__ = (
        Index("ix_field_visibility_product", "product_id", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("data_products.id"), nullable=False, unique=True)

    # Field visibility rules: {field_name: {sensitivity, mask_pattern, auto_approve, description}}
    field_rules: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # Default sensitivity for unlisted fields
    default_sensitivity: Mapped[str] = mapped_column(String(32), nullable=False, default=FieldSensitivity.INTERNAL.value)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    product = relationship("DataProduct")
