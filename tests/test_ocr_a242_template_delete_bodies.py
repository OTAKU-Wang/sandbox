"""OCR A242 leg (templates) — 证书吊销/密钥销毁对话框必须自己把 reason/ticket_id 传进 DELETE body。

`app/static/js/admin.js` 的 `API.del(url, body)`（A242 客户端半已修）在调用方不给
body 时退回通用 prompt 要一个 reason。这只证明共享客户端会兜底，不证明页面对话框
真的把操作者输入传进请求：`certificates.html` 的 `revokeCert` 与 `identities.html`
的 `destroyKey` 在 HEAD 都是 `API.del(url)` 裸调用，DELETE 发出时携带的是兜底路径的
空 reason（后端 `HighRiskOperationRequest.reason` min_length=8 ⇒ 必然 422），
审计要记的 `ticket_id` 更是根本没有出口。本测试的假 prompt 对 admin.js 的通用兜底
问题固定回答 null（模拟操作者不理会自动问题），只有页面自己发起的对话框才拿到
答案 ⇒ 断言的是页面必须自带对话框并显式传 body，而不是蹭客户端兜底。
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape

REPO = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
GENERIC_PROMPT = "删除原因（必填，至少 8 字）："
REASON_ANSWER = "rotate before decommission, per ops runbook 4.2"
TICKET_ANSWER = "CHG-2026-1042"

_HARNESS = r"""
'use strict';
const fs = require('fs');
const vm = require('vm');
const [adminPath, pagePath, callExpr] = process.argv.slice(2);
const GENERIC = '删除原因（必填，至少 8 字）：';
const REASON = 'rotate before decommission, per ops runbook 4.2';
const TICKET = 'CHG-2026-1042';
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
    ok: true, status: 200, statusText: 'OK',
    json: async function () { return { ok: true, items: [] }; },
    text: async function () { return JSON.stringify({ ok: true, items: [] }); },
  };
}
function fakePrompt(msg) {
  if (msg === GENERIC) return null;
  if (/ticket|工单/i.test(msg)) return TICKET;
  return REASON;
}
const sandbox = {
  document: { querySelector: el, querySelectorAll: function () { return []; } },
  localStorage: { getItem: function () { return 'tok'; }, setItem: function () {} },
  fetch: fakeFetch, alert: function () {}, confirm: function () { return true; },
  prompt: fakePrompt, console: console,
};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(adminPath, 'utf8'), sandbox, { filename: 'admin.js' });
vm.runInContext(fs.readFileSync(pagePath, 'utf8'), sandbox, { filename: 'page.js' });
Promise.resolve()
  .then(function () { return vm.runInContext(callExpr, sandbox); })
  .then(function () {
    setTimeout(function () { process.stdout.write(JSON.stringify({ calls: calls })); }, 0);
  })
  .catch(function (e) { process.stdout.write(JSON.stringify({ calls: calls, error: String(e) })); });
"""


def _page_script(tmp_path: Path, template: str) -> Path:
    env = Environment(
        loader=FileSystemLoader(str(REPO / "app" / "templates")),
        autoescape=select_autoescape(["html"], default_for_string=True),
    )
    html = env.get_template(template).render()
    inline = [
        s for s in re.findall(r"<script[^>]*>(.*?)</script>", html, re.DOTALL)
        if s.strip() and "src=" not in s[: s.find(">")]
    ]
    assert len(inline) == 1, f"{template} 页内内联脚本数量异常: {len(inline)}"
    page = tmp_path / f"{template}.page.js"
    page.write_text(inline[0], encoding="utf-8")
    return page


def _delete_call(tmp_path: Path, template: str, expr: str, target: str) -> dict[str, object]:
    assert NODE, "node 不在 PATH - 行为门无法运行（不是通过，是缺条件）"
    harness = tmp_path / "harness.js"
    harness.write_text(_HARNESS, encoding="utf-8")
    proc = subprocess.run(
        [NODE, str(harness), str(REPO / "app" / "static" / "js" / "admin.js"),
         str(_page_script(tmp_path, template)), expr],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    assert proc.returncode == 0, f"node rc={proc.returncode}\n{proc.stderr}"
    out = json.loads(proc.stdout)
    assert not out.get("error"), out["error"]
    deletes = [c for c in out["calls"] if c["method"] == "DELETE" and target in c["url"]]
    assert deletes, f"{template} 没有发出目标 DELETE 请求: {out['calls']}"
    call = deletes[0]
    assert call["body"] is not None, f"{template} 的 DELETE 仍然不带请求体: {call}"
    return json.loads(call["body"])


def test_certificate_revoke_dialog_sends_its_reason(tmp_path):
    body = _delete_call(
        tmp_path, "certificates.html",
        "revokeCert('cert-a242')", "/api/v1/certificates/cert-a242",
    )
    reason = body.get("reason")
    assert isinstance(reason, str) and len(reason) >= 8, (
        f"吊销对话框没有把 reason 传进 DELETE body（后端 min_length=8 必然 422）: {body}"
    )


def test_key_destroy_dialog_sends_reason_and_ticket(tmp_path):
    body = _delete_call(
        tmp_path, "identities.html",
        "destroyKey('dek-a242')", "/api/v1/kms/keys/dek-a242",
    )
    reason = body.get("reason")
    assert isinstance(reason, str) and len(reason) >= 8, (
        f"销毁对话框没有把 reason 传进 DELETE body（后端 min_length=8 必然 422）: {body}"
    )
    ticket = body.get("ticket_id")
    assert isinstance(ticket, str) and ticket, (
        f"销毁对话框没有把 ticket_id 传进 DELETE body（审计要记的关联工单没有出口）: {body}"
    )
