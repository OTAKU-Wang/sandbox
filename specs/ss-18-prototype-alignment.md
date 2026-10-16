# SS-18 · 按原型收敛开发者闭环（前端 IA + 沙箱目录契约）

> 用户 2026-10-16 三条指令（原文）：
> 1. 「这个流程去做 上传数据集生成数据目录之后 都要按照我提供的流程去做」
> 2. 「在线的开发工具使用在线的 jupyter notebook 前端页面进行」
> 3. 「现在前端的功能实现没有严格按照原型实现」
>
> 权威文档：`data-sandbox-developer-portal-prd.md`（18 界面/双工作台）+ `prototype-v3.html`（视觉与文案）
> + `developer-portal-review-plan.md`（可执行权威层，P0–P6）。本文件是 SS-15/SS-17 之后的**收敛层**，
> 不改变已定架构决定（唯 CubeSandbox 单一路径、T5 出域铁门、W2 错误契约、诚实降级）。

## 0. 一句话诊断

后端闭环**已经跑通并在真 MicroVM 上验证过**（台账 V-14：浏览器里 `cds_kernel（有状态）`+退出码 0+stdout）；
不对的是两处**契约表达**：

1. **沙箱目录契约三套并存**，且没有一套是原型承诺的那一套（§2）。
2. **前端 IA 是接口驱动的**，不是原型的信息架构：侧栏 14 项平铺（`Sidebar.tsx:39-111`）、
   没有"开发任务"这个第一公民（`DevTasks.tsx:132-166` 是一张 `Table` + 一个两项弹窗）、没有任务详情页、
   三通道 Tab 命名与字段对不上原型、接口监控页根本不存在（§4）。

## 1. 业务闭环路径（代码真值 → 原型界面）

| # | 业务环节 | 真实端点（handler） | 现在的前端落点 | 原型要求的界面 |
|---|---|---|---|---|
| 1 | 上传数据 → 数据目录 | `POST /data-resources/upload` → `POST /data-products` → `submit` → `approve` → `publish` | `DataResources/`、`DataProducts/`、`Catalog/` | 保留（用户明确要求"上传数据集生成数据目录之后"才进入闭环） |
| 2 | 合约（提供方创建、双方 SM2 签） | `POST /contracts`（仅 `data_provider`）+ `POST /contracts/{id}/sign` + `POST /contracts/{id}/flow-config` | `Contracts/ContractCreate·ContractDetail` | 保留在管理态/提供方侧；开发者侧只**选**合约 |
| 3 | 建开发任务 | `POST /api/v1/notebooks`（`notebooks.py`）| `/flow/tasks` 弹窗只有 名称+合约 | `2-2 新增开发任务`：+ 调试方法 / 导出方式 / 调试内存 2–32G / 执行内存 2–64G / 关联数据双 Tab / 完成并进入调试 |
| 4 | 在线调试 | `POST /notebooks/{id}/dev-session`、`POST /notebooks/{id}/cells/execute` | `/notebooks` 卡片式 IDE | `2-3 调试环境`：状态栏（沙箱连接灯+CPU/内存）、目录树（`/sandbox/data` 只读锁标）、控制台 Tab |
| 5 | 固化发布 | `POST /workloads`、`/workloads/{id}/test`、`/workloads/{id}/publish`（门族 `WORKLOAD_TEST_REQUIRED` 等 5 码） | `Flow/WorkloadCenter` | `2-7 固化发布弹窗`：版本号 / 主入口脚本 / SM3 摘要 / 更新说明 |
| 6 | 生产执行 | `POST /api/v1/compute/runs`（`workload_id`/`params`/`resource_override`，0022） | `Flow/ComputeSchedule` 的 `ExecuteModal`（已按原型 P-01 建） | `2-5 执行确认弹窗`（已有）+ `2-4 任务详情`（缺） |
| 7 | T5 出域审查 | `delivery_service._review_output_bytes` → `OUTPUT_BLOCKED(502)` / 脱敏 / `skipped_binary` | `RunLogDrawer` 读 `resource_usage` 标志位 | `2-6 运行记录抽屉`：日志 + DLP 专区（PII/行数/水印/签名） |
| 8 | 三通道交付 | `POST /deliveries/dataset/bundle` + `/dataset/{id}/preview` + `/download`；`/deliveries/openapi/endpoints` + `POST /open/v1/{slug}`；`/deliveries/webhooks` + `/test` + `/deliveries` | `Flow/DeliveryCenter`（**已有三 Tab**：数据集打包 / API 交付 / 推送，`DeliveryCenter.tsx:1443-1462`） | `3-0 结果集资产中心`（Tabs）+ `3-1/3-2/3-3/3-4/3-5`；**缺一个后端只读端点**：`GET /deliveries/openapi/endpoints` 只到端点粒度，无逐次调用明细（`deliveries.py:750/803`），原型的调用表无数据源 |
| 9 | 审计与存证 | `GET /audit/records`、`GET /audit/verify/{id}`、`GET /audit/merkle-proof/{id}` | `Audit/AuditLog`（详情是 Drawer+`Descriptions`，缺字段） | `4-1 操作日志`（按 4 类操作筛选）+ `4-2 日志详情`（字段化）+ `4-3 存证验真` |

> 端点全部实测存在（评审计划第三章的 13 行对账 + 本轮 `/tmp/e2e_full_chain.py` 11 步 200）。
> **PRD 第四章的 `POST /workloads/{id}/run`、`DLP_BLOCKED` 是发明**，任何任务书禁止引用该表。

## 2. 沙箱目录契约收敛（本轮真正的"主流程"修复）

现状（唯 l2c 路径，实测读码）：

| 层 | 现在用的路径 | file:line |
|---|---|---|
| Cube 挂载表 | `/`(rw)、`/data`(rw)、`/results`(rw)、`/scratch`(rw) | `app/services/cube_runtime_adapter.py:302-310` |
| 授权数据落点 | 已发布产品的资源文件下载后写入数据卷 → 挂到 **`/data`（可写）** | `app/api/sandbox_sessions.py:460-490` |
| 会话文件/产物列举 | `/scratch` 硬编码（upload/download/list） | `app/api/sandbox_sessions.py:1643-1660` |
| 出口策略与场景运行时 | 讲 **`/sandbox/output`** | `app/services/code_scanner.py:103`、`vision_*`、`llm_sft_runtime.py:698` |
| 退役的 bwrap/firecracker 路径 | **`/workspace/output`** | `sandbox_runtime.py:132/1302`、`firecracker_runtime.py:493` |
| 代码扫描器 | 已出现**两种**输出目录口径 | `code_scanner.py:103` 与 `:118` |

后果（用户视角）：按原型口径往 `/sandbox/output` 写产物 ⇒ 成果交付是空的（实测 `bundles items: []`）；
`/data` 可写 ⇒ "原始数据不可改写"这条产品口径在实现上落空。

收敛契约（唯一执行路径 = CubeSandbox）：

```
/sandbox/data     只读   合约授权数据（注入副本；采集面不取它）
/sandbox/output   可写   唯一被 T5 网关采集的产物目录
/scratch          可写   工作区（内核 cwd、会话文件），兼容期仍参与采集
```

改动清单（一次做完，不夹带）：
1. `cube_runtime_adapter.py` 挂载表：数据卷 → `/sandbox/data` + `readonly=True`；结果卷 → `/sandbox/output`；
   `/scratch` 保留为工作区（改名 `/workspace` 会打断既有 exec 通道，风险大于收益）。
2. `_l2c_file_op` 的 list/download/upload、成果包取文件路径 → `/sandbox/output`（交付采集面），
   upload 仍写工作区，**不允许把上传文件直接当产物**（产物必须由执行产生）。
3. `code_scanner.py` 输出目录口径统一（`:103` 与 `:118` 合一）。
4. 前端 `Guide`/IDE 提示：`DATA_DIR="/sandbox/data"`（只读）、`OUTPUT_DIR="/sandbox/output"`，
   并在 IDE 里把"产物请写 `/sandbox/output`"写成常驻提示（现在前端全仓没有任何目录文字）。
5. 钉桩：挂载表用例（gRPC 请求形状）、`/sandbox/output` 采集用例、`/sandbox/data` 写入必须失败（只读真生效）。

兼容与诚实：MicroVM 是短生命周期资源，无历史数据迁移问题；退役路径（bwrap/fc 的 `/workspace/output`）
不动，只保持 `LEGACY_LEVEL_RETIRED` 的诚实表达。**不改挂载表以外的执行语义。**

**已落地（本轮）**：`cube_runtime_adapter.py` 新增 `GUEST_DATA_DIR/GUEST_OUTPUT_DIR/GUEST_WORK_DIR/GUEST_EXEC_DIR`
+ `HARVEST_DIRS`；挂载表改用常量且数据卷声明 `readonly=True`；注入目标改 `/sandbox/data`；`_l2c_file_op`
新增 `where`（读按优先级解析，写默认落 output，上传路由固定写工作区）；`deliveries._collect_session_files`
逐文件按源目录取件；`/usage` 的 `du` 同步新目录（旧口径写死 `/data /results /scratch`，是本轮改名带出的
回归，同一轮修掉并计入台账）。钉桩：`tests/test_cube_runtime_adapter.py` 按字面钉新挂载路径；
`tests/test_ss18_guest_paths.py` 4 条（上传不落采集目录 / 采集逐文件标源且同名 output 优先 / 404 与 400 /
授权数据与 exec 中转永不采集）。

**口径限定（不得说成更强）**："`/sandbox/data` 只读"在当前上游是**声明 + 采集面不取它**，不是 hypervisor 级
强制——冷启动沙箱的声明卷不进 guest（`cube_runtime_adapter.py` 注释自证），真正的出域约束由 T5 出口网关承担。
兼容期 `/scratch` 仍参与采集（逐文件标源目录），存量会话产物不会因升级静默丢交付；Guide/IDE 文案迁完之后
再把 `/scratch` 从采集面去掉。

## 3. 开发任务（Task）读视图

沿用评审计划 §六.3：**不新建表、不加聚合端点**，Task = `notebook` + `workload` + `compute_run` 的前端读视图。
补三件表达：

| 原型字段 | 落点 |
|---|---|
| 任务编号 `TASK-YYYYMMDD-XXX` | 后端在 notebook 上补一个展示编号（`task_no`，迁移 additive；或前端由 `created_at + id` 派生——**选后端**，编号是对外口径不能让每个客户端各算一份） |
| 状态 进行中/已结束 | 由 `notebook.status` + 是否有 published 负载 + 最近运行状态合成，规则写在这里：有 published 负载或有运行记录=进行中→（归档=已结束） |
| 调试方法（Python 3.10 / SQL-DuckDB）、调试/执行内存、导出方式 | `notebook.language` + 新增任务级配额字段（默认 8G/16G，上限受合约 `sandbox_resource_spec` 约束），创建任务时落库 |

## 4. 前端 IA 收敛（严格按原型）

导航（开发者工作台）：`首页 / 我的开发 / 我的结果集 / 安全管理 / 开发指引 / 环境配额`；
管理态保持现有全量菜单（**`/ops/*` 与 `/capability-matrix` 不删**，改 admin-only —— 评审计划 §六.2 与
`workspace.ts` 白名单已实现，本轮只需把开发者白名单从 18 项收敛到 4+2）。

改造顺序（M1→M5，每步都过远程门）：

- **M1 导航与命名（已落地）**：`DEVELOPER_MENU_KEYS` 收到 16 项（剔除 `/sandbox-sessions`、`/compliance-disclosure`）；
  侧栏按原型重排为 `首页 / 我的开发(任务列表·在线调试·负载与发布·计算调度) / 我的结果集(成果中心) /
  安全管理(操作日志) / 开发指引 / 环境配额`；`仪表盘→首页`、`审计中心→安全管理` 同步改名。
  两处分权差异是故意的：(a) **数据准备三项（`/data-resources`、`/catalog`、`/contracts*`）仍留在开发者白名单**——
  用户流程第 0 步就是"上传数据集生成数据目录"，单账号环境没有第二个工作台可切；(b) 侧栏只裁菜单不裁路由，
  `/ops/*` 由 `App.tsx:106-109` 的 `k8sOpsReaders` **路由门**控住，所以 URL 直达不是越权，只是对
  "已具备该角色的账号"不设防（待补 workspace 级路由门）。
- **M2 任务列表 + 新建任务**：`/flow/tasks` 表格 → 卡片网格（编号/沙箱引擎/状态 Tag/调试/执行/详情）+
  筛选栏（名称/状态/调试方法）；新建弹窗 → 全表单（调试方法/导出方式/双内存/关联数据双 Tab + 已选看板/完成并进入调试）。
- **M3 调试环境外壳**：状态栏（`● 沙箱已连接` + CPU/内存条）、左侧目录树（`/sandbox/data` 只读锁标 + 配额条）、
  底部控制台 Tab；单元格内核保持现状（真 `cds_kernel`），**形态按在线 notebook 页面**：单元格顺序执行、
  输出就地回显、变量跨单元格保留。
- **M4 任务详情**：`/flow/tasks/:id` = 页头（状态/编号/进入调试/执行任务）+ KPI 四卡 + 合约与数据卡 +
  运行记录表（`调试执行`/`正式执行`/规格/耗时/查看日志）+ 产出结果集表。
- **M5 结果集与审计**：`DeliveryCenter` 三 Tab **已存在**（数据集打包 / API 交付 / 推送），按原型改名并补
  列字段（T5 审查状态 / 水印状态 / 关联任务）；**新增只读端点 `GET /deliveries/openapi/endpoints/{id}/calls`**
  （从 `openapi.call` 审计行取调用时间/来源 IP/耗时/失败原因）支撑 `3-3 接口监控` 调用明细表；
  `AuditLog` 筛选器换成原型 4 类操作（创建调试沙箱/进入生产环境/结果集导出/数据调用）——
  详情**已是 Drawer+`Descriptions`**（`AuditLog.tsx:101-109`），缺的是字段：日志编号 / 来源 IP /
  操作对象 / 分段耗时 / 存证状态徽章；存证验真弹窗（含 `block_number`，并**如实披露存证后端**
  `pg-log` vs 真链）。

## 5. 验收门（每阶段末）

1. 远程 harness（用户铁律）：`source_lane.sh fast` 全 `STEP_OK`；CI gate 子集只增不减绿；全量回归失败数
   **不得超过 `ci/known-failures.md` 基线**。
2. FE 权威数在远程 FE 车道（`tsc -b` + `vitest`），本地只当迭代探针。
3. 浏览器全链（本机真 Cube）：建任务 → 调试执行（`cds_kernel`）→ 固化 → 发布 → 执行 → 三通道各一次真成功
   → 审计验真。任何一环红就记红（禁伪造、诚实降级）。
4. 新 `CDS_*` 开关需同步五份部署配置；本轮不新增开关。
5. 原型里的示例文案（`沈阳数据中心R`、`TASK-20260812-001` 等）**只作数据形状**，不得硬编码进前端。

## 6. 明确不做

1. 不删 `/ops/*`、`/capability-matrix`、`k8sOpsApi`（改 admin-only，评审计划 §六.2）。
2. 不新建 `src/pages/Developer/{Tasks,Results,Security}` 页面树——复用 `Flow/*` + `Audit/*` 壳，缺的是弹窗/抽屉与外壳字段。
3. 不把 Webhook 的 HMAC-SHA256 说成"SM2 签名凭证"，不宣称 `/open/v1` 可匿名调用，不宣称默认存真链（评审计划 §六.4b）。
4. 不在真实执行未启用的环境里把 sim 当 real 展示。

## 7. 执行记录

- **M1 已过浏览器实测**（qa_dev / buyer 角色，开发者工作台侧栏实返）：
  `首页 | 数据目录 | 合约管理 | 我的开发(任务列表·在线调试·负载与发布·计算调度) | 我的结果集 | 开发指引 | 环境配额`
  ——与原型 4+2 一致（数据目录/合约按角色门保留）；安全管理对 buyer 不可见（不在 `auditReaders`），
  属于现有角色门而非原型差异，已写进 `Sidebar.test.tsx` 的断言。
- 远程验证：定向四文件 **67 passed / 56.94s**（`TREE_MD5_MATCH`，详台账 V-15）；FE 车道（node:20 内）
  `TSC_RC=0` / `vitest 12 files · 141 tests passed` / `BUILD_RC=0`——但该轮树同步早于 IDE 目录提示那一行，
  所以补跑一轮以覆盖；gate 车道（fast）的 runtime/compileall/import/alembic upgrade/alembic check/schema_parity
  已全 `STEP_OK`（Python 3.12.15，36 个 gate 文件）。
- 推送：`56ad292..b8fc5c1` → `origin/feat/ss-11-productization`（本轮 6 个提交，接手请先 `git pull`）。
  推送前门：远程 gate 车道 9 步全 `STEP_OK`（含 alembic upgrade/check/schema parity，Python 3.12.15，
  **gate 244 passed·2 skipped / ss15 85 passed**），FE 车道补跑 `tsc 0 / vitest 12 files·141 tests / build 0`——详台账 V-16。
- 待回填：full 全量回归对比基线、M2–M5 浏览器全链逐步状态码。
- 待做（已计入 §2 改动清单第 4 项）：IDE / 开发指引里把目录约定写成常驻文案（前端全仓现在
  仍无任何目录提示文字，`grep -rn 'scratch' cds-frontend/src` = 0 命中）。

## 8. 未完成清单（换人接手就按这张表做，顺序即依赖顺序）

### P0 · 后端先行（前端 M2/M4/M5 的数据源都在这里）

| # | 要做 | 落点 | 备注 |
|---|---|---|---|
| ① | 开发任务字段：`task_no`（`TASK-YYYYMMDD-XXX`）、调试方法、任务级导出方式、调试内存(2–32G)/执行内存(2–64G) | `app/models/notebook.py` + **新增 additive 迁移 0023** + `app/api/notebooks.py` 创建/回显 + `app/schemas/flow.py` | 线性链只增不改；`verify_schema_parity.py` 与 `alembic check` 是 CI 门，必须同步过。配额上限受合约 `sandbox_resource_spec` 约束 |
| ② | `DeliveryBundle` 补三列：T5 审查状态 / 水印状态 / 行数 | `app/models/delivery_bundle.py` + 打包时写入 | 现在这三项结论**只活在包内 `MANIFEST.json`**，列表页无从显示 |
| ③ | 只读端点 `GET /deliveries/openapi/endpoints/{id}/calls`（调用时间 / 来源 IP / 耗时 / 失败原因 / 成功率） | `app/api/deliveries.py` + 审计行 `openapi.call` | **前置**：`openapi.call` 目前不记来源 IP，先补记录再谈监控页 |
| ④ | IDE / 开发指引里的目录约定常驻文案 | `Guide.tsx`（现无任何目录文字）、`NotebookWorkbench.tsx` 已加提示行 | 迁完之后才能把 `/scratch` 从 `HARVEST_DIRS` 兼容采集里摘掉 |
| ⑤ | 输出目录常量去重：`code_scanner.py` 与 `cube_runtime_adapter.py` 各有一份 | `app/services/code_scanner.py:103/118` | 注意 `:118` 是退役路径的 `/workspace/output` 口径，别一把改错 |
| ⑥ | `WorkspaceRoute`：按 `resolveWorkspace(role, stored)` 给路由加工作台门 | `cds-frontend/src/App.tsx` | 台账 D-16；这是口径缺口，不是安全洞（`/ops/*` 已有角色路由门） |

### P0′ · "空输入返回 200 + 真空为真"家族（后端审计报告 §5 列出，**逐条自行复核后再动**）

`notebook_service.run_cell` 空码 `ok=True`；`/execute` 空输出 `passed=True`；兄弟 `/exec` 预置 `True`；
`output_gateway` 空数据 `success + row_count=0`；二进制 `skipped_binary` 仍 201；K-匿名/MIA/签名在真空集上为 `True`。
已修一处：`notebooks.py::run_notebook` → `NOTEBOOK_NO_CELLS` 409（台账 D-12）。
判据：空/全空白输入必须诚实失败或显式 `no_op`，不得自增版本、不得白起沙箱。

### P1 · 前端 M2–M5（依赖 P0-①②③）

- **M2** `/flow/tasks`：`Table` → 卡片网格（编号 / `CubeSandbox MicroVM` / 进行中·已结束 Tag / 调试 / 执行 / 详情）
  + 筛选栏（名称 / 状态 / 调试方法）。现在只有 名称·类型·阶段·合约·最近更新 五列 + 一个"进入"。
- **M2′** 新增开发任务：两项弹窗 → 11 字段整表单（含关联数据双 Tab 与"已选数据"看板、"完成并进入调试"）。
- **M3** 在线 notebook 调试页外壳：状态栏（`● 沙箱已连接` + CPU/内存条）、左侧目录树
  （`/sandbox/data` 只读锁标 + 配额条）、底部控制台 Tab。内核保持现状（真 `cds_kernel`，跨单元格变量保留）。
- **M4** `/flow/tasks/:id` 任务详情（页头 + KPI 四卡 + 合约与数据卡 + 运行记录表 + 产出结果集表）
  + 运行记录抽屉补 T5 五步流水（数据源在证明包 `output_security.*`，已存在但此页未调）。
- **M5** 结果集资产中心（三 Tab 改名 + 列字段）+ 接口监控调用表（P0-③）+ 审计筛选换原型 4 类操作 +
  日志详情补编号/来源 IP/操作对象/耗时 + 存证验真弹窗补链上时间戳。
- **M6** 首页开发者看板（4 KPI + 调试/执行内存双进度条 + 近 7 天趋势 + 最新结果集）。
  现在 `/` 是管理员运营概览（只调 `monitoringApi`），原型的 4 宫格全部缺；
  内存数据源在 `quotaApi`（`used_memory_mb` / `limits.memory_mb`），但**没有调试/执行拆分**——需要后端补。

### 每阶段验收门（不许跳）

1. `bash scripts/remote-harness/sync_tree.sh` → `TREE_MD5_MATCH`；
2. 远程 `source_lane.sh fast` 全 `STEP_OK`（全量改动日再跑 `full`，失败数不得超过 `ci/known-failures.md`）；
3. `bash scripts/remote-harness/fe_lane.sh` → `TSC_RC=0 VITEST_RC=0 BUILD_RC=0`；
4. 浏览器按原型走一遍受影响的屏，逐步状态码/文案写进 `docs/defect-ledger-2026-10-16-portal-e2e.md`；
5. 环境/操作记忆变了就改 `docs/agent-handoff-2026-10-16.md`，别让下一个人重新发现。
