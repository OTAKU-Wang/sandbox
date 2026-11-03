"""Team Model — 团队与团队级配额（2026-11-03 sandbox realignment Task 3）.

团队是用户与配额之间的组织层：CPU/内存/存储配额落在团队行上，团队内角色
（`members` JSON 数组 + `role_permissions` 映射）由团队管理 API 维护。
成员与权限以 JSON 快照存储，不建独立成员表——团队规模小、变更走 API 单点，
没有跨团队的 SQL 级联查询需求（需要时再拆，勿提前建第四张关联表）。
"""
from enum import Enum
from uuid import uuid4

from sqlalchemy import JSON, Column, DateTime, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID

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

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    name = Column(String(128), nullable=False)
    org_id = Column(String(128), nullable=True)
    description = Column(String(512), nullable=True)
    status = Column(String(32), nullable=False, default="active")
    # 团队级资源配额（核 / GB），由 quota_manager 在调度侧消费
    cpu_quota_cores = Column(Integer, nullable=False, default=16)
    memory_quota_gb = Column(Integer, nullable=False, default=64)
    storage_quota_gb = Column(Integer, nullable=False, default=500)
    members = Column(_JSONB, nullable=False, default=list)
    role_permissions = Column(_JSONB, nullable=False, default=dict)
    created_by = Column(UUID(as_uuid=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
