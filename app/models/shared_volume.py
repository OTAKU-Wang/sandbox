"""W15: shared volumes — named data spaces attachable to multiple sessions.

CubeSandbox C2 alignment (共享卷): a volume is a server-side directory owned
by one user that can be bind-mounted (read-only or read-write) into any of
their L0 sessions, enabling data hand-off between sessions without the
gateway copying payloads.
"""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class SharedVolume(Base):
    __tablename__ = "shared_volumes"
    __table_args__ = (UniqueConstraint("owner_id", "name", name="uq_shared_volume_owner_name"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    # Recorded capacity intent (MB). Enforcement is a P3 follow-up — the
    # quota is advisory metadata until a filesystem quota backend lands.
    size_limit_mb: Mapped[int] = mapped_column(Integer, nullable=False, default=1024)
    read_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SharedVolumeAttachment(Base):
    __tablename__ = "shared_volume_attachments"
    __table_args__ = (
        UniqueConstraint("session_id", "volume_id", name="uq_volume_attachment"),
        Index("ix_volume_attachments_session", "session_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    volume_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("shared_volumes.id", ondelete="CASCADE"), nullable=False
    )
    read_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
