"""A114 — 管理端模板 innerHTML 注入防线。

app/templates/*.html 用 innerHTML 渲染服务端字段（告警 message、证书/连接器
名称、key_id…），历史上全仓没有任何 HTML 转义工具。本文件钉住三件事：

1. `app/static/js/admin.js` 里的 `esc()` 是唯一的转义工具（一处定义），
   `badge()` 内部必须经过它；API 错误消息不透传整个响应体。
2. 静态门：5 个页面脚本 + admin.js 中，任何含 HTML 标签的模板字符串里的
   `${...}` 插值，只要读了对象属性（服务端数据）就必须包在白名单 helper
   （esc / escAttr / badge / statusBadge / formatDate）里。
3. 行为门（需要本机 node）：把页面脚本连同 admin.js 放进 node vm + 假 DOM 跑
   真实渲染，注入串 `<img src=x onerror=alert(1)>` 必须渲染为字面文本，
   `onclick="fn('...')"` 内不能有引号逃逸。
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
ADMIN_JS = REPO / "app" / "static" / "js" / "admin.js"
PAGES = [
    "alerts.html",
    "blockchain.html",
    "certificates.html",
    "connectors.html",
    "identities.html",
]

SCRIPT_RE = re.compile(r"<script>(.*?)</script>", re.S)
LITERAL_RE = re.compile(r"`[^`]*`", re.S)
TAG_RE = re.compile(r"<[a-zA-Z/]")
INTERP_RE = re.compile(r"\$\{([^{}]*)\}")
PROP_RE = re.compile(r"[A-Za-z_$][\w$]*\s*(?:\.|\[)")
WRAPPERS = ("esc(", "escAttr(", "badge(", "statusBadge(", "formatDate(")

IMG = "<img src=x onerror=alert(1)>"
QUOTE = "x');alert(1);//"
ATTR = 'x" onmouseover="alert(1)'

PAYLOADS = {
    "alerts.html": {
        "items": [
            {"alert_type": IMG, "message": IMG, "severity": IMG,
             "status": "active", "created_at": "2026-10-18T08:00:00"},
        ],
    },
    "blockchain.html": {
        "items": [
            {"action": "anchor.batch", "resource_id": IMG,
             "created_at": "2026-10-18T08:00:00",
             "detail": {"merkle_root": IMG, "tx_hash": IMG, "block_height": 7}},
        ],
        "record_id": IMG,
        "merkle_root": IMG,
    },
    "certificates.html": {
        "items": [
            {"cert_id": QUOTE, "subject": IMG, "cert_type": IMG,
             "status": "revoked", "not_after": "2027-01-01T00:00:00"},
        ],
        "cert_id": QUOTE,
        "subject": IMG,
        "cert_pem": IMG,
    },
    "connectors.html": {
        "items": [
            {"space_name": IMG, "space_type": IMG, "status": "active",
             "id": ATTR, "updated_at": "2026-10-18T08:00:00"},
        ],
    },
    "identities.html": {
        "items": [
            {"key_id": QUOTE, "product_id": IMG, "algorithm": IMG,
             "status": "active", "usage_count": 1, "max_usage": 2,
             "created_at": "2026-10-18T08:00:00"},
        ],
        "key_id": QUOTE,
        "key_bytes": IMG,
    },
}

# 页面脚本里自动加载之外的显式触发点（结果面板也走 innerHTML）
INVOKES = {
    "alerts.html": {},
    "blockchain.html": {"fn": "verifyTx", "inputs": {"#tx-hash": "rec-1"}},
    "certificates.html": {
        "fn": "issueCert",
        "inputs": {"#cert-cn": "cn", "#cert-org": "o",
                   "#cert-type": "root", "#cert-validity": "365"},
    },
    "connectors.html": {},
    "identities.html": {"fn": "generateKey", "inputs": {"#key-product-id": "prod-1"}},
}

_HARNESS = r"""
'use strict';
const fs = require('fs');
const vm = require('vm');
const [adminPath, scriptPath, payloadPath, invokePath] = process.argv.slice(2);
const payload = JSON.parse(fs.readFileSync(payloadPath, 'utf8'));
const invoke = JSON.parse(fs.readFileSync(invokePath, 'utf8'));
const els = {};
function el(sel) {
  if (!els[sel]) els[sel] = { innerHTML: '', textContent: '', style: {}, value: '' };
  return els[sel];
}
Object.keys(invoke.inputs || {}).forEach(function (k) { el(k).value = invoke.inputs[k]; });
function mkRes(ok) {
  return {
    ok: ok, status: ok ? 200 : 500,
    json: async function () { return payload; },
    text: async function () { return JSON.stringify(payload); },
  };
}
const sandbox = {
  document: { querySelector: el, querySelectorAll: function () { return []; } },
  localStorage: { getItem: function () { return 'tok'; }, setItem: function () {} },
  fetch: async function () { return mkRes(!invoke.fail); },
  alert: function () {},
  confirm: function () { return true; },
  console: console,
};
vm.createContext(sandbox);
vm.runInContext(
  fs.readFileSync(adminPath, 'utf8') + '\n;\n' + fs.readFileSync(scriptPath, 'utf8'),
  sandbox, { filename: 'page.js' });
const done = (invoke.fn && typeof sandbox[invoke.fn] === 'function')
  ? Promise.resolve().then(function () { return sandbox[invoke.fn](); })
  : Promise.resolve();
done.catch(function (e) {
  els['__harness__'] = { innerHTML: 'HARNESS-ERROR: ' + (e && e.stack || String(e)) };
}).then(function () {
  // fetch stub 只产生微任务；微任务队列在下一个宏任务(本 timer)前必然排空，
  // 所以这里不是靠时序碰运气，而是事件循环语义保证的确定性收敛点。
  setTimeout(function () { process.stdout.write(JSON.stringify(els)); }, 0);
});
"""


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _page_scripts(name: str) -> str:
    """页面经 app.api.admin 真实 Jinja 环境渲染后的内联脚本。"""
    from app.api.admin import _env

    html = _env.get_template(name).render()
    return "\n".join(SCRIPT_RE.findall(html))


def _flagged_interpolations(src: str) -> list:
    """含 HTML 标签的模板字符串里，读了属性/|| 却没包白名单 helper 的插值。"""
    bad = []
    for lit in LITERAL_RE.findall(src):
        if not TAG_RE.search(lit):
            continue
        for m in INTERP_RE.finditer(lit):
            body = m.group(1).strip()
            if body.startswith(WRAPPERS):
                continue
            if PROP_RE.search(body) or "||" in body:
                bad.append(body)
    return bad


class TestCentralEscaper:
    def test_esc_defined_once_and_badge_uses_it(self):
        src = _read(ADMIN_JS)
        assert src.count("function esc(") == 1, "esc() 必须全仓唯一定义在 admin.js"
        assert "function escAttr(" in src, "onclick=\"fn('...')\" 场景需要 escAttr()"
        badge_src = re.search(r"function badge\([\s\S]*?\n\}", src)
        assert badge_src, "badge() 不存在"
        assert "${esc(text)}" in badge_src.group(0), "badge() 必须转义 text"
        assert "${esc(type)}" in badge_src.group(0), "badge() 必须转义 type"
        status_src = re.search(r"function statusBadge\([\s\S]*?\n\}", src)
        assert status_src and "badge(status" in status_src.group(0), \
            "statusBadge() 必须复用 badge()，转义只在一处"

    def test_api_error_message_body_capped(self):
        # 台账：`${res.status}: ${await res.text()}` 把整个响应体塞进 e.message
        # 再进 innerHTML。至少要截断响应体。
        src = _read(ADMIN_JS)
        assert re.search(r"await res\.text\(\)\)\.slice\(0,\s*\d+\)", src), \
            "API 错误消息必须截断响应体（slice）"

    @pytest.mark.parametrize("page", PAGES)
    def test_no_unescaped_server_field_in_html_literals(self, page):
        offenders = _flagged_interpolations(_page_scripts(page))
        assert not offenders, f"{page}: innerHTML 插值未经转义 helper: {offenders}"

    def test_admin_js_no_unescaped_server_field_in_html_literals(self):
        assert not _flagged_interpolations(_read(ADMIN_JS))

    def test_render_env_autoescape_enabled(self):
        from app.api.admin import _env

        assert _env.autoescape, "admin 渲染辅助必须开 Jinja autoescape（防御纵深）"


NODE = shutil.which("node")


@pytest.mark.skipif(not NODE, reason="node 不在 PATH，行为门跳过（静态门仍生效）")
class TestInjectionRendersInert:
    def _run(self, tmp_path: Path, page: str, fail: bool) -> str:
        page_src = tmp_path / "page.js"
        page_src.write_text(_page_scripts(page), encoding="utf-8")
        harness = tmp_path / "harness.js"
        harness.write_text(_HARNESS, encoding="utf-8")
        payload_f = tmp_path / "payload.json"
        payload_f.write_text(
            json.dumps(PAYLOADS[page], ensure_ascii=False), encoding="utf-8")
        invoke = dict(INVOKES[page])
        invoke["fail"] = fail
        invoke_f = tmp_path / "invoke.json"
        invoke_f.write_text(json.dumps(invoke), encoding="utf-8")
        proc = subprocess.run(
            [NODE, str(harness), str(ADMIN_JS), str(page_src),
             str(payload_f), str(invoke_f)],
            capture_output=True, text=True, encoding="utf-8", timeout=60,
        )
        assert proc.returncode == 0, f"node harness rc={proc.returncode}\n{proc.stderr}"
        els = json.loads(proc.stdout)
        assert proc.stdout.strip(), f"harness 无输出\n{proc.stderr}"
        joined = "\n".join(str(v.get("innerHTML", "")) for v in els.values())
        assert "HARNESS-ERROR" not in joined, joined
        return joined

    @pytest.mark.parametrize("page", PAGES)
    @pytest.mark.parametrize("mode", ["ok", "fail"])
    def test_payload_renders_as_literal_text(self, tmp_path, page, mode):
        joined = self._run(tmp_path, page, fail=(mode == "fail"))
        # 注入串必须成为字面文本，且任何位置不得残留可执行标签
        assert "&lt;img src=x onerror=" in joined, \
            f"{page}/{mode}: 注入串没有以字面文本渲染\n{joined}"
        assert "<img" not in joined, f"{page}/{mode}: 残留原始 <img 标签\n{joined}"

    @pytest.mark.parametrize("page", PAGES)
    def test_onclick_attrs_cannot_be_breakout(self, tmp_path, page):
        joined = self._run(tmp_path, page, fail=False)
        attrs = re.findall(r'onclick="([^"]*)"', joined)
        for body in attrs:
            assert body.count("'") == 2, \
                f"{page}: onclick JS 字符串被引号逃逸: {body}"
        assert '" onmouseover="' not in joined, f"{page}: 属性逃逸\n{joined}"
