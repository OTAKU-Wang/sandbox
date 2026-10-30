"""A67 — 中间件/守卫与 deps 的凭据优先级必须一致（自我伤害型 403 关死）。

旧实现（main.py 中间件折叠 + `key_unmapped_deny` 回退）把「Authorization 不是
key」直接当成「改用 X-CDS-API-Key 判 key」；而 `deps.get_auth_subject` 的口径
是 bearer token 出现即凭据（人 JWT 走 JWT 路，绝不回退头凭据）。于是
「人 JWT + 任意残留 cds_ak_ 头」会在未映射前缀被守卫当 key 主体 403——路由本
会按人处理。修复后守卫用 `scopes.effective_key_credential` 与 deps 同优先级：
bearer token（任何 token）说话；头凭据只在 bearer 缺失/非 bearer 形态时说话。
"""
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient

import app.main as m
from app.core.scopes import key_unmapped_deny

DENY_PATH = "/api/jupyter/tickets"  # A65main 同款：无功能组覆盖的未映射前缀
KEY = b"cds_ak_0123456789abcdef"
JWT = b"Bearer eyJhbGciOiJIUzI1NiJ9.human.token"


def _scope(path: str, headers: list[tuple[bytes, bytes]]) -> dict[str, object]:
    return {"type": "http", "path": path, "method": "GET", "headers": headers,
            "query_string": b"", "state": {}}


async def _blocked(mw, scope) -> bool:
    sent: list[dict[str, object]] = []

    async def send(msg):
        sent.append(msg)

    await mw(scope, AsyncMock(), send)
    return any(s.get("type") == "http.response.start" for s in sent)


@pytest.mark.asyncio
async def test_human_jwt_with_residual_key_header_is_not_deny_as_key():
    """A67 核心：人 JWT + 残留 key 头 = 人主体，未映射前缀不得按 key 拒。"""
    mw = m.Ss12KeyDefaultDenyMiddleware(AsyncMock())
    assert not await _blocked(mw, _scope(DENY_PATH, [
        (b"authorization", JWT), (b"x-cds-api-key", KEY),
    ]))


@pytest.mark.asyncio
async def test_pure_key_subjects_keep_failing_closed_on_unmapped_paths():
    """修复只关误伤，不得放松默认拒绝：纯 key 主体照旧 403。"""
    mw = m.Ss12KeyDefaultDenyMiddleware(AsyncMock())
    # 无 Authorization，只有头凭据
    assert await _blocked(mw, _scope(DENY_PATH, [(b"x-cds-api-key", KEY)]))
    # bearer 里就是 key（大小写/空白变形同 A65 口径）
    assert await _blocked(mw, _scope(DENY_PATH, [(b"authorization", b"bearer " + KEY)]))
    # bearer key + 另一个 key 头
    assert await _blocked(mw, _scope(DENY_PATH, [
        (b"authorization", b"Bearer " + KEY), (b"x-cds-api-key", KEY),
    ]))


@pytest.mark.asyncio
async def test_non_bearer_authorization_falls_back_to_header_key_like_deps():
    """deps 在 Authorization 非 bearer（HTTPBearer→None）时回退头凭据；守卫同口径。"""
    mw = m.Ss12KeyDefaultDenyMiddleware(AsyncMock())
    assert await _blocked(mw, _scope(DENY_PATH, [
        (b"authorization", b"Basic dXNlcjpwYXNz"), (b"x-cds-api-key", KEY),
    ]))
    # 空 bearer（deps: credentials 为空串 → machine_key_from_request）
    assert await _blocked(mw, _scope(DENY_PATH, [
        (b"authorization", b"Bearer "), (b"x-cds-api-key", KEY),
    ]))


def test_predicate_priority_matches_router():
    """纯谓词层的优先级钉子（与中间件共用 `effective_key_credential`）。"""
    auth = "eyJhbGciOiJIUzI1NiJ9.human.token"
    assert key_unmapped_deny(DENY_PATH, "Bearer " + auth, api_key_header="cds_ak_x") is False
    assert key_unmapped_deny(DENY_PATH, "", api_key_header="cds_ak_x") is True
    assert key_unmapped_deny(DENY_PATH, "Basic xyz", api_key_header="cds_ak_x") is True
    assert key_unmapped_deny(DENY_PATH, "Bearer cds_ak_x", api_key_header="") is True


@pytest.mark.asyncio
async def test_end_to_end_human_session_with_stale_key_header(client: AsyncClient, auth_headers: dict):
    """端到端：登录态 + 残留 X-CDS-API-Key 照常按人主体取数，不再 403。"""
    resp = await client.get(
        "/api/v1/data-resources/",
        headers={**auth_headers, "X-CDS-API-Key": "cds_ak_residual_stale_key"},
    )
    assert resp.status_code == 200, resp.text
    assert isinstance(resp.json(), list)
