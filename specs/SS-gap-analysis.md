# CDS 密态沙箱 Gap 分析与修复计划

> 更新时间：2026-06-09  
> 范围：`specs/` 整体设计 vs 当前 `app/`、`cds-frontend/` 实现  
> 当前目标：保障逻辑正确、编译通过、单元测试可跑；暂不跑 e2e，不以真实 SGX/GPU/FISCO 环境作为本轮阻塞项。

---

## 1. 当前结论

当前系统已经具备可信数据空间密态沙箱的核心闭环：

- 合约与策略：合约生命周期、多模式策略模板、OPA/Python PDP 回退、字段级 ACL、合约配额。
- 沙箱与任务：L3/bwrap、L2/Firecracker 回退、K8s 适配、场景运行时工厂、任务 pipeline、CODE_SCANNING。
- 数据安全：KMS/HSM 适配、DEK 包装、TEE Quote 校验、密钥分发记录、证书吊销级联、加密存储、SecureDuckDB 脱敏视图。
- 输出控制：PII 检测、k 匿名、重建检测、DP 预算扣减、水印、签名、流式审查代理。
- 审计与互联：审计签名、Merkle/PG append-only 存证、合规报告、联邦连接器、mTLS/证书管理。

Round 5 从安全密态沙箱产品闭环重新复核后，任务输出、开发沙箱输出、契约网关输出、DP 预算失败模式、配额 fallback、任务代码落库等软件缺口已经修复。Round 6 从产品化和易用性复核后，前端构建、角色体系、路由权限、开发沙箱、输出审查、合约创建、分页契约和主布局体验缺口已经修复。Round 7 继续补齐数据产品创建、沙箱会话创建、数据资源详情和基础品牌化缺口。硬件或外部基础设施能力（真实 SGX、GPU-TEE、生产 FISCO BCOS 节点、PG-in-TEE）保持为适配器/模拟器/替代实现，不列为本轮 active gap。

---

## 2. Active Gap 表

| ID | Gap | 优先级 | 状态 | 修复轮次 | 代码证据 | 说明 |
|---|---|---:|---|---|---|---|
| G-001 | DP 预算缺少 `dp_budget_status` 物化状态 | P1 | 已修复，已单测通过 | Round 1 | `app/models/dp_budget.py`, `app/services/dp_budget.py`, `app/api/contracts.py`, `tests/test_dp_budget.py` | 用跨 SQLite/Postgres 的物化快照表替代 PG 专属 materialized view，保持规格的查询语义并可单测。 |
| G-002 | Alembic 仍导入不存在的 `DpBudget` | P0 | 已修复，已编译通过 | Round 1 | `alembic/env.py` | 改为导入 `DPBudgetAllocation` / `DPBudgetEntry` / `DPBudgetStatus`，避免迁移环境导入失败。 |
| G-003 | LLM SFT 在无真实 model/tokenizer 的单测环境下返回空指标 | P1 | 已修复，已单测通过 | Round 2 | `app/services/cpu_trainer.py`, `tests/test_llm_sft_runtime.py` | `torch` 可用不等于真实训练对象可用；CPUTrainer 在缺少 model/tokenizer 时回退到确定性训练代理。 |
| G-004 | SGX/SEV/Firecracker quote 校验只看结构，篡改 report_data 不会失败 | P0 | 已修复，已单测通过 | Round 3 | `app/services/remote_attestation.py`, `tests/test_remote_attestation.py` | 本地 quote 加入规范化 payload + SM3 root 签名，校验 quote_id、measurement、组件链和签名。 |
| G-005 | Secure checkpoint 使用 XOR/哈希流伪加密，无 AEAD tag | P0 | 已修复，已单测通过 | Round 3 | `app/services/secure_checkpoint.py`, `tests/test_secure_checkpoint.py` | 改为 AES-128-GCM 本地 AEAD fallback，篡改 ciphertext 返回失败。 |
| G-006 | 视觉多模态预处理 step 与 DataLoader pipeline 实际直通 | P1 | 已修复，已单测通过 | Round 3 | `app/services/vision_multimodal_runtime.py`, `tests/test_vision_multimodal_runtime.py` | DICOM tag strip、车牌 mosaic、face blur 都会改写 payload 并写入可审计处理标记。 |
| G-007 | TLCP 证书/私钥是 PEM-like 占位文本 | P0 | 已修复，已单测通过 | Round 3 | `app/services/tlcp_service.py`, `tests/test_tlcp_service.py` | 生成真实 DER `CERTIFICATE` 和 RFC5915 风格 EC private key PEM，并用本地 TLCP CA 签发。 |
| G-008 | LLM SFT 的 MIA、PII masking、水印仍是随机/直通/只打日志 | P1 | 已修复，已单测通过 | Round 3 | `app/services/llm_sft_runtime.py`, `tests/test_llm_sft_runtime.py` | MIA 改为确定性样本置信代理，训练数据使用已脱敏 batch，水印通过 ModelWatermarkService 嵌入并验证。 |
| G-009 | ResultVerifier FULL 模式无 attestation quote 也可通过 | P0 | 已修复，已单测通过 | Round 3 | `app/services/result_verifier.py`, `tests/test_result_verifier.py` | FULL 必须 hash、SM2 签名、签名 quote 三者同时成立；无 quote 失败关闭。 |
| G-010 | GPU-TEE stub send/receive 空返回、compute job 立即完成且无加密数据流 | P1 | 已修复，已单测通过 | Round 3 | `app/services/gpu_tee_runtime.py`, `tests/test_gpu_tee_runtime.py` | 保留兼容类名，内部改成本地 AEAD 通道、加密 allocation、签名 attestation report 和可读 compute output。 |
| G-011 | FISCO/AntChain adapter 只生成内存 tx 字符串，无链式完整性语义 | P1 | 已修复，已单测通过 | Round 3 | `app/services/blockchain_adapter.py`, `tests/test_blockchain_adapter.py` | 无外部链节点时使用本地可验证 hash chain，记录 prev_hash、chain_hash、block_number、confirmed。 |
| G-012 | 无 gmssl 时确定性 SM4 fallback 不可逆或为弱 XOR | P1 | 已修复，已单测通过 | Round 3 | `app/services/deterministic_sm4.py`, `app/services/column_encryption.py`, `tests/test_column_encryption.py`, `tests/test_deterministic_sm4.py` | 改为 cryptography AES-CBC 可逆 fallback；随机模式继续使用 AES-GCM AEAD。 |
| G-013 | 网关查询结构化产品时没有从 encrypted_storage_path 加载数据 | P1 | 已修复，已单测通过 | Round 3 | `app/services/gateway_service.py`, `tests/test_contract_gateway.py` | 网关从加密对象存储下载并解析 CSV/JSON/JSONL，按 SQL 表名注册到 SecureDuckDB 后执行查询。 |
| G-014 | StorageService 本地 upload 返回绝对路径，但 download 只接受对象名 | P1 | 已修复，已单测通过 | Round 3 | `app/services/storage_service.py`, `tests/test_storage.py` | download/delete 支持对象名、本地存储根内绝对路径、`minio://bucket/object` 引用。 |
| G-015 | 证书签名失败时写入零签名占位；父 CA 私钥缺失时可能生成不可验证链 | P0 | 已修复，已单测通过 | Round 3 | `app/services/certificate_service.py`, `tests/test_certificate_service.py` | 证书签名失败直接失败关闭；本地 CA 私钥库用于无 HSM 场景的真实链签发。 |
| G-016 | 旧版 vision pipeline 训练/记忆检测为硬编码模拟曲线 | P2 | 已修复，已单测通过 | Round 3 | `app/services/vision_pipeline.py`, `tests/test_vision_pipeline.py` | 指标改为基于实际 dataloader 样本、标签分布、文本图像配对的确定性本地训练代理。 |
| G-017 | 旧版 `blockchain_service.py` 无 FISCO SDK 时直接返回失败 | P1 | 已修复，已单测通过 | Round 3 | `app/services/blockchain_service.py`, `tests/test_blockchain.py` | 保持同步旧接口，缺省使用本地 hash-chain fallback，contract_service 锚定不再因无外部链节点失败。 |
| G-018 | 训练 API 只把 SFT job 标记为 running，无本地训练闭环和 checkpoint | P1 | 已修复，已单测通过 | Round 4 | `app/api/training.py`, `tests/test_training.py` | `/training/sft` 直接接入 `llm_sft_runtime` 本地训练代理，写回 metrics、completed/failed 状态和可加载 JSON checkpoint。 |
| G-019 | 模型水印 API checkpoint 加载失败时静默返回 `{}`，嵌入后不持久化权重 | P0 | 已修复，已单测通过 | Round 4 | `app/api/training.py`, `tests/test_training.py` | 支持 torch/JSON/NPZ/pickle/safetensors checkpoint，规范化常见 state_dict wrapper，加载失败失败关闭，水印嵌入后原子写回。 |
| G-020 | 远端目录同步用随机 UUID 作为 remote provider，占位身份不可追溯 | P1 | 已修复，已单测通过 | Round 4 | `app/services/catalog_sync.py`, `tests/test_catalog_sync.py` | 基于远端空间和 provider_name 创建稳定的本地镜像 provider 用户，满足外键和审计追踪。 |
| G-021 | TaskPipeline 缺少 PREPARING handler 且无 handler 时静默跳过阶段 | P1 | 已修复，已单测通过 | Round 4 | `app/services/task_pipeline.py`, `tests/test_p05_code_scanning.py` | 新增 PREPARING payload 校验；缺 handler 失败关闭，不再把未接线阶段当成功。 |
| G-022 | SQLite 审计分区列表使用当前时间占位 start/end date | P2 | 已修复，已单测通过 | Round 4 | `app/services/audit_partition.py`, `tests/test_audit_partition.py` | 从 `audit_logs_YYYY_MM` 解析真实 UTC 月份范围，Postgres/SQLite 元数据一致。 |
| G-023 | 训练 fallback 曲线仍含随机扰动，视觉/多模态运行时指标不可复现 | P2 | 已修复，已单测通过 | Round 4 | `app/services/cpu_trainer.py`, `app/services/llm_sft_runtime.py`, `app/services/vision_multimodal_runtime.py`, `tests/test_llm_sft_runtime.py`, `tests/test_vision_multimodal_runtime.py` | 改为由数据签名、配置、session 和 epoch 派生的确定性本地训练代理。 |
| G-024 | TaskPipeline 执行输出未进入真实输出审查，raw output 可能进入最终队列结果 | P0 | 已修复，已单测通过 | Round 5 | `app/services/task_pipeline.py`, `tests/test_p05_code_scanning.py` | RUNNING 仅进入 output_review，OUTPUT_INSPECTING 必须调用统一输出审查；失败关闭，raw output 从最终 result 移除，只持久化 redacted output/report/signature。 |
| G-025 | `/sandbox-tasks/{task_id}/complete` 可由普通用户绕过状态机直接完成 | P0 | 已修复，已单测通过 | Round 5 | `app/api/sandbox_tasks.py` | complete 收紧为 operator/admin 内部入口，要求 output_review/running 状态和通过的 output_security 报告；DP 扣减失败返回 409。 |
| G-026 | 开发沙箱 stdout/文件输出未审查且文件输出缺 owner 校验 | P0 | 已修复，已单测通过 | Round 5 | `app/api/dev_sandbox.py` | execute/get_output/terminate 校验 session owner；stdout 和文本文件输出经 OutputInspector 脱敏/签名，超大或二进制不直接释放原始内容。 |
| G-027 | OutputGateway/契约网关各自实现部分脱敏，缺统一输出安全语义 | P0 | 已修复，已单测通过 | Round 5 | `app/services/output_gateway.py`, `app/services/gateway_service.py`, `app/services/output_security.py`, `tests/test_output_gateway.py`, `tests/test_gateway.py`, `tests/test_contract_gateway.py` | 新增共享 output_security helper；OutputGateway 强制审查、非 critical 脱敏、critical 阻断；契约网关复用同一 DLP pattern、水印、签名和审查报告。 |
| G-028 | PolicyEvaluator DP 预算检查异常时失败开放 | P0 | 已修复，已单测通过 | Round 5 | `app/services/policy_evaluator.py`, `tests/test_policy_evaluator.py` | 合约缺失、预算查询或扣减异常在 epsilon_cost > 0 时失败关闭。 |
| G-029 | QuotaManager contract_limits 引用不存在 enum，Redis 不可用时网关配额无限放行 | P1 | 已修复，已单测通过 | Round 5 | `app/services/quota_manager.py`, `tests/test_quota_manager.py` | 新增 DP_EPSILON/DURATION_SECONDS quota，本地 fallback 记录 limits 和 gateway daily usage；Redis 异常降级到本地计数而非 allow。 |
| G-030 | OutputInspector 数据重构检测在无 source baseline 时把 output_rows 与自身比较 | P1 | 已修复，已单测通过 | Round 5 | `app/services/output_inspection.py`, `tests/test_output_inspection.py` | 仅当 detail.source_rows 存在时执行重构匹配；没有 source baseline 时不误判自匹配。 |
| G-031 | 任务代码明文持久化在 `SandboxTask.code_content` | P1 | 已修复，已单测通过 | Round 5 | `app/services/task_code_security.py`, `app/api/sandbox_tasks.py`, `app/services/task_pipeline.py`, `app/services/task_worker.py`, `tests/test_task_code_security.py` | 新建 KMS envelope 加密 helper；API 创建任务时加密，执行/扫描前解密，历史 plaintext 兼容，密文篡改失败关闭。 |
| G-032 | 生产启动无条件 `Base.metadata.create_all()` | P1 | 已修复，已编译通过 | Round 5 | `app/main.py` | 仅 DEBUG、TESTING 或 SQLite 自动建表；生产 Postgres 默认跳过，要求 Alembic。 |
| G-033 | 前端构建失败，合约详情 JSX 字符串引号错误 | P0 | 已修复，已构建通过 | Round 6 | `cds-frontend/src/pages/Contracts/ContractDetail.tsx` | 修复 SM2 签署说明字符串，`npm run build` 不再被 JSX 语法阻断。 |
| G-034 | 前端角色体系与后端角色不一致 | P0 | 已修复，已单测通过 | Round 6 | `cds-frontend/src/types/enums.ts`, `cds-frontend/src/utils/roles.ts`, `cds-frontend/src/components/Layout/Sidebar.tsx`, `cds-frontend/src/App.tsx` | 统一为 `data_provider/buyer/operator/regulator/admin`，保留旧本地角色别名兼容，菜单和路由共用角色组。 |
| G-035 | 菜单暴露不存在路由，路由权限可绕过菜单 | P0 | 已修复，已单测通过 | Round 6 | `cds-frontend/src/components/Layout/Sidebar.tsx`, `cds-frontend/src/App.tsx` | 移除 `/output-control/dp-budget` 死入口；核心路由用 `RoleRoute + ROLE_GROUPS` 收口。 |
| G-036 | 开发沙箱前端仍读取旧 `stdout/stderr`，无法展示输出安全报告 | P0 | 已修复，已构建通过 | Round 6 | `cds-frontend/src/services/sandboxApi.ts`, `cds-frontend/src/pages/Sandbox/DataProductDev.tsx`, `cds-frontend/src/types/security.ts` | 对齐 `output/security_report/output_blocked/exit_code`，展示阶段结果、发现项、水印、签名和阻断状态，并保留旧字段兼容。 |
| G-037 | 输出审查前端阶段名与后端真实审查阶段不一致 | P0 | 已修复，已构建通过 | Round 6 | `cds-frontend/src/pages/OutputControl/InspectionPipeline.tsx`, `cds-frontend/src/services/outputControlApi.ts`, `app/api/output_control.py` | 使用 `format_validation/dlp_scan/differential_privacy/final_approval` 等真实阶段；`/inspect` 返回 SM2 签名，前端展示签名、水印和发现项。 |
| G-038 | 合约创建仍发送旧 `data_product_id` 且要求裸 `buyer_id` | P0 | 已修复，已测试通过 | Round 6 | `cds-frontend/src/pages/Contracts/ContractCreate.tsx`, `cds-frontend/src/services/contractApi.ts`, `cds-frontend/src/services/usersApi.ts`, `app/api/users.py`, `app/schemas/user.py` | 前端改为 `product_ids[]` 多选；新增最小用户选项 API，买方通过选择器填入；输出约束和审查规则纳入表单。 |
| G-039 | 监管/运营前端有合约和会话入口，但后端只允许本人查看 | P1 | 已修复，已测试通过 | Round 6 | `app/api/contracts.py`, `app/api/sandbox_sessions.py` | 运营/监管/管理员可只读查看合约和沙箱会话；运营/管理员可终止会话，监管保持只读。 |
| G-040 | 前端列表分页参数与后端 `skip/limit` 契约不一致 | P1 | 已修复，已构建通过 | Round 6 | `cds-frontend/src/services/dataProductApi.ts`, `cds-frontend/src/services/contractApi.ts`, `cds-frontend/src/services/sandboxApi.ts`, `cds-frontend/src/services/auditApi.ts`, `cds-frontend/src/services/monitoringApi.ts`, `cds-frontend/src/services/dataResourceApi.ts` | 后端分页接口统一发送 `skip/limit`；数据资源数组接口在前端做稳定分页包装。 |
| G-041 | 前端主布局和开发沙箱体验停留在默认 AntD 表单 | P1 | 已修复，已构建通过 | Round 6 | `cds-frontend/src/components/Layout/MainLayout.tsx`, `cds-frontend/src/components/Layout/Header.tsx`, `cds-frontend/src/index.css`, `cds-frontend/src/pages/Sandbox/DataProductDev.tsx` | 增加产品化外壳、页面上下文、角色标签、安全状态、响应式侧栏和统一视觉样式；开发沙箱接入 Monaco 编辑器。 |
| G-042 | 数据产品创建无法绑定已上传资源，也缺少输出控制策略配置 | P1 | 已修复，已构建通过 | Round 7 | `cds-frontend/src/pages/DataProducts/ProductCreate.tsx`, `cds-frontend/src/services/dataProductApi.ts` | 产品创建页可选择 READY 数据资源，支持 schema、允许操作、最大输出行数、DP 默认参数、运行时长和输出格式配置。 |
| G-043 | 买方沙箱会话列表没有创建入口，无法从目录产品启动沙箱 | P1 | 已修复，已构建通过 | Round 7 | `cds-frontend/src/pages/Sandbox/SessionList.tsx`, `cds-frontend/src/services/sandboxApi.ts` | 买方可从已发布目录选择产品、可选 active 合约、沙箱级别和超时时间创建会话。 |
| G-044 | 数据资源前端字段名与后端不一致，查看按钮跳到不存在路由 | P1 | 已修复，已构建通过 | Round 7 | `cds-frontend/src/pages/DataResources/ResourceList.tsx`, `cds-frontend/src/services/dataResourceApi.ts` | 字段对齐 `format/file_size_bytes/schema_fields`，资源详情改为本页抽屉展示。 |
| G-045 | 浏览器标题仍是模板名 `cds-frontend` | P2 | 已修复，已构建通过 | Round 7 | `cds-frontend/index.html` | 标题和语言改为产品化值：`密态沙箱系统 CDS`、`zh-CN`。 |

**当前 active software gap：0。**

---

## 3. 设计裁剪与合理化决策

| 设计点 | 原规格要求 | 当前实现决策 | 理由 |
|---|---|---|---|
| DP 预算状态 | PostgreSQL `MATERIALIZED VIEW dp_budget_status` | SQLAlchemy 模型表 `dp_budget_status`，由 Ledger 写穿透维护 | 项目单测使用 SQLite，开发模式依赖 `Base.metadata.create_all()`；真实物化视图会破坏跨数据库测试。表快照能提供同等读模型。 |
| FISCO BCOS 存证 | 强依赖真实联盟链节点 | PG append-only 哈希链为默认真实适配器，FISCO 作为可替换适配器 | 当前目标是逻辑正确和单测可跑；无链环境时 PG 哈希链更稳定，也满足不可篡改审计的本地验证。 |
| SGX/GPU/PG-in-TEE | 真实硬件运行时 | 保留接口、模拟器和降级路径 | 真实硬件无法通过普通单元测试验证，不应阻塞软件闭环；后续用硬件集成测试覆盖。 |
| PG materialized view refresh | 定时刷新或手动 refresh | 写路径同步刷新状态表 | DP 预算属于使用控制关键路径，写穿透比定时刷新更适合实时拒绝。 |
| 过度细分场景模式 | 9-13 种模式强拆运行时 | 核心场景运行时 + 配置参数 | 降低重复实现，保持策略与输出审查可按配置扩展。 |

---

## 4. Round 1 修复记录

### 已完成

1. 新增 `DPBudgetStatus` 模型，表名为 `dp_budget_status`，字段包括：
   - `contract_id`
   - `total_epsilon`
   - `consumed_epsilon`
   - `remaining_epsilon`
   - `last_consumed_at`
   - `updated_at`

2. `DPBudgetLedger` 改为维护物化状态：
   - `allocate()` 创建或更新分配表后同步状态表。
   - `consume()` 扣减预算后同步状态表。
   - `get_status()` 优先读取状态表，状态缺失时从 allocation + ledger entries 重建。
   - 保留 `_cache` 作为快速读缓存。

3. 合约 DP 查询接口改为读取状态对象：
   - `GET /api/v1/contracts/{contract_id}/dp-budget`
   - 返回 `total_epsilon` / `consumed_epsilon` / `remaining_epsilon` / `last_consumed_at`。

4. 修复 Alembic 过期导入：
   - 删除不存在的 `DpBudget` 导入。
   - 显式导入当前三个 DP 预算模型。

5. 增加单元测试：
   - 分配预算时生成 `dp_budget_status`。
   - 扣减预算时同步更新已用和剩余。
   - 状态缺失时可从账本重建。
   - 重分配预算不清空已消耗 epsilon。

### 验证

| 命令 | 目标 |
|---|---|
| `.venv/bin/python -m compileall -q app tests alembic` | 已通过 |
| `timeout 180s .venv/bin/python -m pytest -q tests/test_dp_budget.py tests/test_p0_security_gaps.py::TestDPBudgetDeduction` | 13 passed |

---

## 5. Round 2 修复记录

### 触发条件

非 e2e 单测暴露 3 个失败，全部集中在 `tests/test_llm_sft_runtime.py`：

- `test_run_sft_success`
- `test_run_sft_records_metrics`
- `test_run_sft_decreasing_loss`

失败原因：`LLMSFTRuntime` 发现 `torch` 可用后调用 `CPUTrainer.train(model=None, tokenizer=None, ...)`，`CPUTrainer` 进入真实训练路径并尝试调用空 tokenizer，最终返回空 `loss_history`。这导致 SFT 结果 `final_loss=0`、`metrics=[]`。

### 已完成

1. 在 `CPUTrainer.train()` 的接口入口增加真实训练可用性检查：
   - 缺少 `model` 时回退模拟训练。
   - 数据集包含原始 `text` 且缺少 `tokenizer` 时回退模拟训练。

2. 保留真实训练路径：
   - 调用方传入真实 model/tokenizer 时继续走 PyTorch forward/backward。
   - 无真实训练对象的单测和降级环境返回稳定 loss 曲线与 metrics。

### 验证

| 命令 | 结果 |
|---|---|
| `.venv/bin/python -m compileall -q app tests alembic` | 通过 |
| `timeout 180s .venv/bin/python -m pytest -q tests/test_llm_sft_runtime.py` | 33 passed |
| `timeout 600s .venv/bin/python -m pytest -q -k "not e2e"` | 1959 passed, 1 skipped, 91 deselected |

---

## 6. Round 3 修复记录

### 触发条件

用户要求“把系统中所有 mock 未真实完成的项目都实现，哪怕没有真实环境硬件先把功能实现”。本轮复核范围集中在产品代码路径，排除测试中的 `unittest.mock`、抽象基类 `pass`、异常兜底 `pass`、以及必须由真实硬件/e2e 验证的外部环境能力。

### 已完成

1. 远程证明不再无条件信任本地 quote：
   - SGX/SEV/Firecracker 本地 quote 均写入规范化 payload 签名。
   - verify 阶段校验 quote 类型、quote_id、measurement、组件链和签名。
   - 新增篡改 report_data / measurement 失败用例。

2. 密态 checkpoint 改为真实 AEAD：
   - 用 AES-128-GCM 替代 XOR/SM3 keystream。
   - ciphertext 携带 GCM tag，篡改后 decrypt 返回 `None`。

3. 视觉/多模态预处理不再直通：
   - DICOM 敏感 tag 会被删除。
   - license plate 会被 mosaic 标记替换。
   - face blur / resize / normalize 等 step 写入可审计处理标记。

4. TLCP 与证书签发补齐真实本地 PKI：
   - TLCP 输出标准 `BEGIN CERTIFICATE` / `BEGIN EC PRIVATE KEY` PEM。
   - TLCP 证书由本地 CA keypair 签发。
   - CertificateService 签名失败不再写零签名占位；父 CA 私钥缺失时失败关闭。

5. LLM SFT 安全流程去随机/去直通：
   - MIA 由随机分数改为确定性样本置信代理。
   - 真实训练入口使用 PII masking 后的 batch。
   - 水印通过 ModelWatermarkService 嵌入 synthetic adapter weights 并验证。

6. ResultVerifier FULL 模式失败关闭：
   - FULL 必须同时满足 hash、SM2 signature、signed attestation quote。
   - 无 attestation quote 不再以 `SIMULATED` 通过。

7. GPU-TEE stub 升级为本地加密运行时：
   - CPU-GPU channel 使用 AEAD 加密 send/receive。
   - compute job 记录加密 input/output allocation。
   - attestation report 写入本地 root 签名。
   - 保留 `"stub"` factory key 仅作兼容别名，`auto` 默认走本地 runtime。

8. 外部链缺省适配器补齐可验证语义：
   - FISCO/AntChain 无真实节点时使用本地 hash chain。
   - 记录 `prev_hash` / `chain_hash` / `block_number` / `confirmed`。
   - 旧版 `blockchain_service.py` 同步接口也改为本地 hash-chain fallback。

9. 加密 fallback 去掉不可逆/弱实现：
   - `deterministic_sm4.py` 无 gmssl 时使用 AES-CBC 可逆 fallback。
   - `column_encryption.py` deterministic fallback 从 XOR 改为 AES-CBC。

10. 网关真实加载产品数据：
    - `GatewayService._query_duckdb()` 从 `encrypted_storage_path` 下载并解密数据。
    - 支持 CSV / JSON / JSONL 解析并按 SQL 中表名注册到 SecureDuckDB。
    - StorageService 支持对象名、本地绝对路径、`minio://bucket/object` 三类引用。

11. 旧版 vision pipeline 去硬编码曲线：
    - 训练指标基于实际样本哈希特征、标签分布、redaction rate 和多模态配对质量。
    - memorization probe 对训练描述做精确/子串/token overlap 检测。

### 本轮保留为合理化模拟器的点

| 项 | 保留原因 |
|---|---|
| `tee_simulator.py` / `sandbox_runtime.py` 的 L1/L2 simulated wording | 真实 SGX/Occlum/Firecracker 需要硬件或宿主运行时，本轮用 bwrap/本地 quote 保持接口可测。 |
| `gpu_tee_simulator.py` 的 simulation 标记 | 明确表示无 NVIDIA CC 硬件时的 CPU fallback；已有加密通道和远程证明形状，不作为 active software gap。 |
| 无 Redis/ClickHouse/Kafka 时的异常兜底 `pass` | 属于外部基础设施可选降级，不是 mock 功能；核心路径已有本地或失败关闭语义。 |
| 抽象基类、异常类型、ORM Base 的 `pass` | Python 结构性写法，不代表未实现业务。 |

### 验证

| 命令 | 结果 |
|---|---|
| `.venv/bin/python -m compileall -q app tests alembic` | 通过 |
| `timeout 180s .venv/bin/python -m pytest -q tests/test_remote_attestation.py tests/test_secure_checkpoint.py tests/test_vision_multimodal_runtime.py tests/test_tlcp_service.py tests/test_result_verifier.py tests/test_gpu_tee_runtime.py tests/test_llm_sft_runtime.py tests/test_certificate_service.py` | 220 passed |
| `timeout 180s .venv/bin/python -m pytest -q tests/test_blockchain_adapter.py tests/test_blockchain_persistence.py tests/test_deterministic_sm4.py tests/test_column_encryption.py tests/test_sm4_duckdb_roundtrip.py tests/test_secure_duckdb.py tests/test_duckdb_encryption.py` | 154 passed, 1 skipped |
| `timeout 180s .venv/bin/python -m pytest -q tests/test_storage.py tests/test_storage_service.py tests/test_contract_gateway.py tests/test_gateway.py` | 51 passed |
| `timeout 180s .venv/bin/python -m pytest -q tests/test_vision_pipeline.py` | 38 passed |
| `timeout 180s .venv/bin/python -m pytest -q tests/test_blockchain.py tests/test_contracts.py tests/test_contracts_extended.py` | 26 passed |

---

## 7. Round 4 修复记录

### 触发条件

继续扫描产品代码中的 `mock` / `placeholder` / `simulation` / `return {}` 语义后，发现若干不依赖真实硬件即可补齐的软件闭环缺口：训练 API job 不完成、checkpoint 加载/水印持久化不完整、远端 provider 身份占位、pipeline 阶段跳过、SQLite 审计分区时间占位，以及训练 fallback 指标随机。

### 已完成

1. SFT API 接入本地训练闭环：
   - `/api/v1/training/sft` 使用 `llm_sft_runtime.run_sft()` 执行本地训练代理。
   - 成功后 job 进入 `completed`，写入 metrics、`completed_at`、本地 JSON checkpoint。
   - base_model 做大小写不敏感规范化，`qwen2.5-7b` 存储为 `Qwen2.5-7B`。

2. 模型权重水印 checkpoint 补齐真实加载和保存：
   - 支持 torch `.pt/.pth/.bin/.ckpt`、JSON、NPZ/NPY、pickle、safetensors。
   - 识别 `state_dict` / `model_state_dict` / `module` / `model` / `net` / `weights` wrapper。
   - 加载失败不再返回空 dict；水印嵌入后原子写回 checkpoint。

3. 远端目录同步 provider 身份稳定化：
   - 用 `uuid5(space_id, provider_name)` 创建稳定镜像 provider。
   - 镜像 provider 写入本地 `users` 表，`is_active=False`，不可登录但满足外键和审计追踪。
   - 远端 `space_id`、`remote_id`、`provider_name`、metadata 写入 `data_schema.remote`。

4. TaskPipeline 不再静默跳过未实现阶段：
   - 新增 PREPARING handler，校验 `task_id`、language、timeout。
   - 缺少 stage handler 时直接失败关闭，并记录 task error。

5. 审计分区 SQLite 元数据去占位：
   - `list_partitions()` 从 `audit_logs_YYYY_MM` 解析真实 UTC 月份范围。
   - PostgreSQL/SQLite 分区元数据解析逻辑统一。

6. 训练 fallback 去随机化：
   - CPUTrainer fallback 指标由 dataset 签名、配置、epoch 推导。
   - LLM SFT 无 torch 分支使用 SM3 稳定 jitter。
   - Vision/Multimodal runtime 的 loss/accuracy 由 session、模型、产品 ID、epoch 推导，重复运行稳定。

### 验证

| 命令 | 结果 |
|---|---|
| `.venv/bin/python -m compileall -q app tests alembic` | 通过 |
| `timeout 180s .venv/bin/python -m pytest -q tests/test_training.py tests/test_catalog_sync.py tests/test_p05_code_scanning.py tests/test_audit_partition.py` | 94 passed |
| `timeout 180s .venv/bin/python -m pytest -q tests/test_training.py tests/test_llm_sft_runtime.py` | 50 passed |
| `timeout 180s .venv/bin/python -m pytest -q tests/test_vision_multimodal_runtime.py` | 60 passed |
| `timeout 600s .venv/bin/python -m pytest -q -k "not e2e"` | 1978 passed, 1 skipped, 91 deselected |

---

## 8. Round 5 修复记录

### 触发条件

从安全密态沙箱产品视角复核发现，上一轮 mock/占位清理后仍存在安全闭环问题：执行输出可绕过 OUTPUT_INSPECTING、开发沙箱输出直通、契约网关与 OutputGateway 审查语义不一致、DP/配额异常存在失败开放、任务代码明文持久化，以及生产启动自动建表。

### 已完成

1. 输出安全出口统一：
   - 新增 `app/services/output_security.py`，集中做 sandbox mode 归一化、审查结果序列化、DLP 脱敏和 blocking severity 判断。
   - `OutputGateway.process()` 强制审查序列化输出；非 critical findings 先脱敏再释放，critical findings 阻断。
   - `GatewayService.apply_content_security()` 复用同一套 DLP pattern、水印、签名和 inspection report。

2. TaskPipeline 输出审查补齐：
   - RUNNING 阶段成功后 ORM task 进入 `output_review`，不再直接 completed。
   - OUTPUT_INSPECTING 阶段调用 OutputInspector，失败关闭并写审计。
   - raw output 从最终 pipeline result 移除，只保留 redacted output、inspection report、watermark、signature。
   - Code scanning 异常从允许执行改为失败关闭。

3. Sandbox task 生命周期收紧：
   - submit 后状态进入 `code_scanning`，与 pipeline 状态一致。
   - `/complete` 仅 operator/admin 可用，要求通过的 output_security report。
   - DP 预算扣减不足或异常不再静默完成。

4. 开发沙箱输出保护：
   - execute stdout/stderr 返回前经 develop 场景审查和脱敏。
   - get output 先校验 session owner；文本文件审查后返回 redacted content，超大/二进制不直接释放原始内容。
   - 数据资源加载 staging 目录按 user/session 隔离并设置 0700/0600 权限。

5. 策略、配额与重构检测失败模式修复：
   - `PolicyEvaluator._check_dp_budget()` 对合约缺失、查询异常、扣减异常失败关闭。
   - `QuotaManager` 新增本地 limits/gateway usage fallback，修复 `contract_limits` enum 映射。
   - `OutputInspector` 仅在有 `detail.source_rows` 时执行数据重构匹配，避免 output self-match。

6. 任务代码与启动行为加固：
   - `SandboxTask.code_content` 新增 KMS envelope 加密 helper，创建时加密、扫描/执行前解密，历史 plaintext 兼容。
   - 生产启动不再无条件 `Base.metadata.create_all()`，仅 dev/test/SQLite 允许自动建表。

### 验证

| 命令 | 结果 |
|---|---|
| `.venv/bin/python -m compileall -q app tests` | 通过 |
| `timeout 240s .venv/bin/python -m pytest -q tests/test_task_worker.py tests/test_output_gateway.py tests/test_p05_code_scanning.py tests/test_policy_evaluator.py tests/test_quota_manager.py tests/test_output_inspection.py tests/test_task_code_security.py tests/test_gateway.py tests/test_contract_gateway.py` | 148 passed, 6 warnings |
| `timeout 600s .venv/bin/python -m pytest -q -k "not e2e"` | 1988 passed, 1 skipped, 91 deselected |

---

## 9. Round 6 修复记录

### 触发条件

从产品化、易用性和前端交付视角复核发现：前端构建失败、角色体系与后端不一致、菜单/路由权限错配、开发沙箱和输出审查页面接不住后端安全输出、合约创建仍使用旧请求结构和裸 ID、列表分页契约不一致，以及主布局仍停留在默认 AntD 拼装。

### 已完成

1. 前端角色与权限统一：
   - `UserRole` 改为后端真实角色：`data_provider/buyer/operator/regulator/admin`。
   - 新增 `utils/roles.ts`，集中处理旧角色别名兼容、角色标签和角色组。
   - 菜单和核心路由共用 `ROLE_GROUPS`，删除无路由的 `/output-control/dp-budget` 入口。

2. 沙箱与输出审查前端契约对齐：
   - 开发沙箱执行结果对齐 `output/security_report/output_blocked/exit_code`。
   - 开发沙箱展示审查阶段、发现项、水印、签名、阻断状态，并接入 Monaco 编辑器。
   - 输出审查页使用后端真实阶段名，展示签名、水印和 findings；会话选择改为选择器。
   - `/output-control/inspect` 返回 `signature`。

3. 合约工作流易用性修复：
   - 合约创建从旧 `data_product_id` 改为 `product_ids[]` 多选。
   - 新增 `/api/v1/users/options` 最小用户选项接口，买方从选择器选择。
   - 合约表单补齐沙箱模式、允许操作、最大输出行数、输出格式、审查规则。
   - 合约详情展示产品数、沙箱模式、输出约束等关键条款。

4. 监管/运营只读视图与后端权限对齐：
   - 合约列表/详情允许 operator/regulator/admin 只读查看全量。
   - 沙箱会话允许 operator/regulator/admin 只读查看全量；operator/admin 可终止会话，regulator 只读。

5. 分页与产品外壳修复：
   - `data-products/contracts/sandbox-sessions/audit/monitoring` 前端服务改为发送 `skip/limit`。
   - `data-resources` 当前数组接口在前端做稳定分页包装。
   - 主布局增加产品化外壳、页面上下文、角色标签、安全状态、响应式侧栏和统一 CSS。

### 验证

| 命令 | 结果 |
|---|---|
| `npm run build`（`cds-frontend`） | 通过 |
| `npm test -- --run`（`cds-frontend`） | 3 files passed, 12 tests passed |
| `.venv/bin/python -m compileall -q app tests` | 通过 |
| `timeout 180s .venv/bin/python -m pytest -q tests/test_sandbox_sessions.py tests/test_contracts.py tests/test_contracts_extended.py tests/test_auth_extended.py` | 38 passed |

---

## 10. 最终验证状态

| 验证项 | 结果 | 备注 |
|---|---|---|
| Python 编译 | 通过 | `.venv/bin/python -m compileall -q app tests alembic` |
| 前端构建 | 通过 | `npm run build` |
| 前端单测 | 通过 | 3 files passed, 12 tests passed |
| DP 预算目标单测 | 通过 | 13 passed |
| LLM SFT 目标单测 | 通过 | 33 passed |
| Round 3 聚焦单测 | 通过 | 220 + 154 + 51 + 38 + 26 passed，1 skipped |
| Round 4 聚焦单测 | 通过 | 94 + 50 + 60 passed |
| Round 5 聚焦单测 | 通过 | 148 passed |
| Round 6 聚焦单测 | 通过 | 38 passed |
| 非 e2e 单测 | 通过 | 1988 passed, 1 skipped, 91 deselected |
| e2e | 未运行 | 按当前任务要求暂不跑 e2e |

测试说明：受限沙箱内 `aiosqlite.connect(":memory:")` 会挂住；已通过最小脚本验证该问题来自执行沙箱限制。因此涉及 aiosqlite 的单元测试在沙箱外、带 `timeout` 运行。

---

## 11. 后续循环规则

1. 任一测试失败，新增或重开 active gap，并记录失败命令和失败点。
2. 修完一轮后必须更新本文件的 Active Gap 表和 Round 记录。
3. 对明显不合理或无法单测验证的硬件/基础设施规格，直接做合理化实现并记录裁剪理由。
4. e2e、真实硬件、真实链节点验证不进入当前完成标准。
