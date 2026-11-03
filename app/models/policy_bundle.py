"""PolicyBundle model — stores compiled contract policies."""
import logging
import uuid
from datetime import datetime

from sqlalchemy import String, DateTime, Text, ForeignKey, func, JSON, Index
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base

logger = logging.getLogger(__name__)


class PolicyBundle(Base):
    """Compiled policy bundle for a contract.

    Stores the Rego source code and field-level ACL rules
    that control what operations are allowed in a sandbox.

    integrity_hash 保证强度（A125b，如实披露）：这是 **无密钥 SM3**，
    只防无意不一致（传输/编译/存储损坏、版本错配），**不防有权写入者**——
    任何能 UPDATE 本表的主体都能对篡改后的 rego_source 重算哈希。
    接线要求（A125b）：任何 fallback PDP 若读取 field_acl / allowed_ops /
    sandbox_modes 而不经外部签名校验，必须把完整性校验纳入其校验面，并在
    披露中说明它防不住特权写入者；升级为 HMAC-SM3/KMS 签名是接线期决策
    （需要密钥来源 + 存量行双哈希策略）。

    LATENT 坐标（A125①：revoked_at 过滤不在模型层——models/ 约定
    declarative-only，无查询 helper，防线落点如下）：
      - 读侧 Serving 过滤：当前无生产读侧——原 A73 过滤
        （PolicyBundle.revoked_at.is_(None) + 合约 valid_until 过期）位于
        app/services/policy_evaluator.py，该模块零生产导入（死读侧），已于 2026-11 删除（死代码清理）；
        policy_compiler.py 的 PolicyBundle 是同名
        内存类，与本表无关。复活迹象由 tests/test_ocr_a125_policy_bundle_pins.py 机器盯防。
      - 撤销写入：app/services/contract_service.py（合约 TERMINATED 时
        逐包写 revoked_at 并同步移出 OPA）
      - 本模块行写入方：app/ 中当前不存在（表由部署侧填充）——任何新
        importer 都会先触发上面的可达性针变红。
    """
    __tablename__ = "policy_bundles"
    __table_args__ = (
        Index("ix_policy_bundles_contract", "contract_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    policy_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    contract_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("contracts.id"), nullable=False)
    rego_source: Mapped[str] = mapped_column(Text, nullable=False)  # OPA Rego source
    # A125b：无密钥 SM3(rego_source)——只防无意不一致、不防有权写入者。
    # 任何能写本行的主体都能对篡改内容重算此哈希；勿当作防篡改机制消费。
    integrity_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="unkeyed SM3(rego_source) — detects accidental corruption only; "
                "a writer with UPDATE can recompute it. Not tamper-proof (A125b)",
    )
    field_acl: Mapped[dict | None] = mapped_column(JSON)  # {field: {read, aggregate_only, mask_pattern}}
    allowed_ops: Mapped[list | None] = mapped_column(JSON)  # ["read","query","train","export"]
    sandbox_modes: Mapped[list | None] = mapped_column(JSON)  # ["query","train","develop","application"]
    version: Mapped[int] = mapped_column(default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Gap A1: set when the governing contract is terminated — the bundle is
    # revoked (also removed from OPA) and must no longer be served.
    # 模型层无查询 helper（A125①）；serving 过滤落点见类 docstring 的 LATENT 坐标。
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


_BOOT_DISCLOSURED = False


def disclose_integrity_limitations(force: bool = False) -> None:
    """T12/A125b：boot 披露 integrity_hash 的真实保证强度。

    由模块 import 触发（main.py 的模型导入链在启动时执行）——「列建好了、
    哈希存上了」不等于「防篡改」。force=True 供门禁测试重放断言。
    """
    global _BOOT_DISCLOSURED
    if _BOOT_DISCLOSURED and not force:
        return
    _BOOT_DISCLOSURED = True
    logger.warning(
        "[A125] policy_bundles.integrity_hash 是 unkeyed SM3（无密钥）："
        "只防无意不一致（损坏/错配），不防有权写入者——能 UPDATE 者即可重算。"
        "fallback PDP 接线时必须把完整性校验纳入校验面。"
    )


disclose_integrity_limitations()
