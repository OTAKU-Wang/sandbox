"""Idempotency-key dedup table for cube egress audit ingestion (SS-10 CB-06).

The node audit tailer pushes at-least-once; a batch can be re-delivered after
crash/restart.  One row per (node_id, file_name, offset, line_no) guarantees
the CDS endpoint stores each forced-point audit line exactly once.  `offset`
is the line's byte offset in the source JSONL; `file_name` embeds the source
inode (rotation-safe idempotency key).
"""
import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String, UniqueConstraint, Index, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class CubeEgressDedup(Base):
    __tablename__ = "cube_egress_dedup"
    __table_args__ = (
        UniqueConstraint(
            "node_id", "file_name", "offset", "line_no",
            name="uq_cube_egress_dedup_key",
        ),
        Index("ix_cube_egress_dedup_node", "node_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    node_id: Mapped[str] = mapped_column(String(128), nullable=False)
    file_name: Mapped[str] = mapped_column(String(512), nullable=False)
    offset: Mapped[int] = mapped_column(BigInteger, nullable=False)
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
