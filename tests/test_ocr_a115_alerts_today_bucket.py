"""OCR A115 — `app/templates/alerts.html` 的"今日告警"用 `new Date().toISOString().slice(0,10)`
（UTC 日）当"今日"前缀，同页时间列却按 `toLocaleString('zh-CN')`（浏览器本地时区）展示 ⇒
东八区 00:00–08:00 的告警被归到"昨天"；`created_at` 为空格分隔无时区串时前缀失配，
为 epoch 秒时更是直接抛 TypeError 让整页变成"加载失败"。台账见
docs/ocr-review-2026-10-18/ocr-verified-findings.md §A115。

本文件与 `tests/test_ocr_a241_admin_js_escaping.py` 同口径：node `vm` + 假 DOM + 假 `fetch`
**真跑** `admin.js` 与 alerts.html 的内联脚本；只把无参 `new Date()` 钉成固定时刻
（`new Date(x)` / `Date.parse` 仍走真实解析），TZ 由子进程环境给定（不碰本机设置）。
"""
import json
import re
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TypedDict

import pytest

REPO = Path(__file__).resolve().parents[1]
ADMIN_JS = REPO / "app" / "static" / "js" / "admin.js"
ALERTS_HTML = REPO / "app" / "templates" / "alerts.html"
NODE = shutil.which("node")

_HARNESS = r"""
'use strict';
const fs = require('fs');
const vm = require('vm');
const RealDate = Date;
const [adminPath, htmlPath, cfgPath] = process.argv.slice(2);
const cfg = JSON.parse(fs.readFileSync(cfgPath, 'utf8'));

class FixedDate extends RealDate {
  constructor(...args) { if (args.length === 0) { super(cfg.nowMs); } else { super(...args); } }
  static now() { return cfg.nowMs; }
}

const els = {};
function el(sel) {
  if (!els[sel]) els[sel] = { innerHTML: '', textContent: '', style: {} };
  return els[sel];
}

const sandbox = {
  Date: FixedDate,
  document: { querySelector: el, querySelectorAll: function () { return []; } },
  localStorage: { getItem: function () { return 'tok'; }, setItem: function () {} },
  fetch: async function () {
    return {
      ok: true, status: 200,
      text: async function () { return JSON.stringify(cfg.list); },
      json: async function () { return cfg.list; },
    };
  },
  console: console,
  setTimeout: setTimeout,
};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(adminPath, 'utf8'), sandbox, { filename: 'admin.js' });
const html = fs.readFileSync(htmlPath, 'utf8');
const m = html.match(/<script>([\s\S]*?)<\/script>/);
if (!m) { process.stdout.write('HARNESS-ERROR no inline <script> in alerts.html'); process.exit(0); }
vm.runInContext(m[1], sandbox, { filename: 'alerts.html' });

setTimeout(function () {
  process.stdout.write(JSON.stringify({
    active: els['#active-alerts'] ? els['#active-alerts'].textContent : null,
    critical: els['#critical-alerts'] ? els['#critical-alerts'].textContent : null,
    today: els['#today-alerts'] ? els['#today-alerts'].textContent : null,
    table: els['#alerts-table'] ? els['#alerts-table'].innerHTML : null,
    emptyShown: els['#alerts-empty'] ? els['#alerts-empty'].style.display || null : null,
  }));
}, 50);
"""


class PageState(TypedDict):
    active: int | None
    critical: int | None
    today: int | None
    table: str
    emptyShown: str | None


def _run(tmp_path: Path, now_ms: int, records: Sequence[Mapping[str, object]], tz: str) -> PageState:
    assert NODE, "node 不在 PATH — 行为门无法运行（不是通过，是缺条件）"
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({"nowMs": now_ms, "list": records}), encoding="utf-8")
    harness = tmp_path / "harness.js"
    harness.write_text(_HARNESS, encoding="utf-8")
    env = {"PATH": str(Path(NODE).parent), "TZ": tz}
    proc = subprocess.run(
        [NODE, str(harness), str(ADMIN_JS), str(ALERTS_HTML), str(cfg)],
        capture_output=True, text=True, encoding="utf-8", timeout=60, env=env,
    )
    assert proc.returncode == 0, f"node rc={proc.returncode}\n{proc.stderr}"
    assert proc.stdout.strip(), f"harness 无输出\n{proc.stderr}"
    raw = json.loads(proc.stdout)
    assert "加载失败" not in (raw.get("table") or ""), f"页面走进了异常分支: {raw['table']}"
    nums = {k: raw.get(k) for k in ("active", "critical", "today")}
    return PageState(
        **{k: (None if v is None else int(v)) for k, v in nums.items()},
        table=raw.get("table") or "", emptyShown=raw.get("emptyShown"),
    )


def _cells(table_html: str) -> list[str]:
    return re.findall(r"<tr>\s*<td>([^<]*)</td>", table_html)


@pytest.mark.skipif(not NODE, reason="node 不在 PATH")
def test_today_counts_local_day_not_utc_day(tmp_path):
    """东八区凌晨 00:30（UTC 仍是前一天）的两条今日告警都得计入，昨日那条不计。"""
    now = datetime(2026, 10, 20, 0, 30, tzinfo=timezone(timedelta(hours=8)))
    now_ms = int(now.timestamp() * 1000)
    records = [
        {"alert_type": "tz", "severity": "warning", "message": "iso-with-offset",
         "created_at": "2026-10-20T00:20:00+08:00"},
        {"alert_type": "naive", "severity": "critical", "message": "space-separated-naive",
         "created_at": "2026-10-20 09:15:00"},
        {"alert_type": "yesterday", "severity": "warning", "message": "local-yesterday",
         "created_at": "2026-10-19T23:50:00+08:00"},
    ]
    out = _run(tmp_path, now_ms, records, "Asia/Shanghai")
    assert out["today"] == 2, f"今日告警应按展示时区当日区间统计（应 2，得 {out['today']!r}）"
    assert out["critical"] == 1 and out["active"] == 3
    cells = _cells(out["table"])
    assert len(cells) == 3, cells
    assert sum(1 for c in cells if c.startswith("2026/10/20")) == out["today"], (
        f"今日统计必须与时间列显示的本地日一致: {cells} vs {out['today']}"
    )
    assert cells[2].startswith("2026/10/19"), f"昨天的行不该被算进今日: {cells[2]}"


@pytest.mark.skipif(not NODE, reason="node 不在 PATH")
def test_epoch_timestamps_are_bucketed_not_fatal(tmp_path):
    """数字型 created_at（epoch 秒 / 毫秒）得计入今日，且不能把整页打成“加载失败”。"""
    now = datetime(2026, 10, 20, 12, 0, tzinfo=timezone(timedelta(hours=8)))
    epoch_s = int(datetime(2026, 10, 20, 10, 0, tzinfo=timezone(timedelta(hours=8))).timestamp())
    late_ms = int(datetime(2026, 10, 20, 23, 59, tzinfo=timezone(timedelta(hours=8))).timestamp() * 1000)
    old_ms = int(datetime(2026, 10, 19, 20, 0, tzinfo=timezone(timedelta(hours=8))).timestamp() * 1000)
    out = _run(tmp_path, int(now.timestamp() * 1000), [
        {"alert_type": "epoch", "severity": "critical", "message": "epoch-seconds",
         "created_at": epoch_s},
        {"alert_type": "ms", "severity": "info", "message": "epoch-millis", "created_at": late_ms},
        {"alert_type": "ms-old", "severity": "info", "message": "yesterday-millis",
         "created_at": old_ms},
    ], "Asia/Shanghai")
    assert out["today"] == 2, (
        f"epoch 秒/毫秒应按本地当日区间归档: {out['today']!r} / {_cells(out['table'])}"
    )
    assert _cells(out["table"])[2].startswith("2026/10/19"), out["table"]


@pytest.mark.skipif(not NODE, reason="node 不在 PATH")
def test_today_boundary_is_the_local_midnight(tmp_path):
    """UTC 时区下当日 23:45 计入、次日 00:10 不计入（当日上界必须是本地午夜）。"""
    now = datetime(2026, 10, 20, 23, 30, tzinfo=timezone.utc)
    records = [
        {"alert_type": "late", "severity": "warning", "message": "23:45Z",
         "created_at": "2026-10-20T23:45:00+00:00"},
        {"alert_type": "tomorrow", "severity": "warning", "message": "00:10Z next day",
         "created_at": "2026-10-21T00:10:00+00:00"},
        {"alert_type": "naive", "severity": "info", "message": "naive local",
         "created_at": "2026-10-20T23:50:00"},
    ]
    out = _run(tmp_path, int(now.timestamp() * 1000), records, "UTC")
    assert out["today"] == 2, f"跨本地午夜的边界算错: {out['today']!r} / {_cells(out['table'])}"


@pytest.mark.skipif(not NODE, reason="node 不在 PATH")
def test_empty_list_and_no_records(tmp_path):
    """空列表：走空态分支，统计不被写成脏值。"""
    now = datetime(2026, 10, 20, 8, 0, tzinfo=timezone(timedelta(hours=8)))
    out = _run(tmp_path, int(now.timestamp() * 1000), [], "Asia/Shanghai")
    assert out["emptyShown"] == "block"
    assert out["today"] is None
