"""A68 邻接（high）— POST/DELETE /network-policies 把 str 塞进 AuditLog.session_id。

台账: docs/ocr-review-2026-10-18/ocr-verified-findings.md §A68（同文件邻接真缺陷）
落点: app/api/network_policy.py:132（成功面 `log`）、:272（删除面 `log`）

`AuditLog.session_id` 是 `UUID(as_uuid=True)` + FK(sandbox_sessions.id)
（app/models/audit_log.py:21），而 `NetworkPolicy.session_id` 是 `String(255)`
（app/models/network_policy.py:24）。`audit_service.log()` 原样透传
（app/services/audit_service.py:166），所以传入 str 时 flush 期即
`StatementError: 'str' object has no attribute 'hex'`（SQLite 侧对合法 UUID 串
同样炸；PG 侧只在 session_id 不是合法 UUID 时炸 —— 也就是说生产与测试栈都会中，
只是触发条件不同）。

既有 `tests/test_ocr_a20_network_policy_unique_race.py:176-177` 把
`audit_service.log` / `log_standalone` 整只换成记录器，因此绿灯套件从来走不到
真实落库路径。本文件刻意**不打桩审计**，只 stub 引擎的强制面（iptables 在测试机上
不可用），断言的是"审计行真的落库了"。
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.api.network_policy as np_module
from app.models.audit_log import AuditLog
from app.models.sandbox_session import SandboxSession, SessionStatus

POLICY_URL = "/api/v1/network-policies"


async def _seed_session(db_session: AsyncSession, user_id: str) -> str:
    session = SandboxSession(
        id=uuid.uuid4(),
        user_id=uuid.UUID(user_id),
        data_product_id=uuid.uuid4(),
        sandbox_level="L3",
        sandbox_mode="structured_query",
        status=SessionStatus.RUNNING.value,
    )
    db_session.add(session)
    await db_session.flush()
    return str(session.id)


@pytest.fixture
def _stub_engine(monkeypatch):
    """只替换强制面（iptables/DNS proxy 在测试机不可用），审计链路保持真实。"""

    async def _ok(*_a, **_k):
        return {"mode": "deny_all", "enforced": True}

    monkeypatch.setattr(np_module.network_policy_engine, "create_policy", _ok)
    monkeypatch.setattr(np_module.network_policy_engine, "remove_policy", _ok)


@pytest.mark.asyncio
async def test_create_policy_persists_audit_row_with_session_link(
    client: AsyncClient,
    db_session: AsyncSession,
    make_user,
    _stub_engine,
):
    headers, uid = await make_user("data_provider", "a68create")
    session_id = await _seed_session(db_session, uid)
    resp = await client.post(
        POLICY_URL,
        json={"session_id": session_id, "mode": "deny_all", "allowed_ips": ["10.0.0.0/8"]},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text

    rows = (
        await db_session.execute(
            select(AuditLog).where(AuditLog.action == "network_policy.create")
        )
    ).scalars().all()
    assert len(rows) == 1, "成功面必须留下 network_policy.create 审计行"
    assert str(rows[0].session_id) == session_id, "审计行必须回链到该沙箱会话"


@pytest.mark.asyncio
async def test_delete_policy_persists_audit_row_with_session_link(
    client: AsyncClient,
    db_session: AsyncSession,
    make_user,
    _stub_engine,
):
    headers, uid = await make_user("data_provider", "a68del")
    session_id = await _seed_session(db_session, uid)
    created = await client.post(
        POLICY_URL,
        json={"session_id": session_id, "mode": "deny_all", "allowed_ips": ["10.0.0.0/8"]},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    policy_id = created.json()["id"]

    resp = await client.delete(f"{POLICY_URL}/{policy_id}", headers=headers)
    assert resp.status_code in (200, 204), resp.text

    rows = (
        await db_session.execute(
            select(AuditLog).where(AuditLog.action == "network_policy.delete")
        )
    ).scalars().all()
    assert len(rows) == 1, "删除面必须留下 network_policy.delete 审计行"
    assert str(rows[0].session_id) == session_id
