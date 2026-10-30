"""A57 — 脱敏视图必须是强制边界：非脱敏管理员不得直查已脱敏的基表。

台账：ocr-verified-findings.md A57（/masked-view 要 ADMIN/OPERATOR，/query 只要 get_current_user）。
契约：W2 {code,message,detail,request_id}，拒绝码 FORBIDDEN / HTTP 403。
"""
import json
import uuid

import pytest

from app.models.sandbox_session import SandboxSession
from app.services.audit_service import audit_service

DB = "/api/v1/sandbox-db"
PHONE = "13800000001"


@pytest.fixture
def audit_spy(monkeypatch):
    calls: list[dict] = []

    async def _log_standalone(**kwargs):
        calls.append(kwargs)
        return object()

    monkeypatch.setattr(audit_service, "log_standalone", _log_standalone)
    return calls


async def _own_session(db_session, user_id: str) -> str:
    session = SandboxSession(
        id=uuid.uuid4(),
        user_id=uuid.UUID(user_id),
        data_product_id=uuid.uuid4(),
        sandbox_level="l2c",
        sandbox_mode="structured_query",
        status="running",
        contract_id=None,
        timeout_seconds=3600,
    )
    db_session.add(session)
    await db_session.commit()
    return str(session.id)


async def _people_table(client, headers, sid: str) -> None:
    resp = await client.post(
        f"{DB}/create-table",
        json={"session_id": sid, "table_name": "people",
              "data": [{"name": "alice", "phone": PHONE},
                       {"name": "bob", "phone": "13800000002"}]},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text


async def _redact_phone(client, operator_headers, sid: str) -> None:
    resp = await client.post(
        f"{DB}/masked-view",
        json={"session_id": sid, "table_name": "people",
              "rules": [{"field_name": "phone", "mask_type": "REDACT"}]},
        headers=operator_headers,
    )
    assert resp.status_code == 200, resp.text


async def _query(client, headers, sid: str, sql: str):
    return await client.post(f"{DB}/query", json={"session_id": sid, "sql": sql}, headers=headers)


@pytest.mark.asyncio
async def test_masked_base_table_is_invisible_to_non_privileged_owner(
    client, db_session, make_user, operator_headers, audit_spy
):
    """A57：会话拥有者（DATA_PROVIDER）直查已脱敏基表 ⇒ 403 FORBIDDEN + 明文不外泄 + 审计留痕。"""
    headers, uid = await make_user("data_provider", "a57own")
    sid = await _own_session(db_session, uid)
    await _people_table(client, headers, sid)
    await _redact_phone(client, operator_headers, sid)

    resp = await _query(client, headers, sid, "SELECT name, phone FROM people")
    assert resp.status_code == 403, resp.text
    body = resp.json()
    assert body["code"] == "FORBIDDEN", body
    assert body["request_id"]
    assert PHONE not in json.dumps(body, default=str)
    assert [c.get("action") for c in audit_spy] == ["sandbox_db.masked_view_bypass_denied"]


@pytest.mark.asyncio
async def test_masked_view_still_serves_the_non_privileged_owner(client, db_session, make_user, operator_headers):
    """A57 的反面：脱敏视图本身照常可查，且拿到的是脱敏后的值。"""
    headers, uid = await make_user("data_provider", "a57view")
    sid = await _own_session(db_session, uid)
    await _people_table(client, headers, sid)
    await _redact_phone(client, operator_headers, sid)

    resp = await _query(client, headers, sid, "SELECT name, phone FROM people_masked ORDER BY name")
    assert resp.status_code == 200, resp.text
    rows = resp.json()["rows"]
    assert [r[0] for r in rows] == ["alice", "bob"]
    assert all(r[1] is None for r in rows), rows


@pytest.mark.asyncio
async def test_subquery_and_cte_cannot_smuggle_the_masked_base_table(client, db_session, make_user, operator_headers):
    """A57：改写 SQL 形状（子查询 / CTE 同名）也不能把基表带进来。"""
    headers, uid = await make_user("data_provider", "a57sneak")
    sid = await _own_session(db_session, uid)
    await _people_table(client, headers, sid)
    await _redact_phone(client, operator_headers, sid)

    for sql in (
        "SELECT * FROM (SELECT * FROM people)",
        "WITH people AS (SELECT * FROM people) SELECT * FROM people",
        "SELECT p.phone FROM people AS p JOIN people_masked m ON m.name = p.name",
    ):
        resp = await _query(client, headers, sid, sql)
        assert resp.status_code == 403, f"{sql} -> {resp.status_code} {resp.text}"
        assert resp.json()["code"] == "FORBIDDEN"


@pytest.mark.asyncio
async def test_masking_admin_may_still_query_the_base_table(client, db_session, make_user, operator_headers):
    """A57：脱敏的管理员（ADMIN/OPERATOR）仍可直查基表 —— 边界针对的是被脱敏保护的对象。"""
    headers, uid = await make_user("data_provider", "a57adm")
    sid = await _own_session(db_session, uid)
    await _people_table(client, headers, sid)
    await _redact_phone(client, operator_headers, sid)

    resp = await _query(client, operator_headers, sid, "SELECT name, phone FROM people ORDER BY name")
    assert resp.status_code == 200, resp.text
    assert resp.json()["rows"][0][1] == PHONE


@pytest.mark.asyncio
async def test_unmasked_session_query_unchanged(client, db_session, make_user):
    """A57 的反面：没有配置脱敏的会话，基表查询行为不变。"""
    headers, uid = await make_user("data_provider", "a57free")
    sid = await _own_session(db_session, uid)
    await _people_table(client, headers, sid)

    resp = await _query(client, headers, sid, "SELECT name, phone FROM people ORDER BY name")
    assert resp.status_code == 200, resp.text
    assert resp.json()["rows"][0][1] == PHONE
