"""A30: 游标分页必须携带权限谓词 —— 非特权角色在 cursor 模式下也只能看见自己的会话。

W8 keyset 分支以前只写了 `paginate_keyset(db, SandboxSession, cursor=, limit=)`，
把上面认真构造的 `user_id` / `status` 谓词整条丢掉（`where` 是选传 ⇒ 等于全表）；
又因为 `cursor=""` 也满足 `is not None`，`GET /api/v1/sandbox-sessions?cursor=`
直接就是「全平台会话第一页」，靠 X-Next-Cursor 逐页枚举整站。

历史盲区：tests/test_pagination.py 的游标断言全部跑在 role=admin 上
（`_can_view_all_sessions` 为真），所以那条隔离断言在旧实现下不可能失败。

本文件按「返回内容的身份」而不是状态码来钉：
- 业务角色（data_provider / buyer）游标模式：body 里每条的 user_id 必须是当前用户，
  并且自己的会话一条都不能少（反向用例 —— 防止把接口修成「永远空」）；
- 特权角色（operator / regulator / admin）游标模式：仍能看到全平台会话；
- status 过滤在游标模式下同样生效，且与租户谓词同时成立；
- 游标模式与 offset 模式给出的身份集合必须完全一致（两条形同一致的隔离线）。
"""
import uuid
from collections.abc import Mapping

import pytest

from app.models.sandbox_session import SandboxSession, SessionStatus
from app.models.user import User

BUSINESS_ROLES = ["data_provider", "buyer"]
PRIVILEGED_ROLES = ["operator", "regulator", "admin"]


async def _mk_user(db_session, role: str) -> User:
    unique = uuid.uuid4().hex[:8]
    username = f"a30_{role}_{unique}".replace("-", "_")
    user = User(
        username=username,
        email=f"{username}@example.com",
        hashed_password="x",
        role=role,
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.refresh(user)
    return user


def _headers(user: User) -> dict:
    from app.services.auth_service import create_access_token

    return {"Authorization": f"Bearer {create_access_token(user.id, user.role)}"}


async def _tenant(
    db_session, role: str, sessions: Mapping[str, int]
) -> tuple[dict, str, list[SandboxSession]]:
    """One tenant: a user of `role` plus `sessions[status]` sessions of that status."""
    user = await _mk_user(db_session, role)
    created: list[SandboxSession] = []
    for status, count in sessions.items():
        rows = [
            SandboxSession(
                id=uuid.uuid4(),
                user_id=user.id,
                data_product_id=uuid.uuid4(),
                sandbox_level="L3",
                sandbox_mode="structured_query",
                status=status,
                container_id=f"l3-{uuid.uuid4().hex[:12]}",
                session_key_id=f"key-{uuid.uuid4().hex[:12]}",
            )
            for _ in range(count)
        ]
        db_session.add_all(rows)
        created.extend(rows)
    await db_session.flush()
    return _headers(user), str(user.id), created


@pytest.mark.asyncio
@pytest.mark.parametrize("role", BUSINESS_ROLES)
async def test_cursor_page_returns_only_own_identity(client, db_session, role):
    """游标首页：返回内容必须全部属于调用者，且自己的会话一条不少。"""
    _, _, victim_rows = await _tenant(db_session, "buyer", {SessionStatus.RUNNING.value: 3})
    headers, caller_id, own_rows = await _tenant(
        db_session, role, {SessionStatus.RUNNING.value: 2}
    )

    resp = await client.get(
        "/api/v1/sandbox-sessions", params={"limit": 50, "cursor": ""}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]

    # 身份钉死：不是「能 200」就算过 —— 每一条的 user_id 必须是当前用户
    assert items, "own sessions must stay visible (never widen the fix into an empty list)"
    assert {item["user_id"] for item in items} == {caller_id}
    assert {item["id"] for item in items} == {str(s.id) for s in own_rows}

    # 反向：他人会话连同其全字段（container_id / session_key_id）都不得出现在 body 里
    victim_ids = {str(s.id) for s in victim_rows}
    assert {item["id"] for item in items}.isdisjoint(victim_ids)
    body_text = resp.text
    for row in victim_rows:
        assert str(row.id) not in body_text
        assert row.container_id not in body_text
        assert row.session_key_id not in body_text


@pytest.mark.asyncio
async def test_cursor_traversal_stays_inside_tenant(client, db_session):
    """逐页走完全部游标页：并集恰好等于自己的会话集（不多一页、不漏一条）。"""
    await _tenant(db_session, "buyer", {SessionStatus.RUNNING.value: 3})
    headers, caller_id, own_rows = await _tenant(
        db_session, "data_provider", {SessionStatus.RUNNING.value: 3}
    )
    own_ids = {str(s.id) for s in own_rows}

    seen: list[str] = []
    pages = 0
    cursor = ""
    while True:
        resp = await client.get(
            "/api/v1/sandbox-sessions", params={"limit": 1, "cursor": cursor}, headers=headers
        )
        assert resp.status_code == 200, resp.text
        items = resp.json()["items"]
        for item in items:
            assert item["user_id"] == caller_id, f"page {pages} left the tenant: {item['id']}"
        seen.extend(item["id"] for item in items)
        cursor = resp.headers.get("X-Next-Cursor")
        pages += 1
        if pages > 12:
            pytest.fail("cursor traversal did not terminate")
        if not cursor:
            break

    assert pages == 3, pages
    assert len(seen) == len(own_ids)
    assert set(seen) == own_ids


@pytest.mark.asyncio
async def test_cursor_mode_status_filter_is_scoped_too(client, db_session):
    """status 谓词与租户谓词必须同时生效（游标分支以前两个都丢）。"""
    await _tenant(
        db_session,
        "buyer",
        {SessionStatus.RUNNING.value: 2, SessionStatus.SUSPENDED.value: 2},
    )
    headers, caller_id, own_rows = await _tenant(
        db_session, "data_provider", {SessionStatus.SUSPENDED.value: 1}
    )

    resp = await client.get(
        "/api/v1/sandbox-sessions",
        params={"limit": 50, "cursor": "", "status": SessionStatus.SUSPENDED.value},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert items, "the caller's own suspended session must still come back"
    assert {item["user_id"] for item in items} == {caller_id}
    assert {item["status"] for item in items} == {SessionStatus.SUSPENDED.value}
    assert {item["id"] for item in items} == {str(s.id) for s in own_rows}


@pytest.mark.asyncio
@pytest.mark.parametrize("role", PRIVILEGED_ROLES)
async def test_cursor_mode_privileged_roles_still_see_every_tenant(client, db_session, role):
    """收紧不能过头：operator / regulator / admin 游标模式仍能看到全平台会话。"""
    _, victim_id, victim_rows = await _tenant(db_session, "buyer", {SessionStatus.RUNNING.value: 3})
    headers, caller_id, own_rows = await _tenant(
        db_session, role, {SessionStatus.RUNNING.value: 1}
    )

    resp = await client.get(
        "/api/v1/sandbox-sessions", params={"limit": 50, "cursor": ""}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    got = {item["id"] for item in items}
    expect = {str(s.id) for s in own_rows + victim_rows}
    assert expect <= got, "privileged roles must still enumerate the platform"
    assert {caller_id, victim_id} <= {item["user_id"] for item in items}


@pytest.mark.asyncio
async def test_cursor_mode_matches_offset_mode_identity_set(client, db_session):
    """对照组：游标模式与 offset 模式必须给出同一个身份集合。"""
    await _tenant(db_session, "buyer", {SessionStatus.RUNNING.value: 3})
    headers, _, own_rows = await _tenant(db_session, "data_provider", {SessionStatus.RUNNING.value: 2})

    offset = await client.get("/api/v1/sandbox-sessions", params={"limit": 50}, headers=headers)
    cursor = await client.get(
        "/api/v1/sandbox-sessions", params={"limit": 50, "cursor": ""}, headers=headers
    )
    assert offset.status_code == 200 and cursor.status_code == 200
    assert {i["id"] for i in offset.json()["items"]} == {
        i["id"] for i in cursor.json()["items"]
    } == {str(s.id) for s in own_rows}
