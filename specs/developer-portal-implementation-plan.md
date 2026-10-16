# 星信可信数据空间 · 数据沙箱开发平台
# 落地实现方案与实施计划 Spec (指导 AI Agent 开发手册)

> **版本**：v2.0  
> **目标**：指导研发与后续 AI Agent 完整、无歧义地落地新原型，实现业务闭环，并彻底收敛为唯 CubeSandbox 架构。  
> **上游基准**：`specs/data-sandbox-developer-portal-prd.md`、`specs/prototype-v3.html`、`specs/ss-15-flow-orchestration.md`  

---

## 一、新原型与既有前端对比与差异审计

### 1.1 现状与新原型对比矩阵

| 维度 | 既有前端 (`cds-frontend` 现状) | 新原型 (`基础版数据沙箱原型` + 闭环补齐) | 落地改造方案 |
|---|---|---|---|
| **核心定位** | 偏底层运维与安全套件管理平台，开发者旅程碎片化 | 专注算法工程师的“开发任务（Task）”交付闭环 | 引入**双模式架构**，默认呈现极简开发者工作台，管理员可切运维后台 |
| **菜单导航** | 15+ 导航大组、26+ 个页面，信息过载 | **4+2 聚焦菜单**：首页、我的开发、我的结果集、安全管理 + 文档/配额 | 顶层导航与侧边栏彻底收敛重构为 4+2 架构 |
| **沙箱运行时** | 混杂选择：`L0/L1/L2/L3/k8s`，多套逻辑并存 | **唯 CubeSandbox 架构**（KVM MicroVM 硬件隔离），无级别混淆 | **彻底下线所有非 CubeSandbox 选项**，统一锁定 level=`l2c` |
| **在线开发体验** | Notebook 多单元格实验界面 (`NotebookWorkbench.tsx`)，操作较重 | 沉浸式 Web IDE（Python 脚本目录树 + 编辑器 + 终端 + 资源占用） | 基于现有 Monaco Editor 重构为工程树一体化在线调试台 |
| **代码到生产** | 负载生成与发布逻辑隐藏较深，缺少显式发布动作 | 明确的 **“固化发布版本 (v1.0.0)”** 与 **“执行确认参数弹窗”** | 补齐 `PublishModal` 与 `ExecuteModal`，强制测试沙箱通过门禁 |
| **成果交付** | OpenAPI 与 Webhook 分散在 Flow 与 Deliveries，无文件资产预览 | **我的结果集多通道中心**：文件交付包（脱敏预览/下载）+ OpenAPI + 推送 | 建立统一结果资产 Tab 页面，补齐脱敏数据抽样预览与打包下载 |
| **安全审计** | 静态表格展示，缺乏国密验真闭环 | **操作日志 + Merkle 根与区块链存证在线验真** | 补齐 `AuditVerifyModal`，展示 SM3 原文字节哈希与区块高度/TxHash |
| **历史包袱** | 存在冗余的 K8s Pod 运维 (`/ops/*`) 与 CapabilityMatrix 多等级对比 | 无传统容器与底层混杂逻辑 | **彻底废弃下线** `/ops/*` 与 `/capability-matrix` 路由与菜单 |

---

## 二、下线与重构清单 (Deprecation & Refactoring Matrix)

为确保系统纯正采用 CubeSandbox 单一安全引擎，执行以下下线与重构操作：

### 2.1 彻底下线与清理清单
1. **下线路由与页面**：
   - 废弃 `/ops/deployments`、`/ops/network-policies`、`/ops/pvcs`、`/ops/logs`（移出路由与侧边栏）。
   - 废弃 `/capability-matrix`（移出路由与侧边栏）。
2. **清理前端表单历史枚举**：
   - 检查并修改 `src/types/enums.ts`：
     - 将 `SandboxLevel` 枚举收敛，仅保留 `CUBESANDBOX = "l2c"`。
     - 标注并废弃历史常量 `L0`、`L1`、`L2`、`L3`、`K8S`。
   - 修改 `src/pages/Contracts/ContractCreate.tsx` 与 `SessionList.tsx`：
     - 移除沙箱等级下拉框，界面仅保留“沙箱运行时：CubeSandbox 密态微虚机（默认硬件级物理隔离）”。
     - 移除旧代码中对 L0/L2/L3 的条件分支渲染。

### 2.2 双模式工作台 Layout 重构
在 `cds-frontend/src/` 中新增双工作台路由分流：
- **`DeveloperLayout.tsx`（默认主布局）**：
  - 顶栏：背景 `#1677ff`，显示平台 Logo、当前集群（`沈阳数据中心R · CubeSandbox`）、用户信息、以及【⚙️ 切至空间管理后台】按钮（仅限 `admin` / `operator` 角色可见）。
  - 侧边栏：4+2 菜单架构（首页、我的开发、我的结果集、安全管理；底部常驻：开发指引文档、环境与配额说明）。
  - 主内容区：承载开发者日常任务闭环。
- **`MainLayout.tsx`（空间管理后台布局）**：
  - 保留原有侧边栏，作为后台运维视图，顶栏提供【返回开发者工作台】快捷入口。

---

## 三、新建组件与代码模块规划

所有开发者工作台新增页面集中于 `src/pages/Developer/` 目录下：

```
cds-frontend/src/pages/Developer/
├── Layout/
│   ├── DeveloperLayout.tsx           # 开发者专属顶栏与 4+2 侧边栏
│   └── TopHeader.tsx                 # 包含集群状态、用户信息与后台切换按钮
├── Home/
│   ├── index.tsx                     # 首页-平台总览 (指标卡、内存进度条、趋势图、最新结果集)
│   └── components/
│       ├── MemoryQuotaCard.tsx       # CubeSandbox 调试/执行内存双进度条
│       └── TaskTrendChart.tsx        # 近 7 天调试与执行趋势图
├── Tasks/
│   ├── TaskList.tsx                  # 我的开发-任务列表网格 (2-1)
│   ├── TaskCreate.tsx                # 我的开发-新增开发任务表单 (2-2)
│   ├── TaskDetail.tsx                # 我的开发-任务详情页 (2-4)
│   ├── TaskIde.tsx                   # 我的开发-在线调试 Web IDE (2-3)
│   └── components/
│       ├── ExecuteModal.tsx          # 生产任务执行确认与参数配置弹窗 (2-5 补齐)
│       ├── PublishModal.tsx          # 调试代码固化发布版本弹窗 (2-7 补齐)
│       └── RunLogDrawer.tsx          # 运行记录详情与 T5 DLP 审查日志抽屉 (2-6 补齐)
├── Results/
│   ├── ResultsHub.tsx                # 我的结果集多通道资产中心 (3-0 补齐)
│   ├── ApiCreate.tsx                 # 新建 OpenAPI 导出接口 (3-2)
│   ├── ApiMonitor.tsx                # OpenAPI 接口调用监控页 (3-3)
│   └── components/
│       ├── ApiDebugModal.tsx         # OpenAPI 在线调试与测试弹窗 (3-4 补齐)
│       ├── DataPreviewModal.tsx      # 离线结果集脱敏抽样预览弹窗
│       └── WebhookConfigModal.tsx    # Webhook 结果推送订阅配置弹窗 (3-5 补齐)
├── Security/
│   ├── SecurityLog.tsx               # 安全管理-操作日志表格 (4-1)
│   └── components/
│       ├── LogDetailModal.tsx        # 操作日志详情弹窗 (4-2)
│       └── AuditVerifyModal.tsx      # 区块链与 Merkle 存证验真弹窗 (4-3 补齐)
└── Docs/
    ├── Guide.tsx                     # 开发指引文档页 (5-1 补齐)
    └── Quota.tsx                     # 环境与配额说明页 (5-2 补齐)
```

---

## 四、前端与后端 API 映射与联调规范

新原型的所有动作无缝映射到系统已实现的 SS-15 与基础后端 API，无需重复造轮子：

| 前端动作 / 页面 | 后端 API 端点 | 请求方法 | 说明 |
|---|---|---|---|
| **任务列表 / 详情** | `/api/v1/workloads` & `/api/v1/notebooks` | `GET` | 查询所有已绑定的开发任务与 Notebook |
| **新建开发任务** | `/api/v1/notebooks` | `POST` | 初始化 Notebook 载荷，指定 CubeSandbox 资源规格 |
| **在线 IDE 获取沙箱** | `/api/v1/notebooks/{id}/dev-session` | `POST` | 幂等申请/连通 CubeSandbox 调试微虚机 (8G 内存) |
| **在线 IDE 运行代码** | `/api/v1/notebooks/{id}/cells/execute` | `POST` | 单元格执行，受 `trusted_runner` 扫描保护 |
| **固化发布任务版本** | `/api/v1/workloads` & `/publish` | `POST` | 从 Notebook 固化代码，生成 Workload 记录 (通过测试门) |
| **正式执行任务** | `/api/v1/workloads/{id}/run` | `POST` | 调度 CubeSandbox 生产环境 (16G) 进行批处理执行 |
| **查询运行记录与日志** | `/api/v1/workloads/{id}/test-runs` & `/sessions/{id}/logs` | `GET` | 获取沙箱日志与 T5 DLP 审查报告 |
| **文件结果集列表与下载**| `/api/v1/deliveries/bundles` & `/bundles/{id}/download` | `GET` | 鉴权并下载注入隐形水印的离线交付结果包 |
| **新建 / 查询 OpenAPI**| `/api/v1/deliveries/endpoints` | `GET/POST` | 管理受控 OpenAPI 查询端点与白名单 |
| **OpenAPI 在线调用** | `/open/v1/{slug}` | `POST` | 平台 API Key 鉴权下的端点安全调用 |
| **结果推送订阅与流水**| `/api/v1/deliveries/webhooks` | `GET/POST` | 管理 Webhook 推送地址与查看历史投递状态 |
| **操作审计日志查询** | `/api/v1/audit/logs` | `GET` | 查询 ClickHouse 归档不可篡改操作日志 |
| **区块链存证验真** | `/api/v1/audit/verify` | `POST` | 校验 SM3 哈希、Merkle 根与星信可信链存证一致性 |

---

## 五、分阶段实施计划 (WBS for AI Agents)

为指导后续 AI Agent 高效协同开发，整个工程分为 6 个阶段，按顺序推进：

### Phase 1：基础设施收敛与类型重构
- **任务目标**：下线历史多等级沙箱枚举，锁定唯一 CubeSandbox，清理废弃模块。
- **关键行动**：
  1. 修改 `src/types/enums.ts`：将 `SandboxLevel` 收敛为 `CUBESANDBOX = "l2c"`，标记并清理 `L0/L1/L2/L3/K8S`。
  2. 审计 `src/pages/Contracts/ContractCreate.tsx`，移除沙箱等级选择器，默认固定为 CubeSandbox 密态微虚机。
  3. 隐藏或隔离旧版 `/ops/*`（K8s 运营管理）及 `/capability-matrix`。
- **验收标准**：
  - 前端编译 `npm run build` 无类型错误。
  - 创建合约时不再出现 L0~L3 下拉选项。

### Phase 2：双模式工作台框架与导航实现
- **任务目标**：构建新原型的 4+2 开发者工作台布局（`DeveloperLayout.tsx`）。
- **关键行动**：
  1. 开发顶部导航组件 `TopHeader.tsx`：呈现星信数据空间品牌、集群状态徽章（`沈阳数据中心R · CubeSandbox`）、用户信息及“切至空间管理后台”跳转按钮。
  2. 开发侧边栏 `DeveloperSidebar.tsx`：4 个一级菜单（首页、我的开发、我的结果集、安全管理）+ 2 个底部常驻链接（开发指引、环境与配额）。
  3. 配置 React Router 路由分流：默认 `/` 路径进入开发者工作台路由组。
- **验收标准**：
  - 页面展现符合 `基础版数据沙箱原型/1、首页-平台总览.png` 视觉规范。
  - 管理员角色可顺畅在开发者工作台与空间管理后台间互相切换。

### Phase 3：首页与“我的开发”全流程闭环落地
- **任务目标**：落地首页看板与“我的开发”全部 7 个页面/弹窗组件。
- **关键行动**：
  1. **首页 (`Home/index.tsx`)**：开发指标 4 宫格、沙箱内存实时双进度条（`MemoryQuotaCard.tsx`）、7 天执行趋势图、最新结果集列表。
  2. **任务列表 (`TaskList.tsx`)**：以卡片网格形式展示任务，显示 CubeSandbox 引擎标注与快捷操作。
  3. **新增任务 (`TaskCreate.tsx`)**：表单包含基本信息、我的合约、CubeSandbox 内存配额（调试 8G/执行 16G 步进器）、数据产品穿梭勾选列表。
  4. **在线调试 Web IDE (`TaskIde.tsx`)**：左侧文件目录树、中间 Monaco Editor、底部流式终端控制台、顶栏状态与“固化发布”按钮。
  5. **补齐弹窗与抽屉**：
     - `PublishModal.tsx`：固化发布任务版本，提取并展示 SM3 摘要。
     - `ExecuteModal.tsx`：执行生产任务，配置输入参数与超时时间。
     - `RunLogDrawer.tsx`：滑出全屏抽屉，展示运行标准输出与 T5 DLP 审查日志（PII 0 违规、水印注入、SM2 签名凭证）。
- **验收标准**：
  - 任务从“创建 -> 在线 IDE 调试 -> 固化发布 -> 生产执行 -> 查看日志与 DLP”全旅程打通。

### Phase 4：“我的结果集”多通道资产中心落地
- **任务目标**：落地文件资产列表、OpenAPI 管理、结果推送三大交付通道。
- **关键行动**：
  1. **资产中心主页 (`ResultsHub.tsx`)**：三大 Tab 分页（文件结果集、OpenAPI 接口、结果推送）。
  2. **文件结果集**：展示输出文件，集成 `DataPreviewModal.tsx` 预览脱敏数据结构，支持安全下载水印交付包。
  3. **OpenAPI 接口全套**：
     - 列表页展示 API ID、状态与在线开关。
     - 新建接口页 (`ApiCreate.tsx`)：支持配置 IP 白名单与字段脱敏算子。
     - 监控页 (`ApiMonitor.tsx`)：指标 3 宫格与调用流水表。
     - 补齐在线调试弹窗 `ApiDebugModal.tsx`：支持实时发送 POST 请求并查看脱敏回显。
  4. **Webhook 结果推送**：配置接收端 URL 与 Secret，查看逐条投递日志。
- **验收标准**：
  - 任务产生的结果文件可在线脱敏预览；OpenAPI 接口可正常生成、在线调试并记录监控流水。

### Phase 5：安全管理与存证验真落地
- **任务目标**：落地不可篡改操作日志与区块链存证验真。
- **关键行动**：
  1. **操作日志页 (`SecurityLog.tsx`)**：展示操作时间、类型 Tag、操作人、IP、存证状态。
  2. **详情弹窗 (`LogDetailModal.tsx`)**：展示环境标识（`CubeSandbox 16G`）、耗时拆解与关联数据。
  3. **存证验真弹窗 (`AuditVerifyModal.tsx`)**：展示 SM3 原文字节哈希、Merkle 根叶子节点、区块链区块高度与 TxHash。
- **验收标准**：
  - 日志数据仅支持只读与导出，点击“验真详情”可呈现完整的区块链存证证据链。

### Phase 6：常驻指引页面与端到端回归验证
- **任务目标**：落地左下角辅助页面，执行端到端回归测试与 CI 门禁。
- **关键行动**：
  1. 开发 `Guide.tsx`：展示 CubeSandbox 目录路径规范（`/sandbox/data`、`scripts/`、`/sandbox/output`）与依赖环境清单。
  2. 开发 `Quota.tsx`：展示当前租户的内存、CPU 与存储配额卡片。
  3. 运行前端构建与单元测试：`cd cds-frontend && npm run build && npm test`。
  4. 验证与现有后端 Python 服务的联调完整性。
- **验收标准**：
  - 前端 0 Lint 错误，0 TypeScript 编译错误，测试全部通过。
