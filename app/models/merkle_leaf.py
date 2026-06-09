"""Merkle Leaf model — persistent storage for Merkle tree leaves.

Enables third-party verifiable proofs by storing leaf hashes
along with their batch root and position in the tree.
"""
import uuid
from datetime import datetime

from sqlalchemy import String, DateTime, Integer, Text, ForeignKey, func, Index
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


class MerkleLeaf(Base):
    """A single leaf in a Merkle tree batch.

    Stores the hash of an audit event along with its position
    in the tree and the batch root for proof generation.
    """
    __tablename__ = "merkle_leaves"
    __table_args__ = (
        Index("ix_merkle_leaves_batch", "batch_id"),
        Index("ix_merkle_leaves_audit", "audit_event_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    batch_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    leaf_index: Mapped[int] = mapped_column(Integer, nullable=False)
    leaf_hash: Mapped[str] = mapped_column(String(64), nullable=False)  # SM3/SHA-256 hex

    # Link to the audit event
    audit_event_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("audit_logs.id"))

    # Batch root when this leaf was anchored
    batch_root: Mapped[str | None] = mapped_column(String(64))
    anchored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Metadata
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
