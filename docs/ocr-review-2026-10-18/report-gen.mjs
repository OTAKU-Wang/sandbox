// Merge the verification ledger into a severity-ranked review report.
// The report is a *view* of ocr-verified-findings.md: every A## entry is carried
// verbatim, so no finding can be dropped by re-summarising.
import { existsSync } from "node:fs";

const dir = process.env.TMP_OUT || "C:/Users/清彧/AppData/Local/Temp/ocr-cds";
const ledger = await Bun.file(dir + "/ocr-verified-findings.md").text();
const lines = ledger.split("\n");

const TIERS = ["critical", "high", "medium-high", "medium", "low", "latent", "untagged"];
const findings = [];
const judged = [];

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
    findings.push({ id: m[1], title, sev: TIERS.includes(sev) ? sev : "untagged", num: parseInt(m[1].slice(1), 10), body: body.join("\n").trim() });
    i = j - 1;
    continue;
  }
  const b = lines[i].match(/^#{3} (B-.*)$/);
  if (b) {
    let j = i + 1;
    const body = [];
    while (j < lines.length && !/^#{2,4} /.test(lines[j])) body.push(lines[j++]);
    judged.push({ head: b[1], body: body.join("\n").trim() });
    i = j - 1;
  }
}

const waves = [
  ["w1-api", "app/api, app/core, app/utils, app/schemas, app/models, app/templates, app/static"],
  ["w2-services", "app/services"],
  ["w3-edge", "sdk, cds-volume-plugin, alembic, contracts, policies"],
  ["w4-frontend", "cds-frontend/src"],
  ["w5-ops", "scripts, deploy, k8s, helm, .github"],
];
const status = waves.map(([n, scope]) => [n, scope, existsSync(`${dir}/${n}.json`) ? "已完成" : existsSync(`${dir}/${n}.log`) ? "进行中" : "未开始"]);

const counts = {};
for (const f of findings) counts[f.sev] = (counts[f.sev] || 0) + 1;

const out = [];
const ledgerItems = (ledger.match(/^## A-/gm) || []).length;
const minId = Math.min(...findings.map(f => f.num));
const maxId = Math.max(...findings.map(f => f.num));
out.push("# CDS 代码评审报告（本机 ocr / OpenCodeReview v1.13.1）\n");
out.push("> 本报告是 `ocr-verified-findings.md`（复核台账）的**排序视图**：每条 A## 结论逐字搬运，未做二次概括。");
out.push("> 台账按批次追加（当前共 " + ledgerItems + " 个 A 批次、A" + minId + "→A" + maxId + "，其中 A68/A107 已并入邻条），报告按严重度重排。判定为「非缺陷 / 高估 / 潜在」的条目集中在文末。\n");

out.push("## 1. 覆盖范围\n");
out.push("| 波次 | 范围 | 状态 | ocr 计划评审 | 其中产出了发现 |");
out.push("|---|---|---|---|---|");
const plan = existsSync(dir + "/wave-plan.json") ? await Bun.file(dir + "/wave-plan.json").json() : {};
{
  const allRaw = await Bun.file(dir + "/ocr-findings.json").json();
  const waveOf = (sp) => {
    if (!sp) return null;
    if (sp === "app") return "pilot";
    if (sp.startsWith("app/api")) return "w1-api";
    if (sp === "app/services") return "w2-services";
    if (sp.includes("cds-volume-plugin")) return "w3-edge";
    if (sp.includes("cds-frontend")) return "w4-frontend";
    if (sp.includes("deploy") || sp.includes("helm")) return "w5-ops";
    return null;
  };
  const produced = {};
  for (const x of allRaw) { const w = waveOf(x.scanPaths); (produced[w] ||= new Set()).add(x.path); }
  for (const [n, scope, st] of status) {
    const p = plan[n] || {};
    const rv = p.reviewing ?? "—";
    const pr = produced[n] ? produced[n].size : 0;
    out.push(`| ${n} | ${scope} | ${st} | ${rv}${p.discovered ? `（发现 ${p.discovered}）` : ""} | ${pr} |`);
  }
  out.push("| _首轮试点_ | app（已并入 w1/w2 口径） | 已完成 | — | " + (produced.pilot ? produced.pilot.size : 0) + " |");
}
out.push("");
out.push("**全程排除**：`Tongsuo/`、`cds-volume-plugin/tongsuo-build/`、`**/node_modules/**`、`app/_stubs/**`、`**/*.pb2.py`、`**/dist/**`、`**/*.min.js`、`**/*.map`、`**/*.png`（前两者是 vendored 上游国密库，第三四是构建产物与 protoc 产物）。");
out.push("");

if (existsSync(dir + "/coverage.json")) {
  const cov = await Bun.file(dir + "/coverage.json").json();
  const A = cov.ALL;
  out.push("**可评审性实测（`ocr scan --preview`，不跑模型，只让工具自己报它会评哪些文件）**：全仓发现 " + A.discovered + " 个文件，ocr 判为可评审 " + A.reviewing + "（" + Math.round(A.reviewing / A.discovered * 100) + "%），跳过 " + A.skipped + "。跳过原因逐项：" + Object.entries(A.skip).map(([k, v]) => "`" + k + "` " + v).join("、") + "。");
  out.push("");
  out.push("");
  out.push("### 1.2 仍未覆盖（逐条列出，不写「大概」）\n");
  out.push("1. **ocr 根本读不了的类型**：`unsupported_ext` " + (A.skip.unsupported_ext || 0) + " 个文件（其中 `.md` " + ((A.skipExt[".md"] || 0)) + "，其余含 `.tpl` / `.service` / `.conf` / `.template` / `.txt` / `.mako` / `.mod` / `.sum` / `.html`），例：`helm/cds/templates/_helpers.tpl`、`deploy/cube-node/audit-tailer.service`、`deploy/cube-node/cds.env.template`、`scripts/cube-contract-paths.txt`。**本次评审对这些文件没有任何结论**。");
  out.push("2. **测试文件**：`default_path` " + (A.skip.default_path || 0) + " 个被 ocr 默认跳过（`tests/**` 与 `cds-frontend/**/__tests__/**` 共 46 个前端用例）。按项目规则 `tests/` 是叶子（不反向 import 任何生产代码），本次不把它当缺陷来源，但也确实**没评**。评审过程中从生产代码倒推出来的测试盲区（如 A204② 的假绿门）已单独记账。");
  out.push("3. **超大与密钥类**：`too_large` " + (A.skip.too_large || 0) + " 个（`specs/prototype.html`，原型文档非代码）、`secret_exclude` " + (A.skip.secret_exclude || 0) + " 个（`.env.prod.example`）、`binary` " + (A.skip.binary || 0) + " 个（图片）。另 `user_exclude` " + (A.skip.user_exclude || 0) + " 个是本次显式排除的 vendored 国密库与构建产物。");
  const w6j = existsSync(dir + "/w6-residue.json");
  let w6txt = "";
  if (w6j) {
    const a6 = ((await Bun.file(dir + "/w6-residue.json").json()).comments) || [];
    const c6 = a6.filter(x => x.severity === "critical").length;
    const h6 = a6.filter(x => x.severity === "high").length;
    const f6 = new Set(a6.map(x => x.path)).size;
    w6txt = "扫描完成并收割（" + a6.length + " 条 / " + f6 + " 文件；critical " + c6 + "、high " + h6 + "）。台账已并入其中的 Jinja 管理台与 prototype-v3 部分（A241/A242）；" + (c6 + h6) + " 条 critical/high 里，compose/启动装配面 12 条已逐行判完 → 台账 A283-A286（含 1 条 critical）；完成时快照被重写一次（135→128 条、行号普遍位移，报告不引用其行号），补判最终快照新增项 → A287-A289。";
    w6txt += "剩余 5 条（docker-compose.middleware.yml 的 Kafka/Vault/CDC 三条、alembic.ini、tests/conftest.py）**只读到、未定级**；w6 的 medium/low 共 " + (a6.length - c6 - h6) + " 条未逐条复核（清单文件 claims-w6.txt）。";
  }
  out.push("4. **五波路径之外的 " + A.outsideWaves.length + " 个可评审文件**（包含启动装配入口 `app/main.py`）——已单开 **w6-residue** 波次补扫：" + (w6j ? w6txt : "**扫描中**（`--concurrency 4`，日志 `w6-residue.log`）；本节与台账的最终版以它跑完为准。") + "清单：" + A.outsideWaves.slice(0, 24).map(p => "`" + p + "`").join("、") + (A.outsideWaves.length > 24 ? " 等" : "") + "。");
    out.push("6. **非代码工件**：w6 补漏扫到 `specs/prototype-v3.html` 产出 29 条（它是已被 `基础版数据沙箱原型/` 图片取代的原型基线文档，AGENTS.md 记为 superseded），**按文档处理、不作缺陷入账**；同理 `cds-frontend/index.html` 的模板注释类。构建配置类（`cds-frontend/package.json` 11 条、`eslint.config.js` 12 条、`tsconfig.*.json`、`alembic.ini` 8 条）尚未逐条判读，列在本节未完成里。");
    out.push("5. **前端只判到高危层，中低危与 w5 运维面仍未复验**。CSS 类 72 条判完（台账批次五十七~六十六：64 成立 / 3 FALSE / 5 潜伏）；逻辑类 critical/high 73 条逐条读到盘上（台账 A243-A272，含 2 条 critical），中低危 373 条**只切片未逐条复验**，构建配置类（`cds-frontend/eslint.config.js` 12 条、`package.json` 11 条、`tsconfig.*`、`alembic.ini` 8 条）**未判**；w5（`scripts` / `deploy` / `k8s` / `helm` / `.github`，预检 71 发现 / 59 可评审）仍在 w4 后面排队，**这一路一条都没评**。");
              {
    const raw = await Bun.file(dir + "/ocr-findings.json").json();
    const w4all = raw.filter(x => x.path.startsWith("cds-frontend"));
    const isLegacy = x => x.path.includes("app/templates") || x.path.includes("admin.js");
    const skip = x => x.path.endsWith(".css") || /package[.]json|eslint[.]config|tsconfig|prototype-v3/.test(x.path);
    const logic = w4all.filter(x => !isLegacy(x) && !skip(x));
    const hi = logic.filter(x => x.severity === "critical" || x.severity === "high").length;
    const ml = logic.length - hi;
    const css = w4all.filter(x => x.path.endsWith(".css")).length;
    out.push("5b. **w4 逻辑类判读进度（数字在生成时从 ocr-findings.json 现算，不是手抄）**：前端波次已收割 " + w4all.length + " 条，其中 CSS " + css + " 条已整批改判完（台账批次五十七~六十六；w4 已结束，该数字冻结）、遗留管理台 116 条收敛为 A241/A242；扣掉构建配置后逻辑类 " + logic.length + " 条（w4-frontend.json 已落盘并收割；raw unique 未增长，因为此前是从日志增量收割、同一批条目已入库）：critical/high " + hi + " 条**已全部逐条读到盘上**（台账 A243-A276，含 2 条 critical：A243 数据面横幅双重解包、A268 MPC 分片读侧无门），medium/low " + ml + " 条**刚开始判**——已整文件判完 services/identityApi.ts（11）、pages/Audit/AuditLog.tsx（10）、pages/Auth/Login.tsx（8）、services/auditApi.ts（9）、services/federationApi.ts（9）、components/Audit/AuditVerifyModal.tsx（8）、components/Feedback/ErrorCard.tsx（7）、services/sandboxApi.ts（8）、utils/workspace.ts（7）、components/Layout/DataplaneBanner.tsx（5）、components/Feedback/QueryFeedback.tsx（5）、services/mpcApi.ts（5）→ 台账 A273-A300（92/本清单条数会随 w4 增量变化，见 claims-fe-med.txt 首行），另含 2 条由前端切片挖出的后端问题 A268/A279；其余仅切片未复验。");
  }  out.push("");
}

{
  const allRaw = await Bun.file(dir + "/ocr-findings.json").json();
  const byWave = {};
  for (const x of allRaw) { const k = x.scanPaths || "?"; (byWave[k] ||= new Set()).add(x.path); }
  out.push("**覆盖计量（只算 ocr 真正产出了发现的文件，不是它读过的文件数）**：原始发现 " + allRaw.length + " 条，分布 —— " + Object.entries(byWave).map(([k, v]) => `\`${k}\` → ${v.size} 文件`).join("；") + "。ocr 还会 `file_read` 大量邻居文件（包含 `tests/`）但不产出发现，所以本表是**保守下界**；未被覆盖的文件不表示安全，只表示本次未产出。");
  out.push("");
}


{
  const c = ledger.match(/^## C\. 仍未复核 \/ 未覆盖\n([\s\S]*?)^## D\./m);
  if (c) out.push("### 1.0 覆盖与限制的最新口径（逐字搬运台账 C 节，与批次叙述冲突时以本节为准）\n", c[1].trim(), "");
}
out.push("### 1.1 复验方法与一手程度（每条怎么被确认的）\n");
{
  const batches = ledger.split("\n");
  const batchOf = {};
  let cur = "(序言)";
  for (const f of findings) batchOf[f.id] = "";
  {
    let i2 = 0;
    const ids = [];
    for (const f of findings) ids.push(f.id);
    for (let li = 0; li < batches.length; li++) {
      const m = batches[li].match(/^#{3,4} (A\d+)\s*[：:]?/);
      if (m && batchOf[m[1]] !== undefined) batchOf[m[1]] = cur;
      const h = batches[li].match(/^## (A-[^:\n]*)/);
      if (h) cur = h[1];
    }
  }
  const buckets = { "我亲验 / 我逐行复验（标题明写）": 0, "坐标来自子代理的逐条复验（批次或标题标明）": 0, "正文含实测/探针证据（RC=、探针输出、像素/打包实测）": 0, "我逐行回读现码 + 追调用方": 0 };
  const first = [], sub = [];
  for (const f of findings) {
    const t2 = f.title, b = f.body || "";
    if (/我亲验|我逐行复验/.test(t2)) { buckets["我亲验 / 我逐行复验（标题明写）"]++; first.push(f.id); }
    else if (/子代理/.test(t2) || /子代理/.test(batchOf[f.id] || "")) { buckets["坐标来自子代理的逐条复验（批次或标题标明）"]++; sub.push(f.id); }
    else if (/实测|探针|RC=|复量/.test(t2 + b)) buckets["正文含实测/探针证据（RC=、探针输出、像素/打包实测）"]++;
    else buckets["我逐行回读现码 + 追调用方"]++;
  }
  out.push("| 复验方式 | 条数 |");
  out.push("|---|---|");
  for (const [k, v] of Object.entries(buckets)) out.push("| " + k + " | " + v + " |");
  out.push("");
  out.push("读法：第一类 " + first.length + " 条（" + first.join(", ") + "）是我本轮逐行复过的。第二类的行号来自子代理（每个子代理都被要求用 `nl -ba` 取真实行号、机审自己的引用、并把仓库改动清零），**我没有逐条再看一遍** —— 用之前值得自己抽几条。第三类带可复跑的探针输出。第四类是台账里逐行回读现码再建立的危害链。");
  out.push("");
  out.push("**抽样复核（本轮做的，不是承诺）**：从 79 条子代理坐标里按序抽 8 条 —— A96 / A105 / A138 / A147 / A167 / A184 / A193 / A211，逐条回读盘上原文，**8/8 命中的行就是结论所引的那一行**。例：A193 指到 output_inspection.py:306 的 stage_results[SIGNATURE] = bool(sig)，紧接 except Exception: 与「Fallback to SM3 hash」注释；A211 指到 alembic/env.py:35 的 config.set_main_option(...)。抽样里同时看得到我自己的降级痕迹：A96 子代理原话是「今天就在跑」，我按 BwrapAdapter 只在 legacy 门后注册（sandbox_runtime.py:2139）改成「dev/e2e 开 L3 时可达」。其余 71 条仍是子代理口径，没有逐条复读。");
  out.push("");
}

out.push("## 2. 工具限制（影响结论口径）\n");
{
  let seAll = 0, tAll = 0, csAll = 0;
  const dead = new Set();
  out.push("### 2.0 扫描器自己的失败账（生成时从日志现算）");
  out.push("");
  out.push("| 波次 | 子任务报错（该批次零产出） | LLM 超时次数 | code_search 失败 |");
  out.push("|---|---|---|---|");
  for (const name of ["w1-api", "w2-services", "w3-edge", "w4-frontend", "w6-residue"]) {
    let log = "";
    try { log = await Bun.file(dir + "/" + name + ".log").text(); } catch (e) { continue; }
    const se = (log.match(/Scan subtask error/g) || []).length;
    const tt = (log.match(/LLM request timeout/g) || []).length;
    const cs = (log.match(/code_search failed/g) || []).length;
    for (const m of log.matchAll(/Scan subtask error for (\S+) \(batch #(\d+)\)/g)) dead.add(m[1]);
    seAll += se; tAll += tt; csAll += cs;
    out.push("| " + name + " | " + se + " | " + tt + " | " + cs + " |");
  }
  out.push("");
  out.push("**合计 " + seAll + " 个 scan 子任务因 LLM 超时报错（provider 侧共 " + tAll + " 次 timeout），这些批次没有产出任何发现** —— ocr 的覆盖不是均匀的。出现过报错的文件/批次：" + [...dead].slice(0, 30).join(", ") + (dead.size > 30 ? " 等 " + dead.size + " 个" : "") + "（同批可能重试成功，所以出现过报错不等于完全没扫到，但它属于 §1.2 未覆盖清单。）");
  out.push("");
  out.push("- **code_search 全军覆没的实测计数：合计 " + csAll + " 次**，报错原文一律是 " + String.fromCharCode(96) + "git grep failed: exit status 129: error: unknown option 'max-count'" + String.fromCharCode(96) + "（本机 git 2.33.1）。=> ocr 每条结论只建立在它 file_read 到的片段上，没有任何跨文件检索能力，这正是本评审必须逐条人工回读现码的原因。");
  out.push("");
}
out.push("- **标题定级口径**：标题里的严重度按该条目**活路径那一部分**定级。另有“正文里确实存在潜伏成分、但整条未定为潜伏”的条目（它们同时有活的调用方或活的诱饵面）：" + (() => { const body = ledger; const parts = [...body.matchAll(/^### (A\d+) ([^\n]+)\n([\s\S]*?)(?=^### A\d+ |^## [ABC]|(?![\s\S]))/gm)]; const list = []; for (const [, id, title, bd] of parts) { if (/（[^）]*(latent|潜伏)[^）]*）/.test(title)) continue; if (/潜伏|LATENT|零导入方|无导入方|零调用方|不可达/.test(bd)) list.push(id.slice(1)); } return list.length + " 条：A" + list.join(", A") + "——这类条目要按正文证据读，不能拿标题当接线状态。"; })());
out.push("- **`code_search` 全废**：本机 git 2.33.1 缺少 ocr 依赖的 grep 选项，所有仓库内检索调用失败 ⇒ ocr 只能看到「被评审文件 + 它自己 file_read 抓到的邻居」，**跨文件调用链它看不到**。因此本报告里每一条危害判定都由人工回读现码 + 追调用方重新建立，未复核项一律不写成缺陷。");
out.push("- **单文件视野带来的系统性偏差**：ocr 频繁把「模型层没写 CHECK」「列可空」「JSON 列无变更跟踪」报成 high，而真实危害取决于是否有调用方在原地修改/是否有人读该列。本报告中同类结论全部按调用方证据降级（见文末）。");
out.push("- **臆造代码片段**：不止一次，ocr 的「现码片段」在盘上找不到对应行。实例：`deliveryOpenapi.css` 的 F-63（交出 `.cds-row3 > .cds-row3-placeholder-never-match { width: auto; }`，全仓 `grep` 0 命中，真值是 `:77-80` 的 `.cds-row3 > .ant-col`）；`cds-volume-plugin/server.go` 的一条它引 `:130`，真值在 `:144-145`。⇒ 工具交来的引用一律当**待验断言**：本报告里我自己复验的条目（§1.1 第一/三/四类）都是逐字回读到盘上才写的，第二类（坐标来自子代理）我**没有**逐条再看，用之前值得自己抽几条。");
out.push("- **宽度类误报集中**：只拿宽度差做证据的条目基本不成立——登录页栅格 F-20 把 `.cds-auth-main` 的 `padding: 32px` 加到栅格轨道最小值上（实际内距在轨道内，且有 `min-width: 0`），另有历史上把 15px 当成内边距问题的同类误报（实际是文档竖向滞动条）。");
out.push("- **三方库契约误判**：F-6 断定 antd 5 不在 FormItem 上发 `.ant-form-item-vertical`，而 `node_modules/antd/es/form/style/index.js:390-397` 的 `genVerticalStyle` 正是以 `${formItemCls}-vertical` 为钥 ⇒ 项目的选择器不是空匹配。查这类问题不能拿字面 grep 的空读数当结论（类名是 `prefixCls` 拼出来的），要读到拼接那行。");
out.push("- **数字偏差**：对比度类数值不精确（任务详情「进行中」标签实测 4.04:1，它写 3.3:1；全局灰态实测 3.24:1，它写 3.0:1），方向对但不足以支撑结论，本报告里的对比度全部用 WCAG 公式重算。");
out.push("- **模型**：qwen-local/Qwen3.8-Flash-Next，provider 为本地量化模型；对 Python/SQL/TS 的语义判断可用，但对「可达性」的判断不可采信。");
out.push("- **不跑测试**：本评审未执行任何测试或部署，所有「必然失败」类结论都是逐行读出来的调用序列，并在报告中标出证据行号。");
out.push("");

out.push("## 3. 结论计数\n");
out.push("| 严重度 | 条数 |");
out.push("|---|---|");
for (const t of TIERS) if (counts[t]) out.push(`| ${t} | ${counts[t]} |`);
out.push(`| **合计** | **${findings.length}** |`);
out.push(`| 判为非缺陷 / 高估 / 潜在的批次 | ${judged.length} 批 |`);
out.push("");

// 建议动手顺序是人工判断层，单独存在 fix-order.md，重新生成报告时不丢。
if (existsSync(dir + "/fix-order.md")) {
  out.push("## 4. 建议动手顺序（按爆炸半径 × 可利用性 × 修复成本，不按严重度机械排序）\n");
  out.push((await Bun.file(dir + "/fix-order.md").text()).trim().replace(/^## /gm, "### ") + "\n");
}

const TIER_TITLE = {
  critical: "## 5. Critical — 立即处理",
  high: "## 6. High",
  "medium-high": "## 7. Medium-High",
  medium: "## 8. Medium",
  low: "## 9. Low",
  latent: "## 10. 潜伏（代码事实成立，但当前无可达入口——接线/重用时必须先修）",
  untagged: "## 11. 前提事实（不是缺陷，是下面多条的判据）",
};
for (const t of ["critical", "high", "medium-high", "medium", "low", "latent", "untagged"]) {
  const list = findings.filter(f => f.sev === t).sort((a, b) => a.num - b.num);
  if (!list.length) continue;
  out.push("\n" + TIER_TITLE[t] + `（${list.length} 条）\n`);
  for (const f of list) {
    out.push(`### ${f.id} — ${f.title}\n`);
    if (f.body) out.push(f.body + "\n");
  }
}

out.push("\n## 12. 判为非缺陷 / 高估 / 潜在（不要再报）\n");
for (const j of judged) out.push(`### ${j.head}\n\n${j.body}\n`);

const dAt = ledger.indexOf("## D. 修复状态");
if (dAt >= 0) out.push("\n" + ledger.slice(dAt).trimEnd() + "\n");
else out.push("\n## 13. 修复状态\n\n台账尚无修复状态节（`## D. 修复状态`）。\n");

const text = out.join("\n");
await Bun.write(dir + "/ocr-review-report.md", text);

const raw = (await Bun.file(dir + "/ocr-findings.json").json()).length;
console.log("ledger findings: " + findings.length + " (raw ocr unique: " + raw + ")");
console.log("counts: " + JSON.stringify(counts));
const dupes = findings.map(f => f.id).filter((id, i, a) => a.indexOf(id) !== i);
console.log("duplicate-ids: " + (dupes.length ? dupes.join(",") : "none"));
const missing = findings.filter(f => !text.includes(f.id + " — ")).map(f => f.id);
console.log("missing-in-report: " + (missing.length ? missing.join(",") : "none"));
console.log("judged blocks: " + judged.length + "  waves: " + status.map(s => s[0] + "=" + s[2]).join(" "));
