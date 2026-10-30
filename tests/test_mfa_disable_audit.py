"""CR-2026-10-15 §4.11 (M4, 用户 2026-10-30 拍板): MFA 关闭必须留痕。

产品决定：允许用户自行关闭第二因素（重登记 / 恢复码两条既有路径），但任何
「确认态 → 非确认态」的降级都必须写独立审计事件
``user.mfa_disabled``（detail 带 reason + high_risk=true），并可在管理端
既有审计查询面（``GET /api/v1/audit/records?action=…``）查到。

诚实边界（与铁律一致，不造假通知）：仓内没有推送/邮件基础设施，"提醒"落地为
高危审计事件 + 管理端查询面，不做虚构的通知发送路径。

拒绝路径（A14 无第二因子证据 ⇒ 401）不产生关闭事件 —— 未降级就不留关闭痕；
首次/在途登记从未确认过，同样不产生（没有东西被关闭）。
"""
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import _totp_code
from app.models.audit_log import AuditLog
from app.models.user import User
from app.models.user_mfa import UserMFA

PASSWORD = "testpass123"


async def _create_user(db_session: AsyncSession, prefix: str = "m4") -> User:
    from app.services.auth_service import hash_password

    unique = uuid.uuid4().hex[:8]
    username = f"{prefix}_{unique}"
    user = User(
        username=username,
        email=f"{username}@example.com",
        hashed_password=hash_password(PASSWORD),
        role="buyer",
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.refresh(user)
    return user


def _headers(user: User) -> dict:
    from app.services.auth_service import create_access_token

    return {"Authorization": f"Bearer {create_access_token(user.id, user.role)}"}


async def _enroll_and_confirm(client: AsyncClient, headers: dict) -> tuple[str, list[str]]:
    resp = await client.post("/api/v1/auth/mfa/enroll", headers=headers)
    assert resp.status_code == 200, resp.text
    secret = resp.json()["secret"]
    resp = await client.post("/api/v1/auth/mfa/verify", json={"code": _totp_code(secret)},
                             headers=headers)
    assert resp.status_code == 200, resp.text
    return secret, resp.json()["recovery_codes"]


async def _disable_rows(db: AsyncSession, user_id) -> list[AuditLog]:
    rows = (await db.execute(
        select(AuditLog).where(AuditLog.action == "user.mfa_disabled")
    )).scalars().all()
    return [r for r in rows if str(r.user_id) == str(user_id)]


@pytest.mark.asyncio
async def test_re_enroll_of_confirmed_writes_high_risk_disable_audit(
    client: AsyncClient, db_session: AsyncSession
):
    """合法重登记（带当前 TOTP）也是降级：mfa_disabled + reason + high_risk。"""
    user = await _create_user(db_session)
    headers = _headers(user)
    secret, _ = await _enroll_and_confirm(client, headers)

    resp = await client.post("/api/v1/auth/mfa/enroll", json={"code": _totp_code(secret)},
                             headers=headers)
    assert resp.status_code == 200, resp.text

    rows = await _disable_rows(db_session, user.id)
    assert rows, "确认态被覆盖必须留下 user.mfa_disabled 审计"
    row = rows[-1]
    assert row.detail["high_risk"] is True
    assert "reason" in row.detail and row.detail["reason"]
    assert row.detail["username"] == user.username

    mfa = (await db_session.execute(
        select(UserMFA).where(UserMFA.user_id == user.id)
    )).scalar_one()
    assert mfa.confirmed is False
    assert mfa.confirmed_at is None, "降级必须清掉确认时刻（刷新门禁随之失效）"


@pytest.mark.asyncio
async def test_recovery_code_re_enroll_writes_disable_audit(
    client: AsyncClient, db_session: AsyncSession
):
    """/mfa/recover 消费恢复码后进入未确认窗口 —— 同样按关闭留痕。"""
    user = await _create_user(db_session, prefix="m4r")
    headers = _headers(user)
    _secret, codes = await _enroll_and_confirm(client, headers)

    resp = await client.post("/api/v1/auth/mfa/recover", json={"recovery_code": codes[0]},
                             headers=headers)
    assert resp.status_code == 200, resp.text

    rows = await _disable_rows(db_session, user.id)
    assert rows
    assert rows[-1].detail["high_risk"] is True
    assert "recovery" in rows[-1].detail["reason"]


@pytest.mark.asyncio
async def test_denied_re_enroll_writes_no_disable_audit(
    client: AsyncClient, db_session: AsyncSession
):
    """A14 拒绝（无第二因子证据）没有发生降级 —— 不得留下关闭痕。"""
    user = await _create_user(db_session, prefix="m4d")
    headers = _headers(user)
    await _enroll_and_confirm(client, headers)

    resp = await client.post("/api/v1/auth/mfa/enroll", json={"code": "000000"}, headers=headers)
    assert resp.status_code == 401

    assert await _disable_rows(db_session, user.id) == []
    mfa = (await db_session.execute(
        select(UserMFA).where(UserMFA.user_id == user.id)
    )).scalar_one()
    assert mfa.confirmed is True
    assert mfa.confirmed_at is not None


@pytest.mark.asyncio
async def test_first_and_inflight_enroll_write_no_disable_audit(
    client: AsyncClient, db_session: AsyncSession
):
    """从未确认过的登记（首次/在途）没有"关闭"语义，不产关闭事件。"""
    user = await _create_user(db_session, prefix="m4f")
    headers = _headers(user)

    first = await client.post("/api/v1/auth/mfa/enroll", headers=headers)
    assert first.status_code == 200
    second = await client.post("/api/v1/auth/mfa/enroll", headers=headers)  # 在途替换
    assert second.status_code == 200

    assert await _disable_rows(db_session, user.id) == []


@pytest.mark.asyncio
async def test_admin_query_surface_sees_disable_event(client: AsyncClient, db_session: AsyncSession, admin_headers: dict):
    """管理端既有审计查询面（action 精确过滤）能查到这条高危事件。"""
    user = await _create_user(db_session, prefix="m4q")
    headers = _headers(user)
    secret, _ = await _enroll_and_confirm(client, headers)
    resp = await client.post("/api/v1/auth/mfa/enroll", json={"code": _totp_code(secret)},
                             headers=headers)
    assert resp.status_code == 200, resp.text

    resp = await client.get("/api/v1/audit/records", params={"action": "user.mfa_disabled"},
                            headers=admin_headers)
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    mine = [i for i in items if i["user_id"] == str(user.id)]
    assert mine, "管理端按 action 过滤必须能查到 user.mfa_disabled"
    assert mine[0]["outcome"] == "success"  # 事件本身写入成功；风险由 detail.high_risk 表达

    # 关键字口径同样命中（4-1 操作日志屏的搜索路径，含用户名）
    resp = await client.get("/api/v1/audit/records",
                            params={"keyword": "mfa_disabled"}, headers=admin_headers)
    assert resp.status_code == 200, resp.text
    assert [i for i in resp.json()["items"] if i["user_id"] == str(user.id)]
