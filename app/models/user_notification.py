"""UserNotification Model — 顶栏通知中心的持久化通知（Task 3）.

通知中心聚合两个来源：本表的持久化通知（系统公告、业务通知）与实时待办
（待签合约 / 待审出域，读取侧现算、不落表）。本表只存前者，实时项不进表
——把「当前状态」持久化成行会立刻过期，是伪造一个不会自动更正的快照。

extra_meta 携带跳转上下文；is_read 的 server_default 用 sa_text("false")
（PG 严格性：text("0") 在真实 PG 上被拒，D-e2e-08）。
"""
from enum import Enum
from uuid import uuid4

from sqlalchemy import JSON, Boolean, Column, DateTime, Index, String, Text, func
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.core.database import Base

# 生产列类型是 PG 原生 JSONB；SQLite 道 with_variant 如实降级为 JSON（见 0035 迁移）。
_JSONB = JSONB().with_variant(JSON(), "sqlite")


class NotificationCategory(str, Enum):
    TODO = "todo"
    SYSTEM = "system"
    BUSINESS = "business"


class UserNotification(Base):
    __tablename__ = "user_notifications"
    __table_args__ = (
        Index("ix_user_notifications_user_read", "user_id", "is_read"),
        Index("ix_user_notifications_category", "category"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id = Column(UUID(as_uuid=True), nullable=True)
    category = Column(String(32), nullable=False, default=NotificationCategory.BUSINESS.value)
    title = Column(String(256), nullable=False)
    content = Column(Text, nullable=True)
    target_path = Column(String(256), nullable=True)
    is_read = Column(Boolean, nullable=False, default=False, server_default=sa_text("false"))
    extra_meta = Column(_JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
