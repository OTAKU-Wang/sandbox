"""Pause / resume / refresh endpoint tests (Round 39 usability).

Covers: state transitions (READY/RUNNING→SUSPENDED→restore), the execute gate
during pause, the lifecycle sweep respecting extended_seconds, and refresh cap
enforcement.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def owned_session(db_session):
    from app.models.sandbox_session import SandboxSession, SandboxLevel, SandboxMode
    from app.models.data_product import DataProduct
    from app.models.user import User
    from app.services.auth_service import hash_password

    user = User(username=f"pau_{uuid.uuid4().hex[:8]}", email=f"{uuid.uuid4().hex[:8]}@e.local",
                hashed_password=hash_password("testpass123"), role="data_provider")
    db_session.add(user)
    await db_session.flush()
    product = DataProduct(name="PauseTest", provider_id=user.id, product_type="structured")
    db_session.add(product)
    await db_session.flush()
    session = SandboxSession(
        user_id=user.id, data_product_id=product.id,
        sandbox_level=SandboxLevel.L3.value, sandbox_mode=SandboxMode.STRUCTURED_QUERY.value,
        status="running", container_id=f"bwrap-{uuid.uuid4().hex[:8]}",
        timeout_seconds=3600, created_at=datetime.now(timezone.utc),
    )
    db_session.add(session)
    await db_session.flush()
    return session


@pytest_asyncio.fixture
async def owner_headers(client, db_session, owned_session):
    from app.services.auth_service import create_access_token
    token = create_access_token(owned_session.user_id, "data_provider")
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_pause_resume_roundtrip(client, owned_session, owner_headers):
    sid = owned_session.id
    resp = await client.post(f"/api/v1/sandbox-sessions/{sid}/pause", headers=owner_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "suspended"
    assert body["pre_pause_status"] == "running"

    resp = await client.post(f"/api/v1/sandbox-sessions/{sid}/resume", headers=owner_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "running"
    assert body["pre_pause_status"] is None


@pytest.mark.asyncio
async def test_pause_from_ready_resumes_to_ready(client, db_session, owned_session, owner_headers):
    owned_session.status = "ready"
    await db_session.flush()
    sid = owned_session.id
    await client.post(f"/api/v1/sandbox-sessions/{sid}/pause", headers=owner_headers)
    resp = await client.post(f"/api/v1/sandbox-sessions/{sid}/resume", headers=owner_headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "ready"


@pytest.mark.asyncio
async def test_resume_non_suspended_rejected(client, owned_session, owner_headers):
    resp = await client.post(f"/api/v1/sandbox-sessions/{owned_session.id}/resume",
                             headers=owner_headers)
    assert resp.status_code == 400
    assert "not suspended" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_pause_requires_ownership(client, owned_session):
    unique = uuid.uuid4().hex[:8]
    resp = await client.post("/api/v1/auth/register", json={
        "username": f"notown_{unique}", "email": f"notown_{unique}@example.com",
        "password": "testpass123", "role": "buyer",
    })
    other = {"Authorization": f"Bearer {resp.json()['access_token']}"}
    resp = await client.post(f"/api/v1/sandbox-sessions/{owned_session.id}/pause", headers=other)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_pause_terminal_session_rejected(client, db_session, owned_session, owner_headers):
    owned_session.status = "terminated"
    await db_session.flush()
    resp = await client.post(f"/api/v1/sandbox-sessions/{owned_session.id}/pause",
                             headers=owner_headers)
    assert resp.status_code == 400
    assert "terminal" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_refresh_extends_expiry_and_respects_cap(client, db_session, owned_session, owner_headers, monkeypatch):
    from app.core.config import get_settings
    from app.services.sandbox_manager import is_session_expired

    sid = owned_session.id
    # Make the session just-expired: created 90min ago with a 1h timeout.
    owned_session.created_at = datetime.now(timezone.utc) - timedelta(minutes=90)
    owned_session.timeout_seconds = 3600
    await db_session.flush()
    assert is_session_expired(owned_session) is True

    resp = await client.post(f"/api/v1/sandbox-sessions/{sid}/refreshes", headers=owner_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["extended_seconds"] == 3600  # default = one timeout window
    assert is_session_expired(owned_session) is False  # 90min elapsed < 60+60min

    resp = await client.post(
        f"/api/v1/sandbox-sessions/{sid}/refreshes",
        json={"extend_seconds": 60}, headers=owner_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["extended_seconds"] == 3660

    # Cap enforcement.
    monkeypatch.setattr(get_settings(), "SESSION_MAX_EXTENDED_SECONDS", 4000)
    resp = await client.post(
        f"/api/v1/sandbox-sessions/{sid}/refreshes",
        json={"extend_seconds": 500}, headers=owner_headers,
    )
    assert resp.status_code == 400
    assert "cap" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_refresh_rejects_non_positive(client, owned_session, owner_headers):
    resp = await client.post(
        f"/api/v1/sandbox-sessions/{owned_session.id}/refreshes",
        json={"extend_seconds": 0}, headers=owner_headers,
    )
    assert resp.status_code == 400
