// Slice frontend-wave claims (w4) for manual verification.
const dir = process.env.TMP_OUT || "C:/Users/清彧/AppData/Local/Temp/ocr-cds";
const all = await Bun.file(dir + "/ocr-findings.json").json();
const fence = "```";
const wave = process.argv[2] || "cds-frontend";
const rows = all.filter(x => (x.scanPaths || "").includes(wave));
const by = {};
for (const x of rows) (by[x.path] ||= []).push(x);
const out = ["# 前端波次待判清单（w4，截至收割时刻）", ""];
let n = 0;
let empty = 0;
for (const p of Object.keys(by).sort((a, b) => by[b].length - by[a].length || a.localeCompare(b))) {
  out.push("## " + p + " (" + by[p].length + ")");
  for (const x of by[p]) {
    n++;
    if (!(x.content || "").trim()) empty++;
    out.push("### [F-" + n + "] " + (x.severity || "?") + "/" + (x.category || "?"));
    if ((x.existing || "").trim()) out.push("现码：\n" + fence + "\n" + x.existing.trim().slice(0, 600) + "\n" + fence);
    out.push((x.content || "").trim());
    out.push("");
  }
}
await Bun.write(dir + "claims-frontend.md", out.join("\n"));
console.log("claims: " + n + "  empty-content: " + empty + "  files: " + Object.keys(by).length);
console.log(Object.entries(by).map(([k, v]) => k + "=" + v.length).join(" "));
