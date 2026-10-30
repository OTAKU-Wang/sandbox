"""A85 — `_persist_trust` 必须是 upsert，同一空间对不得堆积多行live信任。

台账（ocr-verified-findings.md ### A85）钉住的不变量：`_persist_trust` 只 `session.add`
新行，没有 lookup/merge/upsert，末尾 `except Exception` 又吞掉包括 IntegrityError 在内
的一切。后果两条，都要在这里钉住：

* 同一 `(local_space_id, remote_space_id)` 重复 `establish_trust` 之后，**库里那一行必须
  反映最新一次建立**（而不是旧行留着、新写入被 IntegrityError 吞掉 = 静默丢写），
  且 live 行只有一条；
* 之后按最新那个 `trust_id` 吊销，必须真的落到那条 live 行上（A63/A124 叠加面：
  否则「吊销的是另一行」）。

同时不得把历史（revoked 行）当成重复行清掉——A124 的部分唯一索引故意只覆盖 active，
就是为了留住吊销历史与后续重建。
"""
import asyncio

import pytest
from sqlalchemy import select

from app.models.federation_trust import FederationTrustRecord
from app.services.federation_connector import (
    FederationConnector,
    FederationStatus,
    SpaceIdentity,
    TrustLevel,
)

from tests.conftest import test_session_factory

LOCAL = SpaceIdentity(space_id="cds-local-a85", space_name="Local", endpoint="https://local.cds.example.com")
REMOTE = SpaceIdentity(
    space_id="cds-remote-a85", space_name="Remote", endpoint="https://remote.cds.example.com"
)


def _connector() -> FederationConnector:
    connector = FederationConnector()
    connector.set_local_space(LOCAL)
    connector.set_db_session_factory(test_session_factory)
    return connector


async def _drain_pending() -> None:
    current = asyncio.current_task()
    pending = [t for t in asyncio.all_tasks() if t is not current and not t.done()]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


async def _rows() -> list[FederationTrustRecord]:
    async with test_session_factory() as session:
        return list((await session.execute(select(FederationTrustRecord))).scalars().all())


async def _establish(connector: FederationConnector, level: TrustLevel):
    """establish_trust + 等它的 fire-and-forget 落库：两条写路径不能在同一连接上交错。"""
    trust = connector.establish_trust(REMOTE, level)
    await _drain_pending()
    return trust


async def _live_rows() -> list[FederationTrustRecord]:
    return [r for r in await _rows() if r.status in ("active", "suspended")]


class TestA85PersistTrustUpsert:
    @pytest.mark.asyncio
    async def test_repeating_persist_of_one_trust_updates_the_row_in_place(self):
        connector = _connector()
        trust = await _establish(connector, TrustLevel.BASIC)

        trust.trust_level = TrustLevel.FULL
        trust.allowed_operations = ["read_catalog", "execute"]
        await connector._persist_trust(trust)

        rows = await _rows()
        assert len(rows) == 1, f"one trust must stay one row, got {len(rows)}"
        assert rows[0].trust_level == "full", (
            "the second persist was swallowed (IntegrityError) — the write was lost silently"
        )
        assert rows[0].allowed_operations_json == '["read_catalog", "execute"]'

    @pytest.mark.asyncio
    async def test_re_establishing_the_same_pair_leaves_one_live_row_with_the_newest_state(self):
        connector = _connector()
        first = await _establish(connector, TrustLevel.BASIC)

        second = await _establish(connector, TrustLevel.VERIFIED)
        assert second.trust_id != first.trust_id

        live = await _live_rows()
        assert len(live) == 1, f"live rows for one space pair stack up: {[r.trust_level for r in live]}"
        assert live[0].trust_level == "verified", (
            "the live row is the stale one — the re-establish never reached the DB"
        )

    @pytest.mark.asyncio
    async def test_revoking_the_newest_trust_empties_the_live_set(self):
        connector = _connector()
        first = await _establish(connector, TrustLevel.BASIC)
        second = await _establish(connector, TrustLevel.VERIFIED)

        assert connector.revoke_trust(second.trust_id) is True
        await _drain_pending()

        live = await _live_rows()
        assert live == [], (
            f"revoking {second.trust_id} left {[r.trust_level for r in live]} live — "
            "the kill switch addressed a row that is not the live one"
        )

    @pytest.mark.asyncio
    async def test_revoked_history_for_the_pair_is_not_collapsed(self):
        async with test_session_factory() as session:
            session.add(
                FederationTrustRecord(
                    local_space_id=LOCAL.space_id,
                    local_space_name=LOCAL.space_name,
                    local_endpoint=LOCAL.endpoint,
                    remote_space_id=REMOTE.space_id,
                    remote_space_name=REMOTE.space_name,
                    remote_endpoint=REMOTE.endpoint,
                    trust_level="basic",
                    status=FederationStatus.REVOKED.value,
                    allowed_operations_json='["read_catalog"]',
                    policy_sync_enabled=False,
                )
            )
            await session.commit()

        connector = _connector()
        await _establish(connector, TrustLevel.VERIFIED)

        rows = await _rows()
        statuses = sorted(r.status for r in rows)
        assert statuses == ["active", "revoked"], (
            f"re-establishing a pair must keep revoke history, got {statuses}"
        )

    @pytest.mark.asyncio
    async def test_reestablish_after_suspend_reactivates_a_single_live_row(self):
        connector = _connector()
        trust = await _establish(connector, TrustLevel.BASIC)

        assert connector.suspend_trust(trust.trust_id) is True
        await _drain_pending()

        again = await _establish(connector, TrustLevel.FULL)

        live = await _live_rows()
        assert len(live) == 1, f"suspend -> re-establish must not stack rows: {[r.status for r in live]}"
        assert live[0].status == "active" and live[0].trust_level == "full"
