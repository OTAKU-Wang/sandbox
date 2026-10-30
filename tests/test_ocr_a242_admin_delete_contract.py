"""OCR A242 - 遗留管理台与后端契约对不上：DELETE 不带体且无条件 res.json()。

`app/static/js/admin.js` 的 `API.del(url)` 既不发请求体，也无条件 `await res.json()`。
后端的高危删除（`DELETE /api/v1/certificates/{cert_id}`、`DELETE /api/v1/kms/keys/{key_id}`）
要求 `HighRiskOperationRequest = Body(...)`（reason 有 min_length），空体必然 422；
而返回 204/空体的删除成功会在 `res.json()` 上抛 SyntaxError，被页面报成「删除失败」。
本文件用 node vm 真跑 admin.js，记录真实发出的 method/url/body。
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from httpx import AsyncClient

REPO = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
REASON = "legacy admin console: rotate before decommission"

_HARNESS = r"""
'use strict';
const fs = require('fs');
const vm = require('vm');
const [adminPath, callExpr, promptAnswer] = process.argv.slice(2);
const calls = [];
const els = {};
function el(sel) {
  if (!els[sel]) els[sel] = { innerHTML: '', textContent: '', style: {}, value: '' };
  return els[sel];
}
async function fakeFetch(url, opts) {
  calls.push({
    method: (opts && opts.method) || 'GET',
    url: String(url),
    body: opts && opts.body !== undefined ? String(opts.body) : null,
  });
  return {
    ok: true, status: 204, statusText: 'No Content',
    json: async function () { throw new SyntaxError('Unexpected end of JSON input'); },
    text: async function () { return ''; },
  };
}
const sandbox = {
  document: { querySelector: el, querySelectorAll: function () { return []; } },
  localStorage: { getItem: function () { return 'tok'; }, setItem: function () {} },
  fetch: fakeFetch, alert: function () {}, prompt: function () { return promptAnswer; },
  console: console,
};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(adminPath, 'utf8'), sandbox, { filename: 'admin.js' });
Promise.resolve()
  .then(function () { return vm.runInContext(callExpr, sandbox); })
  .then(function (value) {
    process.stdout.write(JSON.stringify({ calls: calls, value: value === undefined ? 'undefined' : value }));
  })
  .catch(function (e) {
    process.stdout.write(JSON.stringify({ calls: calls, error: String(e && e.message ? e.message : e) }));
  });
"""


def _call(tmp_path: Path, expr: str, prompt_answer: str = REASON) -> dict:
    assert NODE, "node 不在 PATH - 行为门无法运行（不是通过，是缺条件）"
    harness = tmp_path / "harness.js"
    harness.write_text(_HARNESS, encoding="utf-8")
    proc = subprocess.run(
        [NODE, str(harness), str(REPO / "app" / "static" / "js" / "admin.js"),
         expr, prompt_answer],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    assert proc.returncode == 0, f"node rc={proc.returncode}\n{proc.stderr}"
    return json.loads(proc.stdout)


@pytest.mark.skipif(not NODE, reason="node 不在 PATH")
def test_delete_carries_the_high_risk_reason_body(tmp_path):
    out = _call(tmp_path, "API.del('/api/v1/certificates/cert-1')")
    assert not out.get("error"), out["error"]
    call = out["calls"][0]
    assert call["method"] == "DELETE", call
    assert call["body"] is not None, "DELETE 仍然不带请求体：高危删除必然 422"
    body = json.loads(call["body"])
    assert isinstance(body.get("reason"), str) and len(body["reason"]) >= 8, body


@pytest.mark.skipif(not NODE, reason="node 不在 PATH")
def test_delete_success_with_empty_body_is_not_reported_as_failure(tmp_path):
    out = _call(tmp_path, "API.del('/api/v1/certificates/cert-1')")
    assert "error" not in out, f"删除成功被报成失败: {out['error']}"


@pytest.mark.skipif(not NODE, reason="node 不在 PATH")
def test_caller_supplied_reason_is_not_overwritten(tmp_path):
    expr = "API.del('/api/v1/kms/keys/k1', { reason: 'operator typed this' })"
    out = _call(tmp_path, expr)
    assert not out.get("error"), out["error"]
    assert json.loads(out["calls"][0]["body"])["reason"] == "operator typed this"


@pytest.mark.skipif(not NODE, reason="node 不在 PATH")
def test_get_tolerates_an_empty_success_body(tmp_path):
    out = _call(tmp_path, "API.get('/api/v1/audit/export-status')")
    assert "error" not in out, f"空体 2xx GET 被报成失败: {out['error']}"


async def test_certificate_delete_contract_requires_a_reason(
    client: AsyncClient, admin_headers: dict
):
    generated = await client.post(
        "/api/v1/certificates/generate",
        json={"subject": "CN=a242-legacy, O=cds", "cert_type": "signing"},
        headers=admin_headers,
    )
    assert generated.status_code == 200, generated.text[:300]
    cert_id = generated.json()["cert_id"]

    bodyless = await client.delete(f"/api/v1/certificates/{cert_id}", headers=admin_headers)
    assert bodyless.status_code == 422, bodyless.text[:200]

    revoked = await client.request(
        "DELETE", f"/api/v1/certificates/{cert_id}",
        json={"reason": REASON}, headers=admin_headers,
    )
    assert revoked.status_code == 200, revoked.text[:300]
