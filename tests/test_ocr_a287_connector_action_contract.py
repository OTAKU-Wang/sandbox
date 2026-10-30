"""OCR A287 — 遗留管理台主要写操作接错契约：已暂停连接器的动作按钮仍打 /suspend。

后端 `POST /api/v1/connectors/{id}/reactivate` 存在，`suspend_connector` 只接受
active→suspended；`connectors.html` 的行内动作按钮对 active / suspended 一律调用
`suspendConnector()`，于是标签为「恢复」的主写操作对已暂停连接器必然 4xx。本文件用
node vm + 假 DOM 真跑页面内联脚本，取出渲染出来的内联处理器并执行，断言真实请求 URL。
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
TABLE_SEL = "#connectors-table"

CONNECTORS = [
    {"id": "c-active", "name": "active-node", "status": "active"},
    {"id": "c-susp", "name": "paused-node", "status": "suspended"},
]

_BOOT = r"""
'use strict';
const fs = require('fs');
const vm = require('vm');
const [adminPath, pagePath, payloadJson, handler] = process.argv.slice(2);
const CONNECTORS = JSON.parse(payloadJson);
const calls = [];
const els = {};
function el(sel) {
  if (!els[sel]) els[sel] = { innerHTML: '', textContent: '', style: {}, value: '' };
  return els[sel];
}
async function fakeFetch(url, opts) {
  const method = (opts && opts.method) || 'GET';
  calls.push(method + ' ' + url);
  let body = {};
  if (String(url).indexOf('/api/v1/connectors') >= 0 && method === 'GET') body = CONNECTORS;
  return {
    ok: true, status: 200, statusText: 'OK',
    json: async function () { return body; },
    text: async function () { return JSON.stringify(body); },
  };
}
const sandbox = {
  document: { querySelector: el, querySelectorAll: function () { return []; } },
  localStorage: { getItem: function () { return 'tok'; }, setItem: function () {} },
  fetch: fakeFetch, alert: function () {}, prompt: function () { return 'ops cleanup'; },
  console: console,
};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(adminPath, 'utf8'), sandbox, { filename: 'admin.js' });
vm.runInContext(fs.readFileSync(pagePath, 'utf8'), sandbox, { filename: 'connectors.page.js' });
"""

_HARNESS = _BOOT + r"""
Promise.resolve().then(function () {
  setTimeout(function () {
    process.stdout.write(JSON.stringify({ calls: calls, table: els['%s'] ? els['%s'].innerHTML : '' }));
  }, 0);
});
""" % (TABLE_SEL, TABLE_SEL)

_HARNESS_EXEC = _BOOT + r"""
Promise.resolve()
  .then(function () { return vm.runInContext(handler, sandbox); })
  .then(function () {
    setTimeout(function () { process.stdout.write(JSON.stringify({ calls: calls })); }, 0);
  })
  .catch(function (e) { process.stdout.write(JSON.stringify({ calls: calls, error: String(e) })); });
"""

_ENTITIES = {"&#39;": "'", "&quot;": '"', "&lt;": "<", "&gt;": ">", "&amp;": "&"}


def _decode(value: str) -> str:
    for ent, ch in _ENTITIES.items():
        value = value.replace(ent, ch)
    return value


def _page_script(tmp_path: Path) -> Path:
    env = Environment(
        loader=FileSystemLoader(str(REPO / "app" / "templates")),
        autoescape=select_autoescape(["html"], default_for_string=True),
    )
    html = env.get_template("connectors.html").render()
    inline = [
        s for s in re.findall(r"<script[^>]*>(.*?)</script>", html, re.DOTALL)
        if s.strip() and "src=" not in s[: s.find(">")]
    ]
    assert len(inline) == 1, f"页内内联脚本数量异常: {len(inline)}"
    page = tmp_path / "connectors.page.js"
    page.write_text(inline[0], encoding="utf-8")
    return page


def _node(tmp_path: Path, harness_src: str, handler: str = "") -> dict:
    harness = tmp_path / "harness.js"
    harness.write_text(harness_src, encoding="utf-8")
    proc = subprocess.run(
        [NODE, str(harness), str(REPO / "app" / "static" / "js" / "admin.js"),
         str(_page_script(tmp_path)), json.dumps(CONNECTORS), handler],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    assert proc.returncode == 0, f"node rc={proc.returncode}\n{proc.stderr}"
    return json.loads(proc.stdout)


def _row_action(tmp_path: Path, marker: str) -> str:
    rendered = _node(tmp_path, _HARNESS)
    assert "HARNESS-ERROR" not in rendered["table"], rendered["table"]
    for row in rendered["table"].split("<tr>"):
        if marker in row:
            for raw in re.findall(r'onclick="([^"]*)"', row):
                handler = _decode(raw)
                if re.search(r"\w*[sS]uspend\w*\(|\w*reactivate\w*\(|\w*toggle\w*\(", handler):
                    return handler
    raise AssertionError(f"未找到标签含 {marker} 的连接器动作按钮:\n{rendered['table']}")


def _posted_urls(tmp_path: Path, handler: str) -> list:
    out = _node(tmp_path, _HARNESS_EXEC, handler)
    assert not out.get("error"), out["error"]
    return [c for c in out["calls"] if c.startswith("POST")]


@pytest.mark.skipif(not NODE, reason="node 不在 PATH")
def test_suspended_connector_action_posts_reactivate(tmp_path):
    handler = _row_action(tmp_path, "恢复")
    posts = _posted_urls(tmp_path, handler)
    assert posts == ["POST /api/v1/connectors/c-susp/reactivate"], posts


@pytest.mark.skipif(not NODE, reason="node 不在 PATH")
def test_active_connector_action_still_posts_suspend(tmp_path):
    handler = _row_action(tmp_path, "暂停")
    posts = _posted_urls(tmp_path, handler)
    assert posts == ["POST /api/v1/connectors/c-active/suspend"], posts
