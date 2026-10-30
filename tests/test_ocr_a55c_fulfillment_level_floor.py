"""OCR A55c — 自动履约（contract → 供数会话）入口必须过同一条供数隔离下限。

`app/services/contract_fulfillment.py::_provision_product_session` 直接取
`contract.allowed_sandbox_levels` 的第一档建会话并下发会话密钥，完全不经过 API 层的
A55b 门（`_reject_sub_floor_supply_data_level` 只覆盖 HTTP 入口）。后果：一纸只放行
低档（L0/k8s…）的合约，经自动履约仍能把承载供数的会话建在已退役档位上 —— 与 A55b
同一个洞的另一条入口。

本文件钉住自动履约入口的新拒绝：判据只在**调用时**读唯一权威
`capability_matrix.can_carry_supply_data` / `SUPPLY_DATA_LEVELS`（A55 落地的强度序，
本文件与被测文件都不另抄阶梯）；违规复用已登记码 `LEGACY_LEVEL_RETIRED`
（`detail.reason` ∈ `below_supply_data_floor` / `level_deprecated`）；拒绝排在
会话落库、`runtime.provision` 与会话密钥下发之前；达标档位（l2c/L3）行为逐字不变，
不升档、不放行任何原本被拒的档。
"""
import uuid

import pytest
from sqlalchemy import func, select

from app.core.errors import CDSError
from app.models.contract import Contract, ContractStatus
from app.models.data_product import DataProduct, DataProductStatus
from app.models.kms import KeyMetadata
from app.models.sandbox_session import SandboxSession
from app.services import capability_matrix
from app.services.contract_fulfillment import contract_fulfillment


class _RecordingRuntime:
    """runtime 替身：记录每次 provision 的档位；provision 可控成功/失败。"""

    def __init__(self, *, fail=False):
        self.fail = fail
        self.provision_calls: list[str] = []

    def provision(self, **kwargs):
        self.provision_calls.append(str(kwargs.get("level")))
        if self.fail:
            return {"container_id": None, "status": "failed", "error": "runtime provision failed"}
        return {"container_id": "a55c-container", "status": "running"}

    def terminate(self, container_id: str):
        return True


class _OkKMS:
    def generate_session_key(self, session_id: str):
        return {"key_id": f"a55c-key-{session_id}", "key_bytes": b"0" * 32}

    def distribute_key(self, key_id: str, session_id: str, **kwargs):
        return {"key_bytes": b"0" * 32}

    def export_wrapped(self, key_id: str):
        return b"wrapped-blob"

    def export_sm2_ciphertext(self, key_id: str):
        return b"sm2-blob"

    def destroy_key(self, key_id: str):
        return True


async def _use_fake_runtime(monkeypatch, *, fail=False) -> _RecordingRuntime:
    import app.services.contract_fulfillment as fulfillment_module

    runtime = _RecordingRuntime(fail=fail)

    async def _fake_manager():
        return runtime

    monkeypatch.setattr(fulfillment_module, "get_sandbox_manager", _fake_manager)
    monkeypatch.setattr(fulfillment_module, "kms_service", _OkKMS())
    return runtime


async def _mk_product(db_session, make_user):
    _, provider_id = await make_user("data_provider", f"a55cprov{uuid.uuid4().hex[:8]}")
    product = DataProduct(
        provider_id=uuid.UUID(provider_id),
        name=f"A55c Product {uuid.uuid4().hex[:8]}",
        product_type="structured",
        status=DataProductStatus.PUBLISHED.value,
    )
    db_session.add(product)
    await db_session.flush()
    return product, provider_id


async def _mk_contract(db_session, make_user, provider_id, *, product_ids, allowed_levels):
    _, buyer_id = await make_user("buyer", f"a55cbuyer{uuid.uuid4().hex[:8]}")
    contract = Contract(
        contract_no=f"A55C-{uuid.uuid4().hex[:8]}",
        contract_type="data_query",
        status=ContractStatus.ACTIVE.value,
        provider_id=uuid.UUID(provider_id),
        buyer_id=uuid.UUID(buyer_id),
        title="a55c-level-floor",
        terms={},
        product_ids=[str(pid) for pid in product_ids],
        allowed_operations="query",
        allowed_sandbox_levels=allowed_levels,
        allowed_sandbox_modes=["structured_query"],
    )
    db_session.add(contract)
    await db_session.flush()
    await db_session.refresh(contract)
    return contract


async def _session_rows(db_session, contract_id):
    return (
        await db_session.execute(
            select(SandboxSession).where(SandboxSession.contract_id == str(contract_id))
        )
    ).scalars().all()


@pytest.mark.asyncio
async def test_fulfillment_refuses_contract_licensing_only_l0(db_session, make_user, monkeypatch):
    """A55c 核心：合约只放行 L0 ⇒ 自动履约拒绝，且不建会话、不碰运行面。

    HEAD 上此履约返回 fulfilled（会话真的建在 L0、密钥照常下发）；改后该产品的错误
    条目带 LEGACY_LEVEL_RETIRED / below_supply_data_floor，且 provision 未被调用、
    会话行数为 0。
    """
    runtime = await _use_fake_runtime(monkeypatch)
    product, provider_id = await _mk_product(db_session, make_user)
    contract = await _mk_contract(
        db_session, make_user, provider_id, product_ids=[product.id], allowed_levels="L0"
    )

    with pytest.raises(CDSError) as exc:
        await contract_fulfillment._provision_product_session(db_session, contract, product.id)

    err = exc.value
    assert err.code == "LEGACY_LEVEL_RETIRED"
    assert err.http_status == 400
    assert err.detail["sandbox_level"] == "L0"
    assert err.detail["reason"] == "below_supply_data_floor"
    assert err.detail["supply_data_levels"] == list(capability_matrix.SUPPLY_DATA_LEVELS)
    assert err.detail["contract_allowed_sandbox_levels"] == ["L0"]

    result = await contract_fulfillment.fulfill_contract(db_session, contract.id)
    assert result["status"] == "partial"
    assert result["provisioned_sessions"] == []
    assert "supply-data session" in result["errors"][0]["error"]
    assert runtime.provision_calls == [], "下限拒绝必须排在 provision 之前（不得先建运行面）"
    assert await _session_rows(db_session, contract.id) == []
    key_rows = (
        await db_session.execute(select(func.count()).select_from(KeyMetadata))
    ).scalar()
    assert key_rows == 0, "会话密钥不得先于下限校验下发/登记"


@pytest.mark.asyncio
async def test_fulfillment_refuses_deprecated_k8s_with_deprecated_reason(
    db_session, make_user, monkeypatch
):
    """A55c：SS-10 后弃用的 k8s（矩阵自标 deprecated）即使被合约点名放行也不得承载供数。

    `reason` 取自权威矩阵自己的 `LEVEL_METADATA[level]["deprecated"]`，不在调用点
    另抄弃用清单。
    """
    runtime = await _use_fake_runtime(monkeypatch)
    product, provider_id = await _mk_product(db_session, make_user)
    contract = await _mk_contract(
        db_session, make_user, provider_id, product_ids=[product.id], allowed_levels="k8s"
    )

    with pytest.raises(CDSError) as exc:
        await contract_fulfillment._provision_product_session(db_session, contract, product.id)

    assert exc.value.code == "LEGACY_LEVEL_RETIRED"
    assert exc.value.detail["reason"] == "level_deprecated"
    assert runtime.provision_calls == []
    assert await _session_rows(db_session, contract.id) == []


@pytest.mark.asyncio
async def test_fulfillment_of_l2c_contract_is_unchanged(db_session, make_user, monkeypatch):
    """A55c 边界（防越界收紧）：达标档位 l2c 的履约行为逐字不变、不升档。

    结果 dict 的形状与字段值就是 HEAD 上达标路径的行为（`_provision_product_session`
    返回 product_id/session_id/sandbox_level/status 四键），此处按字面钉住。
    """
    runtime = await _use_fake_runtime(monkeypatch)
    product, provider_id = await _mk_product(db_session, make_user)
    contract = await _mk_contract(
        db_session, make_user, provider_id, product_ids=[product.id], allowed_levels="l2c"
    )

    result = await contract_fulfillment.fulfill_contract(db_session, contract.id)

    assert result["status"] == "fulfilled"
    assert len(result["provisioned_sessions"]) == 1
    entry = result["provisioned_sessions"][0]
    assert entry["product_id"] == str(product.id)
    assert entry["sandbox_level"] == "l2c", "强度序不得把 l2c 静默换成别的档"
    assert entry["status"] == "running"
    assert runtime.provision_calls == ["l2c"]

    sessions = await _session_rows(db_session, contract.id)
    assert len(sessions) == 1
    assert sessions[0].sandbox_level == "l2c"
    assert sessions[0].session_key_id is not None


@pytest.mark.asyncio
async def test_fulfillment_of_l3_contract_is_unchanged(db_session, make_user, monkeypatch):
    """A55c 边界：达标档位 L3（现网默认档）的履约同样逐字不变。"""
    runtime = await _use_fake_runtime(monkeypatch)
    product, provider_id = await _mk_product(db_session, make_user)
    contract = await _mk_contract(
        db_session, make_user, provider_id, product_ids=[product.id], allowed_levels="L3"
    )

    result = await contract_fulfillment.fulfill_contract(db_session, contract.id)

    assert result["status"] == "fulfilled"
    assert result["provisioned_sessions"][0]["sandbox_level"] == "L3"
    assert runtime.provision_calls == ["L3"]


@pytest.mark.asyncio
async def test_empty_allowed_levels_default_is_not_client_choice(
    db_session, make_user, monkeypatch
):
    """`allowed_sandbox_levels` 为空（该列可空）不得等于「客户端自由选档」。

    可空形态在履约处走服务端默认 L3（达标档），既不是拒绝面、也绝不落到任何
    低于下限的档 —— 这里钉「默认即达标」，同时证明本门没有把空条款误当成
    「随便建」。
    """
    runtime = await _use_fake_runtime(monkeypatch)
    product, provider_id = await _mk_product(db_session, make_user)
    contract = await _mk_contract(
        db_session, make_user, provider_id, product_ids=[product.id], allowed_levels=None
    )

    result = await contract_fulfillment.fulfill_contract(db_session, contract.id)

    assert result["status"] == "fulfilled"
    assert result["provisioned_sessions"][0]["sandbox_level"] == "L3"
    assert runtime.provision_calls == ["L3"]


@pytest.mark.asyncio
async def test_floor_follows_the_authority_not_a_second_ladder(
    db_session, make_user, monkeypatch
):
    """权威档位阶梯一动，履约门跟着动 —— 证明被测文件里没有第二份真相。

    从权威 `SUPPLY_DATA_LEVELS` 里摘掉 L3 ⇒ L3 合约必须改判为拒绝；
    把 k8s 加进权威 ⇒ k8s 合约必须放行到运行面（provision 被调用）。两个方向都
    只 patch 权威符号，被测代码没有自己的字面量比较能顶住这个 patch。
    """
    runtime = await _use_fake_runtime(monkeypatch, fail=True)

    monkeypatch.setattr(capability_matrix, "SUPPLY_DATA_LEVELS", ("l2c",))
    product, provider_id = await _mk_product(db_session, make_user)
    contract = await _mk_contract(
        db_session, make_user, provider_id, product_ids=[product.id], allowed_levels="L3"
    )
    with pytest.raises(CDSError) as exc:
        await contract_fulfillment._provision_product_session(db_session, contract, product.id)
    assert exc.value.code == "LEGACY_LEVEL_RETIRED"
    assert exc.value.detail["supply_data_levels"] == ["l2c"]
    assert runtime.provision_calls == []
    assert await _session_rows(db_session, contract.id) == []

    monkeypatch.setattr(capability_matrix, "SUPPLY_DATA_LEVELS", ("l2c", "L3", "k8s"))
    product2, provider_id2 = await _mk_product(db_session, make_user)
    contract2 = await _mk_contract(
        db_session, make_user, provider_id2, product_ids=[product2.id], allowed_levels="k8s"
    )
    result = await contract_fulfillment.fulfill_contract(db_session, contract2.id)
    # 权威放行 k8s ⇒ 门不再拦，行为回落到 HEAD 的「运行面失败 ⇒ FAILED 会话行」路径。
    assert runtime.provision_calls == ["k8s"], (
        "把 k8s 加进权威后履约门仍拦住了 k8s —— 被测代码里存在第二份档位阶梯"
    )
    assert result["status"] == "partial"
    assert result["errors"][0]["error"] == "runtime provision failed"
