"""A124（装载半，父会话自 poolA-models 移交）— 装载必须定序，同一空间对取最新一条。

poolA 已落模式半（`uq_federation_trusts_active_pair` 部分唯一索引，见
`tests/test_ocr_a124_federation_trust_pair_unique.py`）；本文件钉装载侧：
`load_trusts_from_db()` 读 `status IN ('active','suspended')` 后逐行覆盖
`_space_index[remote.space_id]`，无 `order_by` ⇒ 库里存在同空间对多行时（历史脏行、
或 suspended + active 并存），重启后生效的 `trust_level`/`allowed_operations`
取决于数据库返回顺序，授权面随机扩大或缩小。

不变量：装载结果只由行内容决定、与返回顺序无关，且胜者是 `created_at` 最新那一行
（= 操作员最后一次动作，与重启前内存里 `_space_index` 的覆盖语义一致）。

两行同取 `suspended`：`active` 被部分唯一索引挡住，测不出「多行」这个前置条件。
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models.federation_trust import FederationTrustRecord
from app.services.federation_connector import (
    FederationConnector,
    SpaceIdentity,
    opaque_trust_id,
)

from tests.conftest import test_session_factory

LOCAL = SpaceIdentity(space_id="cds-local-a124", space_name="Local", endpoint="https://local.cds.example.com")
REMOTE_SPACE = "cds-remote-a124"
BASE = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def _row(created_at: datetime, trust_level: str) -> FederationTrustRecord:
    return FederationTrustRecord(
        id=uuid.uuid4(),
        local_space_id=LOCAL.space_id,
        local_space_name=LOCAL.space_name,
        local_endpoint=LOCAL.endpoint,
        remote_space_id=REMOTE_SPACE,
        remote_space_name="Remote",
        remote_endpoint="https://remote.cds.example.com",
        trust_level=trust_level,
        status="suspended",
        allowed_operations_json='["read_catalog"]',
        policy_sync_enabled=False,
        created_at=created_at,
    )


async def _seed(rows: list[FederationTrustRecord]) -> None:
    async with test_session_factory() as session:
        for row in rows:
            session.add(row)
        await session.commit()


async def _stored(remote_space_id: str) -> list[FederationTrustRecord]:
    async with test_session_factory() as session:
        result = await session.execute(
            select(FederationTrustRecord).where(
                FederationTrustRecord.remote_space_id == remote_space_id
            )
        )
        return list(result.scalars().all())


def _connector() -> FederationConnector:
    connector = FederationConnector()
    connector.set_local_space(LOCAL)
    connector.set_db_session_factory(test_session_factory)
    return connector


class TestA124LoaderOrdering:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("newest_first", [True, False], ids=["db-returns-newest-first", "db-returns-oldest-first"])
    async def test_space_index_winner_is_independent_of_row_return_order(self, newest_first: bool):
        newest = _row(BASE + timedelta(days=3), "basic")
        oldest = _row(BASE, "full")
        await _seed([newest, oldest] if newest_first else [oldest, newest])

        connector = _connector()
        assert await connector.load_trusts_from_db() == 2

        winner = connector.get_trust_by_space(REMOTE_SPACE)
        assert winner is not None
        assert winner.trust_level == "basic", (
            "the effective authorization surface after a restart depends on DB row-return order: "
            "the newest row for the space pair must win"
        )
        assert winner.trust_id == opaque_trust_id(newest.id)

    @pytest.mark.asyncio
    async def test_every_row_stays_addressable_by_id(self):
        newest = _row(BASE + timedelta(days=1), "basic")
        oldest = _row(BASE, "full")
        await _seed([newest, oldest])

        connector = _connector()
        assert await connector.load_trusts_from_db() == 2

        assert connector.get_trust(opaque_trust_id(newest.id)) is not None
        assert connector.get_trust(opaque_trust_id(oldest.id)) is not None
        assert len(connector.list_trusts()) == 2
