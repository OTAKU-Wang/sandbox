"""Sandbox Node Registry — tracks available execution nodes for task scheduling."""
import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import String, DateTime, Float, Integer, Boolean, JSON, func, Index
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


class NodeStatus(str, Enum):
    ONLINE = "online"
    OFFLINE = "offline"
    MAINTENANCE = "maintenance"
    DRAINING = "draining"  # Not accepting new tasks, finishing existing ones


class NodeCapability(str, Enum):
    CPU = "cpu"
    GPU_T4 = "gpu_t4"
    GPU_A10 = "gpu_a10"
    GPU_A100 = "gpu_a100"
    TEE = "tee"  # Trusted Execution Environment
    HIGH_MEMORY = "high_memory"


class SandboxNode(Base):
    """Registered sandbox execution node."""
    __tablename__ = "sandbox_nodes"
    __table_args__ = (
        Index("ix_sandbox_nodes_status", "status"),
        Index("ix_sandbox_nodes_region", "region"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    node_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    hostname: Mapped[str] = mapped_column(String(255), nullable=False)
    ip_address: Mapped[str] = mapped_column(String(45), nullable=False)  # IPv4 or IPv6

    # Status and capabilities
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=NodeStatus.OFFLINE.value)
    capabilities: Mapped[dict | None] = mapped_column(JSON)  # ["cpu", "gpu_t4", "tee"]
    region: Mapped[str] = mapped_column(String(64), nullable=False, default="default")

    # Resource metrics (updated periodically)
    cpu_cores: Mapped[int] = mapped_column(Integer, default=0)
    memory_mb: Mapped[int] = mapped_column(Integer, default=0)
    gpu_count: Mapped[int] = mapped_column(Integer, default=0)
    gpu_memory_mb: Mapped[int] = mapped_column(Integer, default=0)

    # Load metrics (0.0 - 1.0)
    cpu_usage: Mapped[float] = mapped_column(Float, default=0.0)
    memory_usage: Mapped[float] = mapped_column(Float, default=0.0)
    gpu_usage: Mapped[float] = mapped_column(Float, default=0.0)

    # Task metrics
    active_tasks: Mapped[int] = mapped_column(Integer, default=0)
    max_tasks: Mapped[int] = mapped_column(Integer, default=10)
    total_completed: Mapped[int] = mapped_column(Integer, default=0)
    total_failed: Mapped[int] = mapped_column(Integer, default=0)

    # Health
    last_heartbeat: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_rate: Mapped[float] = mapped_column(Float, default=0.0)  # 0.0 - 1.0

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
