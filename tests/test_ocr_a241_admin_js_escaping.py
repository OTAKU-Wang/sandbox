"""OCR A241 — 遗留 /admin/* 页面把 API 数据直接塞进 innerHTML，历史上没有任何转义入口。

本文件用 node vm + 假 DOM 真跑 `app/static/js/admin.js` 的公共渲染件
（`esc` / `escAttr` / `badge` / `statusBadge` / `loadPage`），钉住 A241 的载荷类：
`<img src=x onerror=alert(1)>` 必须只剩字面文本，`onclick="fn('...')"` 里的单引号
不得逃逸出 JS 字符串字面量。台账见 docs/ocr-review-2026-10-18/ocr-verified-findings.md。
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
ADMIN_JS = REPO / "app" / "static" / "js" / "admin.js"

IMG = "<img src=x onerror=alert(1)>"
QUOTE = "x');alert(1);//"
ATTR = 'x" onmouseover="alert(1)'

NODE = shutil.which("node")

_HARNESS = r"""
'use strict';
const fs = require('fs');
const vm = require('vm');
const [adminPath] = process.argv.slice(2);
const els = {};
function el(sel) {
  if (!els[sel]) els[sel] = { innerHTML: '', textContent: '', style: {}, value: '' };
  return els[sel];
}
const PAYLOAD = %s;
const sandbox = {
  document: { querySelector: el, querySelectorAll: function () { return []; } },
  localStorage: { getItem: function () { return 'tok'; }, setItem: function () {} },
  fetch: async function () {
    return {
      ok: false, status: 500,
      json: async function () { throw new SyntaxError('Unexpected end of JSON input'); },
      text: async function () { return PAYLOAD; },
    };
  },
  alert: function () {},
  console: console,
};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(adminPath, 'utf8'), sandbox, { filename: 'admin.js' });
const row = '<td>' + sandbox.badge(PAYLOAD, PAYLOAD) + '</td>'
  + '<td>' + sandbox.statusBadge(PAYLOAD) + '</td>'
  + '<button onclick="destroyKey(\'' + sandbox.escAttr(PAYLOAD) + '\')">'
  + 'x</button> <span>' + sandbox.esc(PAYLOAD) + '</span>';
els['#row'] = { innerHTML: row };
Promise.resolve()
  .then(function () { return sandbox.loadPage('/api/v1/x', function () {}); })
  .catch(function () {})
  .then(function () {
    setTimeout(function () { process.stdout.write(JSON.stringify(els)); }, 0);
  });
"""


def _run(tmp_path: Path) -> str:
    assert NODE, "node 不在 PATH — 行为门无法运行（不是通过，是缺条件）"
    harness = tmp_path / "harness.js"
    harness.write_text(_HARNESS % json.dumps(IMG), encoding="utf-8")
    proc = subprocess.run(
        [NODE, str(harness), str(ADMIN_JS)],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    assert proc.returncode == 0, f"node rc={proc.returncode}\n{proc.stderr}"
    out = proc.stdout
    assert out.strip(), f"harness 无输出\n{proc.stderr}"
    els = json.loads(out)
    joined = "\n".join(str(v.get("innerHTML", "")) for v in els.values())
    assert "HARNESS-ERROR" not in joined, joined
    return joined


@pytest.mark.skipif(not NODE, reason="node 不在 PATH")
def test_badge_and_esc_render_payload_as_literal_text(tmp_path):
    joined = _run(tmp_path)
    assert "<img" not in joined, f"残留可执行标签: {joined}"
    assert "&lt;img src=x onerror=" in joined, f"载荷没有以字面文本渲染: {joined}"


@pytest.mark.skipif(not NODE, reason="node 不在 PATH")
def test_onclick_string_literal_cannot_be_broken_out(tmp_path):
    joined = _run(tmp_path)
    for body in re.findall(r'onclick="([^"]*)"', joined):
        assert body.count("'") == 2, f"onclick JS 字符串被引号逃逸: {body}"


@pytest.mark.skipif(not NODE, reason="node 不在 PATH")
def test_api_error_path_renders_response_body_inert(tmp_path):
    """`loadPage` 失败路径：`${res.status}: ${await res.text()}` 直进 innerHTML。"""
    harness_src = _HARNESS % json.dumps(QUOTE)
    f = tmp_path / "harness2.js"
    f.write_text(harness_src, encoding="utf-8")
    proc = subprocess.run(
        [NODE, str(f), str(ADMIN_JS)],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    joined = "\n".join(
        str(v.get("innerHTML", "")) for v in json.loads(proc.stdout).values()
    )
    assert "');alert(1)" not in joined, f"未转义的引号逃逸进了 DOM: {joined}"
    assert "&#39;);alert(1)" in joined, f"错误消息未经转义上屏: {joined}"
