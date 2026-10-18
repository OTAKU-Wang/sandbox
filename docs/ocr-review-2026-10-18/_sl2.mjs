// Slice the w3 edge-wave claims that the first edge verification pass did not cover.
const dir = process.env.TMP_OUT || "C:/Users/清彧/AppData/Local/Temp/ocr-cds";
const all = await Bun.file(dir + "/ocr-findings.json").json();
const judged = new Set([
  "cds-volume-plugin/plugin.go", "cds-volume-plugin/cipher.go", "cds-volume-plugin/server.go",
  "cds-volume-plugin/sm4gcm/sm4_gcm.c", "cds-volume-plugin/cli.go", "cds-volume-plugin/config.go",
  "cds-volume-plugin/kms.go", "cds-volume-plugin/layout.go", "cds-volume-plugin/rootfs.go",
  "cds-volume-plugin/main.go", "alembic/versions/0012_user_mfa.py", "cds-volume-plugin/selftest.go",
]);
const fence = "```";
const by = {};
for (const x of all) {
  if (!(x.scanPaths || "").includes("cds-volume-plugin")) continue;
  (by[x.path] ||= []).push(x);
}
const out = ["# w3 边缘波次 II（新出条目）", ""];
let n = 0;
let empty = 0;
for (const p of Object.keys(by).sort()) {
  if (judged.has(p)) continue;
  out.push("## " + p + " (" + by[p].length + ")");
  for (const x of by[p]) {
    n++;
    if (!(x.content || "").trim()) empty++;
    out.push("### [E2-" + n + "] " + (x.severity || "?") + "/" + (x.category || "?"));
    if ((x.existing || "").trim()) out.push("现码片段：\n" + fence + "\n" + x.existing.trim().slice(0, 500) + "\n" + fence);
    out.push((x.content || "").trim());
    if ((x.suggestion || "").trim()) out.push("建议：\n" + fence + "\n" + x.suggestion.trim().slice(0, 700) + "\n" + fence);
    out.push("");
  }
}
await Bun.write(dir + "claims-edge2.md", out.join("\n"));
console.log("new claims: " + n + "  empty-content: " + empty);
