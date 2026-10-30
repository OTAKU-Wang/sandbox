"""A160b — `OPAClient.close()` 之后求值不得被读成「OPA 暂时不可用」的正常判定。

`app/main.py:430-431` 在 lifespan 关闭时 `await opa_client.close()`。前半已由兄弟轨道落地：
`close()` 摘掉 client 引用并置终态，`_get_client()` 在终态下拒绝重建（否则每个迟到请求都会
 mint 一个没人再 close 的 `AsyncClient`，连接池按次泄漏）。缺的是**披露**这半边：

1. 终态没有任何可读出口——`push_policy/evaluate/delete_policy/health` 在关闭后一律返回
   False/None（这是 tests/test_ocr_a160_opa_client_close.py 钉住的行为），于是消费方
   无法区分「OPA 侧车此刻不可达」与「本进程已经关闭 OPA 客户端」；
2. `PolicyEvaluator.evaluate` 把这四种返回值统一当作「OPA 不可用 → Python 回落」，
   判决照常 ALLOW，`reason` 里也看不出差异 ⇒ shutdown 之后的求值是**静默半开**：
   生产 PDP（OPA）已经不在环上，读结果的人看不出来（A160 原文：安全组件里
   「静默降到 Python PDP」永远看不到根因，四处 `except Exception` 连 traceback 都丢掉）。

要求（关闭语义要么幂等可重开、要么明确拒绝并披露）：终态可读 + 回落到哪一层、
为什么回落，必须出现在 `PolicyDecision` 的结构化字段与 WARNING 里。
可用性不许被收紧：Python PDP 是文档化的设计内回落， legitimate 请求仍要拿到判决。
"""
import logging
import uuid

import pytest

from app.models.policy_bundle import PolicyBundle
from app.services import opa_client as opa_module
from app.services.opa_client import OPAClient, opa_client
from app.services.policy_evaluator import AccessRequest, Decision, PolicyEvaluator
from app.utils.crypto import sm3_hash


def _bundle() -> PolicyBundle:
    rego = "package cds.policies\ndefault allow = true"
    return PolicyBundle(
        id=uuid.uuid4(),
        policy_id="policy-a160b",
        contract_id=uuid.uuid4(),
        rego_source=rego,
        integrity_hash=sm3_hash(rego.encode()),
        field_acl=None,
        allowed_ops=["read"],
        sandbox_modes=["query"],
    )


def _request(bundle: PolicyBundle) -> AccessRequest:
    return AccessRequest(
        user_id=uuid.uuid4(),
        contract_id=bundle.contract_id,
        operation="read",
        sandbox_mode="query",
        fields=["name"],
        epsilon_cost=0.0,
    )


def _stub_db(bundle: PolicyBundle):
    from unittest.mock import AsyncMock, MagicMock

    db = AsyncMock()
    res = MagicMock()
    res.scalar_one_or_none.return_value = bundle
    db.execute = AsyncMock(return_value=res)
    return db


@pytest.fixture()
def evaluator():
    return PolicyEvaluator()


@pytest.fixture()
def bundle():
    return _bundle()


@pytest.mark.asyncio
async def test_terminal_state_has_a_readable_accessor():
    client = OPAClient(base_url="http://127.0.0.1:1")
    await client._get_client()
    assert client.is_shutdown is False

    await client.close()
    assert client.is_shutdown is True, (
        "close() 之后终态必须可查询，否则消费方只能把『已关闭』当成『暂时不可达』"
    )


@pytest.mark.asyncio
async def test_close_stays_idempotent_and_terminal():
    client = OPAClient(base_url="http://127.0.0.1:1")
    await client._get_client()
    await client.close()
    await client.close()
    assert client.is_shutdown is True
    assert client._client is None


@pytest.mark.asyncio
async def test_closed_opa_client_is_disclosed_not_silently_half_open(evaluator, bundle, monkeypatch, caplog):
    closed = OPAClient(base_url="http://127.0.0.1:1")
    await closed._get_client()
    await closed.close()
    monkeypatch.setattr(opa_module, "opa_client", closed)

    with caplog.at_level(logging.WARNING):
        decision = await evaluator.evaluate(_request(bundle), _stub_db(bundle))

    assert decision.decision == Decision.ALLOW, "Python PDP 是设计内回落，不许顺手把可用性收紧"
    assert decision.policy_source == "python-fallback", decision
    assert decision.degraded is True, decision
    assert decision.degraded_reason == "opa_client_closed", decision
    warns = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("closed" in m or "shutdown" in m for m in warns), warns


@pytest.mark.asyncio
async def test_transient_opa_failure_discloses_a_different_cause(evaluator, bundle, monkeypatch):
    async def _push_unavailable(*args, **kwargs):
        return False

    monkeypatch.setattr(opa_client, "push_policy", _push_unavailable)
    decision = await evaluator.evaluate(_request(bundle), _stub_db(bundle))

    assert decision.decision == Decision.ALLOW
    assert decision.policy_source == "python-fallback", decision
    assert decision.degraded is True, decision
    assert decision.degraded_reason == "opa_unavailable", decision


@pytest.mark.asyncio
async def test_opa_path_is_labelled_as_opa(evaluator, bundle, monkeypatch):
    async def _push_ok(*args, **kwargs):
        return True

    async def _eval_ok(*args, **kwargs):
        return {"result": True}

    monkeypatch.setattr(opa_client, "push_policy", _push_ok)
    monkeypatch.setattr(opa_client, "evaluate", _eval_ok)
    decision = await evaluator.evaluate(_request(bundle), _stub_db(bundle))

    assert decision.decision == Decision.ALLOW
    assert decision.policy_source == "opa", decision
    assert decision.degraded is False, decision
    assert decision.degraded_reason is None, decision


@pytest.mark.asyncio
async def test_transport_error_keeps_the_traceback(evaluator, bundle, monkeypatch, caplog):
    async def _push_boom(*args, **kwargs):
        raise ValueError("rego bundle misconfigured")

    monkeypatch.setattr(opa_client, "push_policy", _push_boom)
    with caplog.at_level(logging.ERROR):
        decision = await evaluator.evaluate(_request(bundle), _stub_db(bundle))

    assert decision.degraded is True, decision
    errs = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert errs, "OPA 传输异常必须留下 ERROR 日志"
    assert any(r.exc_info for r in errs), (
        "安全组件只记 str(e) 就等于『配置写错也静默降到 Python PDP 且永远看不到根因』"
    )
