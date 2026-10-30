"""CR-2026-10-15 §4.10 (M3, 生产门): /auth/refresh 的 jti 会话轮换与服务端吊销。

覆盖 refresh 的完整门禁阶梯：登录签发即落 refresh_sessions 行；refresh 校验
jti 存在/属主/未吊销/未过期后**轮换**（旧行 revoked、新 jti 新会话，单次使用）；
logout 吊销当前 jti；无 jti 的存量（0032 之前）token 一律 fail-closed 拒绝
（兼容策略：宁可让 7 天窗口内的会话重登一次，不保留不可吊销的续期通道）；
MFA 确认后旧会话不得再续期（AUTH_MFA_REQUIRED 复用，无新错误码）。
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import _totp_code
from app.models.audit_log import AuditLog
from app.models.refresh_session import RefreshSession
from app.models.user import User
from app.models.user_mfa import UserMFA

PASSWORD = "testpass123"


async def _register(client: AsyncClient, prefix: str = "m3") -> dict:
    """Register a buyer and return {access, refresh, user_id, username}."""
    unique = uuid.uuid4().hex[:8]
    username = f"{prefix}_{unique}"
    resp = await client.post("/api/v1/auth/register", json={
        "username": username, "email": f"{username}@example.com", "password": PASSWORD,
    })
    assert resp.status_code == 201, resp.text
    body = resp.json()
    return {
        "access": body["access_token"],
        "refresh": body["refresh_token"],
        "user_id": body["user"]["id"],
        "username": username,
    }


def _auth(access: str) -> dict:
    return {"Authorization": f"Bearer {access}"}


def _claims(token: str) -> dict:
    from app.services.auth_service import decode_refresh_token

    return decode_refresh_token(token)


async def _session_by_jti(db: AsyncSession, jti: str) -> RefreshSession | None:
    return (await db.execute(
        select(RefreshSession).where(RefreshSession.jti == jti)
    )).scalar_one_or_none()


async def _refresh(client: AsyncClient, token: str):
    return await client.post("/api/v1/auth/refresh", json={"refresh_token": token})


# ── 签发：登录成功（含注册）即记 jti ─────────────────────────────────


@pytest.mark.asyncio
async def test_login_issuance_records_jti_session(client: AsyncClient, db_session: AsyncSession):
    """注册/登录成功返回的 refresh token 必带 jti，且有对应未吊销会话行。"""
    creds = await _register(client)
    claims = _claims(creds["refresh"])
    assert claims.get("jti"), "issued refresh token must carry a jti claim"
    row = await _session_by_jti(db_session, claims["jti"])
    assert row is not None
    assert str(row.user_id) == creds["user_id"]
    assert row.revoked is False
    assert row.expires_at is not None


@pytest.mark.asyncio
async def test_login_password_endpoint_records_session(client: AsyncClient, db_session: AsyncSession):
    """密码登录（MFA 未开启时同「MFA 通过」路径）同样落 jti 行。"""
    creds = await _register(client, prefix="m3b")
    resp = await client.post("/api/v1/auth/login", json={
        "username": creds["username"], "password": PASSWORD,
    })
    assert resp.status_code == 200, resp.text
    claims = _claims(resp.json()["refresh_token"])
    row = await _session_by_jti(db_session, claims["jti"])
    assert row is not None and str(row.user_id) == creds["user_id"]


# ── 轮换：单次使用 ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_refresh_rotates_and_old_token_becomes_dead(client: AsyncClient, db_session: AsyncSession):
    """refresh 成功 ⇒ 旧 jti 立即 revoked、新 jti 换新会话；旧 token 重放 401。"""
    creds = await _register(client, prefix="m3r")
    old_jti = _claims(creds["refresh"])["jti"]

    resp = await _refresh(client, creds["refresh"])
    assert resp.status_code == 200, resp.text
    new_token = resp.json()["refresh_token"]
    new_jti = _claims(new_token)["jti"]
    assert new_jti != old_jti

    old_row = await _session_by_jti(db_session, old_jti)
    assert old_row is not None and old_row.revoked is True
    new_row = await _session_by_jti(db_session, new_jti)
    assert new_row is not None and new_row.revoked is False

    # 重放旧 token：吊销后的 jti 不可再续期
    replay = await _refresh(client, creds["refresh"])
    assert replay.status_code == 401
    assert "revoked" in replay.json()["detail"].lower()

    # 轮换拒绝同样留痕（签名有效的拒绝必须可查）
    denied = (await db_session.execute(
        select(AuditLog).where(AuditLog.action == "user.refresh_denied")
    )).scalars().all()
    assert [d for d in denied if d.detail and d.detail.get("reason") == "revoked_jti"]

    # 新 token 仍可用（轮换不是封号）
    again = await _refresh(client, new_token)
    assert again.status_code == 200, again.text


# ── 登出：吊销当前 jti ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_logout_revokes_current_jti_and_refresh_fails(client: AsyncClient, db_session: AsyncSession):
    creds = await _register(client, prefix="m3lo")
    jti = _claims(creds["refresh"])["jti"]

    resp = await client.post("/api/v1/auth/logout", headers=_auth(creds["access"]),
                             json={"refresh_token": creds["refresh"]})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"revoked": True}
    row = await _session_by_jti(db_session, jti)
    assert row.revoked is True

    denied = await _refresh(client, creds["refresh"])
    assert denied.status_code == 401

    # 幂等：重复登出同一 token 不报错、如实报 False
    again = await client.post("/api/v1/auth/logout", headers=_auth(creds["access"]),
                              json={"refresh_token": creds["refresh"]})
    assert again.status_code == 200
    assert again.json() == {"revoked": False}


@pytest.mark.asyncio
async def test_logout_rejects_foreign_refresh_token(client: AsyncClient):
    """登出必须同主体：拿别人的 refresh token 配自己的 access token ⇒ 403。"""
    owner = await _register(client, prefix="m3own")
    attacker = await _register(client, prefix="m3atk")
    resp = await client.post("/api/v1/auth/logout", headers=_auth(attacker["access"]),
                             json={"refresh_token": owner["refresh"]})
    assert resp.status_code == 403
    # 属主会话未受影响
    assert (await _refresh(client, owner["refresh"])).status_code == 200


@pytest.mark.asyncio
async def test_logout_requires_authentication(client: AsyncClient):
    creds = await _register(client, prefix="m3an")
    resp = await client.post("/api/v1/auth/logout", json={"refresh_token": creds["refresh"]})
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_logout_empty_body_stays_compatible_and_honest(client: AsyncClient, db_session: AsyncSession):
    """空 body = 既有前端登出契约：合法、留痕，但如实报 revoked=False；

    未出示 jti 就没有任何服务端会话被吊销 —— 旧 token 仍可 refresh（这正是
    「只吊销出示的 jti」的边界，lead 拍板 2026-10-30，不许在这里假装有吊销）。
    """
    creds = await _register(client, prefix="m3eb")
    resp = await client.post("/api/v1/auth/logout", headers=_auth(creds["access"]))
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"revoked": False}

    still_alive = await _refresh(client, creds["refresh"])
    assert still_alive.status_code == 200, still_alive.text


# ── 存量无 jti token：兼容策略 = 拒绝（fail-closed，测的是策略本身）──


@pytest.mark.asyncio
async def test_legacy_token_without_jti_is_rejected(client: AsyncClient):
    """0032 之前签发的 refresh token 没有 jti。策略：一律 401，不做宽限窗。

    理由（钉死在此，改动必须过这段注释）：不可吊销正是本条目要修的缺陷；
    refresh token 最长 7 天自然收敛，代价是升级窗口内的会话重登一次。
    """
    creds = await _register(client, prefix="m3leg")
    from app.services.auth_service import create_refresh_token

    legacy = create_refresh_token(uuid.UUID(creds["user_id"]))  # 旧签名路径：无 jti
    assert "jti" not in _claims(legacy)

    resp = await _refresh(client, legacy)
    assert resp.status_code == 401
    assert "jti" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_valid_signature_unknown_jti_rejected(client: AsyncClient):
    """签名有效但表里没有这一行：没有可兑现的会话。"""
    creds = await _register(client, prefix="m3unk")
    forged = create_refresh_token_with_jti(creds["user_id"], uuid.uuid4().hex)
    resp = await _refresh(client, forged)
    assert resp.status_code == 401


def create_refresh_token_with_jti(user_id: str, jti: str) -> str:
    from app.services.auth_service import create_refresh_token

    return create_refresh_token(uuid.UUID(user_id), jti=jti)


@pytest.mark.asyncio
async def test_expired_session_row_rejected_even_if_token_unexpired(client: AsyncClient, db_session: AsyncSession):
    """会话行的 expires_at 是权威：行过期 ⇒ 拒，即便 JWT exp 还没到。"""
    creds = await _register(client, prefix="m3exp")
    jti = uuid.uuid4().hex
    db_session.add(RefreshSession(
        user_id=uuid.UUID(creds["user_id"]), jti=jti,
        issued_at=datetime.now(timezone.utc) - timedelta(days=8),
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        revoked=False,
    ))
    await db_session.flush()
    token = create_refresh_token_with_jti(creds["user_id"], jti)  # 未来 exp 的合法签名
    resp = await _refresh(client, token)
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_rotation_is_a_conditional_update_not_read_modify_write(
    client: AsyncClient, db_session: AsyncSession
):
    """单次使用的原子性钉在 DB 原语上：条件 UPDATE…WHERE revoked=false 的
    rowcount 就是竞态裁判（第二个执行者拿 0）。端点必须用这个形状，
    否则两个并发 refresh 会同时通过"读-检查-写"。"""
    from sqlalchemy import update as sa_update

    creds = await _register(client, prefix="m3race")
    row = await _session_by_jti(db_session, _claims(creds["refresh"])["jti"])

    first = await db_session.execute(
        sa_update(RefreshSession)
        .where(RefreshSession.id == row.id, RefreshSession.revoked.is_(False))
        .values(revoked=True)
    )
    await db_session.flush()
    assert first.rowcount == 1

    second = await db_session.execute(
        sa_update(RefreshSession)
        .where(RefreshSession.id == row.id, RefreshSession.revoked.is_(False))
        .values(revoked=True)
    )
    await db_session.flush()
    assert second.rowcount == 0, "第二次条件更新必须落空——这就是轮换竞态败者的 401 来源"


@pytest.mark.asyncio
async def test_inactive_user_refresh_rejected(client: AsyncClient, db_session: AsyncSession):
    """既有缓解保持：禁用账号在下一次刷新即断（is_active 门在 jti 门之前）。"""
    creds = await _register(client, prefix="m3dis")
    user = await db_session.get(User, uuid.UUID(creds["user_id"]))
    user.is_active = False
    await db_session.flush()
    resp = await _refresh(client, creds["refresh"])
    assert resp.status_code == 401


# ── MFA 重验：确认时间晚于会话签发 ⇒ 拒绝 ───────────────────────────


async def _enroll_and_confirm(client: AsyncClient, access: str) -> str:
    resp = await client.post("/api/v1/auth/mfa/enroll", headers=_auth(access))
    assert resp.status_code == 200, resp.text
    secret = resp.json()["secret"]
    resp = await client.post("/api/v1/auth/mfa/verify", json={"code": _totp_code(secret)},
                             headers=_auth(access))
    assert resp.status_code == 200, resp.text
    return secret


@pytest.mark.asyncio
async def test_refresh_rejected_when_mfa_confirmed_after_session(client: AsyncClient, db_session: AsyncSession):
    """仅密码时代签发的会话，在账号启用 MFA 后不得再免二因子续期。"""
    creds = await _register(client, prefix="m3mfa")
    secret = await _enroll_and_confirm(client, creds["access"])

    # 旧（仅密码）会话：401 + W2 语义码 AUTH_MFA_REQUIRED（复用既有码，无新增）
    resp = await _refresh(client, creds["refresh"])
    assert resp.status_code == 401, resp.text
    body = resp.json()
    assert body["code"] == "AUTH_MFA_REQUIRED"
    assert body["detail"]["reason"] == "mfa_revalidation_required"

    # 重新走 MFA 登录 ⇒ 会话签发晚于确认时间，恢复可续期
    login = await client.post("/api/v1/auth/login", json={
        "username": creds["username"], "password": PASSWORD, "mfa_code": _totp_code(secret),
    })
    assert login.status_code == 200, login.text
    refreshed = await _refresh(client, login.json()["refresh_token"])
    assert refreshed.status_code == 200, refreshed.text


@pytest.mark.asyncio
async def test_legacy_confirmed_row_falls_back_to_created_at(client: AsyncClient, db_session: AsyncSession):
    """0032 存量行 confirmed_at=NULL：回落到 created_at（确认时刻的下界）。

    这是有意的诚实口径：created_at 必然早于真实确认时刻，因此「建 MFA 行之前的
    会话」必拒；「建行之后的会话」放行（「建行后、真实确认前」属披露过的有界
    窗口，见 0032 迁移正文，enroll/verify 重跑一轮即回到精确判定）。

    时间戳全部显式钉住：SQLite 的 CURRENT_TIMESTAMP 只有秒级精度，同一秒内
    注册+登记的存量行无法确定先后，用真实墙钟会让本用例变成掷硬币。
    """
    creds = await _register(client, prefix="m3fb")   # 会话 S1 已签发
    await _enroll_and_confirm(client, creds["access"])
    mfa = (await db_session.execute(
        select(UserMFA).where(UserMFA.user_id == uuid.UUID(creds["user_id"]))
    )).scalar_one()
    s1 = await _session_by_jti(db_session, _claims(creds["refresh"])["jti"])
    mfa.confirmed_at = None                       # 模拟存量行
    mfa.created_at = s1.issued_at + timedelta(seconds=5)  # 存量登记晚于 S1 签发
    await db_session.flush()

    resp = await _refresh(client, creds["refresh"])
    assert resp.status_code == 401, resp.text
    assert resp.json()["code"] == "AUTH_MFA_REQUIRED"

    # created_at 之后签发的会话在回落口径下放行（回落不得把所有会话钉死，
    # 「过严」同样是行为契约的一部分，反向也要钉）
    jti = uuid.uuid4().hex
    db_session.add(RefreshSession(
        user_id=uuid.UUID(creds["user_id"]), jti=jti,
        issued_at=mfa.created_at + timedelta(seconds=5),
        expires_at=mfa.created_at + timedelta(days=8),
        revoked=False,
    ))
    await db_session.flush()
    ok = await _refresh(client, create_refresh_token_with_jti(creds["user_id"], jti))
    assert ok.status_code == 200, ok.text
