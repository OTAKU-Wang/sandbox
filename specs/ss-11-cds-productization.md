# CDS 密态沙箱 · 产品化改造方案（Round 46+ · 功能闭环 · 诚实可信 · 体验对齐）

> 日期：2026-10-12
> 版本：v1.0（初稿，待用户评审）
> 分系统编号：**SS-11**（承接 SS-01..SS-10 系列）
> 基线：Round 45 完成（round3-spec N1–N11 合入，末次提交 `4ccc43b`）；CI 基线 2420 passed / 0 failed / 3 skipped（`ci/known-failures.md`，2026-09-07 记录，Round 45 后实际更高——实施时以该文件更新值为准）；测试文件 172 个（`tests/test_*.py`，2026-10-12 计数）
> 前序（引用不重述）：
> - `specs/sandbox-productization-round3-spec.md`（N1–N11 已执行；**N12–N16 未执行**）
> - `docs/mock-remediation-plan.md`（M-01..M-28 登记；**MR-A1..A14、MR-B1 未实施**，MR-A15 部分完成；M-01 已由 SS-10 §2.9 修订定稿）
> - `specs/ss-10-cube-dataplane-connector.md` v1.1（**CB-00..CB-11 未实施**，2026-10-12 修订定稿）
> - `specs/productization-gap-analysis.md`（P-GAP-001..014 软件项 Fixed；P-GAP-004/005/006/007 Deferred=环境验收）
> - `specs/SS-gap-analysis.md`（FG-001..016 环境验收轨道，FG 表 :177-192；**FG-017 现仅定义于 SS-10，待登记入本表**——回写义务见 PZ-07）
> 性质：产品化改造主计划。新任务编号 **PZ-XX**；与既有 N/MR/CB/P-GAP 任务重叠处只引用并给出整合决议，不重述。
> 纪律：fail-closed 默认；诚实降级（项目铁律，禁伪造 TEE/GPU/链/HSM）；`CDS_*` 配置四处同步（`.env.example` / `docker-compose*.yml` / `helm/cds/values.yaml` / `app/core/config.py`）+ `tests/test_config.py`；实施时以代码为准并回写本文件。

---

## 一、产品化目标与功能闭环定义

### 1.1 一句话目标

把 CDS 从"软件闭环已完成"推进到**可试点交付的产品形态**：主链路上每一跳要么真实可用、要么显式诚实披露（`is_simulation` / 标注字段），五类角色（数商/买方/运营/监管/管理员）能在前端与 SDK 上完整走完核心旅程，且能力边界（级别 × 功能 × 真实/模拟）有一处权威来源、三端（API 文档/前端/SDK）一致。

### 1.2 六大功能闭环（本 spec 的判定框架）

| 闭环 | 定义（端到端旅程） | 判定口径 |
|---|---|---|
| **CL-1 会话执行闭环** | 合约 → 创建会话（级别选择）→ 执行（同步/流式/终端/文件）→ 输出（审查网关）→ 结果下载（T5 出域）→ 会话终止（级联回收）→ 审计落盘 | 每个级别的每步 REAL 或显式标注；无静默降级 |
| **CL-2 数据流通闭环** | 供方发布数据产品 → 目录 → 买方申请 → 合约（策略编译→OPA）→ 会话查询 → 输出网关（DP/PII/水印/审查管线）→ 结果导出 → 证明包下载 | 审查链每阶段真实执行；DP/PII 有真实算法或显式标注；计量可查 |
| **CL-3 训练闭环** | 数据集注册 → SFT（真实训练/模拟回退）→ checkpoint → 模型产出 → 推理（N5 invoke） | 训练真实或模拟显式标注；checkpoint 不伪造；DP 预算若宣称则真实执行 |
| **CL-4 信任存证闭环** | 审计事件（PG）→ SM3 Merkle → ClickHouse 同步 → 链上锚定 → 证明包（policy/quote/key/输出签名/审计摘要 + evidence hash） | 每环真实或显式 `simulated`/`not_configured` 披露；签名来源可辨 |
| **CL-5 运营运维闭环** | 监控指标（/metrics）→ 告警（ack/resolve）→ 安全态势页 → 节点运维（隔离/下线）→ 高危操作（reason+ticket_id 强制）→ 部署（runbook/五路部署文件）→ 发版门禁（Go/No-Go 清单） | 运营动作可执行且审计可检索；部署文件可解析且 CI 门禁 |
| **CL-6 用户体验闭环**（本 spec 新增） | 新用户 onboarding → 级别能力可预知 → 执行过程可观察（流式/状态/错误可行动）→ 能力边界显式（真实/模拟标注）→ 结果可交付（导出/证明包）→ 失败可理解（W2 码 + 用户可行动提示） | 关键旅程无"隐藏 404 / 误导状态 / 静默降级"；能力矩阵三端一致 |

### 1.3 "能否完成功能闭环"的判定（本轮审计结论）

**软件面可以闭环**——Round 32–45 已把软件 gap 清零（P-GAP 全 Fixed，CI 基线零失败），数据闭环主链路（上传 → DEK 加密目录 → 分级沙箱执行 → T5 审查 → 下载）软件通且**下载不绕审查网关**（`sandbox_sessions.py:1578-1590` 下载端点内再跑 `output_inspector.inspect`）。但闭环存在三层系统性缺口，使"闭环"在**诚实性**与**可交付体验**上尚未成立：

1. **诚实性缺口（S4 类）**：多处"宣称真实实为模拟"未修——SM4-GCM 实为 AES-256-GCM（`crypto.py:19-36`）、SFT 恒模拟（`llm_sft_runtime.py:614-619`）、DP 输出管线 no-op（`output_inspection.py:188-193`）、链锚定纯内存（`blockchain_adapter.py:89-221`）、SM2 签名恒软件钥（`audit_service.py:79-94`）——mock-remediation 已登记（M-01..M-15）但 MR-A1..A14 未实施。这是合规验收的一票否决项（国密声明与实现不符）。
2. **产品面缺口**：SS-10 的 L2' 新生产默认级别、节点管理、出域策略编译、模板管线均未实施（CB-00..06 零 diff）；前端无 L0 选项、无节点/设置/模板管理页；SDK 无流式、覆盖不到 20+ 个后端域；W2 错误契约 5 个异常类从未被抛出（客户端只见 `HTTP_{status}`）。
3. **体验缺口（CL-6 无设计）**：模拟/降级披露在前端**仅安全态势卡片一处**（`is_simulation` 在 `sandboxApi.ts:175` 仅类型声明、从未渲染）；能力边界（级别×功能）用户不可预知；会话详情主查询错误被误标为"会话不存在"（`SessionDetail.tsx:520`）。

### 1.4 产品化红线（继承，不可违反）

1. **禁伪造**：TEE/GPU/HSM/链硬件路径必须环境验收（FG 轨道）；软件侧只做接线与诚实披露。
2. **fail-closed 默认**：安全开关生产路径 RAISE（`validate_security_config`）；宽松只在显式配置。
3. **审计不可删**：retention 0=永久（`docs/retention.md`）；只能导出归档；janitor 演练态不切实删。
4. **部署五路同步**：infra 变更落到全部受影响部署文件 + CI 门禁（`tests/test_deploy_manifests.py`）。
5. **诚实降级**：模拟路径 `logger.warning` + 响应/审计/UI 显式字段（`is_simulation` / `simulated` / `backend` / `engine` / `trust_root` / `signing_backend` / `aead_algorithm` / `dp_applied` / `_backend_disclosure`）。

---

## 二、现状盘点与 gap 全景（2026-10-12 代码实证）

### 2.1 后端产品面

- **34 个路由**（`app/main.py:200-252` 注册，前缀仅 main.py 定义）：会话核心 `/api/v1/sandbox-sessions`（`sandbox_sessions.py` 2157L）+ 合约/产品/目录/审计/监控/输出管控/联邦/训练/推理/agent/RAG/MPC/KMS/证书/连接器/网关/沙箱节点/共享卷/K8s 运营（`/api/v1/ops`，N8）等；WS 端点 `/{id}/exec/stream` 绝对路径（`session_stream.py:122`）；`/health` `/metrics` 根路径；`/admin/*` 无认证（默认关，生产门禁拒绝开启）。
- **会话生命周期**：create（:281，配额 :362-370 → 429，TEE 证明门禁 :431 仅 L1/L2）/ execute（:1019，code_scanner → KMS 取钥 → T5 审查 :1161-1229）/ files（:1500-1655，下载 T5 门 :1549）/ snapshots（同步或 202 异步 + W11 操作轮询）/ pause·resume·refreshes（:1834-1903）/ exec（:1948）/ logs（:2071 审计尾随轮询）/ usage / proof-bundle（:642）。无 `/run` 端点——"运行"= create（立即 provision）+ execute。
- **级别枚举**（`app/models/sandbox_session.py:13-19`）：`L0/L1/L2/L3/K8S` 共 5 值。会话侧 `VALID_SANDBOX_LEVELS=("L0","L1","L2","L3","k8s")`（`schemas/sandbox_session.py:7`）；**合约侧 `("L1","L2","L3","k8s")`（`schemas/contract.py:12`）——L0 不可写入合约**（产品决策点，见 PZ-13）。
- **级别能力矩阵（代码实证）**：

  | 能力 | L0 | L1 | L2 | L3 | k8s |
  |---|---|---|---|---|---|
  | 同步执行 | ✓ | ✓（硬件/模拟） | ✓（模拟阶梯） | ✓ | ✓ |
  | 流式 WS | ✓（:359） | ✗ STREAM_UNSUPPORTED | ✗ | ✗ | ✓（:1684） |
  | TEE 证明门禁 | – | required | required | – | – |
  | 文件/快照/回滚 | ✓ | ✗ 400 | ✗ 400 | ✓ | ✗（共享卷 RWX） |
  | 暂停/恢复/续期 | ✓ | ✓ | ✓ | ✓ | ✓ |
  | 网络策略热更 | ✓（running-only） | 同 | 同 | 同 | 同 |

  流式门禁：`session_stream.py:162-165` `not in ("L0","k8s")` → `STREAM_UNSUPPORTED` + close 4409（**L3 bwrap 也不支持**——该处注释"L0 (bwrap)"系命名错误，以枚举为准）。
- **出域（T5）链**：`OutputInspector.inspect`（`output_inspection.py:118-250`）场景路由 → DLP 正则脱敏（REAL）→ 重建检测（REAL）→ k-匿名（REAL）→ **DP no-op（:188-193，M-03）**→ MIA 探针（训练模式）→ 零宽水印（REAL，`app/utils/watermark.py`）→ SM2 签名（失败静默回退 SM3 且仍标 SIGNATURE——弱披露）。PII 双层（正则 + 可选 ML，`ner_engine` 披露）。行数上限 REAL（`output_policy.py:19` 默认 10000 + SQL LIMIT 注入）。
- **信任链**：审计 SM2 签名先试 HSM 恒回退内存软件钥（`audit_service.py:79-94`，无来源字段）；PG 持久化 REAL + ClickHouse 最佳努力（失败 debug 级静默，非事务）；Merkle `sm3` 无 gmssl 静默回退 SHA-256（`merkle_service.py:14-23`）；锚定默认 PG append-only（REAL 但非链，披露诚实），FISCO/AntChain 适配器零网络代码（`blockchain_adapter.py:89-221`，M-07）；verify 在内存锚字典自验证。
- **错误契约漂移（W2）**：`docs/error-codes.md` 10 个具名码；`app/core/errors.py:15-88` 异常类齐全，但 **`SessionNotFound`/`SessionStateConflict`/`RateLimited`/`QuotaExceeded`/`RequestTimeout` 五个类从未被抛出**；配额失败走裸 `HTTPException(429)`（`sandbox_sessions.py:370`）序列化为 `HTTP_429` 而非 `QUOTA_EXCEEDED`；大量会话端点 400/403/404 用 legacy detail → 客户端只见 `HTTP_{status}`。唯一 410 抛出点 `sandbox_sessions.py:1454`。
- **AuthN/AuthZ**：JWT HS256（access/refresh 轮换）+ ws-ticket 单次 JWT（Redis 熔断）；角色五值（`models/user.py:11-16`）**注册时自选、PATCH 改角色无枚举校验**（`auth.py:89`）；无 MFA；网关 app_id/app_secret（SM3 哈希存储，`gateway_service.py:102-126`）；连接器独立 API key。租户=用户（user_id keying）+ Redis-Lua 配额。

### 2.2 SDK 产品面（`sdk/cds_sdk/client.py` 544L）

- 覆盖：会话生命周期 + 文件 + 快照 + logs/usage + 证明包 + 合约（N9）+ 任务（N9）+ 推理（N5）+ 登录/刷新。
- **缺口**：① 无 WS 流式（ws-ticket 流程不可达，流式执行对 SDK 用户不存在）；② 无 network-policy/cleanup-expired/会话 audit 方法；③ 非会话域 20+（data-products/resources/catalog 创建/训练/联邦/KMS/MPC/sandbox-db/监控/合规/证书/用户角色等）无方法；④ 无 app 凭证认证路径（仅 JWT）；⑤ **410 被误纳入重试集**（`client.py:108-127`），与 error-codes.md"停止重试"矛盾；⑥ 不解析 `is_simulation` 等披露字段。

### 2.3 前端产品面（React 19 + Vite 8 + antd 5 + react-query 5）

- **26 个 lazy 页面**（`App.tsx:51-87`）：会话列表/详情（`SessionDetail.tsx` 1303L 复杂度中心）、合约、数据产品/资源、目录、开发沙箱、审计、输出管控、监控、联邦、训练、连接器、KMS/证书、K8s 运营 4 页（N8）。
- **级别选择**：枚举 `types/enums.ts:32-38` = `L1/L2/L3/K8S` **无 L0**；硬编码两处且**标签互相矛盾**（`SessionList.tsx:243-246` "L2-软件增强/L3-最小隔离" vs `ContractCreate.tsx:157-160` "L2-轻量级虚拟化/L3-进程隔离"）；dev-sandbox 无级别选择；登录页静态 "安全域 L1/L2/L3" 装饰标签（`Login.tsx:48-49`）。
- **模拟披露：UI 全缺**——`is_simulation` 仅 `sandboxApi.ts:175` 类型声明，从未渲染；"模拟/simulation/mock" 字符串在 src/ 零匹配。后端模拟路径（TEE 软件/firecracker/链/签名/算法回退）对用户不可见——诚实降级铁律在 UI 未落地。
- **状态覆盖**：`QueryFeedback` 组件仅 13/24 页使用；`SessionDetail` 主查询无 isError 分支——任何抓取错误（500/网络）渲染"会话不存在或无权访问"（:520）；DataProductDev/ResourceList/Federation/Training/Connectors/Identity 等页无错误态。
- **缺失面**：节点管理（无路由/无 API 调用——W14 后端端点在）、系统设置（无）、模板 CRUD（只读消费 + dev-sandbox 4 个硬编码模板）、用户/API key 管理（`/users/options` 仅供合约买方下拉）。
- **代码卫生**：`ragApi.ts` 死模块（零 import）；`k8sOpsApi.ts` 独立函数形态偏离（仍走共享实例，每响应带 `cluster_available` 诚实标注——好实践）；`sandboxApi.downloadFile` 裸 axios 绕过 401 刷新（:316-349）。
- **错误码处理**：仅 409（DLP 下载阻断，:333-348）；**410/408 未处理**；流式终端 `ExecStreamTerminal.tsx`（xterm.js + ws-ticket + close 4401/4408/4409）是流式体验的既有基础。

### 2.4 闭环逐跳状态（数据闭环 · 简表）

| 跳 | 状态 | 说明 |
|---|---|---|
| 上传 | REAL 机制 / 算法披露缺失 | DEK 加密落盘（`session_files.py:100`，`[nonce12][tag16][ct]`），但"SM4-GCM"=AES-256-GCM（M-01，无 API 披露） |
| 数据→沙箱 | REAL | DEK env 注入（`CDS_DEK_HEX`），KMS 分发（`kms_service.py:168`，证明门禁 :219-303） |
| Provision 分级 | L0/L3 REAL · L1/L2 模拟（诚实标注，FG-001/003） | 见 §2.1 矩阵；L2' 待 SS-10 |
| 执行 | REAL | code_scanner + KMS + 运行时门面（未知前缀 fail-closed "SECURITY ERROR"） |
| 结果→T5 | 审查 REAL / **DP no-op** / 签名回退弱披露 | `output_inspection.py:118-250`；M-03/M-11 |
| 下载 | **不绕网关**（REAL） | 端点内再审查；critical→409 永不释放；`X-CDS-Output-Review` 头披露；边界：二进制按原样释放仅头披露 |

### 2.5 Gap 分类汇总

| 类 | 内容 | 来源 | 任务 |
|---|---|---|---|
| **G-A 诚实性（S4）** | M-01..M-15 未修（SM4/SFT/DP/MIA/联邦 JWT/TEE 死配置/链/联邦 mTLS/列 DEK/checkpoint/SM2 签名/GPU stub/seccomp/sm3） | mock-remediation（2026-10-12 核实配置键均不存在） | PZ-01..07（M-01 移交 SS-10 CB-03） |
| **G-B 产品功能** | N12-N16 未执行（GPU 插件/跨节点/MFA/前端二期页/JS SDK）；SS-10 CB-00..06 未实施；SDK 覆盖缺口；前端缺失面（节点/设置/模板/用户） | round3 执行记录 + 本轮盘点 | PZ-09..12, PZ-17/18 |
| **G-C 体验（CL-6）** | 能力矩阵无权威源；模拟披露 UI 全缺；W2 码未接线（5 类死码 + HTTP_429）；错误态覆盖 13/24 页；会话详情错误误导；级别标签矛盾/无 L0；SDK 无流式 | 本轮盘点 | PZ-13..16 |
| **G-D 环境验收** | FG-001..017 全部 open（软件侧就绪度见 §六） | SS-gap-analysis FG 表 | 环境轨道（不伪造） |
| **G-E 文档治理** | 两份产品文档并行矛盾（root 1238L 六场景 vs CDS- 983L 三场景，状态标记不一）；round3 B5 对 SFT 的误读表述；mock-remediation 未随 SS-10 回写 M-01 | 本轮盘点 | PZ-07 |

---

## 三、任务分解（PZ 系列 · 引用不重述）

### 3.0 编号与引用纪律

- **PZ-XX**：本 spec 新增任务。P0 = PZ-01..08（诚实可信基础）；P1 = PZ-09..16（功能补齐 + UX）；P2 = PZ-17..21（路线图）。
- 引用既有任务时标注来源：round3 执行记录（N 系列）、mock-remediation 排期表（MR 系列）、SS-10 里程碑（CB 系列）。
- 与 SS-10 的关系：CB-00（依赖安装）不阻塞 PZ；**PZ-12（SS-10 产品面）依赖 CB 实施进度，随 CB 里程碑渐进合入**；**MR-A1（SM4-GCM 真后端）移交 SS-10 CB-03 §2.9**（helper 方案已定稿），本 spec 保留回写义务（PZ-07）。

### 3.1 P0 · 诚实可信基础（修"宣称真实实为模拟"· 闭环可信度前提）

> 依据：mock-remediation §4.2 排期（P0-1 诚实性速修 + P0-2 能力造假修复）。2026-10-12 核实 `app/core/config.py` 中 `CDS_SM4_AEAD_BACKEND` / `CDS_TRAINING_ALLOW_SIMULATION` / `CDS_MIA_SHADOW_MODEL` / `CDS_ATTESTATION_ALLOWED_MEASUREMENTS` / `CDS_COLUMN_ENCRYPTION_KEY` / `CDS_SM3_FALLBACK_ALLOWED` / `CDS_FEDERATION_CA_BUNDLE` **均不存在**——MR-A1..A14 确认未实施。

| # | 任务 | 对应既有 | 范围摘要 | 估算 |
|---|---|---|---|---|
| PZ-01 | SFT 训练真实化收口 | MR-A2 + MR-A10 | 现状核实（2026-10-12 代码实证）：SFT 调用方硬编码 `model=None, tokenizer=None`（`llm_sft_runtime.py:614-619`）→ `cpu_trainer._can_run_real_training` 恒 False（`cpu_trainer.py:247-259`）→ **永远走 `_simulate_fallback`**，loss/checkpoint 公式伪造（`training.py:92-129` 明文 checkpoint + 伪造 state_dict）——M-02 表述正确，round3 B5"CPU torch 真实训练"系对 `CPUTrainer` 存在性的误读（回写）。收口二选一：(a) 接线真实模型（模型注册表载入 tokenizer/model → 真实 forward/backward；torch 缺装 fail-closed）；(b) 维持模拟则 `CDS_TRAINING_ALLOW_SIMULATION` 门禁（prod RAISE）+ 响应 `is_simulation=True` + checkpoint 存 `simulation_manifest` 替代伪造 state_dict。**默认 (a)**（工时差异：a=3 人日 / b=1 人日） | 3 人日 |
| PZ-02 | DP 管线接线 + 预算口径统一 | MR-A3 | 统一 `dp_engine._budgets` 与 `dp_budget_ledger` 双预算；`output_inspection.py:188-193` DP 两分支恒 True 改为真实 Laplace/Gaussian 加噪（引擎已在 :426-464）；`epsilon=0` 语义统一（0=不施加）；`dp_applied` 字段真实 | 2 人日 |
| PZ-03 | 链存证 DB 持久化 + 披露 | MR-A7 短期 | 锚定结果落 DB（新增表 + 迁移 0011）；`chain_anchoring` 状态在合规报告/证明包披露 `pg-log` / `fisco-sim` / `ant-sim` / `not_configured`（对齐既有 `_backend_disclosure` 口径）；真链 e2e 归 FG-005 | 1.5 人日 |
| PZ-04 | 诚实性速修批次 | MR-A4/A5/A6/A9/A12/A13/A14 | MIA 死门禁暴露（`llm_sft_runtime.py:465-469` + `CDS_MIA_SHADOW_MODEL` 响应字段）；`TEE_SIMULATION_MODE` 死配置清理（`config.py:198`）；联邦 JWT 静态密钥生产必填（`FEDERATION_JWT_KEY_REQUIRED` 现为 flag，`config.py:176`，关闭时仅 WARN `:319-320`）；`column_encryption.py:319` 硬编码 DEK → `CDS_COLUMN_ENCRYPTION_KEY` 必填非默认；`GPUTeeRuntimeStub` 报告加 `is_simulation=True`（`gpu_tee_runtime.py:287-295`）；firecracker seccomp 回退接 `SECCOMP_FALLBACK_ALLOWED` 门禁（`firecracker_runtime.py:642-644,784-786`，S4 缺陷收口）；`sm3` SHA-256 回退加 `CDS_SM3_FALLBACK_ALLOWED` 门禁 + warning（`crypto.py:39-45`） | 3 人日 |
| PZ-05 | 联邦互联真实门禁 | MR-A8 + MR-B1 | mTLS 证书/CA bundle（`federation_connector.py:681` 现未强制；`mutual_tls.py:283-298` 伪 PEM）：生产缺证书则拒联邦通道；互信评分非硬编码；TEE 证明白名单必填 + 本地静态根显式 `trust_root=local-simulated`（`remote_attestation.py:36`，M-17）；联邦 e2e 归 FG-008 | 2 人日 |
| PZ-06 | 证明包 + 安全态势对齐新披露字段 | 新 | evidence bundle（P-GAP-013）与安全态势卡（P-GAP-012）纳入 PZ-01..05 新字段：`signing_backend`（M-11 加字段）/ `aead_algorithm` / `dp_applied` / `trust_root` / `chain_anchoring` / `is_simulation`（SFT）；`/compliance-report` 同步；**披露元数据入 `bundle_meta`，不参与 `evidence_hash`**（实施时核实 P-GAP-013 现有 hash 口径并回写） | 1.5 人日 |
| PZ-07 | 文档回写 + 治理（随各任务） | — | ① mock-remediation 执行记录 + M-01 改为引用 SS-10 §2.9 + M-02 现状确认；② `docs/error-codes.md` 随 PZ-15 接线与 SS-10 新码更新（含 :39 SESSION_TERMINATED_OBSERVED 漂移收口）；③ **两份产品文档归一**：决策项——root `product-design.md` 与 `CDS-product-design.md` 选定单一权威（默认 root：场景更全），另一份头部标 deprecated 指针；④ round3 B5 SFT 表述回写；⑤ **FG-017 登记入 `SS-gap-analysis.md` FG 表**（对齐 FG-001..016 格式，SS-10 义务）；⑥ runbook 配置矩阵同步 | 0.5 人日（分散） |
| PZ-08 | 数据闭环 e2e 验收（软件面） | 新 | 可重复 `tests/test_e2e_data_loop.py`：synthetic 数据上传 → L0 会话查询 → 输出网关（真实审查阶段）→ 导出（含 `X-CDS-Output-Review` 头断言）→ 审计/计量/证明包齐全；**CI 门禁级**（非 slow）；作为 M-α 收口验收 | 1.5 人日 |

**P0 小计 ≈ 15 人日**（不含 MR-A1，已移交 SS-10）

### 3.2 P1 · 产品功能补齐（round3 N12–N16 余量 + SS-10 产品面 + UX 专项）

| # | 任务 | 对应既有 | 范围摘要 | 估算 |
|---|---|---|---|---|
| PZ-09 | 前端二期页面 + 缺失面 | N15 + 本轮盘点 | ① 二期页（按角色价值排序）：合规披露页 > MPC 页 > 数据管线页；② **系统状态页**（只读：`validate_security_config` 披露 + 关键 `CDS_*` 开关状态 + 模拟路径枚举，复用安全态势组件）；③ 用户管理页（ADMIN：角色变更走枚举校验 + reason+ticket_id 高危纪律；注册自选角色收紧为 admin 指派——与 PZ-10 联动）；④ 代码卫生：`ragApi.ts` 死模块处置（接线 RAG 页面或删除）、`k8sOpsApi` 形态统一、`downloadFile` 裸 axios 收敛进共享实例（修 401 刷新绕过）；⑤ 买方工作台视 M-γ 余量 | 5 人日 |
| PZ-10 | 认证增强 | N14 | MFA（TOTP）+ SM2 证书登录双轨（证书体系已真实）；角色 PATCH 枚举校验；新码（如 `AUTH_MFA_REQUIRED`）注册 error-codes.md | 4 人日 |
| PZ-11 | SDK 二期 + JS SDK | N16 + N9 续 | Python SDK：① 补 network-policy / 会话 audit / cleanup-expired / 训练 / 联邦 / 监控 / 合规等高频域（按买方旅程优先）；② **WS 流式**（ws-ticket + websocket-client；L0/k8s 先行，级别矩阵内）；③ 410 移出重试集（`client.py:108-127`）；④ 披露字段结构化解析（`is_simulation` 等 8 字段 → `response.disclosure`）；⑤ TypeScript SDK 对齐面；⑥ app 凭证（app_id/app_secret）认证路径 | 5 人日 |
| PZ-12 | SS-10 产品面整合（L2' 级别上线） | CB-01..06 配套 | ① `SandboxLevel` 加 `L2C="l2c"` + 两处 `VALID_SANDBOX_LEVELS` 同步（会话侧 `schemas/sandbox_session.py:7`、合约侧 `schemas/contract.py:12` 是否纳入 l2c=产品决策，**默认纳入**——生产默认级别必须可写合约）+ 前端枚举/标签（PZ-13 权威源接管，消除两处硬编码矛盾标签，L0 恢复可见并标"开发级"，L2/k8s 弃用灰标）；② 节点管理页（W14 端点 + cube 列"数据面就绪"徽标）；③ egress 策略编译（CB-02）→ 策略页"编译目标"列 + 热推高危弹窗；④ 模板管理页（CB-05：构建→签名→分发→节点 READY 矩阵）；⑤ 审计查询 `source=cube_egress` 过滤 + 安全态势 cube 节点区（CB-06）。**依赖 CB 进度，随 CB 里程碑渐进合入（节奏见 §五）** | 8 人日 |
| PZ-13 | 级别能力矩阵（权威源） | 新 | 数据模型 `{level × capability} → {real\|simulated\|unsupported\|planned, note, evidence?}`（基线见 §2.1 矩阵 + L2' 规划中占位）；**级别元数据（标签/定位/描述）单一来源**，替换前端两处硬编码数组与登录页静态标签；投放四处：API `GET /api/v1/meta/capability-matrix`（+ `/health` 能力区）、前端矩阵页 + 创建表单徽标、SDK `capability_matrix()` + 文档、error-codes.md 旁能力节；流式矩阵以 CB-01 spike 结论更新（回写 SS-10）；**L0 可否入合约**决策随本任务定稿 | 2 人日 |
| PZ-14 | UX：诚实降级统一呈现 | 新 | `SimulationBadge` 组件（`Feedback/` 目录，`real/simulated/unsupported` + tooltip 指向 FG 项）；接入点：创建表单（级别徽标）/ 会话详情头部三元组（级别·后端·证明，L1 软件路径显"software_confidential（模拟，FG-001）"）/ 执行结果区（`dp_applied`/`ner_engine`/签名降级标）/ 安全态势卡（`aead_algorithm`/`chain_anchoring`/`signing_backend`/`trust_root` + SS-10 `data_volume_at_rest`）/ 证明包抽屉披露分组 / 合规披露页（PZ-09）；SDK `disclosure` 属性（PZ-11④ 联动） | 3 人日 |
| PZ-15 | UX：错误体验与 W2 码接线 | 新 | ① W2 码 → 用户可行动提示映射表（现 10 具名码 + SS-10 新码 `SANDBOX_NOT_FOUND`(404)/`RUNTIME_UNAVAILABLE`(400)/`NO_NODE_TEMPLATE`(503)，落 error-codes.md"用户提示"列）；② **W2 码接线修复**：5 个死码类换入 raise 点（`SessionNotFound`→会话 404 各点、`SessionStateConflict`→状态冲突点、`QuotaExceeded`→`sandbox_sessions.py:370` 与 `gateway_service.py:206`、`RequestTimeout`→同步超时点、`RateLimited` 统一）；③ SDK 重试集修正（410 移出，PZ-11③ 同源一次修）；④ 前端 `ErrorCard`（code 徽章 + 提示 + 操作按钮：重试/查能力矩阵/带 request_id 报告）替换零散 `message.error`；⑤ 410 明确"会话已终止，不可重试"、408 带 Retry-After 自动重试一次、`STREAM_UNSUPPORTED` 指引同步执行 | 3.5 人日 |
| PZ-16 | UX：会话工作台增强 | 新 | ① 执行观察：L0/k8s/L2'（spike 后）WS 流式，其余级别轮询 `/logs` 并显式"非实时"标；输出行 T5 状态行级高亮（passed/redacted/blocked）；② 状态机可视化（pending→…终态 + 步骤耗时；失败态 audit 尾 + request_id）；③ 文件区：下载前 T5 结果预告（`X-CDS-Output-Review` 徽标），409 阻断显示发现摘要；④ 快照区：202 异步进度 + 回滚锁定态语义；⑤ 证明包一键下载；⑥ **错误态修复**：`SessionDetail.tsx:520` 主查询错误与"不存在"区分，`QueryFeedback` 补齐至 24 页；⑦ 空态引导（无会话→带徽标的创建表单） | 4 人日 |

**P1 小计 ≈ 34.5 人日**（PZ-12 随 CB 进度浮动）

### 3.3 P2 · 路线图（立项另估）

| # | 任务 | 对应既有 | 说明 |
|---|---|---|---|
| PZ-17 | GPU 加速 runtime 插件 + overlayfs 快照 | N12 | `AccelerationRuntime` 扩展点；FG-002 硬件 |
| PZ-18 | 跨节点 pause/resume / 快照外置 | N13 → SS-10 CB-07/08 | 随 SS-10 M3 对象存储 |
| PZ-19 | HE/MPC 计算集成（sf-heu Option B） | N11 后续 | 评估已交付（`/mpc/capabilities`）；两方 HE 统计最小集成（sf-heu ~6MB wheel） |
| PZ-20 | 非结构化生产引擎镜像 | FG-010 | OCR/ASR/ffmpeg/DICOM 引擎进镜像（环境项） |
| PZ-21 | 智能体框架二期 | N10 后续 | 多智能体 / 供方-需方 agent 协议（B3 完整形态） |

### 3.4 实施顺序（依赖链）

```
P0（诚实可信）: PZ-01 ∥ PZ-02 ∥ PZ-03 ∥ PZ-04 ∥ PZ-05 → PZ-06（依赖 01-05 字段）→ PZ-07 随路
P0 验收:        PZ-08 数据闭环 e2e（P0 全合入后，CI 门禁级）
P1（功能+体验）: PZ-13（能力矩阵权威源）先行
                → PZ-09 ∥ PZ-10 ∥ PZ-11 ∥ PZ-14（依赖 PZ-13 徽标源 + P0 披露字段）
                PZ-15（错误体验）依赖 error-codes.md 更新（SS-10 新码注册后收口）
                PZ-12（SS-10 产品面）随 CB-01..06 实施进度接入（CB 每完成一个里程碑接一项）
                PZ-16（工作台）依赖 PZ-13/PZ-15 基础件
里程碑:         M-α = P0 全绿 + PZ-08 入 CI（第 2 周末）
                M-β = PZ-13/14/15 + PZ-09/10/11（第 5 周末）
                M-γ = PZ-12 全部 + PZ-16 + 统一回归 + 发版门禁走查（随 CB M1..M3 收口，第 8 周末）
```

---

## 四、用户体验设计（CL-6 · 跨 PZ-13..16 的设计原则与细则）

> 现状基线（2026-10-12 实证，§2.3）：模拟披露 UI 全缺；能力边界不可预知；错误响应大量 `HTTP_{status}` 裸码；错误态覆盖 13/24 页。

### 4.1 体验红线（与产品化红线同级）

1. **能力可预知**：选择级别/发起操作前可查"这个级别能做什么、是真隔离还是模拟"。无权威能力矩阵 = 体验缺陷（PZ-13）。
2. **降级必须可见**：任何披露字段为"非真实"值的响应，对应 UI 必须有显式黄标（`SimulationBadge`），不得仅存在于 JSON 字段（PZ-14）。
3. **错误必须可行动**：每个 W2 码有"原因 + 下一步"映射；用户不需要读源码或猜（PZ-15）。
4. **过程必须可观察**：执行中状态（provisioning/running/输出行/失败原因）实时可见，轮询/流式由能力矩阵决定（PZ-16）。
5. **高危操作必须留痕**：沿用 P-GAP-014 reason+ticket_id 弹窗纪律，新页面一律复用。

### 4.2 能力矩阵（PZ-13 · 单一权威源）

- **数据模型**：`{level × capability} → {state: real|simulated|unsupported|planned, note, evidence?}`。基线 = §2.1 矩阵（代码实证）+ L2' 列（`planned`，CB-01 spike 后更新：流式/文件/模板三项待定）。
- **级别元数据**（标签/定位/描述/弃用状态）同源于此——前端两处硬编码数组（`SessionList.tsx:243-246` / `ContractCreate.tsx:157-160`）与登录页静态标签全部改为消费矩阵 API。
- **投放四处**（一致性测试锁定，DoD 9）：API `GET /api/v1/meta/capability-matrix` + `/health` 能力区；前端矩阵页 + 创建表单徽标；SDK `capability_matrix()` + 文档；`docs/error-codes.md` 旁"级别能力"节。
- **更新纪律**：级别/能力变更同改四处 + 一致性测试；SS-10 CB-01 流式 spike 结论回写 SS-10 并随发布更新矩阵。

### 4.3 诚实呈现（PZ-14）

- 新组件 `cds-frontend/src/components/Feedback/SimulationBadge.tsx`：`{state: real|simulated|unsupported, label?, detail?}`，黄标 + tooltip（什么被模拟、影响、何时变真实/指向 FG 项）。
- 接入点（按用户旅程）：
  1. 会话创建表单：级别选择器（§4.2 徽标，L0 标"开发级"，L2/k8s 弃用灰标）；
  2. 会话详情头部：三元组常显——级别 / 运行时后端（`is_simulation`·`tee_mode`·`backend`）/ 证明级别（`proof_level`）；L1 软件路径显示"software_confidential（模拟，FG-001）"；
  3. 执行结果区：`dp_applied` / `ner_engine` / 水印·签名状态；SM2→SM3 签名回退时显示"签名降级"标（收口弱披露）；
  4. 监控安全态势卡：`aead_algorithm`（tongsuo/aes_fallback）/ `chain_anchoring`（pg-log/fisco-sim/not_configured）/ `signing_backend`（software/hsm）/ `trust_root`（local-simulated/external）/ `data_volume_at_rest`（SS-10）；
  5. 证明包抽屉：披露字段区与证据字段区分组（`bundle_meta` 不参与 `evidence_hash`，§七 7.5）；
  6. 合规披露页（PZ-09）：全量披露字段列表 + 导出。
- **SDK 侧**：`CDSClient` 响应层 `disclosure` 结构化提取（8 字段），`get_session`/`get_proof_bundle` 返回对象携带。

### 4.4 错误体验（PZ-15 · 与 §3.2 范围摘要对应）

- 映射表示例（全表实施时落 `docs/error-codes.md`"用户提示"列）：

  | code | 用户提示（zh） | 下一步 |
  |---|---|---|
  | SESSION_TERMINATED (410) | 会话已终止，数据不可恢复 | 不要重试；如需数据联系管理员（审计保留） |
  | SESSION_STATE_CONFLICT (409) | 会话当前状态不允许该操作 | 先 resume/refresh 再操作（提示当前状态） |
  | OPERATION_LOCKED (503+RA) | 另一操作进行中 | 按 Retry-After 等待后自动重试 |
  | RATE_LIMITED (429+RA) | 请求过于频繁 | 按 Retry-After 退避 |
  | QUOTA_EXCEEDED (429) | 租户配额不足 | 释放配额或申请提额 |
  | STREAM_UNSUPPORTED (WS) | 该级别不支持流式执行 | 用同步 execute（能力矩阵指引） |
  | SANDBOX_NOT_FOUND (404) | 底层沙箱已不存在（节点侧） | 终止会话重建；报告 request_id |
  | RUNTIME_UNAVAILABLE (400) | L2' 数据面未启用/节点未就绪 | 联系管理员检查 cube 节点（FG-017） |
  | NO_NODE_TEMPLATE (503) | 无节点具备所需模板 | 等待模板分发完成或联系管理员 |

- 前端：`ErrorCard`（code 徽章 + 提示 + 操作按钮[重试 / 查能力矩阵 / 带 request_id 报告]）替换零散 `message.error`。
- SDK：`CDSApiError.user_hint` 属性（按映射表）；重试集修正（410 移出）。

### 4.5 会话工作台增强（PZ-16）

1. **执行观察**：L0/k8s/L2'（spike 后）WS 流式（复用 `ExecStreamTerminal.tsx` 既有 ws-ticket/close-code 机制）；其余级别轮询 `/logs`（audit-tail 端点）并显式"非实时"标；输出行 T5 状态行级高亮。
2. **状态机可视化**：状态条（pending→key_distributing→ready→running→suspended/completed/…）+ 步骤耗时；失败态 audit 尾 + request_id。
3. **文件区**：下载前 T5 预告（`X-CDS-Output-Review: passed|redacted; findings=N` → 徽标）；409 阻断显示发现摘要（不放开，联系管理员）。
4. **快照区**：202 异步操作进度条；回滚锁定态 OPERATION_LOCKED 语义。
5. **证明包**：一键生成/下载 + 披露分组（§4.3-5）。
6. **错误态/空态**：主查询错误与"不存在"区分（:520 修复）；`QueryFeedback` 补至 24 页；无会话→引导创建（带 §4.2 徽标）。

### 4.6 Onboarding 与角色旅程（PZ-09 范围内）

- 五角色（数商/买方/运营/监管/管理员）首次进入：角色引导卡（3 步：我有什么 / 我能做什么 / 去哪里做）。
- 买方旅程关键断点修复：目录搜索 → 申请 → 合约签署（SM2 签名流程引导）→ 会话创建（级别选择带徽标）→ 执行 → 导出 + 证明包。
- i18n/命名：沿用 P-GAP-010 纪律（synthetic/local/software 正式命名；mock/stub 仅 deprecated alias）。

---

## 五、SS-10 整合接口约定（L2' 级别上线的产品面）

> SS-10 v1.1（2026-10-12 定稿）已修订 4 项严重问题（SM4-GCM helper §2.9 / TLS sidecar §2.10 / private_data 注解 §2.4 / 级别表 §3.4）+ 13 项审计问题待回写（回写作为 CB-00 完成项前置，待用户批准清单）。本章只约定 **SS-10 实施与产品面（PZ-12..16）的接口**，不重述 CB 任务。

| 接口点 | SS-10 侧（CB 任务） | 产品面（PZ 任务） | 约定 |
|---|---|---|---|
| 级别枚举 | CB-01：`SandboxLevel` 加 `L2C="l2c"`（SS-10 §3.4：L2'=生产默认；旧 L2 firecracker 弃用） | PZ-12① / PZ-13 | 迁移接 SS-10 m3（`sandbox_level` 枚举扩展，alembic head 现 `0010_rag_generative` → 接 `0011+`）；前端级别选择器展示 L0/L1/L2'/L3 四值 + 弃用灰标（L2/k8s 观察期）；**l2c 入合约白名单**（`contract.py:12`）默认纳入 |
| 新 W2 码 | CB-01/CB-05：`SANDBOX_NOT_FOUND`(404) / `RUNTIME_UNAVAILABLE`(400，dataplane 关闭时 l2c 请求) / `NO_NODE_TEMPLATE`(503，无 READY 节点) 登记 `docs/error-codes.md`（现 10 具名码） | PZ-15 错误体验 | 映射表三端同步：error-codes.md（用户提示列）/ 前端 ErrorCard / SDK `CDSApiError` |
| 流式矩阵 | CB-01 spike（WS 代理 vs 轮询，M1 回写 SS-10） | PZ-13 能力矩阵 | 当前代码事实：`session_stream.py:162-165` 仅 L0/k8s 可流式（L1/L2/L3 均 `STREAM_UNSUPPORTED`，含 bwrap L3——注释"L0 (bwrap)"系命名错误）；spike 结论更新矩阵并回写 SS-10 |
| 节点管理 | CB-01：`sandbox_nodes` +cube 列（`runtime_version` 等，迁移 m1）+ 健康探针入 `session_lifecycle.py` 清理循环 | PZ-12② 节点运营页 | 沿用 W14 既有节点运维（isolate/stale/offline）UI 模式；cube 节点加"数据面就绪"徽标（`runtime_version` 非空）；门禁 `CDS_CUBE_DATAPLANE_ENABLED` 生产=true 要求 ≥1 节点就绪（SS-10 铁律） |
| egress 策略 | CB-02：`CubeNetworkConfig` 编译器（`cubebox.proto:596-633`）默认拒绝 + 热推 | PZ-12③ 策略页 | 既有网络策略页（netns/iptables，L0/L3 路径原样保留）加"编译目标"列：l2c 会话显示 CB-02 编译产物预览；热推操作复用高危 reason+ticket_id 弹窗 |
| 模板管理 | CB-05：模板 artifact（签名 + 节点 READY 状态） | PZ-12④ 模板页 | 生命周期：构建（本地）→ 签名（`CDS_CUBE_TEMPLATE_SIGNING_KEY`）→ 分发 → 节点 READY 矩阵展示；无 READY 节点 → 503 `NO_NODE_TEMPLATE` 前端提示 |
| 审计 | CB-06：强制点 JSONL → tailer → ClickHouse（`audit_events` +`source` 列）+ Merkle | PZ-12⑤ 审计查询 | 审计查询加 `source` 过滤（app / cube_egress）；安全态势加 cube 节点区（就绪节点数/审计尾随延迟/最近锚定 root） |
| 合规披露 | CB-03：`data_volume_at_rest`（加密卷 at-rest 披露）；§2.9 SM4-GCM helper 自检结果 | PZ-14 诚实呈现 | 安全态势/合规披露纳入：`aead_algorithm`（tongsuo/aes_fallback）、`data_volume_at_rest`、`chain_anchoring`（PZ-03）、`signing_backend`（M-11） |
| 部署 | CB-00/02：节点 bring-up 工具链 + 滚动升级 SOP | runbook | `specs/productization-deployment-runbook.md` 增"Cube 节点接入"节（FG-017 前置清单：KVM/XFS/eBPF/cubelet 安装/插件 socket 网络路径） |
| 弃用观察 | CB-01：`CDS_LEGACY_L2_ENABLED`（默认 false；dev 观察期 true） | PZ-12① | 弃用公告进产品内（能力矩阵页 + 创建表单 tooltip）；观察期指标：L2/k8s 会话数（/metrics 新指标）；两版本后移除=决策项 |

**整合节奏**：PZ-12 不前置阻塞 CB。CB 每完成一个里程碑，对应产品面随该里程碑收口合入：M1（CB-00/01/02，10 人日）→ PZ-12①② + 能力矩阵首版（l2c=planned→real）；M2（CB-03/04，13 人日）→ PZ-12③ + 合规披露字段；M3（CB-05/06，12 人日）→ PZ-12④⑤ + PZ-16 收尾。

---

## 六、环境验收轨道（FG-001..017 · 不伪造，持续跟踪）

> 状态核实于 2026-10-12（`specs/SS-gap-analysis.md` FG 表 :177-192 + 代码抽查）。PZ 任务**不得**在软件侧"完成"任何 FG 项；只能做软件预备（已登记于 PZ-01..05 与 SS-10）。

| FG | 项 | 优先级 | 软件侧现状（2026-10-12） | 验收前提 |
|---|---|---|---|---|
| FG-001 | 真实 L1 TEE 硬件 e2e + 厂商证书链 | P0 | 探测/`software_confidential` 诚实降级/硬件 hook 已实现；证明白名单必填（MR-B1）未实施（PZ-05） | SGX/Occlum/Gramine 环境 |
| FG-002 | 真实 GPU-TEE / NVIDIA CC 训练运行时 | P0 | 本地软件运行时 + CPU simulator（stub 未标 is_simulation，PZ-04 修） | H100/H800 CC 模式 |
| FG-003 | Firecracker 真实 guest-agent/virtio-fs 通道 | P1 | **SS-10 后该路径弃用**（`CDS_LEGACY_L2_ENABLED`）；本项随弃用关闭，真实 MicroVM 通道由 FG-017 承接 | —（关闭） |
| FG-004 | K3s/K8s 真实集群 e2e | P0 | N7 python client 已交付（`e2e-k8s.sh`）；**SS-10 后 k8s 级别弃用**——本项降级为"存量 k8s 用户观察期验证"，新路径不依赖 | 243 或专用节点 |
| FG-005 | 生产 FISCO/AntChain 存证接入 | P1 | PG append-only hash chain + 链适配器保留；锚定结果未落 DB（PZ-03 修软件面） | 真联盟链节点 |
| FG-006 | PG-in-TEE / 生产密态数据库 | P1 | SecureDuckDB + 列加密 + PG RLS + fallback（列 DEK 硬编码，PZ-04 修） | 密态 DB 环境 |
| FG-007 | 生产 HSM/Vault/国密套件 | P0 | 软件 HSM fallback（SM2 恒软件签名 M-11）；Tongsuo 未接线（**SS-10 §2.9 helper 是生产 SM4-GCM 载体**） | HSM/国密环境 |
| FG-008 | 跨空间联邦真实互操作 | P1 | FederationConnector/mTLS 代码在，**mTLS 未强制 + JWT 静态密钥（PZ-05 修）** | 第二可信空间 |
| FG-009 | 告警外部通知/SIEM/前端运营台 e2e | P1 | 告警持久化/ack/resolve/webhook 记录已实现（P-GAP-001 Fixed） | 邮件/短信/SIEM 通道 |
| FG-010 | 非结构化生产引擎镜像 | P2 | 脚本级真实（OCR/ASR/ffmpeg/DICOM），引擎未进镜像 | 生产镜像构建 |
| FG-011 | 真实分布式训练平台 | P2 | 单机闭环在；注意 M-02 该"单机"本身恒模拟（PZ-01 修） | 分布式训练环境 |
| FG-012 | 性能/容量/长稳基线 | P1 | 未压测 | 产品化验收前 |
| FG-013 | 生产部署/备份恢复/迁移演练 | P1 | 五路部署文件 + Alembic 链（head=0010）+ parity 门禁 | 预生产环境 |
| FG-014 | 前端产品化 QA/a11y/五角色 e2e | P2 | P-GAP-003/009/012 已修；新页面沿用同一纪律（PZ-09/12/14/15/16） | 试点验收前 |
| FG-015 | 安全认证/渗透/供应链 | P0 | 代码级闭环 + gitleaks CI；无渗透/SBOM 报告 | 上线前评审 |
| FG-016 | 全量 e2e/CI 回归矩阵 | P1 | CI 分层门禁（gate list，2420 基线）；e2e 矩阵待 243 | 持续交付阶段 |
| **FG-017** | **Cube 节点数据面 bring-up + 全链路 e2e**（SS-10 新增，ss-10 :363/:618；待登记入 SS-gap-analysis FG 表，PZ-07） | P0 | CB-00..06 未实施（2026-10-12 核实：`CDS_CUBE_*` 配置键均不存在于 `config.py`） | KVM/XFS 节点 + CubeSandbox v0.7.3-rc2 |

---

## 七、横切面

### 7.1 配置键矩阵（本 spec 新增 · 四处同步：`.env.example` / `docker-compose.yml` / `docker-compose.prod.yml` / `helm/cds/values.yaml` + `app/core/config.py` + `tests/test_config.py`）

| 键 | 默认 | dev/test | 生产 | 任务 |
|---|---|---|---|---|
| `CDS_TRAINING_ALLOW_SIMULATION` | true | true | **false（prod RAISE）**（仅 PZ-01 选 (b) 时） | PZ-01 |
| `CDS_MIA_SHADOW_MODEL` | false | 按需 | 按需 | PZ-04 |
| `CDS_FEDERATION_JWT_KEY` | 内置 dev 值 | 同 | **必填非默认（prod RAISE）** | PZ-04 |
| `CDS_COLUMN_ENCRYPTION_KEY` | 内置默认 | 同 | **必填非默认（prod RAISE）** | PZ-04 |
| `CDS_SM3_FALLBACK_ALLOWED` | true | true | **false（prod RAISE）** | PZ-04 |
| `CDS_FEDERATION_CA_BUNDLE` / `CDS_FEDERATION_CERT` / `CDS_FEDERATION_KEY` | 空 | 空 | **必填（prod 缺则拒联邦）** | PZ-05 |
| `CDS_ATTESTATION_ALLOWED_MEASUREMENTS` | 空 | 空 | **必填（prod RAISE）** | PZ-05 |
| `CDS_CUBE_*`（11 键） | 见 SS-10 §6.1 | 见 SS-10 | 见 SS-10 | SS-10 CB-xx（不重述，随 CB 实施同步） |

原则沿用：安全默认 fail-closed，宽松只在显式配置；新增键必须过 `tests/test_config.py`。

### 7.2 Alembic 迁移清单（依赖顺序 · 全 additive · 接 head `0010_rag_generative` 之后）

| 序 | 迁移 | 内容 | 任务 |
|---|---|---|---|
| 0011 | `chain_anchoring_records` | 链锚定记录持久化（FG-005 软件面） | PZ-03 |
| 0012 | `user_mfa`（+ 证书登录绑定表若 PZ-10 设计需要） | MFA TOTP secret/恢复码 | PZ-10 |
| 0013+ | SS-10 m1/m2/m3（cube 节点列 / 会话 runtime_ref / 级别枚举 l2c） | 见 SS-10 §6.2 | SS-10 CB-01..06 |

（SS-10 迁移序号在 CB 实施时顺延本表；本 spec 只占 0011/0012。）

### 7.3 每任务 DoD（沿用 round3/mock-remediation 模板 + 本轮新增）

1. 新测试全绿；2. 全量回归不低于基线（基线值以实施时 `ci/known-failures.md` 现行值为准，失败数不得增长）；3. `compileall` 全绿；4. 配置四处同步 + `test_config.py`；5. 模拟路径显式标注（`is_simulation`/披露字段）；6. 生产 fail-closed 开关经 `validate_security_config` 验证；7. 无裸 `except: pass` 新增；8. 部署文件改动过 `tests/test_deploy_manifests.py`；9. **（本 spec 新增）能力矩阵 / 错误映射 / 披露字段三端一致性测试**（API 返回 ↔ 前端类型 ↔ SDK 文档）；10. 回写本文件执行记录 + 相关前序文档（mock-remediation / SS-10 / error-codes）。

### 7.4 里程碑与工时汇总

| 里程碑 | 内容 | 时点 | 工时 |
|---|---|---|---|
| M-α | P0 全项（PZ-01..07）+ PZ-08 数据闭环 e2e 入 CI | 第 1–2 周末 | ≈15 人日 |
| M-β | PZ-13 能力矩阵 + PZ-14 诚实呈现 + PZ-15 错误体验 + PZ-09 二期页面 + PZ-10 MFA + PZ-11 SDK | 第 3–5 周末 | ≈22.5 人日 |
| M-γ | PZ-12 全部（随 CB M1/M2/M3 收口）+ PZ-16 工作台 + 统一回归 + 发版门禁走查 | 第 6–8 周末（与 SS-10 M1–M3 对齐） | ≈12 人日 |
| P2 | PZ-17..21 | 立项另估 | — |

**与 SS-10 的并行关系**：SS-10 CB-00..06（35 人日）与 PZ P0/P1 无依赖冲突（CB-00 前置 2 人日只钉上游，不阻塞）。建议 SS-10 先启动 CB-00+CB-01（与 PZ P0 并行第 1 周），PZ-12 随 CB 里程碑接入。

### 7.5 风险表

| 风险 | 影响 | 缓解 |
|---|---|---|
| PZ-12 与 CB 进度强耦合，CB 延期拖累 L2' 产品面 | 用户看不到新级别 | PZ-12 拆 5 小项随 CB 里程碑渐进合入；能力矩阵先以"l2c=planned"占位（诚实标注） |
| SFT 真实化（PZ-01a）触及训练 API 响应契约 | 既有集成方解析失败 | 只增字段（`is_simulation`）不改既有字段；测试锁定契约 |
| 披露字段批量新增（PZ-06）触及证明包 hash 语义 | `evidence_hash` 稳定性 | 证明包 `evidence_hash` 只覆盖安全证据字段（policy/quote/key/输出签名/审计摘要）；披露元数据入 `bundle_meta` 不参与 hash（实施时核实 P-GAP-013 现有口径并回写） |
| 前端二期页（PZ-09）范围蔓延（N15 列了 6 类页面 + 本轮新增 3 面） | 工期失控 | 按角色价值排序：合规披露 > MPC > 数据管线；系统状态/用户管理小步；gateway/买方工作台拆 M-γ |
| k8s 级别弃用观察期内存量用户迁移 | 存量 k8s 会话中断 | `CDS_LEGACY_L2_ENABLED` dev 观察期 true / 生产 false 门禁；能力矩阵页提前公告；指标观察 L2/k8s 用量 |
| W2 码接线（PZ-15②）改 raise 点影响既有测试断言 | 测试红 | 接线时同步更新断言为 W2 码（断言升级为契约断言，是修复不是回归）；410 唯一抛出点（:1454）行为不变 |
| SS-10 v1.1 的 13 项审计问题未回写 | CB 实施踩文档坑 | 回写作为 CB-00 完成项前置（清单已交付，待用户批准） |

---

## 八、执行边界声明

- 本方案只覆盖**软件可闭环**项 + UX 产品面；FG-001..017 按环境验收轨道跟踪，不伪造。
- 引用前序任务（N/MR/CB/P-GAP）不重述；实施时若与代码冲突，以代码为准并回写本文件。
- 每轮实施完成后在"执行记录"追加：任务 | 状态 | 关键产出 | 新增测试与结果 | 回归结论 | 未实施项。

## 九、执行记录

### Round 46（2026-10-12）—— P0 诚实可信批次（PZ-01..PZ-08，分支 `feat/ss-11-productization`）

| 任务 | 状态 | 关键产出 | 新增测试与结果 | 回归结论 | 未实施项 |
|---|---|---|---|---|---|
| PZ-01 SFT 真实化（选项 a） | ✅ | `resolve_sft_base_model`（`llm_sft_runtime.py:165`）fail-closed（torch 缺失/模型不可解析 → API 400）；仿真仅 `CDS_TRAINING_ALLOW_SIMULATION`（conftest dev 钉 true）且标 `is_simulation=True`；真实 forward/backward 权重变化断言锁定 | tests/test_pz01_sft_real.py 9 项全绿 | 全量回归（最终树）2604 passed / 0 failed / 3 skipped — 零失败，不劣于基线 | 分布式平台归 FG-011 |
| PZ-02 DP 接线 + 预算统一 | ✅ | 真 Laplace 加噪；`dp_applied` 真实（ε=0→false）；引擎 `_budgets` 删除，唯一源 `dp_budget_ledger`（`dp_budget.py:172-235` 同步缓存法）；预算耗尽 fail-closed 阻断；**父任务收口**：`output_gateway.py` 将 `noisy_output_rows` 接入释放路径（子轨偏差 4 的缺口） | tests/test_pz02_dp.py 6 项全绿；output/gateway 套件 125 项全绿 | 同上 | — |
| PZ-03 链锚定 DB 持久化 | ✅ | `chain_anchoring_records`（迁移 0011，head=0010 之后）；FISCO/Ant/PG 锚定落库；重启后 verify 水合；`chain_anchoring` 状态披露（pg-log/fisco-sim/ant-sim/not_configured）入 `_backend_disclosure` | tests/test_pz03_chain_persistence.py 12 项全绿；`verify_schema_parity.py` 40 表 OK | 同上 | 真链 e2e 归 FG-005 |
| PZ-04 诚实性速修批次 | ✅ | `CDS_SM3_FALLBACK_ALLOWED`（生产 RAISE）+ 回退 warning；`CDS_COLUMN_ENCRYPTION_KEY`（16B，生产必填）；`TEE_SIMULATION_MODE` 死配置移除；GPU stub `is_simulation`；seccomp 双点接 `SECCOMP_FALLBACK_ALLOWED`（S4 收口）；另修复 `crypto.py` 重复 `sm3_hash` 影子函数（潜在 bug） | tests/test_pz04_honesty_gates.py 17 项全绿 | 同上 | — |
| PZ-05 联邦真实门禁 | ✅ | `CDS_FEDERATION_ENABLED` 主开关；真 PEM 加载（无效→ValueError）；生产缺材料拒通道（fail-closed）；httpx `verify=ca_bundle`+client cert；`CDS_ATTESTATION_ALLOWED_MEASUREMENTS` 生产必填 + `trust_root` 披露（local-simulated/external）；互信评分核实无硬编码（50 基准动态调） | tests/test_pz05_federation_attestation.py 11 项全绿 | 同上 | 跨空间互操作归 FG-008 |
| PZ-06 披露字段对齐 | ✅ | `disclosure_fields()` 活状态派生（`signing_backend`/`aead_algorithm`/`trust_root`/`chain_anchoring`）；证明包 `bundle_meta`（evidence_hash 输入集不变，测试锁定）；合规报告/安全态势接入；**父任务收口**：`/api/v1/compliance/reports` HTTP body 四字段（Pydantic 模型扩展） | tests/test_pz06_disclosure.py 8 项全绿 + test_compliance_report.py 9 项（含新断言） | 同上 | HSM 真钥 FG-007；SM4 真后端归 SS-10 CB-03 |
| PZ-07 文档回写 | ✅ | mock-remediation Round 46 执行记录；runbook 配置矩阵 +7 键；FG-017 登记入 SS-gap-analysis FG 表；round3 B5 回写；两份产品文档归一（root 权威，CDS- 副本 deprecated） | — | — | error-codes 用户提示列随 PZ-15（P1）收口 |
| PZ-08 数据闭环 e2e | ✅ | `tests/test_e2e_data_loop.py`（CI 门禁级）：产品→L0 会话→上传→审计记录→执行→`X-CDS-Output-Review` 头断言下载→计量→证明包（bundle_meta 六字段）→终止；环境不可 provision 时显式 skip（白名单） | 本机 1 项全绿（0.65s，bwrap provision=running） | 同上 | — |

**验证证据（父任务亲自复核，非转述）**：`compileall` exit 0；新增 61 项测试全绿（pz01 9 + pz02 6 + pz03 12 + pz04 17 + pz05 11 + pz06 8）；Track A 矩阵 45 项、Track C 既有链 78 项、output/gateway 125 项全绿；`verify_schema_parity.py` 40 表；M-01..M-15/M-17 软件面除 MR-A1（移交 SS-10 CB-03）/MR-A11 真钥（FG-007）外全部收口。

**遗留决策/后续**：① 全量回归已结算：第二轮（最终树，含两处修复）2604 passed / 0 failed / 3 skipped（747s），零失败——不劣于 ci/known-failures.md 基线（2420/0/3，pass 增量为 Round 45/46 新测试）。修复的 2 处回归失败：`test_admin_auth.py`（PZ-04/05 新生产门先于 ADMIN_PAGES 触发，按测试本意扩展模拟环境 3 键）；`test_pz06_disclosure.py`（`AuditService._hsm_key_id` 类属性被前置测试懒初始化污染，测试改为显式钉住输入，污染复现验证顺序无关）；② P1（PZ-09..16）按 mass-ulw Run 1（`.omo/plans/ss11-p1-mass-ulw.md`）推进；③ SS-10 13 项审计问题回写仍挂起（待用户批准清单）。

### Round 46 P1 批次（2026-10-12）—— 产品功能补齐（PZ-09..PZ-16，同分支，mass-ulw Run 1 8 节点全 PASS 零重试 + Run 2 验证）

| 任务 | 状态 | 关键产出 | 新增测试与结果 | 回归结论 | 未实施项 |
|---|---|---|---|---|---|
| PZ-09 前端二期页面 + 缺失面 | ✅ | 6 新页面（能力矩阵/合规披露/MPC/数据管线/系统状态/用户管理）+ 4 服务模块，角色门禁路由（`App.tsx:93-98`）；级别标签收口至 `metaApi.levelSelectOptions`（SessionList/ContractCreate），Login 走公开 `/health` + 降级；代码卫生：`ragApi` 死模块删除、`k8sOpsApi` 偏差注释、`downloadFile` 收敛进共享实例（修 401 刷新绕过）；**父任务修**：`usersApi.changeRole` 路径 `/users/`→`/auth/users/`（跨节点 404，BE 路由在 auth router） | P1 子集 106 项全绿（含 e2e data loop） | 见本节回归结论 | ② 买方工作台随 M-γ 余量 |
| PZ-10 认证增强 | ✅ | TOTP MFA（与 SM2 证书登录双轨）；`PATCH /auth/users/{id}/role` 枚举校验 + reason+ticket_id 高危留痕；注册角色白名单收紧（默认 buyer）；`AUTH_MFA_REQUIRED` 新码 + `MFA_SECRET_ENCRYPTION_KEY` 生产启动门；迁移 0012（MFA 表） | test_pz10 17 + 矩阵 21 + admin_auth 4 = 42 项全绿；parity 41 表 | 同上 | — |
| PZ-11 SDK 二期 | ✅ | Python SDK：高频域补齐（network-policy/audit/cleanup-expired/训练/联邦/监控/合规/app 凭证）、WS 流式（ws-ticket，L0/k8s 矩阵内）、**410 移出重试集**（重试仅 429/503）、披露字段结构化（`response.disclosure`）、`CDSResponse` 统一 | sdk 35 + SDK 消费方 20 项全绿 | 同上 | N16 JS/TS SDK（仓库无 TS 目标，README 注） |
| PZ-12 SS-10 产品面整合 | ◐ | l2c（L2'）规划面：矩阵 l2c 全 planned + 标签源 + 创建选项扣留（不可创建，诚实降级）；级别元数据单一来源消除前端两处硬编码矛盾 | l2c 矩阵断言在 pz13 7 项内全绿 | 同上 | **schema 两处 `VALID_SANDBOX_LEVELS` 纳入 l2c（可写）+ 节点管理/egress 策略编译/模板管理页**——按 §3.2 依赖声明随 SS-10 CB-01/02/05 渐进合入 |
| PZ-13 级别能力矩阵（权威源） | ✅ | `GET /api/v1/meta/capability-matrix`（JWT）+ `/health` 能力区；6 级别 × 6 能力与 spec §2.1 基线逐格一致；FE 枚举 `+L0 +L2C` + `CapabilityState` 四态；SDK `capability_matrix()`；**三方一致性（API↔FE↔SDK）父任务验证 PASS** | pz13 7 + health 1 全绿 | 同上 | 流式矩阵随 CB-01 spike 结论更新（回写 SS-10） |
| PZ-14 诚实降级统一呈现 | ✅ | `SimulationBadge`（Feedback/，real/simulated/unsupported + FG tooltip）；SessionDetail 13 处接入 + bundle_meta 披露卡（**不参与 evidence_hash**）；Dashboard 安全态势卡四字段 + `data_volume_at_rest` | FE build exit 0 + vitest 18/18 + 页面 eslint 0 | 同上 | — |
| PZ-15 错误体验 + W2 接线 | ✅ | error-codes.md 新增「用户提示（zh）/下一步」列（13 码）+ `SESSION_TERMINATED_OBSERVED` 漂移收口（410 唯一契约，测试侧 404 为显式契约）；5 死码接线：`SessionTerminated` 唯一 410 点 `sandbox_sessions.py:1474`、`SessionStateConflict` ×11、`QuotaExceeded` `gateway.py:229`+`sandbox_sessions.py:370`、`RequestTimeout` 408+Retry-After、`RateLimited`、`OperationLocked` 423；FE `ErrorCard`+`errorHints`（410 强制不重试）；`api.ts` 408 幂等单重试（`_timeoutRetried` 守卫） | scoped 45 项全绿（error_contract+sandbox_sessions+gateway*） | 同上 | — |
| PZ-16 会话工作台增强 | ✅ | L0/k8s WS 流式 + 非 WS 级别轮询 `/logs` 显「非实时」标 + T5 行级高亮；状态机条 + 步骤耗时 + 失败态 audit 尾；文件区 `X-CDS-Output-Review` 预告 + 409 发现摘要；快照 202 异步轮询（`GET /sandbox-sessions/operations/{id}`，FE==BE `sandbox_sessions.py:1759` 契约验证）+ 锁定态；证明包一键下载（披露分组）；主查询错误态 404/403/500 区分（ErrorCard）+ QueryFeedback 补齐 24 页；空态引导 | FE build exit 0 + 18/18 | 同上 | — |

**验证证据（父任务亲自复核，非转述）**：`compileall` exit 0；P1 BE 子集 106 项全绿（pz13/pz10/sdk/e2e-data-loop/error-contract/sandbox-sessions/auth×2）；FE build exit 0 + vitest 18/18 + lint 零新增错误（14 项均为 4ccc43b 基线债，逐行 diff 证明不在本分支改动内，记为接受基线）；三方矩阵一致性 PASS；`verify_schema_parity.py` 41 表（0011+0012）；父任务级跨节点修复 2 处（PZ-06 compliance 422→400；usersApi changeRole 路径）。

**回归结论**：全量回归 Run 2 两轮修复 + 第三轮定讫：**2649 passed / 0 failed / 4 skipped（832.6s）**——零失败，不劣于 P0 终点基线 2604/0/3（pass 增量 +45 = P1 新测试）。过程：第一轮 26 failed（25× test_e2e_flows 注册白名单适配缺失 + 1× cert-login 测试打错 settings 实例，均为测试侧适配，父任务修复）；第二轮 1 failed（`test_certificate_service::test_issue_ca_certificate` signature_valid=False，取证：30 隔离 + 10×22 前置循环 + 全模块预导入共 40+ 次复现全绿，gmssl verify 实现符 GM/T 语义，certificate_service/tlcp_service 为 P0 时代代码 P1 未触碰 → 罕见单发事件，候选专项排查）；第三轮零失败定讫。skip 3→4 为条件性 skip 波动（非失败，不影响门禁）。详见 ci/known-failures.md 2026-10-12 P1 行。

**遗留/后续**：① PZ-12 l2c 可写纳入 + 节点/egress/模板页随 SS-10 CB-01/02/05 里程碑；② FE 14 项基线 lint 债（非本轮 scope，候选专项卫生轮）；③ `alembic check` 存量漂移 3 项（rag/trained_models FK+索引，基线 4ccc43b 即有，与 P0/P1 无关，候选后续 additive 迁移）；④ error-codes 文档 :~1454 为约数（实际 1474，tilde 标注）；⑤ `test_certificate_service::test_issue_ca_certificate` 全量回归中 1 次罕见 signature_valid=False（40+ 次受控复现未再现，非 P1 引入，候选 SM2 签名字节边界专项排查）。

### 缺口收口轮（2026-10-13）—— 独立实现度审计 + 4 缺口收口

**审计结论**（独立对代码逐条核验 PZ-01..16，非转述）：14 项 PASS、0 MISSING；2 个 PARTIAL 子项 + 2 个 CI/文档观察项在本轮全部收口。SS-10（CB-00..06）经独立复核**未实施**（其执行记录 §八 为空；代码零 cube 产物、零 `CDS_CUBE*` 键）——归 FG-017 轨道，不在本 spec 软件闭环范围。

| # | 缺口 | 收口 | 验证 |
|---|---|---|---|
| G-1 | PZ-10：role PATCH 的 reason+ticket_id 仅 FE 发送（`UserManagement.tsx`/`usersApi.ts`），BE 只读 `role` 忽略留痕字段——P-GAP-014 高危契约服务端未强制 | `UserRoleChangeRequest`（继承 `HighRiskOperationRequest`：reason ≥8 字必填、ticket_id ≤128 可选，`schemas/user.py`）；`auth.py` 端点换类型化 body，审计 detail 落 `reason`/`ticket_id` + 补 `resource_id`（对齐既有调用惯例）；3 处测试调用点更新 + 新增 2 契约测试（缺 reason/过短 → 422；200 + 审计 detail 断言） | test_pz10 18 项全绿 |
| G-2 | PZ-10：`AUTH_MFA_REQUIRED` 已 raise（`auth.py` 登录门禁）但未注册 error-codes.md（spec PZ-10 明确要求） | error-codes.md 清单新增行（401，含用户提示/下一步列） | 文档 diff |
| G-3 | PZ-13：DoD-9 三端一致性仅人工核对未测试固化；第四投放（error-codes.md 级别能力节）缺失 | 新增 `tests/test_capability_matrix_consistency.py`（3 项：API↔FE enums.ts↔SDK↔docs 四源 level 集合一致、弃用标志一致、SDK 路径+cell 状态往返）；error-codes.md 新增「级别能力（PZ-13 · 权威矩阵摘要）」节（6 级别表，测试可解析） | 3 项全绿 + pz13 7 项全绿 |
| G-4 | `test_e2e_data_loop.py` 未入 ci.yml 硬门禁清单（spec PZ-08 为 CI 门禁级） | 入 gate 清单（连带 `test_pz10_mfa.py`、`test_capability_matrix_consistency.py`——契约级测试一并提级） | YAML 解析 OK |

**回归结论**：缺口修复后全量回归定讫：**2654 passed / 0 failed / 3 skipped（771.23s）**——零失败，不劣于 P1 终点 2649/0/4（pass 增量 +5 = 新增契约测试 4 项 + 1 项条件性 skip 本轮转 pass）。详见 ci/known-failures.md 2026-10-13 行。

**遗留**：全部 SS-11 交付物仍为未提交工作树（HEAD=4ccc43b，129+ 文件，移交前需 commit）；M-γ 余量不变（PZ-12 l2c 可写 + 三页随 SS-10 CB 里程碑）。
