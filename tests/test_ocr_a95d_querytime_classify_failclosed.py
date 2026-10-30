"""A95d — 查询期字段分级取数失败必须收紧，不得当成「该合约没有分级」放行。

台账：ocr-verified-findings.md A95 家族第三处（前两处已修：发放门
``app/services/output_policy.py`` = A95c、路由装配 = A95b）。本条出处 =
A95c 落地时留下的最后一处 fail-open。

缺陷（改前实况）：``app/api/sandbox_db._field_classifications_for_contract``
对 product_ids 里不可解析的条目走 ``continue``，对任何取数异常走宽
``except Exception: logger.warning(...); return {}``。``{}`` 在引擎侧的含义是
「该合约没有字段分级」⇒ 分级**不可知**被读成**没有分级**：deny_out（level≥4）
列经 /query 原文读出、mask（level==3）列出原文，而且只在 WARNING 里留一行，
生产 INFO 级别下等同静默放行。发放门（A95c）早就把这两种含义分开了，查询期
是这条链上最后一个没分的。

改后绿（与 A95c 同族语义、复用同一个 ClassificationUnavailableError）：
1) 取数不可知（不可解析 id / 悬空资源引用 / DB 异常）⇒ 该会话的数据操作被拒
   （400 POLICY_COMPILE_ERROR），并留下 ≥ERROR 的 ``[sandbox-db] SECURITY`` 披露
   + 请求事务外的拒绝审计；响应里没有任何一行原文数据；
2) 取数成功 ⇒ 分级照常喂给引擎：deny 列查询期被拒、mask 列出 "***"；
3) 不误伤：确实没有分级资源的合约（资源无分级 / 只有低分级 / 产品行不存在）
   行为与改前逐字一致（原样放行、原样出明文）。
"""
import ast
import inspect
import logging
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession as _AsyncSession

from app.api import sandbox_db
from app.models.audit_log import AuditLog
from app.models.contract import Contract, ContractStatus
from app.models.data_product import DataProduct
from app.models.data_resource import DataResource, ResourceStatus
from app.models.sandbox_session import SandboxSession, SessionStatus
from app.services import output_policy
from app.services.audit_service import audit_service

DB = "/api/v1/sandbox-db"

ROWS = [
    {"name": "alice", "phone": "13800000001", "age": "30"},
    {"name": "bob", "phone": "13800000002", "age": "25"},
]


# --------------------------------------------------------------------------- helpers

async def _mk_resource(db_session, uid: str, classifications: dict[str, int] | None) -> DataResource:
    resource = DataResource(
        provider_id=uuid.UUID(uid),
        name=f"a95d-res-{uuid.uuid4().hex[:8]}",
        status=ResourceStatus.READY.value,
        field_classifications=classifications,
    )
    db_session.add(resource)
    await db_session.flush()
    await db_session.refresh(resource)
    return resource


async def _mk_product(db_session, uid: str, resource_id) -> DataProduct:
    product = DataProduct(
        provider_id=uuid.UUID(uid),
        name=f"a95d-prod-{uuid.uuid4().hex[:8]}",
        resource_id=resource_id,
    )
    db_session.add(product)
    await db_session.flush()
    await db_session.refresh(product)
    return product


async def _mk_contract(db_session, uid: str, product_ids: list[str]) -> Contract:
    contract = Contract(
        contract_no=f"A95D-{uuid.uuid4().hex[:12]}",
        contract_type="data_query",
        status=ContractStatus.ACTIVE.value,
        provider_id=uuid.UUID(uid),
        buyer_id=uuid.UUID(uid),
        title="a95d query-time classification fail-closed",
        terms={},
        product_ids=product_ids,
        allowed_sandbox_levels="L3",
        allowed_sandbox_modes=["query"],
        allowed_operations="read,analyze",
        max_duration_hours=1,
        max_output_rows=1000,
    )
    db_session.add(contract)
    await db_session.flush()
    await db_session.refresh(contract)
    return contract


async def _mk_session(db_session, uid: str, contract_id: str) -> str:
    session = SandboxSession(
        id=uuid.uuid4(),
        user_id=uuid.UUID(uid),
        data_product_id=uuid.uuid4(),
        sandbox_level="L3",
        sandbox_mode="structured_query",
        status=SessionStatus.RUNNING.value,
        contract_id=contract_id,
        timeout_seconds=3600,
    )
    db_session.add(session)
    await db_session.flush()
    await db_session.refresh(session)
    return str(session.id)


async def _mk_stack(
    db_session,
    uid: str,
    classifications: dict[str, int] | None,
    ghost_product: bool = False,
):
    """resource → product → contract；ghost_product=True 时产品行不入库。"""
    resource = await _mk_resource(db_session, uid, classifications)
    if ghost_product:
        return await _mk_contract(db_session, uid, [str(uuid.uuid4())]), resource
    product = await _mk_product(db_session, uid, resource.id)
    return await _mk_contract(db_session, uid, [str(product.id)]), resource


async def _create_table(client, headers, session_id: str):
    return await client.post(
        f"{DB}/create-table",
        json={"session_id": session_id, "table_name": "people", "data": [dict(r) for r in ROWS]},
        headers=headers,
    )


async def _query(client, headers, session_id: str, sql: str):
    return await client.post(
        f"{DB}/query", json={"session_id": session_id, "sql": sql}, headers=headers
    )


def _cleanup(session_id: str):
    engine = sandbox_db._engines.pop(session_id, None)
    if engine is not None:
        engine.close()


@pytest.fixture
def audit_spy(monkeypatch):
    """录下拒绝类审计（写在请求事务之外，见 app/api 约定）。"""
    calls: list[dict[str, object]] = []

    async def _record(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(audit_service, "log_standalone", _record)
    return calls


def _security_records(caplog) -> list[str]:
    return [
        r.getMessage() for r in caplog.records
        if r.levelno >= logging.ERROR and "SECURITY" in r.getMessage()
    ]


# ------------------------------------------------------------------ 取数不可知 ⇒ 拒绝

@pytest.mark.asyncio
async def test_unresolvable_product_id_refuses_data_ops_and_discloses(
    client, db_session, make_user, caplog, audit_spy
):
    """不可解析 product id ⇒ 分级不可知：数据操作被拒 + ≥ERROR 披露，绝不静默放行。

    改前：`continue` 跳过坏 id ⇒ merged={} ⇒ 引擎以为无分级 ⇒ 建表 200、
    SELECT phone 出原文（同文件 test_deny_field_* 的形状），只留一行 WARNING。
    """
    headers, uid = await make_user("data_provider", "a95dbadid")
    contract = await _mk_contract(db_session, uid, ["not-a-uuid"])
    sid = await _mk_session(db_session, uid, str(contract.id))

    try:
        with caplog.at_level(logging.WARNING, logger="app.api.sandbox_db"):
            resp = await _create_table(client, headers, sid)

        assert resp.status_code == 400, (
            f"分级不可知仍放行（改前红）：{resp.status_code} {resp.text[:200]}"
        )
        body = resp.json()
        assert body["code"] == "POLICY_COMPILE_ERROR"
        assert body["detail"]["contract_id"] == str(contract.id)
        assert "field classification" in str(body["detail"]["reason"]).lower()
        assert "13800000001" not in resp.text

        # 响亮披露：必须 ERROR（WARNING 在生产的 INFO 门槛下等于没有）
        assert _security_records(caplog), (
            "分级取数失败必须留 ≥ERROR 的 [sandbox-db] SECURITY 披露，"
            f"实际日志 = {[r.getMessage() for r in caplog.records]}"
        )
        assert any("field classification" in m.lower() for m in _security_records(caplog))

        # 拒绝类审计写在请求事务之外
        assert "sandbox_db.policy_attach_denied" in [c.get("action") for c in audit_spy]
        entry = next(c for c in audit_spy
                     if c.get("action") == "sandbox_db.policy_attach_denied")
        assert "field classification" in str(entry["detail"]["reason"]).lower()

        # 拒绝不是一次性的：同会话后续数据操作同样被拒
        again = await _query(client, headers, sid, "SELECT 1")
        assert again.status_code == 400
        assert again.json()["code"] == "POLICY_COMPILE_ERROR"
    finally:
        _cleanup(sid)


@pytest.mark.asyncio
async def test_db_error_refuses_query_and_discloses(
    client, db_session, make_user, monkeypatch, caplog, audit_spy
):
    """真 DB 异常（资源行读不出来）⇒ 收紧为拒绝，不得 warning 后继续。"""
    headers, uid = await make_user("data_provider", "a95ddbpump")
    resource = await _mk_resource(db_session, uid, {"ssn": 4})
    product = await _mk_product(db_session, uid, resource.id)
    contract = await _mk_contract(db_session, uid, [str(product.id)])
    sid = await _mk_session(db_session, uid, str(contract.id))

    orig_get = _AsyncSession.get

    async def _boom(self, entity, ident, *args, **kwargs):
        if entity is DataResource:
            raise RuntimeError("simulated connection loss")
        return await orig_get(self, entity, ident, *args, **kwargs)

    monkeypatch.setattr(_AsyncSession, "get", _boom)
    try:
        with caplog.at_level(logging.WARNING, logger="app.api.sandbox_db"):
            resp = await _create_table(client, headers, sid)

        assert resp.status_code == 400, (
            f"取数异常被吞成「无分级」后放行（改前红）：{resp.status_code} {resp.text[:200]}"
        )
        assert resp.json()["code"] == "POLICY_COMPILE_ERROR"
        assert "simulated connection loss" in str(resp.json()["detail"]["reason"])
        assert _security_records(caplog), [r.getMessage() for r in caplog.records]
    finally:
        _cleanup(sid)


@pytest.mark.asyncio
async def test_dangling_resource_reference_refuses_and_is_audited(
    client, db_session, make_user, caplog
):
    """产品指向不存在的资源 ⇒ 分级不可知 ⇒ 拒绝，并留下事务外的拒绝审计。

    本例故意不 spy log_standalone：拒绝路径在 SQLite 测试台上也必须能真写出审计行
    （不能因为持有请求会话就锁死或吞掉）。
    """
    headers, uid = await make_user("data_provider", "a95ddangle")
    product = await _mk_product(db_session, uid, uuid.uuid4())  # 资源行不存在
    contract = await _mk_contract(db_session, uid, [str(product.id)])
    sid = await _mk_session(db_session, uid, str(contract.id))

    try:
        with caplog.at_level(logging.WARNING, logger="app.api.sandbox_db"):
            resp = await _create_table(client, headers, sid)

        assert resp.status_code == 400, resp.text
        assert resp.json()["code"] == "POLICY_COMPILE_ERROR"
        assert _security_records(caplog)

        rows = (await db_session.execute(
            select(AuditLog).where(AuditLog.action == "sandbox_db.policy_attach_denied")
        )).scalars().all()
        assert rows, "拒绝必须留下事务外的审计行"
    finally:
        _cleanup(sid)


@pytest.mark.asyncio
async def test_resolver_raises_the_shared_error_type(db_session, make_user):
    """复用 A95c 的异常类型，不另造一套；且「不可知」与「无分级」在返回值上分开。"""
    assert sandbox_db.ClassificationUnavailableError is output_policy.ClassificationUnavailableError

    _, uid = await make_user("data_provider", "a95dresolver")
    bad = await _mk_contract(db_session, uid, ["not-a-uuid"])
    with pytest.raises(output_policy.ClassificationUnavailableError):
        await sandbox_db._field_classifications_for_contract(db_session, bad)

    # 正面确认无分级 ⇒ 仍然只是 {}（不是异常）：不误伤的边界
    resource = await _mk_resource(db_session, uid, None)
    product = await _mk_product(db_session, uid, resource.id)
    clean = await _mk_contract(db_session, uid, [str(product.id)])
    assert await sandbox_db._field_classifications_for_contract(db_session, clean) == {}

    # 产品行不存在 = 「没有可分级资源」，与发放门口径一致
    ghost = await _mk_contract(db_session, uid, [str(uuid.uuid4())])
    assert await sandbox_db._field_classifications_for_contract(db_session, ghost) == {}


def test_lookup_failure_never_falls_back_to_empty_classifications():
    """结构闸：解析函数里的宽 except 只准上抛，不准「打日志后返回 {}」。

    返回 {} 就是「该合约没有分级」，宽 except 里返回它 = 把不可知洗成无分级 ——
    A95d 的缺陷本体，行为测试翻面后仍可能被一次重构悄悄改回来。
    """
    fn = next(
        node for node in ast.walk(ast.parse(inspect.getsource(sandbox_db)))
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_field_classifications_for_contract"
    )
    handlers = [
        node for node in ast.walk(fn)
        if isinstance(node, ast.ExceptHandler)
        and (node.type is None
             or (isinstance(node.type, ast.Name) and node.type.id in ("Exception", "BaseException")))
    ]
    assert handlers, "解析函数必须兜住取数异常（否则 DB 异常会变成 500 而不是语义化拒绝）"
    for handler in handlers:
        assert any(isinstance(n, ast.Raise) for n in ast.walk(handler)), (
            "宽 except 里只准 raise ClassificationUnavailableError，"
            "return {} 会把「分级不可知」洗成「无分级」而 fail-open"
        )


# --------------------------------------------------------------- 取到分级 ⇒ 引擎执法

@pytest.mark.asyncio
async def test_deny_field_never_appears_in_query_result(client, db_session, make_user):
    """取数成功侧的执法面：level≥4 的列在查询期不得出现原文。"""
    headers, uid = await make_user("data_provider", "a95ddeny")
    contract, _ = await _mk_stack(db_session, uid, {"phone": 4})
    sid = await _mk_session(db_session, uid, str(contract.id))
    try:
        assert (await _create_table(client, headers, sid)).status_code == 200

        resp = await _query(client, headers, sid, "SELECT name, phone FROM people")
        assert resp.status_code == 400, f"deny 列经查询期原文出密：{resp.text[:300]}"
        assert "13800000001" not in resp.text

        star = await _query(client, headers, sid, "SELECT * FROM people")
        assert star.status_code == 400, star.text

        ok = await _query(client, headers, sid, "SELECT name FROM people")
        assert ok.status_code == 200, ok.text
        assert sorted(r[0] for r in ok.json()["rows"]) == ["alice", "bob"]
    finally:
        _cleanup(sid)


@pytest.mark.asyncio
async def test_mask_field_is_masked_at_query_time(client, db_session, make_user):
    """level==3 的列：查询期出掩码，不出原文。"""
    headers, uid = await make_user("data_provider", "a95dmask")
    contract, _ = await _mk_stack(db_session, uid, {"phone": 3})
    sid = await _mk_session(db_session, uid, str(contract.id))
    try:
        assert (await _create_table(client, headers, sid)).status_code == 200
        resp = await _query(client, headers, sid, "SELECT name, phone FROM people")
        assert resp.status_code == 200, resp.text
        payload = resp.json()
        phone_idx = payload["columns"].index("phone")
        assert {r[phone_idx] for r in payload["rows"]} == {"***"}, payload["rows"]
        assert "13800000001" not in resp.text
    finally:
        _cleanup(sid)


@pytest.mark.asyncio
async def test_contracts_without_classified_resources_still_pass_through(
    client, db_session, make_user
):
    """不误伤：三种「正面确认无分级」的形态与改前逐字一致（原样放行、出明文）。"""
    cases = {
        "a95dfree": (None, False),
        "a95dlow": ({"name": 1, "age": 2}, False),
        "a95dghost": ({"phone": 4}, True),
    }
    for tag, (classifications, ghost_product) in cases.items():
        headers, uid = await make_user("data_provider", tag)
        contract, _ = await _mk_stack(db_session, uid, classifications, ghost_product)
        sid = await _mk_session(db_session, uid, str(contract.id))
        try:
            created = await _create_table(client, headers, sid)
            assert created.status_code == 200, f"{tag}: {created.status_code} {created.text[:200]}"
            resp = await _query(client, headers, sid, "SELECT * FROM people")
            assert resp.status_code == 200, f"{tag}: {resp.status_code} {resp.text[:200]}"
            assert resp.json()["rows"][0][1] == "13800000001", f"{tag}: 无分级合约被误收紧"
        finally:
            _cleanup(sid)
