import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import String, DateTime, Integer, Text, ForeignKey, func, JSON, Index, Boolean
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


class SandboxLevel(str, Enum):
    L0 = "L0"  # Process isolation (cgroup + output limits)
    L1 = "L1"  # TEE (SGX/Occlum)
    L2 = "L2"  # Firecracker + gVisor
    L3 = "L3"  # bwrap namespace isolation
    K8S = "k8s"  # Kubernetes/K3s pod isolation


class SessionStatus(str, Enum):
    PENDING = "pending"
    KEY_DISTRIBUTING = "key_distributing"
    READY = "ready"
    PROVISIONING = "provisioning"
    RUNNING = "running"
    SUSPENDED = "suspended"
    COMPLETED = "completed"
    FAILED = "failed"
    TERMINATED = "terminated"
    REVOKED = "revoked"


class SandboxMode(str, Enum):
    """6-core sandbox scenario modes (SPEC-1/14)."""
    STRUCTURED_QUERY = "structured_query"        # DuckDB SQL 查询
    STRUCTURED_MODELING = "structured_modeling"   # 统计建模
    STRUCTURED_APP = "structured_app"            # 应用 API 调用
    LLM_TRAINING = "llm_training"               # 模型训练
    PRODUCT_DEV = "product_dev"                  # 产品开发
    JOINT_FEDERATED = "joint_federated"          # 联邦联合计算


class SandboxSession(Base):
    __tablename__ = "sandbox_sessions"
    __table_args__ = (
        Index("ix_sandbox_sessions_user_status", "user_id", "status"),
        Index("ix_sandbox_sessions_product_status", "data_product_id", "status"),
        Index("ix_sandbox_sessions_created", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    data_product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("data_products.id"), nullable=False, index=True)
    sandbox_level: Mapped[str] = mapped_column(String(16), nullable=False, default=SandboxLevel.L3.value)
    sandbox_mode: Mapped[str] = mapped_column(
        String(32), nullable=False, default=SandboxMode.STRUCTURED_QUERY.value,
    )  # P2-5: Scenario mode
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=SessionStatus.PENDING.value)
    contract_id: Mapped[str | None] = mapped_column(String(255))  # 数字合约 ID
    container_id: Mapped[str | None] = mapped_column(String(255))  # Docker/container ID
    session_key_id: Mapped[str | None] = mapped_column(String(255))  # KMS session key ID
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=3600)
    # Round 39 (usability): extra wall-clock seconds granted via POST
    # /{id}/refreshes — extends the effective expiry beyond timeout_seconds.
    extended_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # Round 39 (usability): status to restore on resume (pause stores it).
    pre_pause_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # W9 (parity plan): idle expiry behavior. "kill" (default) terminates the
    # session when expired; "pause" suspends it preserving state. auto_resume
    # lets a subsequent interaction wake a suspended session — gated on the
    # governing contract still being ACTIVE.
    idle_policy: Mapped[str | None] = mapped_column(String(16), nullable=True)
    auto_resume: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=sa_text("0"))
    resource_limits: Mapped[dict | None] = mapped_column(JSON)  # CPU/memory/disk limits
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
