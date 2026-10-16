"""Workload Model — 可发布计算工件（SS-15：测试沙箱验证通过后提升到生产沙箱）."""
import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import String, DateTime, Text, ForeignKey, func, JSON, Integer, Boolean, Index
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base
from app.models.contract import WorkloadEnv


class WorkloadStatus(str, Enum):
    DRAFT = "draft"
    TESTING = "testing"
    TEST_PASSED = "test_passed"
    TEST_FAILED = "test_failed"
    PUBLISHED = "published"
    RETIRED = "retired"


class Workload(Base):
    __tablename__ = "workloads"
    __table_args__ = (
        Index("ix_workloads_contract_env_status", "contract_id", "env", "status"),
        Index("ix_workloads_user_status", "user_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    contract_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("contracts.id"))

    source_notebook_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("notebooks.id"))
    source_task_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("sandbox_tasks.id"))

    entrypoint: Mapped[str | None] = mapped_column(Text)
    language: Mapped[str] = mapped_column(String(32), nullable=False, default="python")
    # SM3(entrypoint)：发布即定版，测试通过的哈希与生产运行的哈希必须逐字节一致
    entrypoint_hash: Mapped[str | None] = mapped_column(String(64))

    resource_spec: Mapped[dict | None] = mapped_column(JSON)
    data_product_ids: Mapped[list | None] = mapped_column(JSON)

    env: Mapped[str] = mapped_column(String(16), nullable=False, default=WorkloadEnv.TEST.value, server_default=WorkloadEnv.TEST.value)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=WorkloadStatus.DRAFT.value, server_default=WorkloadStatus.DRAFT.value)

    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    parent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("workloads.id"))
    is_latest: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=sa_text("true"))

    # 只有通过测试（test_run.passed）才允许置为 test_passed / published，由 workflow_service 强制
    test_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
