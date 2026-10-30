"""A138 — key_distributions 有读者（TTL 清理循环）、有承诺（启动就跑），却没有写入方。

全仓没有一处 INSERT 进 key_distributions ⇒ `cleanup_expired_distributions`
每 5 分钟查一张永远空的表，对外宣传的密钥分发 TTL 是虚构能力。本文件钉死两头：
① 分发即记账：`distribute_key`（所有 TEE/业务调用方的唯一咽喉）成功后必须留下
带真实 `expires_at` 的 ACTIVE 行（经 `flush_pending_distributions` 落库，同步
函数持不到 DB 会话，由 TTL 循环每轮先 flush——延迟边界在 docstring 与启动日志
里如实披露）；行里的 `session_key_enc` 是 KEK-wrapped 块，绝非明文。
② 到期是 crypto-erase 而不是状态翻转：过期行的包裹块必须置 NULL，否则
「过期」只是装饰——再 SELECT 一次就能继续发钥。
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models.kms import DistributionStatus, KeyDistribution
from app.services import kms_service as kms_mod
from app.services.kms_service import kms_service

pytestmark = pytest.mark.asyncio


async def test_key_handoff_records_ledger_row(client, admin_headers, db_session):
    key_id = kms_service.generate_session_key(f"a138-{uuid.uuid4().hex[:6]}")["key_id"]
    key_hex = kms_service.distribute_key(key_id, "session-a138")
    assert key_hex is not None

    before = (await db_session.execute(
        select(KeyDistribution).where(KeyDistribution.key_id == key_id)
    )).scalars().all()
    assert before == [], "落库只应发生在显式 flush（TTL 循环同款路径）"

    flushed = await kms_service.flush_pending_distributions(db_session)
    assert flushed >= 1
    rows = (await db_session.execute(
        select(KeyDistribution).where(KeyDistribution.key_id == key_id)
    )).scalars().all()
    assert len(rows) == 1, "A138: every key hand-off must mint a ledger row"
    row = rows[0]
    assert row.session_id == "session-a138"
    assert row.status == DistributionStatus.ACTIVE.value
    assert row.expires_at is not None, "TTL 执法的前提是行上有真实到期时间"
    got = row.expires_at
    if got.tzinfo is None:
        got = got.replace(tzinfo=timezone.utc)
    expected = datetime.now(timezone.utc) + timedelta(seconds=kms_mod.KEY_DISTRIBUTION_TTL_SECONDS)
    assert abs((got - expected).total_seconds()) < 120
    assert row.session_key_enc is not None
    assert row.session_key_enc != key_hex, "台账里的密钥块必须是 KEK-wrapped，绝不存明文"

    again = await kms_service.flush_pending_distributions(db_session)
    assert again == 0, "flush 必须排空缓冲，不得重复记账"


async def test_rejected_distribution_is_not_recorded(client, admin_headers, db_session):
    key_id = kms_service.generate_session_key(f"a138x-{uuid.uuid4().hex[:6]}")["key_id"]
    assert kms_service.distribute_key("no-such-key", "session-none") is None
    flushed = await kms_service.flush_pending_distributions(db_session)
    assert flushed == 0, "没有发生的交接不得进台账"


async def test_expiry_crypto_erases_not_just_flips_status(client, admin_headers, db_session):
    dist = KeyDistribution(
        key_id=f"dek-a138-{uuid.uuid4().hex[:8]}",
        session_id="session-expire",
        status=DistributionStatus.ACTIVE.value,
        session_key_enc="00" * 32,
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=5),
    )
    db_session.add(dist)
    await db_session.flush()

    count = await kms_service.cleanup_expired_distributions(db_session)
    assert count >= 1
    await db_session.refresh(dist)
    assert dist.status == DistributionStatus.EXPIRED.value
    assert dist.session_key_enc is None, (
        "A138: 到期必须 crypto-erase——包裹块留在行上时「过期」只是装饰"
    )
