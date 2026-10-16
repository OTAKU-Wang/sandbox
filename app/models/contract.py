import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import String, DateTime, Text, ForeignKey, func, JSON, Numeric, Integer, Index, Boolean
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


class ContractStatus(str, Enum):
    DRAFT = "draft"
    NEGOTIATING = "negotiating"
    SIGNED = "signed"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    COMPLETED = "completed"
    BREACHED = "breached"
    VIOLATED = "violated"
    TERMINATED = "terminated"
    ARCHIVED = "archived"


class ContractType(str, Enum):
    DATA_QUERY = "data_query"
    MODEL_TRAINING = "model_training"
    DATA_APPLICATION = "data_application"
    API_SERVICE = "api_service"
    JOINT_COMPUTE = "joint_compute"
    PRODUCT_DEV = "product_dev"
    DATA_MODELING = "data_modeling"


class DeliveryMethod(str, Enum):
    """成果交付方式（SS-15：合约层可多选）。

    DATASET     — 数据集交付：计算产物打包后在控制台下载（带水印/审计）。
    OPEN_API    — API 交付：把已发布工作负载暴露成 OpenAPI 文档化的服务端点。
    RESULT_PUSH — 结果推送：计算完成后主动推送到用户登记的回调地址。
    """

    DATASET = "dataset"
    OPEN_API = "open_api"
    RESULT_PUSH = "result_push"


class ComputeFrequency(str, Enum):
    """自动计算频率。MANUAL 表示只允许人工触发（auto_compute=false 时的取值）。"""

    MANUAL = "manual"
    HOURLY = "hourly"
    DAILY = "daily"


class WorkloadEnv(str, Enum):
    """工作负载/会话所处环境。测试沙箱验证通过后才能提升到生产沙箱。"""

    TEST = "test"
    PRODUCTION = "production"


class Contract(Base):
    __tablename__ = "contracts"
    __table_args__ = (
        Index("ix_contracts_provider_status", "provider_id", "status"),
        Index("ix_contracts_buyer_status", "buyer_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    contract_no: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    contract_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=ContractStatus.DRAFT.value)

    # Parties
    provider_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    buyer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)

    # Terms
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    terms: Mapped[dict | None] = mapped_column(JSON)  # Contract terms as JSON
    product_ids: Mapped[list] = mapped_column(JSON, nullable=False)  # 1:N list of data_product UUIDs
    allowed_sandbox_levels: Mapped[str | None] = mapped_column(String(128))  # e.g. "L2,L3"
    allowed_sandbox_modes: Mapped[list | None] = mapped_column(JSON)  # e.g. ["query","train","develop"]
    allowed_operations: Mapped[str | None] = mapped_column(Text)  # e.g. "read,analyze,train"
    max_duration_hours: Mapped[int] = mapped_column(Integer, default=24)
    dp_epsilon_budget: Mapped[float | None] = mapped_column(Numeric(10, 4))  # Differential privacy budget
    max_output_rows: Mapped[int] = mapped_column(Integer, default=10000)  # Max rows in query output
    allowed_output_formats: Mapped[str | None] = mapped_column(String(256), default="csv,json")  # e.g. "csv,json,parquet"
    inspection_rule_set: Mapped[dict | None] = mapped_column(JSON)  # Inspection rules config

    # --- SS-15 成果交付 + 自动计算 + 沙箱资源配置 -----------------------------
    # 全部加性可空/带默认值：历史合约保持原有行为（人工触发、无交付配置）。
    delivery_methods: Mapped[list | None] = mapped_column(JSON)  # ["dataset","open_api","result_push"]
    auto_compute: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=sa_text("false"))
    compute_frequency: Mapped[str] = mapped_column(String(16), nullable=False, default=ComputeFrequency.MANUAL.value, server_default=ComputeFrequency.MANUAL.value)
    compute_minute: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")  # hourly: 每小时第 N 分
    compute_hour: Mapped[int] = mapped_column(Integer, nullable=False, default=2, server_default="2")  # daily: 每日第 H 时（按 compute_tz）
    compute_tz: Mapped[str] = mapped_column(String(64), nullable=False, default="Asia/Shanghai", server_default="Asia/Shanghai")
    sandbox_resource_spec: Mapped[dict | None] = mapped_column(JSON)  # {cpu_cores,memory_mb,disk_gb,gpu_count,image_template,timeout_seconds}
    next_compute_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_computed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Gap A1: contract validity deadline — ACTIVE contracts past this point
    # are auto-terminated by the session lifecycle sweep. Optional; sourced
    # from terms["valid_until"] at activation when present.
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Gap A4: purpose limitation — 用途限定（如 statistical_analysis /
    # model_training / member_matching）。空值 = 不限；设置了 purpose 后，
    # 任务提交必须声明匹配的用途，且用途进入供需/平台签名原文。
    purpose: Mapped[str | None] = mapped_column(String(64))
    purpose_scope: Mapped[list | None] = mapped_column(JSON)  # 允许的用途列表

    # Signatures
    provider_signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    buyer_signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provider_signature: Mapped[str | None] = mapped_column(Text)  # SM2 signature (hex)
    buyer_signature: Mapped[str | None] = mapped_column(Text)  # SM2 signature (hex)
    platform_signature: Mapped[str | None] = mapped_column(Text)  # Platform witness SM2 signature (hex)
    platform_signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Blockchain
    blockchain_tx_hash: Mapped[str | None] = mapped_column(String(255))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
