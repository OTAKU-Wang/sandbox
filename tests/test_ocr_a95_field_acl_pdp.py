"""A95: 查询期字段级控制在两条评估路径上口径不一致（OPA 优先路径把字段 ACL 结果丢了）。

`PolicyEvaluator` 有两条判定路径：OPA 边车优先、Python PDP 回落。Python 回落里
「请求的字段被字段 ACL 全部拒绝」是 DENY，而 OPA 放行后只是把字段结果**附**在
ALLOW 上——即使一个字段都没留下也照样 ALLOW（policy_evaluator.py:138-150）。
字段级控制被静默丢弃的那一条恰好是生产优先走的那一条。

本文件把两条路径的口径钉成一致。字段 ACL 本身（`rule is None ⇒ allow`、
`field_acl` 为空 ⇒ 全放行）是既有共享断言（tests/test_policy_evaluator.py），本文件不改它。

OPA 传输边界（push_policy/evaluate）在本文件内钉死：本机可能正跑着真 OPA(:8181)，
不钉住就无法确定性地测到某一条路径。
"""
import uuid

import pytest

from app.models.policy_bundle import PolicyBundle
from app.services.opa_client import opa_client
from app.services.policy_evaluator import AccessRequest, Decision, PolicyEvaluator
from app.utils.crypto import sm3_hash


class _FakeOPA:
    def __init__(self, *, allowed: bool, push_ok: bool = True):
        self.allowed = allowed
        self.push_ok = push_ok
        self.pushed: list[str] = []
        self.evaluated: list[dict] = []

    async def push_policy(self, package_path: str, rego_source: str) -> bool:
        self.pushed.append(package_path)
        return self.push_ok

    async def evaluate(self, package_path: str, input_data: dict) -> dict | None:
        self.evaluated.append(input_data)
        return {"result": self.allowed}


@pytest.fixture()
def evaluator():
    return PolicyEvaluator()


@pytest.fixture()
def bundle():
    rego = "package cds.policies\ndefault allow = false\nallow { true }"
    return PolicyBundle(
        id=uuid.uuid4(),
        policy_id="policy-a95",
        contract_id=uuid.uuid4(),
        rego_source=rego,
        integrity_hash=sm3_hash(rego.encode()),
        field_acl={
            "name": {"read": True, "mask_pattern": "***"},
            "secret_field": {"read": False},
        },
        allowed_ops=["read"],
        sandbox_modes=["query"],
    )


def _request(bundle, fields: list[str]) -> AccessRequest:
    return AccessRequest(
        user_id=uuid.uuid4(),
        contract_id=bundle.contract_id,
        operation="read",
        sandbox_mode="query",
        resource_path="/data/t.csv",
        fields=fields,
    )


def _pin_opa(monkeypatch, fake: _FakeOPA) -> None:
    monkeypatch.setattr(opa_client, "push_policy", fake.push_policy)
    monkeypatch.setattr(opa_client, "evaluate", fake.evaluate)


def _stub_bundle_db(bundle):
    from unittest.mock import AsyncMock, MagicMock

    db = AsyncMock()
    res = MagicMock()
    res.scalar_one_or_none.return_value = bundle
    db.execute = AsyncMock(return_value=res)
    return db


@pytest.mark.asyncio
async def test_opa_allow_denies_when_field_acl_denies_every_requested_field(evaluator, bundle, monkeypatch):
    _pin_opa(monkeypatch, _FakeOPA(allowed=True))
    decision = await evaluator.evaluate(_request(bundle, ["secret_field"]), _stub_bundle_db(bundle))

    assert decision.decision == Decision.DENY, (
        f"OPA 放行后字段 ACL 全拒仍返回 {decision.decision}：字段级控制被静默丢弃"
    )
    assert decision.denied_fields == ["secret_field"]


@pytest.mark.asyncio
async def test_opa_allow_keeps_allowed_subset(evaluator, bundle, monkeypatch):
    _pin_opa(monkeypatch, _FakeOPA(allowed=True))
    decision = await evaluator.evaluate(_request(bundle, ["name", "secret_field"]), _stub_bundle_db(bundle))

    assert decision.decision == Decision.ALLOW
    assert decision.allowed_fields == ["name"]
    assert decision.denied_fields == ["secret_field"]
    assert decision.field_masks == {"name": "***"}


@pytest.mark.asyncio
async def test_python_fallback_parity_for_all_fields_denied(evaluator, bundle, monkeypatch):
    _pin_opa(monkeypatch, _FakeOPA(allowed=True, push_ok=False))
    decision = await evaluator.evaluate(_request(bundle, ["secret_field"]), _stub_bundle_db(bundle))

    assert decision.decision == Decision.DENY
    assert decision.reason == "All requested fields are denied by field ACL"


@pytest.mark.asyncio
async def test_opa_allow_without_field_requests_is_unaffected(evaluator, bundle, monkeypatch):
    fake = _FakeOPA(allowed=True)
    _pin_opa(monkeypatch, fake)
    decision = await evaluator.evaluate(_request(bundle, []), _stub_bundle_db(bundle))

    assert decision.decision == Decision.ALLOW
    assert decision.reason == "OPA policy evaluation passed"
    assert len(fake.pushed) == 1
