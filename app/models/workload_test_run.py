"""WorkloadTestRun Model — 测试沙箱运行记录（发布前置门禁的凭证）."""
import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import String, DateTime, Text, ForeignKey, func, JSON, Integer, Boolean
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


class TestRunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"


class WorkloadTestRun(Base):
    __tablename__ = "workload_test_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workload_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("workloads.id"), nullable=False, index=True)
    triggered_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))

    session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("sandbox_sessions.id"))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=TestRunStatus.PENDING.value)
    # 判定结果由 checks 逐项汇总：任一项 passed=false 则整体 passed=false（不做加权/折中）
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    checks: Mapped[list | None] = mapped_column(JSON)
    report: Mapped[dict | None] = mapped_column(JSON)
    exit_code: Mapped[int | None] = mapped_column(Integer)
    error_message: Mapped[str | None] = mapped_column(Text)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
