"""A63 — 重启后不透明 `trust_id` 必须仍然解析：吊销开关不得静默 404。

台账（ocr-verified-findings.md ### A63）钉住的不变量：创建时 `POST /federation/trust`
返回给客户的那个不透明 id，在进程重启（`load_trusts_from_db()`）之后必须仍然能
1) 查到（`get_trust`），2) 吊销（`revoke_trust` 且**落到 DB 里那一行**）。
旧代码装载时写 `trust_id=str(row.id)`，与客户手里的 `trust-<12hex>` 不再相等，
`POST /trusts/{id}/revoke` 对一条仍然 active 的信任直接 404 —— kill switch 失效。

这里全部走真实 SQLAlchemy 会话（SQLite/aiosqlite + 真表），不是 fake store：
要钉的是「装载发出的 id 能寻址到真实行」这条跨进程契约。
"""
import asyncio
import uuid

import pytest
from sqlalchemy import select

from app.models.federation_trust import FederationTrustRecord
import app.services.federation_connector as fc
from app.services.federation_connector import FederationConnector, SpaceIdentity, TrustLevel

from tests.conftest import test_session_factory

LOCAL = SpaceIdentity(space_id="cds-local-a63", space_name="Local", endpoint="https://local.cds.example.com")
REMOTE = SpaceIdentity(
    space_id="cds-remote-a63", space_name="Remote", endpoint="https://remote.cds.example.com"
)


def _connector() -> FederationConnector:
    connector = FederationConnector()
    connector.set_local_space(LOCAL)
    connector.set_db_session_factory(test_session_factory)
    return connector


async def _drain_pending(baseline: set[int]) -> None:
    """让 establish/revoke 的 fire-and-forget 持久化真正跑完（不用 sleep 碰运气）。

    A192x (P4-1): 只等「本用例新造」的任务。套件 loop 钉到 session 级后
    ``asyncio.all_tasks()`` 是全套件视图——旧版 gather 全部 pending，被前置
    用例的长寿守护任务（实证：egress_audit ``_writer_loop``，Event().wait()
    永不完成）拖成 55% 位置全量 hang。基线快照来自 conftest `_task_generation`。
    """
    current = asyncio.current_task()
    pending = [
        t
        for t in asyncio.all_tasks()
        if t is not current and not t.done() and id(t) not in baseline
    ]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


async def _db_rows() -> list[FederationTrustRecord]:
    async with test_session_factory() as session:
        return list((await session.execute(select(FederationTrustRecord))).scalars().all())


class TestA63TrustIdRestart:
    @pytest.mark.asyncio
    async def test_row_primary_key_is_the_derivation_of_the_opaque_id(self, _task_generation):
        connector = _connector()
        trust = connector.establish_trust(REMOTE, TrustLevel.VERIFIED)
        await _drain_pending(_task_generation)

        rows = await _db_rows()
        assert len(rows) == 1, "trust was not persisted at all"
        row = rows[0]
        assert row.id == fc.trust_row_id(trust.trust_id)
        assert fc.opaque_trust_id(row.id) == trust.trust_id

    @pytest.mark.asyncio
    async def test_trust_created_before_restart_is_revocable_after(self, _task_generation):
        """核心：重启前 `POST /trust` 返回的 id，重启后仍能查到并吊销到库里那一行。"""
        connector = _connector()
        trust = connector.establish_trust(REMOTE, TrustLevel.VERIFIED)
        await _drain_pending(_task_generation)

        reloaded = _connector()
        assert await reloaded.load_trusts_from_db() == 1
        assert reloaded.get_trust(trust.trust_id) is not None, (
            f"POST /federation/trust handed out {trust.trust_id}; after a restart it 404s "
            "while the trust is still active in the DB"
        )
        assert reloaded.revoke_trust(trust.trust_id) is True
        await _drain_pending(_task_generation)

        rows = await _db_rows()
        assert [r.status for r in rows] == ["revoked"], (
            "revoke returned True but the row is still active — the kill switch did not reach storage"
        )

        after = _connector()
        assert await after.load_trusts_from_db() == 0, "a revoked trust must not come back on restart"

    @pytest.mark.asyncio
    async def test_loader_issued_id_always_addresses_its_own_row(self, _task_generation):
        legacy_id = uuid.uuid4()
        async with test_session_factory() as session:
            session.add(
                FederationTrustRecord(
                    id=legacy_id,
                    local_space_id=LOCAL.space_id,
                    local_space_name=LOCAL.space_name,
                    local_endpoint=LOCAL.endpoint,
                    remote_space_id="space-legacy-a63",
                    remote_space_name="Legacy",
                    remote_endpoint="https://legacy.cds.example.com",
                    trust_level="basic",
                    status="active",
                    allowed_operations_json='["read_catalog"]',
                    policy_sync_enabled=False,
                )
            )
            await session.commit()

        connector = _connector()
        assert await connector.load_trusts_from_db() == 1
        loaded = connector.list_trusts()[0]

        assert connector.revoke_trust(loaded.trust_id) is True
        await _drain_pending(_task_generation)

        rows = await _db_rows()
        assert len(rows) == 1 and rows[0].id == legacy_id
        assert rows[0].status == "revoked", (
            "the id the loader handed out did not address the row it came from"
        )

    def test_hyphenated_legacy_id_still_maps_to_its_row(self):
        raw = uuid.uuid4()
        assert fc.trust_row_id(str(raw)) == raw
        assert fc.trust_row_id(raw.hex) == raw
