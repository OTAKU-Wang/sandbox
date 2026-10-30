"""OCR A287 leg 2/3 — identities.html 生成 DEK 把 product_id 放进 JSON body。

后端 `POST /api/v1/kms/keys` 的 `create_dek(product_id: uuid.UUID, ...)` 是**非 Body
标量参数**，FastAPI 按 query 解析 ⇒ 模板只发 `{product_id}` body 的请求必然 422，
身份/密钥管理页的主写操作 100% 不可用。本文件两层取证：

1. 真后端合同：query 缺失必然 422、query 携带则 201 —— 钉死「修法方向是 query，
   不是让后端改收 body」；
2. 页面行为：Jinja 渲染 identities.html + node vm 真跑页内内联脚本，断言
   `generateKey()` 实际发出的 POST URL 携带 `?product_id=`。
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from httpx import AsyncClient
from jinja2 import Environment, FileSystemLoader, select_autoescape

REPO = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
PRODUCT_ID = "3f2b6c1d-8a4e-4f5b-9c3d-7e2a1b0c9d8e"

_BOOT = r"""
'use strict';
const fs = require('fs');
const vm = require('vm');
const [adminPath, pagePath, productId] = process.argv.slice(2);
const calls = [];
const els = {};
function el(sel) {
  if (!els[sel]) els[sel] = { innerHTML: '', textContent: '', style: {}, value: '' };
  return els[sel];
}
async function fakeFetch(url, opts) {
  const method = (opts && opts.method) || 'GET';
  calls.push({
    method: method,
    url: String(url),
    body: opts && opts.body !== undefined ? String(opts.body) : null,
  });
  return {
    ok: true, status: 201, statusText: 'Created',
    json: async function () { return { key_id: 'dek-test', items: [] }; },
    text: async function () { return JSON.stringify({ key_id: 'dek-test', items: [] }); },
  };
}
const sandbox = {
  document: { querySelector: el, querySelectorAll: function () { return []; } },
  localStorage: { getItem: function () { return 'tok'; }, setItem: function () {} },
  fetch: fakeFetch, alert: function () {}, prompt: function () { return null; },
  console: console,
};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(adminPath, 'utf8'), sandbox, { filename: 'admin.js' });
vm.runInContext(fs.readFileSync(pagePath, 'utf8'), sandbox, { filename: 'identities.page.js' });
"""

_HARNESS = _BOOT + r"""
els['#key-product-id'] = { innerHTML: '', textContent: '', style: {}, value: productId };
Promise.resolve()
  .then(function () { return vm.runInContext('generateKey()', sandbox); })
  .then(function () {
    setTimeout(function () { process.stdout.write(JSON.stringify({ calls: calls })); }, 0);
  })
  .catch(function (e) { process.stdout.write(JSON.stringify({ calls: calls, error: String(e) })); });
"""


def _page_script(tmp_path: Path) -> Path:
    env = Environment(
        loader=FileSystemLoader(str(REPO / "app" / "templates")),
        autoescape=select_autoescape(["html"], default_for_string=True),
    )
    html = env.get_template("identities.html").render()
    inline = [
        s for s in re.findall(r"<script[^>]*>(.*?)</script>", html, re.DOTALL)
        if s.strip() and "src=" not in s[: s.find(">")]
    ]
    assert len(inline) == 1, f"页内内联脚本数量异常: {len(inline)}"
    page = tmp_path / "identities.page.js"
    page.write_text(inline[0], encoding="utf-8")
    return page


def _generate_key_calls(tmp_path: Path) -> list[dict[str, str]]:
    assert NODE, "node 不在 PATH - 行为门无法运行（不是通过，是缺条件）"
    harness = tmp_path / "harness.js"
    harness.write_text(_HARNESS, encoding="utf-8")
    proc = subprocess.run(
        [NODE, str(harness), str(REPO / "app" / "static" / "js" / "admin.js"),
         str(_page_script(tmp_path)), PRODUCT_ID],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    assert proc.returncode == 0, f"node rc={proc.returncode}\n{proc.stderr}"
    out = json.loads(proc.stdout)
    assert not out.get("error"), out["error"]
    return [c for c in out["calls"] if c["method"] == "POST"]


@pytest.mark.skipif(not NODE, reason="node 不在 PATH")
def test_generate_key_posts_product_id_as_query_param(tmp_path):
    posts = _generate_key_calls(tmp_path)
    create = [c for c in posts if c["url"].split("?")[0] == "/api/v1/kms/keys"]
    assert create, f"generateKey 没有向 /api/v1/kms/keys 发 POST: {posts}"
    url = create[0]["url"]
    assert f"product_id={PRODUCT_ID}" in url, (
        f"product_id 必须按后端契约作为 query 参数发出（后端标量参数按 query 解析），"
        f"实际 URL: {url}"
    )


async def test_backend_create_dek_accepts_both_query_and_body_forms(
    client: AsyncClient, admin_headers: dict[str, str]
):
    """A287 裁决：`POST /api/v1/kms/keys` 对外是**双形态**契约。

    本用例原名 `test_backend_create_dek_requires_query_param`，钉的是「body-only ⇒ 422」——
    而那恰好就是 A287 点名的病灶本身（identities.html 把 `product_id` 放 JSON body，后端只按
    query 解析 ⇒ 必然 422）。平台在 A87 已把同型路由定成双形态（body 可选、query 仍接受、重叠时 body 胜），
    所以这里不是「为了让修复变绿而放宽断言」，而是把旧契约钉换成现行契约钉：
      • body-only ⇒ 201（修复面）
      • query-only ⇒ 201（不得回归：旧页面/脚本仍可用）
      • 两形态皆缺 ⇒ 422（校验覆盖不得消失，本文件原来没有这一腿，现补上）
    """
    body_only = await client.post(
        "/api/v1/kms/keys", json={"product_id": PRODUCT_ID}, headers=admin_headers,
    )
    assert body_only.status_code == 201, body_only.text[:300]
    assert body_only.json()["key_id"]

    with_query = await client.post(
        f"/api/v1/kms/keys?product_id={PRODUCT_ID}", headers=admin_headers,
    )
    assert with_query.status_code == 201, with_query.text[:300]
    assert with_query.json()["key_id"]

    neither = await client.post("/api/v1/kms/keys", headers=admin_headers)
    assert neither.status_code == 422, neither.text[:200]
