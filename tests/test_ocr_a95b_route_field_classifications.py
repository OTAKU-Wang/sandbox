"""A95b — sandbox_db 路由装配路径必须把 DataResource.field_classifications 传给引擎。

台账：ocr-verified-findings.md A95（查询期字段门）+ owner-svc-b 转交（A95b）。
引擎侧（secure_duckdb.set_field_classifications / _effective_field_rules / 查询期掩码）
已由 A95 落好，并由 tests/test_ocr_a95_querytime_field_acl.py 在**引擎层**钉住；
本文件钉的是**生产装配路径**：`_resolve_contract_policy` 在 set_policy 之后必须把
合约所关联 DataResource 的字段分级喂给引擎 —— 否则引擎修好了也没有分类可用，
等于没修。

红（HEAD 实况）：路由从不读取 DataResource.field_classifications ⇒
- level≥4（deny_out）列经 /query 原文读出（应 400 拒）；
- SELECT * 覆盖 deny 列照样放行（应 400）；
- level==3（mask）列出原文（应出 "***"）。
绿：以上三条全部翻面，且无分级资源的行为与改前一致。
"""
import uuid

import pytest

from app.api import sandbox_db
from app.models.contract import Contract, ContractStatus
from app.models.data_product import DataProduct
from app.models.data_resource import DataResource, ResourceStatus
from app.models.sandbox_session import SandboxSession, SessionStatus

DB = "/api/v1/sandbox-db"

ROWS = [
    {"name": "alice", "phone": "13800000001", "age": "30"},
    {"name": "bob", "phone": "13800000002", "age": "25"},
]


async def _mk_stack(db_session, uid: str, classifications: dict[str, int] | None):
    """resource(field_classifications) → product → contract → session 全链落库。

    形态与 tests/test_sandbox_db_policy_gate.py 的 _mk_contract/_mk_session 一致，
    只把 product_ids/data_product_id 换成真实行，让路由能沿
    contract.product_ids → DataProduct.resource_id → DataResource 取到分级。
    """
    resource = DataResource(
        provider_id=uuid.UUID(uid),
        name=f"a95b-res-{uuid.uuid4().hex[:8]}",
        status=ResourceStatus.READY.value,
        field_classifications=classifications,
    )
    db_session.add(resource)
    await db_session.flush()

    product = DataProduct(
        provider_id=uuid.UUID(uid),
        name=f"a95b-prod-{uuid.uuid4().hex[:8]}",
        resource_id=resource.id,
    )
    db_session.add(product)
    await db_session.flush()

    contract = Contract(
        contract_no=f"A95B-{uuid.uuid4().hex[:12]}",
        contract_type="data_query",
        status=ContractStatus.ACTIVE.value,
        provider_id=uuid.UUID(uid),
        buyer_id=uuid.UUID(uid),
        title="a95b route field acl",
        terms={},
        product_ids=[str(product.id)],
        allowed_sandbox_levels="L3",
        allowed_sandbox_modes=["query"],
        allowed_operations="read,analyze",
        max_duration_hours=1,
        max_output_rows=1000,
    )
    db_session.add(contract)
    await db_session.flush()

    session = SandboxSession(
        id=uuid.uuid4(),
        user_id=uuid.UUID(uid),
        data_product_id=product.id,
        sandbox_level="L3",
        sandbox_mode="structured_query",
        status=SessionStatus.RUNNING.value,
        contract_id=str(contract.id),
        timeout_seconds=3600,
    )
    db_session.add(session)
    await db_session.flush()
    return str(session.id), str(contract.id)


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


@pytest.mark.asyncio
async def test_deny_out_column_never_leaves_query_route(client, db_session, make_user):
    """装配路径接线证明：level≥4 的列经 /query 必须被拒 —— 分类必须真的传到引擎。"""
    headers, uid = await make_user("data_provider", "a95bdeny")
    sid, cid = await _mk_stack(db_session, uid, {"phone": 4})
    try:
        resp = await _create_table(client, headers, sid)
        assert resp.status_code == 200, resp.text

        engine = sandbox_db.get_engine(sid)
        assert engine is not None and engine.policy_contract_id == cid
        assert engine._effective_field_rules()[1] == {"phone"}, (
            "路由没有把 DataResource.field_classifications 传给引擎："
            f"engine 侧字段规则 = {engine._effective_field_rules()}"
        )

        resp = await _query(client, headers, sid, "SELECT name, phone FROM people")
        assert resp.status_code == 400, (
            f"deny_out 列经路由原文出密：{resp.status_code} {resp.text[:300]}"
        )
        assert "phone" in resp.text

        ok = await _query(client, headers, sid, "SELECT name FROM people")
        assert ok.status_code == 200, ok.text
        assert sorted(r[0] for r in ok.json()["rows"]) == ["alice", "bob"]
    finally:
        _cleanup(sid)


@pytest.mark.asyncio
async def test_wildcard_select_denied_through_route(client, db_session, make_user):
    """SELECT * 覆盖 deny 列 ⇒ 路由级 400（fail-closed，不许静默丢列）。"""
    headers, uid = await make_user("data_provider", "a95bstar")
    sid, _ = await _mk_stack(db_session, uid, {"phone": 4})
    try:
        assert (await _create_table(client, headers, sid)).status_code == 200
        resp = await _query(client, headers, sid, "SELECT * FROM people")
        assert resp.status_code == 400, (
            f"SELECT * 覆盖 deny 列仍被放行：{resp.status_code} {resp.text[:300]}"
        )
    finally:
        _cleanup(sid)


@pytest.mark.asyncio
async def test_mask_column_returns_masked_over_http(client, db_session, make_user):
    """level==3 的列：HTTP 响应里必须是掩码，不能是原文。"""
    headers, uid = await make_user("data_provider", "a95bmask")
    sid, _ = await _mk_stack(db_session, uid, {"phone": 3})
    try:
        assert (await _create_table(client, headers, sid)).status_code == 200
        resp = await _query(client, headers, sid, "SELECT name, phone FROM people")
        assert resp.status_code == 200, resp.text
        rows = resp.json()["rows"]
        phone_idx = resp.json()["columns"].index("phone")
        assert {r[phone_idx] for r in rows} == {"***"}, (
            f"mask 级列经路由出了原文：{rows}"
        )
    finally:
        _cleanup(sid)


@pytest.mark.asyncio
async def test_resource_without_classifications_unchanged(client, db_session, make_user):
    """兼容锁：资源没有分级 ⇒ 行为与接线前一致（SELECT * 照常 200）。"""
    headers, uid = await make_user("data_provider", "a95bfree")
    sid, _ = await _mk_stack(db_session, uid, None)
    try:
        assert (await _create_table(client, headers, sid)).status_code == 200
        resp = await _query(client, headers, sid, "SELECT * FROM people")
        assert resp.status_code == 200, resp.text
        assert resp.json()["rows"][0][1] == "13800000001"
    finally:
        _cleanup(sid)
