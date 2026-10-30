"""A65main — `app/main.py` 的三处 key 识别必须与路由/`scopes.py` 同口径。

backend-26b 把「什么算 API key 凭据」收敛成 `scopes.key_credential_from_authorization()`
/ `is_key_credential()` 一个口径，但 `main.py` 里还有三处自己解析头：默认拒绝中间件
（重复头取**最后**一个 vs starlette 取**第一**个、scheme 字节比较、`X-CDS-API-Key` 不
strip）与 `/static` 的人机分流守卫（同样字节比较）。差一个字节的后果是：路由仍然把这个
请求当 key 主体认证通过，而 fail-closed 守卫看不见它是 key。
"""
from unittest.mock import AsyncMock

import pytest

import app.main as m


def _scope(path: str, headers: list[tuple[bytes, bytes]]) -> dict[str, object]:
    return {"type": "http", "path": path, "method": "POST", "headers": headers,
            "query_string": b"", "state": {}}


async def _run(inner, scope: dict[str, object]) -> tuple[bool, list[dict[str, object]]]:
    sent: list[dict[str, object]] = []

    async def send(msg):
        sent.append(msg)

    await inner(scope, AsyncMock(), send)
    return any(s.get("type") == "http.response.start" for s in sent), sent


DENY_PATH = "/api/jupyter/tickets"
KEY = b"cds_ak_0123456789abcdef"


@pytest.mark.asyncio
async def test_duplicate_authorization_headers_follow_router_first_wins():
    inner = AsyncMock()
    mw = m.Ss12KeyDefaultDenyMiddleware(inner)
    scope = _scope(DENY_PATH, [(b"authorization", b"Bearer " + KEY),
                               (b"authorization", b"Bearer garbage")])
    blocked, _ = await _run(mw.__call__, scope)
    assert blocked, (
        "重复 Authorization 头：路由/starlette 取第一个（key），守卫取最后一个 ⇒ "
        "key 主体绕过未映射前缀的默认拒绝"
    )


@pytest.mark.asyncio
async def test_scheme_case_and_whitespace_folded_headers_are_still_keys():
    inner = AsyncMock()
    mw = m.Ss12KeyDefaultDenyMiddleware(inner)
    for value in (b"bearer " + KEY, b"BeArEr  " + KEY, b"Bearer   " + KEY):
        scope = _scope(DENY_PATH, [(b"authorization", value)])
        blocked, _ = await _run(mw.__call__, scope)
        assert blocked, f"路由把 {value!r} 当 key 认证，守卫必须同样看见"


@pytest.mark.asyncio
async def test_stripped_api_key_header_is_still_a_key():
    inner = AsyncMock()
    mw = m.Ss12KeyDefaultDenyMiddleware(inner)
    scope = _scope(DENY_PATH, [(b"x-cds-api-key", b"  " + KEY + b"  ")])
    blocked, _ = await _run(mw.__call__, scope)
    assert blocked, "X-CDS-API-Key 带空白时 deps 会 strip 后认证为 key，守卫不得漏"


@pytest.mark.asyncio
async def test_human_bearer_and_covered_paths_are_not_blocked():
    inner = AsyncMock()
    mw = m.Ss12KeyDefaultDenyMiddleware(inner)
    for headers in (
        [(b"authorization", b"Bearer eyJhbGciOiJIUzI1NiJ9.human.token")],
        [(b"x-cds-api-key", b"not-a-key")],
        [],
    ):
        blocked, _ = await _run(mw.__call__, _scope(DENY_PATH, headers))
        assert not blocked, f"非 key 凭据 {headers!r} 不得被默认拒绝误伤"
    blocked, _ = await _run(mw.__call__, _scope("/api/v1/data-resources",
                                                [(b"authorization", b"Bearer " + KEY)]))
    assert not blocked, "已映射到功能组的路径本就不在默认拒绝范围"


@pytest.mark.asyncio
async def test_static_guard_blocks_keys_regardless_of_scheme_case():
    guard = getattr(m, "_KeyBlockedStatic", None)
    if guard is None:
        pytest.skip("app/static 不存在 ⇒ 该机位未装配人机分流守卫（见下一条用例的等值锁）")
    inner = AsyncMock()
    for value in (b"Bearer " + KEY, b"bearer " + KEY, b"Bearer  " + KEY):
        blocked, sent = await _run(guard(inner).__call__, _scope("/static/index.html",
                                                                 [(b"authorization", value)]))
        assert blocked, f"/static 是人机分流面，{value!r} 形态的 key 必须 403"
        start = next(s for s in sent if s.get("type") == "http.response.start")
        assert start["status"] == 403
    blocked, _ = await _run(guard(inner).__call__, _scope("/static/index.html",
                                                          [(b"authorization", b"Bearer human.jwt.token")]))
    assert not blocked, "人类 Bearer 不得被挡住"


def test_static_guard_installed_exactly_when_surface_exists():
    """上一条用件的 skip 不得变成静默绿灯：守卫的安装条件本身钉住。"""
    import pathlib

    assert (pathlib.Path(m.__file__).parent / "static").exists() == hasattr(m, "_KeyBlockedStatic")
