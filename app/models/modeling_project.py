"""ModelingProject Model — 建模项目协同空间（Task 3）.

ModelingProject 是 Notebook / Workload / ComputeRun / Contract 的上层聚合
空间：项目持有团队成员、里程碑与 DAG 任务编排定义（dag_definition JSON），
经 scenario_id / contract_id 关联场景承接与合约。DAG 的拓扑校验在执行侧
（projects API 的 Kahn 排序）完成，本表只存定义不存执行态。

linked_* 两组 ID 是展示层聚合用的引用清单，非外键——notebook/workload 的
生命周期各自独立，不做级联。
"""
from enum import Enum
from uuid import uuid4

from sqlalchemy import JSON, Column, DateTime, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.core.database import Base

# 生产列类型是 PG 原生 JSONB；SQLite 道 with_variant 如实降级为 JSON（见 0035 迁移）。
_JSONB = JSONB().with_variant(JSON(), "sqlite")


class ProjectStatus(str, Enum):
    ACTIVE = "active"
    SUBMITTED_REVIEW = "submitted_review"
    APPROVED = "approved"
    ARCHIVED = "archived"


class ModelingProject(Base):
    __tablename__ = "modeling_projects"
    __table_args__ = (
        Index("ix_modeling_projects_team_id", "team_id"),
        Index("ix_modeling_projects_scenario_id", "scenario_id"),
        Index("ix_modeling_projects_status", "status"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    name = Column(String(128), nullable=False)
    description = Column(Text, nullable=True)
    team_id = Column(UUID(as_uuid=True), nullable=True)
    scenario_id = Column(UUID(as_uuid=True), nullable=True)
    contract_id = Column(UUID(as_uuid=True), nullable=True)
    environment_type = Column(String(32), nullable=False, default="l2c")
    # 项目首选语言（python / sql / r / pyspark），执行侧按此选择 kernel/解释器
    preferred_language = Column(String(32), nullable=False, default="python")
    status = Column(String(32), nullable=False, default=ProjectStatus.ACTIVE.value)
    participants = Column(_JSONB, nullable=False, default=list)
    milestones = Column(_JSONB, nullable=False, default=list)
    dag_definition = Column(_JSONB, nullable=False, default=dict)
    linked_notebook_ids = Column(_JSONB, nullable=False, default=list)
    linked_workload_ids = Column(_JSONB, nullable=False, default=list)
    created_by = Column(UUID(as_uuid=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
