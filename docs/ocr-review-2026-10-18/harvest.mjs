const print = (...a) => console.log(...a);
const write = async (p, c) => { const q = p.startsWith("local://") ? process.env.TMP_OUT + "/" + p.slice(8) : p; await Bun.write(q, c); return q; };
const SD = process.env.OCR_SESSIONS || "C:/Users/清彧/.opencodereview/sessions/D_workspace-sandbox";
const REPO = process.env.OCR_REPO || "D:/workspace/sandbox";

const files = await Array.fromAsync(new Bun.Glob("*.jsonl").scan({ cwd: SD, onlyFiles: true }));
const raw = [];
const sessions = [];

for (const f of files.sort()) {
  const text = await Bun.file(SD + "/" + f).text();
  let start = null;
  for (const line of text.split(/\r?\n/)) {
    if (!line.trim()) continue;
    let r; try { r = JSON.parse(line); } catch { continue; }
    if (r.type === "session_start") start = r;
    if (r.type === "tool_call" && r.tool_name === "code_comment" && r.ok) {
      let a; try { a = JSON.parse(r.arguments); } catch { continue; }
      for (const c of (a && a.comments) || []) {
        raw.push({
          session: r.sessionId, scanPaths: start ? (start.scanPaths || []).join(",") : "",
          path: c.path || r.filePath, existing: c.existing_code || "", content: c.content || "",
          suggestion: c.suggestion_code || "", category: c.category || "", severity: c.severity || "",
          ts: r.timestamp || (start ? start.timestamp : ""),
        });
      }
    }
  }
  const touched = new Set();
  for (const line of text.split(/\r?\n/)) {
    const m = /"filePath":"([^"]+)"/.exec(line);
    if (m) touched.add(m[1]);
  }
  sessions.push({ id: f.slice(0, 8), paths: start ? (start.scanPaths || []).join(",") : "?", touched: touched.size });
}

const norm = (s) => s.replace(/\r\n/g, "\n").replace(/[ \t]+/g, " ").replace(/\s+$/gm, "").trim();
const seen = new Map();
for (const c of raw) {
  const key = c.path + "::" + norm(c.existing).slice(0, 200) + "::" + norm(c.content).slice(0, 90);
  if (!seen.has(key)) seen.set(key, c);
}

const diskCache = new Map();
const diskText = async (p) => {
  if (!diskCache.has(p)) diskCache.set(p, await Bun.file(REPO + "/" + p).text().catch(() => null));
  return diskCache.get(p);
};

const out = [];
for (const c of seen.values()) {
  const t = await diskText(c.path);
  const anchor = norm(c.existing);
  c.located = t != null && anchor.length > 0 && norm(t).includes(anchor);
  c.fileExists = t != null;
  out.push(c);
}

const rank = { critical: 0, high: 0, blocker: 0, major: 1, medium: 2, minor: 3, low: 4, info: 5 };
out.sort((a, b) => (rank[a.severity.toLowerCase()] ?? 9) - (rank[b.severity.toLowerCase()] ?? 9) || a.path.localeCompare(b.path));

const sevCount = {}, catCount = {};
for (const c of out) { sevCount[c.severity] = (sevCount[c.severity] || 0) + 1; catCount[c.category] = (catCount[c.category] || 0) + 1; }

print("sessions: " + sessions.map(s => `${s.id}[${s.paths}] touched=${s.touched}`).join("  "));
print("raw=" + raw.length + " unique=" + out.length + " located_on_disk=" + out.filter(c => c.located).length);
print("severity: " + JSON.stringify(sevCount));
print("category: " + JSON.stringify(catCount));

await write("local://ocr-findings.json", JSON.stringify(out, null, 1));
print("written local://ocr-findings.json");
for (const c of out) print(`[${c.severity}/${c.category}] ${c.path} located=${c.located} :: ${c.content.split("\n")[0].replace(/[#*`]/g, "").slice(0, 110)}`);
