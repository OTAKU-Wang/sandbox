"""PolicyBundle model — stores compiled contract policies."""
import uuid
from datetime import datetime

from sqlalchemy import String, DateTime, Text, ForeignKey, func, JSON, Index
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


class PolicyBundle(Base):
    """Compiled policy bundle for a contract.

    Stores the Rego source code and field-level ACL rules
    that control what operations are allowed in a sandbox.
    """
    __tablename__ = "policy_bundles"
    __table_args__ = (
        Index("ix_policy_bundles_contract", "contract_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    policy_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    contract_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("contracts.id"), nullable=False)
    rego_source: Mapped[str] = mapped_column(Text, nullable=False)  # OPA Rego source
    integrity_hash: Mapped[str] = mapped_column(String(64), nullable=False)  # SM3 hash of rego_source
    field_acl: Mapped[dict | None] = mapped_column(JSON)  # {field: {read, aggregate_only, mask_pattern}}
    allowed_ops: Mapped[list | None] = mapped_column(JSON)  # ["read","query","train","export"]
    sandbox_modes: Mapped[list | None] = mapped_column(JSON)  # ["query","train","develop","application"]
    version: Mapped[int] = mapped_column(default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
