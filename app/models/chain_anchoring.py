"""PZ-03: chain anchoring records — persistent anchor trail for all backends.

chain_anchoring_records — one row per (anchor × anchored audit record).
The full AnchorRecord payload (data_hash, chain metadata, timestamp) is kept
in the `receipt` JSON so a fresh adapter instance can re-hydrate its
in-memory anchor cache after a restart and still verify anchors written
before it started.

`audit_record_id` is the anchored audit record the row refers to. Batch
(Merkle) anchors write one row per covered audit record; anchors without
explicit record ids write a single row keyed by the anchor id itself.
"""
import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


class ChainAnchoringRecord(Base):
    __tablename__ = "chain_anchoring_records"
    __table_args__ = (
        Index("ix_chain_anchoring_audit_record", "audit_record_id"),
        Index("ix_chain_anchoring_backend", "backend"),
        Index("ix_chain_anchoring_created", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    audit_record_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    backend: Mapped[str] = mapped_column(String(32), nullable=False)  # ChainBackend value
    tx_hash: Mapped[str | None] = mapped_column(String(128))  # chain hash / tx hash
    block_height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Full AnchorRecord payload for re-hydration (anchor_id, data_hash,
    # chain metadata, timestamp) — the queryable columns above are a
    # projection of it, so restart-verify does not need the legacy table.
    receipt: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
