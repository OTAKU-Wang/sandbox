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

    # ── 交付中心展示列（SS-18 §8 P0-②）：打包当时就写在行上，读侧不再解 MANIFEST 也不再猜。
    # 存量行为 NULL（迁移只加列不回填），前端按「未记录」渲染。
    t5_review_status: Mapped[str | None] = mapped_column(String(24))  # passed / passed_with_findings / partially_reviewed
    watermark_status: Mapped[str | None] = mapped_column(String(24))  # not_requested / applied / partially_applied / not_applied
    row_count: Mapped[int | None] = mapped_column(Integer)  # 数据表文件（.csv/.tsv/.jsonl）行数合计；无数据表则 NULL
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
