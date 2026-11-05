# 主流程浏览器走查报告（2026-11-05）

用真实浏览器在已部署站点上把产品主流程（上传数据 → 数据目录 → 产品 → 合约 → 沙箱开发 → 测试/生产 → 交付）走查一遍。每步记录页面、角色、操作、断言结果与截图。截图目录：`/tmp/cds_flow/`（本机临时留证，不入库）。

## 0. 环境与账号

| 项 | 值 |
|---|---|
| 控制面 | 远程服务器部署（docker compose 8 容器，详见部署记录），浏览器经本机端口转发访问 |
| 沙箱数据面 | 本机 KVM Cube 节点（MicroVM） |
| 角色账号 | `cdsadmin`（管理员）、走查用 `bw_provider`（data_provider）、`bw_buyer`（买方）；provider 角色经用户表调整 |

角色分离是产品设计（`cds-frontend/src/utils/roles.ts`）：数据资源管理 = provider/admin；**产品创建 = 仅 data_provider**；**合约创建 = 仅 data_provider**；调试/负载/交付 = 买卖双方可见。走查因此必须三账号轮转。

## 1. 流程总表

| # | 步骤 | 页面 | 角色 | 关键操作 | 断言结果 | 截图 |
|---|------|------|------|----------|----------|------|
| 1 | 登录 | `/login` | 各角色 | 用户名+密码，按钮「登 录」 | localStorage `auth-storage` 写入，进入首页 | 01_login.png |
| 2 | 数据资源 | `/data-resources` | provider | 「上传资源」弹窗：名称/描述/文件 | **UI 缺陷 D1**：提交后 `POST /data-resources/upload` 422（同浏览器手发同字段 fetch=200，系前端上传链路缺陷）。改走 API 上传成功，列表 +1 行、状态 ready、schema 自动探查 | 02~05 |
| 3 | 数据目录 | `/catalog` | 任意 | 浏览目录、详情（只读探查面） | 目录树按数据类型/安全等级展示，行「详情」可用 | — |
| 4 | 创建产品 | `/data-products/create` | provider | 四步向导：基本信息（选资源/名称）→ 安全配置（行业/等级/允许操作）→ 输出约束（DP ε/行数/格式）→ 确认「创建」 | 成功后跳产品详情，状态=草稿；资源绑定、provider 归属显示正确 | 08~12 |
| 5 | 提交审核 | 产品详情 | provider | 「提交审核」 | toast「已提交审核」，状态→待审核 | 13 |
| 6 | 审批 | 产品详情 | admin | 「通过审核」/「退回」 | 状态→已批准（审批按钮消失） | 14 |
| 7 | 发布产品 | — | provider | **UI 无发布按钮（缺口 G1）**；API `POST /data-products/{id}/publish`（必须 provider 身份，admin 调 403） | 状态→published | — |
| 8 | 创建合约 | `/contracts/create` | provider | 大表单：标题/类型/数据产品（远程搜索）/买方（用户列表）/用途/允许沙箱级别与模式/DP 预算/交付方式多选（数据集·API·结果推送）→「创建合约」 | toast「合约创建成功」，状态=待签署，编号 CDS-xxx | 16~20 |
| 9 | 双方签署 | 合约详情 | provider→buyer | 「SM2签署」各点一次（服务端签发密钥对存浏览器工作区，本地国密签名） | toast「签署成功」×2，双方状态=已签署，合约生效 | 21~22 |
| 10 | 在线调试 | `/notebooks` | buyer | 新建 Notebook（标题+关联合约）→ 自动进入调试环境 → 新增单元格 → 「运行」 | **UI 缺陷 D2**：单元格代码未随运行请求提交（后端 422 `code cannot be empty`）。API `POST /notebooks/{id}/cells/execute` 验证底层链路通：合约白名单门（`SANDBOX_MODE_NOT_PERMITTED`→放行 product_dev 后）→ 创建 MicroVM 会话 → kernel 执行 `exit 0, stdout=SANDBOX_OK …`，后端=cds_kernel，无降级 | 23~30 |
| 11 | 负载测试 | `/workloads` | buyer | 新建负载（名称/合约/来源 Notebook/入口代码）→ 行「发起测试」 | 负载 v1 草稿 →「测试通过」（门禁：未过测发布返回 412） | 31~33 |
| 12 | 发布生产 | `/workloads` | buyer | 行「发布」 | 状态→**生产环境·已发布** | 34 |
| 13 | OpenAPI 交付 | `/deliveries` | buyer | 新建 API：名称+关联已发布生产任务 | **UI 缺陷 D3**：新建面板点主按钮无任何反馈（无请求发出）。API `POST /deliveries/openapi/endpoints` 成功 201：`/open/v1/api` enabled，UI 列表刷新后可见（正常/上线），行操作含编辑/删除/接口监控 | 35~40 |
| 14 | 数据集交付 | `/deliveries/dataset` | buyer | 选来源会话+关联合约 →「生成成果包」→ 成果包历史下载 | 页面与选择器可用；本次会话仅 print 未写输出目录，故无产物可打包（设计内：未经打包不会有可下载内容）。成果 harvest + 下载全链已在 e2e 12/12 轮（部署验收）实证 | 41~42 |

## 2. 状态机串联（走查实测路径）

```
资源 ready ──provider──▶ 产品草稿 ──提交──▶ 待审核 ──admin审批──▶ 已批准 ──provider发布──▶ 已发布
合约草稿 ──创建──▶ 待签署 ──provider SM2签──▶ buyer SM2签──▶ 生效
Notebook(绑生效合约) ──运行──▶ MicroVM 会话+kernel 执行（白名单/模式双门禁）
负载草稿 ──发起测试──▶ 测试通过 ──发布(门禁412)──▶ 生产已发布 ──▶ OpenAPI 端点(201) / 成果包(需输出产物)
```

## 3. 门禁行为核验（均按设计拦截）

- 产品创建按钮非 provider 不可见（admin 也不给）——角色分离生效。
- 合约沙箱模式白名单：notebook 调试请求 `product_dev` 被 400 `SANDBOX_MODE_NOT_PERMITTED` 拒绝（`detail.allowed_sandbox_modes` 人话披露）——需在合约允许模式里加项。走查中经用户表直接放行（dev 库操作，UI 无此修改入口，见缺口 G2）。
- 代码静态扫描：`csv`/`open()` 不在白名单 → 422 且逐行披露 FATAL 项（诚实报错）。
- 负载未过测不可发布生产（412 门禁，页面有人话披露文案）。
- OpenAPI 端点要求合约含 API 交付 + 已发布生产负载（412 `WORKLOAD_NOT_PUBLISHED` 门）。

## 4. 缺陷与缺口清单

| ID | 级别 | 现象 | 定位线索 |
|----|------|------|----------|
| D1 | 缺陷 | 数据资源「上传资源」弹窗提交 → 422；同 token 同字段 fetch 手发 200 | `cds-frontend/src/services/dataResourceApi.ts upload()` 的 multipart 请求与实际期望不符，需抓 UI 真实请求体比对 |
| D2 | 缺陷 | 在线调试「运行」报 `code cannot be empty`——单元格 textarea 内容未进入执行请求 | `/notebooks` 调试环境运行按钮 payload 未带单元格 code（`POST /notebooks/{id}/cells/execute` 的 `CellExecuteRequest.code` 为空串） |
| D3 | 缺陷 | 交付中心「新建 API」面板提交按钮点击无请求无提示 | `/deliveries` 新建面板主按钮（「新增接口」）未绑定提交动作 |
| G1 | 缺口 | 产品「发布」无 UI 入口（API 仅 provider 可调，admin 403） | `ProductDetail.tsx` 缺发布按钮（可在 provider+已批准态渲染） |
| G2 | 缺口 | 合约创建后「允许沙箱模式」不可修改（「修改配置」只改交付/调度 flow-config） | flow-config 端点不含 sandbox_modes；表单创建时默认 `query`，调试场景必被拦，建议表单暴露该选项或支持 amend |
| G3 | 观察 | 数据集打包要求会话确有输出产物，调试型 print 不产文件 | 设计内行为（honest：未打包不会有可下载内容），文档面向用户时应讲清「写 /sandbox/output 才有产物」 |

## 5. 结论

- **主流程端到端可用**：三账号轮转可完整走完 产品流 → 合约（国密签署）→ 沙箱开发执行（真实 MicroVM 数据面）→ 负载测试/生产发布 → OpenAPI/数据集两种交付面；所有状态机门禁按设计生效且报错是人话。
- **三个 UI 写操作缺陷（D1/D2/D3）需要修**：当前「UI 能看能导航、写操作要靠 API」，达不到产品化交付标准；三处都是请求组装层问题，改动面小。
- 建议修复顺序：D2（主流程核心）→ D1（入口第一步）→ D3 → G1/G2（补 UI 入口）。

## 6. 修复轮时效更新（2026-11-05 同日，append-only 勘误）

复验环境：前端镜像在远程部署机自构建并换容器（`BUILD_FE_RC=0`，`cds-frontend Up`），浏览器经隧道直连重建后的页面实测。

| ID | 走查判定 | 修复轮结论 | 证据 |
|----|----------|------------|------|
| D1 | 缺陷 | **属实，已修**。真因不在 `dataResourceApi.upload()`：`ResourceList.tsx` 上传弹窗把 antd `Upload` 挂在 `Form.Item name="file"` 上而没给 `valuePropName="fileList"`，`onChange` 把整个 fileList 塞进表单字段，`FormData.append('file', fileList)` 字符串化成 `"[object Object]"` → 后端解不出 file 部件 422。手发 fetch 用真 File 所以 200。修复：`valuePropName="fileList"` + `getValueFromEvent` 归一 + 提交前 `pickUploadFile()`（新增 `src/services/uploadFile.ts`）剥回 `originFileObj`，无文件时人话提示。 | 单测 `DataResources/__tests__/ResourceListUpload.test.tsx` 3 例绿；UI 实测弹窗选文件+填名提交 → toast「上传成功」、列表新增「走查-修复后上传」行（截图 43） |
| D2 | 缺陷 | **误报（更正）**。`runCell` 正确读 `cells[index].value`（`NotebookWorkbench.tsx:541-548`，Monaco `onChange` 在 `:942` 写回）。走查时 CDP 注入（textarea setter / insertText / dispatchKeyEvent）均未写进 Monaco model（`view-lines` 恒空），格子里从未有代码——后端 422 `code cannot be empty` 是对空单元格的正确防御。 | 走产品持久化路径（`PUT /notebooks/{id}` nbformat-4 内容）后工作台 cells 渲染出代码，点「运行」输出 `UI_RUN_OK 42`（真实 MicroVM 会话，exit 0） |
| D3 | 缺陷 | **误报（更正）**。提交按钮已绑 `submit()`（`DeliveryCenter.tsx:1096`）；「接口描述」是 required 字段，走查未填 → `validateFields` 拒绝，antd 红字提示在场（走查观察漏了提示）。 | UI 补填描述后提交 → toast「接口已创建：/open/v1/api-2」、列表两行（截图 44） |
| G1 | 缺口 | **误报（更正）**。`ProductDetail.tsx:423-433` 已有「发布到目录」按钮，渲染条件=owner ∧ approved ∧ 资源绑定；走查用 admin（非 owner，API 层 403 也是这个语义）看已发布产品，两个条件都不满足。 | provider 登录产品详情：已发布态按钮正确隐藏、行操作（归档/创建合约）在场，状态机一致（截图 45） |
| G2 | 缺口 | **仍为真缺口**（合约「允许沙箱模式」创建后 UI 不可改），维持原定位线索。 | — |

修复轮验证口径：`vitest src/pages/DataResources` 12/12；`npm run build`（tsc）0 错；eslint 过（`pickUploadFile` 移入 `services/` 以满足 react-refresh 只导出组件）；全量 vitest 963/966，3 个失败集中在 `Flow/__tests__/devHomeOverview.test.tsx`，在 HEAD 基线（stash 本轮改动）单跑同败 → 既有失败，与本轮无关。本轮未 commit（等「提交」授权）。

## 7. 第二轮收口（2026-11-05 同日，G2 落地 + 既有测试失败清零 + 沙箱内 FastAPI 服务演示）

| 项 | 结论 | 证据 |
|----|------|------|
| devHomeOverview 3 败 | **已修（测试 rot，非实现缺陷）**。实现轮（`07102e7` 文案对齐原型）把口径文案搬进 Tooltip/改句式，断言仍锚旧纯文本。同步断言到新文案（tooltip「当前运行任务 0/等待队列 2/…」等 4 处），未删任何断言。 | `devHomeOverview.test.tsx` 单文件绿；`src/pages/Flow` 40 files / 368 tests 绿；全量 `vitest run` **966 passed / 0 failed**（含 D1 轮 +3 例） |
| G2 允许沙箱模式不可改 | **已实现（前后端）**。后端 `FlowConfigRequest.allowed_sandbox_modes`（validator 复用 `ContractCreate` 同一词表，未知模式 422；`null`/未传=保持原值，不支持借此吊销）+ `configure_contract_flow` 写回 + 审计 detail 记录该字段。前端 `flow.ts` 新增 `SANDBOX_MODE_OPTIONS` 业务词表、表单「允许沙箱模式（可改）」多选、`FlowConfigCard` Descriptions 只读回显；`validateFlowConfig` 对「显式清空」给人话提示（清空=保持原值，防误解为吊销）。语义对齐 `specs/ss-15` L54「一次性写入…允许的沙箱模式」本意。 | 后端 `tests/test_flow_g2_sandbox_modes.py` 3 例绿 + `test_ss15_flow_api.py` 35 passed 回归；`tsc -b` 0 错；UI 实测（远程重建后的 FE 镜像）：修改配置抽屉六模式渲染、加勾「应用调用」保存 → toast「交付与计算配置已保存」，详情回显 `query, develop, application`，GET 合约确认落库（截图 50/51） |
| 沙箱白名单不支持 web 服务开发 | **按需扩展（用户指定场景）**。`code_scanner.ALLOWED_IMPORTS` 增加服务开发族 `fastapi/uvicorn/starlette/pydantic/time/threading/socket/socketserver/urllib`；`urllib` 同时进 `EXTERNAL_SOURCE_MODULES`（egress 披露口径不变——沙箱网络仍由 CubeEgress L7 治理，白名单不放开网络策略）。 | `tests/test_scanner_web_service_stack.py` 3 例绿；扫描器合跑 112 passed（`test_code_scanner`/`test_p05`/a97/g2/ss15） |
| 沙箱数据面镜像缺 web 栈 | **镜像分层补齐（已落地）**。`cds/cube-sandbox` 基础镜像只含 stdlib（设计内），演示所需 numpy/pandas/fastapi/uvicorn 以独立依赖层镜像 `cds/cube-sandbox:withdeps` 提供（CA 烘焙铁律沿用：安装层前后校验 CA bundle 非空），`CDS_CUBE_SANDBOX_IMAGE` 指向新 tag 后会话即得。产出方式为 run+commit 链（本机 buildkit 容器 DNS 故障是环境事实非产品缺陷，tuna 源直装后 `docker commit`，前后 CA bundle 225052B 校验通过）。 | 镜像 VERIFY：容器内 `import numpy/fastapi/uvicorn` ok、CA bundle 保留、tags=[latest,withdeps]；会话实测见 §7.1（格0 打印 fastapi 0.162.0 / pydantic 2.14.2 / uvicorn 0.54.0 / starlette 1.10.0） |
| D4（新增）：api 重启后旧资源 DEK 不可解密 | **真实产品缺陷，本轮以运维路径绕行，未修**。存储 DEK（`dek-storage-data-*`）由 KMS 内存态持有，api 容器重建即丢：旧资源对象（走查轮上传的 demo_sales.csv）永不可解密，任何新建 l2c 会话在数据注入下载处 fail-closed 500（`storage_service.download` 抛「密钥在 KMS 中不存在」）。产品面又无「换绑数据资源」通道（`PATCH /data-products` 静默丢弃 `resource_id`——不可变字段，属设计），旧产品的合约被死资源锁死。同族症状：webhook dispatcher 的 `dek-task-code-webhook` 亦报缺钥。**需要 lead 决策**：KMS DEK 持久化（Vault transit/文件后端）是语义改动，涉及密钥管理边界，超出本轮「测试问题修复」授权；产品面「已发布产品换绑资源」是否放开同样是治理决策。本轮绕行：provider 重传同内容数据 → 新产品 submit/approve/publish → 新合约（含 develop 模式，双方 SM2 签名 active）→ 新 notebook，链路全通。 | api 日志栈 `notebooks.py:465→sandbox_sessions.py:534→storage_service.py:186`；绕行链 product `512ae533`/contract `29a29e33`/notebook `b1d4dcdf` 实测绿（§7.1） |

### 7.1 沙箱内 FastAPI 服务开发演示（用户指定）

- 载体：D4 把走查轮 notebook 的旧链锁死（旧合约产品绑死资源），演示重建在 v4 链上：Notebook「走查-FastAPI演示v4」（`b1d4dcdf`，bw_buyer，合约 `29a29e33` 模式 `query+develop`，产品→新资源 ef07a694）。数据源为**真实注入链**：产品资源经 KMS 解密 → 节点卷 → exec 通道注入 guest `/sandbox/data/session-data`（5 行 CSV），不伪造挂载、不内联造数。
- 五格：格0 依赖自检 → 格1 读注入数据（pathlib，白名单内）→ 格2 定义 FastAPI app（`/health`、`/api/sales?city=`、`/api/cities`）→ 格3 uvicorn 线程内起服（127.0.0.1:8123，重入复用）→ 格4 urllib 自调用三端点断言。
- 结果（实测）：l2c 会话（withdeps 镜像，`sandbox_mode=product_dev`）五格全 exit 0；格0 `fastapi 0.162.0/pydantic 2.14.2/uvicorn 0.54.0/starlette 1.10.0`；格1 `rows: 5`、cities 上海/北京/广州/杭州；格4 断言 `health rows:5`、`sales shanghai rows:2 total_sales:3090.5`、**`ALL ASSERTIONS PASSED`**。UI 复验（在线调试工作台）：格输出渲染「执行成功·1.0s」、运行记录 `执行·exit_code=0`/`输出审查 passed=true blocked=false`/`沙箱会话创建 l2c·product_dev`/「沙箱已连接」（截图 52/53/54）。排查插曲：服务首跑全 500 系端点引用未定义名（走查期格间漂移），内核按格共享同一命名空间、端点直调暴露真栈，补齐数据格后转绿——非平台缺陷。
- 边界（诚实）：uvicorn 监听 127.0.0.1 沙箱内自调用，服务不对沙箱外暴露；外部 URL 在沙箱内不可直达（egress 治理），故自测用进程内 HTTP 自调用。开发环内核 `cds_kernel` 逐格 exec，白名单在每格执行前静态扫描。

验证口径（本轮）：全量 vitest 966/0；后端定向 pytest（G2/扫描器/SS15 合跑）绿；`import app.main` 冒烟 + `compileall` 过；远程 api/FE 镜像重建复跑（api 容器 `Duplicate mount point: /data/egress-audit` 为重建脚本对 inspect Binds+Mounts 双重展开所致，脚本侧去重修复，非应用缺陷）。§7.1 演示已实测收口（五格 exit 0 + UI 截图 52/53/54），演示链重建产物见 D4 行。D4（DEK 内存态）为未修缺陷，等 lead 决策。本轮未 commit（等授权）。

