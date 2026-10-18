"""ocr A134: 审计验真页读的是后端从未有过的 `result.valid` —— 永远渲染红色「失败」，
并丢弃后端诚实区分「联盟链 vs 本地 PG 追加日志」的 backend/verification_note。

钉死（后端契约 = app/api/audit.py::verify_audit_record 的实际返回）：
- 页面判定必须读 `result.verified`；
- backend.backend_label / verification_note 是必读展示项，不得丢弃；
- 输入框引导 record_id（UUID），recordId 进路径前 encodeURIComponent；
- 请求失败用固定文案，不透传后端响应原文。

静态门查页面脚本源码；行为门（本机 node）用假 DOM 跑真实 verifyTx，
verified true/false/错误三种响应下的渲染都必须是契约里的样子。
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
ADMIN_JS = REPO / "app" / "static" / "js" / "admin.js"

SCRIPT_RE = re.compile(r"<script>(.*?)</script>", re.S)


def _page_html(name: str) -> str:
    from app.api.admin import _env

    return _env.get_template(name).render()


def _page_scripts(name: str) -> str:
    return "\n".join(SCRIPT_RE.findall(_page_html(name)))


class TestVerifyPageContractStatic:
    def test_reads_verified_not_valid(self):
        src = _page_scripts("blockchain.html")
        assert "result.verified" in src, "验真页必须读后端真实字段 result.verified"
        assert not re.search(r"\bresult\.valid\b", src), "后端从未返回 result.valid"

    def test_disclosure_fields_rendered(self):
        src = _page_scripts("blockchain.html")
        assert "backend_label" in src, "backend/backend_label（联盟链 vs 本地 PG）必须上屏"
        assert "verification_note" in src, "verification_note 是 T8/F1 诚实披露字段，不得丢弃"

    def test_input_is_record_id_and_urlencoded(self):
        html = _page_html("blockchain.html")
        assert "tx_hash 进行验证" not in html, "后端只吃 record_id，不得引导用户输 tx_hash"
        src = _page_scripts("blockchain.html")
        assert "encodeURIComponent" in src, "recordId 未做 encodeURIComponent 直接拼路径"

    def test_error_path_is_fixed_copy(self):
        src = _page_scripts("blockchain.html")
        assert "'验证失败: ' + e.message" not in src and '"验证失败: " + e.message' not in src, (
            "错误提示必须固定文案，不得把 API.get 拼出的后端响应原文透传到页面"
        )


NODE = shutil.which("node")

_HARNESS = r"""
'use strict';
const fs = require('fs');
const vm = require('vm');
const [adminPath, scriptPath, payloadPath, mode] = process.argv.slice(2);
const payload = JSON.parse(fs.readFileSync(payloadPath, 'utf8'));
const els = {};
function el(sel) {
  if (!els[sel]) els[sel] = { innerHTML: '', textContent: '', style: {}, value: '' };
  return els[sel];
}
el('#tx-hash').value = 'rec-1';
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
  fetch: async function () { return mkRes(mode !== 'fail'); },
  alert: function (m) { sandbox.__alerts.push(m); },
  confirm: function () { return true; },
  console: console,
  __alerts: [],
};
vm.createContext(sandbox);
vm.runInContext(
  fs.readFileSync(adminPath, 'utf8') + '\n;\n' + fs.readFileSync(scriptPath, 'utf8'),
  sandbox, { filename: 'page.js' });
const done = (typeof sandbox.verifyTx === 'function')
  ? Promise.resolve().then(function () { return sandbox.verifyTx(); })
  : Promise.resolve();
done.catch(function (e) {
  els['__harness__'] = { innerHTML: 'HARNESS-ERROR: ' + (e && e.stack || String(e)) };
}).then(function () {
  setTimeout(function () {
    process.stdout.write(JSON.stringify({ els: els, alerts: sandbox.__alerts }));
  }, 0);
});
"""


@pytest.mark.skipif(not NODE, reason="node 不在 PATH，行为门跳过（静态门仍生效）")
class TestVerifyPageBehavior:
    def _run(self, tmp_path: Path, payload: dict, mode: str = "ok") -> dict:
        script = tmp_path / "page.js"
        script.write_text(_page_scripts("blockchain.html"), encoding="utf-8")
        payload_f = tmp_path / "payload.json"
        payload_f.write_text(json.dumps(payload), encoding="utf-8")
        harness = tmp_path / "harness.js"
        harness.write_text(_HARNESS, encoding="utf-8")
        proc = subprocess.run(
            [NODE, str(harness), str(ADMIN_JS), str(script), str(payload_f), mode],
            capture_output=True, text=True, encoding="utf-8", timeout=60,
        )
        assert proc.returncode == 0, f"node harness rc={proc.returncode}\n{proc.stderr}"
        return json.loads(proc.stdout)

    def test_verified_true_shows_pass_plus_disclosure(self, tmp_path: Path):
        out = self._run(tmp_path, {
            "verified": True,
            "record_id": "rec-1",
            "tx_hash": "tx-abc",
            "merkle_root": "root-1",
            "backend": {"backend": "pg_append_only", "backend_label": "本地防篡改追加日志（非联盟链，非区块链上链）",
                        "is_consortium_chain": False},
            "verification_note": "本地防篡改日志校验通过（非联盟链上链，见 backend 标注）",
        })
        html = out["els"]["#verify-result"]["innerHTML"]
        assert "HARNESS-ERROR" not in json.dumps(out["els"])
        assert "badge-success" in html and "通过" in html, html
        assert "本地防篡改追加日志" in html, "诚实披露 backend_label 必须上屏"
        assert "非联盟链上链" in html, "verification_note 必须上屏"

    def test_verified_false_shows_fail(self, tmp_path: Path):
        out = self._run(tmp_path, {
            "verified": False,
            "record_id": "rec-1",
            "tx_hash": "tx-abc",
            "merkle_root": "root-2",
            "backend": None,
            "verification_note": "未找到与该 tx_hash 匹配的锚定记录",
        })
        html = out["els"]["#verify-result"]["innerHTML"]
        assert "badge-danger" in html and "失败" in html, html
        assert "未找到与该 tx_hash 匹配的锚定记录" in html

    def test_request_failure_uses_fixed_copy(self, tmp_path: Path):
        out = self._run(
            tmp_path,
            {"detail": {"code": "HTTP_400", "message": "<img src=x onerror=alert(1)>"}},
            mode="fail",
        )
        blob = json.dumps(out, ensure_ascii=False)
        assert "onerror" not in blob, f"后端错误响应原文被透传上屏: {blob}"
        html = out["els"]["#verify-result"]["innerHTML"]
        assert "失败" in html, html
