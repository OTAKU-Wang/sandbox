"""A160c — 合约终止时 OPA 策略包删除未确认，不得静默半开。

`opa_client.delete_policy()` 只返回 bool（异常吞成 False；A160b 之后"客户端已关闭"
同样返回 False 而不抛）。原 `contract_service._revoke_policy_bundles()` 把返回值丢掉，
于是：合约 TERMINATED + DB bundle 已 revoked，但 OPA 里的策略包可能仍在放行，
且调用方与审计都看不到这个缺口。本文件钉住「未确认必须可见」这一半：
返回值 + ERROR 日志 + 审计 detail 三处，缺一即红。
"""
import uuid

import pytest
from sqlalchemy import select

from app.models.audit_log import AuditLog
from app.models.contract import Contract, ContractStatus
from app.models.policy_bundle import PolicyBundle


async def _mk_contract_with_bundle(db_session, provider_id, buyer_id):
    contract = Contract(
        contract_no=f"C-{uuid.uuid4().hex[:8]}",
        contract_type="data_query",
        provider_id=uuid.UUID(provider_id),
        buyer_id=uuid.UUID(buyer_id),
        title="a160c",
        product_ids=[str(uuid.uuid4())],
        status=ContractStatus.ACTIVE.value,
        max_output_rows=10,
    )
    db_session.add(contract)
    await db_session.flush()
    await db_session.refresh(contract)
    bundle = PolicyBundle(
        policy_id=f"cds/policies/contract_{contract.id}",
        contract_id=contract.id,
        rego_source="package cds",
        integrity_hash="sm3-hash",
    )
    db_session.add(bundle)
    await db_session.flush()
    return contract, bundle


@pytest.mark.asyncio
async def test_unconfirmed_opa_delete_is_reported_not_silently_counted(
    db_session, make_user, monkeypatch
):
    """delete_policy 返回 False ⇒ 不得计入已撤销，且必须出现在返回列表里。"""
    from app.services import contract_service as cs
    from app.services.opa_client import opa_client

    _, provider = await make_user("data_provider", "prov")
    contract, bundle = await _mk_contract_with_bundle(
        db_session, provider, str(uuid.uuid4())
    )

    calls: list[str] = []

    async def _fake_delete(package_path: str) -> bool:
        calls.append(package_path)
        return False

    monkeypatch.setattr(opa_client, "delete_policy", _fake_delete)

    revoked, unconfirmed = await cs.contract_service._revoke_policy_bundles(
        db_session, contract
    )
    assert calls == [bundle.policy_id], "必须真的调用了 OPA 删除"
    assert revoked == 0, f"OPA 未确认删除却计入已撤销: {revoked}"
    assert unconfirmed == [bundle.policy_id], f"未确认列表为空 = 静默半开: {unconfirmed}"


@pytest.mark.asyncio
async def test_confirmed_opa_delete_counts_and_reports_no_gap(
    db_session, make_user, monkeypatch
):
    """反向臂：OPA 确认删除时 revoked=1 / 列表为空（防止实现把一切都报成缺口）。"""
    from app.services import contract_service as cs
    from app.services.opa_client import opa_client

    _, provider = await make_user("data_provider", "prov")
    contract, bundle = await _mk_contract_with_bundle(
        db_session, provider, str(uuid.uuid4())
    )

    async def _fake_delete(package_path: str) -> bool:
        return True

    monkeypatch.setattr(opa_client, "delete_policy", _fake_delete)

    revoked, unconfirmed = await cs.contract_service._revoke_policy_bundles(
        db_session, contract
    )
    assert (revoked, unconfirmed) == (1, [])
    assert bundle.revoked_at is not None


@pytest.mark.asyncio
async def test_terminate_audit_discloses_unconfirmed_bundle(
    db_session, make_user, monkeypatch, caplog
):
    """整条 terminate 路径：审计 detail 必须带上未确认的策略包，日志必须 ERROR 级。"""
    from app.services import contract_service as cs
    from app.services.opa_client import opa_client

    _, provider = await make_user("data_provider", "prov")
    _, buyer = await make_user("buyer", "buy")
    contract, bundle = await _mk_contract_with_bundle(db_session, provider, buyer)

    async def _fake_delete(package_path: str) -> bool:
        return False

    monkeypatch.setattr(opa_client, "delete_policy", _fake_delete)

    with caplog.at_level("ERROR", logger="app.services.contract_service"):
        result = await cs.contract_service.terminate(
            db_session, contract.id, uuid.UUID(provider)
        )
    await db_session.flush()

    assert result.status == ContractStatus.TERMINATED.value

    rows = (
        await db_session.execute(
            select(AuditLog).where(AuditLog.action == "contract.terminate_cascade")
        )
    ).scalars().all()
    assert rows, "terminate 级联必须留下审计"
    detail = rows[-1].detail or {}
    assert detail.get("policy_bundles_revoked") == 0, detail
    assert detail.get("policy_bundles_opa_unconfirmed") == [bundle.policy_id], (
        f"审计里看不到 OPA 未确认的策略包 = 对运维静默: {detail}"
    )

    err = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
    assert any(bundle.policy_id in m for m in err), (
        f"未确认删除必须打 ERROR（不是 WARNING）：{err}"
    )


@pytest.mark.asyncio
async def test_raising_delete_is_also_unconfirmed(db_session, make_user, monkeypatch):
    """将来若 delete_policy 改回抛异常，也不得被当成已撤销（兜底臂）。"""
    from app.services import contract_service as cs
    from app.services.opa_client import opa_client

    _, provider = await make_user("data_provider", "prov")
    contract, bundle = await _mk_contract_with_bundle(
        db_session, provider, str(uuid.uuid4())
    )

    async def _boom(package_path: str) -> bool:
        raise RuntimeError("opa down")

    monkeypatch.setattr(opa_client, "delete_policy", _boom)

    revoked, unconfirmed = await cs.contract_service._revoke_policy_bundles(
        db_session, contract
    )
    assert revoked == 0
    assert unconfirmed == [bundle.policy_id]
