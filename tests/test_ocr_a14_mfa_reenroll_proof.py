"""A14 (ocr CR 2026-10-18): MFA 不得被单因素静默关闭。

修复前：`POST /auth/mfa/enroll` 只需 get_current_user，进去就把已确认的
confirmed 置 False 并清空恢复码，而登录门禁只认 confirmed —— 拿到密码者
调一次 enroll 即把账号降级回纯密码登录。修复：对已确认的注册发起重新
登记，必须当场证明第二因子持有（当前 TOTP 或未消费的恢复码）。
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
from app.services.auth_service import create_access_token, hash_password

pytestmark = pytest.mark.asyncio

PASSWORD = "testpass123"


async def _create_user(db: AsyncSession) -> User:
    unique = uuid.uuid4().hex[:8]
    user = User(
        username=f"a14_{unique}", email=f"a14_{unique}@example.com",
        hashed_password=hash_password(PASSWORD), role="buyer",
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return user


def _headers(user: User) -> dict:
    return {"Authorization": f"Bearer {create_access_token(user.id, user.role)}"}


async def _enroll_and_confirm(client: AsyncClient, headers: dict):
    resp = await client.post("/api/v1/auth/mfa/enroll", headers=headers)
    assert resp.status_code == 200, resp.text
    secret = resp.json()["secret"]
    resp = await client.post("/api/v1/auth/mfa/verify", json={"code": _totp_code(secret)}, headers=headers)
    assert resp.status_code == 200, resp.text
    return secret, resp.json()["recovery_codes"]


class TestReenrollRequiresSecondFactor:
    async def test_enroll_on_confirmed_without_proof_denied(self, client: AsyncClient, db_session: AsyncSession):
        """密码态攻击者（无第二因子）不能借 enroll 把账号降级回单因素。"""
        user = await _create_user(db_session)
        headers = _headers(user)
        await _enroll_and_confirm(client, headers)

        resp = await client.post("/api/v1/auth/mfa/enroll", headers=headers)
        assert resp.status_code == 401, resp.text
        assert resp.json()["code"] == "AUTH_MFA_REQUIRED"

        # 降级未发生：confirmed 仍为真，登录依旧强制第二因子
        row = (await db_session.execute(
            select(UserMFA).where(UserMFA.user_id == user.id)
        )).scalar_one()
        assert row.confirmed is True
        assert row.recovery_codes_encrypted

        login = await client.post("/api/v1/auth/login", json={
            "username": user.username, "password": PASSWORD,
        })
        assert login.status_code == 401
        assert login.json()["code"] == "AUTH_MFA_REQUIRED"

    async def test_denied_enroll_is_audited(self, client: AsyncClient, db_session: AsyncSession):
        """拒绝路径必须留痕（log_standalone 约定）。"""
        user = await _create_user(db_session)
        headers = _headers(user)
        await _enroll_and_confirm(client, headers)

        resp = await client.post("/api/v1/auth/mfa/enroll", json={"code": "000000"}, headers=headers)
        assert resp.status_code == 401

        rows = (await db_session.execute(
            select(AuditLog).where(AuditLog.action == "user.mfa_enroll_denied")
        )).scalars().all()
        assert [r for r in rows if str(r.user_id) == str(user.id)]

    async def test_enroll_with_valid_totp_still_allowed(self, client: AsyncClient, db_session: AsyncSession):
        """合法用户带当前 TOTP 仍可换设备重登记（收紧不误伤）。"""
        user = await _create_user(db_session)
        headers = _headers(user)
        secret, _codes = await _enroll_and_confirm(client, headers)

        resp = await client.post("/api/v1/auth/mfa/enroll", json={"code": _totp_code(secret)}, headers=headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["confirmed"] is False

        row = (await db_session.execute(
            select(UserMFA).where(UserMFA.user_id == user.id)
        )).scalar_one()
        assert row.confirmed is False

    async def test_enroll_with_recovery_code_still_allowed(self, client: AsyncClient, db_session: AsyncSession):
        """丢了验证器但有恢复码的用户，同样能凭恢复码重登记（第二因子持有）。"""
        user = await _create_user(db_session)
        headers = _headers(user)
        _secret, codes = await _enroll_and_confirm(client, headers)

        resp = await client.post("/api/v1/auth/mfa/enroll", json={"code": codes[0]}, headers=headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["confirmed"] is False

    async def test_first_enroll_needs_no_body(self, client: AsyncClient, db_session: AsyncSession):
        """在途/首次登记（无 confirmed）保持无请求体的旧契约 —— 不锁死用户。"""
        user = await _create_user(db_session)
        headers = _headers(user)

        resp = await client.post("/api/v1/auth/mfa/enroll", headers=headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["confirmed"] is False

        # 未确认的在途登记同样不要求第二因子（替换半途注册不构成降级）
        again = await client.post("/api/v1/auth/mfa/enroll", headers=headers)
        assert again.status_code == 200, again.text
