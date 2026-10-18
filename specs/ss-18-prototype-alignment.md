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

### Round 2026-10-16（本机 py3.12 门轮）执行记录

验证车道：远程车道从本机不可达（`Permission denied (publickey,password)`），经用户确认改走本机
`.venv-gate`（`--system-site-packages`）门，命令口径
`TEMPORARILY_DISABLE_PROTOBUF_VERSION_CHECK=true .venv-gate/Scripts/python.exe -m pytest …`
（vendored gencode 是 protobuf 7.35.1，本机 protobuf 6.33.5，故需该豁免；为跑门清单另装 `kubernetes>=29`）。
**下表数字全部是本机 3.12 口径，不等于远程车道口径**；`/exec` 系测试在本机恒 skip。

| 任务 | 状态 | 关键产出 | 新增测试与结果 | 回归结论 | 未实施项 |
|---|---|---|---|---|---|
| P0-① 开发任务字段 | 完成 | `notebook.py` 5 字段 + `ix_notebooks_task_no`；迁移 0023；`notebooks.py` 生成 `TASK-YYYYMMDD-NNN`、内存档位与合约封顶、保存点重试 | `tests/test_ss18_task_fields.py` 11 passed | 与 `test_ss15_flow_api` 合跑 46 passed | 成功率类聚合（属 P0-③ 口径外） |
| P0-② 交付三列 | 完成 | `delivery_bundle.py` 三列 + 迁移 0024（additive、不回填）+ 打包时写入 + 列表回显 | `tests/test_ss18_bundle_columns.py` 12 passed | `test_ss17_delivery_bundles` + `test_ss18_bundle_preview` 19 passed | 历史包回填（明确不做） |
| P0-③ 调用明细 | 完成 | 两条审计行补 `ip_address`、归源改 `api_endpoint`；新增 `GET /deliveries/openapi/endpoints/{id}/calls` | `tests/test_ss18_endpoint_calls.py` 7 passed | 无（新读口 + 附加字段） | 端点成功率聚合口径未定，未造数 |
| P0-④ 目录文案 | 完成 | `directoryContract.ts` 唯一来源 + `Guide.tsx` 常驻公告 + 工作台提示行 | `directoryContract.test.tsx`（含与后端 `GUEST_*` 逐字同序断言） | FE 门 199/199 | `/scratch` 摘出采集面（迁移完才做） |
| P0-⑤ 扫描器目录口径 | 完成 | 允许集 = 采集面 `{/sandbox/output/, /scratch/}`；报错稳定序 | `test_training_mode_checkpoint_dir_is_rejected` 等 3 项 + 漂移等式测 | `test_code_scanner` + `test_p05_code_scanning` 71 passed | 不改 `HARVEST_DIRS` 本身（扩采集面是安全决策） |
| P0-⑥ 工作台路由门 | 完成 | `WorkspaceRoute`（`RoleRoute` 外层 / `WorkspaceRoute` 内层）+ 31 条路由接线 | `WorkspaceRoute.test.tsx` 8 项（变异验证由实现它的子任务跑：把门改成恒放行 → 恰好 3 条红） | FE 门 199/199 | `/federation`/`/training`/`/data-pipeline` 归属＝产品决策项 |
| P0′ 空输入家族 | 部分完成 | D-20 空文件 `status=empty` + 包级聚合降级；D-21 空码 422 早于起会话；`/execute`+`/exec` 顶层 `no_output` | `tests/test_ss18_empty_input.py` 8 passed / 1 skipped | `test_ss15_flow_api` + 交付四文件合跑 77 passed | K-匿名/MIA/重构/签名真空为真、`output_gateway.process([])` 仍 `success=True`：判据设计已写在 §8 P0′，需与钉住它的 12 处断言同批改判据语义并跑全量 |
| 迁移与门 | 通过 | 0000→0024 空库 upgrade、`alembic check` 无漂移、`0024→0022→0024` 往返三个 rc=0（新列可安全回退） | `SCHEMA PARITY OK: 54 tables, columns identical`、`compileall -q app tests alembic` rc=0 | `test_capability_matrix_consistency` + `test_error_contract` + `test_frontend_api_contract` 14 passed | `verify_schema_parity.py` 在 Windows 以 rc=1 退出（脚本自身 tempfile 缺陷，见台账），PG 侧未跑 |
| CI 门清单（ci.yml 现场解析 36 文件，本机口径） | 改动面全绿 | 门清单从 `.github/workflows/ci.yml` 的 `Gate tests` step 现场解析（36 项，与 `ci/known-failures.md` 的 `GATE_FILES=36` 一致） | **227 passed / 18 failed / 1 skipped**；改动面首轮 140.85s、最终树 146.33s，**两次失败集合逐条相同**：18 条逐条定性全部为本机环境类——11× `test_deploy_manifests`（`pathlib.read_text()` 无 encoding，Windows 默认 GBK 解 UTF-8 YAML → `UnicodeDecodeError`）、1× `test_migrations::test_schema_parity_script_exits_zero`（断言消息里就是 `SCHEMA PARITY OK: 54 tables, columns identical`，rc=1 来自 tempfile 清理 `WinError 32`）、6× `test_rag_*`（`ModuleNotFoundError: torch` / `onnx`）、1× `test_rate_limit_distributed`（需真实 Redis 共享窗口，本机无 Redis ⇒ 第二实例 200 而非 429） | 无一条落在本轮改动面（`notebooks`/`deliveries`/`open_api`/`sandbox_sessions`/models/scanner/error-codes 契约全在其中且通过）；另 `tests/test_ss15_e2e_flow.py` 13 passed（余 2 条红经 `git stash` 对照法确认是基线既有：`storage_service.py:240` Windows 路径口径）+ `tests/test_ss15_sdk.py` 全绿（新响应字段未打破 SDK 契约） | 车道口径数字仍缺：车道本机不可达，门层「必须全绿」只能在车道判定 |

合并证据集（本机 3.12，13 个文件一次跑）：
`test_ss18_task_fields` + `test_ss18_bundle_columns` + `test_ss18_endpoint_calls` + `test_ss18_empty_input` +
`test_ss15_flow_api` + `test_ss15_sdk` + `test_ss17_delivery_bundles` + `test_ss18_bundle_preview` +
`test_code_scanner` + `test_p05_code_scanning` + `test_capability_matrix_consistency` + `test_error_contract` +
`test_frontend_api_contract` ⇒ **186 passed / 1 skipped**（最终树复跑 102.80s，首轮 98.62s；唯一 skip 是 `/exec` 系 POSIX 导入）。

前端门（本机，node v22.5.1 + `NODE_OPTIONS=--experimental-require-module`）：
`npx tsc -b` **rc=0** / `NODE_OPTIONS=--experimental-require-module npx vitest run` **18 files · 190 tests passed**
（双进程并发满载连跑两次皆 190/190）/ `npm run build` **rc=0**（`built in 1.72s`）。

M2 轮新增 `DevTasks` 卡片列表 + `DevTaskCreateModal` + `TaskRunRecords` + `devTasksModel`/`flow.ts` 口径层，
测试从 14 files/154 涨到 18 files/190。**本机修的一处测试基建缺陷**：满载下 5 条新用例红、空载下全绿——
`findBy*`/`waitFor` 默认 1000ms 上界在并行导入高峰（`import 117s`）会被机器负载先撞穿，
于是 `src/test/setup.ts` 统一 `configure({ asyncUtilTimeout: 10_000 })`（放宽的是等待**上界**，
断言仍是状态条件；修后双进程满载两次 190/190）。

**同轮续（P0′ 真空判据 + M2′ 关联数据看板，本机 3.12 / node 22 口径）**：

| 项 | 改动 | 验收 |
|---|---|---|
| 成果闸对 0 行诚实失败 | `output_gateway.py`：`GatewayResult.empty_output` + Step 0 早退（不进流水线、不出载荷）；`/output-control/gateway` 响应与审计 detail 带出 | `test_ss18_vacuum_semantics.py` 4 例（csv/json 两格式 + 反向护栏：有行照常 `success=True`） |
| 统计量不再被读成放行 | `k_anonymity.py`：`KAnonymityResult.evaluated`/`reason`（真空仍 `passed=True`，数学不动） | 3 例：`check([])` 与 `check_csv(空串)` 均 `evaluated=False`，有行时 `evaluated=True` |
| 检查流水披露未评估阶段 | `output_inspection.py`：`InspectionResult.not_evaluated`（重构 / k-匿名 / MIA 三处）+ `degraded`（SM2 退化成 SM3 摘要）；`output_security.inspection_to_report` 带出 | 3 例：structured_query 列 `data_reconstruction`+`k_anonymity`；train 场景列 `model_safety` 且**不冒名**列未启用的 `k_anonymity`；develop 场景两字段为空数组 |
| 唯一改写的既有断言 | `test_output_gateway.py::test_empty_data` 由 `success is True` 改为诚实失败（docstring 写明理由与判据出处） | 未删、未跳任何用例 |
| M2′ 关联数据看板 | 新 `ContractDataBoard.tsx`（双 Tab + 已选看板 + 三种「没有数据」分开说）+ 纯口径层 `contractDataModel.ts`；`useContractOptions` 下发 `product_ids: string[] | null`；`index.css` 补 `.cds-task-data-board`（复用既有调色板） | `ContractDataBoard.test.tsx` 6 例 + `DevTasks.test.tsx` 集成 1 例（选定合约后真的去打 `/data-products/p-9`） |
| 任务上下文参数选中笔记本（补钉） | 把 `?task=<id>` 的同步从 effect 改到渲染期（React 官方 prop→state 写法），少一次级联渲染，也消掉 lint 的 `set-state-in-effect` | 新 `NotebookWorkbench.taskParam.test.tsx` 2 例：带 `?task=n-2` 只打 `/notebooks/n-2` 且深链参数保留；不带参数时不猜选中、不发详情请求 |

受影响面复跑（本机 3.12）：`test_ss18_vacuum_semantics` / `test_output_gateway` / `_enforcement` / `test_output_inspection` / `_extended` /
`test_k_anonymity` / `test_reconstruction` / `test_mia_probe` / `test_dp_budget` / `test_dp_guardrails` / `test_output_control_api` /
`test_field_classification_policy` / `test_ss18_empty_input` / `test_streaming_inspector` / `test_ss17_delivery_bundles` / `test_ss18_bundle_preview`
⇒ **233 passed / 2 failed / 1 skipped（67.33s）**（2 条 = `storage_service.py:240` Windows 路径口径，`git stash push -q -- app/` 对照法确认基线同红，pop 后 13 个 `app/` 文件全部复原）；
交接验收复跑（同树第二次，本机 3.12）：门清单 ⇒ **227 passed / 18 failed / 1 skipped（162.90s）**，失败构成与前次逐条相同；SS-18 专项 6 文件（task_fields / bundle_columns / endpoint_calls / empty_input / vacuum_semantics / code_scanner）⇒ **81 passed / 1 skipped**；`alembic upgrade head`（0022→0023→0024）+ `alembic check`（No new upgrade operations）均 rc=0；`compileall -q app tests alembic scripts` rc=0；`import app.main` 通过；`test_capability_matrix_consistency.py` 3 passed。另真修一处**口径门假红**：`scripts/verify_schema_parity.py` 在 Windows 上因未 `engine.dispose()` 删不掉临时库，打出 `SCHEMA PARITY OK: 54 tables` 后仍以 rc=1 结束——3 行真修（台账 V-12），修后 `PARITY_RC=0`。

原文：ci.yml 门清单 36 文件 ⇒ **227 passed / 18 failed / 1 skipped（143.08s）**，失败集合与前次逐条相同（10 `test_deploy_manifests` + 1 `test_migrations` + 6 rag + 1 `test_rate_limit_distributed`）；`compileall -q app tests alembic` rc=0。
前端门（终树，node 22 本机口径）⇒ `npx tsc -b` **rc=0**；`vitest run` **20 files · 199 tests 全绿**；
`npm run build` **rc=0**；`npx eslint .` **16 errors，全部落在我没碰过的 9 个老文件里，我改/新增的文件 0 条**
（初稿时我这边有 2 条：`react-refresh/only-export-components` 与 `react-hooks/set-state-in-effect`，
分别用「纯逻辑拆 `Flow/contractDataModel.ts`」和「URL 参数变化改在渲染期对齐 state」消掉，不是关规则）。

未提交：本轮全部改动留在工作区（未 commit、未 push，用户未授权提交）。`git status --porcelain` = **53 项**（**0 删除**）：
**31 改**（`app/` 13 = 5 api + 2 models + 1 schemas + 5 services、`cds-frontend/src/` 11、`tests/` 2、`docs/` 3、`specs/` 1、`.gitignore` 1）
第 32 个改的文件是 `scripts/verify_schema_parity.py`（口径门假红的 3 行真修，台账 V-12）。
+ **21 新**（迁移 `0023`/`0024`、后端 5 个 `test_ss18_*.py`、前端 14 = `WorkspaceRoute` + `directoryContract` + `contractDataModel` +
`DevTaskCreateModal`/`TaskRunRecords`/`ContractDataBoard` + 8 个测试文件）。门日志与门清单等临时文件已删除。

## 8. 未完成清单（换人接手就按这张表做，顺序即依赖顺序）

### P0 · 后端先行（前端 M2/M4/M5 的数据源都在这里）
**状态（2026-10-16 本轮，详 §7 执行记录与台账 D-17..D-21）**：

- ① 已落地：`0023_task_fields` 加 `task_no/debug_method/export_mode/debug_memory_mb/exec_memory_mb` + 唯一索引；
  `task_no` 服务端按日生成 `TASK-YYYYMMDD-NNN`（客户端提交被忽略），唯一冲突走保存点重试而不是 500；
  内存档位 2–32G/2–64G 且受合约 `sandbox_resource_spec.memory_mb` 向下封顶（超出 → `RESOURCE_OVERRIDE_EXCEEDED` 400，
  档位/依赖错 → `VALIDATION_ERROR` 422），执行内存缺省继承调试内存。钉 `tests/test_ss18_task_fields.py`。
- ② 已落地：`0024_bundle_review_columns` 三列 additive、**不回填**，存量行 NULL 由前端显示「未记录」；
  `row_count` 只数数据表文件（`.csv/.tsv` 去表头、`.jsonl` 逐行），无数据表为 NULL。钉 `tests/test_ss18_bundle_columns.py`。
- ③ 已落地：两条审计行补 `ip_address`，数据面调用改按 `resource_type=api_endpoint` 归源（与 `api_keys.py:181`/
  `training.py:422` 同口径，可走 `ix_audit_logs_resource` 精确分页），新增 `GET /deliveries/openapi/endpoints/{id}/calls`；
  **不建影子调用表**（调用明细的唯一真值是带锚定的审计日志）。成功率仍不在本轮：它需要按端点聚合，
  而聚合口径（窗口 / 分母是否含被拒调用）没定，先不造数。钉 `tests/test_ss18_endpoint_calls.py`。
- ④ 已落地（前端）：`src/pages/Flow/directoryContract.ts` 为目录契约文案唯一来源（四个 `GUEST_*` + 采集口径），
  `Guide.tsx` 常驻公告 + `NotebookWorkbench.tsx` 提示行都从它渲染；文案按事实写「只读声明 + 采集排除」，
  不宣称虚拟化强制。注意 `/scratch` 仍在 `HARVEST_DIRS` 兼容采集期内，**摘掉它之前不能删这条文案**。
- ⑤ 已落地，但**清单坐标是错的**：`code_scanner.py` 全文只有一份常量（`:103`），文件里没有 `/workspace/output`
  （那是 `SANDBOX_LEGACY_ENABLED` 之后退役路径的口径）。真缺陷＝允许集 ≠ 采集面：`/sandbox/checkpoints` 放行却不采集
  （产物静默丢），`/scratch` 采集却不被允许。已按最窄口径对齐并由 `test_allowed_save_paths_equal_harvest_contract` 钉住等式。
- ⑥ 已落地（前端）：`WorkspaceRoute` 按 `resolveWorkspace(role, stored)` 给路由加工作台门（`RoleRoute` 外层、`WorkspaceRoute` 内层，
  角色不符仍保持原有跳转语义）。`/federation`、`/training`、`/data-pipeline` **有意不标 admin**：它们的角色门放行业务角色，
  而业务角色切不到管理员工台，标了等于把页面从数商/买方手里拿走——这是产品决策项，不是门。


| # | 要做 | 落点 | 备注 |
|---|---|---|---|
| ① | 开发任务字段：`task_no`（`TASK-YYYYMMDD-XXX`）、调试方法、任务级导出方式、调试内存(2–32G)/执行内存(2–64G) | `app/models/notebook.py` + **新增 additive 迁移 0023** + `app/api/notebooks.py` 创建/回显 + `app/schemas/flow.py` | 线性链只增不改；`verify_schema_parity.py` 与 `alembic check` 是 CI 门，必须同步过。配额上限受合约 `sandbox_resource_spec` 约束 |
| ② | `DeliveryBundle` 补三列：T5 审查状态 / 水印状态 / 行数 | `app/models/delivery_bundle.py` + 打包时写入 | 现在这三项结论**只活在包内 `MANIFEST.json`**，列表页无从显示 |
| ③ | 只读端点 `GET /deliveries/openapi/endpoints/{id}/calls`（调用时间 / 来源 IP / 耗时 / 失败原因 / 成功率） | `app/api/deliveries.py` + 审计行 `openapi.call` | **前置**：`openapi.call` 目前不记来源 IP，先补记录再谈监控页 |
| ④ | IDE / 开发指引里的目录约定常驻文案 | `Guide.tsx`（现无任何目录文字）、`NotebookWorkbench.tsx` 已加提示行 | 迁完之后才能把 `/scratch` 从 `HARVEST_DIRS` 兼容采集里摘掉 |
| ⑤ | 输出目录常量去重：`code_scanner.py` 与 `cube_runtime_adapter.py` 各有一份 | `app/services/code_scanner.py:103` | **原写「`:118` 是退役路径 `/workspace/output`」经复核为误判**：该文件只有一个输出常量（原值含不在采集面的 `/sandbox/checkpoints/`）；`/workspace/output` 属 firecracker/sandbox_runtime 的 `--bind` 口径，在别的文件里，本轮没碰 |
| ⑥ | `WorkspaceRoute`：按 `resolveWorkspace(role, stored)` 给路由加工作台门 | `cds-frontend/src/App.tsx` | 台账 D-16；这是口径缺口，不是安全洞（`/ops/*` 已有角色路由门） |

### P0′ · "空输入返回 200 + 真空为真"家族（后端审计报告 §5 列出，**逐条自行复核后再动**）

**复核真值（2026-10-16 逐条取证，行号已按当前树校正）**：

| 站点 | 现状 | 处置 |
|---|---|---|
| `notebooks.py::run_notebook` 空笔记本 | 已修（D-12，409 `NOTEBOOK_NO_CELLS`） | 作为其余站点的验收模板 |
| `notebooks.py::execute_cell` 空码单元格 | 白起开发会话 + 自增 version（`run_cell` 自己的空码短路在调用方之后） | **已修 D-21**：`_ensure_dev_session` 之前 422 `code cannot be empty` |
| `/execute` 空输出 `passed=True`（`sandbox_sessions.py:1408`） | 至少披露 `stage_results.no_output` | **已补顶层 `no_output`**；不改 `passed` 语义：`mkdir`/只写文件的单元本就无 stdout，判 False 是错的 |
| `/exec` 预置 `passed=True`（`:2437`） | 零披露；空命令守卫**其实已存在**（请求模型 validator 422） | **已补 `no_output`**；handler 层重复守卫已删 |
| 空文本文件进包（`deliveries.py:105 _review_output_bytes`） | 审查器已判 `passed=False`，函数只数 findings → 空文件冒充「已审」 | **已修 D-20**：单独 `status="empty"`，不计 T5 已审指标，包级聚合降为 `partially_reviewed` |
| 二进制 `skipped_binary` 仍 201（`deliveries.py:112`） | 已按现状披露（逐文件 `output_review` + 包级 `partially_reviewed`） | 保持 201：包不是「一次审查结论」，诚实披露即可 |
| K-匿名 / MIA / 重构检测 / 签名 在真空集上 `True` | 真空为真（数学上没错），12 处断言钉着它 | **已修（本轮）**：`KAnonymityResult` 扩 `evaluated`/`reason`，`InspectionResult` 扩 `not_evaluated`/`degraded`；`stage_results` 的真空 `True` 原样保留（不动 DP/合规闸语义、不动那 12 处断言）；披露字段进 `security_report` 与审计**给人看**，闸门按拍板不据此拦截（见下方口径） |
| `output_gateway.process([], "csv")` → `success=True, row_count=0` | 送审文本是 `"[]"`，连 `empty_output` 都不触发 | **已修（本轮）**：网关 Step 0 对 0 行诚实失败并置 `GatewayResult.empty_output`，不进流水线、不出载荷；`/output-control/gateway` 响应与审计 detail 同步带出 |

**统一判据（本轮已按此实现，别一处一改）**：对**成果闸**语义（会决定是否交付/放行）的检查看，
真空 = 不通过（fail-closed）；
对**独立统计量**（`KAnonymityChecker.check`、`check_mia_risk` 等被训练/合规多处复用的纯函数），
把返回值从「bool 真假」扩成 `evaluated: bool` + `reason`，让调用方无法把「无数据」读成「通过」——
翻 `passed` 会同时改 DP/合规闸的通过语义，所以**实际落地选择不翻**：闸门层 fail-closed，
统计量与阶段层只做披露（`evaluated` / `not_evaluated` / `degraded`），那 12 处钉住真空为真的断言因此
**无需改动**，DP 与合规语义保持原样。实现见 `output_gateway.py`（Step 0）、`k_anonymity.py`
（`evaluated`/`reason`）、`output_inspection.py`（三处未评估 + 签名降级）、`output_security.py`
（`inspection_to_report` 带出）。仍**有意保留**的两处真空：`training_pipeline.check_mia_risk` 是收
浮点数的纯函数（真空在调用方已被 `model_safety` 未评估披露）；`FORMAT` 阶段对字面量 `"[]"` 不算空
（它是非空文本），交付路径已由网关 Step 0 拦死。

**口径已拍板（2026-10-16，用户）**：阶段级真空（`not_evaluated`）**只披露、不拦放行**。
理由：可达的那一条是「合约场景含重构检测但 `inspection_rule_set` 没带 `source_rows`」，
拦它等于把现网未提供参照行的合约从放行改拦截，且参照行的真来源（会话输入集）是另一件还没做的事。
`not_evaluated` / `degraded` 因此是**给人看的审计面**，不是闸门输入——`should_block()` 故意不读它们；
以后要翻这个口径，先补 `source_rows` 的承载再谈拦截，别直接往 `should_block()` 里加条件。


判据：空/全空白输入必须诚实失败或显式 `no_op`，不得自增版本、不得白起沙箱。

**本机 3.12 口径验证（本轮）**：受影响 16 文件 **233 passed / 2 failed / 1 skipped**
（那 2 条经 `git stash push -- app/` 对照法确认为基线既有红：`storage_service.py:240` Windows 路径口径）；
ci.yml 门清单 36 文件 **227 passed / 18 failed / 1 skipped**，失败集合与前次逐条相同；`compileall` rc=0；
新增 `tests/test_ss18_vacuum_semantics.py`（11 例，含反向护栏「有行时照常放行」）。


### P1 · 前端 M2–M5（依赖 P0-①②③）

**状态（2026-10-16 本轮）**：M2 / M2′ 已落地，任务详情的「运行记录」入口以站内参数
（`/notebooks?task=<id>&focus=runs` → `TaskRunRecords` 面板上移）实现，未新开路由，属 M4 的第一块；
M3/M4 余下部分/M5/M6 未动。M2′ 的「关联数据」= `ContractDataBoard.tsx` 双 Tab
（「合约授权数据」按 `contract.product_ids` 逐项取回 + 「沙箱内可见路径」复用 `directoryContract` 单一口径），
上方看板写清「授权 N 项（已取回 M，无权查看 K，超出 20 项上限未取回 P）」，三种「没有数据」文案各不相同
（未选合约 / 后端未下发清单 / 确实没绑产品）。**有意不做勾选**：授权作用域是合约而非任务
（后端 `contract_fulfillment` 只承认 `session.data_product_id ∈ contract.product_ids`），做成「本任务挑这 2 份」
是后端不执行的假勾选；「完成并进入调试」= 建完用服务端回显的 id 跳 `/notebooks?task=<id>`，不自拼。
口径层落在 `src/pages/Flow/devTasksModel.ts` 与 `services/flow.ts`
（枚举、GiB↔MiB、`RESOURCE_OVERRIDE_EXCEEDED` 归位到字段、未登记取值原样透出不编中文标签），
门数字见 §7。原型示例文案（任务名、内存数字、`CubeSandbox MicroVM` 等）一律不硬编码，只渲染后端字段。

- **M2** `/flow/tasks`：`Table` → 卡片网格（编号 / `CubeSandbox MicroVM` / 进行中·已结束 Tag / 调试 / 执行 / 详情）
  + 筛选栏（名称 / 状态 / 调试方法）。现在只有 名称·类型·阶段·合约·最近更新 五列 + 一个"进入"。
- **M2′** 新增开发任务：两项弹窗 → 整表单 + 关联数据看板 + 完成并进入调试（**已落地**）。
  表单覆盖 `NotebookCreate` 的每一个有后端语义的字段（title/path/contract_id/debug_method/export_mode/
  debug_memory_mb/exec_memory_mb）+ 编号只读说明；规格正文从未列举过「11 字段」的具体清单，
  因此验收口径以 `app/schemas/flow.py::NotebookCreate` 为准，而不是对着一个未落文档的计数补字段。
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
   本机 Windows 无远程车道凭证时按 `docs/agent-handoff-2026-10-16.md` §1.1 的 `.venv-gate` 本机门代跑，
   且**所有数字必须注明「本机 3.12 口径」**，不得冒充远程车道结论；
3. `bash scripts/remote-harness/fe_lane.sh` → `TSC_RC=0 VITEST_RC=0 BUILD_RC=0`（本机等价：`npx tsc -b` /
   `NODE_OPTIONS=--experimental-require-module npx vitest run` / `npm run build`）；
4. 浏览器按原型走一遍受影响的屏，逐步状态码/文案写进 `docs/defect-ledger-2026-10-16-portal-e2e.md`；
5. 环境/操作记忆变了就改 `docs/agent-handoff-2026-10-16.md`，别让下一个人重新发现。

## 9. 自适应断点阶梯（1:1 真值只在 1440 一档成立）

> 判据前提：1:1 像素真值只在 **1440**（documentElement.clientWidth = 1425，差的是 15px 文档竖向滚动条）一档成立；
> 其余宽度按本节策略处理。凡是钉死像素的改动（定宽栅格、定宽列）必须同时写出**该像素布局成立的宽度区间**，
> 区间只能由真机 Emulation.setDeviceMetricsOverride 逐档量出，不许推算。

| 档位（浏览器宽度） | 外壳 | 首页栅格（实测卡宽） | 表格 | 弹层 |
|---|---|---|---|---|
| ≥ 1402 | 侧栏 220 + 内容 1172（内容右缘 1415） | KPI 单块四芯片一行（.cds-kpi-strip 断点 1402）；两张分析卡并排各 **571** | 全列可见，不内滚 | 定宽 ≤ 内容宽 |
| 1280–1401 | 同上，内容随宽伸缩 | KPI 四芯片仍一行；分析卡并排 **491** | 事件流表 box 426 ≥ 420，内滚未触发 | 同左 |
| 1200–1279 | 同上 | 分析卡并排 **451**（antd xl 生效下界） | 事件流表 box **386 < 420** ⇒ scroll={{x:420}} 生效，卡内横滚代替压字（crushed 0） | 同左 |
| < 1200 | 同上（侧栏可折叠） | 分析卡**堆叠全宽**：1024 档卡 741 / 860 档卡 733 | 全宽不压字 | Modal 宽取 min(100vw − 32, 设计宽) |

规则：

1. 多列/并排布局必须先给成立区间，再钉像素；区间来自真机逐档读数。
2. 表格只有在「列最小可读宽合计 ≤ 卡片内容宽」时才允许全列展示，否则写 scroll.x（值 = 列最小可读宽合计），
   让**卡片内**横向滚动；判据是 crushed = 0（没有任何 td/th 出现 scrollWidth > clientWidth）。
3. 每档必查三项：docOverflow = 0、outOfViewport = 0、crushed = 0。量具 globalThis.probeAtW({path,width,probe})，
   取数纪律见记忆卡 reference/env/cds-1440-capture-via-bun-webview.md（含第 14 条「数据落定」判据）。
4. 反例（2026-10-18 已修，别退回）：首页两张分析卡用 lg={12} 时，1024 档并排导致实时事件流 4 列被压成
   单元格 69px 装 84–114px 内容（10 格溢出 15–45px）；改成 xl={12}（并排下界 1200）+ 事件流 scroll.x=420 后
   1440/1280/1200/1160/1024/860 六档全部 crushed=0、无出屏元素，且 1440 档卡宽 571/571 与真值一致。
5. 未复现项按「未解释的噪声」记账，不记缺陷：审计表在 1024 档实测 box 691 = inner 691、crushed 0、无出屏元素，
   旧台账「审计表 1024 内部溢出 467」不成立（2026-10-18 复量）。

未完（下一轮）：外壳（侧栏折叠、内容宽）与 /deliveries 等宽表页在 <1400 的卡内横滚策略尚未按本表统一，见 §8 与本节表格列。
