# CDS 代码评审报告（本机 ocr / OpenCodeReview v1.13.1）

> 本报告是 `ocr-verified-findings.md`（复核台账）的**排序视图**：每条 A## 结论逐字搬运，未做二次概括。
> 台账按批次追加（当前共 85 个 A 批次、A0→A320，其中 A68/A107 已并入邻条），报告按严重度重排。判定为「非缺陷 / 高估 / 潜在」的条目集中在文末。

## 1. 覆盖范围

| 波次 | 范围 | 状态 | ocr 计划评审 | 其中产出了发现 |
|---|---|---|---|---|
| w1-api | app/api, app/core, app/utils, app/schemas, app/models, app/templates, app/static | 已完成 | 122（发现 126） | 112 |
| w2-services | app/services | 已完成 | 118（发现 120） | 117 |
| w3-edge | sdk, cds-volume-plugin, alembic, contracts, policies | 已完成 | 51（发现 65） | 46 |
| w4-frontend | cds-frontend/src | 已完成 | 137（发现 185） | 149 |
| w5-ops | scripts, deploy, k8s, helm, .github | 已完成 | — | 55 |
| _首轮试点_ | app（已并入 w1/w2 口径） | 已完成 | — | 15 |

**全程排除**：`Tongsuo/`、`cds-volume-plugin/tongsuo-build/`、`**/node_modules/**`、`app/_stubs/**`、`**/*.pb2.py`、`**/dist/**`、`**/*.min.js`、`**/*.map`、`**/*.png`（前两者是 vendored 上游国密库，第三四是构建产物与 protoc 产物）。

**可评审性实测（`ocr scan --preview`，不跑模型，只让工具自己报它会评哪些文件）**：全仓发现 976 个文件，ocr 判为可评审 522（53%），跳过 454。跳过原因逐项：`unsupported_ext` 123、`secret_exclude` 1、`user_exclude` 35、`default_path` 284、`too_large` 1、`binary` 10。


### 1.2 仍未覆盖（逐条列出，不写「大概」）

1. **ocr 根本读不了的类型**：`unsupported_ext` 123 个文件（其中 `.md` 104，其余含 `.tpl` / `.service` / `.conf` / `.template` / `.txt` / `.mako` / `.mod` / `.sum` / `.html`），例：`helm/cds/templates/_helpers.tpl`、`deploy/cube-node/audit-tailer.service`、`deploy/cube-node/cds.env.template`、`scripts/cube-contract-paths.txt`。**本次评审对这些文件没有任何结论**。
2. **测试文件**：`default_path` 284 个被 ocr 默认跳过（`tests/**` 与 `cds-frontend/**/__tests__/**` 共 46 个前端用例）。按项目规则 `tests/` 是叶子（不反向 import 任何生产代码），本次不把它当缺陷来源，但也确实**没评**。评审过程中从生产代码倒推出来的测试盲区（如 A204② 的假绿门）已单独记账。
3. **超大与密钥类**：`too_large` 1 个（`specs/prototype.html`，原型文档非代码）、`secret_exclude` 1 个（`.env.prod.example`）、`binary` 10 个（图片）。另 `user_exclude` 35 个是本次显式排除的 vendored 国密库与构建产物。
4. **五波路径之外的 35 个可评审文件**（包含启动装配入口 `app/main.py`）——已单开 **w6-residue** 波次补扫：扫描完成并收割（128 条 / 21 文件；critical 6、high 24）。台账已并入其中的 Jinja 管理台与 prototype-v3 部分（A241/A242）；30 条 critical/high 里，compose/启动装配面 12 条已逐行判完 → 台账 A283-A286（含 1 条 critical）；完成时快照被重写一次（135→128 条、行号普遍位移，报告不引用其行号），补判最终快照新增项 → A287-A289。剩余 5 条（docker-compose.middleware.yml 的 Kafka/Vault/CDC 三条、alembic.ini、tests/conftest.py）**只读到、未定级**；w6 的 medium/low 共 98 条未逐条复核（清单文件 claims-w6.txt）。清单：`.gitignore`、`.gitmodules`、`alembic.ini`、`app/__init__.py`、`app/main.py`、`app/static/css/admin.css`、`app/static/js/admin.js`、`app/templates/alerts.html`、`app/templates/base.html`、`app/templates/blockchain.html`、`app/templates/certificates.html`、`app/templates/connectors.html`、`app/templates/identities.html`、`cds-frontend/.dockerignore`、`cds-frontend/.gitignore`、`cds-frontend/Dockerfile`、`cds-frontend/eslint.config.js`、`cds-frontend/index.html`、`cds-frontend/package.json`、`cds-frontend/tsconfig.app.json`、`cds-frontend/tsconfig.json`、`cds-frontend/tsconfig.node.json`、`cds-frontend/vite.config.ts`、`docker-compose.blockchain.yml` 等。
6. **非代码工件**：w6 补漏扫到 `specs/prototype-v3.html` 产出 29 条（它是已被 `基础版数据沙箱原型/` 图片取代的原型基线文档，AGENTS.md 记为 superseded），**按文档处理、不作缺陷入账**；同理 `cds-frontend/index.html` 的模板注释类。构建配置类（`cds-frontend/package.json` 11 条、`eslint.config.js` 12 条、`tsconfig.*.json`、`alembic.ini` 8 条）尚未逐条判读，列在本节未完成里。
5. **前端只判到高危层，中低危与 w5 运维面仍未复验**。CSS 类 72 条判完（台账批次五十七~六十六：64 成立 / 3 FALSE / 5 潜伏）；逻辑类 critical/high 73 条逐条读到盘上（台账 A243-A272，含 2 条 critical），中低危 373 条**只切片未逐条复验**，构建配置类（`cds-frontend/eslint.config.js` 12 条、`package.json` 11 条、`tsconfig.*`、`alembic.ini` 8 条）**未判**；w5（`scripts` / `deploy` / `k8s` / `helm` / `.github`，预检 71 发现 / 59 可评审）仍在 w4 后面排队，**这一路一条都没评**。
5b. **w4 逻辑类判读进度（数字在生成时从 ocr-findings.json 现算，不是手抄）**：前端波次已收割 1043 条，其中 CSS 72 条已整批改判完（台账批次五十七~六十六；w4 已结束，该数字冻结）、遗留管理台 116 条收敛为 A241/A242；扣掉构建配置后逻辑类 944 条（w4-frontend.json 已落盘并收割；raw unique 未增长，因为此前是从日志增量收割、同一批条目已入库）：critical/high 138 条**已全部逐条读到盘上**（台账 A243-A276，含 2 条 critical：A243 数据面横幅双重解包、A268 MPC 分片读侧无门），medium/low 806 条**刚开始判**——已整文件判完 services/identityApi.ts（11）、pages/Audit/AuditLog.tsx（10）、pages/Auth/Login.tsx（8）、services/auditApi.ts（9）、services/federationApi.ts（9）、components/Audit/AuditVerifyModal.tsx（8）、components/Feedback/ErrorCard.tsx（7）、services/sandboxApi.ts（8）、utils/workspace.ts（7）、components/Layout/DataplaneBanner.tsx（5）、components/Feedback/QueryFeedback.tsx（5）、services/mpcApi.ts（5）→ 台账 A273-A300（92/本清单条数会随 w4 增量变化，见 claims-fe-med.txt 首行），另含 2 条由前端切片挖出的后端问题 A268/A279；其余仅切片未复验。

**覆盖计量（只算 ocr 真正产出了发现的文件，不是它读过的文件数）**：原始发现 4181 条，分布 —— `scripts,deploy,k8s,helm,.github` → 55 文件；`app/main.py,app/__init__.py,app/static,app/templates,docker-compose.yml,docker-compose.prod.yml,docker-compose.middleware.yml,docker-compose.blockchain.yml,pyproject.toml,alembic.ini,cds-frontend/vite.config.ts,cds-frontend/eslint.config.js,cds-frontend/package.json,cds-frontend/tsconfig.json,cds-frontend/tsconfig.app.json,cds-frontend/tsconfig.node.json,cds-frontend/Dockerfile,cds-frontend/index.html,tests/conftest.py,specs/prototype-v3.html` → 24 文件；`sdk,cds-volume-plugin,alembic,contracts,policies` → 46 文件；`app` → 15 文件；`app/api,app/core,app/utils,app/schemas,app/models` → 112 文件；`app/services` → 117 文件；`cds-frontend/src` → 125 文件；`sdk` → 4 文件。ocr 还会 `file_read` 大量邻居文件（包含 `tests/`）但不产出发现，所以本表是**保守下界**；未被覆盖的文件不表示安全，只表示本次未产出。

### 1.0 覆盖与限制的最新口径（逐字搬运台账 C 节，与批次叙述冲突时以本节为准）

- **扫描波次（全部跑完）**：w1 `app/api,core,utils,schemas,models,templates,static` 96 min / w2 `app/services` 83 min / w3 `sdk,cds-volume-plugin,alembic,contracts,policies` 137 min / w4 `cds-frontend/src` 98 min / w5 `scripts,deploy,k8s,helm,.github` 54 min，`ALL_WAVES_DONE`。原始声明 **4181 条**（去重后进台账 **319 条 A##** + 8 组「非缺陷/高估/潜伏」判定块）。
- **已逐条人工复现复核的范围**：后端 `app/`（w1/w2）、`sdk/`、`cds-volume-plugin`、`alembic`、`contracts`、`policies`、`cds-frontend/src`（w4 的 61 文件 / 319 条中低危由三路只读裁决子代理 + 前期文件级批次**全部判完**）、部署/运维面的**已入台账条目**（A283-A285、A301-A303 等）。
- **仍未结算的范围（不得当作已完成）**：`claims-ops.md` 里 w5 的 **417 条 / 55 文件**（critical 23 / high 123 / medium 199 / low 70）**尚未逐条定判** —— 判真伪与修复合并交给 4 路运维作业（CI 面、cube-node 面、scripts 面、k8s/helm 面）边判边修，完成前 w5 的「已完成」只指**扫描完成**，不指**人工复核完成**。构建配置面（`vite.config.ts`、`Dockerfile*`、`cds-frontend/package.json` 等）只有进过台账编号的条目算判过，其余未单独定判。
- **可达性判定的工具限制（影响所有「某调用方会…」类断言）**：本机 git 2.33.1 不支持 `--max-count` ⇒ ocr 的 `code_search` 在**每个文件上都失败**，即 ocr 是在**没有跨文件上下文**的情况下评审的；台账里所有 LIVE/LATENT 判定由我自己跑 AST 反向导入闭包（`rev_imports3.py`，早期版本漏 `from PKG import SUB` 入边，已修正回扫并留有改判记录）+ 逐条 grep 得出。ocr 对未接线模块按活路径定级，已把 15 条下调为 latent。
- **本机验证能力的边界（影响修复侧「已验」的可信范围）**：① 本机 python 3.12（`.venv-gate`），CI 后端是 3.11、生产镜像是 3.12 ⇒ 所有数字标「本机 3.12 口径」，权威数字在远端 py3.12 + 真 PG16；② `go1.18` 而卷插件要求 Go 1.25 且需 Tongsuo ⇒ Go 侧**没有编译门**，只做静态门与 Python 侧不变量测试；③ `helm` 与 `yq` **不存在**、`kubectl` 无集群 ⇒ 部署清单只做 pyyaml 静态断言 + `bash -n`；④ CI 前端 job（`ci.yml:108-116`）只跑 `npm ci && npm run build`，**不跑 vitest**；⑤ 真 TEE/GPU/HSM/Cube 节点/联盟链/ClickHouse 列宽等一律未跑，标「仅部署可验」。
- **本轮证据完整性的一次自伤（必须披露）**：为停一条噪音监视器我用了 `kill_bash({all:true})`，回执 `Killed 32 session(s)` —— 连带杀掉子代理的 shell 与在跑的门命令，并导致两路（密钥簇、数据面簇）以 `The operation was aborted.` 中止。后果已核算：`gate-evidence.md` 把 70 份 `.gate-*.log` 逐份判定，**名字说绿但 RC≠0 的 1 份**（`.gate-c6-green.log`，`C6GREEN_RC=1`，`MissingGreenlet`）已打回属主；两个中止簇由 `st_01a14f77`/`st_01a14f78` 接续，且要求「先读懂盘上半成品，不得回退重写」。凡缺 RC 行的日志一律作废重跑。
- **模拟路径不是缺陷**：`tee_simulator`/`gpu_tee_runtime`/firecracker sim/`test_data_generator` 按项目铁律属有意特性，一律不按缺陷报；本轮的修向是把「不可用/未验证/未掩码」如实披露，而不是取消模拟。

### 1.1 复验方法与一手程度（每条怎么被确认的）

| 复验方式 | 条数 |
|---|---|
| 我亲验 / 我逐行复验（标题明写） | 27 |
| 坐标来自子代理的逐条复验（批次或标题标明） | 79 |
| 正文含实测/探针证据（RC=、探针输出、像素/打包实测） | 19 |
| 我逐行回读现码 + 追调用方 | 194 |

读法：第一类 27 条（A196, A197, A198, A203, A211, A212, A213, A215, A216, A217, A218, A219, A220, A221, A222, A223, A224, A226, A229, A231, A232, A236, A238, A241, A244, A245, A246）是我本轮逐行复过的。第二类的行号来自子代理（每个子代理都被要求用 `nl -ba` 取真实行号、机审自己的引用、并把仓库改动清零），**我没有逐条再看一遍** —— 用之前值得自己抽几条。第三类带可复跑的探针输出。第四类是台账里逐行回读现码再建立的危害链。

**抽样复核（本轮做的，不是承诺）**：从 79 条子代理坐标里按序抽 8 条 —— A96 / A105 / A138 / A147 / A167 / A184 / A193 / A211，逐条回读盘上原文，**8/8 命中的行就是结论所引的那一行**。例：A193 指到 output_inspection.py:306 的 stage_results[SIGNATURE] = bool(sig)，紧接 except Exception: 与「Fallback to SM3 hash」注释；A211 指到 alembic/env.py:35 的 config.set_main_option(...)。抽样里同时看得到我自己的降级痕迹：A96 子代理原话是「今天就在跑」，我按 BwrapAdapter 只在 legacy 门后注册（sandbox_runtime.py:2139）改成「dev/e2e 开 L3 时可达」。其余 71 条仍是子代理口径，没有逐条复读。

## 2. 工具限制（影响结论口径）

### 2.0 扫描器自己的失败账（生成时从日志现算）

| 波次 | 子任务报错（该批次零产出） | LLM 超时次数 | code_search 失败 |
|---|---|---|---|
| w1-api | 6 | 16 | 385 |
| w2-services | 0 | 0 | 340 |
| w3-edge | 2 | 1 | 156 |
| w4-frontend | 14 | 17 | 368 |
| w6-residue | 4 | 4 | 61 |

**合计 26 个 scan 子任务因 LLM 超时报错（provider 侧共 38 次 timeout），这些批次没有产出任何发现** —— ocr 的覆盖不是均匀的。出现过报错的文件/批次：app/api/agent.py, app/api/notebooks.py, app/api/sandbox_sessions.py, app/models/user_mfa.py, app/schemas/high_risk_operation.py, app/schemas/data_resource.py, cds-volume-plugin/sm4gcm/sm4_gcm.h, cds-volume-plugin/proto/cdsvolume/v1/cdsvolume.proto, cds-frontend/src/services/trainingApi.ts, cds-frontend/src/services/metaApi.ts, cds-frontend/src/stores/authStore.ts, cds-frontend/src/pages/Flow/monacoSetup.ts, cds-frontend/src/services/flow.ts, cds-frontend/src/test/setup.ts, cds-frontend/src/services/usersApi.ts, cds-frontend/src/services/jupyterContents.ts, cds-frontend/src/stores/workspaceStore.ts, cds-frontend/src/services/api.ts, cds-frontend/src/stores/uiStore.ts, cds-frontend/src/services/monitoringApi.ts, cds-frontend/src/stores/crumbStackStore.ts, cds-frontend/src/services/quotaApi.ts, app/static/css/admin.css, docker-compose.yml, docker-compose.middleware.yml, cds-frontend/Dockerfile（同批可能重试成功，所以出现过报错不等于完全没扫到，但它属于 §1.2 未覆盖清单。）

- **code_search 全军覆没的实测计数：合计 1310 次**，报错原文一律是 `git grep failed: exit status 129: error: unknown option 'max-count'`（本机 git 2.33.1）。=> ocr 每条结论只建立在它 file_read 到的片段上，没有任何跨文件检索能力，这正是本评审必须逐条人工回读现码的原因。

- **标题定级口径**：标题里的严重度按该条目**活路径那一部分**定级。另有“正文里确实存在潜伏成分、但整条未定为潜伏”的条目（它们同时有活的调用方或活的诱饵面）：30 条：A39, A42, A61, A66, A67, A72, A75, A93, A94, A95, A96, A103, A109, A148, A155, A156, A170, A177, A199, A202, A228, A237, A240, A241, A256, A288, A296, A298, A307, A314——这类条目要按正文证据读，不能拿标题当接线状态。
- **`code_search` 全废**：本机 git 2.33.1 缺少 ocr 依赖的 grep 选项，所有仓库内检索调用失败 ⇒ ocr 只能看到「被评审文件 + 它自己 file_read 抓到的邻居」，**跨文件调用链它看不到**。因此本报告里每一条危害判定都由人工回读现码 + 追调用方重新建立，未复核项一律不写成缺陷。
- **单文件视野带来的系统性偏差**：ocr 频繁把「模型层没写 CHECK」「列可空」「JSON 列无变更跟踪」报成 high，而真实危害取决于是否有调用方在原地修改/是否有人读该列。本报告中同类结论全部按调用方证据降级（见文末）。
- **臆造代码片段**：不止一次，ocr 的「现码片段」在盘上找不到对应行。实例：`deliveryOpenapi.css` 的 F-63（交出 `.cds-row3 > .cds-row3-placeholder-never-match { width: auto; }`，全仓 `grep` 0 命中，真值是 `:77-80` 的 `.cds-row3 > .ant-col`）；`cds-volume-plugin/server.go` 的一条它引 `:130`，真值在 `:144-145`。⇒ 工具交来的引用一律当**待验断言**：本报告里我自己复验的条目（§1.1 第一/三/四类）都是逐字回读到盘上才写的，第二类（坐标来自子代理）我**没有**逐条再看，用之前值得自己抽几条。
- **宽度类误报集中**：只拿宽度差做证据的条目基本不成立——登录页栅格 F-20 把 `.cds-auth-main` 的 `padding: 32px` 加到栅格轨道最小值上（实际内距在轨道内，且有 `min-width: 0`），另有历史上把 15px 当成内边距问题的同类误报（实际是文档竖向滞动条）。
- **三方库契约误判**：F-6 断定 antd 5 不在 FormItem 上发 `.ant-form-item-vertical`，而 `node_modules/antd/es/form/style/index.js:390-397` 的 `genVerticalStyle` 正是以 `${formItemCls}-vertical` 为钥 ⇒ 项目的选择器不是空匹配。查这类问题不能拿字面 grep 的空读数当结论（类名是 `prefixCls` 拼出来的），要读到拼接那行。
- **数字偏差**：对比度类数值不精确（任务详情「进行中」标签实测 4.04:1，它写 3.3:1；全局灰态实测 3.24:1，它写 3.0:1），方向对但不足以支撑结论，本报告里的对比度全部用 WCAG 公式重算。
- **模型**：qwen-local/Qwen3.8-Flash-Next，provider 为本地量化模型；对 Python/SQL/TS 的语义判断可用，但对「可达性」的判断不可采信。
- **不跑测试**：本评审未执行任何测试或部署，所有「必然失败」类结论都是逐行读出来的调用序列，并在报告中标出证据行号。

## 3. 结论计数

| 严重度 | 条数 |
|---|---|
| critical | 25 |
| high | 102 |
| medium-high | 40 |
| medium | 97 |
| low | 36 |
| latent | 15 |
| untagged | 4 |
| **合计** | **319** |
| 判为非缺陷 / 高估 / 潜在的批次 | 8 批 |

## 4. 建议动手顺序（按爆炸半径 × 可利用性 × 修复成本，不按严重度机械排序）

排法依据三条，不是按严重度机械排序：**① 是否"任意已认证用户"就能触发**（横向越权优先，因为这类改动面最小、收益最大）；**② 静默失效优先于报错失效**（本仓铁律：宁可拒绝不可伪造，静默降级会直接污染合规结论）；**③ 同一处代码顺带修掉同族**。组内按"已实测/已端到端复现 → 纯静态读出"排。

### 第 0 组 · 止血：一行到几行，先合掉（合计 < 半天）

| 条 | 动作 | 代价 |
|---|---|---|
| A116 | `max(1, settings.SESSION_MAX_SNAPSHOTS)`，或把 0/负当"禁用"提前返回 | 1 行（已实测 500） |
| A108 | `validate_resource_limits` 补下限与类型校验 | 3 行（已实测 0/负数被采纳、字符串抛 TypeError） |
| A103 | `agent_service.py:386` 改写掉 PEP 701 嵌套 f-string | 1 行（CI py3.11 `compileall` 必红） |
| A15 | `k8s_ops.py:33` 补 `await` | 1 词 |
| A38 | `core/redis.py:36` 打日志前把 URL 的密码段替掉 | 2 行 |
| A41 | `is_expired()` 用 `datetime.now(timezone.utc)` | 1 行 |
| A6 | `connectors` 写入列宽 8 → 与模型一致（或改列） | 1 处 |
| A134 | `blockchain.html:74` 读 `result.verified`，并把 `backend`/`verification_note` 显示出来 | 2 行（监管页现在恒显"失败"） |
| A150 | `federation_connector.py:565` 把 `.get(operation, 0)` 改成未登记即拒（或取表内最大值） | 1 行（现在一个字符串就能关掉联邦信任门） |
| A136 | `session_templates.py:102` 改 `except Exception` 并逐项校类型 | 2 行（配置形状写错=建会话 500） |
| A154 | `sdk/pyproject.toml` 把 `readme` 移回 `[project]` | 1 行（现在多一个假 extra） |

### 第 1 组 · 鉴权与密钥面（critical 主体，"任意已认证用户"级）

A1（导出全部 SM2 私钥）→ A12（取回全部明文 Shamir 分片）→ A13（DEK 全生命周期）→ A51（`encryption-config` 只 `get_current_user` 就返回明文 DEK）→ A30（游标分页丢权限谓词，已端到端复现）→ A21（不传 `session_id` 同时绕过授权与合约上限）→ A4（拒绝意图被反转成全量批准）→ A89（DEBUG 形态公开硬编码 JWT 密钥）→ A14（MFA 可被单因素静默关闭）。

这一组几乎都是**缺 `require_roles` / 缺谓词**，不是逻辑重构；改完能一次消掉报告里近半数 critical。建议在同一个 PR 批次里做，便于用一份权限矩阵测试覆盖（本仓已有 `test_capability_matrix_consistency.py` 这类文档↔代码门可挂）。

### 第 2 组 · 密态门与策略面（静默失效，比崩更危险）

A111（单元格真实输出对输出 DLP 不可见，已复现）→ A48/A49（合约策略 fire-and-forget 且未挂时 fail-open，同根一起修）→ A95（带字段 ACL/掩码的那套 PDP 无人导入，查询期只跑"看 5 项"的那套）→ **A146（网关限额读不到就是 0，而 0 在调用方约定里等于"不限"；拒绝的请求照样计数且无 `decrby`）**→ **A145（DP ε 预算每进程缓存永不失效：多副本各花各的额度，`consume` 先减内存后写库且库失败被吞）**→ A151（`can_access` 不认识即放行，潜伏但是 1 行 fail-closed）→ A57（脱敏视图不是会话拥有者的强制边界）→ A110（合约签名 payload 分隔符注入，探针证实）→ A32（文件名拼进 guest 内执行的 Python 源码，绕过 code_scan）→ A97（code_scanner 属性名/内建名不分：`re.compile()` 被 FATAL，而 `io.open` 绕过）→ A24（`_run` 里 `db` 全局查找，共享卷静默不挂载，已实跑）→ A129（RAG 维度不一致时 cosine 静默返回 0.0，已复现链路）→ **A141（KMS 显式传 policy 使启动门校验的 `ATTESTATION_ALLOWED_MEASUREMENTS` 在这条路上从未被使用）**。

顺序原则：**先堵 fail-open，再修扫描器误杀**。A95/A97 都涉及"看起来有门、实际门不在链路上"，修完必须配一条"绕过即失败"的回归用例，否则下一轮重构还会掉。A146/A145 是同族的新型态：**门在，但门的输入（限额/预算）来自一个会静默失败的读取**。

### 第 3 组 · 合规可核验性（审计链与锚定）

A93（锚定走永远上不了链的本地块，写库 `tx_hash` 重启即不可验）→ A5（监管接口结论与文案自相矛盾）→ A121（`encrypted_key` 撤销后不清、`rotate_kek` 无调用方、KEK 轮换后所有 blob 解不开、Go `dekLen=16` 常量 vs Python 运行时配置）→ A130（保留期工头计数取 SELECT 条数、单表异常后 PG 事务 aborted）→ A120 / A28（唯一约束上的 check-then-act，重 key 把整批告警/审计带下、后台清理循环卡死）→ A29（审计写入与业务提交互相绑死，违反本仓已写下的契约）。

这一组的验收方式建议统一：**导出一条真实审计记录，独立重算 SM3 Merkle 路径并核对 `backend` 披露**；做不到就说明锚定仍是自证。

### 第 4 组 · 可用性与破坏半径

A59（未转义 LIKE 直接驱动 DELETE ⇒ 跨命名空间目录清空，破坏性最高）→ A60（未校验 endpoint 的带令牌 SSRF）→ **A147（合约履行：容器已拉起后的异常只进 `errors`，DB 回滚不回收容器 ⇒ 带数据通道的孤儿容器）**→ A117/A118（快照回滚"先全删再解包"且漏 symlink）→ A90/A84（`terminate()` 用错 id 导致卷永久孤儿；卷级 `read_only` 可被客户端推翻）→ A70（上传先全量进内存再校大小）→ A126（`secure_checkpoint` 零落盘 + 每次启动随机主密钥 + 无容量上限）→ A101/A102（LoRA 在优化器之后注入 ⇒ 训练静默 no-op；DP 噪声未除 batch 且 `dp_noise_injected` 是配置回声）→ A53/A54（资源不可用仍返回 201；明文副本永不回收）→ A82（僵死 running 操作永久锁住回滚）→ A26（事件循环阻塞与无上限缓冲）→ A112（信封解析不到时同一单元格再执行一遍）→ A19/A7/A22（无锁激活导致 DP 预算重复分配、轮找回读未加锁、检查点反序列化缺防护）。

### 第 5 组 · 先决策再动手（两套实现 / 配置在但没接线）

不是 bug 清单，是**架构裁决清单**：A95（两套 PDP）、A131（两套 k-匿名，reviewed 的那套零导入方）、A133（`rls_engine`/`pg_rls_manager` 整条链未接线）、**A152（`vision_pipeline` 与 `vision_multimodal_runtime` 两套假脱敏，都只被自己的单测导入，却把 `is_redacted` 置 True 并计入合规计数）**、**A151/A153（`can_access`、`task_circuit_breaker` 只被测试导入，一旦接线就是放行/误熔断）**、**A138（`key_distributions` 表全仓无写入方，启动即跑的 TTL 过期循环永远空转）**、A127（`stream_audit` 未接线）、A128（`k8s_sandbox`/`k8s_client` 在 legacy 门后）、A75（持久队列守卫缺失但非活路径）。~~A136 曾在本组，已改判为活路径 500，移入第 0 组。~~

每一条只有两种正确收场：**接线并补测试**，或**删掉并在 `docs/mock-remediation-plan.md` 登记为未实现**。保持现状的代价是每一轮评审都要重新判一次可达性（本轮为此建了 AST 探针，并且因它漏 `from PKG import SUB` 而入边而差点把 `session_templates`、`mpc_service` 误列为死代码）。

### 第 6 组 · 前端与旧后台

A114 / A83（5 个模板 innerHTML、全仓无 HTML 转义层、token 在 localStorage）、A137（fixed 侧边栏无滚动 / 无响应式 / 暗色对比度不足）。旧管理控制台按已定形态属"开发形态可达、生产不可达"，所以整族排最后；若决定继续用，第一件事是加一个 `escapeHtml` 工具并全量套用，而不是逐页修补。`cds-frontend/src`（w4）**尚未评审**，不要把这一组当成前端全貌。

### 第 7 组 · 本轮（w2 后半 + w3 边缘）新增，按同级严重度插进上面各组

**与第 1 组同级的 critical**：**A161（KMS 密钥库是进程内存，`storage_service`/任务码/工作区三类密文只存 `key_id` ⇒ 一次重启永久解不开，`storage_service.py:185-187` 的报错自己承认了根因）**、**A179（卷插件 `NewKMSClient` 写死明文 `http.Client` 且不校 scheme，注释与 AGENTS.md 却称生产走 mTLS ⇒ 跨节点部署时明文 DEK + 服务 token 上线）**、**A162（TEE 证据可由 PATH 上的同名文件/普通文件伪造 ⇒ 安全态势接口对外发布 `tee_status="verified"`）**。

**卷插件其余项（`cds-volume-plugin` 不在 `ci.yml`，`make test` 手动，无自动门兼顾）**：A175（helper 默认相对名 → PATH 查找，stdin 里递的是明文 DEK）→ A180（`do()` 丢 `io.ReadAll` 错误 + 只把 `>=300` 当失败 ⇒ `RevokeDEK` 可谎报「密钥已销毁」，与「revoke 失败必须保留数据」的铁律相反）→ A181（`ref` 未校验拼进 URL 路径）→ A176（`int(Seconds())` 把 <1s 的配置截成 0 ⇒ 每次 helper 调用立即超时，报错却说是超时）→ A177（`Cipher` 入口不校 key/nonce/tag 长度，短了零填充长了截断；今天靠 `kms.go:125` 钉住才不可达）→ A178（超时分支丢 `Kill` 错误后无条件 `<-done`）→ A182（HTTP 错误原样带上游响应体进日志与 gRPC）。

**后端新增（按原组归类）**：A164（`gateway_metering`/`sandbox_activity_events` 有写入有查询但全仓无建表 DDL ⇒ 计量静默归零）→ A165（`unstructured_pipeline`：`input_path` 零约束跨租户读 + 明文中间产物永不回收 + DICOM 脱敏失败写进无人读的 `partial_success`）→ A163（`run_test` 无状态/环境前置，可把生产态工件打回 TESTING 并解绑测试证据）→ A168（OpenAPI 配额读-改-写，竞态窗口＝整次沙箱执行）→ A167（`key_seed` 无上限，权重已改写之后才 500，水印记录被回滚）→ A171（`watermark_records.job_id` 无唯一约束却用 `scalar_one_or_none` ⇒ 并发后永久 500）→ A166（信任分整型截断翻转联邦准入）→ A169（webhook 派发窗口被暂停订阅的滞留行卡死）→ A157/A158（agent `http` 出网白名单只管 hostname；注册表 `roles`/`budget_cost`/`network_targets` 全是装饰）→ A172/A173（图片水印 `bits_per_channel` 只写不读；SDK `_parse_error` 遇非 dict JSON 崩成 `AttributeError`）。

**attest / privacy 回收新增（与第 1 组同级）**：**A183（远程证明门实为源码常量 `_LOCAL_ATTESTATION_ROOT` 派生的自签哈希；`kms_service.py:314` 自建策略绕过 prod 强制的测量白名单；模拟拒收门看自述 `type`）** → **A185（水印写回对 numpy/非连续张量静默 no-op 却报 success）** → **A189（DLP 命中原值进 `findings.samples`，随 `security_report` 回传）** → **A186（`key_seed` 明文列 + 接口原样回传，水印安全性 = 31-bit 种子）** → **A191（输出签名密钥进程内随机生成 ⇒ 不可抵赖不成立，同 A126 根因）** → **A193（SM2 失败时用 SM3 摘要顶替并记 `stage_results[SIGNATURE]=True`，`passed=all(...)` 计入；except 内的 `sm3_hash` 自己会抛 ⇒ 500）** → A192（DP 谎报 True 且不登记 `not_evaluated`；**修法只补披露，禁动 `should_block()`——2026-10-16 已拍板**）→ A190（重构检测按位置元组比较，改列序即绕过）→ A187/A188（空位集判通过、逐 bit 全张量复制）。

**edge 波次（卷插件）回收新增**：**A196（rootfs 读回跟随 guest 可控 symlink ⇒ 宿主 root 任意文件读；FIFO/设备节点 ⇒ 挂死 + loop 泄漏；critical，本轮首要项）** → **A200（Destroy 不清 staging 明文 / Detach 零校验即静默跳过 shred / shredFile 不 Sync 就 unlink / 丢 shred 错误仍报成功）** → **A201（AAD 参与 Seal 却不写回 manifest ⇒ 该卷永久 Attach 不进）** → **A197（`grpc.NewServer()` 无消息上限，与 `MaxVolumeBytes=1GiB` 矛盾 ⇒ >4 MiB 必失败）** → A199（volume_id 只在 CreateVolume 校验）→ A198（固定 `.tmp` 名 + 不 fsync + 无每卷互斥）→ A202（小项簇）。

**w3 构建面回收新增**：**A204（镜像无 CA 根 ⇒ https KMS 必 `x509 unknown authority` 且 KMS fail-closed ⇒ CreateVolume/Attach 全线不可用；`plugin_test.go:319-323` 缺 helper 就静默不测国密后端 ⇒ 唯一自动化门对 SM4-GCM 假绿；libdir 两套口径）** → A205（`VOLUME` 匿名卷 = 密文随容器重建永久丢失；KMS 两个安全参数的镜像默认值 = 空 token + 明文 HTTP 静默；0600 与 root:cds 声明矛盾在镜像侧；基础镜像无 digest pin）。

**SDK 打包面（一行修）**：**A206（`sdk/pyproject.toml:12` 把 `readme` 写进 `[project.optional-dependencies]` ⇒ setuptools 校验直接抛，本机实测 `pip wheel` 与 `pip install -e ./sdk`（= `ci.yml:34` 同一句）双双 RC=1；修法：整行移到 `[project]`，`sdk/README.md` 已确认存在）** → A207（依赖无上限、缺 `py.typed`、版本双来源 = A154 同一处）。

**前端（DevTaskCreate 一页三处）**：**A208（必填校验失败时步进器毫无反馈：`devTaskCreate.css:156-164` 常态硬编码 + `:192-198` `box-shadow:none !important` + 全文件零 `has-error` 规则，而 `DevTaskCreatePage.tsx:275/:289` 两条 required + `:155` 后端 422 回写是活路径）** → A209（`@media 1280` 被 `nth-child(3)` 特异性压住 + 全文件仅一颗断点，1281–1439 无兼容）→ A210（小字样式挂位置不挂语义）。

**alembic / SDK 尾巴（本轮新增）**：**A213（POST 也被重试 → 重复创建资源，`client.py:228-235`）** → **A212（`_request_with_headers` 漏 `>=400` 抛错，`:245-254`，仅 `:328` 一个调用点）** → **A214 里的 E2-37（`mpc_key_shares` 缺 `UNIQUE(key_id, share_index)`，同 x 坐标会让 Shamir 恢复拼出错的分片）** → A216（代码进 query string：日志泄露 + 行长上限）→ A211（`alembic/env.py:35` 的 `%` 插值炸迁移）→ A214 冗余索引族与 E2-64（`0018:30` 非 CONCURRENTLY 锁写）→ A215 / E2-12~17（low）。

**前端 CSS 第二轮**：**A226（`deliveryOpenapi.css:60-63` 三列定宽 360 ⇒ 按仓内定标注释反推，该布局只在 ≥1488 成立，而断点从 1440 才退档 ⇒ 1440–1487 档横向溢出；修法：断点抬 1490 或改 `minmax(0,1fr)`）** → A222/A223（开发环境页零断点 + 零 `focus-visible`）→ **A224（「未接入」与「已关闭」同色，撞 honest-degradation 铁律）** → A219/A220/A221（进度条 `!important`／移动端 header 行高／灰态一族不过 AA）→ A225/A227（卫生族）。
**前端 CSS 第三轮**：**A228（审计详情弹窗挂 portal，本屏令牌/色板整块失效：`--audit-head-bg` 丢灰面板 + `t-*` 标签配色无规则命中）** → A231/A232/A233（任务详情页零断点 + 弹窗 720px 无 `max-width` + 值盒无溢出策略 + 状态标签对比度 2.78/3.25/4.04 全不过 AA） → A229（日期输入 55px 裁掉真实值） → A230/A234（令牌拷贝、页面级 `.ant-alert` 通配、重复盒定义）。
**前端 CSS 第四轮（CSS 集收口）**：A238（`.cds-task-card` hover 蓝描边是空操作：基态 `border: none` ⇒ 只改 `border-color` 无效；另 `:380` 注释「高 36」vs `:385` `height: 32px`） → A239（≤1023 档 `auto-fill minmax(280px,1fr)` 在 ≲328 视口溢出，改 `min(280px,100%)`） → A235（分页带 `text-align:right` 在 flex 容器上无效 ⇒ 右对齐判据没生效 + mini 档箭头尺寸没同步） → A236/A237/A240（卡头 `display:flex` 作废省略、`data-testid` 当选择器 6 处、无背景令牌、`.ant-table` 圆角无裁切、`transition: height` 触发 reflow）。**前端逻辑类第一批（这批有 critical）**：**A243（数据面横幅永不渲染：`DataplaneBanner.tsx:27` 对已被 `api.ts:37` 拆过的响应又取一次 `.data` ⇒ honest-degradation 披露面静默失效）** → **A244（全站零 ErrorBoundary + `CapabilityMatrix.tsx:30` 对 `data.matrix` 无兜底 ⇒ 一个空字段整站白屏）** → **A245（`SimulationBadge.tsx:69-70` 先判 `includes('sim')` ⇒ `not_simulated` 被贴「模拟」，诚实披露失真）** → A246（`display.ts:227` slug 不编码） → A247（`errorHints` 原型链 / `test:coverage` 缺 `@vitest/coverage-v8`，均已降级）。**遗留管理台（门后可达，默认关、生产拒绝）**：A241（六张 Jinja 页 + `admin.js` 全用 `innerHTML` 插 API 数据，零转义 ⇒ 存储/反射型 XSS，ocr 在这一族报的 20 critical + 42 high 全部收敛到这条）→ A242（`del()` 不发 body ⇒ 证书吊销必 422、204 还误报失败；告警枚举 `active/firing` vs 后端 `open/acknowledged/resolved`；「已验证」只看字段有没有值、压根不调 `/verify`；`onclick="revokeCert('${cert_id}')"` 内联注入；token 存 `localStorage` 与 XSS 合成完整窃取链）。**前端逻辑类第二批**：**A248（流式终端：`socketRef` 守卫跨 await 失效 ⇒ 双开 WebSocket 且第一条关不掉；`disabled` 后 effect 不重跑 ⇒ xterm+连接留在已卸载的宿主上，与自述的 running-only 策略不一致）** → **A250（`devTasksModel.ts` 阶段集合不过 `env`、生产执行集合过 ⇒ 同一张卡同时贴「已发布生产」和「没有 published 的生产版本」）** → A249（`auditDisplay.ts:89` 对 `record.action` 无保护 ×A244 无边界 = 白屏）→ A251（注册不校验 token，已降级 low）。**前端逻辑类第三批（跨方签名与密钥链路）**：**A255（`crypto_service.py:215-219` 合约签名原文 `|` 拼接不转义，前端 `utils/crypto.ts` 逐字镜像 ⇒ 跨方可控字段能拼出同一原文）** → **A256（`field_exposure.py:287` 空 `approved_fields` ⇒ 全量批准，当前 UI 恒传全集故不可达，属 API 边界潜伏）** → **A257（`identityApi.ts:113/:195` 证书状态与验链 fail-open：解析失败判有效、缺 `valid` 字段判通过）** → A258（SM2 私钥服务端生成下发 + localStorage 明文 = AGENTS.md 容忍项；`signData` 带私钥上行但 0 调用者 = 死代码）。路径参数不编码 5 处（`mpcApi.ts:47/53/58`、`fieldExposureApi.ts:55`、`display.ts:227`）并入 A246 一条修。**前端逻辑类第四批**：**A260（`csvPreview.ts:63` 先按 `\n` 切行 ⇒ 引号内换行被拆两条记录、字段内空行被 `filter` 删；`:75` 表头同名静默覆盖）** → **A259（`workspace.ts:90` 白名单命中即整节点放行，不裁 children —— 当前靠“分组 key 不带斜杠”侥幸没穿，记 latent）** → A246 补编码点。证伪两条不修：`types/models.ts` 的 `allowed_operations`（后端本来就一 str 一 list）、`types/security.ts` 的三态（`display.ts:182/208` 已是 `=== true` / `!== true`）。**前端逻辑类第五批（含一条新的 critical）**：**A268（critical：`app/api/mpc.py:200-221` 读侧只有 `get_current_user`，返回 `share_value`+`index`+`threshold` ⇒ 任意登录用户可取全部分片离线重组；`:160-166 GET /keys` 同样无角色且 `list_keys(db)` 不收 user ⇒ 全平台密钥可枚举 —— 对照 `:228/:260` 写侧 `require_roles(ADMIN)`）** → **A264（缺 `exit_code`：日志行记成功、状态芯片记失败、还不计问题数）** → **A265（审计 detail 标量全量拼进工作台文本，`NOISY_DETAIL_KEYS` 是噪音清单不是敏感清单）** → **A261（合规报告 `anchoring` 后端可空/前端非空 + 消费点无保护 ⇒ 白屏）** → **A262（互信记录缺 `created_at` 就编一个当前时间）** → A267（无时间记录被排成最新）→ A266（首页 `task_filters` 解引用 + 「失败 undefined」）。降级不修：A269 antdLocale、A270 useCrumbSegments、A271 normalizeKmsKey（当前 API 形状下不成立）。**前端逻辑类第六批（高危层收尾）**：**A272（`AuditVerifyModal.tsx:177` `deriveVerdict` 收不到 proof 的 pending 态 ⇒ 审计验真在 Merkle 路径回来前先亮绿「通过」）**；证伪一条：`computeApi.ts` 的「超时+重试=重复执行」不成立（`App.tsx:63` 只给 `queries` 配 retry:1，mutations 默认 0 次）——残留只是「无幂等键」，定 low。`k8sOpsApi.ts:106`、`outputControlApi.ts:50/53` 并入 A246（现列 8 处）。**中低危首批（前端）**：**A274（`identityApi.ts` 同一文件三种解包：`:144` 正确 / `:157 getKeyAudit` 裸数组⇒静默空审计列表 / `:171 listCertificates` 包装对象⇒抛+无边界=白屏，抽 `unwrapList` 一处收）** → **A275（审计页导出窗口是死的：`EXPORT_RANGES` 0 引用、`setExportDays` 0 调用，Tooltip 却写「按所选时间窗口」并把 `GET /audit/export?days=…` 摊给用户看）** → **A276（同弹窗里导出按 `canExport` 禁用，锚定本条/存证验真任意角色可点）** → A277（toast 渲染「Merkle Root: undefined」/ `invalidateQueries` 从闭包取 `detailId` 失效错 key / `saveBlob` 无 try/finally 漏 Blob URL）→ A273（全仓 112 处路径未编码：类级 low，只修 A246 列出的可被用户输入控制的 5 处）。**中低危第二批（登录页）**：A278 —— `Login.tsx:38` 把 `JSON.stringify(后端错误结构)` 渲染给未登录访客（`detail` 可带内部路由/权限组名，例 `scopes.py:311`）→ 密码规则 `required`+`min:8` 共用一条 message（留空也提示「至少8位」）→ `:20-23` `/health` 能力预检强断言静默兜底 → `:29` 写态不校验（同 A251，不另开号）。自我证伪一条：`main.py:322` 的 `require_group("audit")` 对匿名/no-key 主体是 no-op（`scopes.py:295-307`），且登录页显式传 `baseURL: ''`，所以「/health 预检必 401」不成立。**中低危第三批（审计服务层，挖出一条后端）**：**A279（Merkle 叶子原文 `f"{id}|{action}|{resource_type}|{resource_id}|{created_at}"` 在 `app/api/audit.py:358/421/476/493` 重复 4 次且不转义 `|` ⇒ 跨字段边界歧义落在审计证据基座上；抽 `merkle_leaf_preimage()` 一处收，与 A255 同改）** → **A280（导出只看状态码：2xx 的 HTML 也会被当 `audit_export_30d.csv` 落盘；文件名/Content-Type 是前端复刻后端 `Content-Disposition` 的隐式契约）**。证伪：`anchor` 空数组不会全量/零锚定（`audit.py:352-353` 404），残留只是 `record_ids` 无长度上限（low）；非 Blob 错误其实原样 `throw`（`:191`），不是绕过。**中低危第四批（联邦面）**：**A281（`GET /federation/catalog/entries` 后端 `limit` 默认 100、无 offset、返回裸列表无 total，前端 `federationApi.ts:96` 不透传 ⇒ 目录被静默截到 100 条且 UI 无从分辨）** → A282（远端空间归属编码在 `DataProduct.name` 前缀 + `LIKE` 不过滤 `%`/`_` —— 因该端点本就无按空间授权且要 ADMIN/OPERATOR，我把自己推的「跨空间读取」调低为 latent/low）→ `remote_space_id || ''` 让信任表与同步状态表关联静默失效（归 A257/A262 改造）。**w6 运维面（compose/启动装配）**：**A283（prod compose 裸 `${VAR}` + 全端口绑 0.0.0.0 + Vault dev 模式：`.env` 漏项时 api 会因 `validate_security_config` 拒绝启动，但 PG/Redis/CH/MinIO/Vault 已以空密码在宿主网卡上听者 —— 改 `${VAR:?required}` + 删无需求 ports）** → **A284（OPA `8181:8181` 无认证可写策略 = 授权决策可被任意改写；已证伪 fail-open：无策略返 `{}` ⇒ `.get("result", False)` DENY，不可用时回落 `_evaluate_python`；另记 `:116` 注释与实现不符：每次评估都重推 rego）** → **A285（`CDS_EGRESS_AUDIT_LOG_PATH=/data/egress-audit/…` 与 `CDS_SHARED_VOLUME_ROOT=/data/shared-volumes` 未入卷，与 `:234-236` 注释自相矛盾 ⇒ egress 审计日志容器重建即丢）** → A286（`allkeys-lru`+256mb 驱逐会话/限流计数）。**w6 最终快照补判（三个写操作 + 两个构建/测试口径）**：**A287（遗留管理台三个主写操作 100% 不可用：`connectors.html:61` 的“恢复”永远打 `/suspend`，而后端另有 reactivate 路由；`kms.py:194-199` DELETE 要 `Body(...)` 而 `admin.js` 的 `del()` 不发体；`kms.py:64-68` `product_id` 被 FastAPI 当 query 而模板放 body）** → **A289（`tests/e2e_full_test.py` 会被裸 pytest 采集：`pyproject.toml:3` 无 `python_files` 覆盖 ⇒ 默认含 `*_test.py`，AGENTS.md 的“never collected”判错；12 条打硬编码集群 IP，但四处 requests 都带 timeout ⇒ 有界红，记环境缺失而非代码缺陷）** → **A288（`.dockerignore` 只排 4 项 ⇒ `COPY . .` 把 `.env*`/`.npmrc` 烤进 builder 层；同一 RUN 的 `for` 循环吞退出码被 `test -x` 挡住，降为 latent）**。**中低危第五批（存证验真面）**：**A290（`AuditVerifyModal.tsx:204` 与 `:208` 同层独立 ⇒ 重取失败时「验真请求失败」与上一轮绿色结论同屏，A272 的放大器）** → **A291（`:293` 只看 `proof_path.length===0` 就断言「本批次仅一条」，同响应里 `total_leaves`（`audit.py:519`）已在 `:291` 显示 ⇒ 应双条件；空路径+多叶子要说“证明数据不完整”）** → A292（`:180-186` 披露字段回退 proofData，而 `:177` verdict 只看 verifyData ⇒ 两口来源可矛盾）→ A293（`ErrorCard.tsx:45-46` canRetry/warning 两套口径）。证伪：`pg-log` 绿色不成立（徽标语义＝是否模拟，文案已写「未上链」，`SimulationBadge.tsx:41-56`）。**中低危第六批（下载/输出审查面，含一条 high）**：**A294（跨源部署下脱敏披露静默失效：后端只发 `passed|redacted; findings=N`（`sandbox_sessions.py:1956/1966`），`security.py:204-210` 的 CORSMiddleware **没有 `expose_headers`** ⇒ 跨源时 `parseReviewHeader` 拿到 null（`sandboxApi.ts:276`），而 `NotebookWorkbench.tsx:430` / `SessionDetail.tsx:525` 只对 `redacted` 报警、全仓没有第三态 ⇒ 内容已被脱敏而界面一声不响，破 honest degradation 铁律。修法两端各半：加 `expose_headers` + 前端把 `null` 当要披露的第三态）** → A295（`homeLandingPath` 仍返回 `/home`，`App.tsx:107` 只做重定向；`workspace.test.ts:83` 把陈旧值钉成断言 = 假绿家族）→ A296（`| string` 让联合类型退化、`FileDownloadBlockedError` 死类型、keyless 菜单三连、`== null` 与「禁宽松相等」规范冲突需裁决）。证伪：M-168「所有 409 都被当输出阻断」——`sandbox_sessions.py` 只有 `:1949/:2024` 两处 409，都在 DLP 通道。**中低危第七批（数据面横幅 / 反馈组件 / MPC 服务层）**：**A297（`DataplaneBanner.tsx:26` 绕过 services 直接 `api.get`，而 `metaApi.ts:77-78` 早已封装好 —— 这个绕过就是 A243 双重解包的成因，修 A243 应改调 `metaApi.getCapabilityMatrix()`）** → **A298（`:29-31` 探针失败静默 + `:35` 把 `dp===null` 与「数据面可用」同一渲染 ⇒ 控制面档横幅消失＝完整档，与 A294 同根：全仓缺「披露不可得」第三态）** → A299（antd v5 `Spin tip` 独立用法不渲染 ⇒ `CenteredLoading` 文案从未出现）→ A300（`mode`/`reason` 死字段，后端 `capability_matrix.py:320/324` 已把 `reason="dataplane_disabled"` 送到手边，前端硬编码文案）。降级：M-27「`enabled` 类型漂移」不成立（`:320` 是 `bool(...)`，latent）；M-164 只核到类型面，页面文案未逐行读，不作断言。

---

# 修复映射（2026-10-18 修复轮，本机 3.12 `.venv-gate` 口径）

判据：每组「改前红 / 改后绿」双读数；拿不出本地读数的一律回退或降级为「待部署验收」。台账明细见 `ocr-fixes.md`。

| 结论 | 条目 | 证据（本机实测） |
|---|---|---|
| **已修** | A268（含追加审计留痕） | 改前逐 hunk 回退 ⇒ `2 failed,16 deselected` / `1 failed,17 deselected` / `1 failed … got 0`；改后 `28 passed RC=0` |
| **已修** | A294b CORS `expose_headers` | 新 `tests/test_cors_expose_headers.py` 2 passed；`compileall -q app` RC=0 |
| **已修** | A279 Merkle 叶子 `|` 注入（五处，含我漏计的第 5 处 `cube_egress_audit.py:220`） | 改前 `9 failed, 67 passed` ⇒ 改后 `76 passed` |
| **已修** | A283/A285 prod compose 变量与卷 | 真空文件 `config` ⇒ `RC=1` + 17 行 `required variable`；真实 `.env.prod` ⇒ 缺 4 项被点名（改前静默空值） |
| **已修** | A243/A298/A245/A299 披露与横幅族 | 改前 `5 failed / 4 passed` ⇒ 改后 `3 files / 9 passed`，`tsc --noEmit RC=0` |
| **已修** | A274/A257/A281 服务层解包与取值默认值 | 改前 `17 failed / 33 passed` ⇒ 改后 `50 passed RC=0`，邻居回归 `107/107` |
| **已修** | A272/A290/A291/A275/A276/A277/A280 审计面 | 改前 `15 failed / 6 passed` ⇒ 改后 `21 passed`，存量 `26 passed` |
| **已修** | A278/A261 登录页与合规披露 | 改前 `10 failed / 2 passed` ⇒ 改后 `12 passed`，复跑仍 `12 passed` |
| **已修** | A244 全局 ErrorBoundary | 移出组件 ⇒ `RC=1 Failed to resolve import` ⇒ 加回 `4 passed`；组件层合并门 `13 files / 91 passed` |
| **已修** | A289 `tests/e2e_full_test.py` 被裸采集 | 改前 `12 tests collected` ⇒ 改后 `0 collected / 1 skipped`，`CDS_LIVE_E2E=1` 仍 `12 collected` |
| **已修（显示层）** | 证书 `unknown` 态标签 | 该文件 tsc 0 错误；文案显示属部署验收 |
| **回退** | A288 `.dockerignore` 排除项 | 按字节还原 —— 本机无法验证（要 docker build 上下文），已移入「待部署验收」 |
| **待部署验收** | A288、`unknown` 标签显示、federation/证书页面级提示 | 需镜像构建或真机点击 |
| **登记未修** | A301 remote_deploy fail-fast 漏项 + `CDS_DEBUG=true`；A302 `k8s/cds-app.yaml` 零卷 + `replicas: 2`；A303 middleware 兜底口令 | 验收要跑真实远程车道 / 集群，本机证不了 |

**终局门读数**：后端 `106 passed, 1 skipped / RC=0`（10 文件，118.6s；1 skipped = A289 的模块级开关）；`verify_schema_parity.py` `RC=0`（54 tables, columns identical）。前端门读数见 `ocr-fixes.md`（必须带 `NODE_OPTIONS=--experimental-require-module`）。


## 5. Critical — 立即处理（25 条）

### A1 — `app/api/certificates.py` — 任意已认证用户可导出全部 SM2 私钥（critical）

- `GET /list`(L79-98) 与 `GET /{cert_id}`(L100-119) 都只 `Depends(get_current_user)`；`CertDetailResponse` 继承带 `key_pem: str`（L36-38），L119 原样返回私钥 PEM。
- 先 `/list` 拿到全部 `cert_id`，再逐个 `GET /{cert_id}` ⇒ 全量 TLS 私钥泄露。同文件 `/generate`、`DELETE /{cert_id}` 要 ADMIN/OPERATOR，形成权限倒挂。
- 测试只覆盖 operator 路径（`tests/test_tlcp_service.py`、`tests/test_e2e_sandbox.py:285`），无任何「低权角色读私钥」断言。
- 修法：detail 换 `CertResponse`（不含 key_pem）；私钥导出独立端点 + `require_roles(ADMIN)` + 审计。

### A12 — `app/api/mpc.py:200-215` — 任意已认证用户可取回全部明文 Shamir 分片（critical）

`GET /keys/{key_id}/shares` 只 `Depends(get_current_user)`（`/split`、`/reconstruct`、`/rotate`、`/destroy` 却要 ADMIN/OPERATOR/ADMIN）；`mpc_service.list_shares`（L279-285）→ `_share_from_record`（L123）对每行做 `_decrypt_share_value()` ⇒ `share_value` 以 hex 明文返回。`GET /keys`(L160) 同样只 get_current_user 可枚举全部 key_id。拿到门限值 `threshold` 个分片即可离线拉格朗日插值还原被托管的主密钥 —— 这是比证书私钥更高价值的一层。

### A13 — `app/api/kms.py` — DEK 全生命周期对任意已认证用户开放（critical）

`POST /keys`(L63)、`POST /keys/{key_id}/rotate`(L148)、`DELETE /keys/{key_id}`(L194)、`GET /keys`(L104)、`GET /audit`(L238) **全部只 `get_current_user`**，体内无任何角色/归属判据（读完 rotate 46 行、revoke 43 行确认）。
- `DELETE` 的 docstring 自述后果：「marks as revoked, terminates active sessions, **data becomes inaccessible**」，且真的会 `sandbox_runtime.terminate_sessions_by_key(...)` ⇒ 任意登录账号可对**任意** DEK 发起破坏性操作。
- 它唯一的「高危操作门槛」是 `HighRiskOperationRequest{reason>=8, ticket_id?}`，而 `ticket_id` **只写进审计、全仓无任何校验**（grep 八处全是 `detail={...ticket_id}`）。
- `rotate`/`create` 的 `encrypted_key=_wrapped_key_hex(key_id)` 可为 `None`（函数签名 `-> str | None`）却仍以 `ACTIVE` 落库 ⇒ 产生不可恢复的「有元数据无密文」密钥（ocr 的 fail-open 判断在 create 与 rotate 两处都成立）。
- `GET /keys` 无归属过滤、无 limit ⇒ 全库 DEK 元数据（key_id/版本/usage_count/max_usage）对任意用户可枚举。

### A17 — `app/api/gateway.py` — API 创建的凭据永远无法通过网关认证（critical，已端到端复现）

真机复跑（本机 `.venv-gate` + 真实 TestClient 走产品表面，非推断）：
```
CREATE status: 200            # POST /api/v1/gateway/credentials，expires_hours 默认 720
DB expires_at: datetime.datetime(2026, 11, 17, 23, 59, 59, 809191)   # naive
QUERY raised: TypeError can't compare offset-naive and offset-aware datetimes
  app/api/gateway.py:216 -> app/services/gateway_service.py:119
  if cred.expires_at and cred.expires_at < datetime.now(timezone.utc):
```
根因：`app/models/app_credential.py:50` `expires_at = mapped_column(DateTime)` **无 `timezone=True`**（对比 `Contract.valid_until` 用了 `DateTime(timezone=True)`），而 `gateway.py:114` 写的是 aware datetime。读回 naive ⇒ 与 aware now 比较必抛 TypeError ⇒ 500。`expires_hours` 有默认值 720，所以**任何通API创建的凭据都带 expires_at**，无一例外的会踩。
测试盲区：`tests/test_contract_gateway.py:227` 唯一的网关鉴权用例是直接 `AppCredential(...)` 且**不设 `expires_at`** ⇒ L119 短路，这条路径全仓零覆盖。（同一文件 `gateway_service.py:332-334` 对 JWT 的 `expires_at` 做了 `tzinfo is None` 归一，说明作者知道这个坑、但漏了凭据这条。）
探针已删除（`tests/test_tmp_ocr_probe.py`，rm 后 `git status` 无残留）。

同文件另两条：
- **到期数学错（medium-high）** `gateway.py:114-116`：`datetime.now(utc).replace(hour=23,minute=59,second=59) + timedelta(hours=expires_hours)` ⇒ `expires_hours` 语义从创建时刻飘 0–24h（本次 06:55 创建、720h 得出 11-17 23:59:59而非 11-17 06:55）。另外行内 `__import__("datetime").timedelta(...)` 是个风噪点。
- **`allowed_ips` 在两个端点上完全失效（high）**：`get_metering`(L343) 与 `get_contract_gateway_status`(L409) 硬编码 `client_ip = ""`，而 `gateway_service.py:129` 是 `if cred.allowed_ips and client_ip:` ⇒ 空串直接跳过白名单。`/query`(L212) 与 accept(L306) 传了真 IP，所以只有这两个端点不一致。

### A24 — `app/api/session_stream.py:269-273` — `_run` 里的 `db` 是**全局查找**，共享卷在流式执行里静默不挂载（critical，已实跑）

两条独立证据：
1. **编译期**（`compile()` + `co_freevars`/`co_names`，.venv-gate py3.12）：
```
CODE _stream_loop argcount 3 varnames (ws, session, user_id, ...) freevars () db_in_names False
CODE _run        argcount 0 varnames (shared_volumes, binds)
                 freevars ('_on_line','command','env_vars','runtime','session','timeout')  db_in_names True
module-level db symbol present: False
```
⇒ `db` 不在 `_run` 的自由变量里而是走 **GLOBAL 查找**；`_stream_loop(ws, session, user_id)` 参数表里没有 `db`，模块也没有全局 `db`（真的 `db` 只在 `exec_stream` 的 `async with async_session() as db` 里）⇒ `resolve_binds(db, session.id)` 必抛 `NameError: name 'db' is not defined`，而紧接着的 `except Exception: binds = []`（L274-275）**无日志吐掉**。
2. **实跑**（临时探针已删）：把 `app.services.shared_volumes.resolve_binds` 换成记录调用的假实现后走 WS `start` 帧，`RESOLVE_BINDS_CALLS: 0`，而下游 `runtime.execute_streaming` 确实跑了（返回了 `stderr`+`exit` 帧；本机 stdout 为空是因为 Windows 无 bwrap、L0 诚实拒跑，与本结论无关）。⇒ 调用在**求值参数时就死了**，执行却带着 `binds=[]` 继续。
后果：同一个会话走 `sandbox_sessions.py:231`（非流式 exec）会正常挂载共享卷，走流式 WS 则**永远不挂**，且没有任何日志/告警（违反项目 honest-degradation 铁律：降级必须披露）。
测试盲区：`tests/test_exec_stream.py` 跑通了 WS 认证/帧协议/DLP，`tests/test_shared_volumes.py:129,161` 直接调服务层 `resolve_binds` —— 两者从未接起来，所以这条路径上没有一条能失败的断言。

### A30 — `app/api/sandbox_sessions.py:753` — 游标分页丢弃权限谓词 ⇒ 任何业务用户可枚举全平台会话（**critical，已端到端复现**）

`list_sandbox_sessions` 上面认真构造了 `base`/`count_base`（`_can_view_all_sessions` 判定 + `user_id` + `status`），但 `cursor is not None` 分支里：
```python
rows, next_cursor = await paginate_keyset(db, SandboxSession, cursor=cursor, limit=limit)   # ← 没有 where=
```
`app/core/pagination.py:39` 签名是 `paginate_keyset(db, model, *, cursor, limit, where=None)`，`where` 是**选传**，不传就是 `select(SandboxSession)` 全表。四个调用点里三个传了（`connectors.py:203` `where=where`、`contracts.py:126` 与 `data_products.py:172` `where=and_(*filters) if filters else None`），只有这一处漏了。
又因为 `cursor=""` 也满足 `is not None`（且 `paginate_keyset` 内 `if cursor:` 为假不拼 keyset 谓词），**拿第一页不需要合法游标**：`GET /api/v1/sandbox-sessions?cursor=` 就是全表第一页，后续靠 `X-Next-Cursor` 逐页走完。
端到端复现（.venv-gate 真实 TestClient，探针已删）：
```
OFFSET status: 200 ids: 1 victim_visible: False            # 不传 cursor ⇒ 正确隔离
CURSOR status: 200 items: 4 own_present: True              # 角色 buyer，?cursor=
CURSOR leaked_victim_sessions: 3                            # 3 个他人会话全部返回
LEAK_FIELDS_SAMPLE: {'id': ..., 'user_id': ..., 'data_product_id': ..., 'sandbox_level': 'L3',
                     'status': 'running', 'contract_id': ..., ...}
```
泄露面 = `SandboxSessionResponse` 全字段：`user_id` / `contract_id` / `container_id` / `session_key_id` / `resource_limits` / `trust_root` / `sandbox_engine`。
测试盲区（关键）：`tests/test_pagination.py` 确实测了游标模式，但它的 `pager_user` 是 **`role="admin"`**（L23），而 `_can_view_all_sessions` 包涵 ADMIN/OPERATOR/REGULATOR ⇒ **全部游标断言跑在本来就能看全表的身份上，这条隔离断言不可能失败**。全仓无一条以非特权角色跑游标模式。
修：一行 `where=and_(*filters) if filters else None`（filters 含 `user_id` 与 `status`）；同时补一条非特权角色的跳租户断言。

### A48 — `app/api/sandbox_db.py:262-276` + `secure_duckdb.py:123-133` — 合约策略靠 fire-and-forget 挂接，且未挂时 **fail-open**（critical）

```python
# sandbox_db.py:270-275
loop.create_task(_attach_policy(session_id, engine))   # 不 await、无 done_callback
_engines[session_id] = engine
return _engines[session_id]
# secure_duckdb.py:131-133
if not self._policy:
    return True, "No policy set"        # ← 没策略 = 放行
```
`_attach_policy` 要跑两次 DB 查询（session → contract）才 `set_policy`，而同一次 HTTP 请求紧接着就 `register_table` / `/query` ⇒ `execute_query`（L304 才调 `_check_policy`）在策略到位前执行，**直接放行**；`_attach_policy` 抛异常时 task 无人取回（无 `add_done_callback`）。合约策略是本产品对外的核心承诺，而它在 DuckDB 查询链路上是「尽力而为、失败静默、缺失即放行」。

### A49 — `app/api/sandbox_db.py:79/210` × `280-309` — 默认 `session_id` 永远挂不上策略，异常只进 debug（critical，与 A48 同根）

```python
session_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])   # 两个请求模型（L79 CreateTableRequest / L210 EncryptedTableRequest）
...
select(SandboxSession).where(SandboxSession.id == uuid.UUID(session_id))  # L290 必抛 ValueError
except Exception as e:
    logging.getLogger(__name__).debug(f"Policy auto-attach skipped for session {session_id}: {e}")   # L307-309
```
8 个 hex 字符不是合法 UUID ⇒ 凡用默认 session_id 的会话，`_attach_policy` **100% 失败**，而失败只记一行 debug（生产 `setLevel(INFO)` 下连这行都看不到），策略永远不挂接；再叠加 A48 的 fail-open ⇒ 这条链路上的合约限制等于不存在。修法：默认值改完整 UUID（或直接必填）；`_attach_policy` 失败至少 WARNING + 没策略时 fail-closed。

### A69 — `POST /api/v1/cube-templates` 默认（同步）路径必然失败（critical）

逐环证据：
- `cube_templates.py:84` `async_mode: bool = Query(False, alias="async")` ⇒ 不带参数就是同步。
- `cube_template_service.create_template:454-485`：`db.add(tpl)` + `await db.flush()` + `return tpl` —— **没有 commit**。
- `cube_templates.py:106-112` 在 `await db.commit()`（**:119**）**之前**就 `submit_operation(..., run_async=async_mode, executor=_run)`；`session_operations.py:52-77` 确认 `run_async=False` 时 `await _execute(db, op, executor)` **当场内联执行**。
- `_run` → `build_and_distribute:504-516` 自己开 `async_session()` 并 `db.get(CubeTemplate, template_id)` ⇒ 请求事务里尚未提交的行对新连接不可见 ⇒ `tpl is None` ⇒ `TemplateError("template vanished during build", SESSION_NOT_FOUND, 404)`。
- 回到端点：`:121-123` `if op.status == "failed": await db.commit(); raise CDSError(op.error, code="INTERNAL_ERROR", http_status=500)` —— 语义被压平成 500（ocr 另一条也中了），但**模板行还是被提交了** ⇒ 永久停在 BUILDING 的幽灵行（与第十八批「模板态缺 FAILED」直接叠加）。
- 最硬的旁证在异步分支自己脸上：`session_operations.py:74-78` 注释写着“Persist the operation row BEFORE the response returns: **the background runner uses its own session and must see it committed**”——同一个“跨 session 可见性”陷阱在后台路径上处理了，内联路径漏了。
- 测试盖不住：`grep` 全仓只有一条入口用例 `tests/test_cube_template_dist.py:420`，它用的是 **`?async=true`**；默认同步路径零覆盖（即使补上，SQLite 共享连接也可能掩盖这个缺陷）。
- 修法：同步分支在 `_execute` 前先 `await db.commit()`（与异步分支对齐），或者 `build_and_distribute` 接受已打开的 session。

### A91 — 模板上传对象名与预签名对象名不是同一个（critical，与 A69 是同一链路上的两处独立断路）

```
app/services/cube_template_service.py:350-351  def minio_object_name(artifact_id): return f"{_MINIO_OBJECT_PREFIX}/{artifact_id}.ext4"
app/services/cube_template_service.py:383          object_name = minio_object_name(Path(artifact_path).stem)   # 上传
app/services/cube_template_service.py:370-372      client.presigned_get_object(bucket, minio_object_name(artifact_id), ...)   # 分发时预签名
scripts/cube-rootfs-convert.sh:104                 mv "$EXT4" "$OUT_DIR/artifact.ext4"
```
⇒ 构建脚本永远输出 `artifact.ext4`，`Path(...).stem` 恒为 `"artifact"` ⇒ 上传 key 永远是 `cube-templates/artifact.ext4`，而预签名的是 `cube-templates/<artifact_id>.ext4` ⇒ 节点下载必 NoSuchKey，分发永远到不了 READY。叠加后果：所有模板写同一个 key ⇒ 互相覆盖，而节点按 sha 注解校验会撞上别人的 rootfs（sha mismatch）。套件拓不到：`tests/test_cube_template_dist.py:386` mock 了 `upload_artifact`、`:393` mock 了 `presign_artifact_url` ⇒ 两侧都被掉包，key 对称性从来没人比较。修法：上传也走 `minio_object_name(artifact_id)`（把 artifact_id 一路传迕 `upload_artifact`），并补一条不 mock 的 key 相等断言。

### A110 — 合约签名 payload 存在分隔符注入：purpose 里塞 `|purpose_scope:X` 与真的给出 scope 得到同一串字节（critical）

```
app/services/crypto_service.py:207-220  contract_sign_data(...): base = f"CDS-SIGN|{contract_id}|{contract_no}|{party_role}|{timestamp}"
                                          base += f"|purpose:{purpose}"; base += f"|purpose_scope:{','.join(...)}"
app/schemas/contract.py:31,56-63        purpose_scope: list[str] | None；唯一校验是“不能全空字符串”，不禁 `|`/`,`、不限长
app/api/contracts.py:86,96 → app/services/contract_service.py:128   入参直接进 payload
app/api/sandbox_tasks.py:48-53 / inference_service.py:146-149  执行期用 `purpose in scope` 做用途限定
```
本机门（py3.12.10，`probe_payload.py`）实测：`purpose="研究|purpose_scope:营销", purpose_scope=None` 与 `purpose="研究", purpose_scope=["营销"]` 生成的 payload **逐字节相等**（`collision1: True`）；`purpose_scope=[]` 与 `None` 也不可区分（`empty-vs-none: True`）。**纠正 ocr 的第二个例子：`["a|b"]` 与 `["a","b"]` 并不碰撞**（前者拼出 `a|b`，后者 `a,b`，实测 `collision2: False`）。
危害边界（不强判）：payload 含 contract_id/no/timestamp，所以这不等于跨合约伪造签名；真实危害是**同一份签名同时为两种互斥的用途限定背书**（A4 purpose limitation 的不可抵赖性失效：对方事后可以拿同一签名声称当时约定的是另一个 scope）+ 显式空 scope 被静默当“不限制”。修法：payload 改长度前缀或 `json.dumps(..., sort_keys=True, separators=(',',':'))` 规范编码，并在 schema 层禁止 purpose/scope 元素含 `|` 与 `,`。

### A111 — 单元格真实输出对输出 DLP 不可见：DLP 看的是 base64 信封，明文解码后直落 notebook 并回前端（critical）

```
app/services/notebook_service.py:3-5    模块自述：“…输出 DLP → 审计…因此 Notebook 不可能成为输出策略的旁路”
app/services/notebook_service.py:22,140,180  KERNEL_MARKER = "CDS_CELL_B64::"；_emit() 只往 stdout 写 base64(json)
app/api/sandbox_sessions.py:1411-1434   output = exec_result.get("output") → inspect_text_output(output) → should_block / redacted_output
app/services/notebook_service.py:429-441  先取已扫描的 raw["output"] 解信封，拿到明文 stdout/stderr/repr；仅把 raw 的 output_blocked 转标
app/services/notebook_service.py:351-360  apply_cell_result 把明文写进 outputs（stream/text 与 execute_result text/plain）
app/api/notebooks.py:30,463,487          _clean 只按计划选键，解码后的 stdout/repr 不再过一次内容审查
```
因果链：正则/模式类 DLP 在 base64 噪声上跑，几乎不可能命中 ⇒ `should_block` 恒为假 ⇒ 本函数 `blocked_reason` 分支形同虚设；而平台自己把信封解成明文写进 `notebook.cells[].outputs` 并返回前端 ⇒ **Notebook 就是输出策略的旁路**，与模块自己的声明相悖（项目铁律 honest degradation 方向）。修法：`run_cell` 解信封后对 `stdout/stderr/repr` 再走一次 `inspect_text_output`（或把信封改为先上报结构化输出、由控制面审查后再入档），审查不过则整单元格输出置换为占位与 `output_blocked`。

### A114 — 管理端 5 个模板把服务端字段直接 innerHTML，全仓无 HTML 转义工具，token 存在 localStorage（critical）

```
app/templates/{alerts,blockchain,certificates,connectors,identities}.html   共 13 处 innerHTML（列表渲染 `list.map(...)` + `加载失败: ${e.message}`）
app/ 全仓 grep                                        escapeHtml → 0 命中；createTextNode → 0；textContent 只用于几个计数卡片
app/static/js/admin.js:24                              const token = localStorage.getItem('cds_token')
app/services/cross_space_policy.py:155                 message=f"Operation '{operation}' not in trust allowlist"   # 告警文案里插调用方传入的 operation
app/api/…/API.get 失败文案                              `${res.status}: ${await res.text()}` → 写进 innerHTML（alerts.html:62 等）
```
已证事实：无任何转义；服务端返回的字段（告警 `message`/`alert_type`/`severity`/`status`、审计 `detail`、证书/连接器名称）全部当 HTML 解析；`badge()/statusBadge()` 内部也是模板串拼接；`e.message` 透传整个响应体。可操纵入证：`cross_space_policy.py:155` 把请求里的 `operation` 直接写进告警 message（其余字段是否可注入需逐接口看 schema，**未逐一取证**，不把“存储型 XSS 已可达”当作已证事实）。危害面：管理端需要 admin 角色，但 XSS 一次成功即可拿 `localStorage.cds_token` 当合法会话用。修法：`admin.js` 加 `esc()` 并在所有插值处包上，`badge()` 类 helper 内统一转义，错误提示改 `textContent` 且不透传响应体。

### A116 — `CDS_SESSION_MAX_SNAPSHOTS=0` 让快照接口直接 500（critical，已实测）

```
app/services/session_snapshots.py:79-84  max_n = settings.SESSION_MAX_SNAPSHOTS; while len(existing) >= max_n: oldest = existing.pop(0)
app/core/config.py:162                   SESSION_MAX_SNAPSHOTS: int = 10   # 无 field_validator、无下限
app/api/sandbox_sessions.py:2123         @router.post("/{session_id}/snapshots")   # 活路由
```
本机门实测（`probe_snap.py`，`CDS_SESSION_MAX_SNAPSHOTS=0`，工作区含一个文件）：`RAISED: IndexError pop from empty list`（`max_n=0` 且驱逐完仍满足 `len([]) >= 0`）；负数同理。一个环境变量就能把创建快照打成 500，而“0 = 关闭快照”的意图完全没实现。修法：`max(1, ...)` 钳制或显式把 0/负当禁用并提前返回。

### A129 — 空文本对所有引擎都返回 512 维零向量，索引声明的 dim 因此错误，不匹配时评分静默归零（critical，活路径）

```
app/services/rag_embedding.py:25    TF_DIM = 512
:148-150  if not text: return EmbeddingResult(vector=[0.0]*TF_DIM, engine=engine, dim=TF_DIM)   # 不分引擎
:158      transformers 引擎非空文本：dim=len(vec)（模型隐藏层，常见 384/768）
app/services/rag_service.py:205     dim=embedder.embed("").dim      # 索引用空串推自描述维度
rag_service.py:506-508 / rag_embedding.py:165-167   len(a) != len(b) → return 0.0
```
可达性：`rev_imports2.py` 的 AST 反向导入闭包给出 `main.py reaches it? : True`（rag_service 同时被 `app/api/rag.py:75`、`sandbox_tasks.py:144`、`inference_service.py:308` 使用）。“同一引擎→同一维度”的契约在空/非空两个分支上不一致，而 `build_corpus` 恰好拿空串那次调用来定索引维度 ⇒ engine=transformers 时写入清单（`:63`/`:213`）的 `dim` 永远是 512。最关键的下游后果不是报错而是**静默**：两处 cosine 都对长度不等直接 `return 0.0`（不是抛错也不是截断），所以一旦查询侧向量与入库侧向量维度不同（例如沙箱内 runner 的 `input/embedding_model.bundle`（`rag_service.py:335`）缺失而回退到 512 维 tf_embed），整个 RAG 检索会“成功”返回但**每条得分都是 0.0**，与项目“诚实降级”铁律相冲。修法：空文本返回与引擎实际维度一致的零向量（或返回 `dim=None` 并禁止用它定索引维度）；`build_corpus` 改为对一条非空样本文本取 dim；cosine 维度不等改为抛 `ValueError` 并在索引清单里校验。

### A161 — KMS 密钥库是进程内存，而 `storage_service`/任务码/工作区三类密文只存 `key_id` ⇒ 重启即永久解不开（critical，活路径）

```
app/services/kms_service.py:39-41   self._wrapped_keys / _sm2_encrypted_keys / _cert_public_keys = dict()   # 全部在内存
app/services/kms_recovery.py:22-45  restore_wrapped_keys()：只 SELECT `KeyMetadata.wrapped_payload IS NOT NULL` 的行→ import_wrapped
app/main.py:114-116                 启动时只调这一个恢复函数（grep DataEncryptionKey 在 recovery/main 里 0 命中）
持久化方只有三处：api/connectors.py:506、api/sandbox_sessions.py:658、contract_fulfillment.py:175（都写 KeyMetadata + export_wrapped）
不持久化的生成方：storage_service.py:110（每个对象一把 DEK）、task_code_security.py:15（任务源码加密）、sandbox_runtime.py:945（workspace 密钥）、task_worker.py:117、api/kms.py:71/163（只写 DataEncryptionKey，恢复函数不读它）
```
后果链：`storage_service.upload`（`:103-130`）把 `key_id+nonce+tag+密文` 包成 envelope 写进 MinIO/本地盘，DB 里只留 `key_id`；`download:183-187` 靠 `kms.get_key(key_id)` 取回 DEK，取不到就 `raise ValueError("密钥 … 在 KMS 中不存在，数据无法解密（可能是密钥为内存态且已重启）")`——**代码自己就把根因写在报错里了**。一次 api 重启/重部署就让此前上传的全部对象与全部已落库任务源码（`decrypt_task_code`）永久不可解，而且没有任何补偿路径（KEK 在 HSM 里也没用，wrapped blob 从未落库）。`storage_service` 与 `task_code_security` 均 `main.py reaches it? : True`。修法：凡 `generate_data_key` 就必须同事务写一行 `KeyMetadata(wrapped_payload=export_wrapped(...))`（已有契约：crypto-erase 时销毁），并把 `restore_wrapped_keys` 改成同时回填 `DataEncryptionKey`；加一条“新生成密钥必须有持久化行”的门测。

### A162 — TEE 能力证据可由 PATH 上的任意同名文件伪造，对外发布 `tee_status="verified"`（critical，活路径）

```
app/services/tee_capability.py:74-82  _detect_provider：`if Path(device).exists()`（普通文件即可，实测 `st_mode=0o100666` 也算）
                                    `path = shutil.which(command)`——不传 `path=`，受调用环境 PATH 与可写目录支配
app/api/monitoring.py:71-77         if tee_capability.hardware_available and hardware_hooks_configured: tee_status = "verified"
GET /api/v1/monitoring/security-posture（app/main.py:333 挂载）
```
子代理实跑过：在 PATH 里一个可写目录放个假的 `sgx-detect.bat`，`detect("sgx")` 就返回 `hardware_available=True`，安全态势接口随即把 `tee_status` 标为 **`verified`**、`tee_gate="pass"`——不需要伪造任何硬件/quote，只需一个可写 PATH 目录（容器里 `/tmp` 常被加进 PATH，或依赖包自带同名脚本）。这是项目铁律“禁伪造 TEE”最直接的绕过：它污染的是“这台机器到底有没有 TEE”这个对外结论。同文件另一条：未知的 `TEE_MODE` 会回退成 `provider="tdx_guest"` 并给出“No hardware TEE signal detected”（把配置拼错误归因为硬件缺失）。修法：只接受字符设备（`stat.S_ISCHR`）与绝对路径白名单内的命令（`shutil.which(cmd, path="/usr/sbin:/usr/bin")`），并优先要求真 quote 而非“命令存在”。

### A179 — “生产传输是 mTLS”在代码里不存在：`NewKMSClient` 写死明文 `http.Client`，也不校 scheme（critical，活路径）

```
cds-volume-plugin/kms.go:47-52  func NewKMSClient(...) { hc: &http.Client{Timeout: kmsTimeout()} }   # 唯一构造处，无 TLSClientConfig/CA
全仓 grep `TLSClientConfig|CheckRedirect` 在 cds-volume-plugin/*.go 除注释外 **0 命中**；两个调用方（main.go:55、cli.go:45）都用这个构造函数
config.go:61 默认 `KMSBaseURL = http://127.0.0.1:8000`（http！），`NewKMSClient` 不校 scheme、不拒非 https
kms.go:103 RegisterDEK body = {volume_id, name, dek_hex, cipher}，:70-72 每个请求带 `Authorization: Bearer <service token>`
```
文件头注释与 `cds-volume-plugin/AGENTS.md` 都写“mTLS with certificate_service CA is the production transport … the client carries a `*http.Client` so the transport is injectable”——字段确实可注入，但**没有任何生产代码注入它**，也没有另一个构造函数/选项。后果：只要 `CDS_VOLUME_PLUGIN_KMS_BASE_URL` 不是本机回环（跨节点部署就是这种），插件就把**明文 DEK 的 hex + 服务 token**以明文 HTTP 发出去；而默认 `http.Client` 会跟随 307/308 并重放请求体，等于可把明文 DEK 再递给重定向目标。这是“密钥托管”面上的真实泄露通道，不是加固建议。修法：`NewKMSClient` 拒绝非 `https`（显式 dev 开关除外）、注入带控制面 CA 的 transport、`CheckRedirect` 拒绝跳 host，并在 `verify.sh`/preflight 里加一条“scheme 必须 https”的门。

### A183 — 密钥下发的“远程证明门”验的是本地静态根自签的 quote，且测量白名单在这条路上恒为空（critical，活路径）

```
app/services/remote_attestation.py:36   _LOCAL_ATTESTATION_ROOT = b"cds-local-attestation-root-v1"     # 写死在源码里的常量
:49                              _sign_quote_payload = sm3_hash(_LOCAL_ATTESTATION_ROOT + canonical)
:53-55                           _signature_valid：`signature == _sign_quote_payload(payload)`——重算同一个哈希，不验厂商签名链
SGXProvider.verify_quote:195-240：① `parsed.get("type") != "sgx_ecdsa"` 才拒（type 自述）；② `parsed` 里的 quote_id/measurement 与用**同一份 JSON** 构造出的 TEEQuote 字段自比 ⇒ 恒成立；③ 要求 `measurement == sm3_hash(f"sgx-enclave-{quote_id}")`——任何人都算得出；④ `_signature_valid`
app/services/kms_service.py:314-316      policy = AttestationPolicy(require_fresh_quote=True, max_quote_age_seconds=300); result = service.verify_quote(tee_quote); return result.verified
app/services/remote_attestation.py:127                   allowed_measurements 默认空集 = allow all
app/services/remote_attestation.py:504-521              单例构造函数才从 `CDS_ATTESTATION_ALLOWED_MEASUREMENTS` 读白名单——KMS 自己 new 了 policy，**绕过这段**
app/core/config.py:549-553                              prod 下 `KMS_REQUIRE_ATTESTATION` 却白名单为空 → 启动报错（“否则接受任意 TEE”）
```
两层叠在一起：**(a) 验签不是验签** —— “`sgx_ecdsa`” 分支实际在比一个源码里公开的常量派生哈希，拿得到仓（或镜像/日志里的常量）的一方可以离线伪造一个“硬件 quote”；**(b) 模拟拒收门看的是自述字段** —— `kms_service.py:404-420` 的 `if tee_quote.quote_type == QuoteType.SOFTWARE_HASH and not ALLOW_SIMULATION: return False` 里，`quote_type` 来自 `parsed.get("type", "software_hash")`（:376-389，攻击者/客户侧自填），把 `type` 写成 `sgx_ecdsa` 就不是“模拟”了；**(c) prod 强制的测量白名单在真正下发密钥的路上不生效** —— `remote_attestation.py:504-521` 只在单例构造时读配置，`kms_service.py:314` 自己 new 的空集合策略使白名单形同不存在（而 `config.py:549` 自述“不设就是接受任意 TEE”）；**(d) 代码已经做了诚实披露但下游不消费** —— `:527-533` 给本地根结果标 `trust_root="local-simulated"`，而 KMS 只读 `result.verified`。
可达链：`api/sandbox_sessions.py:556/586`、`api/connectors.py:475` 都经 `kms_service.distribute_key(... attestation ...)`；证明文本由 `_attestation_from_provision` / `_attestation_from_session`（:1254，从 `session.resource_limits["attestation"]` 回读）提供 ⇒ 不是公网直传，而是**能污染 provision/session 记录的一侧**（运行时适配层、guest 回传内容、DB 写入）就能拿到只在硬件 TEE 假设下才应下发的 DEK。修法（三行能堆上）：`kms_service.py:314` 改成复用已配好的单例策略（或显式传 `allowed_measurements=settings 解析值`，空集直接拒）；`verify_quote` 对非 `local-simulated` 信任根才当硬件；`_verify_attestation` 把 `result.trust_root` 当门而不只是 `verified`。

### A185 — 水印写回对 numpy / 非连续 tensor **静默 no-op**，却报 `success=True`（critical，活路径，铁律“禁伪造”直接命中）

```
app/services/model_watermark.py:262-270  def _set_weight_value(cls, tensor, idx, value):
    # torch tensor
    if hasattr(tensor, "flatten") and hasattr(tensor, "__setitem__"):    # :266  numpy 数组同时满足这两个条件
        flat = tensor.flatten()      # numpy.flatten 永远返回副本；torch 仅在连续时可视为 view
        flat[idx] = value            # 写进临时数组
        return                       # 不回写、不报错、不回读校验
# numpy array
    if hasattr(tensor, "flat"): tensor.flat[idx] = value; return          # :271-273 被 :266 抢先，永不达
```
判定：我自己读过分支，子代理又实跑过（numpy 入参下 embed 报 success 但权重一字未改）。真正危险的不仅是“numpy 被当成 torch”：**只要 `flatten()` 返回副本（非连续 torch 张量、转置/切片的权重）这一路径就整体失效**，而函数本身无任何“写后回读”断言。上层 `training.py:834` 直接拿 `checkpoint.state_dict` 调 `embed_watermark`，随后 `:839 _save_model_checkpoint` 落盘、`:848` 写入“已水印”的审计记录 ⇒ 对外声称的权属标记实际一个位都没写进去，而 `verify` 事后也只会“验证失败”， nobody 知道是当初就没写。修法：删掉 :271 那个死分支，改写为 `view = tensor.reshape(-1)`（numpy）/ `tensor.view(-1)` 失败即拒，写后 `assert` 回读一致，否则 `raise`。

### A196 — rootfs 读回路径跟随 guest 可控符号链接⇒宿主 root 任意文件读；FIFO/设备节点⇒挂死与 loop 挂载泄漏（critical，活路径，我逐行复验）

`cds-volume-plugin/rootfs.go:98-111` 就是这三步：`src := filepath.Join(mountPoint, rootfsUpperSubdir, cubeboxID, "upper", rel)` → `os.Lstat(src)` 后**只拒 `fi.IsDir()`** → 用 **Lstat 的尺寸**卡 `maxSandboxFileBytes` → `os.ReadFile(src)`。`src` 在只读回环挂载的 overlay **upper 层**里（`rootfs.go:17-25` 注释自述 guest 写入走上层），而 `os.ReadFile` 会**跟随符号链接**：沙箱把 `results/.cds-exec/<exec_id>` 做成指向 `/etc/shadow` 的 symlink，链接自身 11 字节通过 64 MiB 上限，`ReadFile` 在**宿主命名空间**解析绝对目标，而且插件是以 root 跑（`rootfs.go:86` 的 `mount -o ro,loop` 需要 `CAP_SYS_ADMIN`），字节直接回到控制面，再原样喂进租户的 `execute()` 输出（`app/services/cube_runtime_adapter.py:593`、`:923`、`:938-947`）。同一道过滤器（只拒目录）还放过 **FIFO 与字符设备**：特殊文件 `Lstat` 尺寸是 0 ⇒ 必然过上限，`os.ReadFile` 对 FIFO 永久阻塞、对 `/dev/zero` 型设备无界读到 OOM；两种情形都把 `defer` 里的 `umount` 卡住，叠加 rootfs #4 “umount 失败后的清理被静默丢弃” ⇒ 挂载点与 loop 设备持续泄漏。修法：打开时用 `openat2(RESOLVE_BENEATH|RESOLVE_NO_SYMLINKS)`，要求 `Mode().IsRegular()`，**打开后重新 `fstat` 限长并从 fd 读**（不要二次按路径打开）。

### A243 — 数据面状态横幅永远不会渲染：同一个响应在两个地方被解了两次 `data`（critical，活路径，我两边对照确认）

`services/api.ts:37-39` 的响应拦截器已经把响应解包了：`(response) => response.data`。同一个 `/meta/capability-matrix` 在两个文件里被当两种东西用：
```
pages/CapabilityMatrix.tsx:30   const levelKeys = Object.keys(data.matrix);        ← 把 resolve 值当 body（正确）
components/Layout/DataplaneBanner.tsx:27  setDp(((resp.data ?? {}) as {dataplane?}).dataplane ?? null)   ← 又取一次 .data（错）
components/Layout/DataplaneBanner.tsx:37  if (!dp || dp.enabled) return null;
```
⇒ `resp.data` 在运行时永远是 `undefined`（拦截器已拆一层），`?? {}` 接住后 `.dataplane` 为 `undefined` ⇒ `dp` 永远是 `null` ⇒ `:37` 直接 `return null`。TS 不报错是因为 axios 的类型仍声明成 `AxiosResponse`——这正是拦截器拆包后最典型的一类错：**编译期看不出来，运行时静默失效**。后果按项目铁律是严重的：数据面（cube/L2'）未启用时的全局横幅就是 honest degradation 的披露面，它不渲染等于把「没接上」当成「一切正常」。修法：`resp` 直接当 body 用（与 CapabilityMatrix 一致），或者给这个调用关掉拆包。同一类“双重解包”在本仓很可能不止一处，排修时把 `.get(` 后接 `.data.` 的写法整片扫一遍。

### A268 — `GET /mpc/keys/{key_id}/shares` 只要登录就能拿到全部分片值（critical，我把整个路由读完，并把同文件八个路由的依赖全列了一遍）

`app/api/mpc.py:200-221`：
```python
@router.get("/keys/{key_id}/shares")
async def list_shares(key_id: str, db=Depends(get_db), current_user: User = Depends(get_current_user)):
    shares = await mpc_service.list_shares(db, key_id)
    if not shares: raise HTTPException(404, …)
    return [ShareResponse(..., share_value=s.share_value, threshold=s.threshold, total_shares=s.total_shares, holder=s.holder) …]
```
函数体 21 行我全读了：**没有任何权限 helper、没有 owner/租户比对、没有角色要求**（不是我“没看到”，是从 `:206` 进函数到 `:221` 返回只有 404 分支）。对照同文件写侧：`:228 rotate` 与 `:260 destroy` 都是 `Depends(require_roles(UserRole.ADMIN))`，`:94 /split` 与 `:138 /reconstruct` 是 `require_roles(ADMIN, OPERATOR)` —— **只有读侧不设门**。后果不是“多看一眼元数据”：`share_value` 是完整的 Shamir 分片，加上 `index`/`threshold`/`total_shares`，一个**任意登录用户**（curl 即可，不需前端）拿够 `threshold` 份就能在本地离线 Lagrange 插值把密钥重出来，不必调 `/reconstruct`（那个反而是要角色的）。换句话说本仓的密钥托管模型（AGENTS.md：`mpc_service` = Shamir key custodianship，不是 MPC 计算）在读取面上形同虚设。同一缺口还有两处：`:163-166 GET /keys` 也是 `get_current_user` 且 `mpc_service.list_keys(db)` **签名里根本没有 user 参数**，无从过滤 ⇒ 全平台密钥可被任意登录用户枚举；`:184 GET /keys/{key_id}` 同。修法：三处读侧至少 `require_roles(ADMIN, OPERATOR)`，并给 `list_keys` 加上 owner/租户参数；`share_value` 建议根除——分片应该只发给各 holder 自己的通道，不该汇总给一个 API 调用者。

### A283 — 生产 compose 裸插值 + 全端口绑宿主：空密码栈会完整起来，只是 api 自己不肯跑（critical）

`docker-compose.prod.yml:8 POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}`、`:21 --requirepass ${REDIS_PASSWORD}`、`:45 CLICKHOUSE_PASSWORD`、`:53 MINIO_ROOT_PASSWORD` 全是**裸 `${VAR}`**（compose 对未定义变量只 warning，静默替成空串）。同时 `:10/:24/:36/:37/:50/:51/:67/:87/:108/:249` 把 5432/6379/8123/9000/9001/9002/8181/8200/8000/3000 **全部映射到宿主 0.0.0.0**（没有一处 `127.0.0.1:` 前缀），`:90 VAULT_DEV_LISTEN_ADDRESS: "0.0.0.0:8200"` 还是 **dev 模式的 Vault**（内存存储、自动 unseal、root token 在日志里）。
必须同时记下已存在的**半条防线**：`app/core/config.py` 的 `validate_security_config` 在生产下确实会拦住应用侧——`:401` JWT 等于默认值、`:409` 短于 `_JWT_MIN_KEY_LENGTH`、`:488` `MFA_SECRET_ENCRYPTION_KEY` 为空、以及 `COLUMN_ENCRYPTION_KEY` / `CUBE_TEMPLATE_SIGNING_KEY` 等都会 `raise ValueError`。所以 `.env` 漏项的后果不是“静默不安全地对外服务”，而是“**api 不启动，但 Postgres/Redis/ClickHouse/MinIO/Vault 已经以空密码或默认密钥在宿主网卡上听者**”（`docker compose up -d` 不会因 cds-api crash-loop 而回滚）。修法：变量改成 `${VAR:?required}`（缺项直接 compose 报错不启动）+ 删掉无外部需求的 `ports` 映射（服务间走 bridge）+ Vault 换非 dev 模式与 TLS。

### A309 — `masking_applied` 无条件为 true ⇒ 脱敏预览弹窗在说谎（critical-honesty，**后端**）

`app/api/data_resources.py:373` 无条件返回 `"masking_applied": True`；`_desensitize_csv`（`app/services/test_data_generator.py:199-215`）只掩码 sensitivity∈{pii,sensitive,high} 的列，而上传推断 schema 每列都是 `"sensitivity": "unknown"`（`data_resources.py:55`）⇒ 普通 CSV 实际未脱敏，`DesensitizedPreviewModal.tsx:56-58` 却告诉数商「PII 字段已按策略掩码」。失败测试应写在后端（无 pii 列 ⇒ `masking_applied is False`）。


## 6. High（102 条）

### A2 — `app/api/compute.py::_check_resource_override` — 规格上限校验 fail-open（high，违背注释里的铁律）

- L92-101：`approved = contract.sandbox_resource_spec or {}`，只有 `isinstance(ceiling,(int,float))` 才比上限 ⇒ **合约没写该键就完全不设限**。
- `sandbox_resource_spec` 在模型（`app/models/contract.py:101` JSON 可空）和两个创建 schema（`app/schemas/contract.py:174`、`app/schemas/flow.py:32`）都是 `| None = None` ⇒ 新建合约即可不带规格。
- schema 侧 `override_keys`（`app/schemas/flow.py:102-112`）只限键名+正数，**无绝对上限** ⇒ 合约当事方可提交 `timeout_seconds=315360000`、`memory_mb=1e9`，最终在 `compute_scheduler.py:175` 三层合并生效。
- 同口径还出现在 `app/api/notebooks.py:97,243`（改的时候一起看，不单独判缺陷）。

### A3 — `app/api/data_pipeline.py` — 上传文件名未净化导致越界写（high）

- L58-62：`user_dir = UPLOAD_ROOT / str(uid)`，`file_path = user_dir / file.filename`，随后 `open(file_path,"wb")`。`Path` 的 `/` 语义：filename 为绝对路径时左侧整体被丢弃；`../../` 亦可逃逸。同名文件静默覆盖。
- 鉴权是 `require_roles(ADMIN, OPERATOR, DATA_PROVIDER)`（L51）⇒ **不是任意用户**，但 DATA_PROVIDER 已足以在容器内越出自己的上传目录写文件。ocr 报 critical 偏高，按 high 记账。
- 同文件 `POST /process-path`（L107-134）`input_path: str = Query(...)` 只 `exists()` 检查，ADMIN/OPERATOR 可让流水线读取任意宿主路径（`tests/test_data_pipeline.py:132+` 就用裸 `/tmp/test`），目录入参还会走 `copytree` ⇒ DoS。列为「特权面缺 allowlist」。

### A4 — `app/api/field_exposure.py` — 拒绝意图被反转成全量批准（high）

- L281 `if body.rejection_reason:` 是**真值判断**；`app/schemas/field_exposure.py:83-91` 的 `validate_reason` 只 `strip()` 不把空串转 None ⇒ 传 `"   "` 得到 `""` ⇒ 落到 else 分支。
- L287 `approved = body.approved_fields if body.approved_fields else request.requested_fields` ⇒ `[]`/`None` 都当作「批准全部申请字段」（schema L80 注释确认 None 语义，但空列表与空白理由的组合是缺陷）。
- 结果：提供方一次手滑的「空白拒绝理由」= 把含 PII 的全部字段授出去。修法：显式 `decision` 字段或 `is not None` 判定 + 空列表按「不批准」。

### A5 — `app/api/audit.py` — 监管接口的结论与文案自相矛盾（high）

- L426-438：`verified = await blockchain_adapter.verify(...)` 与 `note` 完全解耦，note 只看 `anchor_backend` ⇒ 校验**失败**时响应是 `verified:false` + `"联盟链存证校验通过"`。面向监管的字段，前端只读文案就会误判。
- L348 锚定查询 `select(AuditLog).where(AuditLog.id.in_(...))` **不排除已锚定行**，L375 无条件覆盖 `blockchain_tx_hash`；而 `/verify`(L415,483) 是按 tx_hash 反查兄弟记录重算根 ⇒ 重锚定后旧锚点的成员集合与当初叶子不一致，产生「未篡改却验不过」的假阳性。
- `AnchorRequest.record_ids`（L157）无长度上限 ⇒ 巨型 `IN` + 同步建树跑在事件循环里。

### A6 — `app/api/connectors.py` — 列宽 8 写入 15 字符，PG 上注册接口必 500（high）

- `app/models/connector.py:42` `api_key_prefix: String(8)`；`generate_api_key()`（同文件 L87-92）产出 `cds-conn-` + 43 字符，写入处取 `api_key[:15]`（`app/api/connectors.py:171` 注册、`:306` 轮换）。
- PG 抛 `value too long for type character varying(8)`；本地/CI 门是 SQLite 不校验长度。**`tests/` 里没有任何测试打 `/api/v1/connectors/register`**（grep 到的 `/register` 全是 `/auth/register`），所以远程 PG16 车道也照不出。

### A7 — `app/api/api_keys.py` — 轮找回读未加锁（high，与 revoke/PATCH 互为竞态）

- rotate 用 `db.get(ApiKey, key_id)` 做 check-then-act；PATCH 的 `status` 读改写同样无 `with_for_update()` ⇒ 并发下已撤销的密钥可被写回 `active`，且 `revoked_at/revoke_reason` 残留成「active 但已撤销」的不一致终态。

### A14 — `app/api/auth.py:366-390` + `app/models/user_mfa.py:17-18` — MFA 可被单因素静默关闭（high）

登录门禁是 `auth.py:190  if mfa and mfa.confirmed:`；而 `POST /mfa/enroll` 只需 `get_current_user`，进去就 `mfa.confirmed = False` 并清空恢复码。模型 docstring 明写「enrolled-but-unverified **does not block** password login」⇒ 拿到密码者调一次 enroll 即把该账号降级回纯密码登录。`/mfa/enroll` 自己的 docstring 却说「login is blocked until re-verification」——**两份文档互斥，代码站在「不拦」那一侧**。

### A15 — `app/api/k8s_ops.py:33` — 少一个 `await`，整个 ops 实机视图永久失效（high）

`get_k8s_adapter()` 在 `app/services/k8s_sandbox.py:801` 是 `async def`，这里不 await ⇒ `adapter` 是协程对象；`getattr(adapter,"_k8s",None)` 取不到 ⇒ 调用方一律返回 `cluster_available: False`（并漏一条 unawaited-coroutine RuntimeWarning）。不抛异常所以 `except` 分支也不触发。
**测试把坏行为钉住了**：`tests/test_k8s_ops.py` 四处 `assert body["cluster_available"] is False`（L17/57/83/116）——这个断言在 bug 存在时永远绿，修好反而会红。属于「不可能失败的断言」家族。

### A19 — `app/api/contracts.py:184-215` — 激活无锁 ⇒ DP 预算重复分配（high）

`get_by_id` 无 `with_for_update()`，`status=="active"` 早退 + `!="signed"` 拦截后直接 `status="active"` → `dp_budget_ledger.allocate(...)` → `fulfill_contract(...)`。两个并发激活（双击/重试/多副本）都读到 `signed` ⇒ **ε 预算被分配两次（隐私预算超授）**且履约两次（两套沙箱会话）。

### A21 — `app/api/output_control.py` — 不传 session_id 即同时绕过授权与合约上限（high）

三处叠加，均为盘上代码：
1. `GatewayRequest.session_id: str | None = None`(L288) 可省；`_authorize_output_session` L49 `if not session_id: return` ⇒ **不传就完全不做到属校验**；同一个条件在 handler L113 `contract_id = await session_contract_id(...) if body.session_id else None` ⇒ **也不取合约上限**，`build_output_policy(db, None)` 直接给平台默认值。docstring 自己写的 `Gap E1: ... may only narrow the contract's allowance, never widen it` 在这条路径上不成立：合约若比默认更严（例如只放行 100 行 / 仅 csv），省掉 `session_id` 就能拿到默认额度。
2. `_authorize_output_session(create_if_missing=True)` 是**默认值**(L47)，`_authorize_owned_session_id` L74-93 先 `db.get(SandboxSession)`；查不到时（session 不存在 / 非 UUID 字符串走 `session_uuid=None`）→ L91 `owner_store[session_id] = current_user.id` **直接认领为 owner**，之后其他合法用户反而 403。这把「鉴权失败」变成了「自动授与所有权」。
3. 两个 owner 表是**模块级 dict**(L17-18)：无锁的 get-then-set、无 TTL/淘汰（无界增长）、多 worker 下不共享（同一 session 在 A 进程属于 X、在 B 进程可被 Y 认领）、重启即丢。`_dp_budget_owners` 路径 L200 也显式传了 `create_if_missing=True`。

### A22 — `app/api/training.py` — 检查点反序列化缺防护（high，附边界）

L595 `pickle.load(fh)`、L579 `np.load(path, allow_pickle=True)`、L556 `torch.load(path, map_location="cpu")` **未指定 `weights_only=True`**，而 `requirements.txt:59` 只钉了 `torch>=2.0.0`（<2.6 时默认仍为 `weights_only=False`，不能当作已被新版默认值保护）。路径来自 `Path(job.output_path).expanduser()`(L512)。
边界（得诚实说）：`output_path` 当前由服务端写入，不直接受控于请求；危害成立的条件是**训练输出目录对多用户/多任务共享且可写**，届时一次“查看检查点”即可在 API 进程内执行任意代码（pickle 反序列化等价 RCE）。定级为纵深防御缺口而非已验证的 RCE。

### A23 — `app/api/jupyter_proxy.py` × `app/services/jupyter_gateway.py` — kernel 归属只认 `POST api/kernels`，而 JupyterLab 不从这里开 kernel（high，可用性方向）

三个盘上事实（已逐条回读）：
1. `claim_kernel` 全仓**只有一个调用点**：`jupyter_proxy.py:367-373`，仅当 `path == "api/kernels" && method == "POST"`；`POST api/sessions` 的响应里带着 `kernel.id`，但代理不登记（L319-327 只校 `spec.path` 前缀）。
2. `owns_kernel`（`jupyter_gateway.py:318-336`）是**严格查表、fail-closed**：Redis `get` 返回 None 就 `return False`，没有任何「按会话前缀反推」的兼容路径。
3. `_guard_kernel` 同时挂在 REST 代理（L297）与 **WebSocket 代理**（L237）上，且 WS 处理器额外要求 `path.startswith("api/kernels/")`（L245），而 kernel 频道正是 `api/kernels/<id>/websocket`。
上游事实：Jupyter 官方文档对 Session 的定义就是「The session should be used to start kernels and then shut them down — for all other operations, the kernel object should be used」（http://jupyter.org/services/classes/defaultsession.html ），session 服务的 REST 入口是 `api/sessions`（packages/services/src/session/restapi.ts: `SESSION_SERVICE_URL = 'api/sessions'`）。⇒ 经由 JupyterLab 正常路径起的 kernel **从未被登记**，于是：`GET api/kernels` 把它过滤掉（L354-365 fail-closed）、任何 `api/kernels/<id>/*` 包括 **WS 频道被 4403 关掉**（而 JupyterLab 的代码执行只走这条 WS）。
方向必须说清：因为守卫是 fail-closed，这**不是**鉴权绕过（ocr 把它描述成“不被记账就能绕过护栏”是反的），而是**自己把主用路径锁在门外**；`DELETE api/kernels/<id>` 的 `release_kernel` 同样进不去，只能等 86400s TTL。
测试盲区：`tests/test_jupyter_gateway_isolation.py:79-103` 只直接调 `_guard_kernel`/`claim_kernel`/`owns_kernel`（L93 正是「未登记→False」），**没有任何用例走过 `POST api/sessions` 再回头用那个 kernel**。
尚未做的一步：没在真 JupyterLab 里跑一遍（需要 cds-jupyter 容器 + 票据）。上面的结论 = 代码事实 + 上游契约，不是端到端实测。

同文件第二条：`proxy_websocket` L277-288 `await asyncio.gather(pump_down(), pump_up())` 无 `return_exceptions`/无取消/无 task group。`pump_down` 卡在 `asyncio.to_thread(upstream.recv)`（`websockets.sync` 阻塞调用、无超时）：客户端断开后 `pump_up` 正常返回，`gather` 仍永不返回 ⇒ `finally` 里的 `upstream.close()`/`websocket.close()` 执行不到，**每个被丢弃的笔记本页签泄漏一条上游 WS + 一个线程**，而 `asyncio.to_thread` 与全进程共用同一个默认线程池（uvicorn 单 loop）⇒ 累积到上限会拖垮整个 API 进程的线程池。外层 `except Exception: pass` 还会吞掉方向上的真错误。

### A26 — `app/api/rag.py` — 事件循环阻塞与无上限缓冲（high，四条均命中代码）

- L83 `build_corpus(...)` 同步 CPU 密集调用直接在 `async def` 里跑（全文无 `to_thread`/`run_in_executor`）；L103/L111 `storage_service.upload(...)` 同步（KMS+SM4+SM3+minio HTTP）同样直接 await 不了 ⇒ 单请求可卡住整个 worker。
- L160 `artifact = await file.read()` 先全量读进内存，`_MAX_BUNDLE_BYTES`(256MB) 的判定在 `rag_generative_service.register` 内部（读完才判），后面 `_validate_bundle` 还会 `zf.read("model.onnx")` 再解一份 ⇒ 内存耗尽面。
- 对象名按 `data_product_id` 确定性生成且 corpus / embedding bundle 分两次写（L103/L111）⇒ 并发 ingest 可静默交叉覆盖为「A 的索引 + B 的模型」。
未实测（需真实语料与 MinIO），但属可直接阅读确认的结构问题，不是推断。

### A28 — `app/api/sandbox_nodes.py:222/271` — 向 unique 列 `dedup_key` 无条件 INSERT，把整个后台清理循环卡死（high）

事实链（逐条盘上）：
1. `app/models/alert.py:36` `dedup_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)`。
2. `sandbox_nodes.py:215` 只在 `health_state == "stale"` 时短路（只能防连续 stale），而 `app/services/cube_node_client.py:440/446` 会把 `health_state` 在 `healthy`/`stale` 之间来回写 ⇒ 节点恢复后再一遇心跳超时，同一个 `node-stale-<node_id>` 二次 INSERT → L237 `await db.flush()` 抛 IntegrityError。离线分支 L271 `node-offline-<node_id>` 连短路都没有。
3. 爆炸半径：`session_lifecycle.py:290-312` 的 `session_cleanup_loop` 一个 `async with async_session() as db:` 里依次跑 `cleanup_expired_sessions` → `_terminate_expired_contracts` → `detect_stale_nodes/recover_stale_nodes`（外面包着 `except Exception` 只记日志）→ `purge_cycle` → **`await db.commit()`**。事务一旦进入失败态，后续同一个 session 上的所有操作以及最后的 commit 全部失败 ⇒ **每 5 分钟一轮的会话回收/合约到期/保留期清扫整体失效**，而日志里只会看到一行「Node stale detection failed」。且因为回滚使 `health_state="stale"` 从未落库，L215 的短路永远不会生效 ⇒ **每一轮都会重新踩**，自维持。

### A29 — `app/api/open_api.py:54-61 / 68-74` — 审计写入与业务提交互相绑死（high，且违反本仓已写下的契约）

`audit_service.log()` 尾部是 `db.add(entry); await db.flush()`（`audit_service.py:134-135`），ClickHouse 写入是 guarded non-fatal（L259-260），但**主表 flush 会往上抛**，而两处调用都没有 try/except：
- 成功路径（L53 → L68-74）：`dv.execute_endpoint` 已真实执行并在内部 flush 了 `calls_used`/`last_called_at`（`delivery_service.py:392`），靠本行 `db.commit()` 落库；审计一抛 → 冒泡 → `get_db` 回滚 ⇒ **已执行的调用配额/计量丢失**，客户端拿到 500 后重试会再真实执行一次。
- 失败路径（L54-61）：`except EndpointUnavailable` 里先 `log(db, ...)` + `commit()` 再 `raise` 429/502（带 `retry_after=60`）。审计或 commit 一失败，正确的 **429 + Retry-After 就被替成无 code 的 500**，客户端按 5xx 退避继续重试。
关键：本仓已知道并写下了这个契约——`audit_service.log_standalone` 的 docstring（L163-168）原话：「拒绝/失败类审计必须写在本请求事务之外：`get_db` 在端点抛异常时回滚整个请求会话，所以先 `log(db, ...)` 再 raise 的写法在生产里会把这条审计直接吞掉」，并且该助手「不能往上抛：那会把一个正确的 401/403 拒访变成 500」。open_api 的失败路径用的正是这个被契约排除的写法。
覆盖面（只给可核对的数字）：`app/` 里 `audit_service.log(` 共 **166** 处，而用了 `log_standalone` 的只有 4 个文件（auth.py / connectors.py / deliveries.py / sandbox_sessions.py）——其余 raise 路径需要一次专项 sweep，本轮只逐行读了 open_api.py 这 2 处。

### A31 — `app/api/sandbox_sessions.py:1770-1797` — `contract_active` 把「没有合约」当成「合约失效」，无合约会话永久 410（high）

`contract_active = False` 初始化，只在 `if session.contract_id:` + UUID 合法 + 合约存在 + ACTIVE/SIGNED 时才置 True ⇒ `contract_id IS NULL` 的会话一旦 `SUSPENDED`，`_maybe_auto_resume` 就写 `session.auto_resume_rejected / reason=contract_not_active` 并 `raise SessionTerminated`（410，W2 契约下游 cds_sdk 故意不重试）。
而 `contract_id` 确实可以为空：`models/sandbox_session.py:61` `Mapped[str | None]`、`schemas:52 contract_id: str | None = None`、`_validate_session_contract` 对空 contract_id 直接 `return None`；`DEV_SANDBOX_REQUIRE_CONTRACT=True` 只门控 `dev_sandbox.py`，不覆盖 `POST /sandbox-sessions`。
陷阱是闭合的（已逐行确认）：`_load_operable_session`(L1631-1641) 本身就会调 `_maybe_auto_resume`(L1640)，而 `POST /{id}/resume`(L2295→L2303) 走的正是 `_load_operable_session` ⇒ 报错文案建议的「手工 resume」救不回来；三个隐式调用点（L1212、L1640、`session_stream.py:148`）全堵。边界：`auto_resume` 默认 False（model L75 / schema L60），需客户端显式开启，故不是默认全量中招。
修正一条我的错推断：我一度以为「手工 /resume 不查合约 ⇒ 已终止合约也能唤回」，读了 `_load_operable_session` 才知道它同样经过该判据 ⇒ **该旁路不存在**，不得写进结论。

### A32 — `sandbox_sessions.py:1683-1697` × `session_files.py:41-50` — 文件名拼进 guest 内执行的 Python 源码，绕过 code_scanner 闸门（high）

`validate_filename` 只拦：禁名清单 / >255 / `/` `\` `\x00` / 控制字符（L44-49）——**单引号、`;`、`(`、`)`、`#` 全部合法**。而 `_l2c_file_op` 的 upload 分支直接把它插值进要在 VM 里跑的源码：
```python
f"open({write_dir!r}+'/{name}','wb').write(base64.b64decode('{b64}'))"
```
⇒ 文件名 `a';import os,socket;#` 可闭合字符串并在 guest 内执行任意 Python；`download`/`list` 分支同样拼 `{name}`。对照：`/execute` 是经 `code_scanner.scan(...)` + 拒绝时写 `sandbox.code_scan_rejected` 审计（L1223-1238），而这条路径的源码由 API 自己拼装、**不过扫描也不进那条审计口径**。
影响边界（得说清）：注入代码跑在该会话自己的 MicroVM 里，不是宿主逃逸；真实危害是「平台对用户代码的静态扫描/拒绝审计被绕过」，而不是 RCE 到宿主。修：`{name!r}` 或干脆把文件名一起走 base64 传递，不要拼字面量。

### A33 — `sandbox_sessions.py:560/597/635` — 回滚路径上直接调同步 `runtime.terminate`（high，同一文件自己已写下理由）

销毁端点 L1032-1036 的形状是：
```python
# Destroy sandbox container — SS-14 RT-07: to_thread, 销毁时插件 Detach/Destroy 会回调本 API KMS（DEK revoke）, 占事件循环 = 循环依赖死锁。
await asyncio.to_thread(runtime.terminate, session.container_id)
```
而会话创建的三个回滚分支里同一个调用是**直接在事件循环上跑的同步调用**（L560 密钥分发拒绝 / L597 密钥分发失败 / L635 数据注入失败），且每一处都是先 `kms_service.destroy_key(...)` 再 `runtime.terminate(...)`（L557-561 / L594-598 / L632-636）——而插件的 DEK revoke 回调正是在 terminate 里发生，密钥先被销毁只会让这个回调更容易失败、阻塞更久。按本文件自己的判定，这三处就是在高并发下把 worker 卡死的形状。三处外层虽有 `try/except Exception: logger.warning(...)`，但 **阻塞不是异常**，try 拦不住。

### A38 — `app/core/redis.py:36` — 含密码的 REDIS_URL 直写 INFO 日志（high）

```python
logger.info(f"Redis connected: {settings.REDIS_URL}")
```
`REDIS_URL` 来自 `CDS_REDIS_URL`，生产形态就是 `redis://:password@host:6379/0` ⇒ **每次建立连接就把凭据写进日志聚合面**（本仓 `CDS_LOG_JSON=true`，日志直接进采集链路）。同行的 `logger.error(f"Redis connection failed: {e}")` 也可能带出 URL。修法：只打 host:port/db，且改惰性格式化（`%s`）。

### A40 — `app/core/config.py:511-519` — 节点 gRPC 明文回退并未 fail-closed（high）

注释（L320）自己写「No silent plaintext fallback — CUBE_NODE_GRPC_TLS=False is dev-only」，而启动门是：
```python
if self.CUBE_NODE_GRPC_TLS and not self.CUBE_NODE_TLS_CA:
    raise ValueError("... refusing to start with plaintext node gRPC (no silent plaintext fallback)")
```
⇒ **唯一能真正关掉 TLS 的开关自己不受任何校验**：生产只要 `CDS_CUBE_NODE_GRPC_TLS=false`，CA/mTLS 检查整体短路，启动成功、无 warning、L2' 节点 gRPC 静默走明文（控制面→cubelet 的 provision/exec/DEK 引用全部裸奔）。报错文案里的「refusing plaintext」只在 TLS=True 但缺 CA 时生效。修法：生产分支里 `if not self.CUBE_NODE_GRPC_TLS: raise`（与同文件 `FEDERATION_ENABLED` 那种「按开关本身 fail-closed」的写法对齐）。

### A41 — `app/models/api_key.py:71-82` — `is_expired()` 把本地钟表直接标成 UTC（high）

```python
now = now or datetime.now()            # naive 本地时间
if now.tzinfo is None:
    now = now.replace(tzinfo=timezone.utc)   # 直接“贴”一个 UTC 标签
```
`expires_at` 列是 `DateTime(timezone=True)`（PG timestamptz，取回是 aware UTC）。进程不在 UTC 时就会差一整段时区偏移：TZ=Asia/Shanghai ⇒ `now` 被“往后推 8h” ⇒ **密钥提前 8 小时过期**（可用性）；TZ=UTC-5 则反向 ⇒ **已到期密钥多活 5 小时**（安全）。本仓部署跑在容器里，但 k8s/compose 都没钉 `TZ`，开发机与 CI 是本地时区 ⇒ 真实可踩。同族（已 grep，影响不同级）：`app/api/compliance.py:38 end = datetime.now()` → 见 A44。对比无害的：`sandbox_runtime`/`vision_pipeline` 里的 `datetime.now()` 都是同一函数内算 duration，自洽。

### A42 — `app/core/database.py:53-115` — SyncDbBridge：健康检查永远“成功”、失败后永久停在未验证态、close 泄池（high，ocr 三条均成立）

1. `_ping()` 把 `ready.set_result(None)` 放在 **`finally`**（L85-89）⇒ `SELECT 1` 因库不可达/URL 配错/驱动报错失败时，异常落在 `asyncio.ensure_future` 建的 task 里无人取回（只留一句 "Task exception was never retrieved"），而 `ready.result(timeout=30)` 正常返回 ⇒ `_ensure()` 认为桥可用，故障被推迟到第一次真 DB 调用。应用 `set_exception` 。
2. `ready.result(timeout=30)` 自 `self._session_factory`（L66）与 `_warm` 里的 `self._loop = loop`（L79）都已经写完 ⇒ 一旦超时抛异常，后续任何 `_ensure()` 都在 `if self._loop is not None: return`（L61-62）直接返回 ⇒ **桥永久停在未验证态**，而那个线程可能还在 `run_forever()` 里。无复位/回滚路径。
3. `close()`（L110-114）只 `call_soon_threadsafe(loop.stop)` + `_loop = None`：**不 `dispose()` bridge_engine**（池内最多 3 条连接不关）、不 `join()`、不清 `_session_factory`；之后 `sync_bridge_session()` 再 `_ensure()` 会新建一套 loop+engine，旧 engine 的池就泄了。修正 ocr 一处措辞：`close()` 路径下旧线程会随 `run_forever()` 返回而退出（线程只是属性陈旧），「线程仍在跑」只适用于第 2 条的超时路径。

### A43 — `app/core/logging.py:42-59` — 生产 JSON 模式下所有 stdlib 日志被剥成裸消息，structlog 那套是死代码（high，比 ocr 说的更重）

`structlog.configure(...)` **未传 `logger_factory=structlog.stdlib.LoggerFactory()`**，也没有 `ProcessorFormatter` / `capture_logs()`；同时：
```python
handler.setFormatter(logging.Formatter("%(message)s"))  # structlog renders the JSON line itself
root.handlers = [handler]
root.setLevel(logging.INFO)
```
全仓 grep `structlog` 只命中本文件（13 处全在 logging.py 里）⇒ **没有任何模块真的用 `structlog.get_logger()`**（各模块清一色 `logging.getLogger(__name__)`）。后果：JSON 模式（生产 compose 里 `CDS_LOG_JSON=true`）下每条 stdlib 日志只剩 `%(message)s`：**无时间戳、无级别、无 logger 名、无 request_id，而且根本不是 JSON**（注释里那句「structlog renders the JSON line itself」只对 structlog 自身事件成立，而这类事件一个都不存在）；`RequestIdFilter` 注的 request_id 也进不去。另：`root.handlers = [...]` 是**整体替换**，会丢掉先前由 uvicorn/gunicorn/dictConfig 装上的 handler（文件/采集 agent），`setLevel(INFO)` 同样强制覆盖原配置。

### A50 — `app/api/sandbox_db.py:223` — `json_path` 由请求体控制，服务端直接读（high，已闭环：中权限任意文件读）

`ImportJsonRequest.json_path: str`（L223）无任何自录限定，`/import/json`（L375-391，ADMIN/OPERATOR/DATA_PROVIDER）把它直传给 `engine.register_json(body.table_name, body.json_path, ...)` ⇒ 相当于任意文件读（读回配置/密钥文件后可用 `/query` 导出）。`/import/parquet` 的 `parquet_path`（L232）同形。本条我只验到请求模型与调用点，**未读完 `register_json` 内部是否还有路径校验** ⇒ 标注为高置信但未闭环。

**【已闭环，升为 high】** `secure_duckdb.py:509-525`：
```python
    def register_json(self, table_name, json_path, ...):
        safe_name = self._sanitize_identifier(table_name)   # 只洗表名
        with open(json_path) as f:                           # ← 无任何路径校验
            content = f.read().strip()
```
请求模型、端点、引擎三层都没有目录限定 ⇒ 持有 DATA_PROVIDER 角色（不是只有 ADMIN）就能 `POST /api/v1/sandbox-db/import/json {"json_path": "/app/.env"}` 把宿主/容器文件当表导入，再用 `/query`（只需 `get_current_user` + 会话归属，A57）把内容读出来 = **中权限任意文件读**（同 uid 可读的 `.env`、`/proc/self/environ`、挂载的配置/凭据文件都在射程内）。全文件还会被 `f.read()` 全量缓冲（无上限）。`/import/parquet`（`register_parquet`，L~549）同形：`parquet_path` 直接进 `read_parquet(?)`，无校验，且 DuckDB 侧还支持 glob，可用于目录枚举。修法：限定到会话导入根 `Path(p).resolve().is_relative_to(root)`，或改为先上传为会话文件再按 id 引用。

### A51 — `app/api/sandbox_db.py:527-542` — `GET /sessions/{id}/encryption-config` 仅 `get_current_user` 就返回明文 DEK hex（high）

```python
async def get_encryption_config(session_id, current_user: User = Depends(get_current_user), ...):
    engine = await _get_authorized_engine(db, session_id, current_user)
    return {"session_id": session_id, "encryption_configs": engine.get_all_encryption_configs()}
```
同文件其余写/查端点全部 `require_roles(ADMIN, OPERATOR, DATA_PROVIDER)`，**只有这一个降级到 `get_current_user`**，而 `get_all_encryption_configs()` 的 dict 含 `dek_hex`（L312-316 `_parse_dek` 的反向）。归属判定 `_authorize_session_engine`（L33-58）确实要求 `session.user_id == current_user.id` 或已登记 owner ⇒ **不是跳租户泄漏**，而是：会话拥有者（角色不限，含 DATA_CONSUMER）可直接 HTTP 拿到列级 SM4 明文密钥，拿到后可离线解密所有落盘/导出密文，列级加密对其彻底失效；且响应路径无任何速率/审计。docstring 说这个 DEK 是给 bwrap `--setenv` 注入用的 —— 同文件 L324-339 已有进程内函数 `get_encryption_config_for_session()` 供沙箱层直接调用，这个 HTTP 口子不是必需的。

### A53 — `app/api/dev_sandbox.py:156-172` — 资源不可用时静默跳过，仍返回 201（high，踩 honest-degradation 铁律）

```python
if resource and resource.status == ResourceStatus.READY.value and resource.storage_path:
    try: …; data_path = str(data_file)
    except Exception as e: raise HTTPException(500, …)
# ← 没有 else：resource 不存在 / 不是 READY / 无 storage_path 时直落，data_path 仍为 None
```
⇒ 接口照样 201，只是 `data_path=None`，调用方无法区分「数据已挂载」与「数据根本没加载」（`data_product.resource_id` 缺失时同形）。在密态数据沙箱里这是危险的静默降级：用户以为自己在真数据上开发，实际是空沙箱。本仓铁律是模拟/降级路径必须显式披露（`is_simulation` / `ALLOW_SIMULATION`），这里连一个警告字段都没有。建议 else 分支直接 409（或在响应里带 `data_loaded: false` + 原因）。

### A54 — `app/api/dev_sandbox.py:165` — 解密后的明文副本永不回收（high）

明文落在 `/tmp/cds-sandbox/data-load/<user_id>/<uuid>/<resource_id>.<ext>`。全仓 grep `data-load` 只命中这一处创建点与 `jupyter_gateway.py:45` 的一句注释，**本文件内 `unlink`/`rmtree` 零命中**，`terminate_dev_session`（L304-320）也没有任何删除逻辑；`create_session` 中途抛异常、provision 返回 failed、后续审计失败，都不会清理。⇒ 供数方的解密明文长期残留在宿主 /tmp，跨会话、跨进程重启。现有缓解仅目录权限（`sandbox_dir.chmod(0o700)` / `data_file.chmod(0o600)`，L166-169）。这直接冲产品前提（密态数据不出域）。

已排除的一个更坏形状：明文目录与 Jupyter 开发环的 `root_dir` 同根（`/tmp/cds-sandbox`），但 `jupyter_gateway.py:40-45` 的设计就是「root_dir 以内只有沙箱工作区，不含 cds-uploads、data-load 里的明文供数」，对用户暴露的前缀由 L114 `Path(workspace).resolve().relative_to(Path(host_root).resolve())` 从会话工作区推导，加上此前已采的「deny 裸 api/contents」（上游忽略 `?path=` 会回根列表）⇒ **不能通过 Lab 读到 `data-load/`**。因此本条的危害边界是「同 UID 进程 / 宿主可读、跨重启残留」，不是跳租户读。

### A57 — `sandbox_db.py:129-133` vs `151-156` — 脱敏视图不是会话拥有者的强制边界（high）

权限面逐字对比：`POST /masked-view` 要 `require_roles(ADMIN, OPERATOR)`；而 `POST /query`（L129）与 `POST /query-decrypted`（L485-495）只要 `Depends(get_current_user)` + 会话归属。`execute_query` 跑的是任意 SQL，基表仍在同一个引擎里 ⇒ 同一会话的用户可直接 `SELECT * FROM <原始表>` 拿到未脱敏明文，BLOB 密文还能用 `/query-decrypted` 自动解密。叠加 A48（无策略时 `_check_policy` 直接 `return True`），脱敏对这会话的普通用户不构成任何强制。要么基表对非 ADMIN 不可见（只暴脱敏视图），要么 `/query` 上同等角色门 + 基表访问审计。

### A59 — `catalog_sync.py:247-257` — 未转义的 LIKE 直接驱动 DELETE ⇒ 跨命名空间目录清空（high，破坏性）

```python
        await db.execute(
            delete(DataProduct).where(
                DataProduct.name.like(f"[remote:{space_id}]%")
            )
        )
```
完定的利用链（逐环各自验过）：
1. `federation.py:25` `space_id: str = Field(..., min_length=1, max_length=64)` —— **无 pattern**，`space_id="%"` 合法入库并作为 `_space_index` 的键；
2. `endpoint`（`federation.py:27` `min_length=1`）指向攻击者主机；
3. `POST /catalog/sync/%25?mode=full` → `catalog_sync.py:175` `get_trust_by_space("%")` 命中；
4. `_fetch_remote_catalog`（`:216-222`）只要求 `status_code == 200`，`{"products": []}` 即可通过（这步失败会 raise 而在删除前中止，所以需要一个能返 200 的端）；
5. `_full_sync` 的 `like("[remote:%]%")` 匹配**所有空间的镜像条目** ⇒ 全删后只插攻击者给的内容（`:268` 返回 `added, 0, existing_count`）。
这不是「跨空间读」，是跨空间写。修法：一个共用的 LIKE 转义助手，且把 `remote_space_id` 存成列用 `==` 过滤，永远不拿名字前缀当谓词。

### A60 — `federation_connector.py:690-710` × `federation.py:27` — 未校验的 `endpoint` 构成带身份令牌的 SSRF（high）

`endpoint: str = Field(..., min_length=1)`（L27）无任何 scheme/host/IP 校验（schema→establish_trust→send_request→_execute_remote_request 全链无 allowlist），而连接器会对其发真请求：
```python
base_url = trust.remote_space.endpoint.rstrip("/")          # :690
with httpx.Client(timeout=timeout, verify=client_verify, cert=client_cert) as client:   # :710
```
请求带本地签发的联邦 JWT（`:579` 用 `settings.JWT_SECRET_KEY` 签），响应 `status_code` 与 `detail` 会回显给调用方 ⇒ 半盲 SSRF（端口/前缀探测）；`verify=` 只保护 https 目标，`http://169.254.169.254/…`、`http://127.0.0.1:6379`、`http://localhost:8200`（Vault）全是明文且完全绕过该保护。`allowed_operations: list[str] | None`（L31）元素无字符合法性校验，会被拼进 URL 路径（前提：先得是 allowed_operations 的成员，而这个名字也是创建者自己写的）。限定：需 ADMIN/OPERATOR。

### A61 — `federation_connector.py:710` + 两个调用点 — 同步 `httpx.Client` 直跑在 `async def` 里（high）

`send_request` 是 `def`（同步），内部 `httpx.Client`（connect=5s / **read=30s**）+ 同步 SM2 签名 + mTLS 证书文件读，全部在事件循环上跑；两个调用点都没包 `to_thread`：`federation.py:297` 与 `catalog_sync.py:216`（后者 ocr 没提）。⇒ 一个不可达的远端空间就能把整个 worker（含 /health、会话 exec、KMS 回调）背停 35s 量级，N 个并发串行化整盘。

### A70 — 上传先全量进内存再校大小（high，TRUE）；文件名的“路径穿越”那一半不成立

- `data_resources.py:150-152`：`content = await file.read()` 在 `if len(content) > MAX_UPLOAD_SIZE` **之前** ⇒ 500MB 上限形同虚设（5GB 请求体先 OOM，413 永远不返回），后面还要拿 `content` 去猜格式/推 schema/加密。可达角色：`require_roles(DATA_PROVIDER, ADMIN)`（:144）。修法：按块 `read(1<<20)` 累加计数，超限立即中断。
- `:189` `object_name = f"data-resources/{resource.id}/{file.filename or 'data'}"` 确实直接用客户端文件名，**但 ocr 推的“写穿目录”不成立**：`storage_service.py:132/158/286` 本地分支走 `safe_join_object_name(self._local_dir, object_name)`，越界时 `:272/:284` 直接 `raise "Local storage reference escapes storage root"`。MinIO 分支（`:125` `put_object`）里 `../` 只是键名污染，不是路径语义。⇒ 降为 low（键名卫生 + 空字节/超长名）；仓库已有 `app/core/path_security.py:13 sanitize_filename` 未在此调用，属一致性缺失。

### A74 — SDK 把 recv 超时当“正常结束”（high，违反 honest-degradation）

`sdk/cds_sdk/stream.py:142-143` `if _is_closed_error(exc): return`，而 `:164-168` 把 `WebSocketTimeoutException` / `isinstance(exc, TimeoutError)` 也算“已关闭”⇒ 沙箱内长时间静默（编译/拉数据）超过 recv_timeout 时，调用方拿到的是**被截断的输出却无任何标记**，看起来像命令跑完了。本仓库铁律是降级必须披露（`ALLOW_SIMULATION`/`is_simulation`），这里是反例。修法：超时单独抛（或至少 yield 一条 `truncated=true` 帧）。

### A75 — 持久队列的归属守卫缺失（high 语义，但**非活路径**）

- `models/task_queue.py:115-116` `complete()` = `item.status = "done"`（不校当前状态、也不校 `claimed_by`）；`:120-130` `fail()` 同样无条件写回 pending/failed ⇒ 过期 worker 可把已被 B 认领的条目改完/重投。
- `requeue_stale:134-155` 逐字确认：回队时**不 `attempts += 1`也不设 `run_after`** ⇒ 毒消息（worker 反复崩）在 pending↔claimed 间零退避循环，`max_attempts` 形同虚设。对比：`claim_next:102-103` 的认领倒是做了条件 UPDATE（`WHERE id=:id AND status='pending'` 的 CAS），只有回队这条路没有守卫。
- **边界（关键）**：全仓 `models.task_queue` 的导入者只有 `app/main.py:36`（为了建表）与 `tests/test_durable_task_queue.py:4/69` —— **没有任何生产服务调它**（活路径是另一套 `app/services/task_queue.py` 的 Redis/内存队列，由 `task_pipeline.py:23/65` 使用）⇒ 定性为潜伏：一旦接线就是“重复执行 + 无限崩溃循环”。

### A78 — SDK 打包元数据错位（high，已用 tomllib 实测）

`readme = "README.md"` 写在 `[project.optional-dependencies]` 表里（`stream = [...]` 之后）。本机实测：
```
extras: {'stream': ['websocket-client>=1.6'], 'readme': 'README.md'}
project.readme present: False
```
⇒ README 变成了一堆 extra 里名为 `readme`、值为**字符串**（不是数组）的项，`[project]` 下没有 readme。同时 `sdk/cds_sdk/__init__.py:22` 仍是 `__version__ = "0.2.0"` 而 pyproject 是 `0.3.0`（pip 侧 0.3.0 / 代码侧 0.2.0）。修法：`readme` 提到 `[project]`，版本改单一来源。（不同 setuptools 版本对非数组 extra 的行为不一致，我没跑构建，故不断言“构建失败”。）

### A82 — 僵死的 running 操作会永久锁住回滚（high，两条事实相乘）

- `models/session_operation.py:22` `session_id` **无 ForeignKey**（同目录 `sandbox_task.py` 有）；`:19` 只有一个普通索引；**无 `started_at`/`finished_at`/租约列**（grep 零命中），而异步分支在 commit 前就置 `status="running"` ⇒ 进程重启/协程被取消后该行永久停在 running。
- `session_operations.py:39-50 _has_running_exclusive`：对 `_EXCLUSIVE_OP_TYPES={"rollback"}`（:22）查 `status.in_(["pending","running"])`，命中即 `raise OperationLocked`。
- 相乘的后果（ocr 只说了“永久 running”，没说这层）：**一次重启后，该 session 的回滚永久被 `OperationLocked` 拒绝**，除非人工改库。另一头，同一函数是 SELECT-then-INSERT 无锁/无约束 ⇒ 并发两个 rollback 又都能通过（双击/重试/多副本），“排他”两侧都不成立。修法：租约列 + 启动时回收，加 `session_id` 外键，并用 `pg_advisory_xact_lock` 或部分唯一索引代替 SELECT 门。

### A83 — 整个旧管理页面无 HTML 转义层（开发形态 high / 生产不可达）

机械统计（`app/templates/*.html` + `app/static/js/admin.js`）：
- `.innerHTML` 写入共 **24 行**；
- `onclick="...${...}"` 内联事件属性内插入服务端数据共 **4 处**，分布在 3 个文件，且**全部包的是不可逆/高权操作**：`certificates.html:73 verifyCert('${c.cert_id||c.id}')`、`:74 revokeCert(...)`、`connectors.html:61 suspendConnector('${c.id}')`、`identities.html:65 destroyKey('${k.key_id}')`；
- `escapeHtml`/`escape_html` 在这 7 个文件里 **全部 0 命中**（无任何转义层），`textContent` 只有零星使用（alerts/blockchain/certificates/identities 各 2-3 处），`admin.js:33 badge(text,type)` 不转义；
- `admin.js:8/15/20` 无条件 `res.json()` ⇒ 对 204/空体必抛 `SyntaxError`（业务层把它当“加载失败”）；`admin.js:61` 把 `${e.message}`（= `${res.status}: ${await res.text()}`）直写 `#app`。
可利用链：服务端数据（cert_id / key_id / product_id / 告警文本）任一含 `'` 或 `<img src=x onerror=...>` ⇒ 在已登录管理页上执行脚本 ⇒ `localStorage.cds_token`（`admin.js:24`）被窃 ⇒ 拿 Bearer 直接打业务 API。
边界（定调）：`app/api/admin.py:21-42` 五个页面端点无 Depends，但 `config.py:558-560` 在 production 直接 raise（`ADMIN_PAGES_ENABLED` 默认 False）⇒ **整簇仅在开发/本机形态可达**，归为“旧页面待下线，修它不如删它”一类；若要修，一个 `escapeHtml()` + 改用 `textContent`/`dataset` + 统一 `parseJsonResponse` 三件就足够。

### A84 — 卷级 `read_only` 可被客户端在挂载时推翻（high）

```
api/shared_volumes.py:28    class AttachRequest: read_only: bool | None = None
api/shared_volumes.py:106   ... attach(db, volume_id, owner_id=current_user.id, read_only=body.read_only)
services/shared_volumes.py:52   effective_ro = volume.read_only if read_only is None else read_only
services/shared_volumes.py:99   binds.append((volume_dir(volume.id), f"/workspace/shared/{volume.name}", attachment.read_only))
```
⇒ `POST /api/v1/shared-volumes/{id}/attach {"session_id": "...", "read_only": false}` 对**建卷时声明 `read_only=True`** 的卷直接得到可写 bind（`:120` 也给运行时 `"readOnly": false`）。服务层没有任何“只读卷不得读写挂载”的校验收敛点。角色门只有 `get_current_user`（`:88`）；卷归属是否校验我未追（`attach` 内部），但即使只能挂自己的卷，这条也推翻了模型里声明的只读不变式。修法：`effective_ro = volume.read_only or (read_only if read_only is not None else False)` —— 只允许“更严”方向。

### A89 — 公开硬编码 JWT 密钥在 DEBUG 形态下签发全部令牌（high）

```
app/services/auth_service.py:20  _LOCAL_DEFAULT_JWT_KEY = "cds-local-dev-test-jwt-secret-key-32-bytes-minimum"
app/services/auth_service.py:39-40  if settings.DEBUG or os.environ.get("TESTING") == "1" or os.environ.get("PYTEST_CURRENT_TEST"):
                                        return _LOCAL_DEFAULT_JWT_KEY
app/services/auth_service.py:53/63      jwt.encode(payload, _effective_jwt_secret_key(), ...)
```
可达性不是“生产误配”那么小众：`docker-compose.yml:116 CDS_DEBUG: "true"`、`deploy/local/local_deploy.sh:118 -e CDS_DEBUG=true`、`deploy/e2e-remote/remote_deploy.sh:122 CDS_DEBUG=${CDS_DEBUG:-true}` —— 默认 compose 与两套部署脚本都是 true ⇒ 这些栈上**任何人拿源码就能自签 `role=admin` 的 access token**。不定 critical 的理由：`docker-compose.prod.yml:113` 与 `helm/cds/values.yaml:36` 均写 `CDS_DEBUG: "false"`，仓里还有回归钉（`tests/test_deploy_manifests.py:46`、release-gate RG-P0-011）。另外：`monitoring.py:181` 会披露 “CDS_DEBUG is enabled”，但**没有任何一处警告此刻用的是源码里公开的密钥**（踩 honest-degradation）。ocr 说的“短路了 `validate_jwt_security`”那一半按设计成立（强度校验本就是 `is_prod` 门），真正能用的是公开密钥那一半。修法：回退分支只在 `TESTING`/`PYTEST_CURRENT_TEST` 下走，DEBUG 形态需要固定密钥时另加一个显式开关并在启动时 WARNING。

### A90 — `terminate()` 用 cubelet 的 sandbox_id 删会话卷 ⇒ 卷永久孤儿（high）

```
app/services/cube_runtime_adapter.py:277/532/734   volume_name = f"cds-data-{session_id}"      # 建
app/services/cube_runtime_adapter.py:996           self._master_delete_volume(f"cds-data-{sandbox_id}")   # 删
app/services/cube_runtime_adapter.py:783           sandbox_id = response.sandboxID or str(uuid.uuid4())
```
模块自述（`:9`/`:11`）：container id 是 `l2c-<node_id>-<sandbox_id>`，`sandbox_id` 是 cubelet 返回的 id（`:363` 注释：“尾部 32 位 hex，实证 cubebox ls”）⇒ 与建卷用的 `session_id` 不是同一个命名空间。全仓 `cds-data-` 只有 6 处引用，**没有任何回收作业**；另一处 `_master_delete_volume(volume_name)` 在 `:422`（建卷遇到 ret_code 130409 陈旧记录时的自我修正，名字是对的）。后果：每次会话终止都在 cube-master 上留一个永久孤儿加密卷（KMS DEK 记录同步累积），而审计与存储回收口径都看不到它。修法：`terminate(container_id, session_id=None)`，删卷只用 session_id；sandbox_id 仅用于 DestroyCubeSandbox。

### A93 — 合约锈定走的是“永远上不了链”的本地块，写库的 tx_hash 重启即不可验（high）

```
app/services/contract_service.py:15,178-185   attestation = blockchain_service.anchor_to_blockchain(contract_hash, metadata={...})
                                              if attestation.success: contract.blockchain_tx_hash = attestation.tx_hash
app/services/blockchain_service.py:25          self._client = None        # 全文件唯一一次赋值，无 setter/无配置读取/无工厂
app/services/blockchain_service.py             def _get_client(self): if self._client is not None: return self._client; return self._client   # 两分支同值
app/services/blockchain_service.py:76          def verify_attestation(self, tx_hash): ...   # 读 _local_anchors（进程内 dict）
```
⇒ 真链分支不可达，服务永远走本地回退；而 `contract.blockchain_tx_hash` 被当成链上凭据写库，**重启后 `_local_anchors` 清空 ⇒ 该 tx_hash 永远验不过**，也没有兄弟模块 `blockchain_adapter` 已有的 `_backend_disclosure`（T8 要求“不得把 PG 日志当成上链”）。两点限制它的危害：`blockchain_service.verify_attestation` 全仓**零调用方**（其余同名命中是 `kms_service`/`result_verifier` 自己的私有方法）⇒ 它是展示性凭据而非授权依据；真正的审计锈定腿走的是 `blockchain_adapter`（PG 落库 + `hydrate_from_db`，`audit.py:149/365`、`merkle_pipeline.py:189`、`cube_egress_audit.py:235`）。同文件另三条（`str(dict)` 入散列、无锁的 prev_hash→自增→回写、`_local_anchors` 无界只增）均成立，但都在这条腿上。修法：`contract_service` 改走 `blockchain_adapter`（已有后端声明与持久化），`blockchain_service` 要么删要么显式加 `set_client()` + 未配置时 WARNING。

### A95 — 查询期策略门用的是“只看 5 项”的那套 PDP，带字段 ACL/掩码的那套无人导入（high）

仓库里有两个同名 `PolicyEvaluator`：
```
app/services/policy_evaluator.py:58   async def evaluate(self, request, db) -> PolicyDecision（含 OPA、field_acl、field_masks）
app/services/policy_evaluator.py:274  policy_evaluator = PolicyEvaluator()
app/services/policy_compiler.py:462-492  def evaluate(self, policy, request) -> dict   # 只查：合约生效/沙箱级别/操作/时长/DP ε
app/services/secure_duckdb.py:136     from app.services.policy_compiler import policy_evaluator
app/services/secure_duckdb.py:143     result = policy_evaluator.evaluate(self._policy, request)   # 取 result["allowed"]
```
全仓 `from app.services.policy_evaluator import` **只命中 `tests/test_policy_evaluator.py:6`** ⇒ `policy_evaluator.py` 是死模块，它的 `_evaluate_field_acl:244-269`（含两处 fail-open：`if not field_acl: return requested_fields`、`rule is None → allow`）与 `field_masks` 在生产里不执行。活的评估器根本不看待字段：`secure_duckdb._extract_select_fields:147-158` 只取 SELECT 的第一个字段名，而活的 `evaluate` 连这个字段也不读。
⇒ **不得误读成“掩码没接上”**：字段级掩码的真实链路是 `policy_compiler:428-446`（按分级算 `mask_fields`/`deny_out_fields`）→ `output_gateway.py:152-177` → `output_security.py:104-128`（命中字段置 `***`、命中 deny 行整行不出），这条是活的。缺的是**查询期**的字段级控制。

**A73 定级修正（自己此前高估）**：第十二批我写 A73「已撤销的策略包继续做授权决策」，依据是 `policy_evaluator.py:61-66` 不过滤 `revoked_at` 与完整性门只摘要 `rego_source`。现在证实该模块**零生产导入方**，所以上述两条均无入口 ⇒ 降为潜伏；活路径上对应的是 `policy_compiler.py:472` 的合约状态位（`Contract not active`）与 `contract_service.py` 的合约状态机，写侧 `revoked_at` 与活读侧不是同一字段。本条不删除原文，但**以本节为准**。

### A97 — 危险内建判定不分属性名与内建名：合法代码被 FATAL，而 `io.open`/`getattr` 绕过（high）

```
app/services/code_scanner.py:253-259  def _get_call_name(...):  # Attribute 节点返回 node.func.attr
app/services/code_scanner.py:94   DANGEROUS_BUILTINS = {exec, eval, compile, __import__, open, globals, locals, breakpoint}
app/services/code_scanner.py:168-174  if func_name in DANGEROUS_BUILTINS: ScanSeverity.FATAL
```
**A97（high，可用性）**：因为属性名与内建名不分，白名单里的 `re`/`torch` 自己的 `re.compile()`、`torch.compile()`、`df.eval()` 都被当危险内建 FATAL。调用方已 grep：`connectors.py:600-605`、`sandbox_sessions.py:1223-1236`（FATAL ⇒ 400）、`task_worker.py:78-82`、`task_pipeline.py:578`。反向同样成立于同一个函数：`io.open(...)`、`getattr(builtins,'ev'+'al')`（`getattr` 不在禁列）、`__class__.__mro__` 只封了 3 个 dunder（`:189-192`）。

### A101 — LoRA 在优化器之后才注入，训练是静默 no-op（high，活路径）

```
app/services/cpu_trainer.py:174   optimizer = self._create_optimizer(model, cfg)
app/services/cpu_trainer.py:175   scheduler = self._create_scheduler(optimizer, cfg, len(encodings))
app/services/cpu_trainer.py:177-178  if cfg.use_lora: model = self._apply_lora(model, cfg)
app/services/cpu_trainer.py:486   _optim.AdamW(model.parameters(), ...)   # 快照的是旧参数表
app/services/cpu_trainer.py:526   model = get_peft_model(model, lora_config)   # 包一层 + 冻结基座
```
`get_peft_model` 返回新的包装体并新增 LoRA 参数，而优化器手里拿着的是**注入前的参数快照**且这些基座参数已被 PEFT 置 `requires_grad=False`；`_clip_and_noise:553-555` 与 AdamW 都只看 `p.grad is not None`，LoRA 的 A/B 从未进入优化器 ⇒ `optimizer.step()` 什么都不改，但 `train()` 仍返回 `epochs_completed`、`loss_history`、`memorization_score` 且 `except` 外的主路径不计失败。可达：`llm_sft_runtime.py:846 return cpu_trainer.train(...)`（真模型分支），`use_lora=(method in LORA/QLORA)`——LoRA 是 SFT 的主选方法。修法：先 `_apply_lora` 再建优化器（或用 `model.named_parameters()` 过 `requires_grad` 重建）。

### A103 — `agent_service.py:386` 用了 PEP 701 语法，py3.11 门上必为 SyntaxError（high，CI）

```
app/services/agent_service.py:386  f"_AGENT_HTTP_ALLOW = {dict(http_allow or {"domains": [], "ips": []})!r}\n"
.github/workflows/ci.yml:25  python-version: "3.11"
.github/workflows/ci.yml:37  run: python -m compileall -q app tests alembic
```
同一对双引号嵌套只在 3.12+（PEP 701）合法；生产镜像是 `python:3.12-slim`（运行时无影响），但 CI 的 `compileall` 跑在 3.11。本条语法事实由子代理报、**两支撑点（该行原文与 ci.yml 的 3.11+compileall）我已在盘上亲读**；未实测 3.11 解释器（本机与门均为 3.12.10）。修法：把内层字典换成单引号或提到外层常量。

### A104 — 证书 DER 构造族：产出的 PEM 在 TLCP 活路径上（high）

我亲读的支撑链：`app/services/tlcp_service.py:336 def _build_cert_pem` → `:348 return certificate_service._build_cert_pem(...)`，而 `tlcp_service.generate_certificate` 由 `POST /api/v1/certificates/generate` 驱动，PEM 写 `/tmp/cds-certs/<id>/cert.pem` 并是 `deploy/cube-node/install.sh:26` 的 `CDS_CUBE_TLS_CERT` 取值源。缺陷本体（子代理逐条给行号并用 `openssl asn1parse` 对过 DER 真值，**我只复验了可达链与函数存在，未逐个重跑 DER 对比**）：INTEGER 编码丢前导零字节、KeyUsage 位序错（终端实体拿到 keyCertSign）且未标 critical、`signatureValue` 写裸 `r||s` 而非 DER `ECDSA-Sig-Value`、扩展解析块因运算符优先级整段死代码。后果：对端严格解析（OpenSSL/BabaSSL 标准链）拒证书，而仓内自解（`_parse_x509_der`）与之同错 ⇒ 自测能过、互操作不能过。修法：改用 `cryptography.x509` 证书 builder（已在 requirements），手写 DER 只留 SM2 签名注入部分。

### A108 — 资源限额只钳上限：0/负数原样采纳，字符串抛 TypeError，不传时返回共享字典（high，已实测）

```
app/services/sandbox_manager.py:55-57   get_resource_limits: return RESOURCE_LIMITS.get(sandbox_level, ...L3)   # 不拷贝
app/services/sandbox_manager.py:60-75   validate_resource_limits: effective[k] = min(requested[k], level_limits[k])
app/schemas/sandbox_session.py:54,111   resource_limits: dict | None = None   # 自由字典，无 ge/类型约束
app/api/sandbox_sessions.py:344        effective_limits = validate_resource_limits(body.sandbox_level, body.resource_limits)
app/api/connectors.py:417-426           effective_limits = validate_resource_limits(sandbox_level, None) → SandboxSession(resource_limits=...)
app/services/contract_fulfillment.py:106  validate_resource_limits(sandbox_level, None)
```
本机门（py3.12.10，`probe_limits.py`）实测：`{"cpu_cores":0,"memory_mb":0,"disk_mb":0}` 全零被原样采纳；`{"memory_mb":-1}` 采纳；`{"cpu_cores":"8"}` 在 `min()` 里抛 `TypeError: '<' not supported between instances of 'int' and 'str'`（入参来自请求体且 schema 不校 ⇒ 用户可稳定把会话创建打成 500，而不是 422）。**别名已实测**：`validate_resource_limits("L3", None) is RESOURCE_LIMITS["L3"]` 为 True，向返回值写一次就把全进程默认限额从 `cpu_cores=1` 改成 9999；而 `connectors.py:426`、`contract_fulfillment.py:106` 正是把那个共享对象直接存进 `SandboxSession.resource_limits`（当前全仓无就地写入，grep `resource_limits[...] =` / `.update(`/`.pop(` 零命中，所以是地雷而不是现行事故）。修法：`get_resource_limits` 返回深拷贝 + 对三个键做“正整数”显式校验（非正或非 int 直接 422）。

### A109 — 租户配额是进程内 dict 的 check-then-act，多副本互不可见，写入是整快照覆盖（high）

```
app/services/sandbox_manager.py:208-215  update_tenant_usage: quota["used_x"] = quota.get("used_x",0) + x   # 非原子 RMW
app/services/sandbox_manager.py:217-234  check_tenant_quota: 只读比较，不加锁、不预占
app/services/sandbox_manager.py:236-243  release_tenant_usage: max(0, ...) 静默截断
app/services/sandbox_manager.py:168-182  _persist_tenant_quota: quota is None → redis.delete(...)；否则 hset(mapping=整个快照)
app/api/sandbox_sessions.py:381-388 / :541   check_tenant_quota(...) → 中间 await 建库/建运行时 → update_tenant_usage(...)
helm/cds/templates/deployment-api.yaml:9    replicas: {{ .Values.replicaCount }}
```
“读用量 → 比较 → 返回 OK”与“累加”分属两个函数，中间隔着建会话的 await，并发请求会一起过校验（超额）；副本数 >1 时每个副本各自 `_tenant_quotas` 计账，Redis 只在启动时 `restore_tenant_quotas` 恢复一次、写入是各自快照的 `HSET` 覆盖（后写者把别人的累加盖掉），所以有效配额≈副本数×限额。`release_tenant_usage` 的 `max(0, …)` 把重复释放/未登记释放静默归零，账目错误不可发现。另一颗地雷：`_persist_tenant_quota` 的 `quota is None` 分支执行的是**破坏性 `redis.delete`**，而全文件没有任何删除 `_tenant_quotas[uid]` 的代码（赋值只在 `:165`/`:266`），今天不可达；一旦有人加缓存淘汰就会误删租户在 Redis 里的累计用量。修法：配额收拢为 Redis 原子 try-reserve（`HINCRBY` + 超限回滚，或 Lua 脚本），按 session_id 幂等记账，删除分支换成直接 return。

### A112 — 驱动已执行但信封解析不到时，同一单元格被再执行一遍（high，非幂等副作用放大）

`app/services/notebook_service.py:420-452`：`prefer_kernel` 分支里，若 `result is None`、`raw["output_blocked"]` 为假、`envelope` 为空且 `raw["error"]` 为空，控制流一路落到函数末尾 `:452  return await run_stateless(executor, code)`。而驱动已经在沙箱里跑完单元格、只是在 emit 前被 timeout 杀死 / 输出被截断 / **base64 行被 DLP 的 `redacted_output` 改写**（见 A111 同一通路），就落入这一支 ⇒ 对 `INSERT`、写文件、调外部接口这类单元格产生重复副作用与重复审计，且第二次结果当正常结果落库（`notebooks.py:463 → apply_cell_result`）。修法：区分“驱动根本没跑”（executor 返回错误/无输出）与“驱动跑了但信封不可解析”，后者直接返回降级错误（`output_unparseable`）而不是重跑；或在 driver 里带 run-id 幂等标记。

### A113 — crypto_service 的“软件回退”是同一个调用的重复；generate 分支只记日志不回退（high）

```
app/services/crypto_service.py:240-255  失败后 log “falling back to software SM2”，紧接着第二次调的仍是 hsm_adapter.sign(data, key_id)
app/services/crypto_service.py:262-274  generate_hsm_signing_keypair 里 log “falling back to software SM2 keypair”，下一行直接 raise
:65                                     真正可用的软件签名 self.sign(data, private_key, public_key)  exists 但未被使用
```
同函数同参数 ⇒ 第一次抛 RuntimeError/ValueError/AttributeError 则第二次必然再抛，日志里的回退承诺永远不会兜住，最终只留一行 warning + 一个 `ValueError("... not available in HSM or software fallback")`（错误文案声称存在一个不存在的回退路）。与 A93/A94 同族：日志/文案对运行事实作了作不了的保证。修法：要么接上 `self.sign(...)`（附软件密钥来源与披露），要么删掉重复调用并把日志改成“无回退，直接失败”；按诚实铁律，后者是下限。

### A117 — 回滚是“先全删再解包”，失败不原子、无备份（high）

`app/services/session_snapshots.py:142-157`：`for p in sorted(workspace.rglob("*"), reverse=True)` 逐个 `rmdir`/`unlink`，**完了才** `tar.extractall`。中途任何失败（磁盘满、归档成员异常抛 `OSError`、进程被 kill、与正在跑的 exec 并发写）都把用户工作区留在半删除状态且无回退件；`rollback()` 外层也没包事务/快照保护（W11 只做了同会话互斥）。修法：先解到同级临时目录，再 `os.replace` 目录对调，失败时原工作区不动。

### A118 — 同一删除循环漏 symlink：非空目录链接残留、空目录链接直接抛异常（high，安全）

`session_snapshots.py:147-151` 只有 `p.is_dir()` 与 `p.is_file()` 两支。`Path.is_dir()` **跟随符号链接**，而沙箱内代码可在工作区建链接（工作区是宿主与 guest 共享的那棵树）：指向非空目录的链接 `is_dir()` 真且 `any(p.iterdir())` 真 ⇒ 两分支都不命中，**回滚后工作区仍留着指向工作区外的链接**（后续 files API 写入可被诱导写到外面）；指向空目录的链接走 `p.rmdir()`，Linux 上抛 `NotADirectoryError`（叠加 A117 停在半删除态）；悬空链接两假 ⇒ 也残留。修法：循环里先 `if p.is_symlink(): p.unlink(); continue`。

### A120 — 唯一约束上的 check-then-act：并发重 key 会把整批告警/审计带下（high）

```
app/models/alert.py:36                       dedup_key: ... unique=True, index=True
app/models/cube_egress_dedup.py              UniqueConstraint("node_id","file_name","offset","line_no", name="uq_cube_egress_dedup_key")
app/services/alert_center.py:69,121,143,161,166,178   6 处 await db.flush()；grep IntegrityError / begin_nested → 0 命中
app/services/cube_egress_audit.py:290        db.add(...)；grep IntegrityError → 0 命中
```
两处都是“先 SELECT 查重 → 再 add/flush”：并发（或同批次内部重复）时后提交者抛 `IntegrityError`，没有任何捕获 ⇒ 整个 ingest 失败，本批其它正常行一并丢失并向上冒 500。修法：改 `insert().on_conflict_do_nothing(index_elements=...)` / `on_conflict_do_update`，或捕获 `IntegrityError` 后回滚到 savepoint 再按 key 重查走合并路径。

### A121 — KMS 密钥生命周期的四处“写了不读/说了不做”（high）

以下四条均为我**自己 grep 复核**过的盘上事实（详细推导见 `C:/Users/清彧/AppData/Local/Temp/ocr-cds/` 下的 KMS 契约报告）：
1. `app/models/kms.py:65 encrypted_key` 只在 `app/api/kms.py:78,171` 写入，全仓 `encrypted_key = None` **0 命中** ⇒ 已撤销/已轮换的产品 DEK 永久保留 wrap 后的密钥块（与同文件 `volume_keys.wrapped_key` 在 `api/kms.py:389` 做 crypto-erase 形成直接对比）。
2. `rotate_kek` 全仓 3 处命中**全是 `def`**（`hsm_adapter.py:38/154/223`）⇒ KEK 轮换无调用方；软件 HSM 分支还会另起一个新 key_id（`new_key_id = f"{key_id}-v{...}"`），与 `kms_service.py:21 _KEK_ID = "cds-master-kek"` 的硬编码对不上 ⇒ 一旦轮换，所有已持久化 blob 解密为 None（fail-closed，不泄露但不可恢复）。
3. `hsm_adapter` 根本没有 `revoke` 方法（grep `def revoke` → 0），而 `kms_service` 类自述 `destroy_key() → delete from store + hsm revoke`，实际只删字典项 ⇒ 又一处“注释替系统作了保证”（与 A93/A113 同族）。
4. 跨语言密钥长度不对称：Go 侧 `cds-volume-plugin/cipher.go:25 dekLen = 16` 是**编译期常量**，Python 侧 `app/core/config.py:356 CUBE_DEK_LENGTH: int = 16` 是**运行时配置**；把 `CDS_CUBE_DEK_LENGTH` 改成 32 控面不会报错，但插件会永远拒绝（`kms.go:125 len(dek) != dekLen` fail-closed）。修法：启动时断言两值相等（或由控制面下发并在 `validate_security_config` 里钉死）。
另：`app/services/kms_service.py:83,167,191` 把 `os.urandom(32)` 注释为 `# SM4-256 key`，而 SM4 分组密钥固定 128 位、实跑的是 AES-256-GCM（`app/utils/crypto.py:67,72`）——“SM4-256”不是存在的算法，属命名诚实问题（与仓内已记的“SM4 实为 AES”同源，不另计危害）。

### A124 — 联邦信任：模型缺“同一空间对只允许一条有效行”约束，装载又无 ORDER BY（high，与 A85 叠加）

```
app/models/federation_trust.py                无 UniqueConstraint(local_space_id, remote_space_id)
app/services/federation_connector.py:346-370  _persist_trust 只 session.add（不 upsert）——见 A85
app/services/federation_connector.py:308-313  select(...).where(status.in_(["active","suspended"]))   # 无 order_by
app/services/federation_connector.py:314+     for row in rows: ... self._space_index[remote.space_id] = trust
```
重复建立信任会堆多行，重启装载时同一 `remote.space_id` 被逐行覆盖，**最终生效的是哪一行取决于数据库返回顺序**（已亲读 `:308-313` 确认无排序）；而 `trust_level`/`allowed_operations` 直接决定跨空间授权面 ⇒ 重启后授权面意外扩大或缩小。修法：加部分唯一索引（`WHERE status='active'`）+ `_persist_trust` 改 upsert + 装载按 `created_at desc, id` 定序。

### A126 — secure_checkpoint 根本没有落盘：进程内字典 + 每次启动随机主密钥 + 无容量上限（high，活路径）

```
app/services/secure_checkpoint.py:8-9    自述：“Used by sandbox training runtime to encrypt checkpoints before writing to shared storage”
:58   self._master_key = master_key or os.urandom(SM4_KEY_SIZE)      # 真值判断 + 随机回退
:59   self._checkpoints: dict[str, EncryptedCheckpoint] = {}          # 密文权重常驻内存
:197  secure_checkpoint_store = SecureCheckpointStore()               # 单例，无人传 master_key
:118-142  decrypt_checkpoint 自述“返回 None”，但 `_derive_key`(:131) 与 `bytes.fromhex`(:136) 在 try 外
app/core/config.py  grep CHECKPOINT|MASTER_KEY → 0 命中          # 没有任何主密钥配置项
全文件 grep write_bytes|open\(|Path\(|persist → 0 命中            # 零磁盘 IO
app/api/training.py:170 encrypt_checkpoint(...) / :475 list_checkpoints(job_id)
```
活路径由 `training.py` 钉住。四个后果：① 自述的“写入共享存储”不存在，重启即全部消失（不是“不可解密”，是直接没有）；② 多 worker 时每个进程各自一份内存，另一 worker 的 `list_checkpoints` 看到的是空；③ 未传 master_key 时静默 `os.urandom(16)`，无错无告警（配置读成空串也会被当成“没传”）；④ `_checkpoints` 无上限无淘汰，训练循环每 epoch 存一份全量权重密文⇒OOM 面。另 `decrypt_checkpoint` 在 `encryption_iv` 非法/`sm3_hash` 被 `CDS_SM3_FALLBACK_ALLOWED=false` 拦下时抛未捕获异常，而不是按接口约定返回 None。修法：要么接上真存储（加密后写对象存储/卷，元数据进 DB）+ 从 KMS 取主密钥 + 容量上限；要么把这个模块标注为“仅供单测的内存假体”并删掉共享存储的说法。

### A130 — 保留期工头：计数拿 SELECT 条数、单表异常后 PG 事务已 aborted（high，活路径，当前 DRY_RUN 兜住）

```
app/services/retention_janitor.py:25-30   id_rows = select(model.id)...limit(batch); total += len(ids)   # 不用 DELETE rowcount
:64-91                          外层 try + 单表 try/except Exception → 记 -1 继续，全文无 rollback/begin_nested
:96                             同会话继续执行 pg_advisory_unlock
app/services/session_lifecycle.py:309     from ... import purge_cycle     # AST 闭包：main.py reaches it? True
:73 settings.RETENTION_JANITOR_DRY_RUN   干跑分支只跑 COUNT，不发 DELETE
```
两条均代码属实：① `removed` 来自 SELECT 到的 id 个数，并发下与真实删除行数不一致（虚高），而 `docs/retention.md` 要求运维核对该数字；② PG 里一条语句报错后事务进入 aborted（25P02），吞掉异常继续下一张表只会让后续 COUNT/DELETE、`:96` 的 `pg_advisory_unlock`、以及调用方末尾的 `db.commit()` 连锁失败，把同一会话里 `cleanup_expired_sessions`/合同扫描的成果一并回滚——自述的“单表失败不影响其余”恰好只在 SQLite 上成立。当前默认干跑（AGENTS.md：“Janitor runs dry-run until compliance review”），所以不是现行数据事故；但 COUNT 本身也能失败（表/分区缺失、权限），一旦发生就是整条后台链回滚。修法：每表包 `begin_nested()` 并在 except 里回到 savepoint；计数用 `result.rowcount`。

### A134 — 审计验真页前后端字段错配，永远显示“失败”，并丢弃诚实披露字段（high）

```
app/api/audit.py:392-451   async def verify_audit_record ... rid = uuid.UUID(record_id)(:399)
                           → {"verified": verified, "backend": anchor_backend, "verification_note": note}(:441,:450-451)
app/templates/blockchain.html:72   API.get(`/api/v1/audit/verify/${recordId}`)      # 无 encodeURIComponent
:74                          ${result.valid ? badge('通过','success') : badge('失败','danger')}
:21                          placeholder="输入 tx_hash 进行验证"
:64                          加载失败: ${e.message}   → innerHTML
```
前端读 `result.valid`，后端从来没有这个字段（只有 `verified`）⇒ **无论验证成功与否都渲染红色「失败」**，而这恰好是监管/运维看的一页；同时后端专门返回的 `backend` / `verification_note`（T8/F1 用来诚实区分“联盟链 vs 本地 PG 追加日志”）被丢弃。叠加两个小面：输入框引导用户输 tx_hash，但后端 `uuid.UUID(record_id)` 只接受 UUID ⇒ 按提示做必报错；`recordId` 未 `encodeURIComponent` 就拼进路径；`:64` 把 `API.get` 拼出的后端响应原文（`${status}: ${text}`）直写 innerHTML。本文件的 XSS 面归到 A114/A115 的模板族定级，不单计。修法：改成 `result.verified` + 把 `backend`/`verification_note` 当必读信息展示、对 `recordId` 做 `encodeURIComponent` 且提示改为 record_id、错误改固定文案。

### A136 — session_templates 配置形状错→会话创建接口 500（high，活路径；本条原标“潜伏”已推翻）

`app/services/session_templates.py:96-97  (tpl.get("files") or {}).items()` / `(tpl.get("env") or {}).items()`：运维把 `CDS_SESSION_TEMPLATES_JSON` 写成 `{"a":{"files":["x"]}}` 这类形状时，非空 list 走 `.items()` 抛 **AttributeError**，而 `:102 except (ValueError, TypeError)` 接不到 ⇒ 自述的“无效配置只记 warning 并忽略”失效，异常从 `list_templates()`/`get_template()` 一路冒到会话创建接口（500）。可达性更正（本会话取证工具的 bug，见第四十一批开头）：本模块是**活的**，只是全部走函数内延迟导入 `from app.services import session_templates as st`——`app/api/sandbox_sessions.py:314`（建会话时先 `st.get_template(body.template)` 做模板存活性校验，**外层无 try**）、`:689`（seed workspace）、`:784`（列表接口）、`app/services/cube_runtime_adapter.py:150`、`app/services/cube_template_service.py:436`。⇒ 一个写错形状的 `CDS_SESSION_TEMPLATES_JSON` 就能让建会话直接 500，而不是自述的“记 warning 并忽略”。修法：`except Exception` 收口并逐项校验类型；接线时补上未接线声明。

### A138 — `key_distributions` 全仓无写入方 ⇒ 启动就跑的 TTL 清理循环是空转（high，活路径）

`app/models/kms.py:107 class KeyDistribution` / `:113 __tablename__ = "key_distributions"` —— 对 `app/` 全量 grep `KeyDistribution\(|key_distributions` **只命中这两行模型定义**（无一处构造/插入）。而 TTL 循环从 `main.py:107-109` 无条件启动（300s 一轮），`cleanup_expired_distributions` 只能扫到空表 ⇒ 自述的“分发到期即标记 EXPIRED 并终止会话”永远不会发生；即使有行，该循环也只改状态不 crypto-erase（`kms_service.py:380-433`）。`docs/spec-implementation-analysis.md:794` 已把它记为 STUB。⇒ 一个看起来在运行的安全控制实际零执行，按铁律属“假完成”一类：要么接上写入点，要么把这条从对外能力里拿掉。

### A139 — `import_wrapped` 对永远解不开的 blob 返回 True 并记“已恢复”（high，活路径：启动期）

`app/services/kms_service.py:367-378`：只校 `if not key_id or not wrapped` 就 `self._wrapped_keys[key_id] = bytes(wrapped)` + `logger.info("[KMS] Imported wrapped key ... from persistence")` + `return True`，**从不用 KEK 试解一次**。而软件 HSM 分支的 KEK 是进程局部的（`kms_recovery.py:9-12` 自己写明“跨副本恢复需要真 Vault/HSM”），于是默认形态下：上一进程持久化的 blob 在新进程里全部解不开（`unwrap` 失败后 fail-closed），但启动日志与返回值都说“已恢复”。修法：导入时强制 unwrap 自检，解不开就记 `unrecoverable=N` 并在 `/health` 里暴露（而不是 INFO 成功）。

### A141 — KMS 传显式 policy 静默绕过“生产必填”的度量白名单（high；时效与绑定另有归属）

```
app/core/config.py:216                ATTESTATION_ALLOWED_MEASUREMENTS: str = ""   # csv (prod: required)
app/core/config.py:549-553            KMS_REQUIRE_ATTESTATION 且该值为空 → validate_security_config 直接拒绝启动
app/services/remote_attestation.py:502-519   只有 `policy is None` 时才从 settings 读白名单
:601-608                              if self._policy.allowed_measurements: … not in whitelist → 拒
app/services/kms_service.py:317       AttestationPolicy(require_fresh_quote=True, max_quote_age_seconds=300)   # 显式传入
```
关键机制（我自己逐行读完分发链与 dispatcher 后的定判，与子代理的说法不同）：`AttestationService.__init__` **只在 `policy is None` 的分支里读 `ATTESTATION_ALLOWED_MEASUREMENTS`**，而 KMS 每次都是显式传一个 `AttestationPolicy(...)` 进去 ⇒ 白名单永远是默认空集，`:601` 的 `if allowed_measurements:` 直接跳过。后果：`config.py:549-553` 在启动时强制校验“生产必须配白名单”，但**被校验的那个值在密钥分发路径上从来没被使用**——运维配了白名单、门也过了，实际仍是 accept-any-TEE（注释自己写着“Empty = allow all”）。这就是典型的“两个组件各自正确、合起来失效”，修法：`__init__` 里无论是否传入 policy 都把运维白名单 merge 进去（或把 policy 默认值改为从 settings 派生），再加一条“KMS 构造的 policy 必须带非空 allowed_measurements”的门测。

同时把两条我一开始误判的更正写回（它们其实有实现，只是不在我最早读的那一段）：
- **时效确实执行**：`remote_attestation.py:586-594` 在 dispatcher 里比 `quote.timestamp` 与 `max_quote_age_seconds`，过期拒——所以 300s 窗口是真的，不是纸面值。
- **软件 quote 的“签名”确实无鉴别力**：`_sign_quote_payload`/`_signature_valid`（`:40-54`）是除 `signature` 外的 SM3 自摘要，measurement 又可由 `quote_id` 推出；但 `kms_service.py:303-315` 在 `ALLOW_SIMULATION=false` 时拒 `QuoteType.SOFTWARE_HASH`，那道门确实存在且默认安全（`AttestationResult` 还带 `trust_root="local-simulated"` 供下游区分）。
- **`report_data` 绑定确实存在于另一条链**：`result_verifier.py:313,350` 拿 `report_data` 与 `result_hash` 比对；而密钥分发路径不做这个比对（`kms_service.py:294` 只是把解析出的 `report_data` 塑进对象），也没有 challenge ⇒ **分发路径的 quote 在 300s 内与具体 key/session 无关（可重放）**，这才是本条真正的安全缺口，不是“时效未执行”。

### A142 — MPC 轮换先提交“删除旧分片”，再生成新分片（high，条件触发即密钥永久不可恢复）

`app/services/mpc_service.py:286-313`：`:299-300` 先把 `old_key.status="rotated"` 并 `delete(MPCKeyShareRecord).where(key_id=...)`，`:304 await db.commit()` **先落盘**，`:306` 才 `await self.split_key(...)` 生成并写入新分片。中间任何异常（`split_key` 的参数校验、DB 错误、进程被 kill）都把旧分片永久 crypto-erase 掉而没有新分片 ⇒ 密钥托管不可用且无回滚路径（`holders` 手里的分片也失效）。`mpc_service.*` 为活路径（`api/mpc.py:103,142,166,187,207,231,236,263,284` ← `main.py:339 /api/v1/mpc`）。修法：先算新分片→写新行→再标 rotated 删旧行，全部放同一个事务末尾提交。

### A143 — MPC 实现族：分片静态密钥、列表即解密、holder 无序、空指针、32 位主键（high）

```
mpc_service.py:97-101   _share_cipher_key = SHA256(AUDIT_ENCRYPTION_KEY or JWT_SECRET_KEY)   # 自述“same chain as egress_audit”
:118-128 / :260-277     _share_from_record 里 share_value=_decrypt_share_value(...)；list_keys 对每把密钥的每个分片都调它
:350-355                _load_holders 无 order_by 且 `[h for h in ... if h]` 滤掉 None
:250-258                get_share：key_rec = await self._load_key(...) 后直接 key_rec.threshold（_load_key 可返回 None）
:164                    key_id = str(uuid.uuid4())[:8]      # 32 位作为主键，而 _load_key 用 scalar_one_or_none
:39-41 / :167           _mod_inverse = pow(a, m-2, m)；secret_int = int.from_bytes(secret, "big") % _PRIME
```
逐条：① 分片静态加密的 KEK 直接由 `AUDIT_ENCRYPTION_KEY or JWT_SECRET_KEY` 取 SHA256，与出口审计共用同一秘密、无域分隔（同一 KEK 用于两种用途）、无版本号 ⇒ 轮换一处即弄坏另一处，且没配审计密钥时静默回落到 **JWT 签名密钥**；② `list_keys` 只是为了数分片个数，却把全库所有分片解密到内存（与 A12「可取回全部明文分片」叠加：连列表接口都在接触明文份额）；③ `_load_holders` 无序，拿它做“按位置把 holder 绑到分片下标”的输入，重启/并发后绑定可能错位；④ `get_share` 在孤立分片（key 行已没）上直接 `AttributeError` ⇒ 500；⑤ 32 位主键碰撞后 `_load_key` 的 `scalar_one_or_none` 抛 `MultipleResultsFound` ⇒ 整个密钥不可用；⑥ **两处定级修正**：`% _PRIME` 改变 DEK 的概率约 2^-32/次（子代理定 critical，我降为低概率隐患；真发生表现为 AEAD 解密失败而不是静默错数据）；重复 `share_id` 的危害不是 `_mod_inverse` “返回 0”而是 `pow(0, m-2, m)` 呖返回 0，Lagrange 该项直接贡献 0 ⇒ **不报错的错密钥**（M2/M5 合并后的准确说法）。

### A145 — DP ε 预算缓存永不失效，两副本各花各的额度（high，活路径）

```
app/services/dp_budget.py:44-45   self._cache: dict[str, float]; self._loaded: set[str]
:47-49     async def _ensure_loaded(...): if contract_id in self._loaded: return      # 一旦加载就再也不读库
:121-139   consume(): await _ensure_loaded(...) → remaining = self._cache[...] → self._cache[contract_id] = remaining - epsilon
:163       except Exception:                     # DB 写入失败被吞
:81-115    allocate(): 只更新本进程的 self._cache
```
活路径：`api/sandbox_tasks.py:451 consume`、`api/contracts.py:209` + `contract_service.py:171 allocate`。后果：helm/k3s 默认多副本下，每个 API 副本都拿自己那份 `remaining` 扣减且永不刷新 ⇒ **同一笔 ε 预算被副本数叠加花费**（隐私预算是合规控制，不是普通配额）；`consume` 先减内存再写库，库失败被 `:163` 吞掉 ⇒ 缓存与库永久背离且不会自愈（`_loaded` 使过期的缓存继续被信）。修法：扣减改成库内原子 `UPDATE ... SET used = used + :e WHERE used + :e <= total` 并看 rowcount（或 Redis 原子扣减），内存只做读缓存 + TTL；`allocate` 后必须 `_loaded.discard()`。另 `consume:133-139` 本身还是 check-then-act（非原子）。

### A146 — 网关配额：读不到限额当“无限制”，拒绝的请求照样计数，Redis 抖动永久转本地（high，活路径）

```
app/services/quota_manager.py:96-104   get_limit(): val = await self._redis.get(key) / except Exception: pass / return self._local_limits.get(key, 0)
:308 / :315                            if rows_limit > 0 and rows_increment > 0: …        # limit==0 即完全不校
:308-321                               pipe.incrby(...) 先加
:336 / :348                            if rows_current > rows_limit: return False, …      # 拒绝后不补偿
全文 grep decrby|decrement|rollback → 0 命中
:354-359                               except Exception → return self._check_gateway_quota_local(...)
:368-383                               _local_gateway_counters 是本进程 dict
```
活路径：`gateway_service.py:185/202 → api/gateway.py:227/263`（Redis 在 `main.py:58` 接线）。三个独立失效：① “限额 = 0”在调用方约定里就是**不限**，而 Redis 读失败/键不存在/另一副本写的 TTL 过期都会落到 0 ⇒ 配额门静默消失；② 计数在判定之前，超限的请求照样被 `incrby`，全文件没有任何 `decrby`/补偿 ⇒ 被拒流量抬高日计数器，当天后续正常请求提前耗尽额度（自 DoS），而计数不再代表真实交付量；③ 任何一次 Redis 抖动把用量永久迁到 `_local_gateway_counters`（不会迁回，且多副本下是 N× 限额）。修法：限额读取区分“未配置”与“读失败”（后者 fail-closed 拒请求或回退到配置默认）；超限时 `decrby` 回去或改为“先校后加”的 Lua；本地回退加“Redis 恢复后回写”与日志升级。

### A147 — 合约履行：容器已拉起后的其他异常只记进 errors，容器泄漏、会话停在非终态（high，活路径）

`app/services/contract_fulfillment.py:126-133` 先 `runtime.provision(...)` 并写下 `container_id`；`:145-165` 只对“`distribute_key` 返回空”这一种失败做了完整清理（destroy_key + `runtime.terminate` + 标 FAILED）；但 `:139 generate_session_key` 抛异常、`:147 distribute_key` 抛异常（而非返回空）、`:169-177 KeyMetadata` 插入失败、`db.refresh` 失败等路径直接冒到 `fulfill_contract:62-64`，那里只 `errors.append(...)`。因为 runtime 副作用不在 DB 事务里，外层请求回滚只会删掉会话行 ⇒ **容器继续在集群里跑（带已注入的数据通道）而数据库里没有任何会话指向它**，会话也不会进入终态。与 A90（`terminate()` 用错 id 导致卷孤儿）同一族：副作用与事务不共用一套生命周期。修法：`_provision_product_session` 内 `try/except` 包裹“容器已存在”之后的全部步骤，统一走已有的 terminate+标记清理分支；并给会话加一个带 `container_id` 的反向孤儿对账（后台扫描）。

### A150 — 联邦动态信任门的阈值表用 `.get(operation, 0)`，而 operation 由请求体决定（high，活路径）

```
app/services/federation_connector.py:290-292   _min_trust_by_op = {"search":0, "read_catalog":0, "read":30, "write":50, "execute":60, "admin":80}
:556-570                                       send_request()：evaluator.evaluate(...) → min_score = self._min_trust_by_op.get(operation, 0) → if trust_result.score < min_score: 拒
app/api/federation.py:287-299                  POST /federation/request → send_request(trust, **body.operation**, body.resource, …)
app/services/sandbox_runtime.py:2896           send_request(trust=trust, operation=operation, …)（上游也是变长字符串）
```
表里只登记六个名字，HTTP 入口却把**调用方自填的 `body.operation`** 透进去 ⇒ 写 `"query"`/`"fetch"`/`"READ"` 或任何拼写变体，`min_score` 就是 0，`score < 0` 永不成立，**这道自述“动态信任”的门完全失效**（`read` 本应要 30 分）。入口有 `require_roles(ADMIN, OPERATOR)`，所以不是未授权绕过，而是“已有的低信任空间限制可被一个字符串关掉”；联邦面止靠它隔高风险操作，属控制失效。修法：未登记的 operation 一律 fail-closed（取表内最大值或直接拒），入口把 operation 收枚为枚举。

### A152 — 两套视觉脱敏步骤都是假脱敏：不动像素却把 `is_redacted` 置 True（high，潜伏；接线即隐私泄露并违反 honest degradation）

```
app/services/vision_pipeline.py:165-181   FaceDetectAndBlurStep：`# In real impl: retinaface detection + gaussian blur` → 实际只 `metadata["faces_blurred"]=True; is_redacted=True`
:196 / :222                                   车牌 mosaic、DICOM strip 同样只置标志
:291-293 / :371                               `before_redacted` 差值统计与 `redacted_count` 把这些样本计入“已脱敏”
app/services/vision_multimodal_runtime.py    同样的空操作，只往字节尾部追加 `|cds_step=face_blur:…` 标记（ocr 的 critical 命中的是这个文件）
两文件 `grep simulat|ALLOW_SIMULATION|NotImplemented` → 0 命中
```
可达性：两个文件**都只被自己的单测导入**（全仓 `grep vision_pipeline|vision_multimodal`：除定义与 `tests/` 外 0 命中），所以不是现行泄露；但它们是项目铁律“禁伪造/必须披露”的反例：既没 `is_simulation` 也没报错，反而主动写了个**合规计数器会读**的 `is_redacted=True`（`:371`）。与 A132（`gpu_tee_simulator` 不填 `is_simulation`）同族，但这一条是“把未处理数据说成已处理”，优先级应高于普通披露缺失。修法：未接真模型前 `apply()` 直接 `raise NotImplementedError`（至少 `is_redacted=False` + 向上披露）。

### A155 — 网络策略引擎“只创建不执行”：三个执行钩子全仓零调用方，但审计里写着已强制（high，活路径）

```
活的写入侧：app/api/sandbox_sessions.py:683 create_policy、api/connectors.py:522（mode="deny_all"）、api/network_policy.py:104/223、remove_policy 在 api/network_policy.py:211/243 + api/sandbox_sessions.py:1039/1126 + services/cube_egress.py（L2' 热切换）
零调用方的执行侧（grep 全仓 app/ 除本文件：无命中）：
  app/services/network_policy.py:714  def check_connection(...)      # 运行期逐连接判定
  :756                      def get_bwrap_netns_args(...)   # 把沙箱放进 netns 的参数
  :769                      def get_dns_env_vars(...)       # 告诉沙箱用哪个 DNS 代理
```
`:756` 的 docstring 写着“allowlist 模式返回加入 netns 的参数”，函数体却在**两个分支都 `return ["--unshare-net"]`**（`:763` / `:767`），`:765` 算出的 `netns_name` 算完就没用过，注释里“we bind-mount the configured one”在代码里并不存在 ⇒ 沙箱从来不会进入 `create_policy` 辛辛苦苦建的 `cds-*` netns；`check_connection` 也无人调用，所以域名/IP 白名单**在任何一条路径上都没被执行过**。当前没泄露的唯一原因是巧合：真正生效的是 `sandbox_runtime.py:107/1612` 与 `firecracker_runtime.py:455` 的 `--unshare-net`（`isolate_network` 默认 True 且全仓无人传 False），即“全禁”而不是“按名单放行”。两个后果：① 配置了出网白名单的租户实际拿到的是全禁（可用性），而 `create_policy` 把 `result["netns_name"]`（`:430`）与链名写进了审计⇒ **合规记录声称已强制执行一个不存在的隔离**；② 任何为了“真出网”而改动 `isolate_network`/补上 `--netns` 的修改，都会直接落到 A156 那五个独立 fail-open 上。修法：把 `get_bwrap_netns_args` 接进运行时（返回 `["--netns", f"/run/netns/{netns_name}"]`）并在 egress 代理里真读 `check_connection`，或者删掉整个引擎并在 `docs/mock-remediation-plan.md` 登记为未实现（项目铁律：宁可拒绝不可伪造）。

### A156 — 网隔隔离链上五处独立 fail-open（接通即全量失效，high，条项依赖 A155 的接通）

```
:435   if self._iptables_available and config.allowed_ips:   # allowed_ips 为空→一条规则也不建（默认 ACCEPT）
:506-575 _create_netns：全部 subprocess.run(..., capture_output=True, timeout=5) 不校 returncode（全文 grep check=True → 0 命中），:573 无条件 `return True`
:695   _start_dns_proxy: local_addr=("127.0.0.1", port)   # netns 里的沙箱到不了 127.0.0.1，且端口不是 53，也无 53 端口 DNAT
:777   port = self._dns_port_counter - 1                # 全局单调计数器被所有会话共享，拿到的可能是别人的端口
:735-743 check_connection：`if config.allowed_ips: …` 否则 `return True`（与同文件 `_is_domain_allowed` 空白名单 `return False` 恰好相反）
```
逐条：非 root / netns 同名 / `10.66.0.1/24` 冲突 / `ip` 子命令不支持都会“失败但返回成功”，调用方拿着填好的 `netns_name` 去写审计；只配域名不配 IP 的策略等于没防火墙（域名过滤只靠那个不可达的 127.0.0.1 代理，攻击者直连 1.1.1.1 即可）；运行期兑底对“空白名单”的解释是**放行**而不是默认拒绝。另外 `sandbox_runtime.py:135` 预留了“不隔离时注入 DNS 代理环境变量”的接口，但 `:396` 传的是 `dns_proxy_port=None` 且 `get_dns_env_vars` 无人调用，所以那条路也是空的。修法：逐步 `check=True` + 失败回滚已建资源；链末尾无条件 DROP；代理绑 `10.66.0.1:53` 或加 DNAT；端口按 session 存字典。

### A164 — `gateway_metering` / `sandbox_activity_events` 有写入有查询但全仓无建表 DDL ⇒ 计量静默归零（high，活路径）

`app/services/gateway_service.py:217` 与 `sandbox_audit.py:259/844` 往这两张表 INSERT（失败只 `logger.warning` / `except Exception: pass`），`api/gateway.py:364`、`api/sandbox_sessions.py:1543` 又 SELECT 它们对外报数；但 `grep -rln 'gateway_metering|sandbox_activity_events'` 只命中代码与 `app/services/clickhouse_schema.py`（该模块不执行），`scripts/clickhouse-init.sql`（挂进 CH 容器的那份）**零命中**，全仓无 CREATE TABLE ⇒ 写入一直失败而被吞，计量接口永远返回 0（不报错）。与 AGENTS.md 已记的“k8s/configmaps.yaml 里是 8 列漂移副本”同族，但这一条是表根本不存在。修法：把两张表的 DDL 加进 `scripts/clickhouse-init.sql`（或在启动时对账 `clickhouse_schema.py` 与 CH 实际表并告警），并把 INSERT 失败从 warning 升级为可见指标。

### A165 — `unstructured_pipeline`（OCR/ASR/视频/DICOM）三条 LIVE：路径零约束、残留明文、DICOM 脱敏只做顶层（high，活路径）

```
app/api/data_pipeline.py:107-119  POST /process-path?input_path=… 只 `Path(input_path).exists()`，角色 ADMIN/OPERATOR
app/services/unstructured_pipeline.py:99-111  src = Path(task.input_path) → 目录走 shutil.copytree、文件走 copy2，无任何前缀/越界校验
全文 grep `terminate|provision` → 0 命中（不进会话生命周期，`/tmp/cds-pipeline/<task_id>` 无人回收）
:475/497/504  results["partial_success"] = True —— 全仓无任何读取方（grep：只这几个写入点 + data_processing 自己那份）
:491-504  DICOM 只对顶层 tag `delattr`，`save_as` 不重写 PatientID/StudyInstanceUID
```
三条后果：① 一个持有会话语柄的运营/管理员可以把 `/tmp/cds-uploads/<另一个用户>` 整目录拷进自己的任务输入并拿到 OCR/抽帧结果（跨租户读，且产物在输出审查**之前**就已上传）；② 明文输入/OCR 全文/抽帧图永久落在宿主机 `/tmp/cds-pipeline/`，既不经会话回收也不走卷级 crypto-erase（与 A90/A53/A54 同族，但这一条是整份明文中间产物）；③ DICOM 脱敏失败只把 `partial_success=True` 写进一个**没人读**的字段，任务依旧报成功 ⇒ “影像元数据已剥离”是不可核验的说法（铁律：宁可拒绝不可伪造）。修法：`input_path` 限定在上传根下并用 `path_security.safe_join_object_name`；任务结束无条件 `rmtree` 工作目录（失败则告警 + 纳入工头回收）；DICOM 失败直接判 task failed。

### A167 — 水印作业：`key_seed` 无上限→ int4 溢出，**在 checkpoint 已改写之后**才 500（high，活路径）

`app/api/training.py:488` `key_seed: int | None = Field(None, description=…)`——相邻的 `num_bits` 有 `ge=8, le=512`，这颗种子什何都没有；而 `app/models/watermark_record.py:33` 是 `mapped_column(Integer, …)` ⇒ PG `INTEGER`。顺序致命：`:839 _save_model_checkpoint(checkpoint)` 先把已嵌水印的权重落盘，`:851-852 db.add(record) / await db.flush()` 才插入记录；`key_seed=10**12` 在 flush 里抛 `value out of range for type integer` ⇒ 事务回滚掉“模型已被水印”的**唯一审计记录**，而磁盘上那个模型已经被改过且无人知道（审计缺口 + 不可重放）。修法：`Field(ge=0, le=2**31-1)`（或把列改 `BigInteger`）；并把“改权重”与“写记录”放进同一个失败即回滚的边界。

### A168 — OpenAPI 端点配额 `calls_used` 是“读-改-写”，竞态窗口 = 整次沙箱执行（high，活路径）

`app/api/open_api.py:29` 装载行 → `app/services/delivery_service.py:300` 用内存里的副本判 `calls_used >= quota_total` → 中间跑完整次沙箱 → `:390-391 endpoint.calls_used = (endpoint.calls_used or 0) + 1` + flush，提交在 `app/core/database.py:39` 请求末尾。两个并发请求从同一个值出发，后写的 UPDATE 直接盖掉前一个 ⇒ **配额少计、可超用**（与 A145/A146 同族：计数器的读与写不在一个原子步里）。修法：`UPDATE api_endpoints SET calls_used = calls_used + 1 WHERE id = :id`，并在门禁处用 `SELECT ... FOR UPDATE` 或直接靠原子自增后的返回值判超限。

### A175 — helper 可执行文件默认是个相对名，等于把 DEK 交给 PATH 上的任意二进制（high）

```
cds-volume-plugin/config.go:59   HelperPath: envStr("CDS_VOLUME_PLUGIN_SM4_HELPER", "sm4_gcm")     # 默认值无路径
 cds-volume-plugin/cipher.go:46  cmd := exec.Command(c.helperPath, op)                              # 相对名 → exec.LookPath 扫整个 PATH
 cds-volume-plugin/sm4gcm/sm4_gcm.h / AGENTS.md：密钥只走 **stdin，绝不走 argv/env**，就是为了不进 /proc
部署侧：cds-volume-plugin/Dockerfile:53 钉了 `CDS_VOLUME_PLUGIN_SM4_HELPER=/usr/local/bin/cds-sm4-gcm`（镜像里安全）；但 deploy/cube-node/install.sh 只往 cubelet 的 config.toml 写 socket/bin，**全仓没有任何地方给主机启动路径设这个变量**
```
后果：主机/cubelet 直接拉起插件时（非镜像），`exec.Command("sm4_gcm")` 按宿主 PATH 找程序。默认情况下找不到就 `start: ... not found` 硬失败（fail-closed，好）；但只要 PATH 里有一个非特权用户可写的目录（节点上很常见），那个目录里的同名二进制就会被以插件权限执行，而**插件递给它的 stdin 里就是明文 DEK**，返回什么密文/tag 也由它定 ⇒ 卷加密与密钥托管同时失效。与 A162（`shutil.which` 不传 `path=`）同一根因，但这里送出去的是密钥。修法：`config.Load` 阶段就 `filepath.IsAbs` 否则拒绝启动，并检查目标文件为 root:root 且非 group/world-writable（插件本来就做 0600/0700 文件纪律，不缺这一行）。

### A180 — `do()` 丢弃 `io.ReadAll` 错误且只把 `>=300` 当失败 ⇒ `RevokeDEK` 可谎报“密钥已销毁”（high，活路径）

`cds-volume-plugin/kms.go:79` `data, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))` 把读错误直接丢掉，`:80` 只看状态码。对 `RevokeDEK`（`out == nil`，`:132`）来说，控制面/代理返回 2xx 但响应体被中途切断（chunked 截断、TLS 错误、代理重启）时仍然 `return nil`；契约写的是 **204**，而任何 2xx（包括 `200` + 空体/HTML）都被接受。上层语义恰好相反：`plugin.go:385` 只有 `RevokeDEK` 报错才保留数据（AGENTS.md 铁律：“revoke failure ⇒ data RETAINED, never delete with a live key”），`plugin.go:93` 的 `abortAfterRegister` 同理 ⇒ 这条假成功让插件在**密钥可能仍存活**的情况下按“已 crypto-erase”继续往下做。修法：`do` 加 `expectedStatus`（Revoke 传 204）并处理 `io.ReadAll` 错误；需要响应体的调用（Register/Unwrap）在 `len(data)==0` 时报错。

### A186 — 选位密钥 `key_seed` 明文入列且原样回给 API 调用方（high，活路径）

`app/services/model_watermark.py:22` 把 `key_seed` 放在“Persisted metadata” dataclass 里，而 `:113` docstring 自己写 `key_seed: Secret seed for the PRNG`，选位用的是 `:121 rng = random.Random(key_seed)`（**Mersenne Twister，非密码学 PRNG**）；`app/models/watermark_record.py:33` 是 `mapped_column(Integer)` 明文列（直接违反 `app/models/AGENTS.md` 的“Never add a plaintext key/secret column”），`app/api/training.py:497` 响应模型里就有 `key_seed: int`，`:904` 真的把 `record.key_seed` 填回去。后果：水印的全部安全性 = 这颗 31-bit 种子，而它既可明文查库也可从接口直读；拿到 seed 的人能精确算出每一位位置，对已发模型做**定向翻转/擦除**而不被发现（同时 :296-303 的 3 位小数量化本身就可统计筛位，见子代理 #25）。修法：`key_seed` 当密钥处理——存 `bytea`/加密列（仓里已有 `KeyMetadata.wrapped_payload` 契约），API 不回 seed，只回一个 SM3 摘要供对账。

### A189 — DLP 命中的**原值**写进 `findings.samples` 并随 `security_report` 回给调用方（high，活路径，二次泄露）

`app/services/output_inspection.py:174 samples=matches[:3]`——`matches` 是 `pattern.findall(output)` 的原始命中串，全文无任何掩码函数（grep `_mask` 0 命中）；`:205 samples=violations[:3]` 同。子代理实测：身份证号→`findings[].samples = [['110101199003077758'], …, ['13800138000'], ['a@b.com']]`，而 `redacted_output` 已正确脱敏成 `[REDACTED:id_card]` ——**正文脱敏了，findings 里又送了一遍原值**。出口不止一个：`api/output_control.py:150` 写审计、`output_security.inspection_to_report()`（11 个调用点）把 findings 序列化进 `security_report` 返回给调用方、`session_stream.py:111` 逐行审查也带着它。这是“输出管控”自身的泄露通道（拿得到审查报告的人 = 拿得到被禁止外发的 PII 原值）。修法：`samples` 改成带上下文的掩码串（`110101****7758`）或只留 `{rule, span, count}`；真要留原值就单独存加密列且不对业务接口返回。

### A191 — 输出报告的 SM2 签名密钥是**进程内随机生成**，不可抵赖性不成立（high，活路径）

`app/services/output_inspection.py:83 _signing_keypair = None` + `:300-305` 类变量惰初始化（无锁、不从 KMS/配置加载）。子代理实测：把类变量置回 None 后重新签名，新旧密钥对不同 ⇒ 一次重启/一次多进程部署就换钥，旧报告无法验证、新报告任何人都能重签；而无锁 check-then-set 在同步审查路径（`deliveries.py:124`、`task_worker.py:139`）上有 TOCTOU，同一进程内就可能出现两把公钥。与 A126（`secure_checkpoint` 每次启动随机主密钥）同根：**拿临时密钥做审计性签名**。修法：从 KMS/`certificate_service` 取固定作业密钥（公钥写进合规报告与锚定记录），惰初始化加锁或改启动时初始化。

### A192 — DP 阶段既谎报 `True` 又不登记 `not_evaluated`；无预算也计 `dp_applied=True`（high，活路径）——**与 2026-10-16 拍板的边界已写明**

```
app/services/output_inspection.py:227-229  DP 在 active_stages 就 `stage_results[DP] = True`（预置），只有 `dp_epsilon > 0` 才真的做
:264                                        `# else: epsilon is None or <= 0 -> DP not applied (honest no-op)`
全文 `not_evaluated` 只登记 DATA_RECONSTRUCTION(:185) / K_ANONYMITY(:209) / MIA(:283)，**DP 完全没登记**
:230 `if dp_budget_ledger.has_budget_sync(session_id):` 才走预算；`:250-263` else 分支直接 `_apply_dp_noise` + `dp_applied = True`，不记账
app/services/dp_budget.py:186 / :175-181  `has_budget_sync` 是**进程内缓存**，注释自认 “process-local (they survive the request, not a restart)”
```
两点都是“只披露”这一半没做到：DP 没跑却把阶段结果置 True，而且不进 `not_evaluated`/`degraded` ⇒ **披露渠道里根本看不见 DP**（同一文件其余三个阶段都登记了，还自述遵循 SS-18 §8 真空口径）；无预算的旁路只给了 `logger.info` + `dp_budget_remaining=None`，而“未分配预算即可无限次拿到已应用 DP”与 M-03 的双预算统一相左（重启后缓存清空还会重开旁路）。
**改法边界（必读）**：本仓已定口径（用户 2026-10-16 拍板，见记忆卡 `reference/decisions/output-gate-vacuum-disclosure-only.md`）是“成果闸对阶段级真空**只披露不拦**，`should_block()` 故意不读 `not_evaluated`/`degraded`”。所以本条的修法**不是**往 `should_block()` 加条件（那会直接推翻拍板），而是：① DP 未应用时 `stage_results[DP]` 不预置 True 并登记进 `not_evaluated`（把披露补齐）；② 无预算分支不给 `dp_applied=True`（或改成 `dp_applied=False` + `degraded=["dp_no_budget"]`）；③ `has_budget_sync` 跨进程取真预算（Redis）而不是进程内字典。要重提“阶段级真空到底拦不拦”只能以“口径要不要改”的形式，并先补 `source_rows` 承载。

### A193 — SM2 签名失败时用 SM3 摘要顶替并记 `stage_results[SIGNATURE]=True`，而 `passed` 是 `all(stage_results.values())`（high，活路径）

```
app/services/output_inspection.py:306  stage_results[SIGNATURE] = bool(sig)
:307-313  except Exception: sig = crypto_service.sm3_hash(redacted.encode()); stage_results[SIGNATURE] = True; degraded.append("… not a GM/T signature")
:319      passed = all(stage_results.values())
```
降级本身是有披露的（`degraded` 里写了“不是 GM/T 签名”），但同一结果里 `passed` 却把“一个 SM3 摘要”当作“签名阶段通过”计入（`passed` 由 `stage_results` 相与得出，而 `degraded` 不参与），对外（`inspection_to_report` 11 个调用点、合规报告）报的就是“签名已做”。子代理实测：monkeypatch 让 SM2 失败后 `signature` 字段变成摘要且 `passed=True`。第 二重：`except` 分支里自己又调一刃 `sm3_hash` —— 而 `sm3_hash` 在 gmssl 缺失且 `CDS_SM3_FALLBACK_ALLOWED=false` 时（本仓 known 行为）会 `raise RuntimeError` ⇒ 异常从异常处理里逸出，整个审查接口 500（错误路径上的错误）。修法：签名失败就 `stage_results[SIGNATURE] = False`（或把 `passed` 改成“必需阶段集”相与且签名失败时不计入），`except` 内不再做任何可抛的加密运算，降级摘要另存字段并明写“未签名”。

### A197 — grpc 服务未设消息尺寸/并发上限/恢复拦截器，与 `MaxVolumeBytes=1GiB` 自相矛盾⇒>4 MiB 卷数据必失败（high，活路径，我逐行复验）

`cds-volume-plugin/server.go:165 srv := grpc.NewServer()` —— 无任何 `grpc.MaxRecvMsgSize/MaxSendMsgSize`（全目录 grep 零命中），而 `config.go:64 MaxVolumeBytes: envInt64("CDS_VOLUME_PLUGIN_MAX_VOLUME_BYTES", 1<<30)` 把卷上限定成 **1 GiB**；Python 侧 `cube_runtime_adapter.py:593 stub.ReadSandboxFile(...)` 也不传 channel options、不分片。grpc-go 默认接收上限 4 MiB ⇒ **任何 >4 MiB 的 `PutVolumeData`/`ReadSandboxFile` 在传输层就断**，而它向上表现为一个无关的 `INTERNAL_ERROR`（与 A180、A179 同一个“卷数据路”）。同一个 `NewServer()` 还没有 recovery 拦截器与每卷串行化 ⇒ 这是 server #2/#9、plugin #4/#8、layout #2 几个表象的公共根因。修法：`grpc.NewServer(grpc.MaxRecvMsgSize(n), grpc.MaxSendMsgSize(n), unary拦截器)` 且 n 与 `MaxVolumeBytes` 取齐（或改成流式/分片），并加 per-volume mutex。

### A199 — `volume_id` 在 RPC 边界零校验，`paths()` 是 VolumeRoot 约束的唯一汇聚点且未净化；`cubeboxID` 放行 glob 元字符并静默取 `matches[0]`（high，子代理逐条复验，我未复验）

`validateVolumeID` 全目录只有两处：定义 `plugin.go:29` 与唯一调用 `plugin.go:50`（CreateVolume 内）；Attach(`plugin.go:199`)、PutVolumeData(`:133`)、Detach(`:314`)、Destroy(`:381`)、GetVolumeInfo(`:404`) 直接用调用方给的 id，gRPC 侧只校非空（`server.go:45/73`），Destroy/Get/Put 透传（`:84/:99/:107`），跳出发生在 `layout.go:57-58 filepath.Join(r.cfg.VolumeRoot, volumeID)`（子代理探针：`filepath.Join("/var/lib/cds/volumes","../../etc/x") → …/lib/etc/x`）。`rootfs.go:47` 只拒空串/超长/含 `/` `\` `.`，`*?[` 全放行，`:65-69` 拿 `filepath.Glob` 的第一个匹配当镜像目录。两条影响面按子代理**下调**：读取/销毁路径都要先过 `loadManifest`，不是“任意文件销毁”；而 `cubeboxID` 的 glob 分支今日走不到——仓内调用方永远传空 session_id（`cube_runtime_adapter.py:923`、`:938`）⇒ 该子项按潜伏计。修法：在 `server.go` 入口统一 `validateVolumeID`（白名单字符集），`paths()` 里再做一次包含性检查（`filepath.Rel` 不得以 `..` 开头）。

### A200 — 销毁语义四处违反「销毁即不可恢复 / shred 失败要报错」（high，子代理逐条复验，我未复验）

```
plugin.go:388-398  Destroy 只 shred ct/nonce/tag/manifest 并 os.Remove(dir)，**不碰 staging 明文**，也不看引用计数（DestroyRequest 无 refCount、服务端无记账）
plugin.go:314-316  Detach 定位 stage 目录纯靠调用方传的 base_dir（与 manifest 无关）；base_dir 缺失就直接 return，shredFile 根本不执行
server.go:57-58    Detach 零必填校验（对比 Attach 在 :45-47 会返 InvalidArgument）；volume_id 空但 base_dir 合法 ⇒ stageDir=`<base>/cds-` ⇒ 文件不存在 ⇒ 静默 `return nil`
plugin.go:345-373  shredFile 全文无 `Sync`：:363 WriteAt → :367 Close → :370 **无条件** `os.Remove(path)`（firstErr 已非 nil 也删）
plugin.go:389-395  `_ = shredFile(f, fi.Size())` 丢错误后仍 `return nil`，与同文件 :334-336 的 fail-closed 立场（“stage shred failed, plaintext retained”）直接矛盾
```
后果：一个仍在挂载使用的卷可被直接 Destroy（`server.go:84` 与 `cli.go:118` 都可达），0400 明文永久留在 `<base>/cds-<vid>`；零写未落盘就 unlink 意味着掉电/快照场景下“shred 成功”并不等于磁盘上没了。缓解事实（如实计）：DEK 已在 `plugin.go:385` 撤销，所以残留明文只在 KMS 未生效窗口内可解。

### A201 — 自定义 AAD 参与 Seal 但从不写回 `Manifest.AAD`⇒该卷永久无法 Attach（high，数据不可用，子代理逐条复验，我未复验）

`plugin.go:140-142 if aad == "" { aad = m.AAD }` ⇒ 调用方可以传自定义 AAD；`:164 r.cipher.Seal(dek, nonce, []byte(aad), data)` 用的就是这个自定义值；`:183-185` 只回写 `HasData`/`DataSizeBytes`，**AAD 从不更新**（AAD 只在 `plugin.go:81` 以 `AAD: name` 写入一次）；Attach 解密固定拿 manifest 的 AAD（`plugin.go:249`）⇒ 任何显式传 AAD 的 Put 之后，该卷 Attach 必 GCM 认证失败（数据在、钥匙对不上 —— 不可用而不是泄露）。修法：Seal 成功后把实际使用的 aad 写回 manifest（并在幂等比对时一并比较）。

### A203 — 四处“体积”列用 4 字节 `Integer`，而 PG 上 `>2,147,483,647` 字节（约 2.1 GB）写入必报 `IntegerOutOfRangeError`（high，活路径，我亲验）

```
app/models/data_resource.py:60        file_size_bytes: Mapped[int | None] = mapped_column(Integer)      # 上传数据集
app/models/trained_model.py:34        size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)   # 模型 checkpoint
app/models/delivery_bundle.py:29      size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
app/models/rag_generative_model.py:27 size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
alembic/versions/0009_trained_models.py:28  sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0")
```
模型与迁移是一致的（`scripts/verify_schema_parity.py` 拓不到，它只对比“模式一致”而不是“类型够不够”），坏的是类型本身：SQLite（开发/测试）的 INTEGER 是 64 位，**只有 PG 上这才是 4 字节有符号数**，⇒ 本地永远重现不出来，上生产传一个大文件/大 checkpoint 才在 INSERT 处报错。同仓已知道止确写法：`app/models/cube_template.py:75 size_bytes: Mapped[int | None] = mapped_column(BigInteger)`，所以这不是口径未知而是漏改。修法：新增一个 revision（additive-only，不改历史）把四列 `ALTER COLUMN ... TYPE BIGINT`，模型同步改 `BigInteger`（两边一起改才能过 parity 门）。

### A204 — 三条互相咬合的构建期断链：镜像没有 CA 根⇒https KMS 必炸；国密后端被唯一自动化门静默跳过；libdir 两套口径（high，①② 我实测/亲验）

**① 最终镜像里没有 CA 根证书，所以上不了 https**（本轮实测，不是推论）：`docker run --rm debian:bookworm-slim ls /etc/ssl/certs/ca-certificates.crt` → `No such file or directory`（同一探针顺便证伪了我自己的一个担心：`/usr/bin/mount` 与 `umount` 在 slim 里**确实有**，所以 `rootfs.go:86` 的 loop mount 不依赖额外安装）。而 `kms.go` 注释与 `cds-volume-plugin/AGENTS.md` 都写“prod transport is mTLS”；`config.go:61` 默认 `http://127.0.0.1:8000`。⇒ 只要部署按口径把 `CDS_VOLUME_PLUGIN_KMS_BASE_URL` 配成 https，Go 默认根池为空 ⇒ 每次 DEK 调用都是 `x509: certificate signed by unknown authority`，而 KMS 客户端 fail-closed、无离线模式 ⇒ **CreateVolume/Attach 全线不可用，且与构建全绿无关（只在运行期冒）**。与 A179（`kms.go:47-52` 写死无 `TLSClientConfig` 的 `http.Client`，无客户端证书注入路径）合起来是一句完整结论：**这个镜像既没根证书也没客户端证书，根本不具备它文档里假定自己拥有的那条 mTLS 传输。**
**② 国密后端被唯一自动化门静默跳过**（亲验）：`plugin_test.go:319-323` 把 tongsuo 后端放在一个条件里——`if _, err := os.Stat("../sm4gcm/sm4_gcm"); err == nil { ciphers["tongsuo"] = NewTongsuoCipher(helper, 30) }`，不在就不加，**既不 `t.Skip` 也不 `t.Fatal`**；而 `sm4gcm/sm4_gcm` 在 `.gitignore:3`、`Makefile:27-28` 的 `test:` 目标不依赖 `helper` ⇒ 干净 checkout 上 `go test ./...` **全绿而 SM4-GCM 的负向用例（篡改 ct/tag、AAD 不符）一次都没跑过**（全文件只有这一处用 tongsuo，grep `tongsuo` 0 其他命中）。考虑到插件根本不在 `ci.yml` 里，“跑一次 `make test`”已经是它最高的自动化保障，而这一保障国密路径时是假的。修法：缺 helper 就 `t.Fatal`（像 `Dockerfile:32` 对 `frobnicate` 退出码那样显式判定）+ `test: helper` 依赖 + `-race`。
**③ libdir 两套口径**（亲验两边写法）：`Dockerfile` 的 tongsuo 阶段用 `ls /tongsuo/lib*/libcrypto.a` 通配兼容 `lib`/`lib64`，而 `sm4gcm/Makefile:12 TSLIB := $(abspath $(PREFIX)/lib64)` 钉死 `lib64` ⇒ 若 Debian 上 Tongsuo 装到 `/tongsuo/lib`，**存在性检查会绿、helper 编译会炸**（`cc: … No such file`，看不出根因是 libdir）。属构建期响，不是静默——但仍是一个“镜像能不能造出来”的随机坑。修法：`./Configure --libdir=lib64`，或把实际目录显式传给 make。

### A206 — `readme = "README.md"` 写在 `[project.optional-dependencies]` 里 ⇒ SDK 打包直接失败（high，活路径，两个探针实测）

`sdk/pyproject.toml:8-12`：`:8 [project.optional-dependencies]` 之后依次是 `:11 stream = ["websocket-client>=1.6"]` 与 **`:12 readme = "README.md"`**（没有新开 section，所以 `readme` 落进了 extras 表），而 `[project]` 段（`:1-6`）里没有 `readme`。PEP 621 要求 extras 的值是字符串数组，于是 setuptools 的配置校验直接报非法。两个探针（本机 `.venv-gate`，**setuptools 82.0.1 / CPython 3.12.10**）：
```
read_configuration('sdk/pyproject.toml')
  → ValueError: invalid pyproject.toml config: `project.optional-dependencies.readme`.
    configuration error: `project.optional-dependencies.readme` must be array
pip wheel --no-build-isolation --no-deps ./sdk        → RC=1  Preparing metadata (pyproject.toml): error
pip install -e ./sdk --no-build-isolation --no-deps    → RC=1  Preparing editable metadata: error（`.github/workflows/ci.yml:34` 就是这一句）
```
堆栈里 `read_configuration(filepath, True, ignore_option_errors, dist)` 说明 `ignore_option_errors=True` 只豁免“选项级”错误，`[project]` 表的 schema 校验依旧会抛。
**口径边界（如实写）**：我只在 setuptools 82.0.1 上测过；本机没有第二个 setuptools 版本可对比（全仓只有 `.venv-gate` 一个 venv），所以“旧版 setuptools 是否只警告不报错”未测。⇒ 两种读法：要么 CI 靠旧版 setuptools 侥幸过，要么这一步本来就是红的（AGENTS.md 写的“CI 全绿”是文档，本轮我没有跑 CI 的证据）。无论哪种，**按 PEP 621 这份文件就是非法的**。修法一行：把 `readme = "README.md"` 移到 `[project]`（`sdk/README.md` 确实存在，已 `ls` 确认）。

### A241 — 遗留 `/admin/*` 页面全是 `innerHTML` 直接插 API 数据，没有任何转义（high，**门后可达**：默认关、生产启动拒绝，我亲验整条链）

缺陷本身为真，`app/static/js/admin.js:33-35`：
```
function badge(text, type) { return `<span class="badge badge-${type}">${text}</span>`; }
app/templates/alerts.html:43  $('#alerts-table').innerHTML = list.map(a => { … ${badge(a.severity…)} … ${a.alert_type || a.type}
```
全文件 `grep -c 'escapeHtml\|textContent\|createTextNode'` = **0**（没有任何转义入口），而写回用的是 `innerHTML`：后端返回的 `alert_type`/`message`/用户名/备注一旦带 `<img src=x onerror=…>` 就是存储型 XSS。`app/templates/` 下 `alerts / base / blockchain / certificates / connectors / identities` 六张页同一写法，ocr 在这一族上报了 20 条 critical + 42 条 high——全部归入本条，不逐条开号。
**现行性我把链跑完了**（这才是定级的依据）：`app/core/config.py:31 ADMIN_PAGES_ENABLED: bool = False`（默认关）→ `app/main.py:411/415/418` 只在开关为真时 `include_router(admin.router)` 并打 `[SECURITY]` 告警 → `app/core/config.py:558-563` 在 `validate_security_config()` 里直接 `raise`「production 不允许 ADMIN_PAGES_ENABLED=true」。⇒ **生产不可达，但 dev/演示只要把这个开关打开就是一整片无鉴权 + 可 XSS 的面**（`app/api/admin.py` 的路由本身无任何依赖），所以定 high 而不是 medium：门的写法是对的（正向记下），但门的宽度是「整个面」而不是「逐页」。
修法两选一：① 直接删掉遗留页（仓里注释已写明 React 控制台才是真管理台）；② 若还要用，`badge()`/表格行改成 DOM API + `textContent`（一个转义函数不够，得把 `innerHTML = list.map(...)` 那层也拆掉）。

### A242 — 遗留管理台与后端契约已经对不上：四类契约脱节 + 内联事件注入 + 所有 DELETE 成功都会被报成失败（high，同样门后可达，我逐条读到盘上）

这批和 A241 同一个可达域（`ADMIN_PAGES_ENABLED`），但是不同类的错，所以单独开号：
1. **`del()` 不发请求体⇒证书吊销必然失败**。`admin.js:17-20 async del(url) { fetch(url, {method:'DELETE', headers}) … return res.json() }`（无 body），而 `app/api/certificates.py:123-126` 的 `DELETE /{cert_id}` 签名是 `body: HighRiskOperationRequest = Body(...)`（reason 必填、min_length=8）；`certificates.html:114 await API.del('/api/v1/certificates/${id}')` ⇒ 422。同一个 `del()` 还无条件 `res.json()`，**204 空体会抛 SyntaxError**，所以即使契约对上了，成功删除也会进 `catch` 弹「失败」（`:116`）。
2. **告警枚举对不上**。`app/models/alert.py:12-15 AlertStatus = open / acknowledged / resolved`，而 `alerts.html:44-45` 是 `const status = a.status || 'active'; if (status === 'active' || status === 'firing') active++` ⇒ 带正确 `open` 的记录**不计入活跃**，反而 `status` 缺失的记录因为兼底成 `'active'` 全部计入——两边的错叠在一起，不是简单计 0。
3. **「已验证」根本没验证**。`blockchain.html:52/57` 的计数与状态列都是 `detail.merkle_root || detail.tx_hash` 有值就算 verified，从不请求 `/api/v1/audit/verify/{id}`；而 `audit.py:391-400` 的 verify 只吃 `uuid.UUID(record_id)`，页面输入框却写「输入 tx_hash 进行验证」（`:21`）且 `:70` 提示「record_id 或 tx_hash」⇒ 粘 tx_hash 必 400。另外 `:69-70` 的 `recordId` 只 `trim()` 就拼进 URL 路径（无 `encodeURIComponent`）。
4. **证书类型下拉与后端不等**。`certificates.py:20 cert_type` 只允许 `signing|encryption`（所以前端把 root/intermediate 都映射成 signing **不会 422**，这条 ocr 说重了），但根 CA 与中间 CA 因此不可区分（信息丢失）。
5. **内联事件里的 JS 字符串注入**：`certificates.html:74 onclick="revokeCert('${c.cert_id || c.id}')"` ——`cert_id` 来自后端且未做 JS 转义，单引号即可跳出字面量。
6. **XSS 与凭证存储合成一条完整链**：`admin.js:22-25` 把 token 存在 `localStorage['cds_token']`，无过期/无刷新/401 不清除；配上 A241 的存储型 XSS，一次注入就能拿到管理台身份。单看两条都是 medium，合起来是这条定 high 的理由。
取证备注：查 2 与查 4 时我先用带反斜杠转义的双引号去 grep，拿到**空读数**差点当成「没有这个写法」；换成单引号/裸模式才发现 `:44-45` 与 `:74` 两行都在——空读数不等于不存在。

### A244 — 全站没有任何 ErrorBoundary，叠加能力矩阵对 `data.matrix` 的强假设 ⇒ 一个空字段白屏（high，我亲验）

`grep -rln 'componentDidCatch\|ErrorBoundary' cds-frontend/src/` → **0 个文件**；而 `pages/CapabilityMatrix.tsx:30-31` 直接 `Object.keys(data.matrix)` 与 `data.matrix[levelKeys[0]]`（`:60` 反而写了 `data.matrix[record.key]?.[cap]`，同一个文件里两种谨慎程度）。后端只要返回 `matrix: null` / 缺字段 / 网关改写成空壳，这一屏就抛 TypeError，而没有边界的情况下抛穿到根 ⇒ 整应白屏。同类强假设在 `pages/Flow/contractDataModel.ts`（`fulfilled` 直接透传 `entry.value`）也有。修法两条都要：`data?.matrix ?? {}` + 给路由层加一层 ErrorBoundary（后者是全站兜底，优先）。

### A261 — 合规报告的 `anchoring` 后端可空、前端当非空用，消费点无一保护（high，两边定义我都读了）

```
app/schemas/compliance.py:60        anchoring: AnchoringDisclosure | None = None
cds-frontend/src/services/complianceApi.ts:25   anchoring: AnchoringDisclosure;        ← 不可空
cds-frontend/src/pages/ComplianceDisclosure.tsx:145  {report.anchoring.backend_label}
                                          :146  {report.anchoring.is_consortium_chain ? (   :151  {report.anchoring.backend}
```
后端把它定为可空是有意设计（`:36-38` 文档字符串：这是一个 honest-disclosure 字段，没配置时就是 `None`）。前端一旦遇到 `anchoring: null`（未配置锚定后端 / 版本不一致），`:145` 直接读 `null.backend_label` 抛 TypeError；叠 A244（全站零 ErrorBoundary）⇒ **合规披露页能把整个后台抓白屏**，而崩掉的恰好是「向后端自证后端是什么」那一页。修法：类型改 `AnchoringDisclosure | null`，消费处先判空并显式显示「未配置」（与 `chain_anchoring: str | None` 已有的 not_configured 口径一致）。

**同时记一笔工具限制的新形状**：ocr 给的坐标是 `pages/Compliance/ComplianceDisclosure.tsx:145-151`——**那个子目录不存在**（真身是 `pages/ComplianceDisclosure.tsx`），它引用的符号 `anchoring_disclosure` 在后端也**一个都没有**（`grep -rn anchoring_disclosure app/schemas app/api` 空）。但它引的**行号 145/146/151 逐字命中**。所以既不能拿它的坐标直接当事实，也不能拿它的错路径当驳回理由——结论只能靠自己那两行定义。（同批：B 区早先三条臆造选择器的判例见 F-63。）

### A284 — OPA 在宿主上无认证可写策略 = 授权决策可被任意改写；但“启动时空策略”不是 fail-open（high，半真）

`docker-compose.prod.yml:64-72`：`opa` 映射 `8181:8181`，`command: run --server --addr=0.0.0.0:8181`，**没有任何认证参数**，且 `./policies` 可写挂载。能访问宿主 8181 的人可以直接 `PUT /v1/policies/...` 写一份 `default allow = true`，而 `app/services/policy_evaluator.py:135-150` 对 `result.get("result")` 为真就直接 ALLOW ⇒ 策略引擎形同虚设；`CDS_OPA_URL: http://opa:8181` 也是明文。
但 ocr 说的“命令里没有位置参数 ⇒ 策略集为空 ⇒ 全部命中无结果分支”这一推论**方呭不对**：（1）策略是运行时按合约 push 的（`policy_evaluator.py:116 opa_client.push_policy(...)`），所以不靠位置参数加载；（2）无策略时 OPA 返 `{}`，`.get("result", False)` ⇒ DENY；（3）OPA 整个不可用时 `:87-93` 回落到 `_evaluate_python`（也是拒绝优先）。⇒ 定级从“安全机制未加载（critical）”降为“**宿主暴露未认证写接口**（high）”，另记一条与它无关的真缺陷：`:116` 注释写“Push policy if not already in OPA”，但 `push_policy` 之前**没有任何缓存判断**，每一次访问评估都会重推一遍 rego（`opa_client.py:30-56` 每次都 PUT 并记 INFO 日志）——注释与实现不符 + 每请求一次多余写。

### A285 — `CDS_EGRESS_AUDIT_LOG_PATH` 与 `CDS_SHARED_VOLUME_ROOT` 在 `/data` 下，却没入卷，与本文件自己的注释相矛盾（high）

`docker-compose.prod.yml:181/:183` 写 `CDS_SHARED_VOLUME_ROOT: /data/shared-volumes`、`CDS_EGRESS_AUDIT_LOG_PATH: /data/egress-audit/access.jsonl`（并且 `:157 CDS_EGRESS_AUDIT_ENABLED: "true"`、`:182 CDS_EGRESS_AUDIT_ENCRYPTION_ENABLED: "true"`）；而 `:233-239` 的 `volumes:` 只有 `sandbox-workspace:/tmp/cds-sandbox`、`sandbox-workspace-l0:/tmp/cds-sandbox-l0`、`cds-storage:/tmp/cds-storage`，顶上 `:254-262` 的全局 `volumes:` 也没有任何 `/data` 条目。最锐的是它自己的注释（`:234-236`）：“两处写入都必须入卷……否则容器重建后库里 ready 指向空盘”——结论对，但**盖列的两处是 `/tmp/*`，真正会丢的是 `/data/*`**。后果：容器的可写层携带 **egress 审计日志（合规产物）** 与共享卷数据，`docker compose down/up` 就没了。修法：给 `/data/egress-audit` 与 `/data/shared-volumes` 各加一个命名卷（或把两个路径改到已入卷的前缀下）。

### A287 — 遗留管理台三个主要写操作全部接错契约（high，功能够不着而不是风格）

1. **“恢复”调的是挂起接口**：`app/templates/connectors.html:61` 只有一颗按钮 `onclick="suspendConnector('${c.id}')"`，文案按状态切 `恢复/暂停`；`suspendConnector` 无条件 `API.post('/api/v1/connectors/${id}/suspend', {})`。后端另有独立的重新激活路由（`app/api/connectors.py:274 """Reactivate a suspended connector."""`，且 `:280` 会拒绝非 suspended 状态）⇒ 对已挂起的连接器点“恢复”只会再打一次 suspend，**控制台上永远恢复不了**。
2. **销毁密钥必然 422**：`app/api/kms.py:194-199 DELETE /keys/{key_id}` 声明了 `body: HighRiskOperationRequest = Body(...)`，而 `app/static/js/admin.js` 的 `del()` 不发请求体 ⇒ 高危操作永远进不了后端。
3. **生成 DEK 必然 422**：`app/api/kms.py:64-68 async def create_dek(product_id: uuid.UUID, ...)` —— 非 Body 的标量参数在 FastAPI 里按 **query** 解析，而模板把 `product_id` 放进 JSON body ⇒ 缺 query 参数直接 422（还多一重：UUID 类型校验）。
这三条是 A241/A242 “遗留管理台整类”判定里最硬的部分（那两条是类级收敛，不逐条列功能缺陷），单独抽出来是因为它们不是“写法不好看”，而是**三个主写操作在生产里 100% 不可用**。

### A294 — 输出审查的脱敏披露在跳源部署下会静默变成“已通过”（high，碰项目铁律 honest degradation）

后端只发两种值：`app/api/sandbox_sessions.py:1956 _header = f"passed; findings={len(_findings)}"` 与 `:1966 _header = f"redacted; findings={…}"`（critical 发现走 `:1949` 的 409 不放出；`:2052/:2062` 是另一条下载通道的同构逻辑）。
前端 `services/sandboxApi.ts:374-378` 在 `transformResponse` 里取 `headers['x-cds-output-review']` 交给 `:275-280 parseReviewHeader`。那个函数本身不算错（`:276` 对缺失/非字符串返回 `null`，`:277` 才是 `header.trim().startsWith('redacted') ? 'redacted' : 'passed'`）——**错在消费端把 `null` 当“没事”**：`pages/Flow/NotebookWorkbench.tsx:430` 与 `pages/Sandbox/SessionDetail.tsx:525` 都只写 `if (review?.status === 'redacted') messageApi.warning(…)`，全仓没有任何地方渲染“未拿到披露”这个第三态（注意 `SessionDetail.tsx:217` 自己定义了 `passed | redacted | blocked` 三态，下载链路却只用两态）。
而 `app/core/security.py:204-210` 的 `CORSMiddleware` 只配了 `allow_origins/allow_methods/allow_headers`，**没有 `expose_headers`** ⇒ 只要前后端不同源，浏览器就不会把 `X-CDS-Output-Review` 交给 JS（`headers` 里根本没这个键）⇒ `parseReviewHeader(null)` 返回 `null` ⇒ 两处消费都不报警。后果：**内容已被后端脱敏（`:1966` 把 content 换成了 `redacted_output`），界面一声不响地当成“审查通过”**，正好踩在项目铁律（模拟/降级必须披露）上。同源的部署（`vite.config.ts:14-20` 的 dev proxy、nginx 同域）看不出来，所以这是一个“测试环境永绿、换拓扑就静默”的坑。
修法要两端各一半：（1）后端 `expose_headers=["X-CDS-Output-Review", "Content-Disposition"]`；（2）前端把 `review === null` 当成**要显式告知的第三态**（“未能读取审查披露”），不能拿“没报警”当“安全”。

### A301 — `deploy/e2e-remote/remote_deploy.sh` 是 A283 的完整版，且 app 侧兜底救不了（high）

该脚本 L11-13 自己维护了一份 fail-fast 变量白名单，但**清单漏了** `CDS_COLUMN_ENCRYPTION_KEY` 与 `CDS_MFA_SECRET_ENCRYPTION_KEY`（两者以 `${VAR:-}` 形式传入，见 `:124`、`:148`）；更重的是 `:122` 传 `-e CDS_DEBUG="${CDS_DEBUG:-true}"` —— 而 `app/core/config.py::validate_security_config` 的十五道生产门止的是 `DEBUG=false` 分支，`CDS_DEBUG=true` 直接绕过。⇒ 与 `docker-compose.prod.yml` 不司：compose 那份现已 `${VAR:?}` 收口，**而远程 e2e 车道仍会带着空列 DEK / 空 MFA key 真的启动**。（本轮未改：需要跑一次真实远程车道才能验收，本机只能证明“变量会为空”，证不了“车道上真为空”。）

### A302 — `k8s/cds-app.yaml` 一个卷都没有，且 `replicas: 2`（high，五份副本里 A285 类最严重）

全文 `volumeMounts|volumes:` 命中数为 **0**：API Deployment 连 `/tmp/cds-sandbox` 这个真工作区都在容器可写层，且两副本各写各的（会话工作区不共享）。helm 侧同构但略好：`values.yaml:73,74,104` 声明了三条路径，`templates/deployment-api.yaml:103-110` 只挂 `/tmp/cds-sandbox`，`templates/pvc.yaml` 只有一个 `-sandbox` PVC ⇒ 出口审计/共享卷/Cube 结果全为临时。（本轮未改：k8s/helm 应用需集群，本机不可验。）

### A314 — `CreateContractRequest` 缺 `purpose`/`purpose_scope` ⇒ Gap A4 目的门对 UI 创建的合约永远空转（high）

`contractApi.ts:6-19` 无这两字段，后端 `sandbox_tasks.py:48-53` 靠它们做目的门 ⇒ 界面建的合约全部无 purpose，门不可达。与 A110（payload 分隔符注入）同一条链。

### A315 — 清空「允许操作」会静默恢复后端默认 `read,analyze`（high）

`contractApi.ts:12-14` + `ContractCreate.tsx:105,228-235`：空数组不发 ⇒ 后端用默认值 ⇒ 用户意图「全部禁止」变成「可读可分析」（授权只应显式，不应默认）。

### A320 — `certificate_service.verify_chain` 对 tz-naive `not_after` 直接比较 ⇒ 有真实行时 500（high）

来源：密钥簇子代理 `st_01a14f57` 在跑 A13 红门时当场撞出（`app/services/certificate_service.py:492`）：SQLite 回来的 `not_after` 是 tz-naive，直接与 aware datetime 比较抛 `TypeError` ⇒ `GET /{cert_id}/verify-chain` **不是「验了个假链」而是 500**（PG 回 aware 时不报，所以远程车道不一定能重现）。已派 `st_01a14f77` 接管该文件（时区归一 + 用例）。


## 7. Medium-High（40 条）

### A18 — `app/api/monitoring.py:275,503` — 同一个 AsyncSession 上 `asyncio.gather`（medium-high）

`await asyncio.gather(db.execute(active_q), db.execute(contracts_q), ... 7 条)`。SQLAlchemy 的 AsyncSession/AsyncConnection 不是并发可重入的，同一时刻只允许一个活动操作 ⇒ 仪表盘端点会间歇 `InvalidRequestError`/greenlet 切换错乱。L503 同形（3 条）。修法：顺序 await（都是 count）或每查询独立 session。

### A34 — `sandbox_sessions.py:1345-1353` + `1483` × `database.py:35-44` — 设想的正常 408 路径会把本次已写状态与审计一起回滚（medium-high）

`get_db` 的真实语义（逐字）：`yield session` → `await session.commit()` / `except Exception: await session.rollback(); raise`。执行端点在超时分枝里：
```python
if exec_timed_out:
    session.status = SessionStatus.FAILED.value
    session.error_message = "Execution timed out"
    await db.flush()          # L1349-1353
```
然后在 L1482-1486 `raise RequestTimeout(...)`（注释写明这是 W2 设计上正常的 408）⇒ **`flush` 被 `rollback` 抵消**，会话依旧 RUNNING，空闲回收看不到 FAILED。同一请求里 `sandbox.output_inspected` 审计（L1457-1477，虽然外层包了 `try/except` 不报错，但写的仍是**本请求事务**）一并丢失——而注释自己说「the operation may still have finished in the sandbox」，即一次可能已真实完成的执行可以没有输出审计留痕。`audit_service.log_standalone` 的存在就是为了这个场景。

### A35 — `_l2c_file_op` 四个 op 对 exec 结果的处理不一致（medium-high）

逐 op 盘上对照（同一段代码里）：
- `download`：唯一检查了结果的 op（L1700-1701 `if result.get("exit_code") != 0 or "error" in result: 502`）。但 L1703-1706：
  ```python
  out = (result.get("output") or "").strip()
  if out.startswith("CDS404"): 404
  return {"data": _b64.b64decode(out.splitlines()[-1])}
  ```
  0 字节文件时 VM 打印的是空行 ⇒ `out == ""` ⇒ 不是 CDS404 ⇒ `"".splitlines()[-1]` 抛 **IndexError → 500**；输出混入诊断行时 `b64decode` 抛 **binascii.Error: Incorrect padding**。两个表达式已复算：`REPRO: IndexError list index out of range` / `REPRO2: Error Incorrect padding`（机制确认，未端到端跑——l2c 数据面不在本机）。
- `upload`（L1683-1691）：`result = await adapter.execute(...)` 后**仍未看 exit_code/error**，VM 内 `os.makedirs`/`open().write()` 失败（目录不可写、磁盘满、python 异常）仍返回 200 并写 `sandbox.file_upload` 审计。
- `list`（L1707-1724）：不查结果，且循环里 `if not line.strip() or "\t" not in line: continue` ⇒ python traceback 会被逐行吐掉，最终返回 `{"files": []}` —— 「空目录」与「采集失败」不可区分（静默同义，踩 honest-degradation 铁律）。
- `delete`（L1725-1731）：只看 `exit_code == 3`，其余非零（含 exec 通道整体失败）一律返回 `{"deleted": name}`。

### A36 — `sandbox_sessions.py:1125-1138` — 策略更新先拆后建且无补偿（medium-high，A20 同族但发生在活跃会话上）

`PATCH` 网络策略的收尾：`await network_policy_engine.remove_policy(...)`（L1126）**无条件先拆掉现有强制**，然后才 `if net_policy.active: create_policy(...)`（L1136-1138）。`create_policy` 抛异常（或后面 `db.flush()`/`get_db` 提交失败）⇒ 异常冒泡成 500 + DB 回滚 ⇒ **库里仍是旧的 active 策略，运行面已经没有任何策略**（deny_all 不再存在，egress 可能默认放行），而调用方收到 500 会理解为「没改成」。与 A20 区别：这里是已活跃会话的策略变更，操作者会相信旧策略仍生效。

### A37 — `sandbox_sessions.py:1870/1883` — 会话文件上传无字节上限（medium-high）

`await file.read()` 全量缓冲进 API 进程内存，**本文件内没有任何单文件字节上限**：全仓只有 `data_resources.py:20 MAX_UPLOAD_SIZE = 500MB`（那是另一个端点，且同样是在 `content` 读完之后才判，L151），会话侧只有 `session_files.py:212` 的**个数**上限（`SESSION_MAX_FILES=200`）与 l2c 分支的 256KB exec 通道上限（**也在读完之后才判**，且只覆盖 l2c）。⇒ 非 l2c 会话上传任意大文件可先完整缓冲在内存，少量并发即 OOM。

至此 `sandbox_sessions.py` 的 18 条 high/critical **已逐条过完**（5 条 critical = 同一个 A30；其余归入 A31-A37 与 B 降级）。

### A39 — `app/core/redis.py:26-45` — 失败清理路径本身会报错 ⇒ 坏客户端被永久钉住（medium-high）

`except Exception:` 里第一句是 `await _redis_client.aclose()`。`aclose()` 在「Redis 不可达」场景下自己就可能抛（socket 超时 / 对端重置）⇒ 后面的 `_redis_client = None` 与 `_last_connect_failure = time.monotonic()` 都不会执行，于是：全局仍指向半初始化的客户端，后续所有 `get_redis()` 在 `if _redis_client is not None: return _redis_client` 处**直接拿到它**，30s 冷却期与重连逻辑被永久绕过；同时原始异常被 `aclose` 的异常替掉。同理 `if _redis_client is not None` 与 `await ping()` 之间存在窗口：**ping 失败前全局已经指着一个未验证的客户端**，并发请求会拿到一个随后被置 None 的连接对象。

### A45 — `app/core/security.py:65-82, 132-146` — 未验签的 JWT `sub` 单独成桶，命中就**不再查 IP 桶**（medium-high）

`_resolve_scope` 只返回**一个** scope：先钉住 login/register/login-cert 到 `auth`（IP，CR-2026-10-15 M1 修的），否则 `_unverified_jwt_sub()`（L26 注释自述「without verification」）只要解出 `sub` 就 `return "user", sub, limits["user"]`。⇒ 只要带一个自造 `Authorization: Bearer <b64 带 sub>`，就能每请求换一个全新的 `user:<sub>` 桶，**`ip` 桶完全不参与**。实际可利用面（已按仓内事实收敛）：login/register 在 sub 判断**之前**就被钉到 auth 桶 ⇒ 凭证撞库仍受限；但本仓有 5 个路由器不要求登录（health/metrics/admin/session_stream/cube_egress），在这些**未鉴权但敏感**的面上每请求一个假 sub = 完全无限速。修法：user 桶之外始终叠加一个 IP 桶；或只用验签后的 sub。

### A52 — `app/api/dev_sandbox.py:163-170` — 同步「对象存储读 + 解密 + 落盘」直跑在事件循环上（medium-high）

```python
decrypted = storage_service.download_decrypted(resource.storage_path)   # 同步
...
data_file.write_bytes(decrypted)                                        # 同步磁盘写
```
整段在 `async def create_dev_session` 里直接调用，**无 `asyncio.to_thread`**（对比同仓 `sandbox_sessions.py:507` 就是 `await asyncio.to_thread(runtime.provision, …)`）⇒ 一个大会话创建请求会把整个 worker 的事件循环停在「下载+解密」上，同 worker 的所有并发请求（含心跳/健检）全部卡住。外层有 try/except（L171-172 → 500），所以只是阻塞不是崩。

### A56 — `sandbox_db.py:18/70/134` × `secure_duckdb.py:992-1005` — TTL 只清 `_engines`，`_engine_owners` 没人清（medium-high）

```python
# sandbox_db.py:134（只挂在 /query 上）
cleanup_expired_engines(_engines)
# secure_duckdb.py:1001-1004
for sid in expired:
    engines[sid].close()
    del engines[sid]              # ← 不会动并行的 _engine_owners
```
三条已逐字核对的后果：
1. **`_engine_owners` 无界增长**：只有 `close_session`（L201）会 pop。叠加 A49 的 `default_factory=lambda: str(uuid.uuid4())[:8]` ⇒ 每个不传 `session_id` 的创建请求都往两个字典里各新增一个键，增长速率由**请求量**而非会话数决定。
2. **懒清理只存在于 `/query`**（全仓 grep `cleanup_expired_engines` 只有这一个调用点）⇒ 只建表/导入/建脱敏视图而从不查询的会话，其 DuckDB 引擎（携明文表与 `_table_deks`）永不回收。
3. 修正 ocr 一句：TTL 后「合法用户无法重连、持续 403」**不成立**——`_authorize_session_engine`（L43-47）先查 DB 会话行且 `session.user_id == current_user.id` 就放行；即使走到 `_engine_owners`，同 owner 也是放行，只有**另一个**用户会被 403（那是正确行为）。所以本条只是内存/驻留问题，不是可用性缺陷。

### A62 — 信任门三处可绕过（medium-high，同一主题归一）

- `expires_at` **从不参与判定**：grep `expires_at` 在连接器只有 `:99`（字段）、`:335`（读库）、`:366`（写库）三处，send_request 前的门只有 `:538` status / `:547` allowed_operations / `:565` 分数。且 `EstablishTrustRequest` 根本没有 `expires_at` 字段 ⇒ 通过 API 建的信任永远不过期。
- `min_score = self._min_trust_by_op.get(operation, 0)`（`:565`）—— 陌生操作名阈值为 **0**，而操作名由创建者自填 ⇒ 把权限叫 `"exec"` / `"read_all"` 就完全跳过分数门（fail-open）。
- `federation.py:153-158` 对 `certificate` / `public_key` 只要非空就 `security += 8 / += 4`，而这两个字段是未验签的自由文本，直接存进 `SpaceIdentity`，从无解析/验链 ⇒ 一个指向攻击者主机的 basic 信任可以被记成「有凭证」的高分。

### A63 — `federation_connector.py:355` × `:327` — 重启后 `trust_id` 不再解析，**吊销开关静默 404**（medium-high）

创建时 `trust_id` 派生成 `uuid.UUID(trust.trust_id.replace("trust-","")[:32].ljust(32,"0"))`（12 位 hex + 20 个 0 补齐），重启从库加载却写成 `trust_id=str(row.id)`（带连字符的完整 UUID）。⇒ `POST /trust` 返回的那个 id 在重启后 `POST /trusts/{id}/revoke` 直接 404（`federation.py:193`），而信任仍然 `status=active` 且 `POST /request` 可用 —— 对一个「随时可掉断的外部空间」来说是失效的 kill switch。修法：把不透明 `trust_id` 当列存，不要从它反推 UUID。

### A92 — 口令哈希在事件循环里同步跑 bcrypt（medium-high，登录主路径）

```
app/services/auth_service.py:30  bcrypt.hashpw(_truncate(password), bcrypt.gensalt(rounds=12))     # 同步，rounds=12 ≈ 200-300ms CPU
app/services/auth_service.py:115/124  async def register_user(...) -> hash_password(password)
app/services/auth_service.py:137  verify_password(...)   # 登录路径，同样同步
```
全仓 `hash_password(` / `verify_password(` 除定义外只命中 `:124`/`:137` 两处，都在 `async def` 里 ⇒ 每次注册/登录独占事件循环线程 200-300ms，同一 worker 上所有请求（含 WebSocket 流）在此期间停止。与 A52/A61/A26 同族，但落在最热的 auth 路径。修法：`await asyncio.to_thread(verify_password, ...)`（仓里已有同型写法，如 `cube_template_service.py:523/718`）。

### A98 — 白名单含 io/pathlib/pyarrow/duckdb/sqlalchemy，“白名单即闸门”的自述不成立（medium-high）

`ALLOWED_IMPORTS:65-90` 含 `io`、`pathlib`、`pyarrow`、`duckdb`、`sqlalchemy`、`PIL`、`cv2` ⇒ 不调 `open()` 也能读写文件与访问外部数据源；文件头 `:62` “whitelist IS the gate” 的自述不成立。**这不是沙箱逃逸**：真边界是 bwrap/Cube 的挂载集与 exec 通道注入，扫描器只是纵深防御；不得强判。

### A102 — DP-SGD 噪声尺度少除 batch_size，且隐私/梯度字段自“配置”而非“实际”（medium-high）

```
app/services/cpu_trainer.py:292   outputs = model(...); loss = ...      # HF loss 已是 batch 均值
app/services/cpu_trainer.py:566-568  noise_scale = config.dp_noise_multiplier * config.dp_max_grad_norm
app/services/cpu_trainer.py:578   p.grad.data.add_(noise)
app/services/cpu_trainer.py:230   gradient_norm=0.0,  # Last step norm not tracked in aggregate
app/services/cpu_trainer.py:233   dp_noise_injected=cfg.dp_enabled,
```
DP-SGD 的标准尺度是 `σC/B`（因为求的是均值梯度），这里直接 `σC` ⇒ 实际注入噪声比声明的 (ε,δ) 对应值大一个 batch_size 倍；同时 `dp_noise_injected` 是配置回声而不是“确实加了噪”，`gradient_norm` 硬编码 0.0。**与 A101 叠加**：`use_lora=True` 时所有 `p.grad is None`，结果是“已注入 DP 噪声、0 梯幅、训练完成”，而真实情况是零噪声零更新。修法：`noise_scale = σC/B`，`dp_noise_injected` 由 `_clip_and_noise` 返回的实际标志决定，至少回传最后一次 `total_norm`。

### A119 — `except TypeError` 后退回**不带 filter** 的 extractall（medium-high）

`session_snapshots.py:154-157`：`tar.extractall(..., filter="data")` 失败时直接裸解。生产镜像是 py3.12（`filter` 可用），所以这条路径只会在解包过程中抛 TypeError 时进入——而它恰好在“已半解包”的状态上再裸解一遍，等于在异常路径上关闭 `../`/绝对路径/symlink 防护。修法：启动时探测 `filter` 可用性；不可用时先遍历 `getmembers()` 校验未逃逸再解。

### A135 — `ET.parse` 直接解用户可控的 XML，无实体膨胀防护（medium-high，活路径）

`app/services/data_processing.py:211-214  import xml.etree.ElementTree as ET; tree = ET.parse(path)`，`_process_xml` 的 `path` 来自待处理文件（上传/沙箱产出），`process` 只校存在性不校内容与大小。可达性：AST 闭包 `main.py reaches it? : True`（经 `app/api/dev_sandbox.py:19 → data_product_sandbox:20 → data_processing`）。定级修正：ocr 把它叫 XXE——**不成立**，Python 的 expat 默认不取外部实体，没有外部数据外带面；但 billion-laughs / quadratic blowup 这类实体膨胀 DoS 仍成立（`ET` 无防护），一个几 KB 的文件就能打满 CPU/内存。修法：换 `defusedxml`（需加依赖）或 `ET.XMLParser(target=ET.TreeBuilder())` + 拒绝含 `<!DOCTYPE`/`<!ENTITY` 的输入，并对 XML 输入加尺寸上限。

### A140 — `_ensure_kek` 无锁 check-then-act，软件 HSM 会用同一个 id 覆写主密钥（medium-high）

`kms_service.py:45-56  if self._kek_ready: return` → `self._hsm.generate_kek(_KEK_ID)`，没有任何锁；软件 HSM 的 `generate_kek` 是 `self._keys[key_id] = os.urandom(32)`（同 id 直接覆写）。`generate_kek` 既在事件循环线程跑也在 `asyncio.to_thread` 工作线程跑（`api/sandbox_sessions.py:507 → sandbox_runtime.provision → :945`）⇒ 启动初期两个并发密钥操作可能各自 mint 一次 KEK，第二次覆写后第一批 wrap 的 DEK 永远解不开。修法：`threading.Lock` + “存在即复用”（先查后建，建失败再查），软件后端还应拒绝覆写已有 id。

### A148 — `_sm3_fallback_allowed()` 在任何异常时返回 True，把生产诚实门翻成 fail-open（medium-high）

`app/services/merkle_service.py:27-33`：该函数的职责就是尊重 `CDS_SM3_FALLBACK_ALLOWED`（PZ-04/MR-A14 的生产硬约束），但 `try: return bool(get_settings().SM3_FALLBACK_ALLOWED) / except Exception: return True`。一旦 `get_settings()` 报错（运行时配置被改坏、Settings 校验异常），审计 Merkle 叶子的哈希就静默退回 SHA-256，而调用方 `:45 if not _sm3_fallback_allowed(): raise` 本应拒绝服务。同一个文件其余四条均潜伏：`[3]` 验证明自证（root 来自证明自身）、`[4]` 空输入通过、`[5]` 死回退分支——因为 `generate_proof`/`verify` 全仓无调用方（台账第 633 行已先一步记过 `generate_proof` 无接线）。修法：这个 helper 应 `except Exception: return False`（宁可不签也不假装 SM3），并改成模块启动时读一次。

### A151 — `can_access()` 对不认识的信任等级放行（medium-high，潜伏；原先叠加的第二半已实测推翻）

`app/services/trust_evaluator.py:203-209`：`return level_order.get(trust_level, 0) >= level_order.get(required_level, 0)` ⇒ “不认识”就是 0 级，缺失/拼错的 `required_level` 等于**不要求**（与同文件 `evaluate()` 的保守默认相反）；修法同上：`try: TrustLevel(x) / except: 拒`。
**我自己给这条加的“第二半”是错的，已实测推翻**：原写“`TrustLevel(str, Enum)` 的 `__hash__` 基于成员名，所以传 `"platinum"` 字符串会两次 miss 而 `0>=0` 放行”。用门 python 3.12 实跑：`hash(TrustLevel.GOLD) == hash("gold")` → **True**，`{TrustLevel.GOLD: 2}.get("gold")` → **2**（`str.__hash__` 在 MRO 里排在 `Enum.__hash__` 前面）⇒ 字符串入参能正常命中，“字符串绕过等级校验”不存在，本条只剩上面那一半。潜伏依据：全仓 `can_access` 只命中定义行 `:189` 与 `tests/test_trust_evaluator.py:175-185`，活的联邦链走 `evaluate()` + 分数比较（`federation_connector.py:559-565`）。修法：`try: TrustLevel(x) / except: 拒`（先归一再比较）+ 默认 fail-closed。

### A157 — agent `http` 工具的会话出网白名单只管 hostname，端口/方法/超时不收敛（medium-high，活路径）

```
app/services/agent_service.py:96-119  _net_allowed(url)：只看 urlparse(url).hostname，与 _AGENT_HTTP_ALLOW["domains"]/["ips"] 比对
app/services/agent_service.py:191-203 _tool_http：`if not _net_allowed(url): raise` → urllib.request.urlopen(Request(url, method=method))
:196  method = str(args.get("method", "GET")).upper()      # 描述写 GET/POST，实际 DELETE/PUT 原样发出
:197  timeout = int(args.get("timeout", 10) or 10)          # 10**9 也照用
:102  pattern = pattern.strip().lower()                    # 不 rstrip(".")；而 network_policy 的 DNS 代理做 `d.lower().rstrip(".")`
:117-119 except ValueError: continue                        # 写错的 CIDR 静默丢弃，无日志
```
门禁确实在（`_tool_http:193` 不过就 `raise`，`file://` 因 `hostname` 为空也被挡），但它的语义是“**这台主机的任意端口**”：`http://allowed-host:6379/` 直接通过，等于用域名白名单换了一个到内网任意服务的 TCP 出口（而 iptables 层因 A155 根本没执行）。同时白名单条目写成 FQDN 形式（`api.example.com.`，k8s/ resolv 里常见）就永不命中且无报错，与同仓另一套 DNS 匹配器的归一化不一致。（更正 ocr：它说“host 做了 `rstrip(".")`”，`:98` 并没有这句，真正的失配方向是“条目带尾点→永不匹配”。）修法：匹配器校 scheme ∈ {http,https} 且收敛端口集合；`method` 取白名单；`pattern`/host 两侧都做 `rstrip(".")`；CIDR 解析失败至少记 warning。

### A163 — `run_test` 无状态/环境前置，会把已发布的生产态工件打回 TESTING 并解绑测试证据（medium-high，活路径）

`app/services/workflow_service.py:130-143`：`run_test` 开头不查 `workload.status`/`env`，直接 `workload.status = TESTING` 且 `workload.test_run_id = None`；`app/api/workloads.py:155-163` 也只做了 `_load_workload`（所有权）就调。⇒ 对一个已 `PUBLISHED` 的生产工件调一次 `POST /workloads/{id}/test`，就把生产 head 改成 TESTING 并丢掉与已通过测试运行的绑定（后续 `publish`/`get_production_head`/依赖它的合约校验会拿不到引用），而 RETIRED 工件可以被拉回发布链。`publish()` 本身门禁完整（见 B 节）。修法：`run_test` 开头加 `if workload.env != TEST or workload.status not in (DRAFT, TEST_FAILED, TEST_PASSED): raise WorkloadGateError(...)`。

### A171 — `watermark_records.job_id` 无唯一约束，但两处用 `scalar_one_or_none()` 读 ⇒ 并发后永久 500（medium-high，活路径）

`app/models/watermark_record.py:19` 只建了普通索引 `Index("ix_watermark_records_job_id", "job_id")`，`:26 job_id: String(64) nullable=False` 无 `unique=True`（`alembic` 里也没有）；而 `app/api/training.py:819-822`（嵌入前查重）与 `:886-889`（验证/导出前取记录）都用 `scalar_one_or_none()`。两个并发的 `POST /training/{job_id}/watermark` 都能通过“已存在？”检查（检查与插入之间无锁、库层无约束）⇒ 落两行，之后该 job 的水印接口**永远** 500（`MultipleResultsFound` 没人接），而且修数据才能恢复。修法：加一条 additive revision 建 `UniqueConstraint(job_id)`（先清历史重复），或把两处改成 `.scalars().first()` + 应用层去重。

### A181 — `ref`（= 控制面返回的 `key_id`）未校验就拼进 URL 路径（medium-high）

`kms.go:120` `"/api/v1/kms/volume-keys/" + ref + "/distribute"`、`:132` DELETE 同理；`ref` 来自 `RegisterDEK` 对控制面响应的 `key_id`（`:110` 只校非空），随后原样写进 manifest 与 private_data。一个含 `..`/`/`/`?`/`#`/`%2f` 的值能把带**服务 bearer token** 的请求改写到控制面的其它路由（误撤销别的卷、命中写入型接口），而 `plugin.go:207` 的 `ref != m.DekRef` 防护无效——两边同源。修法：拿到 `key_id` 就按 `[A-Za-z0-9_-]{1,128}` 白名单校一次（不合法即 fail-closed），并用 `url.PathEscape` 拼接。

### A187 — 空位集 / 重复层名 / 零长度水印被当作“验证通过”（medium-high，活路径）

`app/services/model_watermark.py:200-210`：`len(extracted) != len(watermark_bits)` 在两边都为空时不触发，`:206 ratio = … if watermark_bits else 1.0` ⇒ `verify_watermark(state, [], md)` 得 `passed=True match_ratio=1.000`（子代理实跑）；`:124-126` 只校层名存在，不拒重复层名也不检测两个层名指向同一张量（重复位互相覆盖而报告“嵌入成功”）；`:67-92` 不校 `num_bits<=0`，`training.py:486-488` 只限了上限。三者共同给出一个“有效的”空水印结论。修法：入口拒绝空 `layer_names`/`num_bits<=0`/重复层名，`verify` 对空位集直接返回 `passed=False`。

### A190 — 重构检测按**位置**拼元组比较，改列序 / 改数值字面量即零成本绕过（medium-high，活路径）

`app/services/output_inspection.py:391 source_set.add(tuple(str(v) for v in row.values()))` 与 `:397-398`：按 `dict` 插入顺序取值，不按列名对齐。子代理实测（`source_rows=[{"a":1,"b":2.0,"c":"007"}]*10`）：同序同行 → 拦；**只调列序** → `(True, 0.0)` 放行；`b:2` vs `2.0`、`c:"007"` vs `7` → 也放行；投影（少几列）必不相等。另：docstring `:376-377` 写“Compares output rows against source rows at field level”，实现是整行元组全等；真正的逐字段检测 `check_field_reconstruction`（`:404-433`，集交口径）**没被 `inspect` 主阶段调用**。这直接削弱成果闸唯一拦的那一半（参照行比对）。修法：按列名对齐（`frozenset((k, canon(v)))`）后算匹配率，数值走 `float()` 归一化、字符串 ID 不去前导零，并让主阶段真的用上逐字段函数。

### A198 — “原子写”用固定 `path + ".tmp"` 且不 `fsync`，叠加无每卷互斥⇒manifest 并发互踩、残留 0400 tmp 让 Attach 永久失败（medium-high，活路径，我逐行复验）

`cds-volume-plugin/layout.go:95-100 writeAtomic(){ tmp := path + ".tmp"; os.WriteFile(tmp, …); os.Rename(tmp, path) }`：临时名不含随机后缀 ⇒ 同一卷并发写（`server.go:165` 无任何序列化保证）会互踩两个写入者的 tmp；失败路径不清 tmp（layout #5）⇒ 残留一个 0400 的 `.tmp` 后，后续 `os.WriteFile` 因权限/属主失败，**该卷从此 Attach 不进去**；rename 前不 `fsync` ⇒ 掉电后可能是空/零长 manifest；`os.WriteFile` 无 `O_EXCL/O_NOFOLLOW`（同文件 #5）⇒ tmp 可被预先做的手。修法：`os.CreateTemp(dir, base+"*.tmp")` + 写完 `f.Sync()` + rename + 目录 `fsync`，上层加 per-volume 互斥。

### A208 — 步进器把边框/背景写死成常态值，又用 `!important` 掉掉 antd 的环 ⇒ 必填项校验失败时输入盒完全不变样（medium-high，活路径，我逐行亲验）

antd 的表单错误态只靠 `.ant-form-item-has-error` 染控件自己的边框，但本页把控件画在自己的 `div` 上，而 CSS 里**根没有错误态规则**：
- `pages/Flow/devTaskCreate.css:156-164` `.cds-mem-stepper { … border: 1px solid #d9d9d9; background: #fff; }` 是常态硬编码；全文件**只有一个状态规则** `:166 .cds-mem-stepper:focus-within`（蓝边 + 蓝环）。
- `:192-198` `.cds-mem-stepper .cds-mem-input.ant-input-number { border: 0; box-shadow: none !important; background: transparent; }` 把 antd 自带的 focus / error 环与 disabled 底色全部压掉（`!important` 还迫使后续想按状态覆盖的人再叠一层 `!important`）。
- `grep -n 'has-error|is-error|:has(' devTaskCreate.css` → **空**；TSX 侧 `DevTaskCreatePage.tsx:61 variant="borderless"` ⇒ 内层 InputNumber 自己也不画边框。
错误路径是活的：`:275` `debug_memory_gib` 与 `:289` `exec_memory_gib` 都带 `rules=[{ required: true }]`；`:124 showIssues()` 在 `:148`（本地）与 `:155 showIssues(describeNotebookCreateIssues(error))`（后端 422 回写）把字段错误写进 Form。⇒ 用户提交不合法内存时只剩一行红字，输入盒依旧白底灰边；disabled 同理（步骤按钮 `:188 .cds-mem-step:disabled` 变色，外盒不变）。修法：补 `.cds-dev-task-create .ant-form-item-has-error .cds-mem-stepper { border-color:#ff4d4f; box-shadow:0 0 0 2px rgba(255,77,79,.1); }` 与 `:has(.ant-input-number-disabled)` 灰底，并去掉 `:196` 的 `!important`。

### A212 — SDK 的 `_request_with_headers` 漏了 `>= 400` 抛错（medium-high，活路径，我亲验）

两条请求通道的错误处理不对称：
```
sdk/cds_sdk/client.py:228 def _request(...)        → :236 if resp.status_code >= 400: raise _parse_error(resp, path)
sdk/cds_sdk/client.py:245 def _request_with_headers(...) → 无此判断，:253 body = … resp.json() 后直接 return body, dict(resp.headers)
```
⇒ 走 `_request_with_headers` 的调用在 4xx/5xx 时**拿不到 `CDSApiError`**，而是拿到一个错误体被当成功数据返回的 `(body, headers)`；下游按正常结构读字段会得到 `None`/缺键而不是异常。**边界（如实）**：全文件只有一个调用点 `:328`，所以爆炸半径目前只那一个方法（需分页头信息的那一个），不是全站。

### A213 — SDK 重试不区分幂等性：对 POST 也重发（medium-high，活路径，我亲验）

`client.py:228-235`：`if resp.status_code in (429, 503) and retries > 0: time.sleep(min(_retry_after_seconds(resp), 5.0)); return self._request(method, path, _retries=retries - 1, **kwargs)`——`method` 不判别，也没有幂等键。429/503 只说明「没受理」，但网关/反代在**已落库后**才回 503 的场景很常见 ⇒ 重发 `POST /sessions`、`POST /deliveries`、`POST /api-keys` 会**重复创建资源**（而不是报错）。同一函数里 `_request_with_headers:248-250` 一摸一样。修法：重试集合只对 `GET/HEAD/DELETE/PUT` 生效，POST 要么不重试，要么带客户端生成的 `Idempotency-Key`。

### A226 — 三列定宽 360px 只在 ≥**1488** 成立，但断点写的是 `max-width: 1439px` ⇒ 1440–1487 整档横向溢出（medium-high，我亲验 + 算术，未跑真机）

文件本身（全部 86 行我读完了）：
```
deliveryOpenapi.css:60-63  .cds-api-create-form .cds-row3 { grid-template-columns: 360px 360px 360px; gap: 0 24px; }
deliveryOpenapi.css:57-59  .cds-api-create-form { padding-inline: 24px; }
deliveryOpenapi.css:65-69  @media (max-width: 1439px) → 2 列；:71-75 @media (max-width:1023px) → 1 列
```
外壳的数从仓里取（不是我猜的）：`index.css:186 .cds-content { padding: 23px 25px 23px 23px }`；`:192-196 .cds-content-inner { max-width: 1440px; padding: 16px 0 0 }`（不约束本例）；`:182-183` 的定标注释给出 **1440 视口下 clientWidth = 1403（滞动条 15px）**、左栏右边界 **x=219**。
⇒ 可用内容宽 = 1403 − 219（侧栏）− 48（content 左右内距）− 48（表单 `padding-inline`）= **1088**，而三列要 **3×360 + 2×24 = 1128**。**反推该像素布局成立的区间**：视口 ≥ 1128 + 48 + 48 + 219 + 15 = **1488**；断点却从 1440 才退档 ⇒ **1440–1487 这一档必溢出**（钻入视图按文件尾注不渲染页签卡，若外层还套一层卡身 24 内距，下限再抬 48px）。顶部注释声称旧写法「右缘 1427 越过内容盒 1415」已修，但新写法只是把越界从旧式 gutter 换成了定宽不够。
**证据口径（如实）**：这一步是静态算术 + 仓内定标注释，**我没在真机量**（本机 :3000/:8001 在跑，但量这一屏要登录+钻入路由）。修法两选一：断点抬到 1490，或定宽改 `repeat(3, minmax(0, 1fr))` 加 `min-width` 下限。按用户 2026-10-18 的自适应口径，这类定宽必须把成立区间写进注释。

### A228 — 详情弹窗挂在 portal 里，拿不到本屏令牌与色板 ⇒ 灰面板底色与标签配色整块丢失（medium-high，活路径，我逐行亲验；ocr 定 high，我下调一档）

同一类缺陷在 `notebookWorkbench.css`（A225）我判了潜伏，在这屏它是活的——区别就在“有没有真把子树送进 portal”：
```
auditLog.css:19-28  .cds-audit-screen, .cds-audit-modal-root { --audit-head-bg: #f7f9fc; --audit-tag-green: …; --audit-rule: … }
AuditLog.tsx:400    <Modal className="cds-detail-modal cds-audit-modal" …>   ← 无 wrapClassName / rootClassName
全仓 grep 'cds-audit-modal-root' → 只在 auditLog.css:20 出现过，TSX 里**从不挂这个类**
```
弹窗子树挂在 `document.body`，既不在 `.cds-audit-screen` 下也取不到 `.cds-audit-modal-root`，于是弹窗里凡是用到这些令牌的声明都整条失效：`:404-406 .cds-audit-modal .cds-audit-panel { background: var(--audit-head-bg) }` ⇒ 4-2 的 `#f7f9fc` 灰面板回到 transparent。**标签同理**：`t-green/t-purple/t-orange` 全被限定在 `.cds-audit-screen .cds-audit-records` 下（`:234/:240/:252`），而 `AuditLog.tsx:432` 给标题 Tag 同时上了 `cds-audit-modal-tag` 与 `auditTagClass(detail)`——那些 `t-*` 类在弹窗里**没有任何规则命中**，只剩 `:height:20px` 这类尺寸规则，最继落到 antd 默认描边样式。定 medium-high 而不是 high：丢的是配色与面板底色（1:1 真值层面很重），功能与文案不丢。修法：令牌上提 `:root`（或给弹窗 `wrapClassName="cds-audit-modal-root"`），并把 `t-*` 色板从列表作用域抽出来。`overflow: hidden` 与 `display: contents` 还会把无障碍树里的节点弄挂（见 A230），修的时候一并测。

### A245 — 诚实降级徽标的值解析把 `not_simulated` 判成「模拟」（medium-high，我亲验行号）

`components/Feedback/SimulationBadge.tsx:64-70`：
```
:66 const key = String(value).trim().toLowerCase();
:69 if (key.includes('sim')) return 'simulated';
:70 if (key.includes('not_') || key.includes('none') || key.includes('disabled')) return 'unsupported';
```
否定判断在 `includes('sim')` **之后**：`not_simulated` / `disable_sim` 这类值先被 `:69` 抢走 ⇒ 一个声明「没有模拟」的字段被贴上「模拟」。方向上它不会把模拟的说成真（不会高估能力），所以不升 critical，但它损害的正是本仓用来做诚实披露的唯一公用件（PZ-14 三态），错报就是披露失真。修法：先判否定/前缀边界，或改成枚举映射（旁边 `:40 DISCLOSURE_VALUE_STATES` 就是现成的白名单表，本来就该只用它）。

### A248 — 流式终端的两个独立生命周期缺陷：开连竞态与 `disabled` 后连接不关（medium-high，我逐行读过 effect）

`components/Sandbox/ExecStreamTerminal.tsx`：
```
:82  if (socketRef.current) { … }                    ← 并发守卫只看 ref
:89  const minted = await api.post('/auth/ws-ticket')  ← 中间隔一个 await
:99  const socket = new WebSocket(…)  :102 socketRef.current = socket
:162 useEffect(…) { … term 初始化 … }  :186 }, [closeSocket])   ← 依赖里没有 disabled
:179-182 return () => { closeSocket(); term.dispose(); }       ← 只在卸载/依赖变化时跑
:213 if (disabled) return <Alert … />                         ← 早期 return 把宿主 div 换掉
```
1. **开连竞态**：守卫在 `await` 前看 `socketRef.current`，而它要等 `:102` 才被赋值 ⇒ 双击回车（Input 在 `connecting` 态未禁用，`:82` 那个 guard 形同虚设）会拿到两张 ticket、开两条 WebSocket，第二条覆盖 ref 后**第一条再也关不掉**（ref 里已不是它），输出流也双写。
2. **`disabled` 后不断连**：会话从 running 变成终态 ⇒ `disabled` 变 true ⇒ `:213` 换渲染成 Alert，宿主 div 被卸；但 `:162` 那个 effect 依赖只有稳定的 `[closeSocket]`，**不会重跑也不会跑 cleanup** ⇒ xterm 实例与 WebSocket 都活着，往一个已经不存在的元素里写。本组件自己写的 `disabledReason` 是「只有会话所有者可在 running 状态使用」，而连接恰恰在离开 running 后继续开着——不只是泄漏，是策略与实现不一致。修法：effect 依赖加上 `disabled`（或把 socket 创建收进同一个受控 effect），`handleRun` 另用一个 `connectingRef` 做互斥。

### A250 — 卡片可以一边贴「已发布生产」一边提示「没有生产版本」（medium-high，口径我自己对过两个函数）

`pages/Flow/devTasksModel.ts`：阶段集 `publishedNotebookIds`（`:35-40`）只过 `status === Published && source_notebook_id`，**不过 `env`**；而生产执行入口用的 `publishedProductionByTask`（`:250-256`）过的是 `env !== 'production' || status !== Published` 就跳过，旁边 `:246` 注释还自述「与执行弹窗同口径」。同一份 `workloads` 喂给两个不同口径（`:309-310`），而阶段标签文案就是 `:26 published: { label: '已发布生产' }` ⇒ 只发到 staging 的 notebook 会被贴「已发布生产」，同时同一张卡的生产执行入口弹出 `NO_PRODUCTION_WORKLOAD_REASON`（`:231`「该合约没有 published 的生产版本，需先在负载中心发布」）——同一屏自相矛盾。修法：阶段改吃同一个生产集合，或把标签改成不断言 env 的「已发布」。

### A255 — 合约签名原文用 `|` 直接拼接且不转义不限长（medium-high，两边源码都对上了）

后端 `app/services/crypto_service.py:215-219`：
```python
base = f"CDS-SIGN|{contract_id}|{contract_no}|{party_role}|{timestamp}"
if purpose:            base += f"|purpose:{purpose}"
if purpose_scope:      base += f"|purpose_scope:{','.join(str(s) for s in purpose_scope)}"
return base.encode("utf-8")
```
前端 `utils/crypto.ts` 里的 `contractSignPayload` 逐字镜像这一段（`cds-frontend/AGENTS.md` 明确要求两者字节一致，所以这不是不一致，而是**一致地不一致**）。四个字段没有一个做转义或长度上限：`contract_no` / `purpose` / `purpose_scope` 元素只要含 `|`（或 scope 元素含 `,`），就能拼出与另一组字段完全相同的原文，例：`contract_no="X|purpose:Y"` 与 `purpose="Y"`。而这是**跨方**场景：合约号与用途出自对方提交的资料， SM2 签名就是双方的不可否认证据。修法：拼接前拒绝（或转义）分隔符，或换成 canonical JSON/长度前缀；两边必须同改（仓内已有逐字锁定机制可搭）。

### A262 — 互信关系缺 `created_at` 时用「现在」兑底 = 编一个审计时间（medium-high）

`services/federationApi.ts:59`：`created_at: item.created_at || new Date().toISOString()` —— 后端字段缺失/改名/返回结构变化时，UI 上会出现一个「看起来完全正常」的当前时间。这在其它模块可以是容错，在**跨空间互信记录**上不行：created_at 是证据的一部分，而仓内已经有明文口径反对它：`pages/Flow/devTasksModel.ts:314-315`「时间只在能解析时格式化；解析不了就原样透出，**编不了时间也不装「刚刚」**」，同一个文件旁边 `:55 remote_space_name || ''`、`:58 allowed_operations || []` 用的都是中性兑底。同一批里 A257（缺字段判通过）与 A224（「状态未知」与「已关闭」同色）与本条同根：**不知道的时候要显示“不知道”，不能显示一个好看的值**。修法：类型改 `created_at: string | null`，渲染层走 `formatDevTaskTime` 那一类不编造时间的函数。

### A265 — 审计 detail 的标量字段全量拼进工作台文本（medium-high，清单内容我读了）

`notebookWorkbenchModel.ts:239` 的 `NOISY_DETAIL_KEYS = {'_sm2_signature','signature','watermark','report_hash','released_output_hash','quote','attestation'}` —— 这九项全是**签名/水印/防存/证明类「噪音」字段，不是敏感字段清单**；`:255-257` 对其余一切标量直接 `parts.push(`${key}=${String(value)}`)`。也就是说 `command` / `sql` / `query` / `path` / `params` 这类字段只要出现在审计 detail 里，就原样拼成一行文本渲染到浏览器（本仓的输出管控只管会话产出，不管审计回显）。修法：改成**白名单**（只允许预定的几个展示字段）而不是黑名单；真要展示 `command` 就同时过一道与输出管控同源的值检查。

### A274 — `identityApi.ts` 同一个文件里三种解包写法，两种会错（medium-high，我把文件整片读完）

```
:144 listKeys      → Array.isArray(data) ? data : data.items || []      ← 正确（兼容两种形状）
:157 getKeyAudit   → ((res as { items?: … }).items || []).map(…)        ← 裸数组⇒items=undefined⇒静默空列表
:171 listCertificates → (res as CertificateResponse[]).map(…)            ← 包装对象⇒.map is not a function⇒抛
```
`:157` 是这批里最重的一条：它拿的是 **KMS 密钥审计（高危操作轨迹）**，后端一旦回裸数组（同仓其它端点的常见形状），UI 不报错、不提示，直接显示「没有审计记录」——按本仓铁律这是“把不知道说成没有”，而且是审计面的。`:171` 则是另一种错法：后端改成 `{items: […]}` 就抛 TypeError，叠 A244（零 ErrorBoundary）⇒ 证书页白屏。修法：把 `listKeys` 那个写法抽成 `unwrapList(res)` 共用（三个调用点，一个文件内就能闭环）。

同文件另外四条语义错位（low，都逐字验过）：`:125 algorithm: cert.cert_type === 'encryption' ? 'SM2 encryption' : 'SM2 signing'` ⇒ ca/tls/未知类型一律被标成签名证书；`:127 public_key: cert.cert_pem || ''` ⇒ 字段名叫公钥、装的却是整份证书 PEM（UI 按「公钥」展示/复制就会拿到错东西），而 `:190` 的 `cert.cert_pem || cert.public_key` 兑底又反过来掩盖了 `cert_pem` 缺失；`:129 created_at: cert.not_before` ⇒ 生效时间当创建时间，列表排序错位；`:110-113` 归一化里用嵌套三元推状态（项目规范禁，且 fail-open 已在 A257 单独记账）。ocr 还报了 `rotateKey/revokeKey` 直接返回 `any` 不校验、`reason` 不校空就发不可逆操作 —— 前者归入 A262/A257 同根的中性兑底改造，后者已有 `utils/highRiskOperation.tsx`（reason ≥ 8 字）在 UI 层拦，服务层不重复校。

### A288 — `cds-frontend/.dockerignore` 只排 4 项，`COPY . .` 会把本地秘钥烤进镜像（medium-high）

`.dockerignore` 全文只有 `node_modules` / `dist` / `.git` / `*.md` 四行；`Dockerfile:34 COPY . .` 紧跟其后。任何开发目录下存在的 `.env`、`.env.local`、`.npmrc`（npm 镜像的 auth token）、`*.local` 都会进 builder 层；nginx 镜像只 `COPY --from=builder /app/dist`，但 builder 层在 `docker build` 缓存/导出时仍然可达。修法：加 `.env*` / `*.local` / `.npmrc` / `coverage` / `.vitest`。
**同一段里证伪一条**：`Dockerfile:31` 的 `for i in 1 2 3; do npm ci … || rm -rf node_modules; done` 确实会吞掉循环退出码（第 3 次失败时最后执行的是 `rm -rf`，返回 0），但下一句 `&& test -x node_modules/.bin/tsc && test -x node_modules/.bin/vite` 正好接住（node_modules 已删 ⇒ test 失败 ⇒ RUN 失败），而且文件头三行注释写明了这个设计意图。所以它是**潜伏项**（删掉那行 `test -x` 就会变成真缺陷），不是现行缺陷。`npm run build` 之后也没有对 `dist` 内容做校验（`COPY --from=builder /app/dist` 对空目录同样成功）—— low/latent。

### A304 — SPA 文档无 CSP（nginx 侧与后端侧都只给了别的头）（medium-high）

`cds-frontend/nginx.conf:22-37`、`:100-103` 只加 `nosniff`/XFO/`X-XSS`/Referrer/Cache；CSP 仅给 `/api/jupyter/` 在 `:75`，`app/core/security.py:154-172` 也不覆盖 SPA 文档。⇒ 主页面无 CSP。（子代理读数：M-2≡M-3 已归一。）


## 8. Medium（97 条）

### A8 — `app/api/data_products.py:115` — 运算符优先级把显式入参吃掉（medium，一行可修）

`row_count=body.row_count or resource.row_count if resource else None` 实际解析为 `(body.row_count or resource.row_count) if resource else None` ⇒ ① 未关联 resource 时调用方显式传的 `row_count` 被丢弃落 NULL；② `or` 把合法的 `0`（空表）当「未提供」。修法：`body.row_count if body.row_count is not None else (resource.row_count if resource else None)`。

### A9 — `app/services/capability_matrix.py:352-354` — `l2c_planned` 恒为 False 的死逻辑（medium）

`all(_MATRIX[LEVEL_L2C][cap]["state"] == STATE_PLANNED for cap in CAPABILITIES)`：L2C 行里 `sync_exec=REAL`、`files_snapshot=REAL`、`stream_ws=UNSUPPORTED`（L202+ 逐格核过）⇒ 表达式**永远是 False**，而函数 docstring 说的是「l2c 仍有哪些能力在规划」。注释还写明 RT-06 / CB-04 仍是 planned ⇒ 应为 `any(...)`。消费方（`/health`）据此会把「L2' 尚未就绪」读成「已就绪」。

### A10 — `app/api/inference.py:49` × `app/services/inference_service.py:31` — 格式白名单与失效的 fail-closed 闸门（medium，含潜在 RCE 面）

- API 白名单 `pattern="^(onnx|pickle|safetensors)$"` 放行 pickle/safetensors；
- 服务里 `_EXECUTABLE_FORMATS = {"onnx"}` **全仓只有定义、无任何引用**（grep 确认），即模块注释承诺的「不可执行格式在注册期拒绝」从未接线；`register_model` 只校验 `fmt in MODEL_FORMATS`（L59），且只对 `fmt == "onnx"` 做内容校验（L66）；
- `_MODEL_FILENAME = "model.onnx"` 恒定（L33/270/327），runner 固定 `ort.InferenceSession` ⇒ 注册 pickle/safetensors 返回 201 + `registered`，但每次 invoke 必在沙箱内失败，且产物长期留在 registry、任务照计量。
- 额外口径：让上传接口接受 pickle 本身就是潜在反序列化 RCE 面（只要将来有任何路径真的 load 它）。

### A11 — `app/api/deliveries.py:865`（medium，性能+竞态，同族多处）

`taken = {e.slug for e in (await db.execute(select(ApiEndpoint))).scalars().all()}` —— slug 唯一性检查把整表 ORM 物化；且是 check-then-act，并发同名由唯一约束抛 IntegrityError 变 500 而不是 409。同族全表入内存：`data_products.py` 的合同关联检查、`audit.py` 的导出（365 天全量行 + 整份 CSV 文本，`StreamingResponse` 并未流式）。

### A16 — `app/api/users.py:21-27` — 全员名册对任意登录用户可读（medium）

`GET /users/options` 只 `require_human`（只挡 API key 主体），`ALLOWED_OPTION_ROLES` 含 ADMIN/REGULATOR，响应 `UserOptionResponse{id,username,role,organization}` ⇒ 任意登录用户可按角色拉全平台活跃用户的用户名+组织+角色（含管理员），为定向社工/找靶提供名册。

### A20 — `app/api/network_policy.py:69-104` — 唯一约束上的 check-then-insert + 引擎先于提交（medium）

`models/network_policy.py:24` `session_id ... unique=True`；L69-72 先查后插，并发时第二条在 flush 抛 IntegrityError 被 `get_db` 回滚成 500（而非 409）；L89 `db.add` 后 L104 就调 `network_policy_engine.create_policy(...)`，而事务要到请求尾才提交 ⇒ 提交失败时引擎里已存在一条数据库里不存在的网络策略（强制侧与记录侧分叉）。

### A25 — `app/api/session_stream.py:88-93` — 一次性票据消费非原子（medium，ocr 定 high 偏高）

`consumed = await redis.get(key)` 后 `await redis.delete(key)`：两条命令之间的窗口内，两个并发连接可双双通过 ⇒ single-use 语义可被竞态绕过。修法一行：`redis.getdel(key)`。影响面窄（需已泄露票据 + 30s TTL 内毫秒级并发），所以从 high 降为 medium。

### A27 — `app/core/cache.py` — 失败吐掉 + glob 未转义（medium，ocr 定 high 偏高）

`cache_set` 对非法 ttl / 不可 JSON 序列化对象招 `TypeError`，与 `redis` 错误一起被 `except Exception` 吐进日志且函数正常返回 ⇒ 调用方以为写成功（表现为“缓存永不命中”型隐故障）；`cache_clear(pattern)` 把 `pattern` 直拼进 `MATCH cache:{pattern}*`，`pattern=""` 等价于清空全缓存。当前仓内调用方均为内部固定前缀，**无用户输入直达**，故降为 medium。

### A44 — 合规与锚定的两个口径缺陷（medium）

- `app/api/compliance.py:38-41`：`end = datetime.now()`（naive 本地）直接作为查询区间下给 `DateTime(timezone=True)` 列。PG 下不会报错（参数按会话 TimeZone 解释），但报表窗口会整体平移一个时区偏移：TZ=Asia/Shanghai 时「最近 30 天」的监管报表**少了最近 8 小时**；`compliance_report.py:81 generated_at` 同样写入 naive 值。监管口径，建议统一 `datetime.now(timezone.utc)`。
- `app/models/chain_anchoring.py:25-29`：表注释（L3）承诺「one row per (anchor × anchored audit record)」，但 `__table_args__` 只有三枚普通查询索引，**无唯一约束**；写入侧 `app/services/blockchain_adapter.py:103-117`（`_persist_chain_record`）无任何前置存在性检查，且函数名自述「Never raises」，失败只 `logger.error` 就返回 ⇒ 锚定重试/重放可落重复行（污染重启后的 `confidence` 统计），而锚定行丢失只在日志里可见（叠加 A43：那条日志在生产里只剩裸消息）。

### A46 — `app/core/security.py:122-130` — `_local` 从不删空桶键（medium）

`self._local[bucket_key] = [t for t in ... if t > window_start]` 只清理桶内时间戳，**空键永不回收**，也无键数上限。叠上 A45 可控制造 `sub`（或 IP 漂移），在 Redis 不可用走本地回退时（就是这条路径存在的理由），键空间随伪造身份数线性增长且永不释放。`_local` 只在进程内，长期运行就是内存漏。

### A47 — `app/core/security.py:183-192` — 体积限制对 chunked 请求形同虚设（medium）

`content_length = request.headers.get("content-length")`，**没有这个头就什么都不拦**。⇒ `Transfer-Encoding: chunked` 的请求可以任意大，10MB 上限只约束“自己申报长度”的客户端（真正需边读边计）。至于 ocr 说的「`Content-Length: abc` 稳定 500」：uvicorn/h11 在协议层就会拒掉非法/超长 Content-Length（`int()` 在 h11 里先执行），打不到中间件，降为低。

### A55 — `app/api/dev_sandbox.py:93/178` + `data_product_sandbox.py:134-149` — 客户端自选 `sandbox_level`，合约条款不参与（medium）

`sandbox_level: str = "L3"` 是自由字符串，`_resolve_dev_contract`（L31-72）只校 合约存在/状态/双方/产品覆盖，**从不比对 `contract.allowed_sandbox_levels`**；值经 `DevSandboxConfig` 直透 `self.runtime.provision(level=config.sandbox_level)`。⇒ 合约可以写了隔离等级要求，开发沙箱这条入口上提需求方（`data_product.provider_id == current_user.id` 要隶成立才能挂数）仍可选一个更弱的可用运行面。未证实部分：非法字面值在 `get_adapter()` 里具体报什么错（因此不声称“500”）。

### A58 — `secure_duckdb.py:385-401` — `close()` 不清 `_tables` / `_table_deks`（medium）

`close()` 只关连接：`tmpfs` 模式会加密回写并 `secure_wipe_file` 清 WAL/sidecar（这部分卫生做得很好），但**内存模式（本文件用的就是 `mode="memory"`）下 `_tables` / `_table_deks` 仍挂在引擎对象上**，没有任何清零。字典侧已经 drop 引用，正常可被 GC；但我找到的额外引用就是 A48 那个 fire-and-forget 的 `_attach_policy(session_id, engine)` 闭包——它握着 `engine` 直到 task 结束。归入同一根因修：策略挂接改 await + close() 里显式释表与 DEK。

### A64 — 联邦整条链零审计（medium）

`grep audit_service` 在 `app/api/federation.py` **零命中**（我自己跑过）：`POST /trust` / `/revoke` / `/suspend` / `POST /request`（跳空间数据出境）全都不进审计漏斗，与 `app/api/AGENTS.md` 写的默认约定相冲。`GET /audit` 读的是进程内 `list`（`:285`，`:608` 追加），重启即空、跨 worker 不可见；而 `_persist_audit_entry` 写的 `FederationAuditRecord` 表没有任何端点读它。对一个拿 Merkle 审计链做卖点的产品的说，跳空间读取现在是未入链的。

### A65 — 默认拒绝对 key 的识别比路由器窄一个量级（medium，大小写不同的 `Bearer`）

- 谓词与中间件是**字节比较**：`scopes.py:344` `if not auth_header.startswith("Bearer cds_ak_"): return False`；`main.py:252-258` 原样取 `authorization` 字节（循环不 break ⇒ 重复头**取最后一个**）；`_KeyBlockedStatic` 同形（`main.py:394`）。
- 而认证入口**不区分大小写**。本机 `.venv-gate` 实测（fastapi 0.135.1 / starlette 0.52.1）：
  ```
  get_authorization_scheme_param('bearer cds_ak_abc') -> ('bearer', 'cds_ak_abc')
  HTTPBearer.__call__:  if scheme.lower() != "bearer":
  ```
  `deps.py:151` 再用 `credential.startswith("cds_ak_")` 进 key 路径。
- ⇒ `Authorization: bearer cds_ak_<valid>`（小写 b）**跳过默认拒绍**，落在一个既未映射、又接受 key 的面上：`main.py:371` jupyter 路由器挂载时**没有 `dependencies=`**，`jupyter_proxy.py:119-123` `POST /api/jupyter/tickets` 用 `get_business_user`，而 `deps.py:225-233` 明写 key subject 以**签发人身份**行动 ⇒ 零相关 scope 的 key 可铸 Jupyter 开发环工单。
- 边界（已查）：`docker-compose.prod.yml:133`、`helm/cds/values.yaml:47` 都不配 `CDS_JUPYTER_PROXY_BASE_URL`（`configured()` 假 ⇒ 503），开发/本机形态可达；`/admin` 另受 `validate_security_config` 生产拒绝（该 flag 未逐行复看）。
- 同族两条：重复 `Authorization` 头（中间件取最后 vs 路由层取第一个，后半句出自子代理阅读 starlette `datastructures.py`，**我未复看**）；`.strip()` 不对称（`deps.py:177` strip，中间件不 strip）。
- 修法：中间件与 `_KeyBlockedStatic` 共用一个 `is_key_credential(auth, api_key_header)`（scheme 小写比较 + 首个头优先 + strip）。

### A66 — `/api/v1/flow` 未进 GROUP_PREFIXES（medium，SS-15 机要面全量 403）

`main.py:351` 把 flow 挂在 `dependencies=[Depends(require_group("sessions"))]` 上，但 `grep flow app/core/scopes.py` **零命中** ⇒ `resolve_group` 返回 None ⇒ 所有 machine key 在 flow 面（SS-15，SDK v0.3.0 的 40 个操作）一律 403 `unmapped_prefix`，挂载的 scope 依赖成为不可达代码。旁边 `main.py:353-354` 的注释（quota 刻意挂在 `/api/v1/deliveries` 下，因为「未映射前缀会被 `resolve_group` 判为未映射」）证明这条规则当时是已知的，flow 是漏项。修法：往 `sessions` 组补 `"/api/v1/flow"`，或者（若设计上是 human-only）去掉挂载依赖并在 docstring 写明。

### A68 — `network_policy` 端口维度 fail-open（medium，API 可达）

- 模型 helper `network_policy.py:92-98`：allowlist 且空 ports ⇒ `return True`（同文件 ip/domain 空 ⇒ `return False`，**不对称确认存在**）；但这三个 helper **零调用方**（全仓 grep 只命中定义）。
- 活路径同形：`services/network_policy.py:731` `if config.allowed_ports and dest_port not in config.allowed_ports:` ⇒ 空列表等于不过滤（`:626` 逐端口加规则，空则不加）。
- 边界可达：`schemas/network_policy.py:20-25` `_validate_ports` 只校 1-65535，**空列表通过** ⇒ `PATCH {mode:"allowlist", allowed_ports: []}` 合法落库。
- ocr 的「mode 非法值退化成 allowlist」在 API 边界**证伪**：`schemas/network_policy.py:7/37-41/65-69` 两处 `field_validator` 锁 `VALID_MODES=("deny_all","allowlist")`，只剩非 API 写入者。

### A71 — `_sm3_fallback_allowed` 异常方向 fail-open（medium）

`app/utils/crypto.py:19-32`：`try: return bool(get_settings().SM3_FALLBACK_ALLOWED) / except Exception: return True`。函数 docstring 自称“Fail-closed when CDS_SM3_FALLBACK_ALLOWED=false”，但配置加载失败（pydantic 校验报错、.env 缺失、构造抛错）一律**默认放行 SHA-256 降级**，与本仓库其他诚实门（`validate_security_config` 生产 15 条 raise）方向相反。

### A72 — 目录会同时列出一产品的多个已发布版本（medium，业务正确性）

`catalog.py:28` 只过 `status == published`，**无 `is_latest` 过滤**；`data_products.py:353` 发布只写 `product.status = PUBLISHED`，不降级同系列旧行（`create_new_version` 也不改旧行 status）⇒ 一个产品发布过 N 次就有 N 行 published 同时出现在目录里。与第十八批的 is_latest 结论不矛盾：`DataProduct.is_latest` 全仓仍零读取方，消费端靠的是 `status` 字段。

### A76 — SDK `_parse_error` 对非 dict 的合法 JSON 抛 `AttributeError`（medium）

`sdk/cds_sdk/client.py:115-131`：`try: payload = resp.json(); detail = payload.get("detail", payload) / except ValueError: ...`。只接 `ValueError`，但服务端（或中间的网关）返 `[{...}]` / `"error"` / `null` 这种**合法但非 dict** 的 JSON 时，`payload.get` 抛 `AttributeError` 无人接 ⇒ 调用方看到的是 `AttributeError` 而不是 `CDSApiError`。讽刺的是同一个函数尾部三行都加了 `isinstance(payload, dict)` 守卫，只有最前面这一行没防。修法：`detail = payload.get("detail", payload) if isinstance(payload, dict) else payload`。

### A77 — 宿主侧真正会反序列化的是训练检查点（medium，潜在）

`training.py:548-596 _load_raw_checkpoint` 依次 `torch.load(path, map_location="cpu")`（:556，**无 `weights_only=True`**）、`np.load(path, allow_pickle=True)`（:579，且 :577 先试 `allow_pickle=False` 失败后**回退到 True**，经典反模式）、`pickle.load(fh)`（:595）。`requirements.txt:59` 是 `torch>=2.0.0` 未钉上界 ⇒ 2.0-2.5 区间 `torch.load` 默认就是全量 pickle。
路径来源：`_resolve_checkpoint_path:509-512` 用 `Path(job.output_path).expanduser()`，而 `output_path` 全仓**只被服务端写**（`training.py:281` `_write_sft_checkpoint`，落在 `_TRAINING_OUTPUT_ROOT=/tmp/cds_training_outputs`），无任何 Form/Body 能改 ⇒ 不是 API 可控，定为潜在。但这是全仓唯一的宿主侧反序列化 sink：任何“以后让调用方传路径”的改动，或同机邻居能往 `/tmp/cds_training_outputs/<job_id>/` 放文件，就直接变宿主代码执行。最便宜的加固：`weights_only=True` + 删掉 `allow_pickle=True` 回退。

### A79 — 用户名正则用 `$`，尾随换行可通过（medium）

`app/schemas/user.py:56` `if not re.match(r"^[a-zA-Z0-9_]+$", v)`。本机实测：`admin\n` → **True**（放行），`ad\nmin` → False。⇒ 可注册 `"admin\n"`：DB 唯一约束看作不同值，UI/审计里与 `admin` 视觉不可区分（登录是精确匹配，所以拿不到 admin 账号，但报表、日志换行注入、运维误认都是真实后果）。修法：`re.fullmatch` 或 `\Z`。

### A80 — 水印验证丢弃了 `bits_per_channel`（medium）

`embed_image_watermark(:132)` 与 `extract_image_watermark(:197)` 都收 `bits_per_channel`，但 `verify_image_watermark(:265-269)` 签名里没这个参数，实现里 `extract_image_watermark(image_bytes)` **按默认 1 bit 解码** ⇒ 任何 `bits_per_channel > 1` 嵌入的图永远验证失败（泄密溯源场景里“查不到水印”等于“无法归因”，而且是静默的）。叠加另一条：`return extracted is not None and expected in extracted` 是子串包含而非等值。

### A81 — egress 审计入库无 IntegrityError 兑底（medium）

`cube_egress_audit.py:257-309 ingest_egress_batch` 循环 `db.add` + 末尾单条 `await db.commit()`，**全文件 `grep IntegrityError` 零命中** ⇒ 去重表的一条唯一冲突会让**整批**审计（含本批新行）一起失败。`EgressEntry.record: dict`（`cube_egress.py:20`）对内容无结构约束，下游 `map_egress_record` 却假设具体形态（`sandbox.get(...)`、`",".join(secret_ids)`）⇒ 一条形态异常的记录同样 500。
- 边界（已查，不是匿名面）：`cube_egress.py:35` 挂 `Depends(verify_cube_audit_token)`，`deps.py:294-309` 是 fail-closed（`hmac.compare_digest`，token 未配置直接 401，且与 KMS 卷插件凭据分开），所以只有持令牌的节点 tailer 可达。

### A85 — `_persist_trust` 只 add 不 upsert，重复建信任堆积多行（medium）

`federation_connector.py:346-370`：每次调用都 `FederationTrustRecord(id=uuid.UUID(trust.trust_id.replace("trust-","")[:32].ljust(32,"0")), ...)` + `session.add(record)` + `commit()`，**无 merge/lookup/upsert**，末尾 `except Exception` 吞掉包括 IntegrityError 在内的一切 ⇒ 同一 `(local_space_id, remote_space_id)` 重复 `establish_trust` 会多行堆积（表上也无唯一约束），而重启后 `_space_index[row.remote_space_id] = ...` 只会留其中一行——这直接与 A63（重启后 `trust_id` 派生不一致 ⇒ 吊销 404）叠加：吊销时你可能在销另一行。另外 `:461` `loop.create_task(self._persist_trust(trust))` 是 fire-and-forget（A48 同族），写入失败不影响请求返回 200。

### A86 — WS 会话的两处错误契约缺口（medium）

```
sdk/cds_sdk/stream.py:48-52   _default_ws_open: ws.connect(url) 不带 timeout，连完才 ws.settimeout(timeout)
sdk/cds_sdk/stream.py:132-137 try: ... ws.send(json.dumps(frame))   ← 外层只有 try/finally(:157)，无 except
```
⇒ (a) TCP/握手阶段可无限阻塞（`recv_timeout` 只覆盖 recv，不覆盖 connect）；(b) `ws.send` 抛出的 `websocket-client` 原始异常直接穿透到调用方，破坏 SDK “只抛 `CDSError`” 的契约（A76 同族）。

### A87 — 任务创建把脚本原文放进 query string（medium，根因在服务端契约）

```
sdk/cds_sdk/client.py:620       return self._request("POST", "/api/v1/sandbox-tasks", params=params)   # params 里含 code / rag_query
app/api/sandbox_tasks.py:63-73  async def create_task(session_id, task_type="query", code: str | None = None, ...)   # 全是带默认值的标量 ⇒ FastAPI 当 query 解
app/api/sandbox_sessions.py:1194 code: str | None = Query(None)
```
⇒ SDK 是**合规**的，错在端点签名把 `code` 声明成查询参数。后果：沙箱代码原文会进访问日志/代理日志（与 A64「联邦零审计」、A38「REDIS_URL 进日志」同属敏感信息入日志族），大脚本受请求行长度上限拒（具体上限取决于 uvicorn/h11/代理，我未实测）。

### A88 — 非幂等 POST 参与 429/503 重试（medium）

```
sdk/cds_sdk/client.py:246-250  resp = self._client.request(method, path, **kwargs)
                              if resp.status_code in (429, 503) and retries > 0:
                                  time.sleep(min(_retry_after_seconds(resp), 5.0))
                                  return self._request_with_headers(method, path, _retries=retries - 1, **kwargs)
```
仓里已写下的口径是“只重试 429/503、`410 SESSION_TERMINATED` 不重试”（AGENTS），这一条不违反它；缺的是只读方法限定：`POST /sandbox-sessions`、`POST .../terminate`、`POST .../snapshots` 这类写操作在“网关已转发、后端已执行、响应被 503 取代”时会重复执行（双开会话 / 重复快照）。修法：只对 `GET/HEAD/OPTIONS` 自动重试，写操作把可重试错误交给调用方。

### A94 — HSM 回退门的默认值与默认异常方向（medium）

```
app/services/hsm_adapter.py:269-271   外层 try 把配置导入/构造/Vault 探测全包起来，except Exception: pass
app/services/hsm_adapter.py:274       fallback_allowed = True                      # 先赋 True
app/services/hsm_adapter.py:276-278   try: fallback_allowed = bool(_gs().HSM_SOFTWARE_FALLBACK_ALLOWED) / except Exception: pass
app/services/hsm_adapter.py:280-283   if not fallback_allowed: raise RuntimeError(...)  /  logger.info("Using software HSM fallback (dev/test only)")
```
形状上确实是 fail-open（配置读失败就默认允许软件 KEK），但生产上有三道独立门：`config.py:202` 默认 True 而 `config.py:445-449` 在生产下直接 RAISE（强制置 false）；`hsm_adapter.py:280` 在 false 时 RAISE；`main.py` 启动会披露已启用的回退项（T12）。真正缺的两块：配置读取失败时应该 fail-closed 而不是默认 True；以及回退只记 `logger.info`，且 `SoftwareHSMAdapter` 不向后端输出任何 `is_simulation`/backend 标记（honest-degradation 要求可辨别）。

### A96 — 会话密钥与 DEK 走 argv 注入（medium，仅 L3/bwrap 可达）

```
app/services/sandbox_security.py:487   env_args += ["--setenv", "CDS_SESSION_KEY", session_key]
app/services/sandbox_security.py:491-492  "--setenv", "CDS_DEK_HEX", ... / "CDS_KEY_ID", ...
```
bwrap 的参数在 exec 前就写进 `argv`，同 UID 的任何进程可从 `/proc/<pid>/cmdline` 读到明文密钥（与仓里「Session/DEK 只经环境变量传递、不落盘」的口径不矛盾，但 argv 比 env 更公开）。子代理写的是“今天就在跑”，我不同意这个措词：调用链是 `BwrapAdapter`（`sandbox_runtime.py:587`，`_guest_argv:737-744`，使用点 `:822`/`:1001`），而 `BwrapAdapter` 只在 legacy 门后注册（`:2139`）⇒ 默认生产姿态不可达，dev/e2e 开 L3 时可达。修法：改用 `--syncfd`/文件描述符或 `bwrap --setenv KEY ""` + 父进程写入 stdin，不要把密钥入 argv。

### A99 — 保存路径只认字面量且用 startswith：变量路径全 FATAL，`/sandbox/output/../..` 反而通过（medium）

`_has_valid_save_path:261-273` 只看 `ast.Constant` 且用 `startswith` 无 `os.path.realpath`：`save(out_dir)`（变量）返 False ⇒ FATAL（误杀常见写法）；而 `"/sandbox/output/../../etc/x"` 这种字面量反而通过。

### A100 — SQL 扫描按分号切段取首词：`WITH … DELETE … RETURNING` 在“只允许 SELECT”模式下放行（medium，当前引擎不可利用）

`_extract_sql_statements:275-287` 按 `;` 切段取首词，`WITH t AS (DELETE FROM users RETURNING *) SELECT * FROM t` 首词是 `WITH`，在“只允许 SELECT”的 `STRUCTURED_QUERY` 分支直接放行（`:208-214`）；当前查询引擎是会话内 DuckDB（不支持 CTE 内写操作）故不可利用，但扫描器同时被 SQL 转 DB 网关复用时就是破坏型语句绕过。

### A115 — 告警页“今日”统计用 UTC 日期与本地时区展示比较（medium）

`app/templates/alerts.html` 用 `new Date().toISOString().slice(0,10)` 当“今日”前缀，同页展示用 `toLocaleString('zh-CN')`；东八区 00:00–08:00 的告警会被归到“昨天”，且 `created_at` 为空格分隔无时区串或 epoch 秒时 `startsWith(todayStr)` 直接失配（统计恒为 0）。修法：统一按展示时区算当日区间比较，而不是比字符串前缀。

### A122 — `get_allowed_transitions` 直接返回转换表内部持有的 set（medium，别名地雷，与 A108 同族）

`app/services/session_state_machine.py:126-128  return VALID_TRANSITIONS.get(current, set())` —— 默认分支才是新集合，命中分支返回的是模块级 `VALID_TRANSITIONS[...]` 本体；任何调用方 `.add()/.discard()/.clear()` 一次就永久改写全局状态机且跨请求共享。全仓未见现有调用方写入（grep 无 `get_allowed_transitions(...).add`），所以计地雷不计现行事故。修法：`return set(VALID_TRANSITIONS.get(current, ()))`。

### A123 — `REVOKED` 终端态不可达，且全仓无人写入（medium）

`VALID_TRANSITIONS`（`session_state_machine.py:23-63`）里没有任何一个状态的目标集含 `REVOKED`，`:63 REVOKED: set()`，而 `:76` 把它列入 `TERMINAL_STATES`、`:149` 给了描述文案 ⇒ 谁要是真去转这个态，`validate_transition` 永远失败、`require_transition` 抛 `ValueError`。同时 grep `SessionStatus.REVOKED` 全仓只命中状态机自身三处，密钥吊销级联走的是 `sandbox_runtime.py:2526 terminate_sessions_by_key`（导入状态机后推向 TERMINATED）。⇒ 不是“吊销坏了”，而是**一个对外可见的枚举值与其文案没有实现路径**：要么给它加上入边并在吊销时真用，要么删掉枚举/文案，避免下游按字面意思渲染。

### A137 — 旧后台样式：fixed 侧边栏无滚动、无响应式、暗色对比度不足（medium）

`app/static/css/admin.css` 三条均为真：侧边栏 `position:fixed; height:100vh` 无 `overflow-y:auto`（菜单变多或 Ctrl++ 后多出项永远滚不到）；`margin-left:220px` 与 `.sidebar{width:220px}` 硬耦合且全文件无媒体查询（375px 屏上内容区仅剩 ~155px，flex item 默认 `min-width:auto` 遇宽表格直接撑大整页）；多个前景/背景对按 WCAG 相对亮度算低于 4.5:1（`--text-muted #8b8fa3` 亍 `--surface #1a1d27` 约 3.2:1）。与 A114/A115 同一个遗留管理控制台（该页族本身就是待废弃面），所以整族定 medium 而定，修复前不要往这个后台里加新功能。

### A153 — 熔断器的失败窗口衰减分支不可达，`failure_window_seconds` 完全失效（medium，潜伏）

`app/services/task_circuit_breaker.py:98` 先 `stats.last_failure_time = now`，`:102` 再 `if now - stats.last_failure_time > self.config.failure_window_seconds: stats.failure_count = 0` ⇒ 差恒为 0，衰减永不发生；叠加 `record_success:85-91` **只在 HALF_OPEN 才清零计数**（CLOSED 态成功不清零），净效果是“阈值 = 整个进程生命周期的累计失败数”，而不是配置说的“5 次/300s”。潜伏依据：全仓只有 `tests/test_task_circuit_breaker.py:5-18` 导入本模块（`app_output_proxy.py` 里的 `pii_circuit_breaker` 是无关字串）。修法：先存 `prev = stats.last_failure_time` 再赋值再判窗口。

### A154 — `readme` 被写进 `[project.optional-dependencies]`，变成一个幽灵 extra（medium，构建元数据）

`sdk/pyproject.toml:8-12`：`stream = [...]` 后紧接 `readme = "README.md"`——该表下每个值必须是 PEP 508 requirement **数组**，而 `readme` 不是 extra 名字。确定部分：打包元数据里没有 long_description（PyPI/内部源页无 README），下游会看到一个假的 `cds-sdk[readme]` extra。未实测部分：`pip`/`setuptools` 对非法 extra 取值是否直接报错（本地无离线构建环境，没跑 `python -m build`）⇒ 不写成“安装会失败”。修法：把 `readme` 移到 `[project]` 段。（`sdk/cds_sdk/__init__.__version__` 与 pyproject `0.3.0` 的不一致是 AGENTS.md 已记录的已知漂移，不重复计条。）

### A158 — 工具注册表的授权字段全是装饰：`roles`/`budget_cost`/`network_targets` 无人消费（medium，活路径）

`app/services/agent_tools.py:20 network_targets: list[str]` 在全仓**零消费者**（grep 除本文件无命中），`:5` 模块文档却写“Network-capable tools carry `network_targets` and are gated at runner build time”，而 `:195` 给 `http` 工具填的是 `[]`。真正的门禁是 `_AGENT_HTTP_ALLOW` 字面量 + `_net_allowed`（A157）——与这个字段毫无关系。`validate_run(tool_ids)` 也不看 `roles`/`budget_cost`。**但 ocr 的“buyer 也能选 admin 工具”在 API 路径不成立**：`app/api/agent.py:71` 在 `validate_run` 之后紧接 `_enforce_tool_roles(current_user, body.tool_ids)`——只是这个函数**全仓只有这一个调用点**，`agent_service.py:433` 那套 runner 防伪校验只查“未知/重复工具”。⇒ 任何新增的工具选择入口（后台任务、自动抽取工具链）如果忘了自己调 `_enforce_tool_roles`，就没有任何兜底。修法：把角色/预算收进 `validate_run(tool_ids, *, role, budget)` 单一入口，并删掉或落实 `network_targets` 字段。

### A159 — 记忆化检测与 MIA 探针分母口径不一致，`risk_level` 与可配阈值脱钩（medium，潜伏）

`app/services/memorization_check.py` 用 `generated_text.split()` 的**含重复位置数**作 `total_ngrams`，而 `mia_probe.check_text_memorization` 的分母是**去重后的 n-gram 集合大小** ⇒ `int(ratio * total_ngrams)` 反推的 `matched_ngrams` 与真实匹配数对不上；`risk_level` 又按硬编码 0.1/0.3/0.5/0.8 分档，与 `memorization_threshold` 无关（阈值 0.8 时 ratio=0.45 标 medium 但 `is_memorized=False`；阈值 0.05 时已判记忆化却标 `safe`），而调用方通常同时读这两个字段。`percentile` 也未钳制下界（负值走 Python 负索引取到接近最大困惑度）。可达性：`grep memorization_check|MemorizationChecker` 全仓除本文件**零命中** ⇒ 潜伏；但这两个字段是“模型是否泄露训练数据”的对外结论，接线前必须先统一口径。

### A160 — `opa_client`：评估链潜伏，但 shutdown 后会重建客户端（medium，潜伏为主）

可达性拆开看：评估路径 `policy_evaluator._evaluate_via_opa` 所在模块**零导入方**（A95 已定 latent）⇒ ocr 报的“`resp.json()` 非 dict → 下游 `.get` 抛 AttributeError → 被宽泛 `except` 吞成“OPA 不可用→Python fallback””**今天是潜伏的**；但 `close()` 是活的（`app/main.py:224-225` 在 lifespan 关闭时调），而 `close()` 不置 `self._client = None`、`_get_client()` 只看 `is_closed` ⇒ shutdown 后迟到请求会新建一个永不关闭的 `AsyncClient`（连接池泄漏，测试里反复 start/stop lifespan 会累积）。`package_path` 未百分号编码属真但当前唯一调用方传 UUID 派生值（ocr 自己也这么记）⇒ low。四处 `except Exception` 只记 `str(e)` 丢 traceback，在安全组件里意味着“配置写错也静默降到 Python PDP”永远看不到根因。

### A166 — `evaluate_trust_score` 的整型截断会真的翻转授权（medium，活路径）

`app/services/trust_evaluator.py:131-132` 用 `int()` 截断加权分，而 `federation_connector.py:565` 拿这个整数与 `min_trust_by_op` 比。子代理枚举了 10003 组输入：364 组因截断跨过等级阈（如 29/29/86/77 → 49.99999999999999 → 49 而判为 `new` 而非 `silver`）；（它自己声明 ocr 举的 90→89 例子不可重现，我按“方向存在、例子错误”采信）。联邦信任是准入判断，截断应当四舍五入或直接比浮点。修法：比较前 `round()` 或统一用 `Decimal`/定点整数。

### A169 — webhook 派发窗口会被“已暂停订阅”的滞留行卡住（medium，活路径）

`app/services/delivery_service.py:175` 先 `.limit(limit)` 取一批待发行，`:186` 才逐行看 `if subscription.status != ACTIVE: continue`。若最旧的一批 pending 行属于被暂停（`MAX_CONSECUTIVE_FAILURES` 自动降级，`:161`）的订阅，它们会反复占满窗口而使**活跃订阅永远排不到**（推送饥饿）。子代理同时推翻了 ocr 的“僵尸 `delivering` 行”那一半：`:126-129` 只 `flush()` 不 commit，崩溃/`CancelledError`（`:150` 的 `except Exception` 接不到）会回滚，PG 里仍是 `pending`/`failed`。修法：把订阅状态下推到 SELECT（join + `status = ACTIVE`），或按“未被成功派发过的行”递增游标取窗。

### A172 — `utils/watermark.py` 图片水印：`bits_per_channel` 只写不读，验证器永远按 1 bit 解（medium，今天是哑弹）

`app/utils/watermark.py:132 embed_image_watermark(..., bits_per_channel: int = 1)`（文档写 1-4，`:166 mask = (0xFF << bits_per_channel) & 0xFF`）与 `:197 extract_image_watermark(..., bits_per_channel=1)` 都收这个参数，但 `:265-269 verify_image_watermark(image_bytes, user_id, session_id)` **签名里没它**，固定用默认 1 bpc 解码 ⇒ 任何以 `bits_per_channel>1` 嵌入的图永远返 False（假阴性，而“水印已植入”是对外权属/溯源结论）。今天不爆，因为唯一的活调用方 `app/services/output_inspection.py:510-513` 两边都用默认值（它也没把参数透出来）——一个拿参数就碎的在地雷。ocr 另说的 `:119/:269 expected in extracted` 子串比较：文本水印“长文档里含水印”本就是包含语义（containment 是 by design），图片提取出来的是定长载荷，两种写法几乎等价 ⇒ 这一半降为可读性问题，不进缺陷台账。

### A176 — `SubprocTimeout` 被 `int(Seconds())` 截成秒：小于 1s 的配置把插件整体打死（medium）

`cds-volume-plugin/cipher.go:172` `NewTongsuoCipher(cfg.HelperPath, int(cfg.SubprocTimeout.Seconds()))`，而 `config.go:60` 的默认来是 `CDS_VOLUME_PLUGIN_SUBPROC_TIMEOUT_MS`（默认 10000）。配成 200/500（为了拖长尾延）或 0/负值 ⇒ `c.timeout == 0` ⇒ `cipher.go:63 time.After(time.Duration(0) * time.Second)` **立即触发** ⇒ 每一次 helper 调用都以 `timeout after 0s (fail-closed)` 失败：卷完全不可用，而错误信息把“配置精度被截断”说成“helper 超时”，误导到完全错误的方向（排障时会去查 Tongsuo 性能）。修法：`Config` 直接存 `time.Duration`，`Load` 阶段校验 `> 0`（并限定下限，比如 ≥1s）；超时分支的报错里带上原配置值。

### A177 — `Cipher` 接口入口不校长度：密钥短了静默零填充、长了静默截断（medium，今天不可达，但这是“铁律”边界）

`cds-volume-plugin/cipher.go:70-99`：`helperFrame` 先 `append(buf, make([]byte, dekLen)...)` 开一个**全零 key 槽**，`Seal/Open` 再 `copy(in, key)` —— `copy` 只搬 `min(len(key), 16)`，所以 `len(key) < 16` 时剩下的是**零字节密钥**、`> 16` 时静默截断，两种都不报错；`nonce` 与 `tag` 长度同样不校（而 C helper 严格 `RD_FULL(key); RD_FULL(nonce); RD_FULL(aadlen_be)` 固定偏移读，`sm4gcm/sm4_gcm.c:140-144`），长度不对就是偏移错乱。`cipher.go` 入参声明上写着“Open must … not return unauthenticated plaintext (fail-closed, 禁伪造 red line)”，但长度这道 fail-closed 不存在。**今天不可达**：`kms.go:125` 对 KMS 返回的 DEK 做了 `len(dek) != dekLen` 检查，`plugin.go:63`/`selftest.go:14` 用 `randomBytes(dekLen)`，nonce 也只由 `randomBytes(nonceLen)` 产生 ⇒ ocr 定 critical 高估，实际是新增调用方才会踩的接口地雷。另一个真实差异：`AESGCMCipher.gcm:124` 用 `NewGCMWithNonceSize(b, len(nonce))` 接受**任意长度 nonce**，而 tongsuo helper 只认 12 字节 ⇒ 一旦有人传非常规 nonce，开发后端“能跑”、生产后端解密失败，同一份数据跨后端不可解。修法：`Seal/Open` 入口 `len(key)!=dekLen || len(nonce)!=nonceLen || (op==open && len(tag)!=tagLen)` 直接返回错误（十行，可进 `plugin_test.go` 的 fail-closed 负路径）。

### A178 — helper 超时分支丢弃 `Kill` 错误后无条件 `<-done`（medium-low）

`cds-volume-plugin/cipher.go:63-66`：`case <-time.After(...): _ = cmd.Process.Kill(); <-done; return nil, fmt.Errorf("timeout …")` —— `Kill` 的错误被 `_ =` 丢掉，若 Kill 真的失败（EPERM、已停止、子进程处于不可中断 IO），`cmd.Wait()` 可能永不返回，这行 `<-done` 就把**gRPC 处理 goroutine 无限挂住**，恰好违背它声称的“fail-closed 超时保证”；而且日志里看不到 Kill 失败的原因。`done` 是缓冲的，所以不删 `<-done` 只会泄漏 goroutine，不会丢 panic——两者里选一个，不能两个都要。修法：保留 `<-done` 但包一层 `select { case <-done: case <-time.After(2*timeout): }`，并把 Kill 错误拼进返回的 error。

### A182 — HTTP 错误把原始响应体原样拼进 error（medium，附对 ocr 的修正）

`kms.go:81` `fmt.Errorf("kms %s %s: HTTP %d: %s (fail-closed)", ..., string(data))` 把最多 1 MiB 的上游响应体放进 error，而这个 error 会经 `plugin.go:70/94` 进 gRPC 状态与 `log.Printf`。ocr 说“422 会把 `dek_hex` 回显到 detail ⇒ 明文 DEK 落盘到日志”——**这一推论在当前控制面不成立**：`app/schemas/kms.py:26` 的 `dek_hex: str` 是裸类型，Pydantic v2 的 422 只回显**出错字段**的 `input`，不会把整个 body 贴回来（除非哪天给它加了 `pattern=`/自定义校验器）。仍然真的部分是：上游/代理的任意响应体（HTML 错误页、包含 header 的鉴权失败 JSON）会进日志与 gRPC 回给调用方。修法：响应体只留前 200 字节并进一行白名单摘要，`RegisterDEK` 路径上不拼 body。

### A184 — `mutual_tls` 签发侧无条件写 `space_id`、校验侧只在真值时写 ⇒ 空 `space_id` 的证书永远验不过（medium，潜伏）

`app/services/mutual_tls.py:136` 在 `cert_data` 里无条件带 `"space_id": space_id`（`:140` 拼成签名载荷），而校验侧 `:186-187` 是 `if cert.space_id: cert_data["space_id"] = cert.space_id` ⇒ `issue_certificate("")` 产出的证书重算出的载荷少一个键，签名永远对不上（子代理用桩化 `crypto_service` 实跑重现，与 gmssl 是否安装无关）；同时 `verify_certificate` 入口无 tz/格式校验、`:192-196` 的 `return self._crypto.verify(...)` 外面没有 `try`，签名格式异常会直接冒泡。定潜伏：`app/` 里对 `mutual_tls` 的真实用法只有 `federation_connector.py:671 load_federation_mtls_material`（加载已有材料），`space_id=""` 的签发今天无调用方。修法：两侧共用同一个 `build_cert_data(cert)`，并把空 `space_id` 在签发处就拒。

### A188 — 水印读写的性能项同一根因：每读 1 bit 就整张量 `_flatten`（medium，活路径）

`model_watermark.py:255-261`（`_get_weight_value` 对非“半层 list”一律 `cls._flatten(tensor)`）+ `:306-310`（`_extract_bit` 同样整张量复制）+ `:129-137`（`all_positions` 为**每个权重**建一个 `(name, idx)` tuple）。在水印位数达到几百、层为百 MB 级时，成本是 `O(bits × 张量大小)` 的全量复制，另加百万级 tuple。修法是同一个：一次性 `reshape(-1)` 拿到 view 后按索引读写，位置生成改成逐层 `range(size)` 惰性消耗。

### A195 — `contracts/AuditRegistry.sol` 无访问控制且锚定可被任何人抢先/污染（medium，潜伏：链未接线）

`contracts/AuditRegistry.sol:28-36`：`function anchorAudit(bytes32 merkleRoot, string memory metadata) external { require(audits[merkleRoot] == 0, "already anchored"); audits[merkleRoot] = block.timestamp; emit AuditAnchored(merkleRoot, block.timestamp, metadata); }`——没有任何 `onlyOwner`/角色位，`AuditAnchored` 事件也不带 `msg.sender`。两个后果：① **抢先锚定** —— 任何知道（或能算出）某个 Merkle 根的一方可以先把它写上去并附任意 `metadata`（“batch ID / 时间源”都是自填），随后平台自己的锚定因 `require(… == 0)` 而**失败**，链上留下的却是别人那份带假元数据的记录；② **来源不可证** —— 因为事件里没有锚定者，事后无法区分“平台做的锚定”与“第三方抢先做的”，而合规报告需要把锚定当作可验证事实。另一处口径错：`AGENTS.md` 写的是 `anchorRoot`，盘上真名是 `anchorAudit`（引用前先看盘）。定潜伏的依据：`app/services/blockchain_adapter.py:645` 自述“no real/wired chain anchoring exists”，四个 `anchor()` 实现都是带 `backend` 披露的模拟/外部适配器 ⇒ 今天上不了链。（**交叉验证**：ocr 在 w3 波次也独立报了同一条（`E2-78` high/security），它补了一个我没写到的角度：`require(== 0)` 是 first-writer-wins 且**永不修正** ⇒ 合法审计方对该 root 的锚定被**永久 DoS**（永远 revert），而下游若把 `verifyAudit == true` 当权威就会接受一个攻击者自定时间戳背书的伪造 root。）修法（部署前必做）：加 owner/registry 角色位或在事件里记 `msg.sender` 并让验证侧比对允许集合；锚定前先 `verifyAudit(root)` 把“已锦”区分成“自己已锦”与“被抢先”。

### A202 — 内存卫生与可用性小项簇（medium，子代理逐条复验，我未复验）

① `plugin.go:145-152` 幂等比对用的 `existing` 明文从不 zeroize（对照 Attach 在 `:256/:260/:263` 三处都清）；② `cli.go` 先 `os.ReadFile(*dataFile)` 无上限，`MaxVolumeBytes` 在**读完之后**才校；③ `main.go` 的 `Validate()` 不校 `SubprocTimeout`，<1000ms 的值被截成 0 秒（子进程立即超时）；④ `rootfs.go:83-96` mount/umount 无 context/超时，umount 失败后的清理静默丢弃（挂载点/loop 泄漏）；⑤ `server.go:144-145` 先 `_ = os.Remove(cfg.SocketPath)` 再 `net.Listen`，无条件删 socket 重建且无单实例互斥，也没有优雅停机路径（serve 路径无取消通道）；同文件 `:149` 把 socket 改成 `0600`、`:157` 又 `Chown(root:cds)`，与“`cds` 组成员可 dial”的部署模型自相矛盾；⑥ `cli.go` 丢 `json.Marshal`/`MarshalIndent` 错误 ⇒ 空输出仍 exit 0（脚本化部署读到空 JSON）；⑦ `selftest.go` 只验往返、**不断言“确实发生了加密”**，负向用例缺 AAD 篡改（与项目 honest-degradation 铁律同源）；⑧ 非国密降级只挨一条 WARNING 就继续 Serve（已有四处部署强制 tongsuo + cipher 名入审计，故不是 high）。

本批**证伪 / 降调**（进“不要再报”）：
- **0012 #3 FALSE**：`user_id` 就是主键（CASCADE 查找已被索引覆盖），且 `app/api` 里根本没有删用户路径。
- **0012 #2 的“认证绕过”子结论 FALSE**：`app/api/auth.py:202-219` 对 `confirmed=true` 但无 secret 的情况 **fail-closed 到 401**；缺 CHECK 本身仍计 LATENT。
- **plugin.go #5 的“stageDir 包含性自检可被绕过”FALSE**：`"cds-"` 前缀使首段不可被 `..` 消化，任何含 `/` 的 id 会让 `rel` 变成 `../…` 而被拒（子代理用 stdlib 探针证了这套包含代数）；同句“入口未校验”那一半成立，已并入 A199。
- **rootfs.go #5 不成立（本批再修正一次，连子代理的理由带我自己上一版的理由一起推翚）**：子代理说“`MkdirTemp("/run/cds-volume-plugin", …)` 依赖目录预存”，我当时记成“由 `deploy/cube-node/install.sh` 建”——**两个都不对**：`server.go:140-143` 在 `Serve()` 入口就 `os.MkdirAll(filepath.Dir(cfg.SocketPath), 0711)`，而 socket 默认路径就在这个目录下 ⇒ 服务一启动目录必然存在，该条连潜伏都不算。；**selftest #3 panic 在现有两后端不可达** ⇒ LATENT；**plugin.go #2 空数据推论误**、**#4“回滚一半”描述不准**、**server.go #1 TOCTOU 仅宽松 umask 下成立**、**server.go #8 / layout #4 的“任意文件销毁”不可达** —— 均按上调后的口径记账。

### A205 — 镜像与 Makefile 的部署面默认值（medium，同批 30 条里次一簇）

① `Dockerfile:44-46` 建了 `cds` 组与 `cubelet` 用户却没有 `USER`，而 `server.go:149` 是 `Chmod(socket, 0600)` + `:157 Chown(path, 0, gid)` ⇒ 镜像注释声明的“组内用户可 dial”在 0600 下结不成立（组位为 0）；今天容器实际以 root 跑所以下游能连，但一旦按注释改成 `--user cubelet`，`Chown`/`Chmod` 与 loop mount 全失败而代码只 `log WARN` ⇒ “看着正常但权限不合规”的静默降级（与 A202⑤ 同一事实的镜像侧）。② `VOLUME ["/var/lib/cds/volumes"]`：编排不显式挂 hostPath/named volume 时 Docker 建**匿名卷**，容器重建即丢密文——而本插件是 fail-closed，**密文丢失 = 数据永久不可恢复**；且 `VOLUME` 会覆盖构建期对该目录的 chown/chmod。③ 两个安全参数在镜像层给了“可用但不安全”的默认值：`KMS_BASE_URL` 默认明文 `http://127.0.0.1:8000`、`KMS_SERVICE_TOKEN` 默认空串 ⇒ **漏配不报错**，安静地用空 token 走明文 HTTP 传 `dek_hex`（与 A179/A180 同族）。④ 基础镜像与工具链无 digest/版本 pin（浮动 `golang:1.25-bookworm` + apt 不 pin）——对加密插件镜像是供应链可追溯性问题（`go.sum` 校验没被绕过，只是缺 pin）。⑤ `Makefile:22-25 helper` 盲信子 make：不校 `tongsuo-build/` 是否已 `install_sw`、不确认产物存在就 `cp -f`，新克隆机器上只能得到一串晦涩的链接错误。⑥ `.gitignore`（仅 5 行）确实缺 `*.test`/`coverage.out`/`*.pem`/`*.key`/`.env` 这类**测试产物与密钥材料**，本地调 mTLS 时很容易随 `git add .` 入仓。

本批证伪 / 降调：
- `.gitignore` 里“要补 `*.o`/`*.d`/`*.gch`”——**FALSE**：`sm4gcm/Makefile:21-26` 是单命令从 `.c` 直接出二进制（`$(CC) … -o $@ sm4_gcm.c …`），根本不产出中间目标文件。
- Dockerfile “构建期从未跑过真实 encrypt/decrypt”——机制对（构建只跑 `frobnicate` 拿退出码 1），但危害要打折：运行期 `serve` 前会跑 `RunSelfTest`（真走 helper 做往返），失败即拒绍 serve ⇒ 不是“静默损坏”而是“启动失败”，按 medium 记。同理“`make -j$(nproc) > /dev/null` 丢日志”属可诊断性，不影响正确性。

### A209 — `@media (max-width:1280px)` 的覆盖被自己的 `nth-child(3)` 特异性压住 ⇒ 换行后第三列不按同一基准对齐（medium，自适应链路，我逐行亲验）

```
:19  .cds-dev-task-create .cds-trio > *            { flex: 0 0 373px; }   /* (0,2,0) */
:26  .cds-dev-task-create .cds-trio > *:nth-child(3){ flex: 1 1 auto; }   /* (0,3,0) */
:37  @media (max-width: 1280px) { :42  … .cds-trio > * { flex: 1 1 300px; } }  /* 仍 (0,2,0) */
```
媒体查询不增加特异性，`(0,3,0) > (0,2,0)` ⇒ ≤1280px 换行后第 1/2 列是 `1 1 300px`，**第 3 列仍是 `flex: 1 1 auto`（basis 按内容）**，同排列宽不齐、尾列随内容忽宽忽窄；`:32` 还另有一条同样赢的 `nth-child(3) .ant-typography`。（ocr 原引的 base 值是 `1 1 300px`，真值 `:20` 是 `flex: 0 0 373px` —— 不影响它的特异性推导。）
**自适应口径（用户 2026-10-18 定）**：本文件**只有 `1280` 一颗断点**，而 `:20` 的 `flex: 0 0 373px` 是定宽 ⇒ 该像素布局成立的区间是「卡片内容宽 ≥ 373×2 + 第三列自然宽 + 2×25 内距」（注释自己算的是 821px 加侧栏），**1281–1439 这一段完全没弹性兼容**，与同目录 KPI 带 1280–1439 档被压到 75px 是同一个坑。修法：断点内补同特异性的 `> *:nth-child(3) { flex: 1 1 300px; }`，并把 `0 0 373px` 改成 `1 1 min(373px, 100%)`。

### A211 — `alembic/env.py:35` 把含 `%` 的密码再送进 ConfigParser 插值 ⇒ 带转义密码的库直接跑不了迁移（medium，活路径，我亲验调用点）

`alembic/env.py:35 config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)`，而 `:47 url = config.get_main_option("sqlalchemy.url")` 取回时 configparser **会做一次 `%` 插值**。密码里只要有一个字面 `%`（随机生成的强密码很常见），要么报 `InterpolationSyntaxError`，要么被当插值语法静默吃掉⇒ `alembic upgrade head` 全红。CI 的「Migration chain (fresh DB)」用的是干净密码，因此测不到。修法：`settings.DATABASE_URL.replace("%", "%%")`，或不用 main option 而直接传 `url=` 给 `engine_from_config` 以外的路径。

### A214 — 模式层约束/索引重复与缺失族（medium，子代理逐条复验，我抽验 E2-37）

- **冗余索引（8 处）**：`E2-8` `certificates.serial_number` 既有 `UNIQUE` 又另建同名唯一索引；`E2-11` `field_visibility_configs.product_id` 同形；`E2-9/:10/:24/:36/:41/:53` 非唯一索引与复合索引/唯一约束的前导列完全重叠（`ix_session_operations_session_id`、`ix_shared_volumes_owner_id`、`ix_trained_models_product` vs 0018 的 `ix_trained_models_product_id`、`ix_cube_egress_dedup_node` 等）；`E2-39 ix_mpc_keys_status` 无人使用。写放大 + 存储，不是正确性缺陷。
- **E2-37 `mpc_key_shares` 缺 `UNIQUE(key_id, share_index)`（我亲验）**：`app/models/mpc_key.py` 该表的 `__table_args__` 只有 `Index("ix_mpc_shares_key", "key_id")`，`share_index` 仅 `nullable=False`。Shamir 恢复靠 x 坐标（share_index）做 Lagrange 插值，同一 `key_id` 下出现重复 share_index 会让插值分母归零或拼出**错误的密钥分片**——这是加密正确性而不是卫生问题。**未验部分**：我没逐条追完所有写入路径（custodian 重发分片时是 INSERT 还是 UPSERT），所以定级为 medium-high 并标明「接线/重发路径待确认」。
- **E2-21 没有任何模式级约束阻止两个并发的 exclusive 操作**（同表无部分唯一索引）⇒ 互斥全靠应用层 check-then-act；与 `E2-54`（`cube_egress_dedup` 的 check-then-act 去重不接 `IntegrityError`）同构：一次并发就双写。
- **E2-64 `alembic/versions/0018_owner_fks.py:30 op.create_index("ix_trained_models_product_id", …)`** 在迁移事务里用普通 `CREATE INDEX`（非 `CONCURRENTLY`）⇒ 对 `trained_models` 取 `ACCESS EXCLUSIVE`，存量库上这一步会**锁写**（本仓迁移均单事务，无 `AUTOCOMMIT` 绕过）。
- **E2-7 `app_credentials` 时间列无时区**：`app/models/app_credential*.py:49-52` 四个 `DateTime` 均无 `timezone=True`（PG `TIMESTAMP WITHOUT TIME ZONE`）。**未验部分**：我没找到与 aware datetime 直接比较的现场（`app/services/app_credential*.py` 未命中），所以只按「存/取与比较口径不一」记 medium，不声称会抛 `TypeError`。
- **E2-55** `cube_egress_dedup` 不在保留计划里且无 TTL ⇒ 无限增长（审计保留铁律只禁止清审计日志，不清这张表）。
- **E2-12/13/16/17（low，文档性）**：`0000` 基线 docstring 仍写「两个 revision 的链」、`0001` 自称「对 create_all 已建库安全」、`0017` 自称「additive-only」——实际这些迁移在 `create_all` 库上会重复建列/索引报错（AGENTS.md 里写的 additive-only 只对新 revision 成立）；而 `E2-16` 那句「`contract_index` 不需要索引，因为 `contract_id` 已索引」是事实错误。

**去重**：`E2-40 trained_models.size_bytes` 与 `E2-73 delivery_bundles.size_bytes` 就是已记的 **A203**（四处 4 字节 `Integer` 体积列）；`E2-84` 版本双源就是 **A154 / A207③**；`E2-87`（标识符不做百分号编码）与 `E2-88`（任务代码/RAG 查询走 query string）另登于 A215/A216。

### A216 — SDK 把整段任务代码与 RAG 问句放进 POST 的 **query string**（medium，我亲验行号）

```
sdk/cds_sdk/client.py:609  params = {"session_id": session_id, "task_type": task_type}
                    :611-612 if code is not None: params["code"] = code
                    :619     if rag_query: params["rag_query"] = rag_query
                    :620     self._request("POST", "/api/v1/sandbox-tasks", params=params)
```
同一文件的 `:368` 证明作者本来就会用 `json=body`，所以这是口径丩一而不是能力问题。两条具体危害：（1）**密态租户数据进入请求行**——访问日志/反代日志会记完整 URL，本仓已有 `logs/cds-egress/access.jsonl` 这种先例；（2）**尺寸上限**——h11/uvicorn 对单行长度有默认上限，超过几万的 notebook 会直接 414/431 而不是可恢复错误。修法：改 POST body（服务端 schema 已接 JSON body 的路由直接可用）。

### A219 — 进度条高度靠 `!important` 盖到所有 `.ant-progress-*`，注释与代码不一致（medium，我亲验；环形的部分是潜伏）

`cds-frontend/src/index.css:680-683`：注释写「原型实测填充带高 ≈15px」，代码是 `height: 12px !important`；选择器是 `:is(.cds-home, .cds-detail) .ant-progress-inner, … .ant-progress-bg`，不限定进度条类型。`.ant-progress-inner` 在 antd v5 里同样是 circle/dashboard 进度环的外层容器（尺寸由内联 `style` 按 `width` prop 写），这条 `!important` 会把环容器强压成 12px。**潜伏部分（如实）**：我 `grep 'type="circle"|type="dashboard"' pages/` 命中 **0**，现有六处 `<Progress>`（`ContractDetail.tsx:214`、`DevHome.tsx:265`、`Quota.tsx:39/64`、`TaskDetail.tsx:379`、`SessionDetail.tsx:1300`）全是折线型 ⇒ 压环危害是「以后加了环形进度就会碎」，现阶段真实的是注释/实现不一致与 `size="small"`（`Quota.tsx:39`）被 `!important` 无视。修法：限定 `.ant-progress-line` 前缀，并让注释与值对得上。

### A220 — 顶栏折叠到移动端时只重置了 `height`，`line-height: 64px` 还在 ⇒ 品牌字换行后每行高 64px（medium，移动端可见，我亲验）

```
index.css:102 .cds-header { … line-height: 64px; }      /* 基态：定高行垂直居中靠它 */
index.css:554 @media (max-width: 767px) { :555 .cds-header { height:auto; min-height:64px; padding:10px 14px; align-items:flex-start; } }
index.css:569   .cds-brand-text span { white-space: normal; }   /* 同一断点内允许换行 */
```
断点里**没有 `line-height` 重置**（我把 554-602 整块读完确认），而同一块里反而打开了换行 ⇒ 品牌字一折行就是 64px/行，header 被撑开并被 `overflow: hidden` 裁切；顶栏按钮/图标也继承 64px 行高。补一行 `line-height: normal` 即可。这条止于 ≤767px，桌面端不受影响。

### A221 — 灰态文字一族全部不过 WCAG AA（medium，我亲验用色点 + 自己算对比度）

我拿 WCAG 相对亮度公式自己算（不是引用 ocr 的数），并逐个确认了用色点可达：
| 前景 / 背景 | 对比度 | AA 4.5 | 用在哪（已确认渲染） |
|---|---|---|---|
| `#a6adb8` / `#ffffff` | **2.26:1** | FAIL | `index.css .cds-menu-muted` → `Sidebar.tsx:270`（`/flow/guide`）、`:278`（`/flow/quota`）**可点击导航项**，还只 12px |
| `#86909c` / `#f2f3f5` | **2.92:1** | FAIL | `.cds-state-chip.is-gray` → `DevTasks.tsx:74` 状态胶囊 |
| `#86909c` / `#ffffff` | **3.24:1** | FAIL | `.cds-task-card-specs dt` 小字（ocr 写≈3.0，实际 3.24） |
建议色我顺手验了可用：`#4e5969` on `#f2f3f5` = **6.40:1**，`#6b7280` on 白 = **4.83:1**。**不要把这条说成「颜色是唯一语义载体」**：胶囊里有文案（`DevTasks.tsx:74` 三元只切 `cls`），失败的是对比度，不是色盲可达性。

### A222 — 开发环境页整份样式零断点，两栏高度写死 `calc(100vh-250px)` + `min-height:700px`（medium，我亲验）

`grep -c '@media' notebookWorkbench.css` = **0**（331 行、四个响应式敏感维度无一幸免），并且：
```
:44-50 .nbw-columns { display:flex; gap:24px; height: calc(100vh - 250px); min-height: 700px; }
:51-53 .nbw-left    { flex: 0 0 250px; width: 250px; }
:79    .nbw-card-grow  { flex: 1 1 auto; }      :80 .nbw-card-fixed { flex: 0 0 auto; height: 320px; }
:59-60/:76-77/:98 多处 min-height: 0 + overflow: hidden
```
两条真实的边界：（1）**高度**：视口高 < 950px（1366×768 笔记本、125% 缩放、iframe 嵌入）时 calc 值小于 700，`min-height` 反压上去使实际高超过可用空间，而外层 `overflow: hidden` 直接裁切（不是变双滚动条那么轻）；（2）**宽度**：左栏 `flex: 0 0 250px` 定宽 + 零断点 ⇒ 窄宽下全给了左树。另 `100vh` 在 iframe 里等于内框视口而不是宿主可用高度，这是嵌入场景的常驻错。按用户的自适应口径，这里必须给出定高成立的区间（≥ 950px 视口高）而不是只删定高。

### A223 — 全文件零 `:focus-visible`，而图标按钮自己把 `border/background` 抹平了（medium，我亲验）

`grep -c 'focus-visible' notebookWorkbench.css` = **0**；可交互类 `.nbw-menu-label` / `.nbw-lang-pick` / `.nbw-folder-toggle` / `.nbw-tab` / `.nbw-icon-btn` 只写了 `:hover` 与 `:disabled`。`:277-289` 的 `.nbw-icon-btn` 正常色 `#7b8ba3`、禁用色 `#c4c9d1`（两者差异本来就小，再叠一个无焦点环）。键盘用户在这个页面（文件树 / tab / 图标按钮）看不到焦点位置。修法：给 `.nbw-root` 下所有可聚焦元素统一一条 `:focus-visible { outline: 2px solid var(--nbw-blue); outline-offset: 1px; }`。

### A224 — 「状态未知（接口未接入）」与「已关闭」在 UI 上像素级同色（medium，撞 honest-degradation 铁律，我亲验）

```
notebookWorkbench.css:216 .nbw-chip-off     { color: #8c8c8c; background: #f5f5f5; }
notebookWorkbench.css:217 .nbw-chip-unknown { color: #8c8c8c; background: #f5f5f5; }
```
两个类逐字相同。ocr 把它定 low/可维护性，我改判 medium：本仓铁律是 simulated/未接入必须**可区分地披露**（`ALLOW_SIMULATION` / `is_simulation`），而这里「未知 = 接口没接」与「已关闭 = 真关」在屏幕上完全一样，等于把降级状态看成一个。修法：给 `--nbw-unknown` 一个可区分样式（虚线框 / 斜纹底），或在文案里强制带「未接入」字样。

### A229 — 日期区间两个输入框定宽 55px，选完值之后文字被裁（medium，我亲验行号）

`:158-159 .cds-audit-screen .cds-audit-filter .f-range .ant-picker-input:first-child { width: 55px; flex: none; }`，外层 picker 在 `:147`。55px 来由注释写得很清楚：原型**空值占位态**的墨迹宽（只够 4 字占位符）。但 `YYYY-MM-DD` 在 14px 下要 ~74-78px，input 无 ellipsis 能力 ⇒ 一旦真选了日期，值就被截断（筛选仍按完整值生效，但用户看不到自己选的什么）。叠 `flex: none` 不可压，i18n/长文案时整行顶出卡体。定 medium：不是功能错，是用户无法复核自己的输入。修法：`min-width` 给到 88px（picker 220px）并把 180px 只当≥1400 的参考宽。

### A231 — 整屏零断点 + 三处定宽/定尺寸，最钢的是弹窗 `width: 720px !important` 且全文零 `max-width`（medium，我亲验）

`grep -c '@media' taskDetail.css` = **0**（473 行）。钉死像素的地方：
```
:291 .t24-res      { grid-template-columns: minmax(0,1fr) minmax(0,1fr) 328px; column-gap: 26px }
:122 .t24-tile-row  { grid-template-columns: repeat(4, minmax(0,1fr)); gap: 16px }
:251 基本信息区      { grid-template-columns: repeat(3, minmax(0,1fr)) }
:466 .cds-t24-modal.ant-modal { width: 720px !important; top: 64px }   ← grep 'max-width' 全文件 0 命中
```
按仓内定标（1440 视口可用内容宽≈1088，再扣卡体 24×2）算了一遍：三列网格在 **1440 档不溢出**（前两列各得 ~330），要到 ≤1200 一档才把前两列压到 285 以下，而 `.t24-quota-box` 是 `justify-content: space-between`，值与单位会被顶到两端。真正无条件错的是**弹窗**：antd Modal 走 portal 挂 body，不受 `.cds-t24` 约束，`!important` 钉死 720px 又没有 `max-width: calc(100vw - 32px)` 兑底 ⇒ 视口 <720（分屏笔电、1024 缩放 125%）直接横向滞动，`top: 64px` 在矮屏还会把 footer 顶出屏。同样按用户 2026-10-18 的自适应口径，这里要写清「四列/三列成立的宽度区间」，而不是只加断点。

### A232 — 只读值盒与状态标签都没有溢出策略（medium，我亲验 JSX）

```
:263-272 .t24-field-box { display:flex; min-height:42px; padding:11px 10px }
:304-313 .t24-quota-box { … 同上 + justify-content: space-between }
```
两个盒**都没有** `min-width: 0` / `overflow` / `overflow-wrap`（逐行看过，不是只信 ocr 的片段）。本页值大量是后端原样下发的长串（任务号、合约号、notebook 路径），长 token 不换行会直接溢出 `#f7f8fa` 盒外。同理 `:t24-tag.ant-tag` 钉 `height:21px; line-height:19px; padding:0 12px` 且 antd Tag 默认 `nowrap`，碰上「部分执行失败」这类长枚举会与主标题抢行。另 F-36 我读了 JSX 确认：`TaskDetail.tsx:558` 把整句 `GRANTED_DATA_NOTE` 包在 `.t24-ht`（flex 不换行）里，而它的父级是 antd `.ant-card-head-title`（默认 `overflow:hidden; white-space:nowrap`）⇒ 宽度不够时整句注释被 ellipsis 吃掉且**无 tooltip 兑底**（`:611` 另一句在卡体内，不受此限）。同族的还有 `:t24-table td { height:48px }`：表格 `height` 只是最小值、无 `vertical-align`，内容一折行实际行高会撑到 72+，与「行高 48」的真值对不上。

### A233 — 状态标签与表内链接的可达性（medium，对比度我自己算的，数字与 ocr 不同）

不算 ocr 的数，用 WCAG 相对亮度公式重算：`#00a870/#e8f7ee = **2.78:1**`（ocr 写 ≈2.5）、`#f53f3f/#fdecee = **3.25:1**`（≈同）、`#1b6ef3/#e8f1ff = **4.04:1**`（**ocr 写 3.3，低报了**）、灰注 `#86909c/白 = 3.24:1`、`#86909c/#f7f8fa = 3.05:1`。三枚状态标签都是浅底同色深字、14px，全部不过 AA 的 4.5:1（进行中最接近）。叠加：`grep -c 'focus-visible'` = **0**，而且表内链接只重写了颜色——`.cds-t24 .t24-table a.ant-typography` 特异度 (0,3,1) 高于 antd 自带的 `.ant-typography:hover` (0,2,0)（antd v5 的 hash 类包在 `:where()` 里不计特异度），所以**悬停变色被一起压住**，鼠标/键盘用户只能靠光标形状判断可点。与 A221、A223 是同一族，修的时候一起修。

### A235 — 分页带的「右对齐」判据其实没生效（medium，两路亲验）

`DevTaskDataPicker.tsx:164` 把 `className: 'cds-task-pager'` 交给 Table 的 pagination（它直接落在 `ul.ant-pagination` 上），而 `node_modules/antd/es/pagination/style/index.js:436` 给它的是 `display: flex`；`devTaskCreate.css:232` 只写了 `text-align: right`——flex 子项不受 `text-align` 支配 ⇒ 「分页带右靠」这条像素判据在真机上并不成立（应该 `justify-content: flex-end`）。同族：分页开了 `size: 'small'`（`:163`），CSS 只钉了 `.ant-pagination-item`（`min-width:30px/height:28px`），全文 `grep item-link` **0 命中** ⇒ mini 档的前后钮与 `jump-prev/next` 尺寸没同步，页码与箭头高矮不齐。两条都是能直接看出来的。

### A246 — `/open/v1/${slug}` 不校验不编码（medium，我亲验）

`pages/Flow/display.ts:227-229 openApiPathForSlug(slug) => `/open/v1/${slug}``：slug 含 `/`、`#`、`?`、`..` 时会改变实际命中的路径（服务层已有的 `openApiCallPathForSlug` 做了同样的拼接，两处都没编码）。前端拼路径不能当安全边界，但也不该把用户输入原样注进 URL；修法：`encodeURIComponent(slug)` + 一个 `[A-Za-z0-9_-]+` 格式前置校验，两处共用一个函数。**补录（第七十六批）：同一机制共 8 处**，另处 `services/mpcApi.ts:47/53/58`、`services/fieldExposureApi.ts:55`、`services/k8sOpsApi.ts:106`（`/ops/logs/${sessionId}`）、`services/outputControlApi.ts:50`（查询串拼接，`session_id`/`epsilon` 都没编码，`&` 可以直接注入额外参数）与 `:53`。

### A249 — 审计详情对 `record.action` 直接 `.endsWith`（medium，叠 A244 就是白屏）

`pages/Audit/auditDisplay.ts:89`：`if (record.action.endsWith('_failed') || record.action.endsWith('.blocked') || record.action.endsWith('_rejected'))`——上一行 `:88` 刚对 `tab` 写了保护，这里却没有；详情接口或历史数据缺 `action` 字段时直接 TypeError。单独看只是本屏报错，但**全站零 ErrorBoundary（A244）**⇒ 一个空字段就足以把整应抓白。修法：`record.action?.endsWith(…)`，或者干脆复用 `auditActionLabel` 的入参校验。

### A252 — 真值表外挂：15 条真实路由的面包屑全变成「仪表盘 / 运营概览」（medium，我把两份表做了逐条比对）

方法：从 `App.tsx` 抽出全部 `path=` 声明（规范化前导斜杠后 **44 条**），与 `components/Layout/routeCrumb.ts` 里 `ROUTE_CRUMBS` 的 **26 条 prefix**（严格逐行解 `^\s*\{\s*prefix:`）做包含比对。结果：**15 条落到 `FALLBACK`**（`:70 prefix: '' → title: '运营概览'`, `segs: ['仪表盘']`）：
`/api-keys` `/capability-matrix` `/compliance-disclosure` `/data-pipeline` `/flow/guide` `/flow/quota` `/login` `/mpc` `/ops/deployments` `/ops/logs` `/ops/network-policies` `/ops/pvcs` `/register` `/system-status` `/users`
去掉 `/login` 与 `/register`（它们本来就在壳外），剩下 **13 个真实页面**打开后面包屑与页内标题都是「运营概览」——用户站在「密钥管理」上看到的是「运营概览」。消费方只有一个：`components/Layout/PageCrumb.tsx:19`（我 grep 了 `getRouteCrumb` 的全部引用）；并且 `grep -rn document.title src/` = **0** ⇒ 错的是页内那一行，不是浏览器标签标题，危害到不了 high。
**取证过程里我自己错了两次**：第一遍比出「42 条落空」是假的——`App.tsx` 写的是 `path="audit"`（无斜杠）而真值表写 `/audit`，规范化后才对；第二遍比出「0 条落空」也是假的——正则把 `FALLBACK` 的 `prefix: ''` 吃了进来（空前缀命中一切）。两次都是自写解析器静默交出差集，所以最终改成严格逐行解并**把语料规模（26 / 44）跟着结果一起打出来**。

### A256 — `approved_fields` 传空数组 = 批准全部申请字段（medium，后端权威方为真，当前 UI 不可达）

`app/api/field_exposure.py:287`：`approved = body.approved_fields if body.approved_fields else request.requested_fields` —— Python 空列表是假值，所以「审批人一个字段都没选」与「没传这个字段」不可区分，都被解释成**全量批准**（下面 `:289-291` 的子集校验也拦不住，因为 requested_fields 本来就是自己子集）。当前 shipped UI 不会发空集：`pages/DataProducts/ProductDetail.tsx:236-237` 要么发 `rejection_reason`，要么发 `approved_fields: request.requested_fields`（恒全量）⇒ 所以这一条现在不可达，但它是**API 边界的错**：任何另一个客户端（或下一个加“勾选子集”的 UI）一发 `[]` 就是静默提权。修法：`if body.approved_fields is None` 才回退，空列表应当要么 400 要么真的批准空集。

### A257 — 证书状态与验链结果都是 fail-open（medium）

`services/identityApi.ts` 两处：
```
:112-113 const expiresAt = new Date(cert.not_after);
          const status = Number.isNaN(expiresAt.getTime()) || expiresAt >= new Date() ? 'active' : 'expired';
:195      valid: data.valid !== false && !data.error,   （:196 chain 里同一表达式再来一次）
```
`not_after` 缺失/不可解析 ⇒ 判「有效」；后端响应体缺 `valid` 字段（返回 `{}` / `{result: …}` / 结构改版）⇒ `undefined !== false` 为真 ⇒ **报「验证通过」**。按本仓 honest degradation 铁律，“不知道”必须显示为未知，不能显示为通过（后端 `app/api/certificates.py:148-150` 直接 `return result`，没看到字段保证，所以不能当前端多虑）。修法：解析失败走 `unknown` 态（`SimulationBadge` 旁边就有现成的三态元件），验链只在 `data.valid === true` 时报通过。

### A258 — SM2 私钥由服务端生成并下发、明文入 localStorage；`signData` 带私钥上行但是死代码（medium，**ocr 两条 critical 我都降级**）

```
pages/Contracts/ContractDetail.tsx:57-60  // 注释原文：“服务端登记的公钥只由该端点写入……本地自生密钥对验不过”
                                          const keypair = await authApi.generateSM2Keys();  saveSM2PrivateKey(user.id, keypair.private_key);
services/authApi.ts:17-21                 generateSM2Keys(): {public_key, private_key, message};  api.post('/auth/sign-data', { data, private_key })
utils/crypto.ts:89/96/103                 localStorage.getItem/setItem/removeItem(`cds_sm2_privkey_${userId}`)
```
两条都**不是新发现**：`cds-frontend/AGENTS.md` 已把“SM2 私钥存 localStorage”列为容忍项（理由：后端会验签，且不得扩展到其它密钥）。仍值得记一笔的部分是：（1）密钥对由服务端生成 ⇒ 私钥至少在**服务端内存 + TLS 会话 + 任何反代/网关日志面**出现过，这比“浏览器自生 + 只上传公钥”多一个泄露面，而现有注释说明这是被服务端公钥登记契约锁死的设修；（2）`signData` 把私钥放在**请求体里逐次上行**，全仓调用方 **0 个**（`grep -rn 'signData'` 排除定义文件后空）⇒ 死代码，但留着就会被下一次“方便地”接上。修法：登记接口改成只收客户端上行的公钥，删掉 `signData`（签名本来就在本地做，`ContractDetail` 已经这么用）。

### A260 — CSV 预览先把整段按 `\n` 切行，引号内换行被拆成两条记录（medium，我逐行读了实现）

`utils/csvPreview.ts:60-76`：`splitCsvLine`（`:30-58`）本身是对的——逐字符扫 `quoted` 状态、`:38-40` 处理 `""` 转义。但调用方先把文本切行：
```ts
:63  const lines = trimmed.split('\n').filter((l) => l.trim() !== '');
:70  const columns = splitCsvLine(lines[0]);   :73  const cells = splitCsvLine(line);
:74-76 const row: Record<string, string> = {}; columns.forEach((col, idx) => { row[col] = cells[idx] ?? ''; });
```
两个后果：（a）`a,"line1\nline2",b` 这种 RFC 4180 合法行被切成两条记录，第二条从 `line2"` 开始，列序全错；（b）`:63` 的 `.filter(l => l.trim() !== '')` 还会把**引号字段内的空行直接删掉**（比拆行更隐蔽：内容平静少一截）。叠加（c）`:75` 用表头文本当 key：两列同名（导出里很常见）时后一列静默覆盖前一列，而 `columns.length` 看起来正常，很难排。修法：改成状态机整段扫描（一次 pass 同时处理换行与引号），列名重名时加后缀而不是覆盖。ocr 说的「`__proto__` 原型污染」我**不采**：`row` 是字面对象、写入值只能是 `string`，给 `__proto__` 赋字符串是无效操作，不是污染。

### A264 — 缺 `exit_code` 的执行：日志行记「成功」，状态芯片记「失败」（medium，两个函数逐行对过）

`pages/Flow/notebookWorkbenchModel.ts` 对同一个 `detail.exit_code` 用了两套语义：
```
:264 toneOf      → Number(detail.exit_code ?? 0) === 0 ? 'ok' : 'error'     ← 缺失当 0，判成功
:271 isProblem   → Number(detail.exit_code ?? 0) !== 0                     ← 缺失不计入问题
:328 lastExecutionFromLogs → typeof detail.exit_code === 'number' ? … : null
:331                      ok: exit === 0                                   ← 缺失得 false，判失败
```
⇒ 一条 `sandbox.execute` 只要没带 `exit_code`（审计写入失败/字段改名/旧数据），同一页上日志行是绿「成功」、顶部状态是红「失败」，而且它**不计入问题数**（`:271`）。在密态沙箱里把「没拿到退出码」当成「跑成功了」是方向错的那一边。修法：三处共用一个 `exitCodeOf(detail): number | null`，`null` 单独一档显示「退出码未记录」（旁边 `:337-339 formatDuration` 已有现成的「耗时未记录」口径可照抄）。

### A266 — 首页两处：`task_filters` 三连解引用 + `失败 ${ops.failures}` 会渲染出 undefined（medium）

```
pages/Flow/homeModel.ts:67-69  overview.task_filters.all / .active / .ended    ← 无可选链
pages/Flow/homeModel.ts:99     return `失败 ${ops.failures}`;                    ← 无 UNKNOWN 兑底
调用点 pages/Flow/DevHome.tsx:537 / :561（渲染里直接调，没包裹）
```
`task_filters` 缺失/改名就抛（叠 A244 零边界 ⇒ 白屏；`tsc` 拦不住是因为前端自己把它声明成了必填，这正是 A261 那类「前端类型与后端可空性不一致」）。`:99` 更轻但更难看：后端不返 `failures` 就渲染出字面量「失败 undefined」，而本文件自己的头注要求缺数一律走 UNKNOWN_TEXT。两处一起修：解引用加 `?.` + 计数走 `?? UNKNOWN_TEXT`。

### A267 — 「时间未知的记录」被排成最新（medium，同一行代码可验）

`notebookWorkbenchModel.ts:324`：`.sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)))` —— `created_at` 为空时 `String(undefined)` 得 `"undefined"`，而它比任何 ISO 串都大 ⇒ 降序排在**第一位**，`:325 latest = rows[0]` 就把这条无时间的记录当成最近一次执行（连同它的 exit/耗时/拦截标记）。修法：排序前先 `filter((r) => typeof r.created_at === 'string' && !Number.isNaN(Date.parse(r.created_at)))`，或把无法解析的排到最后。

### A272 — 审计验真在 Merkle 路径回来之前先亮绿（medium）

`components/Audit/AuditVerifyModal.tsx`：`:159 enabled = open && !!recordId` → `:168 anchored = !!verifyQuery.data?.tx_hash` → `:173 proofQuery enabled: enabled && anchored` → `:177 const verdict = deriveVerdict(verifyQuery.data, proofQuery.isError ? proofQuery.error : null)`。注意 `deriveVerdict`（`:37` 签名）只收两个参数，**根本收不到 proof 的成功/待定状态**：proofQuery 刚启用时是 pending，`isError` 为 false ⇒ `proofError` 为 null ⇒ 结论直接算成 `ok`，直到路径回来才可能变。弹窗于是会先显示一帧“验真通过”再可能改口。好消息是最终态是 fail-closed 的（`:73-78` 只要 `proofError` 非空就写「不作成功结论」，`:44` 无锥定给 `unanchored`）。修法：`deriveVerdict` 多收一个 `proofState`，pending 时给 `loading`/`degraded` 而不是 `ok`。

### A275 — 审计页：导出时间窗口是死的，而 Tooltip 对外声称「按所选时间窗口」（medium，按钮与文案都逐行看过）

```
:70   const EXPORT_RANGES = [7, 30, 90, 180];        ← 定义后全文件 0 引用（未 export）
:134  const [exportDays, setExportDays] = useState(30);  ← setExportDays 全文 0 调用
:171  mutationFn: () => auditApi.export({ days: exportDays, format: 'csv' })   ← 永远 30
:273  title={`按所选时间窗口导出 CSV（GET /audit/export?days=${exportDays}&format=csv）`}
```
一半是死代码（`EXPORT_RANGES` 永远不会执行、`exportDays` 不可变），一半是对外说供：页面没有任何时间窗口入口，Tooltip 却说「按所选时间窗口」，而且把**内部接口路径与查询串直接写给用户看**（`:273` 是 `title`，鼠标悬停就看见 `GET /audit/export?days=…`）。公平地说，`:170` 的注释表明作者故意不把列表筛选传给导出（「传过去只会让人以为导出跟随了筛选」），所以误导面只限于「所选」二字与那个天数；修法：要么把 `EXPORT_RANGES` 接成真选项（顺手让文案变真），要么删掉常量与 state 把 30 写进文案，Tooltip 去掉接口路径。

### A276 — 同一张详情弹窗：导出按角色禁用，锚定/验真任意角色可点（medium，权限门控不一致）

`AuditLog.tsx:136` 算出 `canExport = hasAnyRole(user?.role, ROLE_GROUPS.auditReaders)`，`:281` 给导出按钮上了 `disabled={!canExport}` + `:272` 的说明文案；但同一个详情弹窗里的 `:408 存证验真` 与 `:413-419 锚定本条`（`onClick={() => anchorMutation.mutate([detail.id])}`）**没有任何角色判断**，越权时只能在 `:166 onError` 里吃一个 `message.error`。锚定是写链操作、验真是取证操作，比导出的权限级别只会更高。后端仍是权威方（所以不是越权洞），但同一屏上“一个按钮会预先禁用、一个只能撞错”在合规审审眼里就是门控不一致。修法：同一套 `can*` 上到两颗按钮（`ROLE_GROUPS` 里已有现成分组）。

### A279 — Merkle 叶子的原文也是 `|` 直接拼接，且在审计链路上重复了 4 次（medium，A255 同族但更靠近证据基座）

`app/api/audit.py:358 / :421 / :476 / :493` 四处一字不差：
```python
data = f"{r.id}|{r.action}|{r.resource_type}|{r.resource_id}|{r.created_at}"
record_hashes.append(crypto_service.sm3_hash(data.encode()))
```
四处一致所以 `/anchor`、`/verify`、`/merkle-proof` 互相能对上（这是对的），但**字段之间没有转义也没有长度**：`action="a|b", resource_type="c"` 与 `action="a", resource_type="b|c"` 会拼出完全相同的 SM3 原文。本仓的防篡改链就建立在这些叶子上（Merkle 根由 `contracts/AuditRegistry.sol` 锥定，而那里 `anchorRoot` 无访问控制）。诚实估计可利用性：`action` / `resource_type` 是应用内部常量、`resource_id` 多数路径下是 UUID，`created_at` 服务端生成 ⇒ 目前没看到写入通道；所以定 medium（不是 high），但修法很便宜且应当做：拼前拒绝（或转义）`|`，或改成长度前缀/canonical JSON；四处应该抽成一个 `merkle_leaf_preimage(log)` 函数，避免下次只改三处。

### A281 — 联邦同步目录被后端静默截到 100 条，前端既不透传 `limit` 也无分页（medium）

`app/api/federation.py:261-267`：`GET /federation/catalog/entries` 只有 `space_id` 与 `limit: int = Query(100, ge=1, le=500)`，**没有 offset**；`:274 .limit(limit)`；`:276-285` 返回的是**裸列表**（没有 `total`、没有 `has_more`）。而前端 `services/federationApi.ts:96` 写的是 `api.get('/federation/catalog/entries', { params: space_id ? { space_id } : {} })` —— 不透传 limit、也不暴露分页。后果：列表页只能看到最新的 100 条，而 UI 无法区分「一共就这 100 条」与「还有更多被切掉了」（按 `updated_at desc` 排序 ⇒ 被切的恰好是最旧的）。修法：前端至少传 `limit=500` 并在拿到整 500/100 条时显式提示「可能还有更多」；真正的修法要后端给 `total`/游标。

### A286 — `allkeys-lru` + 256mb 上限会让 Redis 驱逐会话与限流计数（medium）

`:21 redis-server --requirepass ${REDIS_PASSWORD} --maxmemory 256mb --maxmemory-policy allkeys-lru`。`allkeys-lru` 是对**所有键**失效驱逐，而本栈往 Redis 里放的正是会话、限流计数（`:185-188` 开了 `CDS_RATE_LIMIT_*`）与登录短态——内存打满时它们会被当缓存一样踢掉，表现为“随机掉登录 / 限流突然放开”，与密码是否为空是两个独立问题。修法：`noeviction`（戒掉写失败当错误处理）或给会话与计数分库/分实例。

### A289 — `tests/e2e_full_test.py` 会被裸 `pytest` 采集，AGENTS.md 的“never collected”判错了（medium）

`pyproject.toml:3 testpaths = ["tests"]`，且**没有 `python_files` 覆盖** ⇒ pytest 默认 `python_files = test_*.py *_test.py`，`tests/e2e_full_test.py` 恰好匹配后半段。文件里有 12 个 `def test_`，模块级 `BASE_URL = os.environ.get("BASE_URL", "http://172.21.0.2:30080")`（硬编码集群 IP），CI 只 `--ignore=tests/test_e2e_full_lifecycle.py`（`.github/workflows/ci.yml:97`）。⇒ 信息级全量跑里这 12 条会去探一个不存在的集群。ocr 说它会“拖垮整个采集”说重了（四处 `requests.` 调用都带 `timeout=`，所以是有界失败），真正的问题是：**AGENTS.md 关于“文件名不匹配 `test_*.py` 所以永不采集”的说法对 pytest 默认值是错的**，而这 12 条红属于环境缺失、不是代码缺陷——要么加 `python_files = test_*.py`，要么把它改名为非采集名并在 `ci/known-failures.md` 里记账。

### A290 — 验真弹窗把“失败”与“上一次的成功结论”同屏渲染（medium，A272 的放大器）

`components/Audit/AuditVerifyModal.tsx:204` 与 `:208` 是同层两个独立块：`{verifyQuery.isError ? <QueryErrorAlert …/> : null}` 紧跟 `{verifyData ? (结论区) : null}`。React Query 在**重取失败**时会同时保留上一次成功的 `data` 并置 `isError=true` ⇒ 屏幕上同时出现「验真请求失败」和上一轮的绿色验真结论；弹窗重开/手动点重试都能触发重取（`retry: 0` 只限制自动重试，管不住 refetch）。叠上 A272（`deriveVerdict` 收不到 proof pending ⇒ 验真先亮绿）就是：审计人员看到一个“报错但仍绿”的面板。修法：`isError` 时优先只渲染错误态，或把结论区门在 `!verifyQuery.isError` 上。

### A291 — 用 `proof_path.length === 0` 单独下“本批次仅一条”的肯定结论，而同一个响应里就有 `total_leaves`（medium）

`:291` 已经显示「本批次共 `{proofQuery.data.total_leaves}` 片叶子」（后端 `app/api/audit.py:519 "total_leaves": len(all_hashes)`，`:517 "proof_path": proof.proof_path if proof else []`），但 `:293` 的分支只看 `proof_path.length === 0` 就给出“本批次仅此一条记录，root 即叶子哈希”。后端多叶子却异常返回空路径时，这是一个**与 Merkle 结构相矛盾、但听起来很确定的结论**，而它出现在存证/合规页面上，审计人员会据此跳过独立复核。修法：双条件 `total_leaves === 1`；空路径且 `total_leaves > 1` 时要渲染成“证明数据不完整”而不是结论。

### A297 — 数据面横幅绕过 services 边界，而这个绕过就是 A243 的成因（medium，修 A243 的正确入口）

`components/Layout/DataplaneBanner.tsx:26` 直接 `api.get('/meta/capability-matrix')`，而 `services/metaApi.ts:77-78` 早就有 `metaApi.getCapabilityMatrix(): Promise<CapabilityMatrix>`（共享实例的拦截器已经把 `response.data` 拆了一层，所以这个封装直接返回矩阵本体）。组件自己拿 axios 又写了 `((resp.data ?? {}) as { dataplane?: … }).dataplane`（`:27`）—— 多拆一层 ⇒ `dp` 恒为 `null` ⇒ 横幅永不渲染（就是 A243）。⇒ 修 A243 不是加个 `?? resp`，而是改调 `metaApi.getCapabilityMatrix()`：一次同时消掉“约定违反 + 双重解包 bug”。

### A298 — 探针失败完全静默 ⇒ “控制面档”横幅消失，与“完整档”不可区分（medium，与 A294 同根）

`:29-31` 的 `catch(() => { /* 探针不可达时保持默…… */ })` 不记录任何东西，`:35 if (!dp || dp.enabled) return null` 又把 `dp === null`（没拿到）与“数据面可用”归成同一个渲染结果。后端对这块写得相当认真：`app/services/capability_matrix.py:309-317` 专门声明“只陈述事实、不改变任何执行行为”、“节点不可达那条路径由 `RUNTIME_UNAVAILABLE` 自己披露，不在此掩盖”——结果前端把“拿不到披露”也归入“不显示”。控制面档部署遇到 401/网关/网络故障时，横幅消失 ⇒ 用户与运维都当成“完整档”。产品决策（不叠加错误噪声）可以保留，但至少留一条 `console.warn` / 指标，并把“档位未知”当成一个可渲染的态（与 A294 的“未拿到审查披露”一起收）。

### A303 — 其余副本携带的同类口令缺陷（medium）

`docker-compose.middleware.yml:16,51,53,67,68,127` 用 `${VAR:-CdsSecureP@ss2026}` 把**已提交的兜底口令**写进仓库（postgres/redis×2/minio×2/clickhouse）：缺 `.env` 条目时不是空口令而是仓内公开可读的口令，与 A283 同类但更难发现。`k8s/secrets.yaml:15-27` 明文入仓（自带 DEV-ONLY 横幅，AGENTS.md 已记“必须轮换”）。不受影响：`helm/cds/values.yaml:41-43`（有意留空 + `existingSecret`，缺 key 则 Pod 起不来）、`deploy/local/local_deploy.sh:13-27`（随机生成到 `/tmp/cds-local.env`）。

### A305 — `#root` 为空时 `createRoot` 静默白屏（medium）

`cds-frontend/src/main.tsx` 对 `document.getElementById('root')!` 非空断言 ⇒ 空 `#root` 时零 DOM 信号白屏。与 A244 无关：`ErrorBoundary`（`App.tsx:76`）包的是渲染树，启动期 `createRoot` 失败不经过它。

### A306 — 未认证跳登录丢了 `state.from`，登录后必回 `/`（medium）

`ProtectedRoute` 的 `<Navigate to="/login">` 不带 `state.from`，而 `Login.tsx:41` 总是 `navigate('/')` ⇒ 深层链接丢失。

### A307 — MainLayout 内无 `<Suspense>`，冷加载把页头+边栏一起换成 spinner（medium）

全 `src` 只有两颗 `<Suspense>`（`App.tsx:77`，在壳外）；`MainLayout.tsx:61` 没有 ⇒ 懒加载路由切换时整壳闪烁。

### A313 — 连接器列表在前端被截到 100 条且游标丢失（medium）

`connectorApi.ts:71` 游标被拦截器吃掉，`Connectors/index.tsx:276-282` 又 `pagination={false}` ⇒ 第 101 个连接器在 UI 上不可见且无截断披露。

### A316 — 激活/终止失败吞掉后端原因（medium）

`ContractDetail.tsx:92,98` 固定文案「激活失败/终止失败」，不取 W2 `message`/`detail` ⇒ 运维无法自服务。

### A317 — 数据产品台账只显 20/N 行且无说明（medium）

无截断 caption（与 A281 联邦目录同一形状）。

### A318 — `trust_level` 健度里硬编码 `延迟: 0 ms` 当测量值展示（medium，命中禁伪造）

健康字段重命名后仍把常量 0 ms 当实测延迟展示。


## 9. Low（36 条）

### A67 — 中间件与 deps 的凭据优先级相反（low，自我伤害型 403）

`main.py:259-260` 在 `Authorization` 不是 key 时把它**替换**成 `X-CDS-API-Key`；`deps.py:194-196` 相反，`credentials` 优先。⇒ 人 JWT + 任意残留 key 头的请求会在未映射路径上被当 key 拒 403（路由层本会按人处理）。修法：`if not auth and header_key.startswith("cds_ak_")`。

### A170 — 模型层的小额真实项（low）

同前缀重复建索引（`webhook.py:33 index=True` 与 `:28` 的具名 `Index`、`watermark_record`/`webhook_delivery` 同型，在 `alembic/versions/0019:211-212` 真的物化了两次）⇒ 写放大与噪声，删除需新 revision（链是 add-only）；`api_endpoint.calls_used`/`quota_total` 与配置列同表且无行锁（属 A168 的根）。`mpc_key`/`compute_run`/`blockchain_anchor` 的 15 条均为潜伏或假（表/列无生产读写方，或列已在新 revision 里）。

### A173 — SDK `_parse_error` 只接 `ValueError`，非 dict 的合法 JSON 直接 `AttributeError`（low-medium，活路径）

`sdk/cds_sdk/client.py` `_parse_error`：`payload = resp.json(); detail = payload.get("detail", payload)` 共在一个 `try` 里，而 `except` 只抓 `ValueError`（= JSON 解析失败）。服务端/网关返回合法但非对象的 JSON（`[]` / `"error"` / `null`）时 `payload.get` 抛 `AttributeError` ⇒ 调用方拿到一个与业务无关的崩溃而不是带状态码的 `CDSApiError`，真实的 4xx/5xx 信息被吞（W2 契约里“`SESSION_TERMINATED`=410 必须终止重试”这类判定也就失效了）。下游 `code=... if isinstance(payload, dict)` 倒是防了，唯独最关键的 `detail` 那行没防。修法：先 `resp.json()` 进 try，再 `isinstance(payload, dict)` 分支取 `detail`。

### A174 — 文档漂移：alembic 实际 head 已到 **0026**，AGENTS.md 与多处文档仍写 0021（low）

`ls alembic/versions | tail` 给到 `0025_notebook_data_resource_ids.py` / `0026_notebook_description.py`；models 子代理另补：0022–0026 只加了 compute_run params / notebooks 列 / bundle 展示列，**没有任何一条补上被评审声称缺失的列或索引**（所以 A171 这类缺约束项确实还没人修）。项目 KB 里“single LINEAR chain 0000→0021”现在只是历史口径，引用前先看目录。

### A207 — `sdk/pyproject.toml` 其余五条：依赖无上限、无 `py.typed`、版本双来源（low，打包卫生）

① `:6 dependencies = ["httpx>=0.27.0"]` 与 `:11 stream = ["websocket-client>=1.6"]` **只有下限**，而 `client.py` 直接依赖 httpx 的 client/异常接口，httpx 还在 0.x（允许破坏性变更）⇒ 建议 `>=0.27,<1.0` / `>=1.6,<2.0`。② 包里**没有 `cds_sdk/py.typed`**（`ls cds_sdk/` → `__init__.py client.py stream.py`）——而这个 SDK 对外自称 typed client，没 `py.typed` 下游 mypy/pyright 拿不到类型。③ 版本双来源：`pyproject.toml:3 version = "0.3.0"` vs `cds_sdk/__init__.py:22 __version__ = "0.2.0"`（即已记的 A154 / AGENTS.md 里的漂移条目，修法：`dynamic = ["version"]` + `version = {attr = "cds_sdk.__version__"}`）。④ `:15 requires = ["setuptools>=68"]` 无上限，把①的构建行为拖在一个浮动点上。⑤ “未装 `websocket-client` 时 `stream_exec` 抛 `CDSError(501)` 而不是裸 `ImportError`”这条降级路径本身是对的（`stream.py::_import_websocket`），但没有回归用例钉住（ocr 说只有 `tests/test_sdk_client.py`）——属测试空白，不是缺陷。

### A210 — 「配额列小字」靠 `nth-child(3)` 定位，背地里也打到了另一张卡的 `export_mode`（low，可维护性）

`:24-25` 的注释自述意图是「配额列小字」，但实现是位置选择器 `:26 / :32 … > *:nth-child(3)`。本页有 3 个 trio 容器（`DevTaskCreatePage.tsx:199 / :233 / :269`），所以每个 trio 的第三子都中招：`:233` 那张（合约卡）的第三子就是 `:252 export_mode` 的 `Select`，同样被写入 `font-size:12px; line-height:20px`。**危害边界要说准**：antd 给 Select 本体与 `.ant-form-item-label > label` 自带 `font-size`，所以这张卡上基本看不出差异（我不会把它当成像素缺陷）；真缺陷是**语义挂在位置上**：字段顺序一调、或某张卡改成两列，12px 静默换人，不报错、只能肉眼回归。修法：真正的配额列挂 `cds-trio-note`，布局与字号两个类分开。

### A215 — SDK 把标识符直接拼进请求路径，不做百分号编码（low，我亲验：全文件无编码调用）

`sdk/cds_sdk/client.py` 有 **64 处** `f"/…/{x}"` 形式的路径拼接（例：`:304 /api/v1/sandbox-sessions/{session_id}`、`:356 …/terminate`、`:360 …/execute`、`:368 …/exec`），而我 `grep 'quote|urlencode|quote_plus'` 在 `client.py` 与 `stream.py` 里**只命中一处无关的 `shlex.quote`**（`stream.py:58`，那是拼 shell 命令用的）⇒ 路径段确实未做任何百分号编码。当前由服务端 UUID 兑住（UUID 不含需转义字符）⇒ 只有当 ID 变成可含 `/`、`?`、空格的形态（如 slug）才会碎，定 low。

### A217 — `ALEMBIC_COMPARE_TYPES` 只认字面 `"1"`：写 `true` / `yes` / `on` 都是**静默关掉**类型对比（low，我亲验）

`alembic/env.py:42 COMPARE_TYPES = os.environ.get("ALEMBIC_COMPARE_TYPES", "1") == "1"`。默认开，但取值不是布尔语义：`1` 以外的一切（包括语义上「开」的 `true`/`yes`/`on`）都把类型对比关掉且不报错。OCR 原报把方向说反了（它说写 `false` 会续涌类型误报），子代理用这一行证伪了它；但**反向的坑是真的**：下一个人把 `=0` 改成 `=true` 想「打开严格模式」，得到的是更松。CI 现在写的是 `=0`（`.github/workflows/ci.yml:45`，旁边有注释），与 `:37-41` 的注释口径一致，所以不是现行缺陷，是未来错用的坑。修法：`in ("1","true","yes","on")` 或者直接用 pydantic 的布尔解析。

### A218 — `stream_exec(timeout_seconds=…)` 不做校验，直接把 `ValueError` 丢给用户（low，我亲验行号，顺手修正了子代理的坐标）

`sdk/cds_sdk/stream.py:135-136 if timeout_seconds is not None: frame["timeout"] = int(timeout_seconds)`（子代理写的 `:130-131` 偏了，那里是 `:128-130` 的 `except Exception → CDSError(502, websocket connect failed)` 转换块，两者不同）。后果：`timeout_seconds="30s"` 抛的是裸 `ValueError: invalid literal for int()`，而同模块对空代码是 `CDSError(400, "command cannot be empty")`（`stream.py:53-55`），对外契约不一致；`0` / `-5` 不校验直接发给服务端。修法：同模块已有的 `CDSError(400, …)` 路径上补一个整数与下限校验。

**复审结论**：剩下五条 FALSE 我逐条读了证据，接受：E2-1（`poolclass=pool.NullPool` ⇒ 引擎不持有连接，无泄漏）、E2-19（`sa.text("false")` 就是本仓声明的惯例，注释已记 SS-14 D-e2e-08）、E2-71（SQLite DDL 事务性 ⇒ `batch_alter_table` 失败整体回滚，不会半重建）、E2-75（子代理跑了真 SQLAlchemy 实验：`{}` / `null` / 从不赋值 三态在 `IS NULL` 上可区分）、E2-90、E2-100（`detail.get("code")` 为 `None` 时短路，产生不了 `"None"` 字符串）。E2-107 不是 FALSE，就是上面的 A218。

### A225 — 同文件卫生小项族（low，但 F-71 那一半是潜伏）

- **两套红 / 硬编码重复**：`:136 #c0392b`（`.nbw-tree-error`）vs `:274 #cf1322`（`.nbw-run-error`），而 `:7` 已声明 `--nbw-muted: #8c8c8c` 却在 `:216/:217/:144/:166` 反复写死字面量。
- **`:295-296` `white-space: pre-wrap` + `word-break: break-all`**：日志里的路径 / URL / 异常名会被从中间切断，`pre-wrap` 已能处理换行；改用 `overflow-wrap: anywhere`。
- **`:133 .nbw-tree-error { padding: 0 8px 6px 30px }`**：30px 是对齐文件树缩进的魔数（TSX 侧是 `paddingLeft: 6 + depth * 14` 内联算），两处一改就静默错位。
- **`--nbw-*` 只声明在 `.nbw-root:5`（潜伏）**：一旦这些卡片被规进 antd Modal/Drawer（portal 挂在 `document.body`，在 `.nbw-root` 外），`var(--nbw-border)` 解析失败会使整条声明被丢弃。**但我查了当前渲染面**：`NotebookWorkbench.tsx` 里 27 处 `nbw-` 全在页面主体，弹窗区（`:1274-1420` 的 `CreateNotebookModal`、`:269` 的 `Modal.useModal()`）**一处也没用 `nbw-` 类** ⇒ 按铁律记潜伏，不是现行缺陷。加兑底值 `var(--nbw-border, #eceff4)` 或直接挂 `:root` 就不靠这个约定了。

### A227 — 同一屏的 antd 内部类覆盖族（low，可维护性）

我逐行对过了，ocr 列的六个点都是真存在但危害被说大了：
- **两处 `> .ant-card-body { padding: 24px }`**（`:6-8`、`:11-13`）重复魔法值 24，依赖 antd 内部类名与注入顺序——顺序博弈我**没实测**，但重复值与内部类耦合是真的；应收拢到一个 `--cds-card-body-padding`。
- **`:41-43 height: 35px`** 不在 antd 档位（token 32 / lg 40）且 `> .ant-btn` 打按钮组全体；顶部注释又要求「新建接口 h40」⇒ 同一屏 32/35/40 三档。35 是按真值 ink 带推的（注释写得很清楚），所以这是设上的债不是手误。
- **`:45-47 min-width: 95px`** 不封顶：文案变长（英文/带图标）就撑宽，注释里 1220 左缘对齐的推算没有任何自动兑底。
- **`:28 color: rgba(0, 0, 0, 0.88)`** 硬写 antd 默认 `colorText`，上暗色/自定义主题时不跟随。
- **`.cds-filter-field` 确实没有 `min-width: 0`**（`:21-25`），标签是 `flex:none; white-space:nowrap`（`:27-31`）——但危害要说准：父行开了 `flex-wrap: wrap`，所以正常情况是**整项换行**而不是横向溢出；真会溢出只有「单个字段自身比行还宽」这一种。
- **`margin-inline-start: auto`（`:33-36`）与 `flex-wrap: wrap` 确实共存在同一行容器上**：一旦字段换行，按钮组会独占一行右靠（“悬空”），与本文件自述的「按钮组与控件行同一像簇」相冲——但这是**条件触发**（筛选项变多/宽档之间），所以定 low 而不是 medium。

### A230 — 本屏的令牌拄贝与结构耦合族（low，含两处 ocr 说大了）

真存在：`:317-318` 把主色拄了一份 `#1b6ef3`（`index.css:7` 定义的就是 `--cds-primary: #1b6ef3`，同一支）；`:219 > tr:last-child > td` 没排除 `.ant-table-placeholder` ⇒ 空态多一条下边线；`:295 display: contents` 拆 antd 分页（键盘焦点与视觉顺序不一致，且该 `li` 自身 margin 失效）；墨色字面量全文重复 14 处，另有一支体系外 `#5b6472` 用在表头。
说大了的两处：① F-53 说“若是不同支蓝请在注释里写明差异与理由”——`:353` 注释**已经写了**「4×16 蓝条（x24..28 **#1f70f3**）」，它就是探值探出来的，不是抄错；② F-52 把 `:has()` 当兼容性风险——Chrome 105 / Firefox 121 已是多年前的基线，本仓没有其他 `:has()` 降级路径，不构成现实风险（真风险只剩「包装节点一多 `:has(> …)` 静默失效」那一条）。F-55（依赖共用类 `cds-detail-modal` 同时存在，无人测试）成立但属测试空白。

### A234 — 本屏卫生族四条（low；其中 ocr 当现行缺陷的一条实测为潜伏）

- **F-37 → 潜伏**：`height: 6px !important` 带 `!important` 而 `background`/`border-radius` 不带，一旦调用方传 `strokeColor`/`strokeWidth`（antd 写内联）就会输——但我查了唯一调用点：`TaskDetail.tsx:379 <Progress percent={0} showInfo={false} />`，**没传这两个 prop**（全文件 grep 0 命中）⇒ 现阶段不坏，按铁律记潜伏。
- **行高自相矛盾**：`.ant-progress { line-height: 6px }` 与 `.t24-mem-bar { line-height: 8px }` 可命中同一元素（前面的选择器已包含 `.t24-mem-bar.ant-progress`），后来人无法判断该信哪个。
- **`:459-462 .cds-t24 .ant-alert { font-size: 13px }`** 是页面级通配，会命中本页**所有** Alert（含以后新增的），本意只是微调其中几处。
- **`.t24-field-box` 与 `.t24-quota-box` 九行声明逐字重复**（仅差 `justify-content`）；`.t24-head-actions .ant-btn { height:38px; padding-inline:21px }` 同样是中文四字按钮量出来的定值（宜 `min-height`）。

### A236 — 卡头标题改成 `display: flex` 作废了 antd 自带的省略（low，我亲验）

`:83-87 .ant-card-head-title { display:flex; align-items:center; gap:9px }`——antd 在同一元素上的 `overflow:hidden; white-space:nowrap; text-overflow:ellipsis` 对 flex 容器不生效，而本页卡头副标题是整句（如「合约用于约束模型输出格式与导出通道」）⇒ 宽度不够时不是截断，而是把 `.ant-card-extra` 挤出去。另 `:300-301` 底部操作条 `padding: 16px 3px 16px 24px`（右内距 3px）叠加 TSX `:325` 的 `display:flex; justify-content:space-between` **无 `flex-wrap`** ⇒ 窄档/125% 缩定时左侧文案与「取消/完成」互挤。这两条与 A231/A232 同一病根：钉死值却没给成立区间。

### A237 — 本文件的债：全局作用域 + 测试钩子当选择器 + 两支品牌蓝（low，我逐条 grep）

- **全局作用域**：`.cds-mem-stepper`（`:156`）、`.cds-picker-side`、`.cds-quota-advice*` 都定在页面根类之外，而 Vite 打的是全局样式表。**当前无冲突**：这些类名全仓只出现在 `devTaskCreate.css` + `DevTaskCreatePage.tsx` + `DevTaskDataPicker.tsx` ⇒ 记为命名空间风险，不是现行缺陷。
- **`:224 [data-testid^='task-tab-']` 当样式选择器**（TSX `:271` 逐字对应）：测试重构改名（或删 data-testid）⇒ 药签直接退化成浏览器默认按钮，没有任何测试会因此变红。
- **两支品牌蓝**：`#1b6ff2` ×5 vs `#1b6ef3` ×3，另有 `DevTaskDataPicker.tsx:40 const PICKER_TAB_BLUE = '#1b6ff2'`（这半条 ocr 归给了 DevTaskCreatePage，实际在隔壁文件，结论不变）。差 1/255 肉眼不可辨，但改主题时必须逐处搜。
- **`:152 body .cds-dev-task-create … ::after { content: none }`**：`body` 前缀纯粹为抬特异性，且按整片 `.cds-unit-item` 关掉单位后缀。
- **已选面板无上限**：`grep -c 'max-height' devTaskCreate.css` = **0**，而 `DevTaskDataPicker` 把 selected 全量渲染成 `<li>` ⇒ 勾上百项时「关联数据」卡无限撑高。

### A238 — 注释与实现不一致两处，其中一处让设计好的 hover 态根本不存在（low，我亲验）

- **`:259 .cds-task-card { border: none }` + `:266-267 .cds-task-card:hover { border-color: #bcd4f6 }`**：`border: none` 把宽度归 0，只改 `border-color` 不会产生任何可见变化，`transition` 里的 `border-color` 也是空转 ⇒ 注释里想要的「悬浮蓝描边」从未被实现（hover 只剩阴影）。修法：基态写 `border: 1px solid transparent`（卡宽 380 的定标要把 `box-sizing` 算回去）。
- **`:380` 注释「三钮等宽满宽、高 36」 vs `:385 height: 32px`**：同一个块里真值注释与代码差 4px，后续按注释复核像素会得出错误结论（旁边那段注释又写「108×32」，36 是转录残留）。

### A239 — `auto-fill` 兼容档在窄端会溢出（low，结论同 ocr 但它的算术错了）

`:246-249` 在 `@media (max-width: 1023px)` 里把 `.cds-task-grid` 改成 `repeat(auto-fill, minmax(280px, 1fr))`——最小轨是固定 px，容器不够宽时不会收缩而是直接溢出。ocr 算的是「320 − 12 − 12 = 284」（得出 320 下刚好不够），但移动端断点里**两层都有 12 内距**：`index.css:573-574 .cds-content{padding:12px}` 与 `:577-578 .cds-content-inner{padding:12px}` ⇒ 320 视口可用宽≈**272**（带经典滞动条则 257），溢出带比我算的更宽（≲328，带滞动条 ≲343）。修法一行：`minmax(min(280px, 100%), 1fr)`。

### A240 — 全局样式的维护族（low，逐条 grep 过）

- **`data-testid` 当样式选择器 5 处**（`index.css` 内 `[data-testid^=]` 计数 = 5，另 `devTaskCreate.css:224` 1 处）：测试属性一改名，hover 位移 / 光标 / 焦点态静默消失，且没有测试会红。与 A237 同一病。
- **无背景/文本令牌**：`grep -c '#f0f5fb' index.css` = **5**（ocr 写 3，实际更多），且 `--cds-page-bg` / `--cds-bg` **全文不存在**（grep 0 命中）；另 #1d2129/#86909c/#f2f3f5/#f7f9fc 等数十处字面量——而文件头自己声明了「组件一律引用令牌」。
- **重复选择器块**：`:is(.cds-home, .cds-detail) .ant-card-head` 出现 **5** 次、`.ant-card-head-title` 出现 **4** 次（同一目标分多处覆写，计算值靠顺序）。
- **`:520-522 .ant-table-wrapper .ant-table { border-radius: 8px }`** 无裁切：全文 `grep ant-table-container` **0 命中** ⇒ 没有 `overflow: hidden`，而表头 `background: #f7f9fc` 铺成方角，圆角实际不可见。
- **`:719-720 [data-bar] { transition: height .25s }`**：`height` 不是合成属性，柱状图数值变动时每帧布局+重绘（多柱同时变则是连续 reflow）；改 `transform: scaleY()` + `transform-origin: bottom`。
- **`:877-879 ::after { content: 'G'; right: 34px }`**：伪元素单位不会被多数读屏软件稳定朗读也不参与复制，InputNumber 对辅助技术就是「无单位数字」；`right: 34px` 是按 ± 按钮宽硬编码的魔数。（后半条如实说明：我没能确认这个 InputNumber 到底有没有 `aria-label`——grep 只扫了一个目录就空，不当结论。）
- **F-26 的兼容性理由不成立**（同 A230）：`:has()` 在 `:201/:205` 是定标唯一实现没错，但 Chrome 105 / Firefox 121 早已是基线，不构成现实风险。

### A247 — 两条 ocr 报成 high、实测要降级（low）

- `components/Feedback/errorHints.ts:129-130 if (code && ERROR_HINTS[code])`：`ERROR_HINTS` 是普通字面量（`:19 Record<string, ErrorHint>`），确实会沿原型链命中 `toString`/`constructor`，但命中后拿到的是**函数**，后续读 `.title`/`.action` 只会得到 `undefined` ⇒ 后果是提示空白，不是执行/信息泄露。一行 `Object.hasOwn` 就关掉，但定 high 说重了。
- `package.json:13 "test:coverage": "vitest run --coverage"` 而 devDependencies 里没有 `@vitest/coverage-v8`（`grep -c coverage-v8 package.json` = **0**）——脚本确实跑不起来，但 `grep -rn 'test:coverage|vitest run --coverage' .github/workflows/` **空命中**，且 AGENTS.md 写明 vitest 不在 CI ⇒ 不是「CI 覆盖率阶段必然失败」，只是一个坏掉的本地脚本。

### A251 — 注册成功后不校验响应字段就写登录态（low，ocr 定 high，我降级）

`pages/Auth/Register.tsx:18-19`：`const resp = await authApi.register(values); login(resp.user, resp.access_token, resp.refresh_token)` —— 没校验就写态。后果范局限在「后端 200 但字段缺失 → 存入 undefined token → 后续 401 循环」，不会越权也不会绕过鉴权（token 仍需后端认可）；`:24-25` 对 `detail` 是数组的情况做了收窄，剩下没收的是 `detail` 为对象/数字的情况（Antd message 能渲染 object 以外的值，最多提示奇怪）。所以定 low；真要修就一行 `if (!resp?.access_token) throw …`。

### A253 — `trigger={null}` 把侧栏折回去就 Manual 展开不了（low，代码里自带权衡说明）

`MainLayout.tsx:36-40` 是 `collapsed={collapsed} onCollapse={setSidebarCollapsed} collapsedWidth={64} trigger={null}`，而 `Header.tsx` / `Sidebar.tsx` 里 `grep collapsed\|setCollapsed` **0 命中** ⇒ 确实没有任何手动展开入口。但它不是没想到：`:33` 注释原文「所以 `trigger={null}` 只拆钩子，breakpoint 自动收窄留着——**折到 0 菜单就没入口了**」，配套 `collapsedWidth={64}` 保证收起后图标仍可点。所以危害是“不能拉回宽态”，不是“菜单没了”，定 low。

### A254 — `antdReact19Patch.ts` 的两条都需要“容器复用”才成立 ⇒ 现阶段记潜伏（low）

```
:10  host.cdsReactRoot ??= createRoot(container)   ← 缓存在宿主上
:13-18 return async () => { await setTimeout(0); root.unmount(); }   ← unmount 后从不删 host.cdsReactRoot
```
表面看两条都对：缓存不清⇒复用宿主时会在**已 unmount 的 root** 上 render；延迟卸载⇒cleanup 与新 render 竞态。但我查到容器根本不是复用的：`node_modules/antd/es/message/index.js:82-84` 里 `message` 是**模块级单例**，首次调用 `document.createDocumentFragment()` 建一个持有者，`:91` 只 `reactRender` 一次，后续消息走 `newMessage.instance/sync` 不再过这个渲染钩子 ⇒ 本版本 antd 下两条路径都触发不到。**但补丁是承重的**（文件头写明不打补丁全站 212 处 `message.*` 静默丢失），所以修法仍旧便宜且该做：cleanup 里 `root.unmount(); delete host.cdsReactRoot;`。

### A263 — 按 id 取合约的 query 只挡 `null` 不挡空串（low）

`pages/Flow/useContractOptions.ts:56-60`：`enabled: contractId !== null`，而 `queryFn: () => contractApi.get(contractId as string)` —— 表单初值 / `row.contract_id ?? ''` / URL query 缺省都会送一个 `''` 进来，发出 `GET /contracts/`，拿到列表（或 404）再喂给 `toContractOption`。`queryKey` 里带的是 `''`，所以不会被缓存掩盖，每次渲染都会真打一发。修法：`enabled: !!contractId`。

### A271 — `normalizeKmsKey` 硬编码 `key_type: 'dek'` / `algorithm || 'SM4'`（low，当前不成立）

`services/identityApi.ts:98-106` 确实写死了两个默认值，但后端 `app/api/kms.py:142` 的键列表返回字段里**根本没有 `key_type`**（只有 `key_version`/`algorithm`/`status` 等），而那个端点列出的本来就是 DEK 集合 ⇒ ocr 说的“丢失 session/kek 类型”在当前 API 形状下无从发生。剩下真的一半是 `algorithm || 'SM4'`：**缺字段时编一个算法名出来**，与 A262（编时间）、A257（缺字段判通过）同根，属于同一个“中性兑底 vs 编造值”的改造（建议这三条一起改）。

### A273 — 路径拼接不编码：全仓 112 处（low，类级判定，计数来自机器枚举）

命令与语料：`grep -rn 'api\.[a-z]*(\s*`[^`]*${' cds-frontend/src/services cds-frontend/src/pages | grep -v encodeURIComponent` ⇒ **112 处**（前 24 条已逐条看过，全部是 `/resource/${id}` 形式，id 来自路由参数或上游响应）。这 112 处是一个仓内约定，不是 112 个缺陷，所以不开 112 条编号：绝大多数 id 由后端签发，拼进路径不构成注入。**真正要修的只有 id/参数可被用户输入控制的那几处**，已在 A246 单列：`outputControlApi.ts:50`（拼查询串，`&` 可直接注入额外参数，最优先）、`display.ts:227`（slug）、`k8sOpsApi.ts:106`（sessionId）、`mpcApi.ts:47/53/58`、`fieldExposureApi.ts:55`。统一修法：`services/` 里加一个 `p(seg)` 助手（`encodeURIComponent` + `[A-Za-z0-9_.:-]+` 前置校验），或直接在 `services/api.ts` 请求拦截器里校路径段，一处收干净比改 112 行现实。

### A278 — 登录页四条（low-medium，逐行验过）

1. **`:38 return JSON.stringify(e);` 把后端错误结构原样渲染进 `message.error`**（medium）。按 `docs/error-codes.md` 的 W2 契约，`detail` 是可以带结构化内部信息的，而它会被整串拼给一个**未登录访客**看。本仓现例就在眼前：`app/core/scopes.py:309-315` 抛的 `API_KEY_SCOPE_DENIED` 带 `detail={"group": …, "verb": …, "path": …}` —— 等于把内部路由与权限组名贴到登录页上。修法：走 `components/Feedback/QueryFeedback.tsx` 的 `getErrorMessage`（其他页都在用），只显 code+message。
2. **`:80 rules={[{ required: true, min: 8, message: '密码至少8位' }]}`**（low）——一条规则两个约束共用一个 message，async-validator 先跑 required，但自定义 message 会覆盖内置文案 ⇒ 密码**留空**也提示「密码至少8位」，用户分不清是没填还是填短了。拆成两条规则即可。
3. `:20 (await api.get('/health', { baseURL: '' })) as { capability?: { levels?: string[] } }` 与 `:23 health?.capability?.levels ?? []`（low）：强类型断言 + 静默兜底，后端把 `capability` 改名就是「芯片区域默默消失」，不报错也不说明原因（同 A257/A262 那根“不知道要显示不知道”的线索）。
4. `:26-29` `try { … login(resp.user, resp.access_token, resp.refresh_token); navigate('/') } finally { setLoading(false) }`（low）：不校验响应字段就写登录态——与 A251（`Register.tsx:18-19`）**同一个错**，不另开编号；`navigate` 在 try 里先跑、`finally` 再 setState 属于卸载后 setState，React 19 已不警告，也无实际危害。

**我自己猜错的一条（记下来免得下次重新推一遍）**：看到 `app/main.py:322 app.include_router(health.router, … dependencies=[Depends(require_group("audit"))])`，我推测登录页未登录访问 `/health` 会被角色门 401 ⇒ 能力芯片永远渲染不出来。读 `app/core/scopes.py:295-307` 后**不成立**：`require_group` 对非 API-key 主体直接 `return`（文档字符串写明 “User subjects: no-op”），且 `subject` 走 `_optional_subject` 允许匿名。同理 `Login.tsx:20` 显式传了 `baseURL: ''`，盖掉了 `services/api.ts:5` 的 `/api/v1` 前缀，所以也没有打到 `/api/v1/health`。ocr 这一条剩下的只有风格问题。

### A280 — 导出：任何 2xx 的 blob 都被当作 CSV 落盘，文件名与 Content-Type 是前端复刻的（low-medium）

`services/auditApi.ts:173-177`：`const blob = await api.get('/audit/export', { params: {days, format:'csv'}, responseType: 'blob' }); return { blob, filename: \`audit_export_${days}d.csv\`, contentType: 'text/csv' }`。两个后果：（1）**只看状态码不看内容**，网关/代理以 200 返回 HTML 登录页、或后端以 200 包一个 JSON 错误体，都会落成一个名为 `audit_export_30d.csv` 的 HTML 文件；（2）文件名与 `contentType` 完全由前端硬编码复刻后端 `Content-Disposition`（注释写了原因：共享拦截器把响应头丢了），形成一处**隐式跳仓契约**：后端改命名不会报错，只会静默产出与后端说法不一致的文件。修法：落盘前校一下 magic/首行（或至少 `blob.type`），文件名改成从 `Content-Disposition` 取（需要给这一个调用保留响应头）。

### A292 — 结论与披露字段来自两个端点，可同屏自相矛盾（low-medium）

`:177` 的 `verdict = deriveVerdict(verifyQuery.data, …)` 只看 `/verify` 的返回；而 `:180-186` 的 `backend/block_number/anchor_id/confirmed/tx_hash/merkle_root` 全部写成 `verifyData?.x ?? proofData?.x`。两个端点的披露不一致时（`/verify` 给 null 或 `pg-log`、`/merkle-proof` 给链上后端），结论按 A 口径、文案按 B 口径。修法：披露字段不从 proof 回退，或两者不一致时显式渲染“两路披露不一致”。

### A293 — `ErrorCard` 的按钮与告警两套口径（low）

`components/Feedback/ErrorCard.tsx:45-46`：`canRetry = status === 410 ? false : retryable ?? base.retryable`，而 `warning = base.retryable && canRetry`。调用方用 `retryable: true` 覆盖一个映射表里不可重试的错误（如 `VALIDATION_ERROR`）时，`:93` 的重试按钮会出来但 `warning` 不亮 ⇒ 视觉与行为脱节。定 low（口径不一致，不是功能坏），但两套口径应该汇成一处派生。

### A295 — 开发者落地页钉在旧 `/home`，而且测试把这个陈旧值当断言（low，属“假绿”家族）

`utils/workspace.ts:65 homeLandingPath` 返回 `'/home'`，而 `:15` 自己的注释写明“旧开发者首页 /home 已改为重定向到 '/'（SS-18）”，`App.tsx:107` 就是 `<Route path="home" element={<Navigate to="/" replace />} />` ⇒ 开发者登录要多走一跳重定向。真正要记的是第二层：**`utils/__tests__/workspace.test.ts:83 expect(homeLandingPath(UserRole.DATA_PROVIDER, null)).toBe('/home')`** —— 测试钉住的是已被推翻的行为，改实现一定红，红会被误读成“改坏了”。同文件 `:49 isWorkspaceHome` 的 `pathname === '/home'` 也是同一次变更留下的死分支。

### A299 — `Spin tip` 在 antd v5 独立用法下不渲染 ⇒ 加载文案从来没出现过（low）

`components/Feedback/QueryFeedback.tsx` 的 `CenteredLoading` 写 `<Spin tip={tip} size="large" …/>`，而 `cds-frontend/package.json:22` 锁的是 `"antd": "^5.29.3"`：v5 的 `Spin.tip` 只在 nest（包裹 children）/fullscreen 模式下渲染，独立使用不输出文案（并在控制台告警）。修法：改成包裹一个空容器，或自己渲染一行文案。

### A300 — `mode` / `reason` 是死字段，而后端把“为什么是控制面档”已经算好了（low）

横幅接口返回 `{enabled, mode, reason, note}`：`capability_matrix.py:320 enabled = bool(get_settings().CUBE_DATAPLANE_ENABLED)`、`:324 "reason": None if enabled else "dataplane_disabled"`。前端的 `DataplaneStatus` 声明了 `mode` 与 `reason` 却**从未读取**，`message` 把“当前部署为控制面档：沙箱执行不可用”硬编码在前端。后端已经把原因送到手边，接上就能把“为什么”说准确（也能挡住以后新增的 mode 取值）。

### A310 — `GET /data-resources/` 无分页，前端每页全量下载再 `slice`（low）

`app/api/data_resources.py:222-231` 无分页；`dataResourceApi.ts:35` + `DataPipeline.tsx:41`/`ProductCreate.tsx:37` 为拿 count 也付全量（含 `schema_fields`）。

### A311 — 上传走全局 30s 超时而后端上限 500MB（low）

`api.ts:6` 全局 `timeout: 30000`；`data_resources.py:20,151` 上限 500MB；`flow.ts:1477-1553` 已有逐调用放宽超时先例并被 `flow.test.ts:114-115` 钉住，`dataResourceApi.ts:45` 漏了。

### A312 — 删除接口有响应体但声明 `Promise<void>`，UI 无条件报「删除成功」（low）

后端返 `{deleted, resource_id, storage_deleted}`（`data_resources.py:294`），`storage_service.py:247-258` 确实会返 `False`（KMS 销毁还被 `except: pass` 吞），而 `ResourceList.tsx:40` 无条件 `message.success('删除成功')`。

### A319 — 文案错字「把行」→「把关」（low，两文件）


## 10. 潜伏（代码事实成立，但当前无可达入口——接线/重用时必须先修）（15 条）

### A73 — 已撤销的策略包继续做授权决策（**已降级→潜伏**，见第二十九批“A73 定级修正”）

- 读路径 `policy_evaluator.evaluate:61-66`：`select(PolicyBundle).where(contract_id == ...).order_by(version.desc()).limit(1)` —— **无 `revoked_at.is_(None)`**；全文件 `grep revoked_at` 零命中。而写侧确实存在：`contract_service.py:243` `bundle.revoked_at = datetime.now(timezone.utc)` ⇒ 撤销后该 bundle 仍是决策源（且是 version 最大的那一个）。
- 完整性门只护 `rego_source`：`:78` `expected_hash = sm3_hash(bundle.rego_source.encode())`，而 Python fallback PDP 的真实判据全部在未计摘要的 JSON 列上：`_evaluate_python` 读 `bundle.field_acl`（:140/:179）、`bundle.allowed_ops`（:163）、`bundle.sandbox_modes`（:171）⇒ 改这三列即可放开操作/沙箱模式而完整性检查永不动作。（OPA 分支只 `push_policy(..., bundle.rego_source)`（:116），两个 PDP 的保护范围本来就不一致。）另：`integrity_hash` 与数据同行自存储，能拦脏数据/单边篡改，拦不住能写库的人。

### A105 — 输出代理两条真值（潜伏：接线即 high）

`app_output_proxy.py:150-151` + `streaming_inspector.py:196`：按 4KB 字节切 chunk 后逐块 `decode(errors="ignore")`，跳 chunk 边界的 PII 永不命中、多字节字符被静默丢弃，而 `:178-180` 的全有全无式改写又把受损 body 写回；`app_output_proxy.py:134-136,209-212`：Content-Type 前缀白名单，头缺失/未列举就直接返回（零审查），一个头绕过泄露控制。不可达依据见第二十九批（`requirements.txt` 无 mitmproxy、`proxy_launcher.py` 零调用方），所以计潜伏而不是 high；**任何接线这两个模块的变更必须先修这两条**。

### A106 — 证书链校验与撤销族（潜伏）

`verify_chain` 从不验父签名、`verify_certificate` 用叶子自己的公钥验自己的签名（自签仹即信）、信任锚条件是“库里任意 `is_ca and issuer == subject` 行”、CRL 每次调用现生成一把一次性密钥签名、`revoke_certificate` 的会话终止失败被吞但仍记 `cascade.complete`。我亲读部分：路由与导入存在（`app/api/certificates.py:176,182`、`app/api/auth.py:491`、`app/services/result_verifier.py:202,227`）；**“`certificates` 表几乎无写入方（`issue_certificate` 无调用方）”是子代理的 grep 结论，我未独立复验**，故整族计潜伏：一旦有代码开始 `issue_certificate`，这些就是高危（证书体系形同自签即信）。

### A125 — policy_bundle 模型层两条 high 随 A73 一起降级（潜伏）

`app/models/policy_bundle.py` 的两条（“读取路径不过滤 `revoked_at`，合同 terminate 后 PDP 仍 ALLOW”、“`integrity_hash` 只摘要 `rego_source`，而 fallback PDP 读的是 `field_acl`/`allowed_ops`/`sandbox_modes`”）——它们的读侧锚点都是 `app/services/policy_evaluator.py`，而第二十九批已证实**该模块零生产导入方**（活的评估器是 `policy_compiler.py:462-492`，只看合约生效/级别/操作/时长/DP ε）。⇒ 两条均按 LATENT 计，与 A73 同处置。保留为真的一般性结论只有一条：**SM3 是无密钥摘要**，能写这一行的主体都能重算哈希，所以它只能防无意不一致，不能防有能力写入者（写侧完整性若要成立必须换成带密钥的签名/MAC）。

### A127 — stream_audit 三处并发/隐私缺陷，但模块零生产导入方（潜伏）

`app/services/stream_audit.py:195 stream_audit_agent = StreamAuditAgent()` 是全仓唯一对该模块的引用（grep importers → 0）⇒ 三条均 LATENT，接线即高：① `_window`/`_chunk_counter`/`_callbacks` 是实例可变状态却导出了模块级单例，`append`+驱逐+全量重算+`+=1` 非原子（`chunk_index` 可重复/跳号；`_fire_alerts` 遭历 `_callbacks` 时另一线程 `on_alert` 可拼出运行时错误）；② `ChunkResult.pii_found` 里的 `PIIMatch.value` 是**未脱敏 PII 明文**且随窗口整段保留，与同模块提供 `redacted_data` 的意图相悖；③ 量纲冲突：`pii_density = total_pii / total_chunks` 是“每 chunk 平均条数”，却拿去比“占比阈值”默认 0.1 ⇒ 窗口内出现一条 PII 就是 1.0 > 0.1，告警几乎常开。修法：接线前必须先把 value 换成 `[REDACTED:*]`、密度改按 `含 PII 的 chunk 数 / 总 chunk 数`、单例改每会话实例或加锁。

### A128 — k8s_client 的 RWX 默认 StorageClass 猜选与 exec 透传（潜伏）

`app/services/k8s_client.py` 只被 `app/services/k8s_sandbox.py:155` 导入，而 `app/services/AGENTS.md:8` 自己写明 `k8s_sandbox`/`k8s_client` 是 **LEGACY，仅在 `SANDBOX_LEGACY_ENABLED`/`LEGACY_L2_ENABLED` 后注册** ⇒ 默认生产姿态不可达。两条真值：① `_default_storage_class()` 无 default 注解时直接返 `classes[0].metadata.name`，紧接写 `accessModes: ["ReadWriteMany"]` 的 PVC ⇒ PVC 永久 Pending（provisioner 拒 RWX），恰好绕过函数自述的 fail-closed；② `exec_stream` 把 `command` 直接透传 pod exec（容器名定 `sandbox`），无入参校验/白名单——当前调用方传的是定长 `list[str]`，所以只有“上层将来拼接用户输入”时才成立，不把命令注入当现行缺陷。修法：只信带 default 注解且显式支持 RWX 的 class（否则报错），并在边界强制 `list[str]` + 可执行白名单。

### A131 — k_anonymity 只校验首行的准标识符存在性，缺失值归入同一个等价类（潜伏，隐私门可假通过）

`app/services/k_anonymity.py:95  missing_cols = set(quasi_identifiers) - set(data[0].keys())` 只看第一行，`:104  key = tuple(row.get(qi) for qi in quasi_identifiers)` 把后续行缺列一律读成 `None`（CSV 短行同理，空单元格为 `""`）⇒ 脏数据在 `(age=None, zip=None, ...)` 上聚成同一个等价类，可人为把小组凑到 k 以上，**k-匿名检查虚假通过**。可达性：AST 反向导入闭包显示本模块**零导入方**，活的 k-匿名是另一套实现 `app/services/output_inspection.py:435 check_k_anonymity`（`output_gateway.py:115` 只传递 rules 键名，不导入本模块）⇒ 计潜伏。但请记下一条结构问题：**同一个隐私门有两套实现， reviewed 的这套没人用**，与第二十九批 A73（两套 PDP）完全同族，接线前必须先定唯一实现。修法：逐行校验 QI 存在，缺失走 `evaluated=False`/fail，不要当合法泛化值。

### A132 — GPU-TEE 双胞胎：模拟器的证明报告不披露 is_simulation（碰铁律）、auto 分支不回退、SM4 密钥长度标签错（潜伏）

```
app/services/gpu_tee_runtime.py:63,75-76   class AttestationReport: is_simulation: bool = False; tee_backend: str = ""
app/services/gpu_tee_runtime.py:301-302     另一个实现会正确填 is_simulation=True, tee_backend="local_software"
app/services/gpu_tee_simulator.py:169-182   attest() 只填 tee_enabled=True / valid=True / driver_version="simulator-1.0.0"，**不填** is_simulation / tee_backend
:39-40  key: bytes  # SM4 channel key (32 bytes) + algorithm: str = "SM4-GCM"
app/services/gpu_tee_runtime.py:474-481,492-494  auto 分支依赖 :494 的 try-import 注册，失败则 raise ValueError("Unknown GPU-TEE implementation: simulator")
```
可达性：两个模块只相互导入，AST 闭包 `main.py reaches it? : False` ⇒ 现行不可达（接适配器之前先修）。但 `:169-182` 那一条直接撞项目铁律（模拟路径必须用 `is_simulation` 自我声明）：基类已经有这两个字段、同一个仓的兄弟实现也真填了，而模拟器的证明报告用默认值 `is_simulation=False`/`tee_backend=""` 配 `tee_enabled=True, valid=True, signature` 为空 ⇒ 下游无法与真 NVIDIA CC 报告区分，“simulation” 只出现在一行 log 里。修法：`GPUAttestationReport(..., is_simulation=True, tee_backend="simulator")`；`auto` 分支持平回退到 `local` 并告警；密钥字段改名为“AES-256 作为 SM4-GCM 软件等效”或删除“32 bytes SM4”的说法（SM4 密钥固定 128 位）。

### A133 — RLS 双子系统 `rls_engine` / `pg_rls_manager`（潜伏：子代理 36 条，我独立复验了可达性）

子代理报 36 条（20+16，含 1 条 critical：FORCE RLS 策略 `TO <session_role>` 单角色会锁死其他所有角色）。结论的根——**两者都不在活路径上**——由我自己重跑 `rev_imports2.py` 证实（不依赖子代理）：`pg_rls_manager` 直接/传递导入方均 NONE（连测试都不导）；`rls_engine` 唯一导入方是 `db_access_proxy`，而 `db_access_proxy` 自身导入方 NONE；`main.py reaches it? : False`。仓内文档也同口径：`docs/spec-implementation-analysis.md:494` “PostgreSQL-in-TEE 完整架构 … 未实现”。⇒ 36 条统一计潜伏，不逐条入表（子代理逐条判定与基线 `pytest tests/test_rls_engine.py tests/test_db_access_proxy.py tests/test_sprint4_services.py` → 50 passed 在 `verify-rls.md`）。接线前必修项取前三：FORCE RLS 角色锁定、策略下发与表名拼接、以及策略缺失时的默认开放语义。

**证伪（不计入条数）**：`rag_service.py:598-599` 的 critical “`repr()` 拼进生成源码 ⇒ 代码注入”不成立，三条反证：① 边界类型是真 `str`——`app/api/sandbox_tasks.py:73  generative_model_id: str | None = None`，FastAPI/pydantic 不会让 `bytes`/自定义 `__repr__` 对象送进来，而 `str` 的 `repr()` 是单行字面量（换行被转义），无法拼出“字面量 + 换行 + 表达式”；② 独立的 AST 门 `validate_rag_runner`（`rag_service.py:618-666`）要求每个参数都是 `ast.Constant`（`_RAG_QUERY` 还额外要求是 `str`）；③ 它随后用解析出的值重新 `build_rag_runner(...)` 并要求 `code == expected`（`:662-664` 不等即拒“tampered or stale”），任何注入字节都会在这一步断掉。真正需要的是防御性约束（对 `generative_model_id` 加格式校验），不是安全事故。

### A144 — scenario_engine 全部 11 条潜伏，接线即 critical（潜伏）

AST 反向导入闭包与子代理双重一致：`scenario_engine` 只被 `tests/test_scenario_engine.py` 导入，无路由、无服务导入方、`cds-frontend/src` 无引用，spec 存在但未实现 ⇒ 11 条全 LATENT。接线前必修首条：模板用关键字黑名单 + `str.format` 拼接而非参数化（`render_sql` 只生成字符串、不执行，所以现在不痛）；其余：先剥关键字再剥注释会静默篡改合法值、`max_output_rows` 靠 `"LIMIT" not in sql.upper()` 子串判、`forbidden_operations` 声明了但不执行、`allowed_operations` 从没被读、`table_count = join_count + 1` 等。列入 A133/A95 那张“接线前先决定接不接”的同一张清单。

### A149 — 三个未接线引擎共 38 条潜伏（潜伏）

`field_mask.py`（11 条：HASH 模式是无盐无密钥摘要、GENERALIZE 处处 fail-open、浅拷贝导致嵌套容器不脱敏且别名调用方数据等）、`alert_engine.py`（7 条：`evaluate()` 无去重/冷却反复告警、`_check_off_hours` 看墙上时钟而非事件时间戳、单例可变状态无锁与无界增长）、`cross_space_policy.py`（9 条：`export_result` 不在 `OPERATION_TRUST_MAP` 里导致默认规则的 VERIFIED 门被绕过、`min_trust_level`/`require_dp_budget` 是死字段、`expires_at` 从不检查、优先级语义反了）。可达性由我自己核对：活的脱敏链是 `output_security.mask_rows_by_field_rules`（`output_gateway.py:163-164`）；`alert_center.py:17` 只导入 `Alert` 数据类；`cross_space_policy` 除自己测试外零导入方。⇒ 与 A95/A131/A133/A144 归入同一张“接线前先拍板”的清单；其中 `field_mask` 的浅拷贝与 `cross_space_policy` 的信任映射缺失属于接线必碎项。

### A194 — 潜伏族定档（latent，代码事实成立但无生产入口——接线前必修）

- **`pii_ner.py` 14 条全 LATENT**：唯一 importer 是 `stream_audit.py:19`，而 `stream_audit` 在 `app/` 内 0 引用方（本轮 `rev_imports3.py` + `grep "import.*stream_audit"` 两路一致）。但缺陷真实且不是吹毛求疵：`ner_engine="off"` 实测仍产出 `layer=ner` 的命中却上报 `regex_only`（违反“披露实际引擎”的铁律）；重叠消解有三种丢法，实测各留下 25/12/30 个**明文 PII 字符**。一旦 `stream_audit` 接线，就是“逐行审查声称脱敏了但漏字符”。
- **`mia_probe.py` 10/11 LATENT**：`analyze_batch` 对全疑似样本算出 `risk_score=-498.5` 却判 `safe`（分母口径与 A159 同族），`loss=1.0` 精确复现 `ZeroDivisionError`。接线前需与 `memorization_check` 一起统一分母口径。
- **`output_inspection` 2 条 + `model_watermark` 2 条 LATENT** 已分别归入 A187/A192 的同一修法（入口校空集/零位）。
- 另：`#9`（DLP 绕过场景路由）与 `#28`（`output=None` 500）代码事实成立但**无现行入口**（四个场景都含 DLP ⇒ 今日无可观测差异；18 个调用点都保证 `str`），按“潜伏”归档，不占修复排期。

台账编号完整性（本轮自检）：A0→A194 共 193 条，**无重复 ID**；**缺号 A68 / A107** —— 早期批次里这两号被合并进邻条（当时的处置没写回编号），不重编号是为了不打断 `fix-order.md` 与正文里的 `A##` 互引；读到缺号请当“已并入邻条”，不是漏判。另：`claims-misc.md` 的 91 条是按模块整体判的（network_policy → A155/A156、agent_tools → A157/A158、opa_client → A160、memorization_check → A159、utils/watermark → A172、SDK → A0/A154/A173），不是 1:1 逐条，因此 misc 的“逐条去向”不能当覆盖度证据。

### A259 — 开发者菜单白名单命中即整节点放行（latent，当前靠一个命名约定在撑）

`utils/workspace.ts:83-94` 的 `prune`：`:89` 先把「无 key 且无 children」的结构节点原样保留，`:90` 是 `if (DEVELOPER_MENU_KEYS.includes(String(node.key ?? ''))) return item as T;` —— **命中白名单的节点直接 `return item`，不递归裁 children**。而 `:87-88` 的注释（CR N-2）说明作者想到了“白名单不能漏”这件事，但只处了 keyless 分组。我比对了两边：`DEVELOPER_MENU_KEYS`（`:10-24`）全是**带前导斜杠的路由**，而 `components/Layout/Sidebar.tsx` 里所有带 children 的分组 key 全是**不带斜杠的 token**（`data-products` `:53`、`contracts` `:75`、`sandbox` `:87`、`dev` `:111`、`results` `:116`、`output-control` `:137`、`audit` `:146`、`monitoring` `:158`、`k8s-ops` `:170`、`identity` `:184`、`sec` `:258`）⇒ 今天没有任何白名单项能当一个父节点，穿不过去。但**没有任何测试或类型锁住「分组 key 不带斜杠」这个约定**，下一个人把某个分组 key 写成 `/data-products` 就会整块子菜单泄露给开发角色（菜单可见性，不是鉴权：路由层还有 RoleRoute）。修法：`:90` 命中后仍走 `:91` 递归（`{ ...node, children: pruneChildren }`），让“命中”只授权这一层。

### A270 — useCrumbSegments 两条降为 low（latent）

`components/Layout/useCrumbSegments.ts:6-10`：`const key = segs.join('|'); useEffect(() => { setStack(key ? key.split('|') : []); return () => setStack([]); }, [key, setStack])`。（1）段名含 `|` 会把往返拆坏——但我把真值表里的段名全看过（`routeCrumb.ts` 全是中文词 + `OpenAPI导出`/`新建API` 一类），目前无一带 `|` ⇒ 潜伏；（2）cleanup 无条件 `setStack([])` 写在共享 store 上，同一 `key` 变化时顺序安全（cleanup 先于新 body），但**第二个消费者挂载/卸载会清掉别人的栈** ⇒ 也是潜伏（现在只有一个调用点）。都不是现行缺陷，修时换结构化 key（`JSON.stringify(segs)`）+ 按 owner 隔离即可。

### A282 — 远端空间标签靠 `name` 前缀 + `LIKE` 过滤，`%`/`_` 没转义（latent / low，我把自己推的危害调低了）

`app/api/federation.py:263-268`：`select(DataProduct).where(DataProduct.name.like("[remote:%"))`，带 `space_id` 时是 `like(f"[remote:{space_id}]%")` —— `space_id` 是**原样插进 LIKE 模式**的查询参数，含 `%` 就变成匹配所有远端空间、含 `_` 就匹配任意单字符。另外整个“远端空间”归属是编码在 `DataProduct.name` 的字符串前缀里的（不是独立列）——这两点都是坑。但我把自己的第一反应调低了：这个端点本来就没有“按空间授权”这回事（不传 `space_id` 就是全部远端条目），且它 `require_roles(ADMIN, OPERATOR)`，所以通配符注入**得不多任何本来看不到的数据**，也没法跨到本地条目以外。定 latent/low；修法：`LIKE … ESCAPE` 或改 `startswith` 比较（更好的是把 space_id 提成独立列 + 索引）。


## 11. 前提事实（不是缺陷，是下面多条的判据）（4 条）

### A0 — 前提事实：`require_group(...)` 对人类主体是 no-op（决定了下面多条）

`app/core/scopes.py:295-316` 的 docstring 原文：「**User subjects: no-op** (their existing require_roles role gates apply). Key subjects: need `<group>:<verb>`」。`app/main.py` 用 `dependencies=[Depends(require_group("infra"|"pipeline"|"gateway"))]` 挂的那批 router，**对 JWT 用户完全不设角色门**，只看各端点自己的 `Depends`。⇒ 判断某个端点谁能调，只能读端点体内联的判据，不能看挂载行。（ocr 因为 `code_search` 失效，整批都没看到这行挂载。）

### A269 — antdLocale 两条降级为 low（一层脱壳 + 模块加载期无校验）

`antdLocale.ts:11-14` 只脱一层：`inner = mod?.default`，仅当 `inner` 带 `locale` 字段才用 `inner`，否则原样返回。两条 ocr 指控（处多层包装、`zhCNModule` 为空时模块加载期就铸成错值）机制上都在，但**最坏后果就是文件头自己写明的那件事**：“全站 antd 内建文案静默回落成英文（空表 No data、分页 10 / page）”；而 `:17 const baseLocale = unwrapLocale(zhCNModule)` 与 `:21-23 {...baseLocale, Pagination: {...baseLocale.Pagination, jump_to: '前往'}}` 对 `undefined` 展开不报错，不会崩。至于 `{default:{default:{…}}}` 多层壳：本仓是 Vite 8 + rolldown，**我没构造出这一形态**，所以按 latent 记，不算现行缺陷。真要动就加一句启动自测：`if (!('locale' in baseLocale)) throw …`（错配在启动时炸比文案默默变英文好）。

### A277 — 审计页三条 low（都验到行）

1. `:162` `message.success(\`已锚定 ${r.anchored} 条记录, Merkle Root: ${r.merkle_root?.slice(0, 16)}...${height}\`)` —— 后端不返 `merkle_root` 时 toast 渲染出「Merkle Root: undefined...」：锚定确实成功了，但向用户报了一个不存在的根值。与 A262/A257 同根（缺字段该显示「未回传」）。
2. `:164` `invalidateQueries({ queryKey: ['audit-record', detailId] })` 取的是闭包里的 `detailId`：锚定 in-flight 期间用户关掉弹窗（`setDetailId(null)`）后，失效的是 `['audit-record', null]` ⇒ 刚锚定那条的详情缓存不刷新，Tx Hash 栏继续空着直到手动重进。修法：`mutate` 时把 id 随变量传进去（`mutate({ids, id})`），不从闭包取。
3. `saveBlob:115-123`：`URL.createObjectURL` 与 `:122 URL.revokeObjectURL(url)` 之间没有 try/finally，`anchor.click()` 或 `removeChild` 招异常就永久漏一个 Blob URL（反复导出大文件会累积）。一行 `try { … } finally { URL.revokeObjectURL(url); }`。

### A296 — 两个文件的其余条目（合并成一条，不刷编号）

- `resolveWorkspace` 只拦 `stored === 'admin'` 越权，不校验 `stored` 是否为合法枚举值（M-195，low）。
- 菜单树的 keyless 三连（M-196/197/317，low）：`!Array.isArray(node.children)` 对“children 是非数组真值”的历史数据会当叶子保留；无 key 的分组重建后 `key` 仍为 `undefined`（antd SubMenu 展不开）；`collectLeafRoutes` 又用 `String(item.key ?? '')` 把它坐实成**空串** groupKey，调用方常见的 `if (groupKey)` 判不到。当前菜单常量都带 key ⇒ 潜伏，但三处应该一起收。
- `sandboxApi.ts:… 'pending' | 'running' | 'succeeded' | 'failed' | string`（M-301，low-medium）：`| string` 让整个联合退化成 `string`，`op.status === 'succeded'` 这种拼错不会报错 —— 要的是去掉 `| string` 或改成 `(string & {})`。
- `FileDownloadBlockedError`（M-302）定义后从未被引用（`downloadFile` 抛的是临时交叉类型）——死类型，low。
- `list()` 的 `skip = ((params?.page ?? 1) - 1) * (params?.page_size ?? 20)`（M-300，low）：`page: 0` 直接算出负 `skip`，后端 `Query(0, ge=0)` 会 422（拦住，只是文案不友好）。
- `devGetTemplate(name)` 路径参数未编码（M-304）：已归 A273 类级判定。
- `!= null` / `== null` 多处（M-319）：TS 惯用法，但与本仓“禁宽松相等”的规范字面冲突 —— 列给规范裁决，不归入缺陷数。


## 12. 判为非缺陷 / 高估 / 潜在（不要再报）

### B-追加：`app/core/challenge_store.py`（ocr 定 high/阻塞 — 高估）

`consume()` 用的是 `redis.set(..., nx=True, ex=...)`（注释自己写了 “SETNX + TTL is the atomic first-setter-wins; no read-then-write race”），降级路径 `except Exception` 后每 30s 打一条 `[CHALLENGE] Redis unavailable — single-use challenge tracking degrades to this process only` 警告，模块 docstring 开头就声明了「Disclosed degradation, never silent」。ocr 说的“配置/权限错误也被当成网络抖动降级”属实，但这是**已披露、有界、有告警**的故意 posture（与 `core.cache`、fail-open 限流同口径），不是静默安全缺陷 ⇒ 归入高估。

### B-追加（第十批降级）

- **ocr 第 13 条「失败分支只 flush 不 commit ⇒ 状态/密钥/审计全部丢失」是错的**：create 会话的四个失败分支（provision 软失败 L522-541、attestation 拒绕 L566-582、密钥分发失败 L594-617、数据注入失败 L632-656）**全部 `return SandboxSessionResponse.model_validate(session)` 而不 raise** ⇒ `get_db` 正常提交，`flush` 不会丢。同一前提（get_db 会回滚）只在 raise 路径成立，所以只有 A34 那一条成立。
- **ocr 第 5/8/15 条（provision 抛异常留下孤儿）只部分成立**：`SandboxManager.provision`（`sandbox_runtime.py:2204-2217`）确实**无 try/except**，调用点 L505 先 `db.commit()`（注释说明是为了让 cubelet 回调能读到会话行），所以一旦抛异常就会留下已提交的 PROVISIONING 行 + 未回收的卷/DEK。但我逐段读了 `cube_runtime_adapter.provision`（L697 起）与前面几个适配器的失败路径，**它们都是返回 `{"error": ..., "status": FAILED}` 字典，窗口内无 `raise`** ⇒ 「适配器到底会不会抛」未逐一证实，记为结构性风险（建议同样包 try/except + to_thread 已在用），不当已证实缺陷上报。

### B-追加（第十一批降级 / 证伪）

- **`app/core/pagination.py` 的 critical（SQLite 精度不一致会漏行/重复）在两种方言上都不会发生**：`ORDER BY created_at DESC, id DESC` 与 SQLite 分支的 `strftime("%s", created_at)` 截断确实不同口径，但**四个走 `paginate_keyset` 的模型（connectors/contracts/data_products/sandbox_sessions）全部是 `server_default=func.now()`**，SQLite 下存的就是整秒（CURRENT_TIMESTAMP），而 PG 走另一个不截断的分支 ⇒ 前提不成立。真正的隐含条件是「哪天这四个模型改成 Python 侧 datetime 默认值（本仓已有先例：`app/models/alert.py:52` `default=lambda: datetime.now(timezone.utc)`）」才会踩。且 `tests/test_pagination.py::test_cursor_traversal_no_dup_no_miss` + `test_cursor_survives_mid_page_deletes` 正好钉的是这个性质。⇒ 降为低（潜伏陷阱，建议加一条带亚秒 created_at 的用例），不是当前缺陷。
- **`app/core/redis.py` 的「并发双 `from_url` 导致连接池泄漏」不成立**：`if _redis_client is not None` 与 `_redis_client = aioredis.from_url(...)` 之间**没有任何 `await`**（中间只有冷却期判断，无挂起点），单线程事件循环上不可能两个协程都通过 None 检查 ⇒ 不会出现重复创建/覆盖泄漏。真实的那一半是「ping 未成就写入全局」，已归入 A39。
- `app/models/app_credential.py` 的 `status String(32)` 无 CHECK/Enum 约束：事实成立（`CredentialStatus` 只在 Python 侧），但属一致性约束缺失一类，与 A17 同文件，一并修即可。

### B-追加（第十二批降级）

- `app/core/telemetry.py:71` 的「用畸形 `X-Request-ID` 可稳定让任意接口 500」**不成立**：HTTP 头在 ASGI 层是按 latin-1 解码的（h11/uvicorn），客户端无法投递码位 > U+00FF 的字符；latin-1 字母（如 U+00E4）确实能过 `isalnum()` 并被回显，但回显时 latin-1 编码必然成功 ⇒ 不会 `UnicodeEncodeError`；CRLF 也不可能出现在头值里。降为低（建议改 `^[A-Za-z0-9-]{8,64}$`）。
- `app/core/telemetry.py:96-105` 的 `status` 未绑：事实成立（`CancelledError` 是 BaseException，`except Exception` 接不到），但 `str(status)` 本身就在内层 `try/except Exception` 里（L104-107）⇒ UnboundLocalError 被吞成一句 metric WARNING，原始 CancelledError 照常传播。实际代价 = 客户端断开的请求少一条 metric + 一行日志噪声。降为低。
- `app/models/api_key.py:44` `scopes: Mapped[list] = mapped_column(JSON, ...)` 确实无变更跟踪，但 grep 全仓 `\.scopes`（api_keys.py / deps.py / scopes.py）**没有任何原地修改**：创建用 `scopes = list(payload.scopes)`，轮换用 `old.scopes` 重新构造 ⇒ 属潜在陷阱（建议 `MutableList.as_mutable(JSON)`），非现行缺陷。
- `app/models/chain_anchoring.py` 「`audit_record_id` 两套语义」是**文件头写明的设计**（L10-11：无显式 record id 的锚写一行、以 anchor id 自身为键），不是隐式不一致；真正的问题只是无唯一约束（归 A44）。`app/services/tlcp_service.py:164` 的 `datetime.now()` 是自签 CA 路径内 naive-naive 自洽运算，不属 A41 同族。

### B-追加（第十六批降级）

- `GET /trusts?status=Active` 确实会 500（`federation.py:96/101` `status: str | None` 直接 `FederationStatus(status)`，无 try），但既不泄数据也不绕鉴权，只破 W2 错误契约（应 422）⇒ **降为 low**（同文件 `:241` 就有 `pattern=` 的止确写法可抄）。未跑实请求，属阅读推导。
- `catalog/entries?space_id=%` 的「跳空间读」**危害不成立**：不带 `space_id` 时 `:269` 的 `like("[remote:%")` 本就返回全部镜像行，投影只有 id/remote_space_id/product_name/product_id/synced_at（`:277-283`）；`space_id` 是展示过滤器，不是鉴权过滤器。真正的问题只是 A59 那个把同一拼法用在 DELETE 上的破坏性变体，以及 `get_trust_score:140` 的 `COUNT(*)` 可以被 `space_id="%"` 刷满 data_quality（归入 A62）。

### B-追加（第十七批：ocr 三条 high 的判定）

- **第 3 条「改用 `X-CDS-API-Key` 就绕过未映射前缀的默认拒绝」不成立**：唯一调用方 `app/main.py:253-260` 在调 `key_unmapped_deny` **之前**就做了归一：
  ```python
  elif k == b"x-cds-api-key":
      header_key = v.decode("latin-1")
  # 文档化的数据面凭据（X-CDS-API-Key）同样是 key subject：默认拒绝必须
  # 看得见它，否则换一个头就绕过了未映射前缀的 fail-closed 判定。
  if not auth.startswith("Bearer cds_ak_") and header_key.startswith("cds_ak_"):
      auth = "Bearer " + header_key
  ```
  ⇒ `key_unmapped_deny` 的窄签名只是给调用方留的坑，不是现行绕过。
- **第 1/2 条「`path.startswith(prefix)` 把最长前缀匹配退化成裸前缀匹配」结构上成立，当前零影响**。逐字看 `resolve_group:75`：`if path == prefix or path.startswith(prefix + "/") or path.startswith(prefix)` —— 前两个条件确实是第三个的子集。但危害要的是「某条已声明前缀是另一条的裸前缀」，我把 `GROUP_PREFIXES`（12 组 / 37 条）与 `HUMAN_ONLY_PREFIXES`（5 条）全量解析后做了两两比对：**跳组裸前缀遮蔽 0 条，human-only 被组前缀裸吞掉 0 条**。
  ⇒ 当前的真实语义只是：一个只是「以某已映射前缀开头」的不存在路径（`/api/v1/data-productsXXX`）会被当成已映射而不走默认拒绝，但它紧接着在 FastAPI 路由上 404，拿不到任何访问。human-only 那一侧方向相反（`/staticx` 会被判 human-only ⇒ `resolve_group` 返回 None ⇒ 对 key 更保守）。
  定性：**潜伏陷阱，降为 low**。它只在全新加一条与现有前缀构成裸包含关系时静默变坏（例：往 `data` 组加 `/api/v1/data`）。建议：删掉第三个条件（`or path.startswith(prefix)`），并加一条不依赖文案的结构用例：断言（a）任意两个已声明前缀不构成裸前缀关系，（b）`prefix + "X"` 形式的 `resolve_group` 返回 None。
- 顺带一个**不是缺陷**的观察：`/api/jupyter/*` 不在任何 group 前缀里，所以 key subject 在开发环反代上天然 fail-closed（与 A23 的 Lab 可用性方向合并看）。

### B-追加（这一批证伪两条类型指控，不要再报）

- **`types/models.ts` 的「`allowed_operations` 两个模型类型不一致」不成立**：后端本来就是一个一个样——`app/schemas/contract.py:23 allowed_operations: str = "read,analyze"`（逗号串）、`app/schemas/data_product.py:22 allowed_operations: list[str] | None`。前端三个消费方全部对得上：`ContractDetail.tsx:184` 直出字符串、`Catalog/index.tsx:231` 用 `.join(', ')`、`ContractCreate.tsx:138` 提交前走 `joinOrUndefined(...)`。真把两个类型“统一”了反而会把合约侧的 `"read,analyze".includes('e')` 这种子串误判引进来。
- **`types/security.ts` 的「消费端无法区分通过/未执行/被拦」在当前代码里不成立**：`pages/Flow/display.ts:182-183` 写的是 `latestRun.passed === true` / `=== false`（缺失自然落到第三态 `null`），`:208` 用 `check.passed !== true` 收集失败项（fail-closed）；`NotebookWorkbench.tsx:570` 缺失时显示「有失败」，也是 fail-closed 方向。可选布尔的类型表达确实不够精确（值 `low`），但不是“消费端会把未执行读成通过”。

### B-追加证伪：`computeApi.ts` 的「900s 超时 + 重试 = 重复执行」不成立

链条断在重试根本不存在：`App.tsx:62-64` 写的是 `defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } }` —— **只给 queries 配了 retry，没给 mutations**，React Query 5 的 mutation 默认 retry=0，所以超时的 `api.post('/compute/runs'…)`（`services/computeApi.ts:42-50`，`timeout: RUN_TIMEOUT_MS`）不会被自动重发；`api.ts` 的 401 拦截器只重放 **401** 请求（服务端未执行就被拒），也不构成重复执行。真正残留的是“没有幂等键”：客户端 900s 超时而服务端还在跑时，用户再点一次就是第二次执行（现有护栏只有 `utils/highRiskOperation` 的人工确认）。定 low，修法：`/compute/runs` 接受 `Idempotency-Key`（或前端提交后禁用按钮直到出编号）。


## D. 修复状态（2026-10-18 修复轮 · 本机 3.12 `.venv-gate` 口径）

判据：只修**本机可验证**的（每组「改前红 / 改后绿」双读数都在盘上日志里）；拿不出本地读数的回退或降级为「待部署验收」。逐组明细见 `ocr-fixes.md`，映射与门读数见 `fix-order.md` 末节。

**已修（有本机双读数）**：A268（+分片读取审计留痕）、A294b、A279（含第 5 处 `cube_egress_audit.py:220`）、A283、A285、A243、A245、A298、A299、A274、A257、A281、A272、A290、A291、A275、A276、A277、A280、A278（并扩到同族的 `Register.tsx`，新增未编号项：注册页 `detail` 对象直入提示 ⇒ 红读数 `[object Object]`）、A261、A244、A252、A260、A289，另加显示层收尾（证书 `unknown` 标签）。

**放弃 / 回退**：A288（`.dockerignore`）——本机无镜像构建上下文可验，已按字节还原，移入待部署验收。

**登记未修（本机证不了，不冒充完成）**：A301（远程车道 fail-fast 漏项 + `CDS_DEBUG=true` 绕过十五道生产校验）、A302（`k8s/cds-app.yaml` 零卷 + `replicas: 2`）、A303（middleware 已提交兜底口令）；`/kms/audit` 默认 `limit=50` 的同类静默截断（A281 同族，改法要动 `KeyManagement.tsx`）；`/certificates/list` 忽略前端 `status` 参数。

**门读数（终局）**：后端 10 文件 `106 passed, 1 skipped / RC=0`；`compileall` RC=0；`verify_schema_parity.py` RC=0（54 tables，改动前后各一次）；前端 `tsc -b` RC=0（0 条 `error TS`）；前端 vitest 全量读数见 `fix-order.md`（本机必须带 `NODE_OPTIONS=--experimental-require-module`，属本机 jsdom 环境坑，CI 前端 job 不跑 vitest）。

**既存项（干净树 `D:/workspace/ocr-base` @452ffc6 复现，非本轮引入）**：`tests/test_p0_security_gaps.py` 5 条 `UnicodeDecodeError`（`read_text()` 未指定编码 + 本机 cp936）——基线同样 `5 failed, 10 passed`。
