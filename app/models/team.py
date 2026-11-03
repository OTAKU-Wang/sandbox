"""Team Model — 团队与团队级配额（2026-11-03 sandbox realignment Task 3）.

团队是用户与配额之间的组织层：CPU/内存/存储配额落在团队行上，团队内角色
（`members` JSON 数组 + `role_permissions` 映射）由团队管理 API 维护。
成员与权限以 JSON 快照存储，不建独立成员表——团队规模小、变更走 API 单点，
没有跨团队的 SQL 级联查询需求（需要时再拆，勿提前建第四张关联表）。
"""
from enum import Enum
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import JSON, DateTime, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

# 生产列类型是 PG 原生 JSONB；SQLite（CI/parity/测试道）没有 JSONB 编译器，
# with_variant 如实降级为 JSON（0035 迁移同形状，两侧在 PG 上收敛为 JSONB）。
_JSONB = JSONB().with_variant(JSON(), "sqlite")


class TeamRole(str, Enum):
    ADMIN = "team_admin"
    DEVELOPER = "core_developer"
    ANALYST = "data_analyst"
    VIEWER = "viewer"


class Team(Base):
    __tablename__ = "teams"
    __table_args__ = (
        Index("ix_teams_org_id", "org_id"),
        Index("ix_teams_status", "status"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    org_id: Mapped[str | None] = mapped_column(String(128))
    description: Mapped[str | None] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    # 团队级资源配额（核 / GB），由 quota_manager 在调度侧消费
    cpu_quota_cores: Mapped[int] = mapped_column(Integer, nullable=False, default=16)
    memory_quota_gb: Mapped[int] = mapped_column(Integer, nullable=False, default=64)
    storage_quota_gb: Mapped[int] = mapped_column(Integer, nullable=False, default=500)
    members: Mapped[list[dict[str, object]]] = mapped_column(_JSONB, nullable=False, default=list)
    role_permissions: Mapped[dict[str, list[str]]] = mapped_column(_JSONB, nullable=False, default=dict)
    created_by: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
