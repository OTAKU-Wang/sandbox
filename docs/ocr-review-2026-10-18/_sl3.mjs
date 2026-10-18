// Delta since the E2 slice: w3-session claims that are not already in claims-edge2.md.
const dir = process.env.TMP_OUT || "C:/Users/清彧/AppData/Local/Temp/ocr-cds";
const all = await Bun.file(dir + "/ocr-findings.json").json();
const e2 = (await Bun.file(dir + "claims-edge2.md").text()) + "\n" + (await Bun.file(dir + "claims-edge.md").text());
const inE2 = (c) => e2.includes((c || "").trim().slice(0, 160));
const w3 = all.filter(x => (x.scanPaths || "").includes("cds-volume-plugin"));
const fence = "```";
const out = ["# w3 收尾增量（E2 切片之后新出的条目）", ""];
const by = {};
let n = 0;
for (const x of w3) {
  const key = (x.content || "").trim().slice(0, 160);
  if (!key || inE2(key)) continue;
  (by[x.path] ||= []).push(x);
  n++;
}
for (const p of Object.keys(by).sort()) {
  out.push("## " + p + " (" + by[p].length + ")");
  for (const x of by[p]) {
    out.push("### [" + x.path + "] " + (x.severity || "?") + "/" + (x.category || "?"));
    if ((x.existing || "").trim()) out.push("现码：\n" + fence + "\n" + x.existing.trim().slice(0, 400) + "\n" + fence);
    out.push((x.content || "").trim());
    out.push("");
  }
}
await Bun.write(dir + "claims-edge3.md", out.join("\n"));
console.log("delta claims: " + n + " across " + Object.keys(by).length + " files");
console.log(Object.keys(by).map(k => k + "=" + by[k].length).join(" "));
