# SS-15：数据流通业务闭环（目录 → 合约 → 在线开发 → 测试/生产沙箱 → 三通道交付）

> 版本：v2.0 | 分系统编号：SS-15 | 日期：2026-10-16
> 上游：SS-11（产品化）、SS-12（平台 API Key）、SS-13（MicroVM 单路径）、SS-10（Cube 数据面连接器）、`specs/data-sandbox-developer-portal-prd.md`
> 前端对应交互原型：`specs/prototype-v3.html`（对齐《基础版数据沙箱原型》）与落地计划 `specs/developer-portal-implementation-plan.md`
> 核心原则：**唯 CubeSandbox MicroVM 运行时**（彻底下线 Bubblewrap 进程隔离与 Kubernetes 容器沙箱），所有调试与生产执行 100% 运行于独立的 KVM MicroVM。
> 范围来源（用户指令 2026-10-15 & 2026-10-16）：①用户上传数据生成数据目录；②通过任务与合约选择数据目录 + 交付方式（数据集 / Open API / 结果推送）+ 是否自动计算 + 计算频率（日/小时）+ CubeSandbox 资源配置（调试8G / 执行16G），创建沙箱任务；③沙箱提供在线 Python Web IDE 开发调试，开发完先在调试沙箱测试，测试通过后固化发布至生产沙箱执行；④通过结果文件下载、OpenAPI 导出、结果推送完成三通道成果交付。
> 交付要求：前后端完整实现，可用并达到产品化使用标准。

## 一、闭环与不变量

```
上传数据 ──▶ 数据产品/目录 ──▶ 合约（数据 + 交付方式 + 自动计算 + CubeSandbox 资源）
                                      │
                    ┌─────────────────┼──────────────────┐
                    ▼                 ▼                  ▼
             在线 Web IDE        自动计算调度        交付三通道
          （CubeSandbox 调试态8G）（日/小时频率）    离线包 / OpenAPI / 结果推送
                    │
             固化版本（entrypoint）──▶ 测试沙箱验证（记录不可绕过）──▶ 生产沙箱发布执行(16G)
```

三条平台不变量（本分系统的全部设计都落在这三条上）：

1. **执行只有一条策略通路，且唯一运行于 CubeSandbox**。在线 Web IDE 脚本运行、工作负载测试、自动计算批处理、OpenAPI 端点调用，全部经 `app/api/sandbox_sessions.py::execute_in_sandbox` 并锁定底层运行时为 CubeSandbox MicroVM (`l2c`)（静态扫描 → KMS 注入 → 场景运行时 → 输出 DLP → 审计）。Bubblewrap 与 Kubernetes 容器沙箱已全面下线，新通道不允许自行调用 runtime，因此不可能成为输出策略的旁路。
2. **未通过测试沙箱的工作负载不能进生产**。发布门在 `workflow_service.publish()` 内实现：状态、`test_run_id` 外键、关联 `WorkloadTestRun.passed`、entrypoint 哈希一致性，任一不满足返回 412 + 机器可读码（`WORKLOAD_TEST_REQUIRED` / `WORKLOAD_TEST_NOT_PASSED` / `WORKLOAD_HASH_MISMATCH` / `WORKLOAD_STATE_CONFLICT`）。测试记录是不可绕过的证据链，不是 UI 状态位。
3. **交付方式由合约声明，平台不越权**。`_require_method(contract, DeliveryMethod.X)` 在每个交付入口前置校验；沙箱模式（`sandbox_mode`）只从合约 `allowed_sandbox_modes` 授权集合里选（§4.3）。

## 二、数据模型与迁移（0019_flow_orchestration）

| 表 | 作用 | 关键约束 |
|---|---|---|
| `notebooks` | 在线 Notebook 文档（nbformat 4.5 JSON）+ 绑定的开发会话 | `content_hash` 为文档 SM3 规范化哈希；`version` 每次内容变更 +1 |
| `workloads` | 沙箱内可运行负载（entrypoint + 资源规格 + env=test/production） | `(contract_id, env, status)` 索引；`is_latest` 支持发布/回滚版本链 |
| `workload_test_runs` | 测试沙箱运行记录（checks / report / session_id） | 发布门读取，禁止被删除后发布 |
| `compute_runs` | 一次自动计算运行（幂等键 + 触发来源 + 输出清单） | `idempotency_key(contract_id, scheduled_for)` 防同刻重复跑 |
| `api_endpoints` | OpenAPI 交付端点（slug + 限流 + 配额 + 请求/响应 JSON Schema） | 密钥只在创建时返回一次；`calls_used` 计量 |
| `webhook_subscriptions` / `webhook_deliveries` | 结果推送订阅与逐条投递（签名、退避、重投） | 密钥列表接口不回显；连续失败熔断阈值 `MAX_CONSECUTIVE_FAILURES` |
| `delivery_bundles` | 数据集交付包的归属记录（包 → 合约/会话/创建人） | 下载端点先查归属再按合约当事方鉴权；无记录即 404（fail-closed，0021） |

`contracts` 增列：`delivery_methods`、`auto_compute`、`compute_frequency`（daily/hourly）、`compute_hour`、`compute_minute`、`sandbox_resource_spec`、`allowed_sandbox_modes`、`next_compute_at`（索引 `ix_contracts_next_compute_at`）。`sandbox_sessions` 增 `workload_id`（索引），把会话回指到负载，使审计/证明包能证明"这次执行属于哪个已发布版本"。

Alembic 链保持线性、只增不改：0019 接 0018；PG 与 SQLite 双通道 `alembic upgrade head` + `alembic check` + `scripts/verify_schema_parity.py` 均在远程复证（§六）。0020 把推送投递历史从订阅外键上解绑（删订阅不再 500，投递记录按留存策略不删）；0021 新增 `delivery_bundles` 归属表，把交付包下载从"猜到 UUID 即可取件"改为按合约当事方鉴权。

## 三、后端 API 面

| 前缀（main.py 唯一赋值点） | 路由模块 | 职责 |
|---|---|---|
| `/api/v1/notebooks` | `app/api/notebooks.py` | CRUD + `/dev-session`（幂等取/建测试沙箱）+ `/cells/execute` + `/run` + `/jupyter`（如实探测） |
| `/api/v1/workloads` | `app/api/workloads.py` | 从 Notebook 生成负载、`/test`（测试沙箱）、`/publish`（发布门）、`/retire`、`/rollback`、`/test-runs` |
| `/api/v1/compute` | `app/api/compute.py` | 自动计算开关/预览下次时间/运行历史/手动触发 |
| `/api/v1/deliveries` | `app/api/deliveries.py` | 数据集打包下载（水印 + MANIFEST）、webhook 订阅/密钥轮换/投递记录/重投、OpenAPI 文档与端点管理 |
| `/open/v1` | `app/api/open_api.py` | 外部调用面：`POST /open/v1/{slug}`，平台 API Key 鉴权 + 合约授权 + 限流 + 配额 |
| `POST /api/v1/contracts/{id}/flow-config` | `app/api/contracts.py` | 一次性写入交付方式/自动计算/频率/沙箱资源配置/允许的沙箱模式 |

后台循环（`app/main.py` lifespan）：`compute_scheduler_loop`（按 `next_compute_at` 领取到期合约，日/小时频率在 `compute_next_run()` 计算）与 `delivery_retry_loop`（webhook 退避投递）。两者都遵循"依赖缺失即诚实降级并在日志披露"，不影响主服务启动。

## 四、关键设计决策与取舍

### 4.1 单元格执行的"字节级信任"（trusted_runner）

问题：Notebook 单元格的执行驱动脚本（常驻 kernel 守护 + 载荷封装）是平台代码，但内含 `os`/`socket`/`subprocess`，与用户代码混在一起提交时会被白名单扫描器 422 拒绝。直接放宽扫描器等于掏空 SS-03 的安全边界。

决策：`SandboxExecuteRequest.trusted_runner: bool = False` **不是**信任声明，只是"请求按平台模板校验"。真正的判定在 `notebook_service.split_trusted_driver()`：
- 按模板三段（head/mid/tail）重建脚本，必须与提交脚本**逐字节相等**；
- 可变面仅两个 base64 字段，其中 kernel 源码解码后必须等于受信任模板 `_KERNEL_DAEMON`；
- 通过后**改为扫描内嵌的用户代码**（扫描不消失，只是扫描对象更准确），审计 `sandbox.code_scan` 的 detail 带 `trusted_runner` 披露；
- 任何一字节偏移 → 返回 None → 退回全量扫描（fail-closed）。

同一机制容纳平台常量脚本（`TRUSTED_PLATFORM_SCRIPTS`，当前为 Jupyter 探针）：整段字节等于内置常量才免扫，探针被改写即回到扫描路径。单测 `TestCellDriverTrust` 同时钉住"篡改失信任"与"驱动内的用户代码仍被拒绝"。

### 4.2 平台包装脚本不再借用 `os`/`__import__`

OpenAPI 端点调用的包装脚本原写 `import os` + `__import__('hashlib')`，被扫描器判定 FATAL → 整条 OpenAPI 交付通道在真实执行下 100% 不可用。改为只用白名单模块（`json`/`hashlib`），请求哈希改为脚本内变量 + stdout 披露，不再写 `os.environ`。取舍：宁可让平台代码遵守与用户代码相同的白名单，也不给平台代码开后门。

### 4.3 沙箱模式必须由合约授权集合决定

`workflow_service.resolve_sandbox_mode(contract, preferred)`：优先取调用方期望模式，若不在 `contract.allowed_sandbox_modes` 内则按别名表（`structured_query↔query/read`、`product_dev↔develop` 等，与 `sandbox_sessions._mode_allowed_by_contract` 的语义一致）匹配，仍不中则退回授权集合首元素——替代值一定落在提供者授权集合内，绝不越权，也避免"模型注释写 `["query","train","develop"]`、代码硬编码 `product_dev`"这类真实配置直接 400。已接线四处：Notebook 开发会话、工作负载测试、自动计算运行、OpenAPI 端点调用。

### 4.4 Notebook 文档持久化：纯函数 + 追写语义

`content` 是普通 `JSON` 列（无 `MutableDict`）。原实现在同一 dict 上就地写入 outputs 再赋回**同一引用**，SQLAlchemy 不标脏 → 执行结果静默不落库 → 下一单元格索引越界。修法：`apply_cell_result()` 返回深拷贝新对象（纯函数），路由统一赋新对象并同步 `content_hash` 与 `version`。
同时定义工作台语义：`cell_index == len(cells)` 表示"跑下一个单元格"→ 追写一个新 code cell；跳号仍报 400。执行时把 `body.code` 写回单元格 `source`，保证"发布用的 entrypoint"与"实际跑过的代码"一致（否则证据链与实际执行可能不同源）。

### 4.5 dev-session 阻塞链的真因（三条，逐条实证）

1. `cube_egress` 把 `session.contract_id`（`String(255)` 列）直接与 `Contract.id`（UUID 列）比较，SQLite `Uuid` bind 处理器对 str 调 `.hex` 抛 DBAPIError；异常被该模块的宽 except 吞掉，但共享连接（测试用 StaticPool）事务已坏 → 后续写入静默消失。修法沿用仓库既有收口方式 `uuid.UUID(str(...))`（`output_policy.py`、`sandbox_tasks.py` 早已如此），全仓扫描确认此处是唯一漏网点。
2. `provision` 之前必须 `await db.commit()`：Cubelet 插件在建箱期间用**另一条连接**回调 `/kms/distribute`，未提交的会话行对它不可见。
3. 行缺失时 fail-closed：`CDSError("SANDBOX_SESSION_MISSING", 503)`（detail 带 created_id/type/recent）+ `logger.warning`，不再静默返回半成品。

同轮静态扫描（AST 校验新增模块对 services 的方法调用）抓出 3 处 `session_state_machine.get_status`（不存在的方法）误用 → 改 `is_active(session.status)`，与 `notebooks.py` 内同类问题一并修掉。

### 4.6 测试边界与生产边界的差异（重要，影响可信度）

测试套件用 `StaticPool` + 单一共享连接，服务层内部 `async_session()` 与请求会话实际是**同一条连接**，因此"内部 session 提交/回滚"这类生产语义在默认测试里被掩盖。闭环用例因此引入 `production_db_boundaries` fixture：把 13 个使用 `async_session` 的模块替换为共享 `db_session` 的 `asynccontextmanager`，并把 `get_db` 覆盖为按请求提交，镜像生产的请求边界（teardown 还原）。dev-session 的事务问题正是先在此边界下复现、再定位到 §4.5。

### 4.7 l2c 执行数据面在套件内用本机子进程 stand-in

真实 l2c 的 Exec 需要节点卷插件 unix socket（`CDS_CUBE_VOLUME_PLUGIN_SOCKET`，硬件依赖，fail-closed）。闭环用例用 `local_vm_dataplane` fixture 只替换 `CubeRuntimeAdapter.execute`（monkeypatch，函数级自动还原），把驱动脚本作为**真子进程**执行：内核守护协议、跨单元格状态、envelope 解析、`/tmp` 运行时目录全部真实跑通，被替换的只有 VM 传输层。conftest 已有的 `TestCubeNodeClient` + `prepare_session_volume` stand-in 属同一模式（同一 import 期/函数级打桩约定）。真机数据面的验证归部署环境 e2e（SS-14 路径），不混进本套件。

### 4.8 数据集交付的取舍

打包只走"会话输出文件"（受输出策略 DLP 约束的读取面），512MB 上限直接 413 并提示改走数据产品通道；不可见水印只对文本类注入（`watermark_scope: "text-files-only"`），二进制注入会破坏下游解析，故 MANIFEST 对每个文件如实记录 `watermark_applied` 与原因，不假装"全部已水印"。
交付包元数据（bundle 清单）当前只写审计与返回体，不新增表：bundle 可由 `session_id` + 审计重放复原，加表会引入与审计双写不一致的风险。若后续要做"交付包列表页"，应把清单落到 `delivery_bundle` 表并在审计里保留哈希。

### 4.9 合约模式词表与会话执行档词表必须合一（部署 QA 抓出的 500）

远程部署上用**合约创建页真实提交的字段**跑闭环时，`POST /notebooks/{id}/dev-session` 直接 500：

- 合约侧 `allowed_sandbox_modes` 是业务词（前端选项 `query / train / develop / application`）；
- 会话侧 `sandbox_mode` 是执行档（`VALID_SANDBOX_MODES` 六值）。
- `resolve_sandbox_mode` 原先把合约里的词原样返回并塞进 `SandboxSessionCreate`，pydantic 校验抛
  `ValidationError` → 未捕获 → `INTERNAL_ERROR`。测试里合约都是 ORM 直造（`allowed_sandbox_modes=None`
  或已是执行档），所以 51 条绿测对这条真实路径结构性失明——与解释器版本/DB 方言同一类盲区。

收口方式（三层，全部 fail-closed）：

1. `app/schemas/sandbox_session.py` 成为唯一词表源：`SANDBOX_MODE_ALIASES` + `canonical_sandbox_mode()`；
   `sandbox_sessions._mode_allowed_by_contract` 与 `workflow_service.resolve_sandbox_mode` 共用它，
   删掉 `workflow_service._MODE_ALIASES` 副本（副本里 `model_training` 根本不是合法执行档）。
2. `resolve_sandbox_mode` 只返回执行档值；合约没放行 preferred 时抛
   `CDSError(SANDBOX_MODE_NOT_PERMITTED)`，**不**静默换成另一个执行档（授权集合不是候选列表）。
3. `ContractCreate` 增校验：不认识的模式在创建时 422，而不是等到建会话时 500。

回归证据：`test_ss15_flow_unit.py::TestSandboxModeResolution`（6 条）+
`test_ss15_e2e_flow.py::TestContractModeVocabulary`（用 `allowed_sandbox_modes=["develop","query"]`
的合约打开 dev-session，断言 `sandbox_mode == "product_dev"` 且单元格可执行）。

### 4.10 页面角色必须跟着“能干活的那个人”

部署 QA 发现一个流程级断层：`sandbox_sessions` 规定“只有合约**买方**能开会话”（fail-closed，数据接收方
才能碰数据），但 `/notebooks`、`/workloads` 路由挂在 `ROLE_GROUPS.devSandboxUsers = [DATA_PROVIDER]` 下——
即**唯一被允许跑开发的身份（买方）看不到页面，能看到页面的身份（数商）一请求就被 403**。

收口（只改前端门禁，不动后端授权）：新增 `ROLE_GROUPS.flowDevUsers = [DATA_PROVIDER, BUYER]`，
`/notebooks`、`/workloads` 的路由与侧栏入口改用它；`/dev-sandbox`（数商自己产品的开发沙箱）仍只给
`devSandboxUsers`，避免把旧的数商专属面抖给买方。`_load_notebook` 仍为“仅所有者 + admin”，所以
Notebook 属于创建它的那个人，与“会话只能买方开”一致：买方自建自用。

回归锁：`test_ss15_e2e_flow.py::TestContractModeVocabulary` 增加一条断言——数商对自己不属于买方
的合约请求 dev-session 必须被 403/404 拒掉（写入后端不变量，不依赖前端）。

### 4.11 删订阅不能删掉推送历史，也不能拿空错误当“成功”

部署探针里 `DELETE /deliveries/webhooks/{id}` 返回 500：`webhook_deliveries` 外键仍引用该订阅，
PostgreSQL 抛 `ForeignKeyViolation` 未被接住。投递记录是交付历史，按留存策略不得清空，所以
不能“顺手删子表”：新增 0020 迁移把 `subscription_id` 改为可空（PG 直接 ALTER，SQLite 走
`batch_alter_table` 重建），并在 `delete_webhook` 里先显式置空子表外键。故意不在模型外键上写
`ondelete="SET NULL"`：SQLite 不强制 ON DELETE，写了会使 `alembic check` 在 SQLite 上报出待执行
差异（parity 门禁红），而应用层显式置空已使两方言结果一致。

同一个探针还暴露一个误导性文案：`POST /webhooks/{id}/test` 的 `detail` 取
`last_error or snippet or "已接收 2xx 响应"`，而当接收端不可达时 `str(exc)` 可能为空串
（某些 connect/timeout 异常就是没消息），`last_error` 落成空串→ falsy → 回落到“已接收 2xx 响应”，
而 `delivered=false`。违反项目铁律（不得把失败说成成功）。修：`last_error` 取
`str(exc) or 异常类名`；`detail` 只在 `status == delivered` 时才说 2xx，否则明说“未送达：接收端未返回响应”。

### 4.12 交付与计算配置的入口必须在合约创建页

用户目标的第二步是“通过合约选择数据目录与交付方式…设置是否自动计算、频率、沙箱资源配置”。若
这些字段只存在于合约详情页的 `FlowConfigCard`，创建时就无从表达，不配置也不报错（默默降到
“仅数据集交付 + 手动计算”）。因此 `ContractCreate` 内嵌一个默认展开的“交付与计算配置”面板。

实现取共享字段组件而非重复一份表单：`pages/Flow/FlowConfigFields.tsx`（只有默认导出，内部用
`Form.useFormInstance()` 读外层 form，所以 Collapse / Drawer / 创建页三个宿主都能直接用）+ 非组件
常量拆到 `pages/Flow/flowResource.ts`（`RESOURCE_FIELDS`/`numberRangeRule`/`formatResourceSpec`）。
拆开是为了 `react-refresh/only-export-components`：一个文件同时导出组件与常量会新增 lint 错误，
而本仓库 lint 基线不得增长。`FlowConfigCard` 因此从 348 行降到 193 行。

创建是**两次提交**：`POST /contracts` → `POST /contracts/{id}/flow-config`。第二步失败时合约已经
存在，所以 `onError` 不得说“创建失败”：区分成“合约已创建，但配置未保存，请到详情页重试”并仍然
跳到详情页（那里可以重配）。没展开面板 = 不提交配置（antd 的隐藏字段不注册、不校验），与后端
默认一致，不是静默降级。

### 4.13 “开发完”的那一跳不能留白：Notebook 页要能一键提升为负载

用户目标的第二步把“在线 Notebook 开发 → 测试沙箱 → 发布生产”写成一句连贯的话，但页面上它原先
是断的：`NotebookWorkbench.tsx` 里 `workload` 出现 0 次，只有 dev-session（测试沙箱）入口。用户开发
完必须自己换页到 `/workloads`、手填负载名、再从下拉里回忆刚才那个 Notebook——一个产品不该要求用
户自己记住实体 ID 并跳页对齐。

后端契约早已够用，所以**不加任何新端点**：`POST /workloads` 的 `entrypoint` 可空，留空即由
`wf.entrypoint_from_notebook(notebook.content)` 取全部代码单元格并算 `entrypoint_hash`；空笔记会被
`workloads.py` 的400（“工作负载缺少 entrypoint：请提供代码或由包含代码单元格的 Notebook 生成”）
挡下，前端用 `describeFlowError` 原样显示，不翻译成模糊的“创建失败”。前端只加一个「提升为负载」
按钮与一次确认弹窗。

弹窗必须说清两件事，不能只说“成功”：一是入口指纹是按**当前 `v{version}`** 的单元格内容算的（后续
再改笔记会导致测试通过→发布被后端判为哈希不一致），二是 `notebook.contract_id` 为空时直接写明
“未关联合约，发布前需在负载中心补齐”——不能把一个无法发布的负载伪装成可发布状态。确认后跳
`/workloads`，测试/发布仍在那页做（测试会真起沙箱、有成本，不该由开发页静默代作决定）。

类型上 `services/flow.ts` 的 `Notebook` 补 `contract_id: string | null`：后端 `NotebookOut` 一直返回该
字段，是前端接口漏声明，不是新增字段。验证：本地 `tsc -b && vite build` `BUILD_RC=0`；部署面复证见 §七。

### 4.14 编辑器不能依赖公网 CDN：Monaco 必须随包本地装载

部署实例真浏览器实测：`/notebooks` 打开笔记后 `performance` 资源清单里全是
`https://cdn.jsdelivr.net/npm/monaco-editor@0.55.1/min/vs/...`，等 45s `.monaco-editor` 仍不出现、
`window.MonacoEnvironment` 为 `null`、页面无任何报错——`@monaco-editor/react` 默认在**运行时**从 jsDelivr
拉 monaco。对密态沙箱的主交付形态（专网 / 内网 / 数据专区）这是必然结果：核心开发界面永久空白且静默。

修法不引入新依赖，也不改任何 `Editor` 用法：新增 `src/pages/Flow/monacoSetup.ts` 用
`loader.config({ monaco })` 走本地装载，worker 用 Vite 的 `?worker` 从包内取 `editor.worker.js`；
只注册 Python 语言以控住包体（流程面只用 python）。子路径必须写全
`monaco-editor/esm/vs/editor/editor.api.js`——该包导出映射是 `"./*": "./*"` 的字面通配，省掉 `.js`
会命中不存在的路径，`tsc` 报 TS2307。`monaco-editor` 此前只是 `@monaco-editor/react` 的 peer 传递依赖，
现按实际使用登记进 `package.json`（`^0.55.1`）与锁文件。装载点放在 `NotebookWorkbench.tsx` 而不是
`main.tsx`：monaco 只随该页 lazy chunk 加载，首屏不受影响。

### 4.15 React 19 下 antd 静态提示是静默空操作：212 处 `message.*` 全站失效

取证方式不是看提示（提示本来就不出现），而是看网络：部署实例上点「保存」，XHR 钩子抓到
`PUT /api/v1/notebooks/{id} → 200`、随后 react-query 自动 `GET` 详情与列表、页面「有未保存修改」徽标消失、
列表版本号升 v4——**保存真的成功了，但用户看不到任何反馈**（`.ant-message-notice-content` 节点数 0、
console 干净）。根因：antd 5.29 的静态 `message` / `notification` / `Modal.confirm` 经 React 19 已移除的
`ReactDOM.render` 挂载，未打兼容补丁时静默不渲染；全仓 **212 处** `message.*`（含 Login、ProductList、
ContractDetail 等既有页面）的用户反馈全是哑的——这是全站缺陷，不是流程页新引入的。

修法零新增依赖：`antd` 自身导出 `unstableSetRender`（`antd/es/config-provider/UnstableContext.d.ts`，
签名 `(node: ReactElement, container: Element | DocumentFragment) => () => Promise<void>`），新增
`src/antdReact19Patch.ts` 用 `react-dom/client` 的 `createRoot` 复现官方
`@ant-design/v5-patch-for-react-19` 的等价实现，`main.tsx` 首行装载。对照实验证明是全站修复而不只新页：
在完全未改动的 `/login` 上用错密码登录，`Invalid credentials` 提示正常出现。产品结论：任何“点击后只有
toast”的动作在这条修复前对用户等于按钮失灵，关键反馈仍应优先内联（Alert / 表格状态 / 版本徽标），
toast 只做锦上添花。

### 4.16 运行时不可用必须在动手前就说：`GET /api/v1/flow/runtime-status` + 常驻 banner

SS-13 把执行路径收口到单一 MicroVM 后，留下一个产品面上的坑：没有可用计算节点时，在线开发 / 测试沙箱 / 发布后计算**全都不可用**，但页面上一切按钮看起来都是能点的——用户直到点下去才碰到一个错（而错还是英文的 `RUNTIME_UNAVAILABLE`）。“环境不具备”不是用户的错，不该让他用点击去发现。

- 后端新增 `app/api/flow_runtime.py`：`GET /api/v1/flow/runtime-status` 返回 `{available, reason, message, ready_nodes, total_nodes, checked_at}`；`reason` 枚举化（`dataplane_disabled` / `no_ready_node` / `dataplane_error` …）并集中到 `REASON_MESSAGES` 中文文案表，`message` 自带下一步（“按 `deploy/cube-node/` 完成计算节点部署（前置：节点有 `/dev/kvm`）”）；挂在 `main.py` 的 `require_group("sessions")` 下，未登录 401 走 W2 错误体。失败时也不推断“应该可用”：探不到节点就是 `total_nodes=0`，报异常就是 `available=false`。
- 同时把 `sandbox_runtime.py` / `sandbox_sessions.py` 里抛给用户的英文文案改中文（同一页面不应该一半中文一半英文）。
- 前端：`services/flow.ts` 新增 `getRuntimeStatus` / `useFlowRuntimeStatus` / `RUNTIME_REASON_COPY` + `describeRuntimeReason()`（后端文案缺失时用前端同名 reason 文案兜底，绝不因此隐掉提示）；新增 `pages/Flow/RuntimeStatusBanner.tsx`（`available=true` 时渲染 `null`，不做“绿色已就绪”响扰），挂在 `NotebookWorkbench.tsx` 与 `WorkloadCenter.tsx` 顶部。
- 真机取证（部署实例，非测试容器）：未登录 401 `{"code":"HTTP_401",…}`；登录后 200 `{"available":false,"reason":"dataplane_disabled","message":"当前部署未启用 MicroVM 沙箱数据面（CDS_CUBE_DATAPLANE_ENABLED=false）…（前置：节点有 /dev/kvm）。","ready_nodes":0,"total_nodes":0}`；无头浏览器里 `/notebooks` 与 `/workloads` 各出一条 `ant-alert-warning`，`message=当前部署无法真实执行代码`、`description` 为上述全文（DOM 结构断言，非文案 grep）。

这一项不是把功能补上（没硬件就是没硬件，禁伪造），而是把“跑不通”从点击后的事故变成打开页面就看到的事实；远程权威定向套 **106 passed / `RT_STATUS_DONE rc=0`**（含新增 4 条：可用时 `available=true`、禁用数据面时 `reason=dataplane_disabled`、未注册节点计数一致、未登录 401）。



## 五、三段流程完成度矩阵

图例：✅ 已实现且有用例/远程证据；🟡 已实现但依赖硬件/部署环境才能端到端确认；⬜ 未做。

| 段 | 能力 | 后端 | 前端 | 证据 |
|---|---|---|---|---|
| ① 目录 | 上传数据→数据产品→审核发布→目录可见 | ✅ | ✅（目录/产品页为既有页，闭环用例覆盖调用序） | `test_ss15_e2e_flow.py::TestStageOneCatalog` |
| ② 合约 | 选数据目录 + 三种交付方式多选 + 自动计算开关 + 日/小时频率 + 沙箱资源配置一次写入 | ✅ | ✅ `ContractCreate`（创建时就填，默认展开）+ `ContractDetail`/`FlowConfigCard`（事后改） | `TestStageTwoContractFlow`（回读校验 9 字段）+ 部署站浏览器 QA（§7.3） |
| ② 沙箱 | 创建测试沙箱（dev-session 幂等、模式受合约约束） | ✅ | ✅ `NotebookWorkbench` | `TestFullLoop` + `test_ss15_flow_api.py` |
| ③ 开发 | 在线 Notebook：单元格执行、跨单元格状态、后端如实披露（cds_kernel / cds_stateless / jupyter） | ✅（VM 传输层 stand-in） | ✅ `NotebookWorkbench`（编辑器 + 输出 + 后端标识） | `TestFullLoop`（`base=40`→`print(base+7)`→47）+ `TestCellDriverTrust` |
| ③ 测试→生产 | 测试沙箱验证记录 + 发布门 412 + 发布为 production 版本 + 回滚 | ✅ | ✅ `WorkloadCenter` | `TestFullLoop`（未测发布必须 412） |
| ④ 数据集 | 输出文件打包 + 文本水印 + MANIFEST + 下载（无权限会话必须拒绝；下载按合约当事方鉴权，无归属记录即 404） | ✅ | ✅ `DeliveryCenter` | `test_dataset_channel_*` + `test_ss15_flow_api.py::TestDatasetBundleOwnership`（正向：当事方下载带水印 zip；负向：非当事方 403/404） |
| ④ OpenAPI | 端点声明 → 自动 OpenAPI 3.1 文档 → `/open/v1/{slug}` 调用（API Key + 合约授权 + 限流 + 配额 + 哈希回指） | ✅ | ✅ `DeliveryCenter` | `TestFullLoop`（文档先空后含 slug；6 次内触发 429 `RATE_LIMITED`） |
| ④ 结果推送 | 订阅 + 密钥仅返回一次 + 签名可验 + 退避/重投 + 连续失败熔断 | ✅ | ✅ `DeliveryCenter` | `test_result_push_channel_queues_signed_delivery` + 部署栈真实外呼（§5.1：`delivered=true`、接收端 HMAC 校验通过、失败投递 `attempt 2/6` 且退避时刻递增） |
| 自动计算 | 日/小时频率计算 + 到期领取（幂等键）+ 生产沙箱执行 + 事件派发 | ✅（后台循环需部署环境长跑观察） | ✅ `ComputeSchedule` | `test_ss15_flow_unit.py`（频率/幂等）+ `test_ss15_flow_api.py` |
| 平台化 | 生产配置告警、作用域（scopes）、错误契约 W2 全量覆盖、迁移与 schema parity | ✅ | ✅ | §六 |

未做（⬜，明确不在本轮）：真实 JupyterLab 界面直连（当前只如实探测并给出代理 URL，`JUPYTER_PROXY_BASE_URL` 未配置即为 null）；数据集交付包列表页（见 §4.8）；多节点卷插件寻址（单节点拓扑限制，SS-14 记录）。

### 5.1 部署环境实测（远程 `100.112.5.253`，非测试容器）

把改动后的后端（挂源码树的新 `cds-api` 容器）与前端 dist + 新 nginx.conf 部署到远程，用外部客户端跑真实 HTTP：

| 项 | 结果 |
|---|---|
| 0019 落到部署库 | `alembic current = 0019_flow_orchestration (head)`；7 张 SS-15 表 + 6 个合约新列到位 |
| `/open/` 反代 | `:3000/open/v1/<slug>` → API（GET 405 / 缺 Key POST 401），此前会落到 SPA 回落 |
| 页面渲染（data_provider 账号，真实浏览器） | `/notebooks`、`/workloads`、`/compute`、`/deliveries`、`/contracts` 均渲染出表/卡片/按钮，无错误文案；侧栏出现“数据流通”分组；notebook 列表可见真实条目 |
| 真实闭环（上传→产品→审核→发布→合约→双方 SM2 签署→active→flow-config） | 全链路 PASS（签名走 `/auth/generate-sm2-keys` + `/auth/sign-data` + `/contracts/{id}/sign` 真实路径，无数据库捷径） |
| dev-session（修复后复跑） | 不再 500：`400 RUNTIME_UNAVAILABLE` + `detail.reason=dataplane_disabled`，属于该机无 Cube 节点的诚实披露面 |
| 真实单元格执行 | 部署环境 `CDS_CUBE_DATAPLANE_ENABLED=false` 且无 Cube 节点（已核实：无 cubelet 二进制/服务/`/data/cubelet` 挂载）→ 该环境不能真实执行单元格（诚实披露，需节点上线后复验） |
| 发布→API 端点→外部调用全链 | 因上一行，部署侧测试跑不绿→发布停在 412，所以真实数据返回只在测试栈（l2c 本机子进程 stand-in，见 §4.7）验证过；部署侧已验的是拒绝面（`/open/v1/<slug>` GET 405 / 无 Key POST 401）与 OpenAPI 文档自动生成 |
| 部署面缺陷 D-QA-1 | 部署库由 SS-14 引导脚本以 create_all 建表 + `stamp 0016` 起步，导致 `alembic upgrade head` 重放 0017 时撞已存在表（`relation already exists`）。QA 以 `alembic stamp 0018_owner_fks` 对账后升级；runbook 应改为“空库 `alembic upgrade head` 建库”，禁 create_all+stamp 引导。 |
| 深度探针（22 步，买方身份真实 HTTP） | notebook 创建/追写、workload 创建、测试运行记录、**发布 412 `WORKLOAD_TEST_REQUIRED`**、OpenAPI 文档自动生成、**建 API 端点 412 `WORKLOAD_NOT_PUBLISHED`**、调度表读取（auto/每日/:30/三交付方式）、手动触发产生运行记录、webhook 创建/轮转/测试/投递记录（attempt 1/6 + `next_retry_at`）全部按契约返回 |
| 页面渲染（buyer 账号，修复角色后） | 四页均可达且渲染（含真实排程行：自动计算已开启 / 每日 / 下次 2026-10-16 02:30 / 三种交付方式）；`denied=false`、`err=false` |
| 部署面缺陷 D-QA-2 | `DELETE /deliveries/webhooks/{id}` 在有投递记录时 500（外键阻挡）——修见 §4.11（测试补上“建→测→删”链，旧用例从不制造投递记录，以故全组绿而部署红） |
| 部署面缺陷 D-QA-3 | 投递失败时 `last_error` 可能为空串，使 `detail` 回落到“已接收 2xx 响应”（误导成功）——修见 §4.11 |
| 结果推送真实投递（部署栈 → 真实接收端） | 在 `cds-net` 内拉起一个接收端容器 `cds-sink`（复用 `cds-api` 镜像跑一个记录请求的 http.server，不伪造任何硬件）：`POST /webhooks/{id}/test` 返回 `delivered=true response_status=200`，接收端收到 241 字节事件体并用轮转后的密钥重算 HMAC-SHA256 → **`sig_ok=true`**（`X-CDS-Signature-Sha256` 64 位 + `X-CDS-Timestamp` + `X-CDS-Event-Id` 对齐）；`redeliver` 产生第二次真实外呼 |
| 退避重投真实观察 | 指向不可达端点（`cds-sink:18098`，DNS 可解析、端口拒接）的订阅：首投 `status=failed`，等待后同一条投递记录变 `attempt=2/6` 且 `next_retry_at` 往后推（后台退避 worker 真在跑）；失败原因为人话 `All connection attempts failed`，未谎报成功 |
| 合约创建页写入交付与计算配置 | 浏览器提交后直接跳到新合约详情页，详情卡片与 API 同时确认落库：`delivery_methods=[dataset,result_push] auto_compute=true daily 03:15 Asia/Shanghai`，`next_compute_at=2026-10-15T19:15:00Z`（= 北京时间 10-16 03:15，时区换算正确）；截图 `/tmp/qa_fe_create_{1_form,2_filled,3_detail}.png` |

## 六、验证记录（远程权威环境，用户规则：测试不在本地跑）

远程 `100.112.5.253`，`cds-api-test2`（Python 3.12 生产同版解释器）容器内执行，源码经 tar 同步：

| 项 | 命令 | 结果 |
|---|---|---|
| SS-15 单测 + 接口测 + 闭环 | `pytest tests/test_ss15_{flow_unit,flow_api,e2e_flow}.py` | **51 passed**（远程 py3.12，UTC 2026-10-15） |
| SS-15 定向 + schema parity | `bash /root/cds-test/ss15_verify.sh` | STEP_OK parity；`pytest_ss15 45 passed`（新增 3 条信任门/常量信任用例后为 48+） |
| 前端四步（tsc / vitest / lint） | `bash /root/cds-test/fe_verify2.sh` | `FE_STEP_OK npmci/tsc/vitest/lint`，`FE_ALL_DONE` |
| SS-15 三文件（含词表/角色/推送历史/审计留痕回归锁） | `pytest tests/test_ss15_{flow_unit,flow_api,e2e_flow}.py`（`/root/cds-test/src2`） | **61 passed**（远程 py3.12，UTC 2026-10-15；本轮新增 2 条审计留痕锁） |
| 权威全量回归 | `bash /root/cds-test/ss15_reg4.sh` → `pytest -q tests/ --ignore=tests/test_e2e_full_lifecycle.py --ignore=tests/test_e2e_full_test.py` | 进行中，结果回填于 §八。排除 `test_e2e_full_test.py` 的理由：`tests/AGENTS.md` 已标为 manual-only（写死 `172.21.0.2:30080` 活集群地址，该地址已不存在 → 每请求 10s 超时，且它的 pytest 面永不失败）；上一轮盲跑 85 分钟不结案即卡在此文件。 |
| 前端三步（创建页配置面板 + 字段组件拆分后） | `npx tsc -b` / `npx vitest run` / 远程全量 `npx eslint src` | tsc RC=0；vitest **109 passed / 7 files**；eslint 回到既存基线 **13 errors**（Flow 目录 0 错；本轮一度因共享常量与组件同文件新添 4 错，已按 `react-refresh/only-export-components` 要拆分至 `flowResource.ts` 后消除）。注意：`fe_verify2.sh` 的 lint 步仍是假 OK（管道在容器内层 shell，`pipefail` 不继承），因此 lint 以全量 `npx eslint src` 的 `ESLINT_RC` 为准。 |
| PG + SQLite `alembic check` + parity（0020 落地后复证） | `bash /root/cds-test/ss15_pg3.sh` | 全绿：`STEP_OK alembic_check_pg`、`STEP_OK alembic_check_sqlite`（空库一路 `0017→0018→0019→0020`）、`SCHEMA PARITY OK: 53 tables, columns identical`、`ALL_DONE`；两侧 `alembic check` 均 `No new upgrade operations detected.` |
| 部署库升级（0020） | `bash /root/cds-test/ss15_deploy_sync.sh`（`alembic upgrade head` + `docker restart cds-api`） | `Running upgrade 0019_flow_orchestration -> 0020_webhook_detached_history`、`alembic current` = `0020_webhook_detached_history (head)`、`STEP_OK restart`；根 `/health` = 200（健康端点在根，不在 `/api/v1`）。 |
| 部署面 webhook 复跑（D-QA-2 / D-QA-3） | `qa_loop5` 探针经 stdin 注入 `cds-api` 容器 | `wh_delete=204`、删除后列表不含该订阅、`DELETE_GONE=True`；失败投递 `delivered=false detail="All connection attempts failed"`（`lie=False`），不再把空错误当成功。 |
| 自动计算整点真实触发 | 合约 flow-config 改 `hourly` + `compute_minute=北京分+2`，等 60s 调度 tick | `GET /compute/runs` 出现 `trigger=schedule scheduled_for=2026-10-15T13:05:00+00:00`（`created=13:05:40`，落在 60s 间隔内），`next_compute_at` 自动滚到 `14:05`，`last_computed_at` 同步更新；`error_message`=「该合约没有处于 published 状态的生产工作负载，请先在测试沙箱验证后发布」——调度器真实在跑，失败属诚实前置条件（本节点无 Cube 数据面，见 §5.1）；观察完毕已还原 `daily 02:30 Asia/Shanghai`（`next=18:30Z`）。 |
| Notebook 留痕（部署面复证） | `qa_audit_probe` 探针注入 `cds-api` 容器 | 201 建 + 204 归档后，`GET /audit/records?resource_type=notebook` 返回该 notebook 的 `notebook.created` 与 `notebook.archived` 两条，detail 带 `_sm2_signature`（审计链签名）、`content_hash`、`version`、`path`、`title`；用户代码不落审计（单元格执行只记 `code_sha256` + 字节数）。 |
| 错误码契约登记 | `docs/error-codes.md` | 流程面 **20 个新码**登记进 W2 清单（`WORKLOAD_NOT_PUBLISHED` 412、`DELIVERY_METHOD_NOT_ALLOWED` 403（`detail.configured` 回显）、`NO_OUTPUT_FILES` 409、`CONTRACT_MISMATCH` 403、`BUNDLE_TOO_LARGE` 413、`COMPUTE_RUN_*` 409×3、`OUTPUT_BLOCKED`/`WORKLOAD_FAILED`/`WORKLOAD_UNAVAILABLE`/`ENDPOINT_DISABLED`/`CALLER_UNKNOWN` 502、`NOTEBOOK_UNAVAILABLE`/`SANDBOX_SESSION_MISSING` 503、`SANDBOX_MODE_NOT_PERMITTED`/`SANDBOX_MODE_UNSUPPORTED`/`INVALID_REQUEST_BODY`/`WEBHOOK_EVENT_UNSUPPORTED` 400、`ENDPOINT_SLUG_TAKEN` 409），此前清单只有 12 个通用码，SDK / 外部 agent 无表可对；顺带修掉 `SANDBOX_MODE_NOT_PERMITTED` 提示病句（“参加”→“加上”，无测试钉该文案）。 |
| 成果交付诚实面（部署环境，无 Cube 节点） | `qa_loop7` 探针 | `POST /deliveries/dataset/bundle` → 403 `CONTRACT_MISMATCH`（会话跨合约时拒绝，不串数据）；`GET /deliveries/openapi/contract/{id}` → 200 真实 OpenAPI 3.1 文档且 `paths: {}` + 文案“未发布的工作负载不会出现在此文档中”；`POST /deliveries/openapi/endpoints` → 403 `DELIVERY_METHOD_NOT_ALLOWED` 并回显 `configured=[dataset, result_push]`（未选 open_api 就拒绝建端点）。 |

| 交付包下载鉴权（0021）+ 正向下载闭环 | `pytest tests/test_ss15_{flow_unit,flow_api,e2e_flow}.py`（`/root/cds-test/src2`，哨兵 `TARGETED_DONE rc=0`） | **63 passed in 59.89s**（远程 py3.12）；新增 2 条锁：当事方下载拿到带水印 zip（`files/result.txt` 已注入、`files/weights.bin` 原文透传、`MANIFEST.json` 计数与之一致），非当事方下载被拒（403/404） |
| PG + SQLite `alembic check` + parity（0021 落地后复跑） | `bash /root/cds-test/ss15_pg3.sh`（哨兵 `PG3_EXIT=0`） | 全绿：`STEP_OK alembic_pg`（空库一路 `0017→…→0020→0021_delivery_bundle_ownership`）、`STEP_OK alembic_check_pg`、`STEP_OK alembic_check_sqlite`、`SCHEMA PARITY OK: **54 tables**, columns identical`（上一轮 53，+`delivery_bundles`）、`ALL_DONE`；两侧 `alembic check` 均 `No new upgrade operations detected.` |
| 部署面外部 OpenAPI 入口鉴权（`/open/v1`） | 经前端 nginx `POST /open/v1/{slug}`，不带凭证与带伪造 `X-API-Key` 各一次 | 两次均 401，响应体为完整 W2 契约（`{"code":"HTTP_401","message":"Invalid token","request_id":"4fc9685081224a03","detail":…}`）；证明 nginx 的 `/open/` 反代没被 SPA 回落吞掉、外部 agent 面在部署环境可达且 fail-closed（真实执行需已发布负载，本节点无 Cube 数据面，见 §5.1）。 |
| 部署前端与源码树构建一致性对账 | node:20 镜像在部署树 `src/cds-frontend` 内 `npm run build` → 先清空容器 `assets/` 与 `index.html` → `docker cp dist/.` 热替换 | `BUILD_RC=0`、`STEP_OK cp`、`TREE_MD5 == CONTAINER_MD5 == 2ef29925e2065fa27b44e0bb33ecae9a`、`ASSETS_TREE = ASSETS_CONTAINER = 107`（此前容器内飘着 162 个 js，其中约 55 个是历次热替换遗留的旧 chunk，已清净）、`FE_HTTP=200` / `FE_DELIVERIES_HTTP=200`。附带澄清：此前构建走的是另一棵树 `fe-root/cds-frontend`，但重建后 `index.html` 哈希一字不差 → 旧部署并非旧代码；本地 `cds-frontend/dist`（`453bf28d…`）才是早期构建残留，对账以远程部署树为准。 |
| 最终权威全量回归（0021 前的整树快照 reg5） | 远程 py3.12 镜像内 `pytest tests/` 全量（排除两个需真实集群的 e2e），31:02 | **22 failed / 2927 passed / 5 skipped**（`REG_DONE rc=1`）。对照 `ci/known-failures.md` 权威基线 **2864 / 24 / 5**：失败数**未增长（24 → 22）**，通过数 +63 正是 SS-15 系列新增用例。22 条全属环境族：`test_cds_volume_plugin.py` ×13（镜像无 make/gcc，构不出 volume 插件）、`test_cube_upstream_check.py` ×3（缺上游 Cube 仓与 pin 文件）、`test_pz01_sft_real.py` ×4（镜像内基础模型缺 `q_proj/v_proj` 目标模块）、`test_e2e_flows.py::test_e2e_monitoring_stats_api` 与 `test_async_operations.py::test_failed_operation_records_error`（临时容器 Redis 未配密码：`Redis connection failed: Authentication required`）。上一轮 9 条 `FileNotFoundError`（compose / helm / 前端 `enums.ts`）在整树同步后全部转绿——`test_deploy_manifests.py` 与 `test_capability_matrix_consistency.py` 已不在失败清单。SS-15 流程面套件零失败。 |
| 部署库升级 0021 + 部署面鉴权探针 | `bash /root/cds-test/ss15_deploy21.sh`（`D21_EXIT=0`） | `Running upgrade 0020_webhook_detached_history -> 0021_delivery_bundle_ownership`、`alembic current` = `0021_delivery_bundle_ownership (head)`、`STEP_OK restart`、`STEP_OK health after 3x3s` / `HEALTH_CODE=200`；带合法买方 token 请求不存在的 bundle_id → **404** `{"code":"HTTP_404","message":"交付包不存在或无归属记录，请重新生成交付包","request_id":"195d82915dcc4036"}` → 新鉴权分支在部署容器上已真实生效（不是只跑在测试镜像里）。 |
| 部署实例路由契约清单（运行态 OpenAPI） | `docker exec -i cds-api python - < route_probe5.py`（枚举 `app.openapi()['paths']`） | 部署契约共 **255 paths / 293 ops**，三段流程面共 **46 ops**：`/api/v1/notebooks` 9（含 `dev-session` / `cells/execute` / `jupyter` / `run`）、`/api/v1/workloads` 9（`test` / `test-runs` / `publish` / `rollback` / `retire`）、`/api/v1/deliveries` 15（数据集打包与下载 / OpenAPI 文档与端点 / webhook 增删改查 + 投递历史 + 重投 + 轮转 + 测试）、`/api/v1/compute` 5（含 `schedule`）、`/api/v1/contracts/{id}/flow-config` 1、`/open/v1/{slug}` 1（另有 SS-12 `api-keys` 6）；挂载对账 `docker inspect cds-api` = `/root/cds-test/src -> /app`（部署代码就是同步过 md5 的那棵树）。枚举注意：本 FastAPI 把 `include_router` 记为 `_IncludedRouter` 包装对象，直接读 `app.routes` 的 `.path` 会得 0 命中，必须走 `app.openapi()`。 |
| S6 内容载体修复（部署面字节级取证） | 临时 vitest 用例从 `serializeNotebookContent()` 导出浏览器实际发出的 374 字节 PUT 体 → 打部署实例 `PUT /api/v1/notebooks/{id}` | **`PUT 200`**（修复前同一跳 422），回读 `version=2` / `content` 为对象 / `cells=2`；远程定向套 `pytest tests/test_ss15_{flow_unit,flow_api,e2e_flow}.py` = **66 passed in 62.97s**（`TARGETED_DONE rc=0`，原 63 + 3 条载体用例）；本地 `compileall` OK、`npx eslint` 三个改动前端文件真实退出码 0 / 诊断 0 行。 |
| 最终权威全量回归（reg7，含 0021 + S6 的最终部署树） | `bash /root/cds-test/ss15_reg7.sh` → 远程 py3.12 镜像 `pytest -q tests/`（排除两个需真实集群的 e2e），1871.75s | **22 failed / 2932 passed / 5 skipped**（`REG7_DONE rc=1`）。与上一轮（22 failed / 2927 passed / 5 skipped）的失败集合做节点 ID 逐条 diff：**`FAILED_SET_IDENTICAL_REG4_VS_REG7`（一字不差）** → 失败不升，对照 `ci/known-failures.md` 基线 24 条反而少 2 条，基线未增长；SS-15 流程面套件零失败。22 条环境族构成：`test_cds_volume_plugin.py` ×13（镜像无 make/gcc，构不出 volume 插件）、`test_cube_upstream_check.py` ×3（远程无 pin 住 go-sdk-v0.7.3 的上游副本，另本主机无 `/dev/kvm`，见 §八第六轮）、`test_pz01_sft_real.py` ×4（镜像基础模型缺 `q_proj/v_proj`）、Redis 未配密码 ×2。通过数 +5 的来源：3 条本轮新增内容载体用例（SS-15 定向套 63 → 66 实测），另 2 条是上一轮跑 `src` 时该树落后于验证树 `src2` 的既有用例（本轮 5 个文件同时同步、三向 md5 一致后自然补齐）。 |
| S7 编辑器本地化（Monaco 不再走公网 CDN） | 真浏览器 `performance.getEntriesByType('resource')` 取证 → 新增 `src/pages/Flow/monacoSetup.ts` 本地装载 + `monaco-editor` 登记进 `package.json` → 部署树重建换版 | 改前：资源清单全是 `cdn.jsdelivr.net/npm/monaco-editor@0.55.1/min/vs/...`、45s 内 `.monaco-editor` 不出现、`MonacoEnvironment` 为 null；改后：`.monaco-editor` 出现、**CDN 命中 0**、本地 chunk `editor.api2-BmGoRSl4.js` + `editor.worker-Cn2oRESe.js`；`npm run build` RC=0（dist 6.9M、assets 107→117）、`npx eslint` 四个改动文件真实 RC=0 |
| S8 React 19 静态提示补丁（212 处 `message.*`） | 新增 `src/antdReact19Patch.ts`（antd 自带 `unstableSetRender` + `createRoot`）+ `main.tsx` 首行装载 → 部署树重建换版 → 真浏览器复跑 | 改前：`PUT …/notebooks/{id} 200` + 徽标消失 + 版本升 v4，但 toast 节点 **0**；改后：保存弹「已保存，当前版本 v5」、未改动过的 `/login` 错密码弹「Invalid credentials」（坐实全站 212 处复原）；远程权威前端单测（`node:20-bookworm-slim` 镜像内 `npx vitest run`，跑验证树 `src2`）= **112 passed / 7 files / VITEST_RC=0** |
| 前端两轮换版对账（S7 / S8） | `bash /root/cds-test/ss15_fe_sweep2.sh`（部署树 `npm run build` → 清旧 chunk → `docker cp` 热替换 → 内外 md5/条目对账） | S7 换版：`TREE_MD5 == CONTAINER_MD5 = af0d57c9ad86727513afc2d1a206682f`、`ASSETS 117 == 117`、`FE_HTTP=FE_DELIVERIES_HTTP=200`；S8 换版：`a24c3b07c2af8ed0a94aedf667fd8f21`、117 == 117、两页 200。两次修复均**未新增 npm 依赖**（补丁用 antd 自带导出实现），故远程 `fe-cache/node_modules` 无需重装，构建 `BUILD_RC=0` |
| 部署面前端路由兜底（404） | 新增 `src/pages/NotFound.tsx` + `App.tsx` 末条 `<Route path="*">` → 真浏览器访问未匹配路径 | 改前：未匹配路径只剩侧栏、内容区空（实测 `has_menu=true / text_len=159`、无任何提示文案）；改后：页面出现「页面不存在」与「返回首页」入口（DOM 断言 `页面不存在` 命中） |
| SDK 流程面覆盖（40 op）| 从**部署实例** `app.openapi()` 逐 op 抽 path/query/body 形状 → `sdk/cds_sdk/client.py` 新增 40 个方法 + `_compact()`（PATCH/PUT 只带显式传入的字段）→ 新增 `tests/test_ss15_sdk.py` 9 条契约用例（`httpx.MockTransport`，无服务器）→ 远程权威定向套 `bash /root/cds-test/ss15_targeted_sdk.sh`（`cds-api-test2:latest` 镜像 + 验证树 `src2`）| **75 passed in 64.36s / `SDK_TARGETED_DONE rc=0`**（= SDK 9 + 流程三套 66）；用例含「覆盖数恰为 40 且逐个可调用」防漂移断言、`_compact` 负向断言（未传字段不出现在 body）、412 发布门错误类型化（`code`/`request_id`）、他人成果包下载 404 归属拒绝、204 端点返 `None`。写用例过程中被真实行为纠正两处：httpx 空 query 的 `str(url.query)` 是 `"b''"`、JSON 上线是紧凑无空格（改用解析后的语义断言） |

| 运行时不可用前置展示（后端端点 + 两页 banner + 中文文案）| 同步 6 个后端/前端文件到验证树 `src2`（4 个关键文件 md5 与本地一致）→ `bash /root/cds-test/ss15_rt_status.sh`（`cds-api-test2:latest`，`/src2`）跑 6 文件定向套 | **106 passed in 83.66s / `RT_STATUS_DONE rc=0`**（0 failed；= SDK 9 + 流程三套 66 + Cube 工具链 13 + TEE 探测 8 中含本轮新增 4 条 runtime-status 用例）；另本地窄门：`tsc -b` 0 错、`eslint` 0 错、`vitest` 113 passed、`compileall` RC=0、`import app.main` OK 且路由已注册 |
| 前端权威单测复跑（用户规则：不在本地跑测试）| `node:20-bookworm-slim` 镜像内 `npx vitest run`（验证树 `src2/cds-frontend` + `fe-cache/node_modules`）| **`Test Files 7 passed (7)` / `Tests 113 passed (113)` / `FE_VITEST_DONE rc=0`**（8.41s）。上一轮启动失败是脚本自身的 `--reporter=basic`：该 reporter 在当前 vitest 版已被移除，`Failed to load custom Reporter from basic` → rc=1（哨兵诚实报 rc，不是假 OK） |
| Cube 节点门禁（发布钉 AND + 新增沙箱网段检查）| 同步 `deploy/cube-node/{preflight.sh,install.sh}` + 两个测试文件（三文件本地/远程 md5 一字不差）→ `bash /root/cds-test/ss15_cidr.sh` | **29 passed in 20.18s / `CIDR_GATE_DONE rc=0`**；首轮为 **2 failed / 27 passed**，真因是新门禁在 `test_ss13_node_tee_probe.py` 自带的 `build_mock_root` 副本里没有 `ip` 垫片，拿到测试机真实网段（实有 `192.168.17.0/24`、`192.168.18.0/24`）后按上游默认 `192.168.0.0/18` 判为冲突——门禁本身正确（真实环境里确实会碎），补 fixture 后转绿。详见 `specs/ss-10-cube-dataplane-connector.md` §八 2026-10-16 |
| 部署面热替换（让线上站点跑上本轮代码）| 后端 4 文件 `docker cp` 入 `cds-api` → 容器内 `import app.main` 通过 → `docker restart`；前端在远程验证树 `npm run build`（`FE_BUILD_DONE rc=0`）→ 备份 `/usr/share/nginx/html` 后 `docker cp dist/.` | `IMPORT_OK`、`cds-api Up (healthy)`、`HEALTH=200`、`FE_HTTP=200`；`GET /api/v1/flow/runtime-status` 在部署面返回上述真实 JSON，`POST /api/v1/notebooks/{id}/dev-session` 的 400 文案已是中文（阐明文案改动在线上生效，不只是在仓里）。注：这是**文件级热替换**（镜像未重建），属 QA 取证性质，下一次镜像级部署仍走 `Dockerfile.api` 重建 |

说明：`ci/known-failures.md` 基线不得增长；全量回归完成后据实回填，若出现新增失败先定位再收口，禁止用调高超时等方式掩盖。

## 七、真实页面 QA

已在部署环境用真实浏览器（headless Chrome，外部地址 `:3000`，`qa_ss15_dev` / data_provider 账号）点通：

- `/notebooks`：侧栏“数据流通”分组含四项；页面出 Notebook 列表（真实条目 `QA 冒烟 /qa/smoke.ipynb v1`，后端列“未记录”符合未执行事实）、合约选择器、新建按钮、降级提示常驻文案。
- `/workloads`：空态文案“还没有工作负载，先从 Notebook 创建一个” + 状态图例（草稿/测试中/测试通过/测试失败/已发布/已退役）+ 门禁文案（412 与错误码按人话披露）。
- `/compute`：调度表 + 运行记录双表，含“每 10 秒自动刷新”“下次计算时间以后端 `next_compute_at` 为准”。
- `/deliveries`：三通道 Tab（数据集打包 / API 交付 / 结果推送）与来源会话选择器（“仅列出当前账号可见的会话”）。
- `/contracts`（data_provider 视角）：空列表（合约属于买卖方），无越权渲染。
- 买方视角（`qa_ss15_buyer`，§4.10 修复后复跑）：侧栏“数据流通”四项全部可见，`/notebooks` 空态文案、`/workloads` 新建负载 + 状态图例、`/compute` 真实排程行与“手动触发”、`/deliveries` 三通道 Tab 均正常；截图已落盘 `/tmp/qa_fe_buyer_*.png`。
- 合约创建页（`qa_ss15_dev`，§4.12 落地后）：`/contracts/create` 上“交付与计算配置”面板默认展开（`panelOpen=true`），三种交付方式带人话提示可勾选，自动计算开关打开后频率单选从 `disabled` 转为可用（`radiosDisabled=[false]`），沙箱资源数字框就位；填标题/产品/买方 + 每日 03:15 提交后直接落到 `/contracts/{id}`，详情卡片显示“已开启 / 每日 / 3:15 / 下次计算时间”，API 回读同值。另单独验证了复选框逐个点击（隔 700ms）能分别正确置位，open_api 选项可选。

- 2026-10-15 换版后复证（前端从部署树重建 + 清旧 chunk 热替换之后，用无头 Chrome 真实表单登录 `qa_ss15_buyer` 逐页跑）：`/notebooks` 1 表 3 行 + 「新建」；`/workloads` 1 行 + 「新建负载 / 查看测试记录 / 发起测试 / 发布 / 回滚 / 退役」六钮；`/compute` 双表 5 行（「手动触发」×2、「取消」×2 —— 调度产生的运行记录在页面上可操作）；`/deliveries` 三 Tab（数据集打包 / API 交付 / 结果推送）+「生成成果包」；四页 `errText`（加载失败/系统错误/请求失败/Traceback 等关键词）均为空。截图 `/tmp/qa_fe_after_sweep_deliveries.png`。

- 2026-10-15 换版前基线（改前快照，供换版后对账）：容器 `assets=107`、`index.html` md5 `2ef29925e2065fa27b44e0bb33ecae9a`、`/notebooks` 200、`POST /open/v1/qa-nonexistent` 401（网关可达且 fail-closed）。用 `grep -rl <中文标签>` 检索新增按钮文案「提升为负载」——先用已知标签「启动测试沙箱」命中 1 个文件验证检索法本身有效——结果源码树 `dist/` 与部署容器均为 **0 命中**（而 `src/pages/Flow/NotebookWorkbench.tsx` 已有该按钮），坐实部署前端确实早于本轮改动，换版是必需步骤而不是假想动作。

- 2026-10-15 第七轮（真浏览器逐断言，`qa_ss15_buyer` 真实表单登录，headless Chrome 1440×1000）：`/notebooks` 列表 5 行 → 点标题 `<a>` 进入工作台（**该页没有 `/notebooks/{id}` 深链路由**：列表与编辑器同页、靠 `selectedId` 选中，`App.tsx` 只有 `path="notebooks"`，深链过去内容区为空——属设计而非缺陷，空白问题由本轮新增的 404 兜底页消除）→ `.monaco-editor` 在场且 CDN 命中 0 → 载入后**无**「有未保存修改」徽标（S6 canonical 对比修复的正向证据）→ 点「添加单元格」后徽标出现、「保存」从 `disabled` 解除 → 点「保存」出现 `.ant-message` 提示「已保存，当前版本 v5」且徽标消失，同一跳 XHR 记录 `PUT /api/v1/notebooks/f683b5f8… 200` → 未匹配路径显示「页面不存在」。截图 `/tmp/qa_fe_s7_workbench.png`、`/tmp/qa_fe_s8_toasts.png`、`/tmp/qa_fe_s8_login_error.png`、`/tmp/qa_fe_monaco_state.png`。

- 2026-10-15 第八轮（流程页布局与可访问性机器巡检，取代目视校对）：`/notebooks`、`/workloads`、`/deliveries`、`/compute`、`/contracts` 五页逐页遍历可见元素。结果：**文本裁切 0**（`scrollWidth > clientWidth` 命中 0）、**页面级横向滚动 0**（`documentElement.scrollWidth - innerWidth = -15`，即只占滞滚动条）、无可读名称的 `button/a` 与 11px 字号均为预先存在且老页面同现（antd 展开/分页按钮、MainLayout 品牌副标题），不归为本轮缺陷。曾一度误报 `/compute` 运行表越界 15 个元素：向上追溯发现它在 `.ant-table-content{overflow-x:auto}`（`clientWidth 1107 < scrollWidth 1200`）里，属可滚动容器而非裁切——**判“越界”必须先找可滚动祖先**，否则会把正常的宽表横向滚动误写成缺陷。

未取得的部分（诚实记帐）：本轮模型通道不收图片（第八轮再次实测：读 PNG 回 `model does not support images`），截图未能逐张目视校对；视觉校对改用 DOM 结构计数（表/卡片/按钮）+ 布局与可访问性巡检（裁切/越界/字号/按钮命名）+ vitest 组件用例；浏览器侧改用 XHR 钩子 + `performance` 资源清单取证（本轮正是据此坐实 S7 的公网 CDN 依赖与 S8 的“请求成功但提示丢失”），console 文本仍未捕获（需 CDP 订阅，属后续 QA 工具项）；发布门的 412 弹窗、单元格输出与成果包下载入口的**页面级**交互，因部署环境无 Cube 节点（见 §5.1）不能真实跑通，已用 API 级闭环代替并在 §4.9 修复后重跑。

- 2026-10-16（运行时前置展示换版后复证）：后端 4 文件热替换 + 前端 dist 换版后，用无头浏览器（Bun.WebView）从外部 `:3000` 真实表单登录 `qa_ss15_dev`：登录出现 `.ant-message` 「登录成功」（同时反证 S8 补丁在线上生效）→ `/notebooks` 与 `/workloads` 各捕到一条 `ant-alert` 警告，`msg=当前部署无法真实执行代码`、`desc` 以「当前部署未启用 MicroVM 沙箱数据面（CDS_CUBE_DATAPLANE_ENABLED=false）」开头（DOM 节点属性直读，非页面全文 grep）；截图落盘 `/tmp/qa_notebooks_banner.png`、`/tmp/qa_workloads_banner.png`（本轮模型通道仍不收图片，所以取证以 DOM 结构断言为准，图片仅供用户自看）。另：页面首次渲染后立刻查 `.ant-alert` 会得到空集（banner 要等 `runtime-status` 往返），轮询取证得给够等待窗口，否则会误报“没渲染”。

## 八、执行记录（滚动回填）

- 2026-10-15：服务层三件套（workflow/compute/delivery）+ 5 路由 + `/open/v1` 外部面 + 0019 迁移 + 前端 4 页 + FlowConfigCard 落地；dev-session 阻塞链三条真因定位并修复；trusted_runner 字节级信任边界落地（含平台常量表）；OpenAPI 包装脚本改为白名单内模块；Notebook 文档持久化改纯函数并定义追写语义；`resolve_sandbox_mode` 接线四处；生产边界 fixture 使闭环用例在真实请求边界下通过；SS-15 三文件 51 passed（远程）。
- 2026-10-15（部署 QA）：把新后端/新前端 dist/新 nginx.conf 部署到远程；0019 落到部署库（先 `stamp 0018_owner_fks` 对账 create_all 引导库，见 §5.1 D-QA-1）；`/open/` 反代在 compose/k3s 共用的 `cds-frontend/nginx.conf` 补齐（helm ingress 的 `/` 已覆盖）；真实浏览器点通四页（§七）；外部客户端真 SM2 签署跑通三段闭环，抓出 dev-session 500（§4.9）并在同一轮修复 + 补 7 条回归用例。
- 2026-10-15（部署 QA 第二轮）：部署树同步修复 + 重启 `cds-api`；买方视角四页复验通过（发现并修正角色断层 §4.10）；22 步深度探针跑完三通道与门禁（§5.1）；抓出并修好 D-QA-2（删订阅 500，新增 0020 迁移）与 D-QA-3（空错误当成功），补 2 条回归锁；远程定向验证 **60 passed**；前端 lint 基线从 14 降为 13（`ContractDetail.tsx` 未使用的 `Modal` 导入），FE 校验脚本补 `set -o pipefail`（原脚本用 `npm run lint | tail` 取尾部退出码，lint 报错仍报 `FE_STEP_OK lint`，属假 OK）。
- 2026-10-15（部署 QA 第三轮：真实交付链 + 创建入口）：在 `cds-net` 内起接收端容器，部署栈上拿到结果推送的**真实投递证据**（HMAC 签名接收端可验、`redeliver` 二次外呼、失败投递 `attempt 2/6` 退避递增）；发现并补齐流程入口缺口——交付方式/频率/沙箱资源现在在**合约创建页**就能一次填完（§4.12），前端拆出 `FlowConfigFields`/`flowResource` 共用而不复制表单；远程 tsc RC=0、vitest 109 passed、eslint 回到 13 错基线，新 dist 已热替换进 `cds-frontend` 并用真实浏览器跑通创建→详情→API 回读。
- 2026-10-15（部署 QA 第四轮：调度器与留痕收口）：整树同步到远端（补回 compose/helm/`cds-frontend` 等此前不在测试树里的文件——由此产生的 9 条 `FileNotFoundError` 失败被证实是**测试树缺文件**而非产品缺陷）；部署库落到 0020 并重启；四步迁移/parity 全绿；部署面复跑 webhook 探针取到 204 与“不谎报”证据；**首次拿到自动计算的真实调度记录**（`trigger=schedule`，60s 内落库，失败原因是“无已发布生产负载”的诚实前置条件）；补齐 `app/api/notebooks.py` 审计缺口（6 个 mutation 原先只留痕 2 处，现 create/update/archive/cell_executed/run 全留痕，单元格执行只记 `code_sha256`+字节数、不落用户代码），并加 2 条审计留痕回归锁；定向三套件 **61 passed**（远程 py3.12）。审计口径同时校正一处：内容哈希未变的 PUT 不该产生 `notebook.updated`，用例改为“保存并改名”以真实触发（先前断言口径错误已修）。留痕改动在部署面复证（建/归档后 `notebook.created`+`notebook.archived` 可见且带 `_sm2_signature`）；把流程面 20 个错误码登记进 `docs/error-codes.md` 的 W2 清单；补齐文件后的验证树上 `test_capability_matrix_consistency.py` + `test_deploy_manifests.py` **25 passed**，坐实上一轮那 9 条失败纯属测试树缺文件。
- 2026-10-15（第五轮：成果交付面鉴权收口）：产品化自查发现 `GET /deliveries/dataset/{bundle_id}/download` 只按 `bundle_id` 从对象存储取件——UUID 不可猜，但**不可猜不等于已授权**：任何业务用户拿到（或从日志、截图、共享链接里读到）bundle_id 就能取走他方的密态成果包。补 0021 `delivery_bundles` 归属表（包 → 合约/会话/创建人，随包体同事务落库），下载先查归属、再复用 `_load_contract` 的合约当事方规则；无归属记录（0021 之前生成的历史包）一律 404 而不是放行。同时补上此前缺失的**正向**成果包用例（原来只有两条负向门禁），一次锁住"打包 → 当事方下载 → zip 条目/水印/清单"整条链路：`.txt` 注入按买方+会话生成的不可见水印、二进制文件按原文交付（避免破坏下游解析）。定向三套件 **63 passed**（远程 py3.12），迁移四步/parity 复跑记于 §六。
- 权威全量回归（reg5，0021 之前的整树快照，远程 py3.12 镜像，31:02）= **22 failed / 2927 passed / 5 skipped**：失败数较 `ci/known-failures.md` 基线 **24** 不升反降，通过数 +63 = SS-15 新增用例；上一轮的 9 条 `FileNotFoundError`（compose / helm / 前端 `enums.ts`）在整树同步后全部转绿，15 errors 一并消失。剩下的 22 条逐条定性为环境族（volume 插件缺 make/gcc ×13、Cube 上游检查缺仓与 pin ×3、真实 SFT 缺可加载基础模型 ×4、临时容器 Redis 未配密码 ×2），明细记在 §六。比对基准以 `ci/known-failures.md` 为准，不用本地 py3.14 的绿跑数（3.12 与 3.14 的注解求值时机不同，本地全绿结构上看不见那类缺陷）。
- 最终权威回归：reg6 为把 S6 修复并进最终树而主动停掉容器提前结束（`docker stop` 后脚本仍写了收尾哨兵），改跑 **reg7**（同一镜像、同一棵部署树 `src`，含 0021 + S6 修复）：**22 failed / 2932 passed / 5 skipped（1871.75s）**，失败集合与上一轮节点 ID 逐条 diff 一致（`FAILED_SET_IDENTICAL_REG4_VS_REG7`），基线未增长；数字已回填 §六。

### 第六轮：部署面跑真链路挖出内容载体缺陷 S6

拿脚本从 nginx 外面跑完整链路（建笔记 → 存内容 → 建负载 → 测试 → 发布 → 建 OpenAPI 端点 → 签密钥 → 调 `/open/v1/{slug}`），第一跳就碎：**`PUT /notebooks/{id}` 返回 422**。根因是载体类型不一致：`NotebookUpdate.content: dict[str, Any] | None`，而 `serializeNotebookContent` 递的是 JSON **字符串**——库里存文档、编辑器存文本，两者在边界上没对。63 条套件全绿是因为测试用 dict 递（`test_workload_created_from_notebook_entrypoint` 第 238 行），跟真实客户端形状不一样；只有真点「保存」才会暴露。连带后 `dirty` 判定永远为真（`notebook.content` 是对象，比较基准是字符串）。

修法是把边界一次定死：入库仍只认 nbformat 4 文档；`_as_document()` 接受文档本体或它的 JSON 字符串，统一走 `normalize_notebook`，不合规从原来的 **500 变 400**（`notebook content 不合规范：…`）；前端 `Notebook.content` 改回对象，`parseNotebookContent` 同时吃对象与字符串，`serializeNotebookContent` 不论来源一律回写 nbformat 4（百分比/纯脚本自动升格，保留原有 cell 元数据），新增 `canonicalizeNotebookContent()` 给 dirty 与“服务端已变”两个判定共用。

证据：远程 py3.12 镜像 `tests/test_ss15_flow_*.py` = **66 passed**（原 63 + 3 条新载体用例：字符串保存并能驱动 `entrypoint_hash`、dict 形状不回退、非文档内容 400 且 W2 体 `HTTP_400`）；本地 `tsc -b && vite build` RC=0、前端 vitest **112 passed**（既有的百分比往返用例未改一字仍过）。部署面重启后 `HAS_AS_DOCUMENT True`，同一条链路 `CHAIN_PUT 200 version=2` → `CHAIN_WORKLOAD 201 hash=c9ab0836d23a`（入口指纹由笔记单元格算出）。最强的那条证据不走测试替身：用临时 vitest 用例从真实序列化函数导出**浏览器实际发出的 374 字节 PUT 体**（`{"content": "<2 空格缩进的 nbformat 4 字符串>"}`），直接打部署实例的 `PUT /api/v1/notebooks/{id}`：**200**，回读 `version=2`、`content` 是对象、`cells=2`——入库仍只存规范文档。浏览器不需要模拟，载荷本身就是从产品代码里取出来的。

同时把“最后一米”的真限制实——不是代码缺口：开发沙箱起不来时后端回 `RUNTIME_UNAVAILABLE` / `detail.reason=dataplane_disabled`（不伪装可用），测试记录里 `error_message` 与检查项 `entrypoint_executed.detail` 都带上了原因，负载页「检查项 → 说明」直接渲染得出来（本轮一开始以为前端漏渲染，查了序列化器与 `checks` 数据后确认不是缺陷，没改一字）。该主机 Cube 镜像已在（cube-egress / cube-lifecycle-manager / cube-proxy v0.7.3-rc2），但 `CDS_CUBE_DATAPLANE_ENABLED=false`、`/data/cubelet` 未挂载、无 cubelet 进程。本轮拿仓内硬门禁《SS-10 CB-02》`deploy/cube-node/preflight.sh` 在该主机跑了一次（输出留存远程 `/root/cds-test/logs/preflight.log`）：`PREFLIGHT_RC=1`，第一道硬门禁即 **`FAIL: kvm: /dev/kvm 不存在`**（该主机无嵌套虚拟化），而 Cube 是 MicroVM 方案、CB-02 规定任一 FAIL 即退出、不降级；同一个根因也解释了全量回归里 `test_cube_upstream_check` 的环境族失败（远程没有钉住 go-sdk-v0.7.3 的上游 CubeSandbox 副本，本地 `/home/kratos/Documents/workspace/CubeSandbox` 有）。结论：“最后一米”在这台机器上不是“再跑一次节点拉起脚本”能通的，需要一台有 `/dev/kvm` 的裸金属/可嵌套虚拟化主机；而无 KVM 环境下产品侧行为是正确的（不伪装可用、`RUNTIME_UNAVAILABLE` 带原因、负载页检查项能渲染出来）。同一轮 `tee_probe` 以 `tee=none` PASS——SS-13 下 TEE 可选、裸 MicroVM 合法，这也是“诚实降级”在真机上的一个实例。
- 2026-10-15（第七轮：真浏览器把最后两层“静默失效”挖出来）：不再看界面像不像，而是把网络与 DOM 当仪表——先按真实用户路径走（登录 → `/notebooks` → 点标题进工作台），一次挖出两条与流程无关、却直接决定“可用不可用”的全站缺陷：**S7** 编辑器在运行时从公网 jsDelivr 拉 Monaco，交付环境（专网/内网）必然静默空白，改为随包本地装载并登记 `monaco-editor` 依赖（§4.14）；**S8** React 19 下 antd 静态 `message/notification/Modal.confirm` 静默不渲染，全仓 212 处用户反馈为哑——表现为“保存成功却毫无提示”，用 antd 自带 `unstableSetRender` 零依赖修复并在未改动的 `/login` 上做对照复证（§4.15）。顺带补齐前端路由兜底（未匹配路径此前内容区全空，现在给「页面不存在」+ 返回首页）。取证方法本身也定了型：**测试用的载荷形状 ≠ 真实客户端形状**（S6）、**看网络而不是看提示**（S8）、**看 `performance` 资源清单而不是猜依赖**（S7）。两轮换版均零新增 npm 依赖，远程权威前端单测 112 passed / VITEST_RC=0；后端未改一行，故权威全量数字仍取 reg7（22 failed / 2932 passed / 5 skipped，失败集合与上轮逐条一致）。工作树累计 54 项待提交（24 M + 30 ??，全为交付物；另有 vendored `Tongsuo` 嵌套仓脏，只报不动）。
- 2026-10-15（第八轮：把“外部 agent 能自动调用”补齐到 SDK）：三段流程的后端 40 个 op 此前只有两个消费面——管理前端与 `/open/v1/{slug}`，`cds_sdk` 停在 SS-12 的 key / 合约 / 会话面，第三方智能体要跑完整流程只能手拼 HTTP。本轮给 `CDSClient` 补上 notebooks 9 / workloads 9 / deliveries 15（含 `/open/v1/{slug}` 与成果包二进制下载）/ compute 5 / flow-config 1 = **40 个方法**，形状一律从部署实例的 `app.openapi()` 抽（不凭记忆），`tests/test_ss15_sdk.py` 用 `httpx.MockTransport` 钉住 wire 契约，远程权威定向套 **75 passed / rc=0**。两个细节：`_compact()` 保证 PATCH/PUT 只带调用方显式传入的字段（否则 `null` 会被后端当成“清空”）；`monaco-editor` 登记进 `package.json` 后锁文件带 `0.55.1` + integrity，因此 FE Docker 构建的 `npm ci` 仍可确定性解析（本地不跑 `npm ci`，避免毁掉 `node_modules` 后被迫依赖公网重装）。

- 2026-10-16（运行时不可用的诚实前置展示 + Cube 工具链两处真缺陷）：环境勘察先定结论——远程主机无嵌套虚拟化（`systemd-detect-virt=kvm`、`grep -c 'vmx|svm' /proc/cpuinfo` 为 0），MicroVM 在这台机器上永远不可达，所以下一件事不是去磨执行链，而是把“用不了”诚实地前置告知用户（§4.16）。同时修掉 Cube 节点工具链的两处真缺陷：preflight 的 cubelet 默认路径不是真实发布布局（`Cubelet/bin/cubelet`）、版本判定用 OR 导致“版本对、sha 错”也能过——现改 AND 并新增发布钉（`cubesandbox.release` 块，逐字取自发布包 release-manifest）与源码钉的分列（详 SS-10 §八）。新增 preflight 检查项 10（沙箱网段冲突）：上游默认 `192.168.0.0/18` 会盖住常见 LAN，本机断网的根因就是它（不是 clash 也不是 EasyConnect 自身的配置）；`install.sh` 默认改写 `10.100.0.0/18`。验证：远程定向套 **106 passed / rc=0**（runtime-status）、**29 passed / rc=0**（Cube 门禁）、前端远程 vitest **113 passed / rc=0**；未跑全量回归（本轮改动面：后端 6 + 前端 6 + 部署脚本 2 + 测试 2）。本机侧 clash 网段排除已落并热重载，但 `.one-click.env`（`0600 root`）与残留 tap 清理需 root，无免密 sudo → 未执行（详 SS-10 §八）。
