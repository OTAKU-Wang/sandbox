"""OCR A83 — 遗留 /admin/* 页面缺少 HTML 注入的防御纵深（开发形态 high / 生产由 ADMIN_PAGES_ENABLED 门挡住）。

`app/api/admin.py` 把 5 张 Jinja 管理页直接端给浏览器，页面把 API 数据写进
`innerHTML` 并把管理台 Bearer 放在 `localStorage['cds_token']`。转义层（`esc()` /
Jinja autoescape）之外，端这些页面时还必须带上响应头层的纵深：
外部脚本源与跨源 `fetch` 一律禁止（注入点无法再把 token 送出同源之外）、
禁止被 iframe 套嵌、禁止嗅探类型、禁止缓存含管理台上下文的文档。
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.admin import router

PAGES = ["/admin/identities", "/admin/certificates", "/admin/connectors",
         "/admin/alerts", "/admin/blockchain"]


@pytest.fixture()
def admin_client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _csp(client: TestClient, path: str) -> dict:
    res = client.get(path)
    assert res.status_code == 200, res.text
    raw = res.headers.get("content-security-policy", "")
    directives = {}
    for part in raw.split(";"):
        bits = part.strip().split()
        if bits:
            directives[bits[0]] = set(bits[1:])
    return {"headers": res.headers, "csp": directives}


@pytest.mark.parametrize("path", PAGES)
def test_page_response_pins_script_and_connect_sources(admin_client, path):
    got = _csp(admin_client, path)
    csp = got["csp"]
    assert "default-src" in csp and "'self'" in csp["default-src"], csp
    assert "script-src" in csp and "'self'" in csp["script-src"], csp
    sources = set().union(*csp.values()) if csp else set()
    assert not [s for s in sources if s.startswith(("http:", "https:", "*"))], \
        f"CSP 允许了外部源: {sources}"
    assert csp.get("object-src") == {"'none'"}, csp
    assert csp.get("base-uri") == {"'none'"}, csp
    assert csp.get("frame-ancestors") == {"'none'"}, csp


@pytest.mark.parametrize("path", PAGES)
def test_page_response_is_not_sniffable_and_not_cacheable(admin_client, path):
    got = _csp(admin_client, path)
    headers = got["headers"]
    assert headers.get("x-content-type-options") == "nosniff", dict(headers)
    assert "no-store" in headers.get("cache-control", ""), dict(headers)


def test_connect_src_blocks_token_exfiltration_origin(admin_client):
    got = _csp(admin_client, "/admin/identities")
    connect = got["csp"].get("connect-src", set())
    assert connect and "'self'" in connect, f"connect-src 未收敛到同源: {connect}"
