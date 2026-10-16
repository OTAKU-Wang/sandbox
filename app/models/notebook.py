"""Notebook Model — 在线 Notebook 开发产物（SS-15 数据开发流程）.

用户在沙箱里通过在线 Notebook 开发；本表保存**文档本身**（nbformat v4 的 JSON），
使开发成果在沙箱销毁后仍可复用、可版本化、可提升为 Workload。

沙箱内的真实执行由 notebook_service 负责同步：文档 → 沙箱工作区文件 → kernel。
`execution_backend` 如实披露当前会话实际使用的执行后端（jupyter / cds_kernel），
不做任何"看起来像 Jupyter"的伪造（项目铁律 honest degradation）。
"""
import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import String, DateTime, Text, ForeignKey, func, JSON, Integer, Index
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


class NotebookStatus(str, Enum):
    DRAFT = "draft"
    ARCHIVED = "archived"


class Notebook(Base):
    __tablename__ = "notebooks"
    __table_args__ = (
        Index("ix_notebooks_user_status", "user_id", "status"),
        Index("ix_notebooks_contract", "contract_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    contract_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("contracts.id"))
    session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("sandbox_sessions.id"))

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    # 沙箱工作区内的相对路径，写入前必须经 path_security.safe_resolve_within 校验
    path: Mapped[str] = mapped_column(String(512), nullable=False)
    kernel: Mapped[str] = mapped_column(String(64), nullable=False, default="python3")
    language: Mapped[str] = mapped_column(String(32), nullable=False, default="python")

    # nbformat v4 文档：{cells: [...], metadata: {...}, nbformat, nbformat_minor}
    content: Mapped[dict | None] = mapped_column(JSON)
    content_hash: Mapped[str | None] = mapped_column(String(64))  # SM3 of canonical JSON
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("notebooks.id"))

    status: Mapped[str] = mapped_column(String(16), nullable=False, default=NotebookStatus.DRAFT.value)
    execution_backend: Mapped[str | None] = mapped_column(String(32))  # "jupyter" | "cds_kernel"，服务端回写
    last_executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
