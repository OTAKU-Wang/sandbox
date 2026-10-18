import uuid
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_register(client: AsyncClient):
    unique = uuid.uuid4().hex[:8]
    resp = await client.post("/api/v1/auth/register", json={
        "username": f"user_{unique}",
        "email": f"user_{unique}@example.com",
        "password": "securepass123",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert "access_token" in data
    assert data["user"]["username"] == f"user_{unique}"
    assert data["user"]["role"] == "buyer"


@pytest.mark.asyncio
async def test_register_duplicate(client: AsyncClient):
    unique = uuid.uuid4().hex[:8]
    payload = {
        "username": f"dup_{unique}",
        "email": f"dup_{unique}@example.com",
        "password": "securepass123",
    }
    resp1 = await client.post("/api/v1/auth/register", json=payload)
    assert resp1.status_code == 201
    resp2 = await client.post("/api/v1/auth/register", json=payload)
    assert resp2.status_code in (400, 409, 422)  # duplicate rejected


@pytest.mark.asyncio
async def test_login(client: AsyncClient):
    unique = uuid.uuid4().hex[:8]
    await client.post("/api/v1/auth/register", json={
        "username": f"login_{unique}",
        "email": f"login_{unique}@example.com",
        "password": "mypass123",
    })
    resp = await client.post("/api/v1/auth/login", json={
        "username": f"login_{unique}",
        "password": "mypass123",
    })
    assert resp.status_code == 200
    assert "access_token" in resp.json()


@pytest.mark.asyncio
async def test_login_wrong_password(client: AsyncClient):
    unique = uuid.uuid4().hex[:8]
    await client.post("/api/v1/auth/register", json={
        "username": f"wrong_{unique}",
        "email": f"wrong_{unique}@example.com",
        "password": "correctpass",
    })
    resp = await client.post("/api/v1/auth/login", json={
        "username": f"wrong_{unique}",
        "password": "wrongpass",
    })
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_login_failure_records_audit(client: AsyncClient, db_session):
    from sqlalchemy import select

    from app.models.audit_log import AuditLog

    unique = uuid.uuid4().hex[:8]
    await client.post("/api/v1/auth/register", json={
        "username": f"audit_{unique}",
        "email": f"audit_{unique}@example.com",
        "password": "correctpass",
    })
    resp = await client.post("/api/v1/auth/login", json={
        "username": f"audit_{unique}",
        "password": "nope",
    })
    assert resp.status_code == 401

    rows = (await db_session.execute(
        select(AuditLog).where(AuditLog.action == "user.login_failed")
    )).scalars().all()
    matched = [r for r in rows if (r.detail or {}).get("username") == f"audit_{unique}"]
    assert len(matched) == 1
    assert matched[0].detail["reason"] == "invalid_credentials"


@pytest.mark.asyncio
async def test_login_failure_audit_outage_still_denies(client: AsyncClient, monkeypatch):
    from app.core import database as db_module

    def _no_connection():
        raise RuntimeError("audit write must not break the denial")

    monkeypatch.setattr(db_module, "async_session", _no_connection)
    resp = await client.post("/api/v1/auth/login", json={
        "username": "whoever",
        "password": "nope",
    })
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_get_me(client: AsyncClient, auth_headers: dict):
    resp = await client.get("/api/v1/auth/me", headers=auth_headers)
    assert resp.status_code == 200
    assert "username" in resp.json()


@pytest.mark.asyncio
async def test_get_me_no_token(client: AsyncClient):
    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code == 401 or resp.status_code == 403  # no auth header


@pytest.mark.asyncio
async def test_machine_key_header_is_read_as_a_key_credential(client: AsyncClient):
    """SS-12 数据面凭据：交付文档声明的 `X-CDS-API-Key` 必须被当作密钥凭据读进来。

    用一把形状合法但不存在的密钥：应当走 SS-12 key 路径（`API_KEY_INVALID` /
    `reason=not_found`，带 key_prefix），而不是把这个头整体忽略后退回「无凭据」的
    `HTTP_401 / Invalid token`——后者正是客户端照着自己拿到的 OpenAPI doc
    （`components.securitySchemes.CdsApiKey`，delivery_service 生成、
    test_ss15_flow_unit.py:180 钉死）调用时拿到的答案。
    """
    resp = await client.get("/api/v1/auth/me", headers={"X-CDS-API-Key": "cds_ak_" + "0" * 43})
    assert resp.status_code == 401
    body = resp.json()
    assert body["code"] == "API_KEY_INVALID", body
    assert body["detail"]["reason"] == "not_found", body
    assert body["detail"]["key_prefix"].startswith("cds_ak_")


@pytest.mark.asyncio
async def test_jwt_is_never_accepted_via_machine_key_header(client: AsyncClient, auth_headers: dict):
    """人用 JWT 只能走 `Authorization: Bearer`：换到 `X-CDS-API-Key` 上必须按无凭据处理，
    否则一个非标准头就成了绕过 Bearer 侧审计/限流口径的后门。"""
    token = auth_headers["Authorization"].split(" ", 1)[1]
    resp = await client.get("/api/v1/auth/me", headers={"X-CDS-API-Key": token})
    assert resp.status_code == 401
    assert resp.json()["code"] == "HTTP_401", resp.json()


@pytest.mark.asyncio
async def test_machine_key_header_cannot_reach_human_only_surface(client: AsyncClient, admin_headers: dict):
    """SS-12：`X-CDS-API-Key` 不得成为绕过 human-only 闸门的第二个入口。

    同一把密钥走 Bearer 会被 `require_human` 拒成 403 API_KEY_SCOPE_DENIED；换成
    文档头也必须得到同样的裁决——否则闸门看不见它，而路由侧的 `get_business_user`
    会把密钥按签发人身份解析（= 管理员权限），凭据登记面就连密钥列表本身都能读。
    """
    created = await client.post("/api/v1/api-keys", headers=admin_headers, json={
        "name": f"hdr-gate-{uuid.uuid4().hex[:8]}", "preset": "full",
        "expires_in_days": 90, "rate_limit": 120,
    })
    assert created.status_code == 201, created.text
    key = created.json()["key"]

    via_header = await client.get("/api/v1/api-keys", headers={"X-CDS-API-Key": key})
    via_bearer = await client.get("/api/v1/api-keys", headers={"Authorization": f"Bearer {key}"})
    assert via_bearer.status_code == 403, via_bearer.text
    assert via_header.status_code == 403, via_header.text
    assert via_header.json()["code"] == "API_KEY_SCOPE_DENIED", via_header.json()
    assert via_header.json()["detail"]["reason"] == "human_only"
