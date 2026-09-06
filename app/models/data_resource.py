"""Data Resource — encrypted data source linked to data products.

Supports file upload (CSV/Parquet/JSON/image), API endpoints, and database connections.
All file data is SM4-GCM encrypted before storage in MinIO.
"""
import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import String, DateTime, Integer, ForeignKey, func, JSON, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


class ResourceType(str, Enum):
    FILE = "file"          # Uploaded file (CSV, Parquet, JSON, image, etc.)
    API = "api"            # API endpoint
    DATABASE = "database"  # External database connection


class ResourceFormat(str, Enum):
    CSV = "csv"
    PARQUET = "parquet"
    JSON = "json"
    IMAGE = "image"
    TEXT = "text"
    PDF = "pdf"
    DICOM = "dicom"
    OTHER = "other"


class ResourceStatus(str, Enum):
    UPLOADING = "uploading"
    ENCRYPTING = "encrypting"
    READY = "ready"
    FAILED = "failed"


class DataResource(Base):
    """A data source that can be loaded into sandbox sessions."""
    __tablename__ = "data_resources"
    __table_args__ = (
        Index("ix_data_resources_provider_status", "provider_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1000))
    resource_type: Mapped[str] = mapped_column(String(32), nullable=False, default=ResourceType.FILE.value)
    format: Mapped[str] = mapped_column(String(32), nullable=False, default=ResourceFormat.CSV.value)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=ResourceStatus.UPLOADING.value)

    # Storage
    storage_path: Mapped[str | None] = mapped_column(String(512))       # MinIO object name
    encryption_key_id: Mapped[str | None] = mapped_column(String(255))  # KMS DEK reference
    chunk_refs: Mapped[list | None] = mapped_column(JSON)               # [{path, iv, auth_tag, sm3_hash, size}]
    file_size_bytes: Mapped[int | None] = mapped_column(Integer)
    sm3_checksum: Mapped[str | None] = mapped_column(String(64))       # SM3 hash of original data

    # Schema metadata
    schema_fields: Mapped[list | None] = mapped_column(JSON)            # [{name, type, sensitivity, mask_pattern}]
    row_count: Mapped[int | None] = mapped_column(Integer)
    error_message: Mapped[str | None] = mapped_column(String(1000))    # Last error if status=failed

    # Gap A5/T7: field-level classification map {field: level} where
    # level ∈ {1,2,3,4} (public/internal/confidential/secret). Level 3 fields
    # are force-masked on output, level 4 fields are denied out of the
    # sandbox. Consumed by policy_compiler.field_rules_from_classifications.
    field_classifications: Mapped[dict | None] = mapped_column(JSON)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    provider = relationship("User", back_populates="data_resources")
    data_products = relationship("DataProduct", back_populates="resource")
