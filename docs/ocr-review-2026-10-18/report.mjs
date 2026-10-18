const print = (...a) => console.log(...a);
const SRC = "C:/Users/清彧/AppData/Local/Temp/ocr-cds/ocr-findings.json";
const DST = "C:/Users/清彧/AppData/Local/Temp/ocr-cds/ocr-findings.md";
const all = await Bun.file(SRC).json();

const order = { critical: 0, high: 1, medium: 2, low: 3 };
all.sort((a, b) => (order[a.severity] ?? 9) - (order[b.severity] ?? 9) || a.path.localeCompare(b.path));

const byFile = new Map();
for (const c of all) {
  if (!byFile.has(c.path)) byFile.set(c.path, []);
  byFile.get(c.path).push(c);
}

let md = "# ocr (OpenCodeReview) 扫描原始发现\n\n";
md += `来源：本机 \`ocr scan\`（provider qwen-local / Qwen3.8-Flash-Next）。共 ${all.length} 条去重后发现，按严重度分组；`;
md += `"锚点命中"= 评论里的 existing_code 能在当前工作区文件中逐字找到（防幻觉校验）。\n\n`;

const sev = {};
for (const c of all) sev[c.severity] = (sev[c.severity] || 0) + 1;
md += "严重度分布：" + Object.entries(sev).map(([k, v]) => `${k} ${v}`).join(" · ") + "\n\n";

for (const [path, list] of byFile) {
  md += `## ${path}（${list.length}）\n\n`;
  for (const c of list) {
    md += `### [${c.severity}/${c.category}] 锚点命中=${c.located}\n\n${c.content.trim()}\n\n`;
    if (c.existing) md += "```\n" + c.existing.trim() + "\n```\n\n";
    if (c.suggestion) md += "<details><summary>建议改法</summary>\n\n```\n" + c.suggestion.trim() + "\n```\n</details>\n\n";
  }
}
await Bun.write(DST, md);
print("written " + DST + "  (" + md.length + " chars, " + byFile.size + " files)");
print("\nunlocated findings:");
for (const c of all.filter(c => !c.located)) print(" - [" + c.severity + "] " + c.path + " :: " + c.content.split("\n")[0].slice(0, 120));
