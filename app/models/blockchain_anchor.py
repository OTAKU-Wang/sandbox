"""Blockchain Anchor model — persistent storage for blockchain anchor records.

Stores anchor records so they survive restarts. Used by PGAppendOnlyAdapter
and can be used by other backends for audit trail.
"""
import uuid
from datetime import datetime

from sqlalchemy import String, DateTime, Boolean, Text, func, Index, JSON
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


class BlockchainAnchor(Base):
    """A record anchored to a blockchain backend.

    Stores the data hash, chain hash, and metadata so that
    anchor records persist across application restarts.
    """
    __tablename__ = "blockchain_anchors"
    __table_args__ = (
        Index("ix_blockchain_anchors_backend", "backend"),
        Index("ix_blockchain_anchors_tx_hash", "tx_hash"),
        Index("ix_blockchain_anchors_created", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    data_hash: Mapped[str] = mapped_column(String(64), nullable=False)  # SM3 hex
    backend: Mapped[str] = mapped_column(String(32), nullable=False)  # ChainBackend value
    tx_hash: Mapped[str | None] = mapped_column(String(128))  # chain hash or tx hash
    block_number: Mapped[int | None] = mapped_column()
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    metadata_json: Mapped[str | None] = mapped_column(Text)  # JSON metadata
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
