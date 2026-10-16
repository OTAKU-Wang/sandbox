"""ComputeRun Model — 合约维度的自动/人工计算运行记录（SS-15 调度）."""
import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import String, DateTime, Text, ForeignKey, func, JSON, Integer, Index
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


class ComputeRunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED_OVERLAP = "skipped_overlap"
    CANCELLED = "cancelled"


class ComputeTrigger(str, Enum):
    SCHEDULE = "schedule"
    MANUAL = "manual"


class ComputeRun(Base):
    __tablename__ = "compute_runs"
    __table_args__ = (
        Index("ix_compute_runs_contract_status", "contract_id", "status"),
        Index("ix_compute_runs_status_created", "status", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    contract_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("contracts.id"), nullable=False, index=True)
    workload_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("workloads.id"))
    requested_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))

    trigger: Mapped[str] = mapped_column(String(16), nullable=False, default=ComputeTrigger.MANUAL.value)
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 调度触发写入 f"{contract_id}:{scheduled_for}"，靠唯一约束实现"同一时点只跑一次"；
    # 人工触发置 NULL（PG 与 SQLite 的唯一约束均允许多个 NULL）。
    idempotency_key: Mapped[str | None] = mapped_column(String(128), unique=True)

    status: Mapped[str] = mapped_column(String(32), nullable=False, default=ComputeRunStatus.PENDING.value)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")

    session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("sandbox_sessions.id"))
    task_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("sandbox_tasks.id"))

    output_files: Mapped[list | None] = mapped_column(JSON)
    output_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    resource_usage: Mapped[dict | None] = mapped_column(JSON)
    # 人工触发时提交运行参数（向沙箱内的入口代码以 CDS_PARAMS 注入）与规格覆盖。
    params: Mapped[dict | None] = mapped_column(JSON)
    resource_override: Mapped[dict | None] = mapped_column(JSON)
    error_message: Mapped[str | None] = mapped_column(Text)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
