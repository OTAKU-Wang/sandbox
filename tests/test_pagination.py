"""W8: keyset pagination — no duplicates, no misses, header contract.

Uses the real /api/v1/sandbox-sessions endpoint (cursor opt-in, offset path
unchanged) plus the connector cap. Traversal tolerance for mid-page deletes
is covered by the keyset property: pages are anchored on (created_at, id).
"""
import uuid

import pytest
import pytest_asyncio

from app.models.sandbox_session import SandboxSession, SessionStatus
from app.models.user import User


@pytest_asyncio.fixture
async def pager_user(db_session):
    unique = uuid.uuid4().hex[:8]
    user = User(
        username=f"pager_{unique}",
        email=f"pager_{unique}@example.com",
        hashed_password="x",
        role="admin",
    )
    db_session.add(user)
    await db_session.flush()
    return user


@pytest_asyncio.fixture
async def seeded_sessions(db_session, pager_user):
    """250 sessions for one user (second-precision server defaults)."""
    sessions = [
        SandboxSession(
            id=uuid.uuid4(),
            user_id=pager_user.id,
            data_product_id=uuid.uuid4(),
            sandbox_level="L3",
            sandbox_mode="structured_query",
            status=SessionStatus.RUNNING.value,
        )
        for _ in range(250)
    ]
    db_session.add_all(sessions)
    await db_session.flush()
    return sessions


def _make_admin_headers(db_session, pager_user):
    from app.services.auth_service import create_access_token

    return {"Authorization": f"Bearer {create_access_token(pager_user.id, pager_user.role)}"}


@pytest.mark.asyncio
async def test_cursor_traversal_no_dup_no_miss(client, db_session, seeded_sessions, pager_user):
    headers = _make_admin_headers(db_session, pager_user)
    seen: list[str] = []
    cursor = ""  # empty string = keyset page 1; absent = legacy offset mode
    pages = 0
    while True:
        resp = await client.get(
            "/api/v1/sandbox-sessions", params={"limit": 100, "cursor": cursor}, headers=headers
        )
        assert resp.status_code == 200
        body = resp.json()
        seen.extend(item["id"] for item in body["items"])
        pages += 1
        cursor = resp.headers.get("X-Next-Cursor")
        if pages > 10:
            pytest.fail("cursor traversal did not terminate")
        if not cursor:
            break

    assert pages == 3
    assert len(seen) == 250
    assert len(set(seen)) == 250
    all_ids = {str(s.id) for s in seeded_sessions}
    assert set(seen) == all_ids


@pytest.mark.asyncio
async def test_offset_mode_unchanged(client, db_session, seeded_sessions, pager_user):
    headers = _make_admin_headers(db_session, pager_user)
    resp = await client.get("/api/v1/sandbox-sessions", params={"skip": 0, "limit": 20}, headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 250
    assert body["page"] == 1
    assert len(body["items"]) == 20
    assert "X-Next-Cursor" not in resp.headers


@pytest.mark.asyncio
async def test_limit_upper_bound_rejects_501(client, db_session, seeded_sessions, pager_user):
    headers = _make_admin_headers(db_session, pager_user)
    resp = await client.get("/api/v1/sandbox-sessions", params={"limit": 501}, headers=headers)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_invalid_cursor_rejected(client, db_session, seeded_sessions, pager_user):
    headers = _make_admin_headers(db_session, pager_user)
    resp = await client.get(
        "/api/v1/sandbox-sessions",
        params={"limit": 10, "cursor": "not-a-cursor"},
        headers=headers,
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_cursor_survives_mid_page_deletes(client, db_session, seeded_sessions, pager_user):
    headers = _make_admin_headers(db_session, pager_user)
    resp = await client.get("/api/v1/sandbox-sessions", params={"limit": 100, "cursor": ""}, headers=headers)
    cursor = resp.headers["X-Next-Cursor"]

    for s in seeded_sessions[:50]:
        await db_session.delete(s)
    await db_session.flush()

    resp2 = await client.get("/api/v1/sandbox-sessions", params={"limit": 100, "cursor": cursor}, headers=headers)
    assert resp2.status_code == 200
    page2_ids = {item["id"] for item in resp2.json()["items"]}
    page1_ids = {item["id"] for item in resp.json()["items"]}
    assert page1_ids.isdisjoint(page2_ids)


def test_sdk_iter_sessions_autopages():
    """iter_sessions walks every page via X-Next-Cursor (MockTransport)."""
    import httpx
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sdk"))
    from cds_sdk import CDSClient

    all_items = [{"id": f"s-{i:03d}"} for i in range(250)]

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        cursor = params.get("cursor")
        limit = int(params.get("limit", "100"))
        start = int(cursor) if cursor else 0
        page = all_items[start:start + limit]
        headers = {}
        if start + limit < len(all_items):
            headers["X-Next-Cursor"] = str(start + limit)
        return httpx.Response(200, json={"items": page}, headers=headers)

    with CDSClient("http://test", token="tok", transport=httpx.MockTransport(handler)) as cds:
        items = list(cds.iter_sessions(page_size=60))

    assert len(items) == 250
    assert len({i["id"] for i in items}) == 250
    assert [i["id"] for i in items] == [i["id"] for i in all_items]
