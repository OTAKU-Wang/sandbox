import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import String, DateTime, Integer, Text, ForeignKey, func, JSON, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


class DataProductStatus(str, Enum):
    DRAFT = "draft"
    REVIEWING = "reviewing"
    APPROVED = "approved"
    PUBLISHED = "published"
    SUSPENDED = "suspended"
    ARCHIVED = "archived"


class DataProductType(str, Enum):
    STRUCTURED = "structured"  # CSV, Parquet, Excel
    SEMI_STRUCTURED = "semi-structured"  # JSON, XML, JSONL
    UNSTRUCTURED = "unstructured"  # PDF, DICOM, MP4
    API = "api"  # API data source


class DataProduct(Base):
    __tablename__ = "data_products"
    __table_args__ = (
        Index("ix_data_products_provider_status", "provider_id", "status"),
        Index("ix_data_products_industry_status", "industry", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    resource_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("data_resources.id"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    product_type: Mapped[str] = mapped_column(String(32), nullable=False, default=DataProductType.STRUCTURED.value)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=DataProductStatus.DRAFT.value)
    industry: Mapped[str | None] = mapped_column(String(128))  # 行业分类
    data_schema: Mapped[dict | None] = mapped_column(JSON)  # 字段描述 / schema
    row_count: Mapped[int | None] = mapped_column(Integer)  # 数据行数
    encrypted_storage_path: Mapped[str | None] = mapped_column(String(512))  # MinIO path
    encryption_key_id: Mapped[str | None] = mapped_column(String(255))  # KMS key ID
    sm4_checksum: Mapped[str | None] = mapped_column(String(64))  # SM3 hash of encrypted data
    security_level: Mapped[str | None] = mapped_column(String(32))  # public/internal/confidential/secret
    allowed_operations: Mapped[list | None] = mapped_column(JSON)  # ["read","query","export","train"]
    output_constraints: Mapped[dict | None] = mapped_column(JSON)  # {max_rows, max_bytes, dp_epsilon, formats}
    # Version management
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("data_products.id"), nullable=True)
    change_summary: Mapped[str | None] = mapped_column(Text)  # What changed in this version
    is_latest: Mapped[bool] = mapped_column(default=True, nullable=False)  # Only latest version is active
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    provider = relationship("User", back_populates="data_products")
    resource = relationship("DataResource", back_populates="data_products")
