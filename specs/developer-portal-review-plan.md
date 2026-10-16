# 开发者工作台提案（prototype-v3 / PRD / 实施计划）评审与修订研发计划

> 评审人：本仓 Agent（取证式评审，全部结论带 file:line 或命令实测）
> 评审对象：`specs/prototype-v3.html`(1911)、`specs/data-sandbox-developer-portal-prd.md`(317)、`specs/developer-portal-implementation-plan.md`(188)、`docs/developer-sandbox-spec.md`(23)、`specs/product-spec.md`(M)、`specs/frontend-spec.md`(M)、`specs/ss-15-flow-orchestration.md`(??)
> 结论定位：**方向采纳，落地路径重写**。提案对"开发者视角"的产品判断是对的，但它的三条技术前提与仓库现实不符，照原文交给 AI Agent 执行会产出重复页面 + 404 端点 + 合规口径收缩。

---

## 一、总体评分与一句话结论

| 维度 | 评分 | 依据 |
|---|---|---|
| 产品判断（开发者视角、4+2、三通道交付） | **A-** | 与用户口径的三段业务流程完全同构，页面颗粒度合理 |
| 与仓库现实的一致性 | **D** | 9 个新页面里 7 个已有实现；API 映射表 13 行中 6 行路径错误 |
| 与既有铁律/决策的一致性 | **C-** | 删改了承载合规口径的 spec 段落；验证门写成"本地 npm test" |
| 可直接派工性 | **C** | 无环境阶段、无后端阶段、无迁移阶段，验收门不可观测 |

**一句话**：这不是一份"实施计划"，而是一份"前端页面愿望清单"。缺的是**环境真值、契约对账、复用策略**三件事，本文件补齐这三件事后重写为 P0–P6。

---

## 二、三条 Blocker（不解决就别开工）

### B-1 环境真值：全系统当前跑不了 CubeSandbox，提案却在承诺 100% MicroVM

实测（2026-10-16）：

| 位置 | 事实 |
|---|---|
| `.env.example:98`、`docker-compose.yml:155`、`docker-compose.prod.yml:158`、`helm/cds/values.yaml:92` | `CDS_CUBE_DATAPLANE_ENABLED` **四份部署配置全部 = false** |
| 远程演示机 `100.112.5.253` 容器 env | `CDS_CUBE_DATAPLANE_ENABLED=false`、`CDS_SANDBOX_LEGACY_ENABLED=false` |
| 远程机硬件 | 无 `/dev/kvm`、`vmx\|svm`=0（SS-10 §八已定案：这台机器上 Cube 真实执行链**永不可达**） |
| 本机（当前开发机） | `virt=none`（裸金属）、`/dev/kvm` 存在、VT flag=24、`/usr/local/services/cubetoolbox/` 全套 CubeAPI/CubeMaster/CubeOps/Cubelet 已装、tap 设备 503 个、本地 `l-cds-api` 容器 env 已是 `CDS_CUBE_DATAPLANE_ENABLED=true` |
| 本机运行镜像 | `cds-api:latest` 构建于 2026-10-14，`NOT_IN_RUNNING_IMAGE`（SS-15 的 notebooks/workloads/deliveries/compute/open_api/flow_runtime 六个路由**一个都不在**运行镜像里，`/api/v1/flow/runtime-status` 实测 404） |
| cube 服务进程 | `ps` 无任何 cube 进程 → 控制面/数据面**未启动** |

后果：在远程演示站上，开发者工作台的每一个动作（申请调试机、执行、发布）都会落到 `RUNTIME_UNAVAILABLE`；本机配置对了但镜像落后且 cube 没起。也就是说**新前 18 屏现在无一可验证**。
→ 必须有 **P0 环境阶段**：本机起 cube 控制面 + 起新版镜像 + 跑通一次真 l2c 执行，拿到 `runtime-status=ready` 与一条真实 `sandbox_sessions(sandbox_level=l2c)` 记录，作为后续所有前端阶段的验收基线。

### B-2 合规口径收缩：spec 删改把"可选硬件/多场景"当成"历史包袱"删掉了

`git diff specs/product-spec.md` 删掉了原 §3.3 的四类沙箱（标准分析 TEE/SGX、模型训练 VM/SEV、联合计算 MPC+TEE、API 服务沙箱）与五条隔离能力要求（内存加密、网络隔离、存储擦除、运维隔离、代码隔离）；`git diff specs/frontend-spec.md` 删掉了 §1 **四角色×九模块权限矩阵**（数商/买方/运营方/监管）与 §2 布局。

问题：
1. 用户的 SS-13 决策原文是 **microVM 单一路径 + TEE 硬件可选（没有就直接 microVM）**，不是"废除 TEE/MPC"。`app/services/sandbox_runtime.py:1818-1824` 仍注册 `TEEAdapter`/`BwrapAdapter`/`FirecrackerAdapter`/`K8sRuntimeAdapter`，只是收在 `SANDBOX_LEGACY_ENABLED` 门后（默认关）；`mpc_service`（Shamir 密钥托管）、`app/api/mpc.py`、`Identity/`、`federation/` 等能力仍在产品面。
2. 四角色权限矩阵是后端 RBAC 的对外契约（`cds-frontend/src/utils/roles.ts:26-46` 17 个 ROLE_GROUPS、`app/api/*` 的 `require_group(...)`），删掉矩阵等于让 FE 单侧改写角色语义 → 正是 `specs/AGENTS.md` 禁止的"spec 与代码背离"。
3. `禁伪造`/诚实降级铁律要求：退役不是删除。后端已经用 `LEGACY_LEVEL_RETIRED` + `docs/error-codes.md` + 前端 `errorHints.ts:104-109`（"该沙箱级别已退役，仅支持 l2c … CDS_SANDBOX_LEGACY_ENABLED=true"）实现了诚实表达，提案的"彻底下线"反而更弱。

→ 处理：`product-spec.md` / `frontend-spec.md` 的这次改写**退回重写**（保留新架构叙述，恢复被删的两张表并把它们改标为"管理态能力 / 可选硬件加速（默认关，需显式开闸）"），在 B-2 修完前这两份文件不得进入提交。

### B-3 重复建设：9 个"新页面"有 7 个已存在（其中 1 个存在两份）

`cds-frontend/src/pages/` 实测 44 个 `.tsx` / 13,418 行；`src/pages/Developer/` 不存在（`NO_DEV_DIR`）。

| 提案页面 | 判定 | 已有实现（实测） |
|---|---|---|
| TaskList / TaskCreate | **重复** | `Sandbox/SessionList.tsx`(294) 列表+创建弹窗；`Flow/NotebookWorkbench.tsx`(807) |
| TaskIde | **重复 ×2** | `Flow/NotebookWorkbench.tsx`(807，Monaco + dev-session + Jupyter 状态)、`Sandbox/DataProductDev.tsx`(359)；终端 `components/Sandbox/ExecStreamTerminal.tsx` |
| ResultsHub | **重复** | `Flow/DeliveryCenter.tsx`(1048)：数据集包/水印/OpenAPI 端点/Webhook 投递与重投全在里面 |
| ApiCreate / ApiMonitor | **重复** | `DeliveryCenter.tsx:391-460`（端点表单）、`:508-518`（调用/配额列） |
| Workloads / 发布流水线 | **重复** | `Flow/WorkloadCenter.tsx`(603) test→publish→retire→rollback |
| SecurityLog + 验真 | **重复（含内嵌验真）** | `Audit/AuditLog.tsx:41-47,97-103` 已有"验真"按钮调 `GET /audit/verify/{id}`；`Sandbox/ProofBundleDrawer.tsx`(201) |
| Guide / Quota | **真缺** | 全仓 grep 指南/guide = 0；Quota 只是 `DeliveryCenter.tsx:460,508-518` 的字段，无页面 |

→ 处理：**壳 + 复用**路线（P2/P3/P4 只新建 Layout、路由分流与 6 个弹窗/抽屉，页面主体复用现有 4 个 Flow 页 + Audit 页），旧路由保留 301 式 `<Navigate>` 别名，唯一会红的测试是 `src/components/Layout/__tests__/Sidebar.test.tsx:57`（断言 operator 能看到「K8s 运营」），必须按新角色语义**改写**该断言而不是删测试。

---

## 三、API 映射对账（提案第四章 → 真实路由）

真实前缀（`app/main.py`）：`/api/v1/notebooks`、`/api/v1/workloads`、`/api/v1/compute`、`/api/v1/deliveries`、`/api/v1/audit`、`/open/v1`，全部挂 `require_group(...)`。

| 提案声明 | 真实 | 判定 |
|---|---|---|
| `POST /notebooks/{id}/dev-session` | `app/api/notebooks.py:278` | ✅ |
| `POST /notebooks/{id}/cells/execute`（trusted_runner 保护） | `notebooks.py:291` | ✅ |
| `POST /workloads/{id}/publish` | `app/api/workloads.py:188` | ✅ |
| **`POST /workloads/{id}/run`（正式执行）** | 不存在；正式执行入口是 `POST /api/v1/compute/runs`(`app/api/compute.py:90`)，`ComputeRunCreate` 只有 `contract_id`(`app/schemas/flow.py:67-68`) | ❌ 路径错 |
| `GET /workloads/{id}/test-runs` | `workloads.py:120` | ✅ |
| `GET /deliveries/bundles` + `/bundles/{id}/download` | 真名 `POST /deliveries/dataset/bundle`(`deliveries.py:100`)、`GET /deliveries/dataset/{bundle_id}/download`(`:182`) | ⚠️ 名称错 |
| `GET/POST /deliveries/endpoints` | `POST/GET /deliveries/openapi/endpoints`(`:486`,`:539`) | ⚠️ 名称错 |
| `GET/POST /deliveries/webhooks` | `deliveries.py:242,281`（另有 `rotate-secret`/`test`/`deliveries`/`redeliver`） | ✅ |
| `POST /open/v1/{slug}` | `/open/v1` 前缀已注册(`main.py:46`) | ✅ |
| `GET /api/v1/audit/logs` | 真名 `GET /audit/records`(`app/api/audit.py:54`) | ❌ |
| `POST /api/v1/audit/verify` | 真名 `GET /audit/verify/{record_id}`(`:201`)，另有 `GET /audit/merkle-proof/{record_id}`(`:260`) | ❌ 方法+路径错 |
| 数据字典 7 张表 | `notebooks`/`workloads`/`sandbox_sessions`/`delivery_bundles`/`api_endpoints`/`webhook_subscriptions`/`webhook_deliveries` 全部存在 | ✅ |
| `workloads(is_latest, content_hash)` | `is_latest` 存在(`app/models/workload.py:52`)，`version`(`:50`)，**哈希列叫 `entrypoint_hash`(`:42`)** | ⚠️ 字段名错 |
| 错误码负载门族 5 码（`WORKLOAD_NOT_TEST_ENV`/`WORKLOAD_TEST_REQUIRED`/`WORKLOAD_TEST_NOT_PASSED`/`WORKLOAD_HASH_MISMATCH`/`WORKLOAD_STATE_CONFLICT`） | 代码里真实存在(`app/services/workflow_service.py:31-35`)，2026-10-16 已全部登记进 `docs/error-codes.md`（412 段，含触发点与用户下一步） | ✅ 我方欠账已还（远程 `test_error_contract.py` 8 绿 + `test_capability_matrix_consistency.py` 3 绿） |
| 错误码 `DLP_BLOCKED` | `docs/error-codes.md` 与 `app/` 均无 | ❌ 发明码；真实出口拦截语义是 `OUTPUT_BLOCKED` |
| 错误码 `QUOTA_EXCEEDED(429)` / `SESSION_TERMINATED(410)` | `app/core/errors.py` + 契约文档 | ✅ |

**给派工方的硬规则**：任何 FE 任务书里的端点必须写成"真实路径 + handler file:line"，禁止引用 PRD 第四章原表。

### 三·补 鉴权与密码学身份（第二路审计补正，来自导入的路由表 + 逐 handler 阅读）

| 项 | 实测事实 | 对原型的影响 |
|---|---|---|
| `/open/v1/{slug}` 并非开放接口 | `main.py:346` 挂 `gateway` 组 + `get_business_user`，并有合约当亣校验（`app/api/open_api.py:38`） | P-05 “外部 agent 匿名调用”叙事不成立；在线调试弹窗必须用真实凭证 |
| 权限组分散 | notebooks/workloads/flow=`sessions`，compute=`pipeline`，deliveries 与 `/open/v1`=`gateway`，audit=`audit`（`app/core/scopes.py:34-52`） | 一个平台 API Key 要 **4 个组**才能跑完开发者旅程；`groups` 选项得进密钥创建页 |
| Webhook 签名 = **HMAC-SHA256** | `sign_payload`=`HMAC_SHA256(secret, f"{timestamp}.{body}")`（`app/services/delivery_service.py:51-52`），头 `X-CDS-Signature-Sha256` | 原型 P-02/P-06 写的“SM2 签名凭证”是**密码学身份错标**，必须改（禁伪造口径） |
| 退避重试 | `30s × 3^(attempt-1)`，上限 3600s，`max_attempts=6`，连续 10 失败自动暂停订阅（`delivery_service.py:34-36,158-160`；`webhook_delivery.py:39`） | P-06 的“退避重试次数”可直接渲染真值 |
| 投递不依赖调度器 | `webhook_dispatcher_loop` 是**进程内 asyncio**，受 `WEBHOOK_DISPATCH_ENABLED` 与 `TESTING!=1` 门控（`app/main.py:145-150`） | API 进程宕机=不投递；P-08 配额页要诚实标注该限制 |
| 发布产生**新 prod 行** | `workflow_service.publish` 新建 `version=max+1`、`is_latest` 下移（`workflow_service.py:225-256`） | ExecuteModal “选已固化版本”必须按新 id 选择，不能假设 id 不变 |
| 排期不是 cron | 频率/时/分/tz 存在 `Contract`（`app/models/contract.py:95-103`），`GET /api/v1/compute/schedule` 已返回 `next_compute_at`/`last_computed_at`（`app/api/compute.py:58`） | 首页/P-08 直接复用，无需新端点（除租户配额） |
| 运行时无模拟位 | `runtime-status` 只回 `{available, reason∈(null,dataplane_disabled,no_ready_node), ready_nodes, total_nodes, checked_at}`（`app/api/flow_runtime.py:52-67`），**无 `is_simulation`** | 如果要满赿诚实降级铁律，`runtime-status` 应补 `is_simulation`/`cube_version` 位（归 P0） |

---

## 四、8 项闭环补齐的真实工作量

| 编号 | 真实缺口 | 层 | 规模 |
|---|---|---|---|
| P-01 执行确认弹窗 | 后端 `POST /compute/runs` 只接 `contract_id`；**无运行参数、无版本选择、无逐次规格覆盖**（`ComputeRunCreate`、`compute_runs` 表均无 params 列）→ 需要新字段/新 schema + 把参数注入 MicroVM + 审计 + 迁移 | **后端 + 迁移** | M |
| P-02 运行日志抽屉 | 后端已有 `GET /sandbox-sessions/{id}/logs`(`app/api/sandbox_sessions.py:2468`)；`flow.ts` 未封装；DLP 审查流水在 `OutputControl/InspectionPipeline.tsx` 侧 | 前端 + service | S |
| P-03 固化发布弹窗 | 后端齐（`/test`、`/publish`、`entrypoint_hash`、`WORKLOAD_TEST_REQUIRED` 门）；`WorkloadCenter` 已有按钮，缺"从 IDE 一键固化" | 前端 | S |
| P-04 结果集三通道中心 | `DeliveryCenter.tsx`(1048) 三通道全有，缺信息架构（Tabs 化 + 从任务跳入 + 脱敏抽样预览）；**迨到硬缺口：后端根本没有结果包列表端点**（`delivery_bundles` 只在 `deliveries.py:158` 写入，全仓无 `GET` 列表） | 前端重构 **+ 1 个后端只读列表端点** | M |
| P-05 OpenAPI 在线调试弹窗 | 端点齐（`/open/v1` + 端点 CRUD）；缺 FE；注意浏览器直连 `/open/v1` 的鉴权与同源代理策略要先定 | 前端 + 1 个决策 | S |
| P-06 Webhook 配置与投递监控 | 后端全齐（含 rotate-secret/test/redeliver），FE 已在 `DeliveryCenter.tsx:631-793` | **无缺口**（改为搬到 P-04 的 Tab） | XS |
| P-07 存证验真报告 | `GET /audit/verify/{record_id}`(`app/api/audit.py:202`) 重算 SM3 + 重建批根 + 链适配器验锚；`GET /audit/merkle-proof/{record_id}`(`:261`) 回 `record_hash/merkle_root/proof_path/leaf_index/total_leaves/tx_hash`。**区块高度 MISSING**：`block_number` 存在 `AnchorRecord`(`app/services/blockchain_adapter.py:41`) 并已持久化(`app/models/blockchain_anchor.py:33`)，但 `app/api/` 零处返回 | 前端 + 小后端（暴露 block_number + **诚实标注存证后端**，默认是 `pg-log` 追加型哈希链而非真链） | M |
| P-08 指引与配额页 | Guide 纯静态（真缺）；**Quota 需要后端只读聚合端点**：现有 `quota_manager`/`check_tenant_quota`(`app/api/sandbox_sessions.py:31,367`) 只在创建时计数，无对外配额/用量端点；`/monitoring/task-trend` 可复用给首页趋势图 | **后端 + 前端** | M |

---

## 五、修订后的研发计划（P0–P6，可直接派给 AI Agent）

前置门槛 **G0**：当前有 **79 项未提交**（41 未跟踪 + 38 修改），其中包含全部 SS-15 后端路由与 Flow 前端页。**G0 = 用户一句「cr 并提交到远程」**；未提交前，任何 Agent 都工作在唯一副本上，无法并行、无法回滚。（本轮不自动提交。）

| 阶段 | 目标 | 关键任务 | 验收门（必须可观测） |
|---|---|---|---|
| **P0a 远程测试环境（当前基线）** | 拿到一台可复现的权威验证面 | 远程机 `/root/cds-test/remote_test_env.sh`：依赖镜像（bubblewrap+pillow+cryptography）+ 树 md5 对账 + `alembic upgrade head`（含 0019-0021）+ `alembic check` + schema 对账 + CI gate 子集 + SS-15 子集；无 KVM 主机上执行路径走 bwrap（`CDS_SANDBOX_LEGACY_ENABLED=true`）并沿用 `is_simulation`/`dataplane_disabled` 诚实披露 | 已实测（2026-10-16）：镜像/编译/导入/迁移/对账均 `STEP_OK`，**gate 子集 231 passed / 2 skipped / 0 failed（200s，34 文件）**；树对账门当场逮到 3 个测试文件漂移（旧门只比 `app/`，已扩为 `app+tests+alembic`）。业务流在本机的执行出口经实测受三道诚实闸门：`RUNTIME_UNAVAILABLE`（`reason=dataplane_disabled`，“按 SS-13 不降级到进程隔离执行”）、`LEGACY_LEVEL_RETIRED`、以及合约侧 `Sandbox level 'L3' not allowed by contract` —— 因此**不得靠改合约放行级别把演示“做完”**，执行证据只仍由 pytest lane 提供 |
| **P0a·附 远程站点执行探针结论** | 知道哪些步骤可在远程真验 | 上传→目录→合约→交付配置→审计 全部可在远程验证；notebook/dev-session 硬锁 `l2c`（`app/api/notebooks.py:115`）→ 在无 KVM 机上必为诚实 400；镜像已加 bwrap 薄层（`cds-api:bwrap`）、容器已改 `--restart unless-stopped`，业务姿态保持 `CDS_SANDBOX_LEGACY_ENABLED=false` | 站点 `:3000` 200、`/health` ok、`runtime-status` 仍诚实返回 `dataplane_disabled` |
| **P0b 真 MicroVM 节点（硬件门控）** | 拿到一台能真跑 l2c 的 KVM 机器 | 已实测：远程机为 OpenStack 虚机，CPU 未暴露 `svm`、`modprobe kvm_amd/kvm_intel` 均 `Operation not supported`、`dmesg: kvm: no hardware support` —— **客户机内无法启用 KVM**；Cube 在远程也未安装。可用 KVM 的只有一台已装 Cube v0.7.3-rc2 的裸金属（本机），但远程→本机回访实测不可达，只能靠反向隧道或直接把裸金属当节点；裸金属侧待办：`CUBE_SANDBOX_NODE_IP` 仍指旧 IP `10.206.60.150`（11 个渲染文件引用），需 `--node-ip` 重跑上游安装器 | `GET /api/v1/flow/runtime-status` → `ready`；新建一条 `sandbox_sessions` 记录 `sandbox_level='l2c'` 且状态 running；一次真实 cells/execute 退出码 0 |
| **P1 契约对账** | 消灭 404 与发明字段 | **实测对账已完成（2026-10-16）**：`cds-frontend/src/services/*.ts` 的 182 条字面量调用 vs `app.main` 真实路由 —— 失配仅 3 条，其中 2 条是缺尾斜杠靠 307 兜（已补 `/connectors/`、`/data-resources/`）、1 条是后端固定名模板路由（前端 4 个 key 与后端 4 路由一致，非缺陷）。即**现有前端服务层没有发明端点，发明只发生在 agy 的 PRD/原型**；**已完成的修正**：PRD 第四章 `content_hash`→`entrypoint_hash`、`DLP_BLOCKED (409)`→`OUTPUT_BLOCKED (502)`（`notebooks.content_hash` 经核实为真列，保留）；负载门族 5 码 + 全仓扫出的 7 码已登记 `docs/error-codes.md` | 新增守卫 `tests/test_frontend_api_contract.py`（**已进 CI gate，远程 3 绿，含守卫的 35 项清单 234 passed / 2 skipped / 0 failed（200.50s）**）：CI 期把前端调用与真实路由对账、禁 307 依赖、模板名两侧一致；PRD 内每个端点仍需在 `app/api/*.py` 找到 handler；两份被删表按 B-2 恢复 | **PRD 端点普查已完成（2026-10-16）**：PRD 全文只有 2 处端点字面量（它是逐页 UI 描述件，不是 API 契约）——`/open/v1/{slug}` 为真路由；`/api/v1/workloads/{id}/run` 为**发明端点**（真实只有 publish/retire/rollback/test/test-runs，无 `/run`），已改为真实的 `POST /api/v1/compute/runs` 并注明“按负载选版本/传运行参数”尚未实现（归 P4）。取表仍用递归 + `openapi()` 并集的版本无关写法 |
| **P2 双模式壳** | 开发者工作台 / 管理员工台 双视图，**复用现有页面与现有路由，不新建 `Developer/` 页面树**（本节原方案里的 `DeveloperLayout.tsx`/`TopHeader` 属 agy 原框构，按 §二 “复用而非重建” 判定不采纳；也未采用其 4+2 菜单命名，因为三段业务闭路必须在工作台内可直接达） | 已实现（2026-10-16）：`utils/workspace.ts` 纯函数（`canAccessAdminWorkspace`/`defaultWorkspace`/`resolveWorkspace`/`filterMenuByWorkspace`，白名单**只登记叶子路由**，父分组凭存活子项保留）+ `stores/workspaceStore.ts`（zustand persist，`workspace=null` = 未选过、按角色定默认）+ `Header.tsx` 的 `Segmented` 开关（仅运营/监管/管理员可见）+ `Sidebar.tsx` 应用过滤；**额外堵掉一个真问题**：`App.tsx` 的 `capability-matrix` 原先没包 `RoleRoute`，任何登录角色敲 URL 即可看隔离能力/认证后端矩阵 → 现改为 `ADMIN_WORKSPACE_ROLES`；`ErrorCard` 的能力矩阵深链改为同条件下才渲染（判据写的“5 处深链”实测只有 1 处，对无权角色隐入口优于留一个必被弹回的按钮） | 本地预言机：`tsc -b` 干净、`vitest` **9 文件 / 125 通过**（新增 `utils/__tests__/workspace.test.ts` 6 例、`Feedback/__tests__/ErrorCard.test.tsx` 3 例、`Sidebar.test.tsx` 追加 3 例，未改动任何现有断言）；非 admin 访问 `/ops/*` 已由现有 `RoleRoute`（`k8sOpsReaders`）拦住，本轮核对确认；权威数字已出（远程 `fe_verify3.sh`，已修为容器内 `set -o pipefail` 的诚实 harness）：`npm ci` ✅ / `tsc -b` ✅ / `npm run build` ✅ / `vitest` **9 文件 / 125 通过** ✅（与本地预言机逐字一致）/ `npm run lint` **❌ 13 errors**。那 13 条全在与 `HEAD` 字节相同的文件（`a77bf27` 2026-06-09 … `5d5417e` 2026-10-15），`ci.yml` 无 FE job 所以 CI 不暴露；属存量债，已列为独立待办（修代码或建 FE lint 基线并把 `npm run lint` 纳入 CI），不在 P2 里顺手改。**线上实测（2026-10-16，`http://100.112.5.253:3000`，Bun.WebView headless）**：站点已换当天构建（入口 `index-CJOYCmjc.js` → `index-DMAE8cos.js`）；管理员 `qa_ss15_admin` 登录后落管理员工台、侧栏 **17 项**（含 K8s 运营 / 监控中心 / 身份管理 / 用户管理 / API 密钥），切到开发者工作台剩 **8 项**（K8s 运营·监控中心·身份管理·用户管理·API 密钥·连接器管理·跨空间联邦·数据管线全部消失），买方 `qa_ss15_buyer` **5 项且无切换器**；1440×900 无横向溢出（docW=scrollW=1425）、侧栏 244px、切换器 (833,20,186px) 在视口内、菜单零截断、零 `>=400` 资源请求。**实测抓到并修复的缺陷**：工作台偏好持久化在浏览器全局键 `cds.workspace` 且**登出不清**，导致同一浏览器下一次登录（含同一运营重新登录）落在上一个会话的工作台 → `workspaceStore` 新增 `reset()`、`authStore.logout()` 调用它，并在 `authStore.test.ts` 补一条会失败的原用例（数字待 FE lane 远程出数） |
| **P3 我的开发闭环** | 复用而非新写 IDE | 复用 `NotebookWorkbench`（作 TaskIde 主体）+ `WorkloadCenter`；新增 `PublishModal`、`RunLogDrawer`；Task 聚合视图 = **前端 merge notebooks+workloads**（不新建表、不加聚合端点，规则写进 spec） | 一条任务从 创建→调试→固化→执行→日志 全绿，且日志里能看到真实 MicroVM 容器 ID |
| **P4 运行参数（新增后端面之一）** | 让 P-01 成立 | `ComputeRunCreate` 增 `workload_id`/`params`/`resource_spec` 覆盖；`compute_runs` 增列（**新建 alembic 修订，线性链尾，禁改历史**）；参数注入执行链 + 审计记录；补 20 码风格的错误语义 | 远程定向 pytest 新用例绿；`alembic upgrade head` + `alembic check` 干净；`python scripts/verify_schema_parity.py` 通过 |
| **P4·安 出口审查补齐（安全，优先级高于所有页面）** | 堵两个真缺陷 | **①② 已实现（2026-10-16）**：`_review_output_bytes()` 接 `output_inspector.inspect` + 复用现成的 `should_block()`（`BLOCKING_SEVERITIES`），在**写 zip 与上传之前**逐文件审；语义与单文件下载（SS-14 RT-07）**对齐**：block → `OUTPUT_BLOCKED`(502，已登记码，非新发明)且不上传不落库；有发现且可脱敏 → 交付脱敏文本（实测手机号→`[REDACTED:phone]`）；非 UTF-8 二进制 → 不静默跳过而是写 `status=skipped_binary` 并于 `manifest.output_review_scope` 诚实披露；拦截/拒绝留痕走**独立会话**（`async_session()`+`commit`，照 `session_stream.py:288` 先例，因为 `get_db` 会在异常时回滚整个请求会话）。存量包（无审查标记）在 `GET /dataset/{id}/download` 按 **409 `OUTPUT_REVIEW_REQUIRED`**（已登记）拒出域并要求重新生成，与本端点对 0021 前历史包的 fail-closed 姿态一致。③ OpenAPI 导出口已查实（2026-10-16）：`POST /open/v1/{slug}` 不是裸返回——`delivery_service.execute_endpoint` 走 `execute_in_sandbox`，后者在 `sandbox_sessions.py:1376-1398` 引 `output_security.should_block` 对执行输出做 T5 拦截，OpenAPI 继承同一道门禁；真正缺的是水印（数据集包与单文件下载有，JSON 响应无）。对 JSON 响应注入零宽水印会破坏调用方的字节级哈希契约，本轮明确不做，取而用 `openapi.call` 审计里的 `request_sha256` 与响应行数做溯源。**同类缺陷已收敛为一处入口（2026-10-16）**：新增 `audit_service.log_standalone(**kwargs)`（内部开独立会话 + `commit`；`async_session` **必须函数级导入**，否则绕过 `tests/conftest.py:83` 的 `db_module.async_session` 补丁）。上述 11 处已用 AST 逐个确认“审计后确有 raise”，其中 **9 处已改**：`auth.py:179/192/211/495`（登录失败 / MFA 缺失 / MFA 错误 / 证书登录拒绝）、`sandbox_sessions.py:1237/1727/1880/1955`（代码扫描拒绝 / 自动恢复拒绝 / 两处单文件出口拦截）、`connectors.py:606`（连接器执行前扫描拒绝）。**`connectors.py:449/492` 故意不改**：它们先写业务状态（`session.status=FAILED`、密钥分发失败）再 raise，属“部分失败状态要不要随请求事务一起丢”的事务设计决策，不是一行替换能了账，已单独立项。测试边界说清：`test_login_failure_records_audit` 只钉住“拒绝留痕确实写入且 payload 正确”，**不能**证明“回滚后仍存活”（`override_get_db` 既无 commit 也无 rollback，该属性在测试体系内不可判别）——后者由读 `get_db` 源码得出 | 新增缺陷回归用例 **4 条全部可失败**（本地绿；远程待跑）：身份证文本走 `/dataset/bundle` → 502 `OUTPUT_BLOCKED` 且 `storage_service.upload` 零调用、无 `DeliveryBundle` 行；高危手机号 → 201 但包内实为 `[REDACTED:phone]`（原文不得残留）且 `MANIFEST.json` 标 `redacted`；干净文本+二进制 → `passed`/`skipped_binary` 分开且字节不变；无标记存量包 → 409。**远程权威复验完成（2026-10-16，真实 PostgreSQL 16 + py3.12 生产派生镜像）：含这 4 条用例的 SS-15 子集 **83 passed / 0 failed（72.28s）**（基线 79 + 新增 4），同树门禁 **234 passed / 2 skipped / 0 failed（203.26s）**。**已知不一致（存量行为，本轮不改）**：RT-07 单文件下载仍用非 W2 形体的 `HTTPException(409)`，与新包路径的 `OUTPUT_BLOCKED`(502) 不统一，待单独一轮统一到登记码 |
| **P5 结果集与交付中心** | IA 重构 + 两个新弹窗 | `DeliveryCenter` 拆三 Tab（文件/OpenAPI/推送）；**新增 `GET /deliveries/dataset/bundles` 只读列表端点**；`DataPreviewModal`（脱敏抽样预览，走 T5 出口策略，**预览本身必须过出口审查**，不得绕过 DLP）；`ApiDebugModal`（真实凭证，多组权限的 API Key）；`Quota.tsx` + 新只读端点 `/deliveries/quota-usage`（或 `monitoring` 下；现有 `quota_manager`/`check_tenant_quota` 只在创建时计数，无对外端点） | 三通道各一次真实成功（下载包/OpenAPI 调用/webhook 200 投递），预览数据与出口策略同源 |
| **P6 存证验真 + 收尾** | P-07 + 指引 + 门禁 | `AuditVerifyModal` 封装 `verify` + `merkle-proof` + `block_number`；**修验真可重现性**：两处重建查询缺 `ORDER BY`（`app/api/audit.py:224-227`、`:288-291`）→ 根依赖 DB 行序，必须按确定序重建并加用例；`merkle_leaves` 表全仓**无写入方**（仅 `alembic/versions/0000_baseline_all_tables.py:648` 建表与 janitor 引用），要么接上要么在文档标为废弃位；验真报告必须披露实际哈希算法（gmssl 真 SM3，否则 `CDS_SM3_FALLBACK_ALLOWED` 下的 SHA-256，`app/utils/crypto.py:34-58`）；`Guide.tsx` 静态页（目录约定 `/sandbox/data` 只读、`scripts/`、`/sandbox/output`）；回归 | 见下方"统一验收门" |

**统一验收门（每阶段末尾都要过）**

1. 测试一律在**远程 harness** 跑（用户铁律）：`pytest` 定向 + 全量回归走远程 py3.12 镜像，本地只做 `compileall` / 配置解析 / FE 构建这类廉价预言机。
2. `ci/known-failures.md` 基线**不得增长**；gate 子集全绿，全量作参考。
3. 诚实降级：任何未接真硬件的路径必须继续走 `runtime-status` + `RuntimeStatusBanner` 显式披露，禁止把 sim 当 real 展示（`is_simulation`/`ALLOW_SIMULATION`）。
4. 前端不得出现硬编码集群名/租户（原型里 `沈阳数据中心R` 出现 7 次 → 必须来自配置端点）。
5. 五份部署配置同步：任何新 `CDS_*` 开关同时落 `.env.example` / 两份 compose / `k8s` / `helm`（本仓已知漂移点）。
6. 提交纪律：整理好索引后用**不带 pathspec** 的 `git commit`，并比对 `git diff --cached --stat` 与 `git show --stat`（本轮已踩过 `git commit -- <path>` 读工作树的坑）。

---

## 六、明确不做 / 驳回

1. **不新建 `src/pages/Developer/{Tasks,Results,Security}` 页面树**（§二 B-3）；只建 Layout、分流与弹窗组件。
2. **不删 `/ops/*` 页面与 `k8sOpsApi`**：k8s 运维视图在 `SANDBOX_LEGACY_ENABLED` 关闭时是"空但有意义"的运维面；改为 admin-only。`/capability-matrix` 页面被 `ErrorCard` 5 处深链依赖，删除会让错误提示变死链。
3. **不在 FE 造 Task 实体/新表**：Task = notebook + workload 的读视图，映射规则写进 spec。
4. **不接受"100% 运行于 KVM MicroVM"作为当前对外口径**，在 P0 通过前对外口径仍是"唯一执行路径 = CubeSandbox 数据面（未启用时显式不可用）"。
4b. **不接受原型里的三处对外口径**：webhook“SM2 签名凭证”（实为 HMAC-SHA256）、外部 agent 匿名调 `/open/v1`（实需 `gateway` 组凭证 + 合约当亣校验）、“区块链存证”默认是真链（实为 `pg-log` 追加型哈希链，后端必须随报告披露）。
5. **不接受提案 Phase 6 的验收方式**（只跑本地 `npm run build && npm test`）：漏掉后端 pytest、alembic、schema parity、远程 harness、诚实降级审查。
6. **PNG 原型资源（`基础版数据沙箱原型/`，2.5MB / 10 文件）不进仓**，改放外部附件或 `docs/assets` 且只留引用；原型 HTML 与 `prototype.html` 同级定位：视觉稿，**永不作为功能面**。

---

## 七、我建议的执行顺序（今天就能定的部分）

1. 你说「cr 并提交到远程」→ 收掉 G0（79 项，含本文件与 SS-10 §八 item 7）。
2. 我做 P0：本机 cube 起服务 + 重建镜像 + 打通一次真实 l2c 执行（这是唯一需要 root/本机资源的一步）。
3. P1 代码侧已闭环（对账 + 守卫 + 错误码登记，均远程出数），剩 PRD/旧 spec 的文字修正。
4. P2/P3/P5/P6 派 2–3 个 Agent 并行（互不重叠目录：Layout 与路由 / Flow 页重构 / 审计弹窗），P4 串行（含迁移，只能一人）。

## 八、执行记录（2026-10-16 收口轮）

| 项 | 权威结果（远程实测） | 口径 |
|---|---|---|
| 全量回归 | **2958 passed / 22 failed / 10 skipped**（1912.82s） | 22 条全部落在既有环境类别（13 `test_cds_volume_plugin` 需 CB-03 卷插件 socket、4 `test_pz01_sft_real` peft 版本耦合、3 `test_cube_upstream_check` 镜像内无 git、`test_e2e_flows` + `test_async_operations` 跨事件循环连接池同族）；相对上一轮 24 条**净减 2、无新类别** ✓ 基线未增长（`ci/known-failures.md` 已记最终行） |
| 门禁层 | **234 passed / 2 skipped**（203.26s） | 清单以 `.github/workflows/ci.yml` 为唯一真源，lane 现场解析得 **`GATE_FILES=35`**（先前推定的 36 是把 `tests/test_auth.py` 重复计数，已按实测更正） |
| SS-15 三段流程子集 | **83 passed**（72.28s） | 同树同库（真实 PG16） |
| P2 双模式工作台线上实测 | 通过 | headless 1440×900：管理员 17 项 ↔ 开发者 8 项、买方 5 项且无切换器；零横向溢出、侧栏零截断、零 ≥400 资源请求。**当前模型不能看图，未做视觉目测** |
| 实测抓到并修复的缺陷 | 工作台偏好跨登出残留 | 全局键 `cds.workspace` 登出不清 → 同浏览器下次登录落在上一会话工作台。`workspaceStore.reset()` + `authStore.logout()` 调用 + 新增可失败用例 `authStore.test.ts` |
| 拒绝类审计（部署面） | 通过 | `docker restart cds-api` 后错口令 → 库中 `user.login_failed` + `detail.reason=invalid_credentials`（先前"0 条留痕"的真因是挂载树下旧进程仍持旧模块，非代码缺陷） |

**lane 口径变更（用户指示）**：远程机只做测试、直接用源码，暂不做镜像构建测试。主机已补装 `/opt/py312`（python-build-standalone 3.12.15，npmmirror）+ `/opt/node20`（v20.18.1）；后续权威数字来自**主机源码直跑 + 同一真实 PostgreSQL**，与本轮镜像 lane 的 22 条失败清单逐条对齐，新增失败先归因再动代码。

**站点分发变更**：不再为站点构建镜像——前端源码 `npm run build` 出的 `dist/` 直接 bind-mount 给现有 nginx 容器。

**站点基座镜像事实（ddn 渡渡鸟镜像站）**：`swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/library/node:20-alpine`、`.../nginx:alpine` 实测可拉；`cds-frontend/Dockerfile` 已改 build-arg（`NODE_IMAGE`/`NGINX_IMAGE`，ARG 必须在**第一个 FROM 之前的全局作用域**），builder 默认改 **`node:20-bookworm-slim`（glibc）**——`node:20-alpine`(musl) 构建必失败：lockfile 锁了 `@rolldown/binding-linux-x64-gnu`，musl 下 `Cannot find module '@rolldown/binding-linux-x64-musl'`。

**本机做真实 MicroVM 闭环的可行性**（用户新问）：硬件与组件过关（`PASS: kvm` / `cubelet_bin` / `containerd v2.3.3` / `volume_sock`，`tee=none` 如实上报），4 项 FAIL 里 3 项是非 root 伪失败。网络冲突机理已实测清楚：**mihomo 为每个 `route-exclude-address` 插 `to <段> lookup main`，未列入排除表的段整体落进 `table 2022` 的 TUN**（实测 `ip route get 11.0.0.5` → `dev Meta table 2022`；而 `10.100.0.0/18` → `dev cube-dev` 干净）。因此守卫第 10 项新增**路由归属差分探针**（对照地址 `240.0.0.1` 差分，整机默认出口即隧道时如实放行不误拦），配套 3 条新用例。上线前活体三验：归属落 `cube-dev` / 经 clash 出网正常 / **EasyConnect 重连一次**后归属仍不被吞。

