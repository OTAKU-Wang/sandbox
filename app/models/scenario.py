"""Scenario Model — 业务场景（数据开发场景 + 数据交付场景承接，Task 3）.

场景是需求侧的入口实体：运营发布场景（title/行业/业务背景/目标），开发团队
承接（accepted_team_id）后由场景一键转化为 ModelingProject，场景的合规约束
（compliance_constraints）与交付技术预埋（delivery_tech_config）注入项目配置。

delivery_scene 记录交付方式预埋（API / 数据集文件 / TEE 计算），与合约的
DeliveryMethod 白名单是两个层面：这里表达场景期望，白名单是合约硬约束。
"""
from enum import Enum
from uuid import uuid4

from sqlalchemy import JSON, Column, DateTime, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.core.database import Base

# 生产列类型是 PG 原生 JSONB；SQLite 道 with_variant 如实降级为 JSON（见 0035 迁移）。
_JSONB = JSONB().with_variant(JSON(), "sqlite")


class ScenarioStatus(str, Enum):
    PUBLISHED = "published"
    ACCEPTED = "accepted"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class DeliverySceneType(str, Enum):
    API = "api"
    DATASET_FILE = "dataset_file"
    TEE_COMPUTE = "tee_compute"


class Scenario(Base):
    __tablename__ = "scenarios"
    __table_args__ = (
        Index("ix_scenarios_status", "status"),
        Index("ix_scenarios_delivery_scene", "delivery_scene"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    title = Column(String(256), nullable=False)
    industry = Column(String(64), nullable=True)
    business_background = Column(Text, nullable=True)
    target_description = Column(Text, nullable=True)
    delivery_scene = Column(String(32), nullable=False, default=DeliverySceneType.API.value)
    status = Column(String(32), nullable=False, default=ScenarioStatus.PUBLISHED.value)
    # 运营推荐的数据资源集（仅建议，最终授权仍走合约白名单）
    suggested_resource_ids = Column(_JSONB, nullable=False, default=list)
    compliance_constraints = Column(_JSONB, nullable=False, default=dict)
    delivery_tech_config = Column(_JSONB, nullable=False, default=dict)
    accepted_team_id = Column(UUID(as_uuid=True), nullable=True)
    accepted_by = Column(UUID(as_uuid=True), nullable=True)
    created_by = Column(UUID(as_uuid=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
