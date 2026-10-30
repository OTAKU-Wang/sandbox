"""A109 配额预留接入会话创建路径（2026-10-30, lane-b）。

锁定 create 路径从 check-then-act 换成原子预留后的三个行为：
1. 超预留直接 429 QUOTA_EXCEEDED（复用现有错误码，W2 契约），且**不进
   provision**、不动 Redis、不落会话行；
2. 先预留再 provision（provision 被调用时这笔用量已在账上），provision
   失败精确回滚自己那笔——本地 dict 与 Redis hash 双双净零；
3. 精确性：基线用量非零时失败回滚只退自己那笔，基线分毫不动。
"""
import uuid

import pytest
import pytest_asyncio
import fakeredis.aioredis
from httpx import AsyncClient
from sqlalchemy import select

from app.models.contract import Contract, ContractStatus, ContractType
from app.models.data_product import DataProduct
from app.models.sandbox_session import SandboxSession, SessionStatus
from app.services import sandbox_manager
from app.services.sandbox_manager import (
    TENANT_QUOTA_KEY_PREFIX,
    flush_quota_persistence,
    get_tenant_quota,
    update_tenant_usage,
)
from app.api import sandbox_sessions as sandbox_sessions_api


@pytest_asyncio.fixture(autouse=True)
async def _fake_redis_and_clean_state(monkeypatch):
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    import app.core.redis as redis_module

    monkeypatch.setattr(redis_module, "_redis_client", fake)
    sandbox_manager._tenant_quotas.clear()
    sandbox_manager._pending_deltas.clear()
    yield fake
    sandbox_manager._tenant_quotas.clear()
    sandbox_manager._pending_deltas.clear()


class _RecordingRuntime:
    """记录 provision 被调用时刻的租户账快照；provision 结果可配。"""

    def __init__(self, result: dict):
        self._result = result
        self.calls: list[dict] = []
        self.quota_at_call: list[dict] = []

    def provision(self, **kwargs):
        self.quota_at_call.append(dict(self._snapshot()))
        self.calls.append(kwargs)
        return dict(self._result)

    @staticmethod
    def _snapshot() -> dict:
        return {uid: dict(q) for uid, q in sandbox_manager._tenant_quotas.items()}

    def terminate(self, container_id):  # pragma: no cover - 防御性
        return True

    def get_adapter(self, container_id):  # pragma: no cover - L3 不走 l2c 卷分支
        return None


def _fake_manager(runtime):
    async def _get_sandbox_manager():
        return runtime
    return _get_sandbox_manager


async def _contract_for_buyer(client: AsyncClient, auth_headers: dict, db_session, make_user, publish_product):
    """产品由 provider(auth_headers) 创建并上架；买方 make_user 拿合同。"""
    buyer_headers, buyer_id = await make_user("buyer", f"quota_buyer_{uuid.uuid4().hex[:8]}")
    product_resp = await client.post(
        "/api/v1/data-products",
        json={"name": f"Quota Reservation {uuid.uuid4().hex[:6]}", "product_type": "structured"},
        headers=auth_headers,
    )
    product_id = product_resp.json()["id"]
    await publish_product(product_id)
    product = await db_session.get(DataProduct, uuid.UUID(product_id))
    contract = Contract(
        contract_no=f"QR-{uuid.uuid4().hex[:12]}",
        contract_type=ContractType.DATA_QUERY.value,
        status=ContractStatus.ACTIVE.value,
        provider_id=product.provider_id,
        buyer_id=uuid.UUID(buyer_id),
        title="Quota Reservation Contract",
        terms={},
        product_ids=[product_id],
        allowed_sandbox_levels="L3",
        allowed_sandbox_modes=["query"],
        allowed_operations="read,analyze",
        max_duration_hours=1,
        max_output_rows=200,
    )
    db_session.add(contract)
    await db_session.flush()
    return product_id, contract, buyer_headers, str(buyer_id)


def _body(product_id: str, contract) -> dict:
    return {"data_product_id": product_id, "contract_id": str(contract.id), "sandbox_level": "L3"}


@pytest.mark.asyncio
async def test_over_quota_reservation_returns_429_without_provision(
    client: AsyncClient, auth_headers: dict, db_session, make_user, publish_product, monkeypatch,
    _fake_redis_and_clean_state,
):
    product_id, contract, buyer_headers, buyer_id = await _contract_for_buyer(
        client, auth_headers, db_session, make_user, publish_product,
    )
    # 买方租户账打满 CPU（max 8，L3 还要 +1）→ 预留必拒
    update_tenant_usage(buyer_id, cpu_cores=8, memory_mb=1024, disk_mb=2048)
    await flush_quota_persistence()
    fake = _fake_redis_and_clean_state
    redis_before = await fake.hgetall(TENANT_QUOTA_KEY_PREFIX + buyer_id)

    runtime = _RecordingRuntime({"container_id": "should-not-be-created", "status": "running"})
    monkeypatch.setattr(sandbox_sessions_api, "get_sandbox_manager", _fake_manager(runtime))

    resp = await client.post("/api/v1/sandbox-sessions", json=_body(product_id, contract), headers=buyer_headers)

    assert resp.status_code == 429, resp.text
    assert resp.json().get("code") == "QUOTA_EXCEEDED"
    # 预留被本地预筛拒绝：绝不进 provision、绝不改 Redis、绝不落会话行
    assert runtime.calls == []
    redis_after = await fake.hgetall(TENANT_QUOTA_KEY_PREFIX + buyer_id)
    assert redis_after == redis_before
    # 路由在配额门前已 flush 过会话草稿行（既有顺序，429 抛出后不会 commit）；
    # 回滚丢弃该未提交草稿后必须查不到任何该产品的会话——429 不建会话。
    await db_session.rollback()
    rows = await db_session.execute(
        select(SandboxSession).where(SandboxSession.data_product_id == uuid.UUID(product_id))
    )
    assert rows.scalars().all() == []


@pytest.mark.asyncio
async def test_provision_failure_rolls_back_own_reservation_exactly(
    client: AsyncClient, auth_headers: dict, db_session, make_user, publish_product, monkeypatch,
    _fake_redis_and_clean_state,
):
    product_id, contract, buyer_headers, buyer_id = await _contract_for_buyer(
        client, auth_headers, db_session, make_user, publish_product,
    )
    runtime = _RecordingRuntime({"error": "runtime exploded"})
    monkeypatch.setattr(sandbox_sessions_api, "get_sandbox_manager", _fake_manager(runtime))

    resp = await client.post("/api/v1/sandbox-sessions", json=_body(product_id, contract), headers=buyer_headers)
    assert resp.status_code == 201, resp.text  # 失败会话按既有契约 201 + status=failed 返回
    assert resp.json()["status"] == SessionStatus.FAILED.value

    # 先预留再 provision：provision 被调用那一刻，L3 这笔（cpu 1）已在租户账上
    assert len(runtime.calls) == 1
    snap = runtime.quota_at_call[0].get(buyer_id, {})
    assert int(snap.get("used_cpu_cores", 0)) == 1, "预留必须发生在 provision 之前"

    # provision 失败 → 精确回滚：本地账净零、Redis 净零（落 0 而不是缺键）
    quota = get_tenant_quota(buyer_id)
    assert int(quota["used_cpu_cores"]) == 0
    assert int(quota["used_memory_mb"]) == 0
    assert int(quota["used_disk_mb"]) == 0
    await flush_quota_persistence()
    stored = await _fake_redis_and_clean_state.hgetall(TENANT_QUOTA_KEY_PREFIX + buyer_id)
    assert stored.get("used_cpu_cores") == "0"
    assert stored.get("used_memory_mb") == "0"
    assert stored.get("used_disk_mb") == "0"


@pytest.mark.asyncio
async def test_rollback_touches_only_own_delta_on_nonzero_baseline(
    client: AsyncClient, auth_headers: dict, db_session, make_user, publish_product, monkeypatch,
    _fake_redis_and_clean_state,
):
    product_id, contract, buyer_headers, buyer_id = await _contract_for_buyer(
        client, auth_headers, db_session, make_user, publish_product,
    )
    # 基线：别的会话已占 2 核（< max 8，预留仍可过）
    update_tenant_usage(buyer_id, cpu_cores=2, memory_mb=2048, disk_mb=4096)
    await flush_quota_persistence()

    runtime = _RecordingRuntime({"error": "provision boom"})
    monkeypatch.setattr(sandbox_sessions_api, "get_sandbox_manager", _fake_manager(runtime))

    resp = await client.post("/api/v1/sandbox-sessions", json=_body(product_id, contract), headers=buyer_headers)
    assert resp.status_code == 201, resp.text

    # 只退自己那笔：基线 2/2048/4096 分毫不动
    quota = get_tenant_quota(buyer_id)
    assert int(quota["used_cpu_cores"]) == 2
    assert int(quota["used_memory_mb"]) == 2048
    assert int(quota["used_disk_mb"]) == 4096
    await flush_quota_persistence()
    stored = await _fake_redis_and_clean_state.hgetall(TENANT_QUOTA_KEY_PREFIX + buyer_id)
    assert stored["used_cpu_cores"] == "2"
    assert stored["used_memory_mb"] == "2048"
    assert stored["used_disk_mb"] == "4096"
