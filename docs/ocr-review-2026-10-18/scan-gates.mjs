// Scan .gate-*.log evidence produced by the fix children.
// A log counts as evidence only if it carries a test-summary line; 0-line logs
// and logs that stop mid-run are reported as "无结论" (my kill_bash(all) incident
// killed some in-flight runs, so absence of a summary is expected, not green).
import { existsSync } from "node:fs";
// 本脚本用 `Bun.Glob` / `Bun.file`，只能由 bun 跑。交接件里写的入口是 `node …`，
// 直接拿 node 跑会在 import 阶段抛一个与证据无关的栈 ⇒ 先诚实地说清楚该用哪个运行时。
if (!globalThis.Bun) {
  console.error("scan-gates.mjs 需要 bun 运行（用了 Bun.Glob/Bun.file）：bun docs/ocr-review-2026-10-18/scan-gates.mjs");
  process.exit(2);
}
import { mkdir } from "node:fs/promises";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

// 仓库根从脚本自身位置推导（写死 `D:/workspace/sandbox` 在换机后直接 ENOENT）；
// 需要跨目录扫描时用 CDS_REPO_ROOT 覆盖。运行器是 bun（用了 Bun.Glob / Bun.file）。
const HERE = path.dirname(fileURLToPath(import.meta.url));
const repo = process.env.CDS_REPO_ROOT || path.resolve(HERE, "..", "..");
const files = await Array.fromAsync(new Bun.Glob(".gate-*.log").scan({ cwd: repo, onlyFiles: true, dot: true }));
// 第二轮（10-19）起，改前/改后读数不再只落在仓库根的点文件（那些是机器本地的，
// 换机即丢）——它们入库存放在台账目录 `gate-logs/`。不同步扇描它，重生出来的
// 「门证据清单」就会在新机器上报 `logs=0`（实测踩到），等于证据存在但不可核对。
const logDir = process.env.GATE_LOG_DIR || path.join(HERE, "gate-logs");
if (existsSync(logDir)) {
  for (const f of await Array.fromAsync(new Bun.Glob("*.log").scan({ cwd: logDir, onlyFiles: true }))) {
    files.push(path.relative(repo, path.join(logDir, f)));
  }
}
files.sort();
const rows = [];
for (const f of files) {
  const p = repo + "/" + f;
  const raw = await Bun.file(p).text().catch(() => "");
  // vitest/pytest 都会吐 ANSI 颜色码，不剥掉则 `Test Files  12 passed` 这类汇总行
  // 在码序列中间断开，分类器会把它误判成「无结论」（10-19 实测踩到）。
  const txt = raw.replace(/\x1b\[[0-9;]*m/g, "");
  const st = Bun.file(p);
  const lines = txt.split("\n");
  // 只用 HH:MM 在 180+ 份跨日日志里不可判读（且 git checkout 会重写 mtime），带上日期。
  const mtime = new Date(await st.lastModified).toISOString().slice(0, 16).replace("T", " ");
  // 两个形式都认：带 `====` 横幅的 pytest 原样输出，以及人工归档的**裸汇总行**
  // （`5 failed, 322 passed, …`）与车道拼接行（`ss15   94 passed, …`）。
  const fail = txt.match(/^\s*(?:\S+\s+)?=*\s*\d+ failed\b.*$/m);
  const passedOnly = txt.match(/^\s*(?:\S+\s+)?=*\s*\d+ passed[^\n=]*$/m);
  const collectErr = /error in /m.test(txt) || /INTERNALERROR/.test(txt);
  const vitestFail = /Test Files\s+\d+ failed/.test(txt);
  const vitestPass = /Test Files\s+[\d ]+passed/.test(txt);
  const tsc = /\berror TS\d+/.test(txt);
  const truncated = /Killed|Traceback \(most recent call last\):.*\Z/s.test(txt) && !fail && !passedOnly;
  const rcs = [...txt.matchAll(/(?:^|\s)([A-Za-z0-9_]*RC)=(\d+)\s*$/gm)];
  const rcLast = rcs.length ? { k: rcs[rcs.length - 1][1], n: +rcs[rcs.length - 1][2] } : null;
  const nameMismatch = /green|after/.test(f) && rcLast && rcLast.n !== 0 ? "名字说绿、RC 非 0" : /red|before/.test(f) && rcLast && rcLast.n === 0 ? "名字说红、RC 为 0" : "";
  let verdict, sum;
  if (lines.filter(Boolean).length === 0) { verdict = "空文件（无结论）"; sum = "-"; }
  else if (fail || vitestFail) { verdict = "红（有失败明细）"; sum = (fail ? fail[0] : txt.match(/Test Files.*/)?.[0] || "").trim().slice(0, 90); }
  else if (passedOnly || vitestPass) { verdict = "绿（有通过汇总）"; sum = (passedOnly ? passedOnly[0] : (txt.match(/Test Files.*/)?.[0] || "")).trim().slice(0, 90); }
  else if (tsc) { verdict = "红（tsc）"; sum = (txt.match(/error TS\d+.*/)?.[0] || "").slice(0, 90); }
  else if (rcLast !== null) { verdict = rcLast.n === 0 ? "绿（RC=0）" : "红（RC=" + rcLast.n + "）"; sum = (rcLast.k + "=" + rcLast.n + (passedOnly ? " / " + passedOnly[0].trim().slice(0, 60) : "")); }
  else if (/tsc/.test(f) && !tsc) { verdict = "绿（tsc 无 error TS）"; sum = "0 条 error TS"; }
  else if (/lint|eslint/.test(f) && !/problem|error  /.test(txt)) { verdict = "绿（lint 无告警）"; sum = "no problems"; }
  else if (/no tests ran|ERROR: file or directory not found|collected 0 items/.test(txt)) { verdict = "无结论（没采到用例）"; }
  else { verdict = "无结论（无汇总行）"; sum = lines.filter(Boolean).slice(-1)[0]?.slice(0, 90) || "-"; }
  if (nameMismatch) verdict += " ⚠" + nameMismatch;
  rows.push({ f, mtime, lines: lines.filter(Boolean).length, verdict, sum });
}
rows.sort((a, b) => a.mtime.localeCompare(b.mtime));
const tally = {};
rows.forEach(r => tally[r.verdict] = (tally[r.verdict] || 0) + 1);
const out = ["# 门证据扫描（自动生成，只认日志里的汇总行）", "", `日志数=${rows.length}  ` + Object.entries(tally).map(([k, v]) => `${k}=${v}`).join("  "), "", "| 时间 | 行数 | 判定 | 汇总行 | 文件 |", "|---|---|---|---|---|"];
for (const r of rows) out.push(`| ${r.mtime} | ${r.lines} | ${r.verdict} | ${(r.sum || "").replace(/\n/g, " ⏎ ").replace(/\|/g, "/")} | ${r.f} |`);
// 输出目录：默认写到仓库外的 .gate-scratch/，绝不默认落在台账目录——那里
// `gate-evidence.md` 是**已提交的上一轮证据**，默认覆盖等于销毁证据（10-19 实测踩到）。
const outDir = process.env.TMP_OUT || path.join(repo, ".gate-scratch");
// 默认产物名保持 `gate-evidence.md`；指定 OUT_NAME 可写本轮清单而不碰上一轮已提交的同名件。
const outName = process.env.OUT_NAME || "gate-evidence.md";
await mkdir(outDir, { recursive: true });
await Bun.write(outDir + "/" + outName, out.join("\n"));
console.log("wrote " + outDir + "/" + outName);
console.log("logs=" + rows.length + "  " + Object.entries(tally).map(([k, v]) => k + "=" + v).join("  "));
console.log("无结论清单: " + rows.filter(r => r.verdict.includes("无结论") || r.verdict.includes("空文件")).map(r => r.f).join(" "));
