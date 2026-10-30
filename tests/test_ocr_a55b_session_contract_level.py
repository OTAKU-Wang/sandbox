"""OCR A55b — 生产会话入口的合约校验必须同时执行**平台隔离下限**（A55 的兄弟入口）。

`app/api/sandbox_sessions.py::_validate_session_contract` 原本只做「合约档位成员校验」
（`body.sandbox_level in contract.allowed_sandbox_levels`）。SS-13 的 l2c-only 门在生产里
兜住了弱档，但 `CDS_SANDBOX_LEGACY_ENABLED=true`（观察期/开发环境的既有开关，测试基线
同样为 true）时，一纸**只放行 L0** 的合约就能把供数会话建在进程隔离上 —— 隔离下限由
合约数据决定，而不是平台不变量。

本文件钉住新增的第二道门：成员校验之后，还必须过
`capability_matrix.can_carry_supply_data`（A55 落地的**唯一权威强度序**，本文件不另抄阶梯）。
违规按 SS-13 一律 400 已登记码 `LEGACY_LEVEL_RETIRED`（`detail.reason` ∈
`below_supply_data_floor` / `level_deprecated`），排在任何 provision / 密钥下发之前，
不静默升档、也不因「合约点了头」放行更弱的档。

`runtime.provision` 以替身观测（本文件测的是判档门，不是隔离机制本身）。
"""
import uuid

import pytest
from sqlalchemy import func, select

from app.models.contract import Contract, ContractStatus
from app.models.data_product import DataProduct
from app.models.data_resource import DataResource, ResourceStatus
from app.models.sandbox_session import SandboxSession
from app.services.storage_service import storage_service

CSV = b"city,amount\nShanghai,100\nBeijing,200\n"


class _CaptureRuntime:
    def __init__(self):
        self.calls: list[str] = []

    def get_adapter(self, level: str):
        return self

    def provision(self, **kwargs):
        level = str(kwargs.get("level"))
        self.calls.append(level)
        return {"container_id": f"{level.lower()}-fake", "status": "running"}

    def terminate(self, container_id: str):
        return True


async def _use_fake_runtime(monkeypatch) -> _CaptureRuntime:
    import app.api.sandbox_sessions as sandbox_sessions_api

    runtime = _CaptureRuntime()

    async def _fake_manager():
        return runtime

    monkeypatch.setattr(sandbox_sessions_api, "get_sandbox_manager", _fake_manager)
    return runtime


async def _mk_product(db_session, provider_id):
    meta = storage_service.upload(
        data=CSV, object_name=f"test-resources/{uuid.uuid4().hex}.csv", content_type="text/csv"
    )
    resource = DataResource(
        provider_id=uuid.UUID(str(provider_id)),
        name=f"a55b-res-{uuid.uuid4().hex[:8]}",
        storage_path=meta["path"],
        encryption_key_id=meta.get("key_id"),
        status=ResourceStatus.READY.value,
        file_size_bytes=meta.get("size"),
        sm3_checksum=meta.get("checksum"),
        format="csv",
    )
    db_session.add(resource)
    await db_session.flush()
    product = DataProduct(
        provider_id=uuid.UUID(str(provider_id)),
        name=f"a55b-{uuid.uuid4().hex[:8]}",
        status="published",
        resource_id=resource.id,
    )
    db_session.add(product)
    await db_session.flush()
    await db_session.refresh(product)
    return product


async def _mk_contract(db_session, provider_id, buyer_id, product_ids, *, allowed_levels):
    contract = Contract(
        contract_no=f"A55B-{uuid.uuid4().hex[:8]}",
        contract_type="data_query",
        provider_id=uuid.UUID(str(provider_id)),
        buyer_id=uuid.UUID(str(buyer_id)),
        title="a55b-level-gate",
        product_ids=[str(pid) for pid in product_ids],
        status=ContractStatus.ACTIVE.value,
        allowed_sandbox_levels=allowed_levels,
    )
    db_session.add(contract)
    await db_session.flush()
    await db_session.refresh(contract)
    return contract


async def _session_count(db_session, user_id) -> int:
    return (
        await db_session.execute(
            select(func.count()).where(SandboxSession.user_id == uuid.UUID(str(user_id)))
        )
    ).scalar() or 0


@pytest.mark.asyncio
async def test_contract_licensing_only_l0_does_not_open_l0_for_supply_data(
    client, db_session, make_user, monkeypatch
):
    """A55b 核心：合约「只放行 L0」不得把供数会话建在进程隔离上。

    成员校验会放 L0 过（L0 确实在条款里），但承载供数的强度序只有 l2c/L3。
    HEAD 上此请求返回 201（会话真的建在 L0 上），改后必须是 400 且没有会话行、
    没有运行面。
    """
    runtime = await _use_fake_runtime(monkeypatch)
    provider_headers, provider_id = await make_user("data_provider", "a55bprov")
    buyer_headers, buyer_id = await make_user("buyer", "a55bbuyer")
    product = await _mk_product(db_session, provider_id)
    contract = await _mk_contract(
        db_session, provider_id, buyer_id, [product.id], allowed_levels="L0"
    )

    resp = await client.post(
        "/api/v1/sandbox-sessions",
        json={
            "data_product_id": str(product.id),
            "contract_id": str(contract.id),
            "sandbox_level": "L0",
        },
        headers=buyer_headers,
    )
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body["code"] == "LEGACY_LEVEL_RETIRED", body
    assert body["detail"]["reason"] == "below_supply_data_floor", body
    assert body["detail"]["sandbox_level"] == "L0", body
    assert body["detail"]["contract_allowed_sandbox_levels"] == ["L0"], body
    assert body["detail"]["supply_data_levels"] == ["l2c", "L3"], body
    assert runtime.calls == [], "下限拒绝必须排在 provision 之前（不得先建运行面）"
    assert await _session_count(db_session, buyer_id) == 0


@pytest.mark.asyncio
async def test_sub_floor_level_refused_even_without_a_contract_clause(
    client, db_session, make_user, monkeypatch
):
    """A55b：合约没写档位条款（该列可空）时平台下限仍独立生效。

    条款为空 ⇒ 成员校验整段跳过；若只看条款，「合约没写」就等于回到客户端自由选档。
    """
    runtime = await _use_fake_runtime(monkeypatch)
    provider_headers, provider_id = await make_user("data_provider", "a55bprov2")
    buyer_headers, buyer_id = await make_user("buyer", "a55bbuyer2")
    product = await _mk_product(db_session, provider_id)
    contract = await _mk_contract(
        db_session, provider_id, buyer_id, [product.id], allowed_levels=None
    )

    resp = await client.post(
        "/api/v1/sandbox-sessions",
        json={
            "data_product_id": str(product.id),
            "contract_id": str(contract.id),
            "sandbox_level": "L1",
        },
        headers=buyer_headers,
    )
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body["code"] == "LEGACY_LEVEL_RETIRED", body
    assert body["detail"]["reason"] == "below_supply_data_floor", body
    assert body["detail"]["contract_allowed_sandbox_levels"] == [], body
    assert runtime.calls == []


@pytest.mark.asyncio
async def test_deprecated_level_is_not_a_supply_data_run_plane(
    client, db_session, make_user, monkeypatch
):
    """A55b：SS-10 后弃用的 L2（受 legacy 门控）即使被合约点名放行也不得承载供数。

    `reason` 取自矩阵自己的 `LEVEL_METADATA[level]["deprecated"]`，不在调用点另抄弃用清单。
    """
    runtime = await _use_fake_runtime(monkeypatch)
    provider_headers, provider_id = await make_user("data_provider", "a55bprov3")
    buyer_headers, buyer_id = await make_user("buyer", "a55bbuyer3")
    product = await _mk_product(db_session, provider_id)
    contract = await _mk_contract(
        db_session, provider_id, buyer_id, [product.id], allowed_levels="L2"
    )

    resp = await client.post(
        "/api/v1/sandbox-sessions",
        json={
            "data_product_id": str(product.id),
            "contract_id": str(contract.id),
            "sandbox_level": "L2",
        },
        headers=buyer_headers,
    )
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body["code"] == "LEGACY_LEVEL_RETIRED", body
    assert body["detail"]["reason"] == "level_deprecated", body
    assert runtime.calls == []


@pytest.mark.asyncio
async def test_in_floor_level_under_contract_still_creates_session(
    client, db_session, make_user, monkeypatch
):
    """A55b 边界（防越界收紧）：条款内的**达标**档位不受影响，仍 201、档位不变。

    同 `tests/test_e2e_flows.py` 的合约形状（`allowed_sandbox_levels="L2,L3"` + 请求 L3）。
    """
    runtime = await _use_fake_runtime(monkeypatch)
    provider_headers, provider_id = await make_user("data_provider", "a55bprov4")
    buyer_headers, buyer_id = await make_user("buyer", "a55bbuyer4")
    product = await _mk_product(db_session, provider_id)
    contract = await _mk_contract(
        db_session, provider_id, buyer_id, [product.id], allowed_levels="L2,L3"
    )

    resp = await client.post(
        "/api/v1/sandbox-sessions",
        json={
            "data_product_id": str(product.id),
            "contract_id": str(contract.id),
            "sandbox_level": "L3",
        },
        headers=buyer_headers,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["sandbox_level"] == "L3"
    assert runtime.calls == ["L3"], "强度序不得把 L3 静默换成别的档"


@pytest.mark.asyncio
async def test_gate_does_not_fire_on_the_contractless_session_path(
    client, db_session, make_user, monkeypatch
):
    """A55b 边界（本轨未扩大的那一半，如实钉住）：无合约会话**不**经这条门。

    `_validate_session_contract` 在无 `contract_id` 时整体不参与，因此「无合约 + L0 +
    已发布产品」的行为由 PZ-08 的 CI 门禁用例
    `tests/test_e2e_data_loop.py::test_e2e_data_loop_upload_execute_download_audit`
    （钉 201）决定，不属本条目授权范围（见证据文件 NEEDS-OWNER）。本用例只断言
    「本门没有把无合约路径也拒掉」，为后续扩权留下显式红线。
    """
    await _use_fake_runtime(monkeypatch)
    provider_headers, provider_id = await make_user("data_provider", "a55bprov5")

    product = await _mk_product(db_session, provider_id)
    resp = await client.post(
        "/api/v1/sandbox-sessions",
        json={"data_product_id": str(product.id), "sandbox_level": "L0"},
        headers=provider_headers,
    )
    detail = (resp.json() or {}).get("detail") if resp.status_code == 400 else None
    reason = detail.get("reason") if isinstance(detail, dict) else None
    assert reason not in ("below_supply_data_floor", "level_deprecated"), (
        f"无合约路径被本轨新增的下限拒掉了（越界）: {resp.text}"
    )
