import { existsSync } from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

// 换机修正：原先写死 `C:/Users/清彧/AppData/Local/Temp/ocr-cds`，离开那台 Windows 机直接 ENOENT。
// 本脚本与台账同目录，默认取脚本自身所在目录（台账真源）；跨目录试跑用 TMP_OUT 覆盖。
const dir = process.env.TMP_OUT || path.dirname(fileURLToPath(import.meta.url));
const ledger = await Bun.file(dir + "/ocr-verified-findings.md").text();
const lines = ledger.split("\n");

const TIERS = ["critical", "high", "medium-high", "medium", "low", "latent", "untagged"];
const findings = [];
const judgedIds = new Set();

for (let i = 0; i < lines.length; i++) {
  const m = lines[i].match(/^#{3,4} (A\d+)\s*[：:]?\s*(.*)$/);
  if (m) {
    let j = i + 1;
    const body = [];
    while (j < lines.length && !/^#{2,4} /.test(lines[j]) && !/^## /.test(lines[j])) body.push(lines[j++]);
    const title = m[2].trim();
    const paren = (title.match(/（([^）]*)）/g) || []).pop() || "";
    const sevMatch = paren.match(/medium-high|critical|high|medium|low|潜伏|latent|none/i);
    const sevRaw = sevMatch ? sevMatch[0].toLowerCase() : "untagged";
    const sev = sevRaw === "潜伏" ? "latent" : sevRaw;
    findings.push({ id: m[1], title, sev: TIERS.includes(sev) ? sev : "untagged", num: +m[1].slice(1), body: body.join("\n") });
    i = j - 1;
    continue;
  }
  const b = lines[i].match(/^#{3} (B-.*)$/);
  if (b) {
    let j = i + 1;
    const body = [];
    while (j < lines.length && !/^#{2,4} /.test(lines[j])) body.push(lines[j++]);
    for (const mm of (b[1] + "\n" + body.join("\n")).matchAll(/\bA\d{1,3}\b/g)) judgedIds.add(mm[0]);
    i = j - 1;
  }
}

// §D 里写「已修：#### A134 …」这类修复记录会被上面的标题正则当成一条新 finding，
// 同一个编号就会双计 ⇒ 总条目膚胀。按编号去重，保留首次出现（带真实标题/级别的那条）。
const seenFindingId = new Set();
for (let k = 0; k < findings.length; k++) {
  if (seenFindingId.has(findings[k].id)) { findings[k] = null; continue; }
  seenFindingId.add(findings[k].id);
}
for (let k = findings.length - 1; k >= 0; k--) if (findings[k] === null) findings.splice(k, 1);

const secD = ledger.slice(ledger.indexOf("## D. 修复状态"));
const fixed = new Set();
// §D 里也会写「这条还没修、下一轮修」——把这种行里的编号当成已修就是假绿。
// 已修 = 该行没有被显式标为「仍开着 / 不登记为已修 / 未判定完」。
const secDFixed = secD
  .split("\n")
  .filter((l) => !/仍开着|不登记为已修|未判定完|待下一轮/.test(l))
  .join("\n");
for (const mm of secDFixed.matchAll(/\bA\d{1,3}\b/g)) fixed.add(mm[0]);

const PATH = /(?:`|^|\s)((?:app|sdk|alembic|tests|scripts|policies|contracts|cds-frontend|cds-volume-plugin|deploy|k8s|helm|ci|docs|specs)\/[\w./-]*|[\w.-]+\.(?:py|tsx|ts|sql|go|yaml|yml|sh|json|toml|rego|sol))/;
const domainOf = (p) => {
  if (/^cds-frontend/.test(p) || /\.(tsx|ts)$/.test(p) && !/^(app|sdk)\//.test(p)) return p.startsWith("cds-volume-plugin") ? "plugin" : "fe";
  if (/^(deploy|k8s|helm|\.github|docker-compose|ci\/)/.test(p) || /docker-compose/.test(p)) return "deploy";
  if (/^(sdk\/|cds-volume-plugin)/.test(p)) return "plugin";
  if (/^(alembic|scripts|policies|contracts)/.test(p)) return "infra";
  return "backend";
};

const rows = [];
for (const f of findings) {
  const hit = (f.title + " " + f.body).match(PATH);
  const file = hit ? hit[1].replace(/^`/, "") : "(unresolved)";
  const dom = domainOf(file);
  const state = fixed.has(f.id) ? "fixed" : judgedIds.has(f.id) ? "judged-nondefect" : "open";
  rows.push({ ...f, file, dom, state });
}

const count = (pred) => rows.filter(pred).length;
const out = ["# 待修队列（自动生成自台账 + D 节已修清单）", ""];
out.push(`总条目=${rows.length} 已修=${count(r => r.state === "fixed")} 判为非缺陷=${count(r => r.state === "judged-nondefect")} 待修=${count(r => r.state === "open")}`, "");
const OPEN_TIERS = ["critical", "high", "medium-high", "medium", "low", "latent", "untagged"];
const DOMS = ["backend", "fe", "plugin", "infra", "deploy"];
out.push("| 域 | " + OPEN_TIERS.join(" | ") + " | 合计 |", "|---|" + OPEN_TIERS.map(() => "---").join("|") + "|---|");
for (const dm of DOMS) {
  const open = rows.filter(r => r.state === "open" && r.dom === dm);
  out.push(`| ${dm} | ` + OPEN_TIERS.map(t => open.filter(r => r.sev === t).length).join(" | ") + ` | ${open.length} |`);
}
out.push("");
for (const dm of DOMS) {
  for (const t of OPEN_TIERS) {
    const open = rows.filter(r => r.state === "open" && r.dom === dm && r.sev === t).sort((a, b) => a.file.localeCompare(b.file));
    if (!open.length) continue;
    out.push(`## ${dm} / ${t} (${open.length})`, "");
    for (const r of open) out.push(`- **${r.id}** \`${r.file}\` — ${r.title.slice(0, 110)}`);
    out.push("");
  }
}
const byFile = {};
for (const r of rows.filter(r => r.state === "open" && r.dom === "backend")) (byFile[r.file] ||= []).push(r.id + "/" + r.sev);
out.push("## 后端按文件聚合（给互斥修波用）", "");
Object.entries(byFile).sort((a, b) => b[1].length - a[1].length).slice(0, 40).forEach(([f, ids]) => out.push(`- \`${f}\` (${ids.length}) ${ids.join(" ")}`));
out.push("", "文件总数(后端待修)=" + Object.keys(byFile).length);

await Bun.write(dir + "/fix-queue.md", out.join("\n"));
console.log("queue written: open=" + count(r => r.state === "open") + " backend-open=" + count(r => r.state === "open" && r.dom === "backend") + " files=" + Object.keys(byFile).length);
