"""OCR A55 — 开发沙箱的隔离档位必须由合约与平台决定，客户端字符串不得直通 provision。

`app/api/dev_sandbox.py` 的 `sandbox_level` 是自由字符串：
1) 合约写了 `allowed_sandbox_levels` 也必须参与判档（与
   `sandbox_sessions._validate_session_contract` 同一判据）；且这条判据不能只在
   `DEV_SANDBOX_REQUIRE_CONTRACT=true` 时生效——开关的语义是「挂供数**必须**带合约」，
   不是「带了合约也可以不看条款」。
2) 字面值必须是平台真实存在的档位（`SandboxLevel`）：非法字面值不得先解密供数、
   再在建会话处炸成 500；档位在本节点没有运行面时（`get_adapter` 抛 ValueError）
   必须诚实报 400 `RUNTIME_UNAVAILABLE`，绝不降级到别的隔离面（SS-13）。

runtime.provision 以 spy 替身观测（本文件测的是判档门，不是隔离机制本身）；
唯一走真实 provision 的用例是让 `get_adapter` 自己抛——没有伪造任何运行面。
"""
import uuid
from dataclasses import dataclass, field

import pytest

from app.core.config import get_settings
from app.models.contract import Contract, ContractStatus
from app.models.data_product import DataProduct
from app.models.data_resource import DataResource, ResourceStatus
from app.services.data_product_sandbox import dev_sandbox
from app.services.storage_service import storage_service

CSV = b"city,amount\nShanghai,100\nBeijing,200\n"


@dataclass
class Capture:
    calls: int = 0
    downloads: int = 0
    level: str | None = field(default=None)
    paths: list[str] = field(default_factory=list)


async def _mk_product(db_session, provider_id, *, resource_id=None) -> DataProduct:
    product = DataProduct(
        provider_id=uuid.UUID(str(provider_id)),
        name=f"a55-{uuid.uuid4().hex[:8]}",
        status="published",
        resource_id=resource_id,
    )
    db_session.add(product)
    await db_session.flush()
    await db_session.refresh(product)
    return product


async def _mk_resource(db_session, provider_id) -> DataResource:
    meta = storage_service.upload(data=CSV, object_name=f"test-resources/{uuid.uuid4().hex}.csv",
                                  content_type="text/csv")
    resource = DataResource(
        provider_id=uuid.UUID(str(provider_id)),
        name=f"a55-res-{uuid.uuid4().hex[:8]}",
        storage_path=meta["path"],
        encryption_key_id=meta.get("key_id"),
        status=ResourceStatus.READY.value,
        file_size_bytes=meta.get("size"),
        sm3_checksum=meta.get("checksum"),
        format="csv",
    )
    db_session.add(resource)
    await db_session.flush()
    await db_session.refresh(resource)
    return resource


async def _mk_contract(db_session, provider_id, product_ids, *, allowed_levels) -> Contract:
    contract = Contract(
        contract_no=f"A55-{uuid.uuid4().hex[:8]}",
        contract_type="data_query",
        provider_id=uuid.UUID(str(provider_id)),
        buyer_id=uuid.UUID(str(provider_id)),
        title="a55-level-gate",
        product_ids=[str(pid) for pid in product_ids],
        status=ContractStatus.ACTIVE.value,
        allowed_sandbox_levels=allowed_levels,
    )
    db_session.add(contract)
    await db_session.flush()
    await db_session.refresh(contract)
    return contract


def _spy_provision(monkeypatch, cap: Capture) -> None:
    def _fake(session_id, level, data_path, timeout, user_id=""):
        cap.calls += 1
        cap.level = level
        cap.paths.append(data_path)
        return {"container_id": f"{str(level).lower()}-fake", "status": "running",
                "attestation_quote": None}

    monkeypatch.setattr(dev_sandbox.runtime, "provision", _fake)


def _spy_download(monkeypatch, cap: Capture) -> None:
    def _fake_download(object_name):
        cap.downloads += 1
        return CSV

    monkeypatch.setattr(storage_service, "download_decrypted", _fake_download)


@pytest.mark.asyncio
async def test_contract_level_clause_gates_when_contract_required(
    client, db_session, make_user, monkeypatch
):
    """A55 核心（HEAD 已实现，本用例是回归锁）：条款外的档位 ⇒ 400，条款内的档位照常可用。"""
    monkeypatch.setattr(get_settings(), "DEV_SANDBOX_REQUIRE_CONTRACT", True)
    headers, provider_id = await make_user("data_provider", "a55on")
    resource = await _mk_resource(db_session, provider_id)
    product = await _mk_product(db_session, provider_id, resource_id=resource.id)
    contract = await _mk_contract(db_session, provider_id, [product.id], allowed_levels="L2")
    cap = Capture()
    _spy_download(monkeypatch, cap)
    _spy_provision(monkeypatch, cap)

    resp = await client.post("/api/v1/dev-sandbox/sessions", json={
        "mode": "structured", "data_product_id": str(product.id),
        "contract_id": str(contract.id), "sandbox_level": "L3",
    }, headers=headers)
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body["code"] == "VALIDATION_ERROR", body
    assert body["detail"]["allowed_sandbox_levels"] == ["L2"], body
    assert cap.calls == 0
    assert cap.downloads == 0, "拒绝必须发生在解密供数之前"

    contract.allowed_sandbox_levels = "L2,L3"
    await db_session.flush()
    ok = await client.post("/api/v1/dev-sandbox/sessions", json={
        "mode": "structured", "data_product_id": str(product.id),
        "contract_id": str(contract.id), "sandbox_level": "L3",
    }, headers=headers)
    assert ok.status_code == 201, ok.text
    assert cap.level == "L3"


@pytest.mark.asyncio
async def test_contract_level_clause_still_gates_when_require_switch_off(
    client, db_session, make_user, monkeypatch
):
    """A55：开关只决定「是否必须带合约」；既然带了合约，条款就必须参与判档。"""
    monkeypatch.setattr(get_settings(), "DEV_SANDBOX_REQUIRE_CONTRACT", False)
    headers, provider_id = await make_user("data_provider", "a55off")
    resource = await _mk_resource(db_session, provider_id)
    product = await _mk_product(db_session, provider_id, resource_id=resource.id)
    contract = await _mk_contract(db_session, provider_id, [product.id], allowed_levels="L2")
    cap = Capture()
    _spy_download(monkeypatch, cap)
    _spy_provision(monkeypatch, cap)

    resp = await client.post("/api/v1/dev-sandbox/sessions", json={
        "mode": "structured", "data_product_id": str(product.id),
        "contract_id": str(contract.id), "sandbox_level": "L3",
    }, headers=headers)
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body["code"] == "VALIDATION_ERROR", body
    assert "not allowed by contract" in body["message"], body
    assert cap.calls == 0
    assert cap.downloads == 0

    free = await _mk_contract(db_session, provider_id, [product.id], allowed_levels=None)
    ok = await client.post("/api/v1/dev-sandbox/sessions", json={
        "mode": "structured", "data_product_id": str(product.id),
        "contract_id": str(free.id), "sandbox_level": "L3",
    }, headers=headers)
    assert ok.status_code == 201, ok.text
    assert cap.level == "L3"


@pytest.mark.asyncio
async def test_unknown_level_rejected_before_the_supply_data_is_decrypted(
    client, db_session, make_user, monkeypatch
):
    """A55：非平台档位（不在 SandboxLevel 内）⇒ 400，且不得先把密态供数解密落盘。

    修复前这个请求会先把供数解密到宿主暂存目录，再在 get_adapter 处抛 ValueError
    被兜底处理器变成 500 —— 一次注定失败的请求先做了一次不可逆的解密。
    """
    headers, provider_id = await make_user("data_provider", "a55bogus")
    resource = await _mk_resource(db_session, provider_id)
    product = await _mk_product(db_session, provider_id, resource_id=resource.id)
    cap = Capture()
    _spy_download(monkeypatch, cap)
    _spy_provision(monkeypatch, cap)

    resp = await client.post("/api/v1/dev-sandbox/sessions", json={
        "mode": "structured", "data_product_id": str(product.id), "sandbox_level": "L9",
    }, headers=headers)
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body["code"] == "VALIDATION_ERROR", body
    assert body["detail"]["sandbox_level"] == "L9", body
    assert cap.downloads == 0, "非法档位请求不得先解密供数"
    assert cap.calls == 0


@pytest.mark.asyncio
async def test_level_without_runtime_is_400_not_500(
    client, db_session, make_user, monkeypatch
):
    """A55 未证实部分坐实：档位字面合法且平台许可，但本节点没有该运行面 ⇒ 400 RUNTIME_UNAVAILABLE。

    `get_adapter` 对未注册的档位抛裸 ValueError（修复前 = 兜底 500，W2 契约失效）。
    这里只把 L3 从**本进程**的适配器表里摘掉（不伪造任何运行面/硬件），
    并断言拒绝发生在解密供数之前。（旧版本用例摘的是 L2——A55 强度序落地后 L2
    根本不得承载供数，到不了适配器这一腿，所以改用同样在阶梯内的 L3。）
    """
    headers, provider_id = await make_user("data_provider", "a55norun")
    resource = await _mk_resource(db_session, provider_id)
    product = await _mk_product(db_session, provider_id, resource_id=resource.id)
    monkeypatch.setattr(
        dev_sandbox.runtime,
        "_adapters",
        {k: v for k, v in dev_sandbox.runtime._adapters.items() if k != "L3"},
    )
    cap = Capture()
    _spy_download(monkeypatch, cap)

    resp = await client.post("/api/v1/dev-sandbox/sessions", json={
        "mode": "structured", "data_product_id": str(product.id), "sandbox_level": "L3",
    }, headers=headers)
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body["code"] == "RUNTIME_UNAVAILABLE", body
    assert body["detail"]["level"] == "L3", body
    assert cap.downloads == 0, "运行面缺失必须在解密供数之前拒绝（否则白做一次不可逆解密）"

    from app.api.dev_sandbox import _data_load_root
    root = _data_load_root()
    leftovers = list(root.glob(f"{provider_id}/**/*")) if root.exists() else []
    assert leftovers == [], f"A54: 拒绝后明文副本仍在宿主上: {leftovers}"


@pytest.mark.asyncio
async def test_contract_licensing_a_sub_floor_level_does_not_open_it(
    client, db_session, make_user, monkeypatch
):
    """A55 强度序腿：合约「只放行 L0」不得把密态供数放进进程隔离。

    条款成员校验会放 L0 过（L0 确实在条款里），但隔离下限是平台不变量、不由合约
    数据决定（与 workflow_service.pick_test_sandbox_level 的 CR BLOCKER-1 同一判据）。
    按 SS-13 诚实拒绝：不静默升到 l2c，也不因为「合约同意了」就放行，且不得先解密。
    """
    monkeypatch.setattr(get_settings(), "DEV_SANDBOX_REQUIRE_CONTRACT", True)
    headers, provider_id = await make_user("data_provider", "a55floor")
    resource = await _mk_resource(db_session, provider_id)
    product = await _mk_product(db_session, provider_id, resource_id=resource.id)
    contract = await _mk_contract(db_session, provider_id, [product.id], allowed_levels="L0")
    cap = Capture()
    _spy_download(monkeypatch, cap)
    _spy_provision(monkeypatch, cap)

    resp = await client.post("/api/v1/dev-sandbox/sessions", json={
        "mode": "structured", "data_product_id": str(product.id),
        "contract_id": str(contract.id), "sandbox_level": "L0",
    }, headers=headers)
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body["code"] == "LEGACY_LEVEL_RETIRED", body
    assert body["detail"]["reason"] == "below_supply_data_floor", body
    assert body["detail"]["contract_allowed_sandbox_levels"] == ["L0"], body
    assert body["detail"]["supply_data_levels"] == ["l2c", "L3"], body
    assert cap.downloads == 0, "下限拒绝不得先解密供数"
    assert cap.calls == 0


@pytest.mark.asyncio
async def test_sub_floor_level_refused_even_without_a_contract_clause(
    client, db_session, make_user, monkeypatch
):
    """A55：合约没写档位条款（列可空）时，平台下限仍然独立生效——不得因为「合约没
    说」就回到客户端自由选档。"""
    monkeypatch.setattr(get_settings(), "DEV_SANDBOX_REQUIRE_CONTRACT", True)
    headers, provider_id = await make_user("data_provider", "a55noclause")
    resource = await _mk_resource(db_session, provider_id)
    product = await _mk_product(db_session, provider_id, resource_id=resource.id)
    contract = await _mk_contract(db_session, provider_id, [product.id], allowed_levels=None)
    cap = Capture()
    _spy_download(monkeypatch, cap)
    _spy_provision(monkeypatch, cap)

    resp = await client.post("/api/v1/dev-sandbox/sessions", json={
        "mode": "structured", "data_product_id": str(product.id),
        "contract_id": str(contract.id), "sandbox_level": "L1",
    }, headers=headers)
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body["code"] == "LEGACY_LEVEL_RETIRED", body
    assert body["detail"]["reason"] == "below_supply_data_floor", body
    assert cap.downloads == 0


@pytest.mark.asyncio
async def test_deprecated_level_is_not_a_supply_data_run_plane(
    client, db_session, make_user, monkeypatch
):
    """A55：SS-10 后弃用的 L2/k8s（受 legacy 门控）不得承载供数，即使合约明写放行。"""
    headers, provider_id = await make_user("data_provider", "a55dep")
    resource = await _mk_resource(db_session, provider_id)
    product = await _mk_product(db_session, provider_id, resource_id=resource.id)
    contract = await _mk_contract(db_session, provider_id, [product.id], allowed_levels="L2")
    cap = Capture()
    _spy_download(monkeypatch, cap)
    _spy_provision(monkeypatch, cap)

    resp = await client.post("/api/v1/dev-sandbox/sessions", json={
        "mode": "structured", "data_product_id": str(product.id),
        "contract_id": str(contract.id), "sandbox_level": "L2",
    }, headers=headers)
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body["code"] == "LEGACY_LEVEL_RETIRED", body
    assert body["detail"]["reason"] == "level_deprecated", body
    assert cap.downloads == 0


@pytest.mark.asyncio
async def test_sub_floor_level_still_allowed_without_supply_data(
    client, db_session, make_user, monkeypatch
):
    """A55 边界：下限守的是「供数」，不是把所有开发沙箱钉到 L3。

    没挂数据产品的会话里没有任何解密明文，L0 仍是矩阵写明的「开发级」合法选择；
    强度序不得越界把它也拒掉（那会把 SS-19 给无 Cube 节点部署留的 L0 逃生挡掉）。
    """
    headers, provider_id = await make_user("data_provider", "a55bare")
    cap = Capture()
    _spy_download(monkeypatch, cap)
    _spy_provision(monkeypatch, cap)

    resp = await client.post("/api/v1/dev-sandbox/sessions", json={
        "mode": "structured", "sandbox_level": "L0",
    }, headers=headers)
    assert resp.status_code == 201, resp.text
    assert cap.level == "L0"
    assert cap.calls == 1
    assert resp.json()["data_loaded"] is False

