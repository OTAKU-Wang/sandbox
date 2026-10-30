"""MAJOR-3 (cr-2026-10-15 §4.5) — MPC 轮换/销毁的高危留痕必须真实入审计。

前端轮换/销毁弹窗采集的 ``reason``/``ticket_id`` 曾被后端直接丢弃
（``app/api/mpc.py`` rotate/destroy 无 body 参数），制造了 P-GAP-014 高危
纪律"已生效"的假象。契约形状参考 PZ-09/PZ-10 角色 PATCH
（``app/schemas/high_risk_operation.py``），但对这两个既有端点为**可选**
body —— 不带 body 的旧调用必须继续 200（wire 兼容），只是审计会显式
标 ``reason_missing``/``ticket_id_missing``：留痕缺失可查，不再假象。
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.models.audit_log import AuditLog


async def _mk_key(client, admin_headers) -> str:
    split = await client.post("/api/v1/mpc/split", json={
        "secret_hex": "aabb" * 16, "threshold": 2, "total_shares": 3,
    }, headers=admin_headers)
    assert split.status_code == 200, split.text
    return split.json()[0]["key_id"]


async def _audit_rows(db_session, action: str, resource_id: str) -> list[AuditLog]:
    rows = (await db_session.execute(
        select(AuditLog).where(AuditLog.action == action)
    )).scalars().all()
    return [r for r in rows if r.resource_id == resource_id]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("verb", "action"),
    [("rotate", "mpc.key.rotate"), ("destroy", "mpc.key.destroy")],
)
async def test_trail_fields_land_in_audit(
    client: AsyncClient, make_user, db_session, verb: str, action: str,
):
    """带 reason/ticket_id 的高危操作：两者逐字进审计 detail。"""
    admin_headers, admin_id = await make_user("admin", f"m3_{verb}_trail")
    key_id = await _mk_key(client, admin_headers)

    resp = await client.post(
        f"/api/v1/mpc/keys/{key_id}/{verb}",
        json={"reason": "quarterly key ceremony", "ticket_id": "CHG-2026-1042"},
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text

    matched = await _audit_rows(db_session, action, key_id)
    assert len(matched) == 1, f"expected exactly one {action} row, got {len(matched)}"
    detail = matched[0].detail or {}
    assert detail.get("reason") == "quarterly key ceremony"
    assert detail.get("ticket_id") == "CHG-2026-1042"
    assert "reason_missing" not in detail and "ticket_id_missing" not in detail
    assert str(matched[0].user_id) == str(admin_id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("verb", "action"),
    [("rotate", "mpc.key.rotate"), ("destroy", "mpc.key.destroy")],
)
async def test_bodyless_call_still_200_but_trail_gap_is_disclosed(
    client: AsyncClient, make_user, db_session, verb: str, action: str,
):
    """旧调用方（无 body）不得被本次修改打破 —— 但仍要 200，且审计必须
    显式承认留痕缺失，而不是留下一条看起来合规的 detail。"""
    admin_headers, _ = await make_user("admin", f"m3_{verb}_legacy")
    key_id = await _mk_key(client, admin_headers)

    resp = await client.post(f"/api/v1/mpc/keys/{key_id}/{verb}", headers=admin_headers)
    assert resp.status_code == 200, resp.text

    matched = await _audit_rows(db_session, action, key_id)
    assert len(matched) == 1
    detail = matched[0].detail or {}
    assert detail.get("reason_missing") is True
    assert detail.get("ticket_id_missing") is True
    assert "reason" not in detail and "ticket_id" not in detail


@pytest.mark.asyncio
async def test_only_ticket_is_also_a_disclosed_gap(client: AsyncClient, make_user, db_session):
    admin_headers, _ = await make_user("admin", "m3_destroy_ticket_only")
    key_id = await _mk_key(client, admin_headers)

    resp = await client.post(
        f"/api/v1/mpc/keys/{key_id}/destroy",
        json={"ticket_id": "CHG-7"},
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text

    matched = await _audit_rows(db_session, "mpc.key.destroy", key_id)
    assert len(matched) == 1
    detail = matched[0].detail or {}
    assert detail.get("ticket_id") == "CHG-7"
    assert detail.get("reason_missing") is True


@pytest.mark.asyncio
async def test_short_reason_is_rejected_at_the_edge(client: AsyncClient, make_user):
    """reason 给了就必须达到 PZ-09 同一形状下限（>=8），422 而不是静默丢弃。"""
    admin_headers, _ = await make_user("admin", "m3_rotate_short_reason")
    key_id = await _mk_key(client, admin_headers)

    resp = await client.post(
        f"/api/v1/mpc/keys/{key_id}/rotate",
        json={"reason": "oops"},
        headers=admin_headers,
    )
    assert resp.status_code == 422
