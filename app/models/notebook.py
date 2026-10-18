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


class TaskDebugMethod(str, Enum):
    """开发任务调试方法（SS-18 §8 P0-①，创建时单选）。

    只登记本产品有真实后端语义的三项：web_ide=本工作台，local_plugin=本地 IDE
    插件经 /open/v1 通道，jupyter=原生 .ipynb 导入导出。原型第 4 项无后端语义，
    不登记也不伪造（宁缺不造）。
    """

    WEB_IDE = "web_ide"
    LOCAL_PLUGIN = "local_plugin"
    JUPYTER = "jupyter"


class TaskExportMode(str, Enum):
    """任务级成果导出方式（SS-18 §8 P0-①）。

    与 DeliveryMethod（合约交付方式白名单）无关：这里选的是成果离开开发环的通道，
    bundle=成果集打包走数据交付中心，openapi=发布在线 API；打包链仍由合约白名单把关。
    """

    BUNDLE = "bundle"
    OPENAPI = "openapi"


class Notebook(Base):
    __tablename__ = "notebooks"
    __table_args__ = (
        Index("ix_notebooks_user_status", "user_id", "status"),
        Index("ix_notebooks_contract", "contract_id"),
        Index("ix_notebooks_task_no", "task_no", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    contract_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("contracts.id"))
    session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("sandbox_sessions.id"))

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    # 图 2-2「基本信息」卡的任务描述（非必填）：没有这一列就只能默默丢掉用户输的正文。
    description: Mapped[str | None] = mapped_column(Text)
    # 沙箱工作区内的相对路径，写入前必须经 path_security.safe_resolve_within 校验
    path: Mapped[str] = mapped_column(String(512), nullable=False)
    kernel: Mapped[str] = mapped_column(String(64), nullable=False, default="python3")
    language: Mapped[str] = mapped_column(String(32), nullable=False, default="python")

    # ── 开发任务字段（SS-18 §8 P0-①；§2 决策「任务 = 开发笔记本」，不另起任务表）──
    # 存量行为 NULL：迁移只加列、不回填业务编号，前端按「未编号」渲染。
    task_no: Mapped[str | None] = mapped_column(String(32))  # TASK-YYYYMMDD-NNN（当日序号，唯一索引兜底）
    debug_method: Mapped[str | None] = mapped_column(String(16))  # TaskDebugMethod
    export_mode: Mapped[str | None] = mapped_column(String(16))  # TaskExportMode
    debug_memory_mb: Mapped[int | None] = mapped_column(Integer)  # 调试资源-内存（MB）
    exec_memory_mb: Mapped[int | None] = mapped_column(Integer)  # 执行内存（MB），默认继承调试内存
    # SS-19 G2（图 2-2「已选择数据」）：只注入合约授权集里的这几个数据资源。
    # None = 全部注入（存量行为不变，存量行不回填）。
    data_resource_ids: Mapped[list | None] = mapped_column(JSON)

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
