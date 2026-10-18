// Build coverage.json from `ocr scan --preview` outputs (hard coverage numbers).
const dir = process.env.TMP_OUT || "C:/Users/清彧/AppData/Local/Temp/ocr-cds";
const files = {
  "w1-api": "w1-prev.txt", "w2-services": "w2-prev.txt", "w3-edge": "w3-prev.txt",
  "w4-frontend": "w4-prev.txt", "w5-ops": "w5-prev.json",
};
const parse = (t) => {
  const discovered = +(/Preview: (\d+) file/.exec(t) || [])[1];
  const reviewing = +(/Will review \((\d+)\)/.exec(t) || [])[1];
  const skip = {}, skipExt = {}, reviewList = [];
  let inRev = false;
  for (const raw of t.split("\n")) {
    const l = raw.trim();
    if (/^Will review \(\d+\)/.test(l)) { inRev = true; continue; }
    const m = /\((\w+)\)\s*$/.exec(l);
    if (m) {
      skip[m[1]] = (skip[m[1]] || 0) + 1;
      const e = /(\.\w+)\s+\(\w+\)$/.exec(l);
      if (e) skipExt[e[1]] = (skipExt[e[1]] || 0) + 1;
      continue;
    }
    if (inRev) { const f = /^\[.\]\s+(\S+)/.exec(l); if (f) reviewList.push(f[1]); }
  }
  return { discovered, reviewing, skipped: discovered - reviewing, skip, skipExt, reviewList };
};
const out = {};
for (const [k, f] of Object.entries(files)) out[k] = parse(await Bun.file(dir + f).text());
const all = parse(await Bun.file(dir + "all-prev.txt").text());
const wavePrefix = ["app/api", "app/core", "app/utils", "app/schemas", "app/models", "app/services", "sdk", "cds-volume-plugin", "alembic", "contracts", "policies", "cds-frontend/src", "scripts", "deploy", "k8s", "helm", ".github"];
all.outsideWaves = all.reviewList.filter(p => !wavePrefix.some(w => p === w || p.startsWith(w + "/")));
all.reviewList = undefined;
out.ALL = all;
await Bun.write(dir + "coverage.json", JSON.stringify(out, null, 1));
console.log(Object.entries(out).map(([k, v]) => `${k}: ${v.discovered}/${v.reviewing} skip=${v.skipped}`).join("\n"));
console.log("ALL skip reasons: " + JSON.stringify(out.ALL.skip));
console.log("outside waves: " + out.ALL.outsideWaves.length);
