"""DeliveryBundle Model — 数据集交付包的归属记录（SS-15 成果交付）。

交付包字节落在对象存储（`delivery/{bundle_id}.zip`），下载端点原先只按 bundle_id 取件，
任何业务用户拿到 id 就能取走他人的密态成果；本表记录包 → 合约/会话/创建人的绑定，
下载时按合约当事方鉴权（fail-closed，无归属记录即拒绝）。
"""
import uuid
from datetime import datetime

from sqlalchemy import String, DateTime, Integer, ForeignKey, func, Index
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


class DeliveryBundle(Base):
    __tablename__ = "delivery_bundles"
    __table_args__ = (
        Index("ix_delivery_bundles_contract", "contract_id"),
        Index("ix_delivery_bundles_session", "session_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    contract_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("contracts.id"), nullable=False)
    session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("sandbox_sessions.id"), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    object_name: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    file_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
