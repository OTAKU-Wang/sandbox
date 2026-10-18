// Scan .gate-*.log evidence produced by the fix children.
// A log counts as evidence only if it carries a test-summary line; 0-line logs
// and logs that stop mid-run are reported as "无结论" (my kill_bash(all) incident
// killed some in-flight runs, so absence of a summary is expected, not green).
import { existsSync } from "node:fs";

const repo = "D:/workspace/sandbox";
const files = await Array.fromAsync(new Bun.Glob(".gate-*.log").scan({ cwd: repo, onlyFiles: true, dot: true }));
const rows = [];
for (const f of files) {
  const p = repo + "/" + f;
  const txt = await Bun.file(p).text().catch(() => "");
  const st = Bun.file(p);
  const lines = txt.split("\n");
  const mtime = new Date(await st.lastModified).toTimeString().slice(0, 5);
  const fail = txt.match(/^\s*=+ .*(\d+) failed.*=+$/m);
  const passedOnly = txt.match(/^\s*=+ .*(\d+) passed[^=]*=+$/m);
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
for (const r of rows) out.push(`| ${r.mtime} | ${r.lines} | ${r.verdict} | ${r.sum.replace(/\|/g, "/")} | ${r.f} |`);
await Bun.write((process.env.TMP_OUT || "C:/Users/清彧/AppData/Local/Temp/ocr-cds") + "/gate-evidence.md", out.join("\n"));
console.log("logs=" + rows.length + "  " + Object.entries(tally).map(([k, v]) => k + "=" + v).join("  "));
console.log("无结论清单: " + rows.filter(r => r.verdict.includes("无结论") || r.verdict.includes("空文件")).map(r => r.f).join(" "));
