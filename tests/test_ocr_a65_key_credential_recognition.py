"""OCR A65 — 默认拒绝对 key 的识别比路由器窄一个量级（大小写不同的 `Bearer`）。

台账（docs/ocr-review-2026-10-18/ocr-verified-findings.md ### A65）：SS-12 rule 1 的
默认拒绝谓词按字节比较 `"Bearer cds_ak_"`，而认证入口把 scheme 小写后才比较
（`fastapi.security.http.HTTPBearer`: `scheme.lower() != "bearer"`），`deps.py` 再用
`credential.startswith("cds_ak_")` 进 key 路径 ⇒ `Authorization: bearer cds_ak_<valid>`
跳过默认拒绝，落在一个既未映射、又接受 key 的面上（jupyter 工单等无 `dependencies=`
的挂载路由，key 以签发人身份行动）。

修法口径：谓词与认证入口共用同一套取值口径（scheme 大小写不敏感 + 去空白）。
重复 `Authorization` 头那半（中间件取最后一个 vs `Headers.get` 取第一个）落在
app/main.py，见证据文件的 NEEDS-OWNER。
"""
import uuid

import pytest
from fastapi.security.http import get_authorization_scheme_param
from httpx import AsyncClient

from app.core import scopes as ss12_scopes

KEY_TAIL = "z" * 43
KEY = "cds_ak_" + KEY_TAIL
UNMAPPED = "/api/v1/not-a-real-surface"
MAPPED = "/api/v1/data-products"
HUMAN_ONLY = "/api/v1/api-keys"

HEADER_VARIANTS = [
    f"Bearer {KEY}",
    f"bearer {KEY}",
    f"BEARER {KEY}",
    f"BeArEr {KEY}",
    f"Bearer  {KEY}",
    f"Bearer {KEY} ",
]

NON_KEY_HEADERS = [
    "",
    "Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJodW1hbiJ9.sig",
    f"Token {KEY}",
    f"Basic {KEY}",
    KEY,
]


def _router_accepts_as_key(header: str) -> bool:
    """How the auth entry point itself reads one Authorization header."""
    scheme, param = get_authorization_scheme_param(header)
    return bool(scheme) and scheme.lower() == "bearer" and param.startswith("cds_ak_")


@pytest.fixture(autouse=True)
def _clean_ss12_state():
    ss12_scopes.reset_auth_counters()
    ss12_scopes.reset_rate_limits()
    yield
    ss12_scopes.reset_auth_counters()
    ss12_scopes.reset_rate_limits()


@pytest.mark.parametrize("header", HEADER_VARIANTS)
def test_a65_every_bearer_casing_is_seen_as_a_key(header: str):
    assert ss12_scopes.key_unmapped_deny(UNMAPPED, header) is True, (
        f"default-deny is blind to {header!r} while HTTPBearer+deps authenticate "
        "it as a cds_ak_ key subject"
    )


@pytest.mark.parametrize("header", HEADER_VARIANTS)
def test_a65_never_narrows_below_the_auth_entry_point(header: str):
    """谓词必须至少覆盖认证入口认作 key 的那些头（只多不少 = fail-closed）。"""
    if _router_accepts_as_key(header):
        assert ss12_scopes.key_unmapped_deny(UNMAPPED, header) is True


@pytest.mark.parametrize("header", NON_KEY_HEADERS)
def test_a65_non_key_credentials_are_left_alone(header: str):
    """不是 key 的凭据一律不接管：人类 JWT / 匿名仍拿框架原本的 401/404/405。"""
    assert _router_accepts_as_key(header) is False
    assert ss12_scopes.key_unmapped_deny(UNMAPPED, header) is False


@pytest.mark.parametrize("header", HEADER_VARIANTS)
def test_a65_mapped_and_human_only_paths_keep_answering(header: str):
    """只放宽「这是不是 key」，不放宽拒绝范围：已映射前缀仍交给 scope 门，human-only 仍由 require_human 回答。"""
    assert ss12_scopes.key_unmapped_deny(MAPPED, header) is False
    assert ss12_scopes.key_unmapped_deny(HUMAN_ONLY, header) is False


def test_a65_x_cds_api_key_transport():
    """X-CDS-API-Key 走 deps 的 strip 口径：前导空白不得变成绕过。"""
    assert ss12_scopes.key_unmapped_deny(UNMAPPED, "", f"  {KEY}") is True
    assert ss12_scopes.key_unmapped_deny(UNMAPPED, "", KEY) is True
    assert ss12_scopes.key_unmapped_deny(MAPPED, "", KEY) is False
    assert ss12_scopes.key_unmapped_deny(UNMAPPED, "", "not-a-key") is False


def test_a65_shared_helpers_are_adoptable_by_the_static_guard():
    """`_KeyBlockedStatic`（app/main.py）需要的正是这两个共用取值口。"""
    assert ss12_scopes.is_key_credential(KEY) is True
    assert ss12_scopes.is_key_credential(f" {KEY}") is True
    assert ss12_scopes.is_key_credential("Bearer jwt") is False
    assert ss12_scopes.is_key_credential(None) is False
    assert ss12_scopes.key_credential_from_authorization(f"bearer {KEY}") == KEY
    assert ss12_scopes.key_credential_from_authorization("Token " + KEY) is None
    assert ss12_scopes.key_credential_from_authorization("") is None


async def _create_key(client: AsyncClient, admin_headers: dict[str, str]) -> str:
    resp = await client.post(
        "/api/v1/api-keys",
        json={"name": "a65-probe-key", "preset": "full"},
        headers=admin_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["key"]


@pytest.mark.asyncio
async def test_a65_lowercase_bearer_key_is_default_denied_over_http(
    client: AsyncClient, admin_headers: dict[str, str]
):
    """端到端：小写 scheme 的 key 也必须被默认拒绝，而不是穿到路由层拿 404。"""
    key = await _create_key(client, admin_headers)
    resp = await client.get(
        UNMAPPED, headers={"Authorization": f"bearer {key}"}
    )
    assert resp.status_code == 403, (
        f"lowercase `bearer` key credential reached {resp.status_code} instead of "
        "the default-deny 403"
    )
    assert resp.json()["detail"]["reason"] == "unmapped_prefix"


@pytest.mark.asyncio
async def test_a65_zero_scope_key_cannot_mint_jupyter_ticket(
    client: AsyncClient, admin_headers: dict[str, str]
):
    """台账给出的实际可达面：jupyter 路由器挂载时没有 dependencies=，key 以签发人身份铸开发环工单。"""
    key = await _create_key(client, admin_headers)
    resp = await client.post(
        "/api/jupyter/tickets", headers={"Authorization": f"bearer {key}"}
    )
    assert resp.status_code == 403, (
        f"request reached the jupyter route with {resp.status_code}: an unmapped "
        "prefix must be refused before routing, not inside the route"
    )
    assert resp.json()["detail"]["reason"] == "unmapped_prefix"


@pytest.mark.asyncio
async def test_a65_humans_are_untouched(client: AsyncClient, admin_headers: dict[str, str]):
    """人类 JWT 打到同一未映射前缀仍是框架 404（默认拒绝只针对 key）。"""
    resp = await client.get(UNMAPPED, headers=admin_headers)
    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
async def test_a65_uppercase_bearer_key_behaviour_unchanged(
    client: AsyncClient, admin_headers: dict[str, str]
):
    """原本就生效的标准写法不得因本次放宽而漂移：仍 403 unmapped_prefix。"""
    key = await _create_key(client, admin_headers)
    resp = await client.get(UNMAPPED, headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"]["reason"] == "unmapped_prefix"
