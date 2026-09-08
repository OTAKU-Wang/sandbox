"""N3: MPC key persistence — Shamir secret shares at rest (spec N3).

mpc_keys        — one row per split secret (lifecycle: active → rotated/destroyed)
mpc_key_shares  — one row per share; share_value stored as SM4-GCM ciphertext

The share_value column is encrypted with a KEK derived from the audit
encryption master secret (same derivation chain as egress_audit), so shares
are not recoverable from the DB without the configured master secret.
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, LargeBinary, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class MPCKeyRecord(Base):
    __tablename__ = "mpc_keys"
    __table_args__ = (Index("ix_mpc_keys_status", "status"),)

    # String PK (not UUID): the service generates short hex key_ids and the
    # API contract returns them verbatim, so the DB column mirrors that shape.
    key_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    algorithm: Mapped[str] = mapped_column(String(16), nullable=False, default="sm4")
    threshold: Mapped[int] = mapped_column(Integer, nullable=False)
    total_shares: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MPCKeyShareRecord(Base):
    __tablename__ = "mpc_key_shares"
    __table_args__ = (Index("ix_mpc_shares_key", "key_id"),)

    share_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    key_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("mpc_keys.key_id", ondelete="CASCADE"), nullable=False
    )
    share_index: Mapped[int] = mapped_column(Integer, nullable=False)
    share_value: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    holder_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
