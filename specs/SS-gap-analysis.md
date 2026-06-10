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

Round 5 从安全密态沙箱产品闭环重新复核后，任务输出、开发沙箱输出、契约网关输出、DP 预算失败模式、配额 fallback、任务代码落库等软件缺口已经修复。Round 6 从产品化和易用性复核后，前端构建、角色体系、路由权限、开发沙箱、输出审查、合约创建、分页契约和主布局体验缺口已经修复。Round 7 继续补齐数据产品创建、沙箱会话创建、数据资源详情和基础品牌化缺口。Round 8 补齐产品生命周期、目录到沙箱、上下文预填、角色工作台、产品搜索/状态筛选和运营/监管产品可见性缺口。Round 9 修复普通沙箱直接执行的输出审查绕过、会话详情不可执行/不可审查、运营终止配额释放对象错误，以及认证页模板化问题。Round 10 补齐沙箱网络策略控制面，修复网络策略 API 热加载未 await、session 路由被遮挡和策略写入权限校验不足。Round 11 补齐字段级最小化控制面、买方申请体验、restricted 字段复核语义、契约网关查询执行面字段授权，以及网关凭证合约路径一致性校验。Round 12 收紧跨空间连接器代理沙箱，修复合约约束失败开放、connector 与 contract 绑定缺失、代理执行绕过代码扫描/输出审查，以及代理会话缺少 key/network/quota 安全基线。Round 13 继续收紧本地沙箱合约绑定、数据产品生命周期与归档、合约创建/激活授权、SecureDuckDB 和输出控制 DP budget 的 session owner 授权。Round 14 修复数据资源上传/删除闭环、输出控制 inspect/gateway session owner 授权、产品版本与字段策略可见性，以及字段申请角色语义。Round 15 修复运行时路由失败关闭、L2/K8s session context 透传、K8s manifest/NetworkPolicy 控制面和 Python 3.12 运行期兼容性。Round 16 继续收紧 K8s/K3s 产品化部署面，修复只读 rootfs 可写路径、零信任 egress、ResourceQuota 语义、exec 密钥泄露、env 校验和 243/K3s 中国网络安装脚本问题。Round 17 补齐 K8s 作为可创建 runtime level 的产品入口，并修复 Pod readiness、控制面 apply 失败回滚、exec 前 ready gate、terminate returncode 和 CDS/K8s 状态映射问题。Round 18 收紧 connector 与 contract fulfillment 两条非普通会话入口，修复 provision/key distribution 失败后继续激活会话，以及合约产品 ID 字符串未规范化的问题。Round 19 收紧 K8s 多集群与 allowlist 语义，修复 kubeconfig 未透传、域名 allowlist 失败开放和 allowlist 输入校验不足问题。Round 20 清理剩余软件薄实现：补齐 FederatedRuntime、Firecracker 非网络串口 fallback、DP budget 自动告警评估，以及非结构化处理可选依赖失败显式化。Round 21 修复 L1 TEE 运行时语义：无 TEE 环境时明确降级为普通软件密态沙箱并生成 `software_hash` 证明；有硬件信号且配置 runner/attester 时进入硬件路径。Round 22 补齐告警中心后端产品化闭环：告警持久化、去重、通知投递记录、确认/解决处置状态和监控 API。Round 23 补齐非结构化/多媒体管线的软件产品化闭环：任务重试、阶段状态、DICOM 入口、加密 artifact manifest、输出审查，以及 L3 bwrap seccomp 兼容重试。Round 24 修复训练数据切分不可复现和 RAG `chunking` 阶段名拼写错误，保障训练审计、事故回放和阶段状态对齐。Round 25 补齐 CDC Kafka Connect 控制面 hook，配置真实 Connect URL 时可通过 REST upsert/pause/delete connector。Round 26 修复前端服务层与后端 API 的契约脱节，补齐连接器、联邦、训练的缺失路由和统一返回体，并收敛页面误导性操作。Round 27 收敛产品化命名，把测试数据正式接口从 mock 迁移到 synthetic，并把 GPU-TEE 默认运行时从 stub 命名改为 local/software 语义。Round 28 补齐发版安全复核和 Go/No-Go 门禁文档，明确试点/生产边界、残余风险、证据要求和发布后观察项。Round 29 补齐安全态势接口和首页披露，防止软件 fallback 被误解为硬件能力。Round 30 补齐沙箱会话证明包和输出审查摘要审计，支持客户/监管验收归档。Round 31 补齐高危管理员操作理由必填与审计字段，防止 API 直调绕过前端二次确认。硬件或外部基础设施能力（真实 SGX、GPU-TEE、生产 FISCO BCOS 节点、PG-in-TEE、真实 K3s 集群）保持为适配器/模拟器/可部署验证项，见 2.1 后续待实现/验证 Gap 表，不列为本轮 active gap。

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
| G-046 | 产品详情页只展示少量字段，缺少生命周期和安全策略操作面 | P1 | 已修复，已构建通过 | Round 8 | `cds-frontend/src/pages/DataProducts/ProductDetail.tsx`, `cds-frontend/src/services/dataProductApi.ts`, `cds-frontend/src/types/enums.ts`, `cds-frontend/src/types/models.ts` | 产品详情改为资产页，展示资源绑定、Schema、输出约束、版本记录，并接入提交审核、审批、发布和创建合约入口。 |
| G-047 | 目录详情只用列表摘要，无法看到输出约束/Schema，也不能直接启动沙箱 | P1 | 已修复，已构建通过 | Round 8 | `cds-frontend/src/pages/Catalog/index.tsx`, `cds-frontend/src/pages/Sandbox/SessionList.tsx` | 目录详情拉取完整产品信息；买方可从目录卡片或详情弹窗带产品上下文创建沙箱。 |
| G-048 | 合约创建和沙箱创建不能从上游页面预填产品上下文 | P1 | 已修复，已构建通过 | Round 8 | `cds-frontend/src/pages/Contracts/ContractCreate.tsx`, `cds-frontend/src/pages/Sandbox/SessionList.tsx` | 支持 `product_id` 查询参数预填合约产品和沙箱产品，减少复制裸 ID。 |
| G-049 | 仪表盘只有指标，没有按角色引导下一步操作 | P2 | 已修复，已构建通过 | Round 8 | `cds-frontend/src/pages/Dashboard/index.tsx` | 增加数商、买方、运营/管理员、监管方的角色快捷动作入口。 |
| G-050 | 产品列表搜索框无效，缺少状态筛选和审核直达能力 | P1 | 已修复，已测试通过 | Round 8 | `app/api/data_products.py`, `cds-frontend/src/services/dataProductApi.ts`, `cds-frontend/src/pages/DataProducts/ProductList.tsx` | 后端产品列表支持 `q` 搜索；前端搜索实际生效，新增状态筛选并支持 `/data-products?status=reviewing`。 |
| G-051 | 运营/监管/管理员有产品入口，但后端产品详情仍按非 owner 隐藏未发布产品 | P1 | 已修复，已测试通过 | Round 8 | `app/api/data_products.py` | 数据产品列表和详情按角色收口：数商看自有，买方看已发布，operator/regulator/admin 可查看全量用于审核和监管。 |
| G-052 | 普通沙箱会话 `/execute` 可绕过统一输出审查直接返回 raw output | P0 | 已修复，已单测通过 | Round 9 | `app/api/sandbox_sessions.py`, `app/schemas/sandbox_session.py`, `tests/test_sandbox_sessions.py` | 直接执行输出统一进入 OutputInspector；非 critical 发现脱敏释放，critical 发现阻断；响应包含 security_report、水印和签名。 |
| G-053 | 沙箱会话详情只展示少量字段，无法执行任务或查看输出审查结果 | P1 | 已修复，已构建通过 | Round 9 | `cds-frontend/src/pages/Sandbox/SessionDetail.tsx`, `cds-frontend/src/services/sandboxApi.ts`, `cds-frontend/src/types/models.ts` | 会话详情升级为工作台，展示产品/合约/密钥/资源限制，owner 可执行代码并查看输出、安全发现、水印和签名。 |
| G-054 | 运营/管理员终止他人会话时释放操作者配额，审计 previous_status 不真实 | P1 | 已修复，已单测通过 | Round 9 | `app/api/sandbox_sessions.py` | 终止会话按 session owner 释放 tenant usage，并在变更前记录 previous_status。 |
| G-055 | 登录/注册页仍是默认模板卡片，缺少产品化认证入口 | P2 | 已修复，已构建通过 | Round 9 | `cds-frontend/src/pages/Auth/Login.tsx`, `cds-frontend/src/pages/Auth/Register.tsx`, `cds-frontend/src/index.css` | 认证页改为 CDS 产品化入口，补齐响应式布局、角色注册上下文和安全状态视觉。 |
| G-056 | 沙箱默认 deny_all 网络策略没有会话级查看/修改控制面 | P1 | 已修复，已构建通过 | Round 10 | `app/api/sandbox_sessions.py`, `cds-frontend/src/pages/Sandbox/SessionDetail.tsx`, `cds-frontend/src/services/sandboxApi.ts` | 会话详情新增网络策略卡片；支持查看默认策略，owner/operator/admin 可在 running/ready 状态用 JSON body 更新 deny_all/allowlist、CIDR、域名、端口、DNS 和速率限制。 |
| G-057 | 独立 `network-policies` API 热加载没有 await，`/session/{id}` 被 `/{policy_id}` 遮挡 | P1 | 已修复，已单测通过 | Round 10 | `app/api/network_policy.py`, `tests/test_sandbox_sessions.py` | 修复 create/update/delete 的异步策略引擎调用；将 session 路由前置，确保 `/api/v1/network-policies/session/{session_id}` 可达。 |
| G-058 | 网络策略写入缺少 session 权限校验，CIDR/端口校验不完整 | P1 | 已修复，已单测通过 | Round 10 | `app/api/network_policy.py`, `app/schemas/network_policy.py`, `tests/test_sandbox_sessions.py` | 创建/修改/删除策略按 session owner/operator/admin 授权，监管只读；CIDR 和端口在 create/update 中统一校验，list 默认值改为 `Field(default_factory=...)`。 |
| G-059 | 字段暴露策略已有后端 API，但产品详情没有规则配置和审批工作台 | P1 | 已修复，已构建通过 | Round 11 | `cds-frontend/src/pages/DataProducts/ProductDetail.tsx`, `cds-frontend/src/services/fieldExposureApi.ts` | 数商/管理员可在产品详情维护默认敏感度、字段敏感度、mask pattern、auto approve，并查看/审批字段申请。 |
| G-060 | restricted 字段可被 auto_approve 或数商审批，复核语义过宽 | P0 | 已修复，已单测通过 | Round 11 | `app/api/field_exposure.py`, `tests/test_field_exposure.py` | restricted 字段不再自动批准；数商审批 restricted 返回 403，只有平台 admin/operator 可审批；管理员可查看 provider-scope 待审申请。 |
| G-061 | 买方目录没有字段申请入口，也看不到已获批字段 | P1 | 已修复，已构建通过 | Round 11 | `cds-frontend/src/pages/Catalog/index.tsx`, `cds-frontend/src/services/fieldExposureApi.ts` | 目录详情展示已批准字段；买方可基于产品 schema 选择字段、填写用途并提交字段暴露申请。 |
| G-062 | 字段暴露只停留在申请/UI，契约网关查询未按批准字段失败关闭 | P0 | 已修复，已单测通过 | Round 11 | `app/services/gateway_service.py`, `tests/test_field_exposure.py` | 网关查询执行前按产品字段可见性、买方已批准字段和 public/internal 默认放行规则检查；配置字段可见性后拒绝 `SELECT *` 和未批准字段引用。 |
| G-063 | 网关 URL `contract_id` 与 app credential 绑定合约未校验，审计/调用语义可能错位 | P1 | 已修复，已单测通过 | Round 11 | `app/api/gateway.py`, `tests/test_contract_gateway.py` | query/access/metering/status 在认证后校验路径合约必须等于 credential 合约，不一致直接 403。 |
| G-064 | `ContractFulfillment.check_contract_constraints` 在绑定合约缺失时失败开放，且字符串 contract_id 查询 UUID 会异常 | P0 | 已修复，已单测通过 | Round 12 | `app/services/contract_fulfillment.py`, `tests/test_contract_fulfillment.py` | session 绑定合约缺失/非法 ID/非 active/signed/产品不匹配均失败关闭；`SandboxSession.contract_id` 统一先解析 UUID 再查 `Contract.id`。 |
| G-065 | 跨空间 connector 只要知道 active contract id 即可创建代理会话，缺少 contract 与 connector/space 绑定校验 | P0 | 已修复，已单测通过 | Round 12 | `app/api/connectors.py`, `tests/test_connectors.py` | 合约 terms 若声明 `connector_id`、`space_id`、`remote_space_id` 或嵌套 connector/federation 绑定，创建/执行代理会话时必须匹配当前 connector。 |
| G-066 | connector 代理执行直接返回 runtime 输出，绕过代码扫描和统一输出审查 | P0 | 已修复，已单测通过 | Round 12 | `app/api/connectors.py`, `tests/test_connectors.py` | `/connectors/remote/sessions/{id}/execute` 接入 CodeScanner 和 output_security，阻断危险代码，非 critical 输出脱敏，critical 输出阻断并返回 security_report。 |
| G-067 | connector 创建的底层 sandbox session 缺少 session key、KeyMetadata、默认 deny_all 网络策略和合约配额基线 | P1 | 已修复，已单测通过 | Round 12 | `app/api/connectors.py`, `tests/test_connectors.py` | 代理会话创建时写入 KMS session key、KeyMetadata、默认 deny_all NetworkPolicy，并按合约设置 rows/bytes/api/gpu_seconds quota limits。 |
| G-068 | 本地沙箱 `contract_id` 只作为标签写入，会话创建未校验合约存在、买方、产品、级别和时长 | P0 | 已修复，已单测通过 | Round 13 | `app/api/sandbox_sessions.py`, `tests/test_sandbox_sessions.py` | 传入 contract_id 时必须能解析、存在、active/signed、当前用户为买方或运营、产品被合约覆盖、级别/模式/操作/时长满足合约。 |
| G-069 | `sandbox_db._attach_policy` 用字符串 contract_id 查询 UUID，导致合约策略自动附加失败 | P1 | 已修复，已单测通过 | Round 13 | `app/api/sandbox_db.py`, `tests/test_secure_duckdb.py` | 先解析 `SandboxSession.contract_id` 为 UUID，再读取 Contract；非法 ID 失败关闭为跳过策略附加。 |
| G-070 | 通用数据产品 `PATCH/PUT` 可直接改 `status=published`，绕过提交、审批、发布和资源绑定 | P0 | 已修复，已单测通过 | Round 13 | `app/api/data_products.py`, `tests/test_data_products.py`, `tests/test_catalog.py`, `tests/test_field_exposure.py`, `tests/test_sandbox_sessions.py` | 通用更新拒绝 lifecycle 字段；测试造数改为 DB 夹具或真实生命周期；前端 update 类型移除 status。 |
| G-071 | 合约创建/激活缺少参与方、买方角色、产品归属和已发布校验，且 negotiating 可直接 activate | P0 | 已修复，已单测通过 | Round 13 | `app/api/contracts.py`, `app/services/contract_service.py`, `tests/test_contracts.py` | 只有数据产品所属数商可创建合约；买方必须是 buyer；激活必须由合约参与方执行且状态为 signed/active；转 active 前所有产品必须已发布且属于 provider。 |
| G-072 | 数据产品删除会物理删除已发布或被合约/会话引用的资产，造成目录、合约和沙箱悬空 | P0 | 已修复，已单测通过 | Round 13 | `app/api/data_products.py`, `cds-frontend/src/pages/DataProducts/ProductList.tsx`, `tests/test_data_products.py` | 物理删除仅允许无引用 draft；非 draft 走 archive；有 active contract 或 active session 时归档失败关闭，前端非草稿操作改为归档。 |
| G-073 | `sandbox-db` DuckDB 引擎按裸 session_id 访问，缺少 owner/session 授权 | P0 | 已修复，已单测通过 | Round 13 | `app/api/sandbox_db.py`, `tests/test_secure_duckdb.py` | API 创建 ad-hoc 引擎记录 owner；真实 SandboxSession 校验 session owner；operator/admin 可管理；非 owner 查询/关闭/读密钥配置返回 403。 |
| G-074 | 输出控制 DP budget 允许任意用户按裸 session_id 初始化、覆盖和查询预算 | P1 | 已修复，已单测通过 | Round 13 | `app/api/output_control.py`, `tests/test_output_control_api.py` | DP budget 记录 owner；真实 SandboxSession 校验 session owner；operator/admin 可管理；非 owner 查询或重初始化返回 403。 |
| G-075 | 数据资源上传写 `chunk_refs` 读取不存在的 `sm3_hash`，导致上传接口 500 | P0 | 已修复，已单测通过 | Round 14 | `app/api/data_resources.py`, `tests/test_data_resources_api.py` | `StorageService.upload()` 返回 `checksum`；资源 API 兼容 `sm3_hash`/`checksum`，上传后可正常生成 chunk provenance。 |
| G-076 | 数据资源删除缺少产品引用保护，也未删除密文对象和本地 DEK | P0 | 已修复，已单测通过 | Round 14 | `app/api/data_resources.py`, `tests/test_data_resources_api.py` | 被任意 DataProduct 引用的资源返回 409；未引用资源删除时清理 storage 对象、销毁 KMS key 并写审计。 |
| G-077 | 输出控制 `/inspect` 和 `/gateway` 可使用任意真实或 ad-hoc session_id 写审计/释放输出 | P0 | 已修复，已单测通过 | Round 14 | `app/api/output_control.py`, `tests/test_output_control_api.py` | 真实 SandboxSession 按 owner 校验；ad-hoc output session 首次使用绑定 owner；operator/admin 可管理。 |
| G-078 | 数据产品 `/versions` 未复用产品可见性，买方可枚举未发布产品或草稿后继版本 | P1 | 已修复，已单测通过 | Round 14 | `app/api/data_products.py`, `tests/test_data_products.py` | 入口产品不可见返回 404；版本列表只返回当前用户可见的版本，运营/监管/管理员保留全量可见。 |
| G-079 | 字段可见性/approved-fields 可泄露未发布产品策略，字段申请未限制买方角色，管理员配置语义不一致 | P1 | 已修复，已单测通过 | Round 14 | `app/api/field_exposure.py`, `tests/test_field_exposure.py` | 字段策略读取先校验产品可见性；仅 buyer 可发起字段申请；provider 或 admin/operator 可维护字段规则，监管只读。 |
| G-080 | FastAPI `Query(regex=...)` 已弃用，测试持续产生产品化警告 | P2 | 已修复，已单测通过 | Round 14 | `app/api/data_products.py`, `app/api/data_resources.py` | 采样接口参数校验改为 `pattern`，消除 FastAPI deprecation warning。 |
| G-081 | 默认 JWT sentinel key 少于 32 字节，测试/开发签发 HS256 token 时持续触发弱密钥告警 | P1 | 已修复，已单测通过 | Round 14 | `app/services/auth_service.py`, `tests/test_security_hardening.py` | 生产默认 sentinel 继续失败关闭；TESTING/DEBUG 使用明确的 32+ 字节本地签名 key，避免用短 key 签发 token。 |
| G-082 | 联邦连接器 JWT fallback 默认/测试密钥少于 32 字节，弱化跨空间身份 fallback 示例 | P1 | 已修复，已单测通过 | Round 14 | `app/services/federation_connector.py`, `tests/test_federation_connector.py` | JWTIdentityProvider 默认复用应用 JWT secret；未配置时生产失败关闭，TESTING/DEBUG 使用 32+ 字节本地 key；测试示例改用长 key。 |
| G-083 | 部分安全单测把同步 `db.add()` mock 成 AsyncMock，CI 产生未 await 协程告警 | P2 | 已修复，已单测通过 | Round 14 | `tests/test_p0_8_9_10.py`, `tests/test_p0_security_gaps.py` | 测试 DB double 改为同步 `add=MagicMock()`、异步 `flush/refresh=AsyncMock()`，保持 AsyncSession 接口形态。 |
| G-084 | `SandboxRuntime` 对未知 container_id 前缀回退 Docker，可能误操作非沙箱容器 | P0 | 已修复，已单测通过 | Round 15 | `app/services/sandbox_runtime.py`, `tests/test_sandbox_runtime.py` | 未知前缀 execute/terminate/status 全部失败关闭，不再隐式调用 Docker adapter。 |
| G-085 | L2 Firecracker 和 K8s 执行路径未完整透传 session key、env context 和 timeout | P1 | 已修复，已单测通过 | Round 15 | `app/services/sandbox_runtime.py`, `app/services/firecracker_runtime.py`, `tests/test_sandbox_runtime.py` | L2/K8s 透传 `CDS_SESSION_KEY`、审计/合约/模式等 env 和执行 timeout，保持与 L0/L1/L3 一致。 |
| G-086 | K8s Runtime pod 名截断不一致，execute/status/terminate 会找不到 provision 创建的 pod | P1 | 已修复，已单测通过 | Round 15 | `app/services/sandbox_runtime.py`, `tests/test_sandbox_runtime.py` | 统一 `sandbox-{session_id[:16]}` pod name 派生规则。 |
| G-087 | K8s `_kubectl()` 不接收 stdin input，NetworkPolicy selector 用截断 session id 匹配不到 pod | P1 | 已修复，已单测通过 | Round 15 | `app/services/k8s_sandbox.py`, `tests/test_sandbox_runtime.py` | `kubectl apply -f -` 可正确接收 manifest；NetworkPolicy selector 使用完整 session id，与 pod label 一致。 |
| G-088 | `str | "SandboxMode"` 类型注解在 Python 3.12 运行期导入失败 | P0 | 已修复，已编译通过 | Round 15 | `app/services/sandbox_runtime.py` | 改为 `str | SandboxMode`，243 的 Python 3.12 环境已通过编译和目标测试导入。 |
| G-089 | K8s pod 启用只读 rootfs 但缺少完整 writable workspace/tmp/home 和运行时可写环境 | P1 | 已修复，已单测通过 | Round 16 | `app/services/k8s_sandbox.py`, `app/services/sandbox_runtime.py`, `tests/test_sandbox_runtime.py` | Pod manifest 挂载 `/workspace`、`/tmp`、`/home/sandbox` tmpfs，设置 `HOME/TMPDIR/PYTHONPYCACHEPREFIX`，exec 前创建 `/tmp/pycache`，避免真实执行写只读 root。 |
| G-090 | K8s `deny_all` NetworkPolicy 仍允许 DNS egress，不符合默认零信任 | P1 | 已修复，已单测通过 | Round 16 | `app/services/k8s_sandbox.py`, `tests/test_sandbox_runtime.py` | deny_all 策略 egress 改为空列表，默认不放行 DNS；需要外联时必须切换 allowlist。 |
| G-091 | K8s ResourceQuota 使用 arbitrary label `scopeSelector`，真实集群 apply/语义不可靠 | P1 | 已修复，已单测通过 | Round 16 | `app/services/k8s_sandbox.py`, `tests/test_sandbox_runtime.py` | 改为 namespace-level `quota-cds-sandbox`，移除非法 scopeSelector，补齐 ephemeral-storage hard limits；租户级 quota 由控制面执行。 |
| G-092 | K8s exec 把 session key/env 放入 kubectl argv，容易被进程列表或审计日志泄露 | P0 | 已修复，已单测通过 | Round 16 | `app/services/sandbox_runtime.py`, `tests/test_sandbox_runtime.py` | `kubectl exec -i ... sh -s` 只在 argv 暴露固定命令；session key/env 和 base64 用户代码经 stdin 脚本注入。 |
| G-093 | K8s provision 未校验 env 名称，非法 env 会导致 manifest apply 失败且错误不清晰 | P1 | 已修复，已单测通过 | Round 16 | `app/services/k8s_sandbox.py`, `tests/test_sandbox_runtime.py` | provision 阶段使用统一 env 名称校验，非法名称失败关闭且不触发 `kubectl apply`。 |
| G-094 | K3s 安装脚本实际偏 k3d 且路径写死，不适配 243 root、native K3s 和中国镜像环境 | P1 | 已修复，已脚本化 | Round 16 | `scripts/install-k3s.sh`, `k8s/registries.yaml` | 脚本支持 `CDS_K3S_MODE=k3s/k3d`，默认 native K3s；内置 `INSTALL_K3S_MIRROR=cn`、containerd registry mirrors、可配置镜像预拉和 root 环境路径。 |
| G-095 | K8s RuntimeAdapter 已存在但 API/schema/frontend 不允许创建 K8s 沙箱 | P1 | 已修复，已构建/单测通过 | Round 17 | `app/models/sandbox_session.py`, `app/schemas/sandbox_session.py`, `app/schemas/contract.py`, `app/services/sandbox_manager.py`, `cds-frontend/src/pages/Sandbox/SessionList.tsx`, `cds-frontend/src/pages/Contracts/ContractCreate.tsx`, `tests/test_sandbox_levels.py`, `tests/test_contracts.py` | 新增 `SandboxLevel.K8S`、API/合约 schema、资源限额和前端选项，用户可通过产品入口选择 K8s/K3s runtime。 |
| G-096 | K8s provision 创建 Pod 后不等待 Ready，可能把 Pending/ImagePullBackOff 当成 running | P1 | 已修复，已单测通过 | Round 17 | `app/core/config.py`, `app/services/k8s_sandbox.py`, `tests/test_sandbox_runtime.py` | 新增 ready timeout/poll 配置；provision 必须等待 Pod Running 且 container ready，否则失败关闭并回滚。 |
| G-097 | K8s NetworkPolicy 或 ResourceQuota apply 失败会被忽略，可能留下无策略沙箱 Pod | P0 | 已修复，已单测通过 | Round 17 | `app/services/k8s_sandbox.py`, `tests/test_sandbox_runtime.py` | NetworkPolicy/ResourceQuota apply 返回值纳入失败判断；任一控制面资源失败即删除 Pod/Policy 并返回 failed。 |
| G-098 | K8s runtime execute 未检查 Pod ready，可能直接 exec 到未就绪 Pod | P1 | 已修复，已单测通过 | Round 17 | `app/services/sandbox_runtime.py`, `tests/test_sandbox_runtime.py` | exec 前读取 Pod `cds_status/ready`，非 running/ready 直接返回结构化失败，不触发 `kubectl exec`。 |
| G-099 | K8s terminate 忽略 `kubectl delete` returncode，删除失败也返回成功 | P1 | 已修复，已单测通过 | Round 17 | `app/services/k8s_sandbox.py`, `tests/test_sandbox_runtime.py` | pod 与 NetworkPolicy 删除结果逐一检查；任一 delete 非 0 直接返回 false 并记录错误。 |
| G-100 | K8s `get_status()` 返回原始 Kubernetes phase，未映射到 CDS session lifecycle | P1 | 已修复，已单测通过 | Round 17 | `app/services/k8s_sandbox.py`, `app/services/sandbox_runtime.py`, `tests/test_sandbox_runtime.py` | K8s status 统一输出 `cds_status`：ready Running→`running`，Pending/未 ready→`provisioning`，Failed/Unknown→`failed`，Succeeded→`completed`。 |
| G-101 | connector 代理会话在 runtime provision 失败后仍继续生成 key/NetworkPolicy/active connector session | P0 | 已修复，已单测通过 | Round 18 | `app/api/connectors.py`, `tests/test_connectors.py` | provision error、无 container 或 failed status 时立即返回 503，底层 SandboxSession 标记 failed，不创建 KeyMetadata、NetworkPolicy 或 ConnectorSession。 |
| G-102 | connector 代理会话忽略 session key distribution 失败，可能暴露无可用密钥的 active 会话 | P0 | 已修复，已单测通过 | Round 18 | `app/api/connectors.py`, `tests/test_connectors.py` | key distribution 返回空时销毁 key、终止 runtime、标记 session failed，并拒绝创建 active connector session。 |
| G-103 | contract fulfillment 使用字符串 product_id 查询 UUID 列，自动履约在 SQLite/PG 类型语义下失败 | P1 | 已修复，已单测通过 | Round 18 | `app/services/contract_fulfillment.py`, `tests/test_contract_fulfillment.py` | 自动履约先将 contract.product_ids 规范化为 UUID，再查询 DataProduct 和写入 SandboxSession。 |
| G-104 | contract fulfillment 在 provision/key distribution 失败后仍继续生成 key metadata 或把失败 session 计入履约结果 | P0 | 已修复，已单测通过 | Round 18 | `app/services/contract_fulfillment.py`, `tests/test_contract_fulfillment.py` | provision 失败立即记录 failed session 并抛出，由 fulfill 汇总为 partial；key distribution 失败销毁 key、终止 runtime、无 KeyMetadata。 |
| G-105 | K8s adapter 接收 kubeconfig 但控制面和 exec 路径未统一透传，真实多集群部署会打到默认集群 | P1 | 已修复，已单测通过 | Round 19 | `app/core/config.py`, `app/services/k8s_sandbox.py`, `app/services/sandbox_runtime.py`, `tests/test_sandbox_runtime.py` | 新增 `SANDBOX_K8S_KUBECONFIG`；adapter `_kubectl()` 统一追加 `--kubeconfig`，runtime exec 改用 adapter 方法而非模块级 kubectl。 |
| G-106 | K8s allowlist 接收 allowed_domains 但原生 NetworkPolicy 不支持 FQDN，实际会失败开放或语义不生效 | P0 | 已修复，已单测通过 | Round 19 | `app/core/config.py`, `app/services/k8s_sandbox.py`, `tests/test_sandbox_runtime.py` | 默认域名 allowlist 失败关闭；仅 `SANDBOX_K8S_FQDN_POLICY_PROVIDER=cilium` 时生成 CiliumNetworkPolicy `toFQDNs`。 |
| G-107 | K8s allowlist 的 CIDR/domain 缺少 adapter 级校验，错误会推迟到集群 apply 或形成不可预测策略 | P1 | 已修复，已单测通过 | Round 19 | `app/services/k8s_sandbox.py`, `tests/test_sandbox_runtime.py` | provision 前校验 CIDR 和域名模式；空 allowlist、非法 CIDR、非法域名均失败关闭且不触发 apply。 |
| G-108 | `joint_federated` 是合法模式但 `SceneRuntimeFactory` 未注册真实 FederatedRuntime，实际退回 passthrough | P1 | 已修复，已单测通过 | Round 20 | `app/services/sandbox_runtime.py`, `tests/test_scene_runtime.py`, `specs/ss-04-sandbox-runtime.md` | 新增 `FederatedRuntime`，提供联邦模式 SQL/Python guard、`federation_request` 代理执行和输出截断，并注册到场景运行时工厂。 |
| G-109 | Firecracker 非网络串口执行路径返回 `Serial execution not fully implemented` 占位 | P1 | 已修复，已单测通过 | Round 20 | `app/services/firecracker_runtime.py`, `tests/test_firecracker_runtime.py` | 串口/guest-agent 不可用时复用 QEMU TCG 的 hardened bwrap fallback，并透传 session key、env 和 timeout。 |
| G-110 | `AlertRuleEngine.evaluate()` 中 DP budget exhaustion 分支为空，只有手工 `check_dp_budget()` 能触发 | P2 | 已修复，已单测通过 | Round 20 | `app/services/alert_engine.py`, `tests/test_alert_engine.py` | 新增 DP budget snapshot 记录，`evaluate()` 自动产生预算耗尽告警，保留直接检查接口。 |
| G-111 | 非结构化数据处理的 Pillow/pdftotext/ffprobe 异常被静默吞掉，调用方无法区分完整成功和元数据降级 | P2 | 已修复，已单测通过 | Round 20 | `app/services/data_processing.py`, `tests/test_data_processing.py` | 可选提取失败时返回 `partial_success` 与 `extraction_errors`，基础元数据仍可用。 |
| G-112 | Firecracker/QEMU fallback 只识别 `EINVAL`，243 上 bwrap 返回 `PR_SET_SECCOMP: Invalid argument` 时不会禁用 seccomp 重试 | P1 | 已修复，已单测通过 | Round 20 | `app/services/firecracker_runtime.py`, `tests/test_firecracker_runtime.py` | 新增 seccomp retry helper，兼容 `EINVAL`、`Invalid argument` 和 `PR_SET_SECCOMP` stderr 形态。 |
| G-113 | L1 TEE 无硬件时仍走 SGX 形状模拟 quote，且缺少硬件能力探测和硬件 runner 对接路径 | P0 | 已修复，已单测通过 | Round 21 | `app/core/config.py`, `app/services/tee_capability.py`, `app/services/sandbox_runtime.py`, `tests/test_tee_capability.py`, `tests/test_sandbox_runtime.py` | 新增 `TEE_MODE`、软件降级开关和硬件 provision/exec/attest/terminate 命令 hook；无硬件时返回 `software_confidential` 并生成 `software_hash` attestation；有 SGX/TDX/SEV-SNP/iTrustee 信号且配置 runner 时走硬件路径。 |
| G-114 | 告警中心只有内存规则和 audit log 反推列表，缺少告警持久化、去重、通知记录和处置闭环 | P1 | 已修复，已单测通过 | Round 22 | `app/models/alert.py`, `app/services/alert_center.py`, `app/api/monitoring.py`, `tests/test_alert_center.py`, `tests/test_alert_engine.py` | 新增 `AlertRecord`、`AlertCenterService`、webhook 投递结果、去重 occurrence 计数、acknowledge/resolve API；`/monitoring/alerts` 优先读取持久化告警，并保留 audit fallback。 |
| G-115 | 非结构化 pipeline 只返回明文工作区结果，缺少重试、加密产物清单、输出审查和 DICOM 入口 | P1 | 已修复，已单测通过 | Round 23 | `app/services/unstructured_pipeline.py`, `app/api/data_pipeline.py`, `app/models/pipeline_task.py`, `tests/test_data_pipeline.py` | `PipelineTask` 增加 options/max_retries/retry_count/attempts/stage_status/artifacts；输出文件统一 envelope 加密上传到 `StorageService`，返回 artifact manifest；文本 artifact 经 `OutputInspector` 审查，critical DLP 阻断释放；API 支持 `max_retries` 与 `dicom`。 |
| G-116 | L3 `BwrapAdapter` seccomp fallback 只识别 `EINVAL`，243 上 `PR_SET_SECCOMP: Invalid argument` 会导致 API 路径失败 | P1 | 已修复，已单测通过 | Round 23 | `app/services/sandbox_runtime.py`, `tests/test_sandbox_runtime.py`, `tests/test_data_pipeline.py` | 新增 Bwrap seccomp retry helper，兼容 `EINVAL`、`Invalid argument` 和 `PR_SET_SECCOMP` stderr；非结构化 API 在 243 上通过真实 bwrap 聚焦测试。 |
| G-117 | 训练 pipeline 的 train/validation split 使用进程随机数，审计回放和训练复现实验会得到不同数据边界 | P2 | 已修复，已单测通过 | Round 24 | `app/services/training_pipeline.py`, `tests/test_training_pipeline.py` | `split_dataset()` 改为按规范化 record JSON + seed 的 SHA-256 稳定排序；默认 seed 可复现，显式 seed 可生成另一个稳定切分。 |
| G-118 | RAG 训练 pipeline 的 `PipelineStage.CHUNKING` 枚举值误写为 `chunkding`，阶段状态和前后端契约不一致 | P2 | 已修复，已单测通过 | Round 24 | `app/services/training_pipeline.py`, `tests/test_training_pipeline.py` | 枚举值修正为 `chunking`，RAG stages 单测断言阶段名与规格一致。 |
| G-119 | CDC connector lifecycle 只改内存状态，未对接 Kafka Connect REST 控制面 | P2 | 已修复，已单测通过 | Round 25 | `app/core/config.py`, `app/services/cdc_agent.py`, `tests/test_cdc_agent.py` | 新增 `CDC_KAFKA_CONNECT_URL` 和 timeout；配置 URL 时 `start/pause/stop_connector` 分别调用 Kafka Connect `PUT /config`、`PUT /pause`、`DELETE /connectors/{name}`，失败标记 failed；无 URL 保持本地可测 fallback。 |
| G-120 | 前端服务层仍调用不存在或返回体不完整的后端接口，训练/连接器/联邦页面存在隐藏 404 和误导性交互 | P1 | 已修复，已编译/构建通过 | Round 26 | `app/api/connectors.py`, `app/api/federation.py`, `app/api/training.py`, `cds-frontend/src/services/*Api.ts`, `cds-frontend/src/pages/Connectors/index.tsx`, `cds-frontend/src/pages/Federation/CrossSpaceDashboard.tsx`, `cds-frontend/src/pages/Training/TrainingDashboard.tsx` | 补齐连接器详情/心跳、联邦 trust score/目录同步、训练审计/检查点接口；统一连接器和训练任务返回体；前端改为真实字段、详情抽屉、确认操作和行级 loading。 |
| G-121 | 产品化 API 和运行时默认配置仍暴露 `mock/stub` 主命名，容易被误判为未真实实现 | P2 | 已修复，已编译通过 | Round 27 | `app/api/data_products.py`, `app/api/data_resources.py`, `app/services/gpu_tee_runtime.py` | 新增 synthetic 测试数据正式接口；旧 mock 路由标记 deprecated 并保持兼容；GPU-TEE factory 默认改为 `local`，`software`/`stub` 作为兼容 alias。 |
| G-122 | 发版前缺少安全产品设计复核、残余风险和 Go/No-Go 门禁文档 | P0 | 已修复，文档已补齐 | Round 28 | `specs/product-security-release-review.md`, `specs/release-gate-checklist.md`, `specs/productization-deployment-runbook.md`, `specs/productization-gap-analysis.md` | 新增发版安全复核、发布门禁清单和 runbook 发版签署入口，明确试点/生产边界、P0/P1 证据、残余风险和发布后观察项。 |
| G-123 | 缺少面向运营/监管的安全态势披露，用户可能误解软件 fallback 为硬件 TEE/HSM/链/SIEM 已启用 | P0 | 已修复，已编译/构建通过 | Round 29 | `app/api/monitoring.py`, `cds-frontend/src/services/monitoringApi.ts`, `cds-frontend/src/pages/Dashboard/index.tsx` | 新增安全态势接口和首页卡片，按配置/TEE 探测返回 Go/Conditional Go/No-Go、能力状态、证据和建议动作，不暴露 Secret。 |
| G-124 | 沙箱会话缺少统一可下载证明包，policy hash、attestation、key id、输出签名和审计摘要分散不可交付 | P0 | 已修复，已编译/构建通过 | Round 30 | `app/api/sandbox_sessions.py`, `cds-frontend/src/services/sandboxApi.ts`, `cds-frontend/src/pages/Sandbox/SessionDetail.tsx`, `specs/productization-gap-analysis.md` | 新增会话证明包接口和前端抽屉/下载入口；执行后持久化 `sandbox.output_inspected` 审计摘要；证明包包含稳定 evidence hash 与本次 bundle hash，且不返回密钥明文、原始输出或 raw quote。 |
| G-125 | 管理员高危操作缺少后端强制理由和可检索审计字段，API 直接调用可绕过前端二次确认 | P0 | 已修复，已编译/构建通过 | Round 31 | `app/schemas/high_risk_operation.py`, `app/api/kms.py`, `app/api/certificates.py`, `app/api/contracts.py`, `app/api/connectors.py`, `cds-frontend/src/utils/highRiskOperation.tsx`, `cds-frontend/src/pages/Identity/*`, `cds-frontend/src/pages/Contracts/ContractDetail.tsx`, `cds-frontend/src/pages/Connectors/index.tsx` | KMS 密钥撤销、证书撤销、合约终止、连接器暂停/恢复/API Key 轮换强制提交 reason，可选 ticket_id；前端统一理由弹窗；审计 detail 记录 reason/ticket_id。 |

**当前 active software gap：0。**

### 2.1 后续待实现/验证 Gap 表

> 下表记录“当前代码闭环已可编译、可单测，但产品化/真实硬件/真实外部系统上线前仍必须完成”的 gap。它们不计入当前 active software gap；一旦进入对应交付阶段或具备真实环境，应转入 Active Gap 表并按轮次修复。

| ID | 后续 Gap | 优先级 | 当前状态 | 触发条件 | 验收标准 | 代码/规格证据 |
|---|---|---:|---|---|---|---|
| FG-001 | 真实 L1 TEE 硬件 e2e 与厂商证书链验证未完成 | P0 | 软件侧已部分完成：自动探测、无硬件 `software_confidential` 降级、硬件 runner/attester hook 已实现；真实硬件 e2e 待验证 | 有 SGX/Occlum/Gramine、TDX、SEV-SNP 或国产 TEE 节点 | 沙箱进程真实运行在 TEE；quote 使用厂商证书链校验；measurement/策略绑定；密钥只在 attestation 通过后释放；硬件集成测试通过 | `app/core/config.py`, `app/services/tee_capability.py`, `app/services/sandbox_runtime.py`, `app/services/remote_attestation.py`, `specs/product-spec.md`, `specs/ss-04-sandbox-runtime.md` |
| FG-002 | 真实 GPU-TEE/NVIDIA CC 训练运行时未接入 | P0 | 本地加密运行时和 CPU simulator 已实现 | 有 H100/H800 CC 模式或等价 GPU-TEE 环境 | GPU attestation 可验证；CPU-TEE 到 GPU-TEE 加密通道使用真实设备能力；训练 batch/梯度/checkpoint 不落明文；LLM/视觉训练硬件集成测试通过 | `app/services/gpu_tee_runtime.py`, `app/services/gpu_tee_simulator.py`, `app/services/llm_sft_runtime.py`, `app/services/vision_multimodal_runtime.py`, `specs/ss-07-ai-training-pipeline.md` |
| FG-003 | Firecracker 真实 guest-agent/virtio-fs 执行通道未完成 | P1 | Firecracker/QEMU 不可用或无 guest-agent 时使用 hardened bwrap fallback | 有可启动 kernel/rootfs/guest agent 的 L2 节点 | 非网络 VM 内可通过 guest-agent 或 virtio-fs 执行代码；文件传输、stdout/stderr、超时、env/session key 全在 guest 内完成；不依赖本地 fallback | `app/services/firecracker_runtime.py`, `app/services/sandbox_runtime.py`, `specs/sandbox-adapters-spec.md` |
| FG-004 | K3s/K8s 真实集群 e2e 尚未跑通并固化 | P0 | manifest、NetworkPolicy、quota、ready gate 已单测；安装脚本已提供 | 243 或专用节点安装 K3s/K8s 后 | 使用中国镜像源部署成功；Pod 创建/Ready/exec/terminate/status 全链路通过；Cilium FQDN allowlist、deny_all、ResourceQuota 在真实集群生效；形成 CI/e2e 脚本 | `scripts/install-k3s.sh`, `k8s/registries.yaml`, `app/services/k8s_sandbox.py`, `app/services/sandbox_runtime.py` |
| FG-005 | 生产 FISCO BCOS/AntChain 节点存证未接入 | P1 | 默认 PG append-only hash chain 可验证；链适配器保留 | 有真实联盟链节点、账号、证书和网络 | 存证 tx 真实上链；回执/区块高度/确认数可查；链不可用时按策略失败关闭或降级可审计；链上链下 hash 可双向校验 | `app/services/blockchain_adapter.py`, `app/services/blockchain_service.py`, `app/models/blockchain_anchor.py`, `specs/blockchain-integration-spec.md` |
| FG-006 | PG-in-TEE/生产密态数据库后端未落地 | P1 | SecureDuckDB、列加密、PG RLS 和应用级 fallback 已实现 | 需要承载生产结构化数据查询或多租户 SQL 服务 | PostgreSQL/DuckDB 实例运行在 TEE 或等价机密 VM 内；RLS/列加密/密钥释放/审计策略端到端验证；性能基线达标 | `app/services/secure_duckdb.py`, `app/services/pg_rls_manager.py`, `app/services/column_encryption.py`, `specs/ss-09-structured-db-storage.md` |
| FG-007 | 生产 HSM/Vault/国密套件未完成环境级接入 | P0 | 软件 HSM fallback、TLCP/证书/KMS 逻辑已实现 | 上生产或等保/国密合规环境 | KEK/签名密钥由 HSM/Vault 托管；SM2/SM3/SM4/TLCP 使用合规实现；Tongsuo/gmssl 依赖可安装；密钥轮换、吊销、审计通过演练 | `app/services/hsm_adapter.py`, `app/services/kms_service.py`, `app/services/tlcp_service.py`, `app/services/crypto_service.py`, `specs/ss-01-kms-identity.md` |
| FG-008 | 跨空间联邦真实互操作/e2e 未完成 | P1 | `FederationConnector`、mTLS、JWT/SM2 身份、FederatedRuntime 已实现 | 有第二个可信数据空间或互操作测试桩 | 双空间 trust 建立、证书校验、策略同步、目录同步、联邦请求、审计回写全链路通过；异常空间/低信任分数失败关闭 | `app/services/federation_connector.py`, `app/api/federation.py`, `app/services/catalog_sync.py`, `specs/ss-06-audit-ss-08-interconnect.md` |
| FG-009 | 告警中心外部通知通道、SIEM 对接和前端运营台 e2e 未完成 | P1 | 后端软件侧已部分完成：告警持久化、去重、Webhook 投递记录、确认/解决处置 API 已实现；真实邮件/短信/SIEM 与前端工作台 e2e 待验证 | 进入运维/监管产品化阶段 | 邮件/短信/Webhook/SIEM 推送在真实环境通过；告警抑制/升级策略可配置；前端告警工作台支持运营处置；处置状态和审计留痕端到端通过 | `app/models/alert.py`, `app/services/alert_center.py`, `app/services/alert_engine.py`, `app/api/monitoring.py`, `app/templates/alerts.html`, `specs/product-spec.md` |
| FG-010 | 非结构化/多媒体生产处理管线真实引擎镜像与生产数据 e2e 未完成 | P2 | 软件侧已完成：隔离 worker、失败重试、阶段状态、DICOM 入口、加密 artifact manifest、输出审查、partial_success；真实 PaddleOCR/ffmpeg/ASR/pydicom 镜像和生产数据验证待完成 | 要支持 PDF/OCR/DICOM/音视频生产数据上线 | PaddleOCR/PDF text/DICOM tag 清洗、视频抽帧/转码/语音转写依赖在生产镜像内可用；大文件/异常文件/并发任务 e2e 通过；输出 artifact 可从加密存储按授权释放 | `app/services/data_processing.py`, `app/services/unstructured_pipeline.py`, `app/services/vision_multimodal_runtime.py`, `app/api/data_pipeline.py`, `specs/tech-spec.md` |
| FG-011 | 模型训练生产编排未接入真实分布式训练平台 | P2 | 单机/确定性代理、可复现 dataset split、RAG stage 命名、checkpoint/watermark/MIA 闭环已实现 | 需要真实 LLM/视觉训练交付 | 支持真实 tokenizer/model/dataset；GPU/CPU 资源调度；OOM/中断恢复；checkpoint 加密持久化；训练审计、MIA、水印、指标全链路通过 | `app/services/llm_sft_runtime.py`, `app/services/cpu_trainer.py`, `app/services/training_pipeline.py`, `app/api/training.py`, `specs/ss-07-ai-training-pipeline.md` |
| FG-012 | 性能、容量和稳定性基线未完成 | P1 | 单元/聚焦测试通过；未跑压力和长稳 | 产品化验收前 | 并发沙箱数、启动耗时、查询延迟、输出审查吞吐、DP/审计写入吞吐、故障恢复时间有基线；至少 24h 长稳和故障注入通过 | `specs/product-spec.md`, `specs/deployment-spec.md`, `tests/test_e2e_full_lifecycle.py` |
| FG-013 | 生产部署、备份恢复和迁移演练未完成 | P1 | Docker/Helm/K8s 配置和 Alembic 基础存在 | 上生产或预生产环境 | Postgres/Redis/MinIO/对象存储 HA 配置；备份恢复演练通过；Alembic migration 在真实 PG 上演练；Secret/TLS 轮换流程可执行 | `helm/`, `k8s/`, `docker-compose.yml`, `alembic/`, `specs/deployment-spec.md` |
| FG-014 | 前端产品化 QA、可访问性和多角色工作流 e2e 未完成 | P2 | 主要页面、角色菜单和构建已修复 | 产品演示或试点验收前 | 五类角色端到端流程可用；空状态/错误态/加载态齐全；移动端/窄屏无错位；关键表单可恢复；基础 a11y 和中文文案统一 | `cds-frontend/src/`, `specs/frontend-spec.md`, `specs/frontend-implementation-guide.md` |
| FG-015 | 安全认证、渗透测试和供应链合规未完成 | P0 | 代码级安全闭环和单测已覆盖主要逻辑 | 上线前安全评审 | 威胁模型复核；SAST/依赖漏洞/SBOM/镜像扫描；K8s/CIS baseline；渗透测试和沙箱逃逸测试；修复项回归通过 | `app/services/sandbox_security.py`, `Dockerfile.api`, `requirements.txt`, `cds-frontend/package-lock.json`, `specs/data-security-spec.md` |
| FG-016 | 全量 e2e/CI 回归矩阵未固定 | P1 | 非 e2e 和聚焦测试通过；e2e 按当前任务暂未跑 | 进入持续交付阶段 | 243 或 CI 环境自动跑完整生命周期、K3s、联邦、链存证、训练、输出审查 e2e；失败自动生成 active gap；测试数据和依赖初始化脚本稳定 | `tests/test_e2e_full_lifecycle.py`, `tests/test_e2e_minimal_loop.py`, `tests/test_e2e_flows.py`, `.github/` 或后续 CI 配置 |

---

## 3. 设计裁剪与合理化决策

| 设计点 | 原规格要求 | 当前实现决策 | 理由 |
|---|---|---|---|
| DP 预算状态 | PostgreSQL `MATERIALIZED VIEW dp_budget_status` | SQLAlchemy 模型表 `dp_budget_status`，由 Ledger 写穿透维护 | 项目单测使用 SQLite，开发模式依赖 `Base.metadata.create_all()`；真实物化视图会破坏跨数据库测试。表快照能提供同等读模型。 |
| FISCO BCOS 存证 | 强依赖真实联盟链节点 | PG append-only 哈希链为默认真实适配器，FISCO 作为可替换适配器 | 当前目标是逻辑正确和单测可跑；无链环境时 PG 哈希链更稳定，也满足不可篡改审计的本地验证。 |
| SGX/GPU/PG-in-TEE | 真实硬件运行时 | 保留接口、模拟器和降级路径 | 真实硬件无法通过普通单元测试验证，不应阻塞软件闭环；后续用硬件集成测试覆盖。 |
| L1 TEE 无硬件行为 | 规格默认 L1 是真实 TEE | 无 TEE 环境时 L1 明确作为普通软件密态沙箱运行，使用 `software_hash` attestation；有硬件信号且配置 runner/attester 时才进入硬件路径 | 防止无硬件环境伪造 SGX 证明；同时让部署在普通节点、SGX/TDX/SEV-SNP/iTrustee 节点上都有可解释、可单测的行为。 |
| K8s/K3s 分布式沙箱 | 真实 K8s/K3s 集群运行沙箱 pod | 当前单元测试覆盖 hardened manifest、NetworkPolicy、ResourceQuota、ready gate、状态映射、runtime 路由、产品入口、kubeconfig 和 Cilium FQDN allowlist；243 可作为后续 K3s e2e 节点 | 当前完成标准是逻辑正确、编译通过、单元测试可跑；已提供适配中国网络的 K3s/k3d 安装脚本，真实集群部署验证不作为本轮阻塞项。 |
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

## 10. Round 7 修复记录

### 触发条件

继续从产品化和易用性角度复核发现：数据产品创建没有资源绑定和输出约束配置，买方无法从沙箱会话列表创建会话，数据资源详情入口跳转到不存在路由，浏览器标题仍是模板名。

### 已完成

1. 数据产品创建补齐资源和安全策略：
   - 产品创建可选择 READY 数据资源并继承资源格式、行数和 schema。
   - 表单补齐安全等级、允许操作、Schema JSON、最大输出行数、DP 要求、运行时长和输出格式。

2. 买方沙箱会话创建入口：
   - 会话列表对买方显示创建按钮。
   - 创建弹窗可从已发布目录选择产品、选择 active 合约、设置沙箱级别和超时时间。

3. 数据资源列表契约和详情修复：
   - 对齐后端 `format/file_size_bytes/schema_fields` 字段。
   - 资源查看改为本页抽屉，避免跳到不存在详情路由。

4. 基础品牌化：
   - `index.html` 语言改为 `zh-CN`。
   - 浏览器标题改为 `密态沙箱系统 CDS`。

### 验证

| 命令 | 结果 |
|---|---|
| `npm run build`（`cds-frontend`） | 通过 |
| `npm test -- --run`（`cds-frontend`） | 3 files passed, 12 tests passed |

---

## 11. Round 8 修复记录

### 触发条件

下一轮产品化复核发现：产品详情仍是极简只读页，产品生命周期入口缺失；目录详情只展示列表摘要，买方无法从目录上下文直接启动沙箱；合约/沙箱创建需要手工选择或复制产品；仪表盘没有角色化下一步入口；产品列表搜索框没有真实查询能力；运营/监管/管理员产品路由与后端产品详情可见性不一致。

### 已完成

1. 产品资产详情页：
   - 产品详情展示资源绑定、Provider、Schema、输出约束、允许操作、生命周期和版本记录。
   - 数商可提交审核、发布已批准产品，并从已发布产品创建合约。
   - 运营/管理员可在审核中产品详情直接通过或退回。

2. 目录到沙箱的闭环：
   - 目录详情弹窗拉取完整产品详情，展示输出约束和 Schema。
   - 买方可从目录卡片或详情弹窗进入沙箱创建，并自动带入产品 ID。

3. 上下文预填：
   - 合约创建支持 `product_id` 查询参数，自动预选产品并生成默认标题。
   - 沙箱会话列表支持 `create=1&product_id=...`，自动打开创建弹窗并预填产品。

4. 角色工作台：
   - 仪表盘增加数商、买方、运营/管理员、监管方快捷动作。
   - 产品审核入口可直达 `reviewing` 状态筛选。

5. 产品列表和后端可见性：
   - `/api/v1/data-products` 支持 `q` 名称/描述搜索。
   - 前端产品列表搜索实际生效，新增状态筛选和 API 类型展示。
   - 后端产品列表/详情按角色可见性收口：数商看自有，买方看已发布，operator/regulator/admin 看全量。

### 验证

| 命令 | 结果 |
|---|---|
| `npm run build`（`cds-frontend`） | 通过 |
| `npm test -- --run`（`cds-frontend`） | 3 files passed, 12 tests passed |
| `.venv/bin/python -m compileall -q app tests` | 通过 |
| `timeout 180s .venv/bin/python -m pytest -q tests/test_data_products.py tests/test_catalog.py tests/test_sandbox_sessions.py` | 20 passed |

---

## 12. Round 9 修复记录

### 触发条件

继续从安全闭环和产品化工作流复核发现：普通沙箱会话 `/execute` 只做代码扫描，没有进入统一输出审查，可能直接释放 raw output；会话详情页只展示基础字段，用户创建沙箱后无法在页面内执行和查看输出安全报告；运营/管理员终止他人会话时释放的是操作者配额而不是会话 owner 配额；登录/注册页仍是默认模板卡片。

### 已完成

1. 普通沙箱直接执行输出安全闭环：
   - `/api/v1/sandbox-sessions/{session_id}/execute` 支持 JSON body，同时保留 query 参数兼容。
   - 执行结果统一调用 `OutputInspector`，非 critical DLP 发现脱敏后释放。
   - critical 发现阻断输出，返回 `output_blocked=true`、`security_report`、水印和签名。
   - 新增单测覆盖手机号脱敏和身份证号 critical 阻断。

2. 沙箱会话工作台：
   - 前端会话详情展示真实响应字段：owner、data_product_id、sandbox_mode、session_key、timeout、resource_limits、started/ended 时间。
   - 会话详情提供数据产品/合约跳转、按权限终止会话、owner running 会话代码执行入口。
   - 执行结果展示 exit code、脱敏输出、阻断状态、审查发现、水印和签名。

3. 运维终止配额和审计修复：
   - operator/admin 终止他人会话时按 `session.user_id` 释放 tenant usage。
   - 终止前保存 `previous_status`，审计不再记录终止后的假前态。

4. 认证入口产品化：
   - 登录/注册页改为 CDS 产品化认证界面。
   - 保留原接口、错误提示和表单校验，增加响应式布局。

### 验证

| 命令 | 结果 |
|---|---|
| `.venv/bin/python -m compileall -q app tests` | 通过 |
| `npm run build`（`cds-frontend`） | 通过 |
| `npm test -- --run`（`cds-frontend`） | 3 files passed, 12 tests passed |
| `timeout 180s .venv/bin/python -m pytest -q tests/test_sandbox_sessions.py` | 7 passed |

---

## 13. Round 10 修复记录

### 触发条件

继续从“会话运行后的安全控制面”复核发现：沙箱创建时已经默认生成 deny_all 网络策略，但前端没有查看/修改入口；会话级更新接口使用 query/list 参数，不适合作为产品 UI 契约；独立 `network-policies` API 调用异步策略引擎时没有 `await`，导致热加载可能没有真正执行；`/network-policies/session/{session_id}` 被 `/{policy_id}` 路由遮挡；网络策略创建缺少对目标 session 的权限校验，update 也没有复用 CIDR/端口校验。

### 已完成

1. 会话级网络策略 API：
   - 新增 `GET /api/v1/sandbox-sessions/{session_id}/network-policy`，返回或初始化会话默认 deny_all 策略。
   - `PUT /api/v1/sandbox-sessions/{session_id}/network-policy` 改为 JSON body，复用 `NetworkPolicyUpdate` schema。
   - owner/operator/admin 可修改 running/ready 会话策略；regulator 可只读查看。

2. 网络策略 UI 控制面：
   - 沙箱会话详情新增“网络策略”卡片。
   - 支持 deny_all/allowlist、CIDR、域名、端口、DNS 代理、连接速率、带宽上限和 active 开关。
   - 表单输入转换为结构化数组，避免用户手写 JSON 或复制 query 参数。

3. 独立网络策略 API 修复：
   - `create/update/delete` 中对 `network_policy_engine.create_policy/remove_policy` 增加 `await`。
   - 将 `/session/{session_id}` 路由置于 `/{policy_id}` 前，避免被 UUID 路由遮挡。
   - 创建/查看/修改/删除策略按 session owner/operator/admin/regulator 权限收口。

4. 校验和默认值修复：
   - `NetworkPolicyCreate` 的 list 默认值改为 `Field(default_factory=...)`。
   - create/update 均校验 CIDR 和端口范围。

### 验证

| 命令 | 结果 |
|---|---|
| `.venv/bin/python -m compileall -q app tests` | 通过 |
| `npm run build`（`cds-frontend`） | 通过 |
| `npm test -- --run`（`cds-frontend`） | 3 files passed, 12 tests passed |
| `timeout 180s .venv/bin/python -m pytest -q tests/test_sandbox_sessions.py` | 10 passed |

---

## 14. Round 11 修复记录

### 触发条件

继续从“字段级最小化”和产品化可用性复核发现：字段暴露后端能力没有完整接入产品详情和目录；restricted 字段仍可能被 auto approve 或数商审批；买方申请字段后，契约网关查询路径没有把批准字段作为执行前授权条件；网关 URL 中的 `contract_id` 与 app credential 绑定合约没有一致性校验，可能造成审计标签和调用语义错位。

### 已完成

1. 字段可见性产品控制面：
   - 新增 `fieldExposureApi` 前端服务。
   - 产品详情页支持数商维护默认敏感度、逐字段敏感度、mask pattern、auto approve 和字段说明。
   - 产品详情页增加 provider/admin 字段申请审批表，支持批准和驳回待审申请。

2. 买方字段申请体验：
   - 目录详情展示产品完整 schema 和当前买方已获批字段。
   - 买方可在目录详情中选择字段、填写用途说明并提交字段暴露申请。
   - 申请提交后刷新已批准字段和产品详情状态，减少跳转和裸 ID 操作。

3. restricted 字段审批语义收紧：
   - restricted 字段不再被 `auto_approve=true` 自动批准。
   - 数商审批 restricted 字段返回 403。
   - 平台 admin/operator 可查看 provider-scope 待审申请并完成 restricted 字段审批。

4. 契约网关执行面字段最小化：
   - `GatewayService.execute_query()` 在 DuckDB 查询前加载字段可见性配置和买方已批准字段。
   - public/internal 字段默认允许；sensitive/pii/restricted 字段必须有未过期批准记录。
   - 一旦产品配置字段可见性，`SELECT *` / `alias.*` 失败关闭；显式查询中 SELECT/WHERE/GROUP/ORDER/HAVING 等引用未授权字段也失败关闭。

5. 网关合约路径一致性：
   - query/access/metering/status 在 app credential 认证后校验 URL `contract_id` 等于 credential 绑定合约。
   - 不一致直接 403，避免使用 A 合约凭证调用 B 合约路径造成审计和调用语义错位。

### 验证

| 命令 | 结果 |
|---|---|
| `.venv/bin/python -m compileall -q app tests alembic` | 通过 |
| `npm run build`（`cds-frontend`） | 通过 |
| `npm test -- --run`（`cds-frontend`） | 3 files passed, 12 tests passed |
| `timeout 180s .venv/bin/python -m pytest -q tests/test_field_exposure.py` | 4 passed |
| `timeout 180s .venv/bin/python -m pytest -q tests/test_contract_gateway.py tests/test_gateway.py tests/test_field_exposure.py` | 38 passed |

---

## 15. Round 12 修复记录

### 触发条件

继续从跨空间互联和代理沙箱入口复核发现：`ContractFulfillment.check_contract_constraints()` 在 session 已绑定合约但合约缺失时返回允许，并且用字符串 `SandboxSession.contract_id` 直接查询 UUID `Contract.id` 会在 SQLite 下异常；connector API 允许任意 active connector 使用已知 active contract id 创建代理会话；connector 代理执行直接返回 runtime 输出，没有代码扫描、输出审查、水印/签名报告；connector 创建的底层 sandbox session 也缺少普通沙箱已有的 session key、KeyMetadata、默认 deny_all 网络策略和合约配额基线。

### 已完成

1. 合约约束检查失败关闭：
   - `SandboxSession.contract_id` 先解析为 UUID，再查询 `Contract.id`。
   - 绑定合约缺失、非法 contract id、合约非 active/signed、session 产品不在合约范围内均返回 `allowed=false`。
   - `allowed_sandbox_modes` 改为检查 `session.sandbox_mode`，不再错误地拿 operation 字符串比较。

2. connector 与 contract 绑定校验：
   - 支持从 contract `terms` 顶层或 `connector` / `remote_space` / `federation` 嵌套对象读取 `connector_id`、`space_id`、`remote_space_id`。
   - 合约声明绑定时，创建和执行 connector session 必须匹配当前 connector。
   - 未声明绑定的历史合约保持兼容。

3. connector 代理执行安全闭环：
   - `/api/v1/connectors/remote/sessions/{id}/execute` 执行前调用 `CodeScanner`。
   - 扫描失败写审计并返回 422，不进入 runtime。
   - 执行输出进入 `output_security`，非 critical 发现脱敏，critical 发现阻断，并返回 `security_report`、`output_blocked`、`blocked_reason`。

4. connector 代理会话安全基线：
   - 创建代理 session 时生成 KMS session key 并写入 `KeyMetadata`。
   - 默认创建并热加载 deny_all `NetworkPolicy`。
   - 按合约设置 rows/bytes/api_calls/gpu_seconds quota limits。
   - `SandboxSession.contract_id` 写入统一使用字符串，`ConnectorSession.contract_id` 保持 UUID 外键。

### 验证

| 命令 | 结果 |
|---|---|
| `.venv/bin/python -m compileall -q app tests alembic` | 通过 |
| `npm run build`（`cds-frontend`） | 通过 |
| `npm test -- --run`（`cds-frontend`） | 3 files passed, 12 tests passed |
| `timeout 180s .venv/bin/python -m pytest -q tests/test_connectors.py tests/test_contract_fulfillment.py` | 8 passed |
| `timeout 180s .venv/bin/python -m pytest -q tests/test_contracts.py tests/test_contracts_extended.py tests/test_sandbox_sessions.py tests/test_connectors.py tests/test_contract_fulfillment.py` | 41 passed |

---

## 16. Round 13 修复记录

### 触发条件

继续从产品化和安全授权链路复核发现：本地沙箱会话传入 `contract_id` 时没有强制校验合约，数据产品通用更新可绕过生命周期直接发布；合约创建/激活缺少买方角色、产品归属、产品发布状态和参与方校验；数据产品删除会让已发布资产或被引用资产悬空；`sandbox-db` 与输出控制 DP budget 均按裸 `session_id` 访问，缺少 owner 授权。

### 已完成

1. 本地沙箱合约绑定失败关闭：
   - `create_sandbox_session()` 在传入 `contract_id` 时强制解析 UUID 并读取合约。
   - 校验合约 active/signed、当前用户为 buyer 或 operator/admin、产品被合约覆盖。
   - 校验 sandbox level、默认 structured_query 模式、allowed_operations 和 timeout 不超过合约时长。
   - `sandbox_db._attach_policy()` 修复字符串 contract_id 查询 UUID 的问题。

2. 数据产品生命周期收口：
   - 通用 `PATCH/PUT /data-products/{id}` 拒绝 `status` 字段，状态变更必须走 submit/approve/publish/reject/archive。
   - 单测造数不再依赖接口后门，改用测试夹具或真实生命周期。
   - 前端 `UpdateProductRequest` 移除 status，避免 UI 误用通用更新接口。

3. 合约创建与激活收紧：
   - 只有 `data_provider` 可创建合约，且只能为自己的 data product 创建。
   - `buyer_id` 必须存在且角色为 buyer，不能与 provider 相同。
   - `/contracts/{id}/activate` 必须由合约参与方调用，active 状态幂等返回，其他状态只允许 signed 转 active。
   - 签署或显式激活转 active 前，所有产品必须存在、属于 contract provider 且已发布。

4. 数据产品删除与归档：
   - 物理删除只允许无合约引用、无 active session 的 draft 产品。
   - 新增 `/data-products/{id}/archive`，非 draft 产品通过归档下架。
   - 有 active/signed/negotiating/suspended contract 或 active session 时归档失败关闭。
   - 产品列表前端对 draft 显示删除，对非 draft 显示归档。

5. Session-scoped 控制面授权：
   - `sandbox-db` API 创建 ad-hoc DuckDB 引擎时记录 owner；真实 SandboxSession 按 session owner 校验；operator/admin 可管理。
   - 查询、列表、关闭、加密元数据、加密配置、导入和解密查询均复用同一授权逻辑。
   - 输出控制 DP budget 初始化/查询记录 owner；真实 SandboxSession 按 owner 校验；非 owner 无法覆盖或查询预算。

### 验证

| 命令 | 结果 |
|---|---|
| `.venv/bin/python -m compileall -q app tests alembic` | 通过 |
| `npm test -- --run`（`cds-frontend`） | 3 files passed, 12 tests passed |
| `npm run build`（`cds-frontend`） | 通过 |
| `timeout 180s .venv/bin/python -m pytest -q tests/test_data_products.py tests/test_catalog.py tests/test_contracts.py tests/test_contracts_extended.py tests/test_field_exposure.py tests/test_sandbox_sessions.py tests/test_connectors.py tests/test_contract_fulfillment.py` | 74 passed |
| `timeout 180s .venv/bin/python -m pytest -q tests/test_secure_duckdb.py` | 27 passed |
| `timeout 180s .venv/bin/python -m pytest -q tests/test_output_control_api.py` | 23 passed |
| `timeout 600s .venv/bin/python -m pytest -q -k "not e2e"` | 2027 passed, 1 skipped, 91 deselected |

---

## 17. Round 14 修复记录

### 触发条件

继续从产品化安全和易用性角度复核发现：数据资源上传路径与 `StorageService.upload()` 返回契约不一致，导致上传接口直接 500；数据资源删除没有检查产品引用，也没有清理本地密文对象和 DEK；输出控制 `/inspect`、`/gateway` 仍能按任意 session_id 写审计或释放输出；数据产品版本列表、字段可见性和 approved-fields 没有完整复用产品可见性；字段申请 API 也没有限制为买方角色，字段策略写入与“平台管理员可治理”的产品语义不一致。

### 已完成

1. 数据资源上传与删除闭环：
   - 上传写入 `chunk_refs` 时兼容 `sm3_hash` 和当前 `checksum` 返回字段，避免资源上传 500。
   - 删除资源前统计 `DataProduct.resource_id` 引用，被引用直接返回 409。
   - 未引用资源删除时同步删除 storage 对象、销毁本地 KMS key，并写入 `data_resource.delete` 审计。

2. 输出控制 session 授权：
   - 新增 output-control session owner 记录，ad-hoc session_id 首次使用绑定当前用户。
   - `/inspect` 和 `/gateway` 如果传入真实 `SandboxSession` UUID，必须是 session owner；operator/admin 可管理。
   - 非 owner 不能借用他人 session_id 写审计或释放输出。

3. 产品版本与字段策略可见性：
   - `/data-products/{id}/versions` 入口产品不可见时返回 404。
   - 版本遍历时只返回当前用户可见版本，买方看不到未发布产品或草稿后继版本。
   - `field-visibility` 和 `approved-fields` 读取前先按产品可见性校验，未发布产品策略不对买方泄露。

4. 字段暴露角色语义：
   - 只有 buyer 可创建字段暴露申请，provider 自有产品仍返回“已有完整访问权”。
   - 字段策略写入允许 provider 或 platform admin/operator；regulator 保持只读。

5. 产品化告警清理：
   - 数据产品和数据资源采样接口的 FastAPI `Query(regex=...)` 改为 `pattern`。
   - 消除测试中持续出现的接口参数弃用警告，降低后续 FastAPI 升级风险。
   - JWT 签发/解码在 TESTING/DEBUG 且未配置真实 secret 时使用明确的 32+ 字节本地 key；生产默认 sentinel 仍失败关闭。
   - 联邦连接器 JWT fallback 复用应用 JWT secret，未配置真实 secret 时生产失败关闭；测试/DEBUG 本地 key 也满足 HS256 32 字节要求。
   - 安全测试里的 DB mock 对齐 AsyncSession：同步 `add()` 使用 MagicMock，避免 CI 未 await 协程告警。

### 验证

| 命令 | 结果 |
|---|---|
| `.venv/bin/python -m compileall -q app tests alembic` | 通过 |
| `timeout 180s .venv/bin/python -m pytest -q tests/test_data_resources_api.py tests/test_output_control_api.py tests/test_field_exposure.py tests/test_data_products.py` | 48 passed |
| `timeout 180s .venv/bin/python -m pytest -q tests/test_federation_connector.py tests/test_security_hardening.py tests/test_auth.py tests/test_auth_extended.py tests/test_data_resources_api.py tests/test_output_control_api.py tests/test_field_exposure.py tests/test_data_products.py` | 144 passed |
| `timeout 180s .venv/bin/python -m pytest -q tests/test_p0_8_9_10.py::TestCertRevocationCascade::test_revoke_cascades_to_sessions tests/test_p0_security_gaps.py::TestSM2SignatureNoFallback::test_sign_without_certificate_raises tests/test_remote_attestation.py::TestLogAndStats::test_log_limit` | 3 passed |
| `timeout 180s .venv/bin/python -m pytest -q tests/test_rate_limiting.py` | 17 passed |
| `timeout 600s .venv/bin/python -m pytest -q -k "not e2e"` | 2041 passed, 1 skipped, 91 deselected；1 个延迟回收测试 warning |

---

## 18. Round 15 修复记录

### 触发条件

继续从沙箱安全和分布式运行时可运行性复核发现：`SandboxRuntime` 对未知 `container_id` 前缀会回退到 Docker adapter，存在误操作非沙箱容器的风险；L2 Firecracker 和 K8s 路径没有完整透传 session key、审计 env 和 timeout，导致多级沙箱执行语义不一致；K8s adapter 的 pod name 截断规则、`kubectl apply` stdin 和 NetworkPolicy selector 都会让真实 K8s 控制面不可用；在 243 的 Python 3.12 环境中，`str | "SandboxMode"` 注解会导致模块导入失败。

### 已完成

1. Runtime 路由失败关闭：
   - 未知 `container_id` 前缀执行直接返回 `SECURITY ERROR`。
   - 未知前缀 terminate 返回 `False`，status 返回 `unknown`。
   - 不再隐式调用 Docker adapter 操作任意容器。

2. L2/K8s 执行上下文一致性：
   - FirecrackerRuntime 支持 `env_vars` 和 `timeout`。
   - Firecracker simulation/QEMU fallback/SSH 路径注入 session key 和上下文 env。
   - K8sRuntimeAdapter 支持 python/bash 执行，并用 `env` 注入 `CDS_SESSION_KEY`、session id、sandbox mode 等上下文。

3. K8s 控制面修复：
   - 统一 K8s pod name 派生：`sandbox-{session_id[:16]}`。
   - `_kubectl()` 支持 `input=`，`kubectl apply -f -` 能接收 manifest。
   - NetworkPolicy selector 使用完整 `session_id`，与 pod label 一致，避免策略不生效。

4. Python 3.12 运行期兼容：
   - `SceneRuntimeFactory.create()` 注解改为 `str | SandboxMode`。
   - 243 的 Python 3.12 环境验证导入、编译和目标测试通过。

5. 243 验证环境准备：
   - 当前源码同步到 `/root/cds-sandbox-codex`。
   - 远端使用 `/usr/local/bin/python3.12` + `uv` 创建 `.venv`。
   - 因 `tongsuo` 在镜像源不可用，目标单测采用最小后端依赖集；不把 Tongsuo/真实 K3s/重型训练包作为本轮阻塞项。
   - 后续真实 K3s e2e 建议使用 `INSTALL_K3S_MIRROR=cn` 安装，并配置 containerd registry mirror 与 `python:3.12-slim` 等沙箱镜像预拉。

### 验证

| 命令 | 结果 |
|---|---|
| `.venv/bin/python -m compileall -q app tests alembic` | 本机通过 |
| `ssh root@172.22.4.243 'cd /root/cds-sandbox-codex && /root/.local/bin/uv run python -m compileall -q app tests alembic'` | 243 通过 |
| `ssh root@172.22.4.243 'cd /root/cds-sandbox-codex && timeout 180s /root/.local/bin/uv run python -m pytest -q tests/test_sandbox_runtime.py::test_sandbox_runtime_routes_l1_and_l2_container_prefixes tests/test_sandbox_runtime.py::test_sandbox_runtime_terminate_and_status_route_l1_l2_prefixes tests/test_sandbox_runtime.py::test_sandbox_runtime_fails_closed_for_unknown_container_prefix tests/test_sandbox_runtime.py::test_k8s_runtime_uses_stable_pod_name_and_passes_context tests/test_sandbox_runtime.py::test_k8s_sandbox_provision_applies_manifest_and_session_policy tests/test_sandbox_sessions.py::test_execute_runtime_with_context_passes_supported_kwargs tests/test_p05_code_scanning.py::TestCodeScannerPython::test_product_mode_names_are_supported tests/test_p05_code_scanning.py::TestCodeScannerPython::test_llm_training_mode_enforces_save_path_restriction'` | 8 passed |

---

## 19. Round 16 修复记录

### 触发条件

继续从真实 K8s/K3s 沙箱部署前的安全和可运行性复核发现：Pod 已启用 `readOnlyRootFilesystem`，但未为 `/workspace`、`/tmp`、`/home` 和 Python cache 提供完整可写路径；`deny_all` NetworkPolicy 仍保留 DNS egress；ResourceQuota 使用 Kubernetes 不支持的 arbitrary label scopeSelector；K8s exec 把 session key 放在 `kubectl` argv；env 名称非法时会把错误推迟到 Kubernetes apply；安装脚本实际偏 k3d，不适配 243 root/native K3s 和中国网络镜像下载。

### 已完成

1. K8s Pod hardened manifest：
   - 新增 `SANDBOX_K8S_NAMESPACE`、`SANDBOX_K8S_IMAGE`、`SANDBOX_K8S_IMAGE_PULL_POLICY`、`SANDBOX_K8S_RUNTIME_CLASS` 配置。
   - manifest 设置 `automountServiceAccountToken=false`、`enableServiceLinks=false`、`seccompProfile=RuntimeDefault`、`runAsGroup=1000`、`privileged=false`。
   - `/workspace`、`/tmp`、`/home/sandbox` 使用 `emptyDir` tmpfs，并设置 `HOME/TMPDIR/PYTHONPYCACHEPREFIX` 指向可写路径。
   - requests/limits 补齐 `ephemeral-storage`。

2. K8s 网络和 quota：
   - `deny_all` NetworkPolicy egress 改为空列表，默认不放行 DNS。
   - ResourceQuota 改为 namespace-level `quota-cds-sandbox`，移除非法 scopeSelector。
   - 租户级 quota 继续由 CDS 控制面在 provision 前执行，不伪造 Kubernetes per-label quota。

3. K8s exec 密钥注入：
   - runtime 改为 `kubectl exec -i ... -- sh -s`。
   - session key、上下文 env 和 base64 用户代码经 stdin 脚本注入，不出现在 `kubectl` argv。
   - exec 前创建 `/workspace/tmp`、`/workspace/output` 和 `/tmp/pycache`。

4. K8s env 校验：
   - provision 阶段校验 env 名称，非法名称失败关闭。
   - 测试覆盖非法 env 不触发 `kubectl apply`。

5. 243/K3s 安装脚本：
   - `scripts/install-k3s.sh` 支持 `CDS_K3S_MODE=k3s` native K3s 和 `CDS_K3S_MODE=k3d` 本地 k3d。
   - 默认 `INSTALL_K3S_MIRROR=cn`，支持 registry mirror 配置和沙箱镜像预拉。
   - `k8s/registries.yaml` 补齐 Docker Hub、registry.k8s.io、quay.io、ghcr.io 的国内镜像端点。

### 验证

| 命令 | 结果 |
|---|---|
| `.venv/bin/python -m compileall -q app tests alembic` | 本机通过 |
| `timeout 180s .venv/bin/python -m pytest -q tests/test_sandbox_runtime.py::test_k8s_runtime_uses_stable_pod_name_and_passes_context tests/test_sandbox_runtime.py::test_k8s_sandbox_provision_applies_manifest_and_session_policy tests/test_sandbox_runtime.py::test_k8s_sandbox_provision_rejects_invalid_env_names tests/test_sandbox_runtime.py::test_k8s_sandbox_manifest_supports_readonly_root_with_writable_tmpfs tests/test_sandbox_runtime.py::test_k8s_runtime_exec_injects_secret_via_stdin_not_argv` | 5 passed |
| `ssh root@172.22.4.243 'cd /root/cds-sandbox-codex && /root/.local/bin/uv run python -m compileall -q app tests alembic'` | 243 通过 |
| `ssh root@172.22.4.243 'cd /root/cds-sandbox-codex && timeout 180s /root/.local/bin/uv run python -m pytest -q tests/test_sandbox_runtime.py::test_k8s_runtime_uses_stable_pod_name_and_passes_context tests/test_sandbox_runtime.py::test_k8s_sandbox_provision_applies_manifest_and_session_policy tests/test_sandbox_runtime.py::test_k8s_sandbox_provision_rejects_invalid_env_names tests/test_sandbox_runtime.py::test_k8s_sandbox_manifest_supports_readonly_root_with_writable_tmpfs tests/test_sandbox_runtime.py::test_k8s_runtime_exec_injects_secret_via_stdin_not_argv'` | 5 passed |

---

## 20. Round 17 修复记录

### 触发条件

继续从 K8s/K3s 真实运行闭环复核发现：后端已有 K8s RuntimeAdapter，但 session/contract schema、资源限额和前端创建入口都不允许用户选择 K8s；K8s provision 在 Pod apply 成功后立即返回，Pending/ImagePullBackOff 也会进入 running 路径；NetworkPolicy/ResourceQuota apply 失败被忽略，可能留下无默认隔离策略的 Pod；runtime execute 未检查 Pod ready；terminate 只捕获异常、不检查 `kubectl delete` returncode；status 返回 Kubernetes phase，没有映射到 CDS session lifecycle。

### 已完成

1. K8s runtime 产品入口：
   - `SandboxLevel` 新增 `K8S="k8s"`，`sandbox_level` 字段长度放宽到 16。
   - session schema、contract schema 和 sandbox resource limits 接受 `k8s`。
   - 前端合约创建、沙箱创建和 Dashboard 色板加入 K8s 选项。

2. K8s readiness gate：
   - 新增 `SANDBOX_K8S_READY_TIMEOUT_SECONDS` 和 `SANDBOX_K8S_POLL_INTERVAL_SECONDS`。
   - provision apply Pod 后等待 Pod `Running` 且 container ready。
   - 未 ready、失败 phase 或等待超时都失败关闭并回滚 Pod/Policy。

3. K8s 控制面失败回滚：
   - NetworkPolicy 和 ResourceQuota apply 均检查 returncode。
   - 默认 deny_all policy 或 quota 失败时删除 Pod，避免无策略沙箱残留。

4. K8s exec/terminate/status 生命周期：
   - exec 前读取 Pod 状态，非 running/ready 直接结构化失败，不触发 `kubectl exec`。
   - terminate 检查 pod 和 NetworkPolicy delete returncode，失败返回 `False`。
   - `get_status()` 输出统一 `cds_status`：running/provisioning/failed/completed。

### 验证

| 命令 | 结果 |
|---|---|
| `.venv/bin/python -m compileall -q app tests alembic` | 本机通过 |
| `timeout 180s .venv/bin/python -m pytest -q tests/test_sandbox_runtime.py::test_k8s_runtime_uses_stable_pod_name_and_passes_context tests/test_sandbox_runtime.py::test_k8s_sandbox_provision_applies_manifest_and_session_policy tests/test_sandbox_runtime.py::test_k8s_sandbox_provision_fails_closed_when_policy_apply_fails tests/test_sandbox_runtime.py::test_k8s_sandbox_provision_fails_closed_when_pod_not_ready tests/test_sandbox_runtime.py::test_k8s_sandbox_terminate_checks_kubectl_returncode tests/test_sandbox_runtime.py::test_k8s_sandbox_provision_rejects_invalid_env_names tests/test_sandbox_runtime.py::test_k8s_sandbox_manifest_supports_readonly_root_with_writable_tmpfs tests/test_sandbox_runtime.py::test_k8s_runtime_exec_injects_secret_via_stdin_not_argv tests/test_sandbox_runtime.py::test_k8s_runtime_refuses_exec_when_pod_not_ready tests/test_sandbox_levels.py::TestSandboxLevelEnum tests/test_sandbox_lifecycle.py::test_resource_limits_defined tests/test_contracts.py::test_contract_schema_accepts_k8s_sandbox_level` | 15 passed |
| `cds-frontend: npm run build` | 通过 |
| `ssh root@172.22.4.243 'cd /root/cds-sandbox-codex && /root/.local/bin/uv run python -m compileall -q app tests alembic && timeout 180s /root/.local/bin/uv run python -m pytest -q ...Round17 targets...'` | 243 编译通过；15 passed |

---

## 21. Round 19 修复记录

### 触发条件

继续从 K8s/K3s 真实部署和多集群运行语义复核发现：`K8sSandboxAdapter` 已暴露 `kubeconfig` 参数但模块级 `_kubectl()` 没有统一使用该配置，runtime exec 也绕过 adapter 直接调用默认 kubectl；allowlist 模式接收 `allowed_domains`，但 Kubernetes 原生 NetworkPolicy 不支持 FQDN，实际语义会失败开放或不生效；allowlist 的 CIDR/domain 未在 adapter 层校验，错误会推迟到集群 apply。

### 已完成

1. kubeconfig 多集群透传：
   - 新增 `SANDBOX_K8S_KUBECONFIG` 配置。
   - `K8sSandboxAdapter._kubectl()` 统一追加 `--kubeconfig`。
   - `K8sRuntimeAdapter.execute()` 改用 adapter `_kubectl()`，避免 exec 路径打到默认集群。

2. K8s FQDN allowlist 失败关闭：
   - 新增 `SANDBOX_K8S_FQDN_POLICY_PROVIDER` 配置。
   - 默认不接受 domain allowlist，直接返回失败。
   - 配置为 `cilium` 时生成 `CiliumNetworkPolicy`，使用 `toFQDNs.matchName/matchPattern`。

3. allowlist 输入校验：
   - adapter provision 前校验 network policy mode。
   - allowlist 必须至少包含 CIDR 或 domain。
   - 非法 CIDR、非法 domain 在进入 `kubectl apply` 前失败关闭。

### 验证

| 命令 | 结果 |
|---|---|
| `.venv/bin/python -m compileall -q app tests alembic` | 本机通过 |
| `timeout 180s .venv/bin/python -m pytest -q tests/test_sandbox_runtime.py::test_k8s_runtime_uses_stable_pod_name_and_passes_context tests/test_sandbox_runtime.py::test_k8s_sandbox_provision_applies_manifest_and_session_policy tests/test_sandbox_runtime.py::test_k8s_sandbox_provision_fails_closed_when_policy_apply_fails tests/test_sandbox_runtime.py::test_k8s_sandbox_provision_fails_closed_when_pod_not_ready tests/test_sandbox_runtime.py::test_k8s_sandbox_terminate_checks_kubectl_returncode tests/test_sandbox_runtime.py::test_k8s_sandbox_provision_rejects_invalid_env_names tests/test_sandbox_runtime.py::test_k8s_sandbox_provision_rejects_domain_allowlist_without_fqdn_provider tests/test_sandbox_runtime.py::test_k8s_sandbox_provision_rejects_invalid_allowlist_values tests/test_sandbox_runtime.py::test_k8s_sandbox_allowlist_with_cilium_domains_applies_fqdn_policy tests/test_sandbox_runtime.py::test_k8s_sandbox_adapter_passes_kubeconfig_to_kubectl tests/test_sandbox_runtime.py::test_k8s_sandbox_manifest_supports_readonly_root_with_writable_tmpfs tests/test_sandbox_runtime.py::test_k8s_runtime_exec_injects_secret_via_stdin_not_argv tests/test_sandbox_runtime.py::test_k8s_runtime_refuses_exec_when_pod_not_ready` | 13 passed |
| `ssh root@172.22.4.243 'cd /root/cds-sandbox-codex && /root/.local/bin/uv run python -m compileall -q app tests alembic && timeout 180s /root/.local/bin/uv run python -m pytest -q ...Round19 targets...'` | 243 编译通过；13 passed |

---

## 22. Round 20 修复记录

### 触发条件

继续扫描 `未实现`、`not implemented`、`stub`、`mock` 和运行时工厂后发现：规格仍标注 `FederatedRuntime 未实现`，代码中 `joint_federated` 虽是合法模式但未注册专用场景运行时；Firecracker 非网络串口执行直接返回占位字符串；告警引擎的 DP budget exhaustion 在 `evaluate()` 路径为空；非结构化数据处理对 Pillow、pdftotext、ffprobe 的失败静默吞掉，产品侧无法提示“只完成基础元数据”。

### 已完成

1. 联邦场景运行时：
   - 新增 `FederatedRuntime` 并注册到 `SceneRuntimeFactory`。
   - SQL 路径阻断 DDL/DML 和默认 raw `SELECT *`，并补齐输出行数限制。
   - Python 路径阻断直接 `requests/httpx/urllib/socket` 网络访问，要求走联邦连接器上下文。
   - 支持 `federation_request` 上下文，调用 `FederationConnector.send_request()` 进行真实跨空间代理请求。

2. Firecracker 非网络执行路径：
   - 删除 `Serial execution not fully implemented` 占位返回。
   - guest agent/SSH 不可用时复用 QEMU TCG 的 hardened bwrap fallback。
   - session key、审计 env 和 timeout 透传到 fallback 执行。
   - bwrap seccomp 不兼容检测同时识别 `EINVAL`、`Invalid argument` 和 `PR_SET_SECCOMP`，避免 243 环境 fallback 重试失效。

3. DP budget 自动告警：
   - `AlertRuleEngine` 新增 `record_dp_budget()` 快照入口。
   - `evaluate()` 的 `DP_BUDGET_EXHAUSTION` 分支会自动生成告警。
   - 保留现有 `check_dp_budget()` 手工检查接口。

4. 非结构化数据处理降级显式化：
   - Pillow/pdftotext/ffprobe 缺失或失败时不再静默吞掉。
   - 返回 metadata 中的 `partial_success`、`extraction_errors` 和具体阶段状态。
   - 基础文件元数据仍保持成功返回，避免可选依赖缺失阻塞上传/登记流程。

5. 规格状态同步：
   - `ss-04-sandbox-runtime.md` 将 LLM、视觉多模态和 FederatedRuntime 状态更新为当前实现状态，不再保留过期 `Stub/未实现` 描述。

### 验证

| 命令 | 结果 |
|---|---|
| `.venv/bin/python -m compileall -q app tests alembic` | 本机通过 |
| `ssh root@172.22.4.243 'cd /root/cds-sandbox-codex && /root/.local/bin/uv run python -m compileall -q app tests alembic && timeout 240s /root/.local/bin/uv run python -m pytest -q tests/test_scene_runtime.py tests/test_firecracker_runtime.py tests/test_alert_engine.py tests/test_data_processing.py'` | 243 通过，102 passed |

---

## 23. Round 21 修复记录

### 触发条件

继续修复后续 gap 时明确新的 L1 TEE 产品语义：如果部署环境没有提供 TEE，则安全沙箱应作为普通密态沙箱运行；如果提供 TEE 环境，则应进入相应硬件能力对接路径。当前实现的问题是：无硬件时仍使用 SGX 形状的模拟 quote，容易让 KMS/审计误判为硬件 TEE；同时缺少 SGX/TDX/SEV-SNP/iTrustee 自动探测和硬件 runner/attester hook。

### 已完成

1. TEE 运行配置：
   - 新增 `TEE_MODE=auto|software|sgx|tdx|sev_snp|itrustee`。
   - 新增 `TEE_ALLOW_SOFTWARE_FALLBACK`。
   - 新增 `TEE_HARDWARE_PROVISION_CMD`、`TEE_HARDWARE_EXEC_CMD`、`TEE_HARDWARE_ATTEST_CMD`、`TEE_HARDWARE_TERMINATE_CMD`。

2. TEE 能力探测：
   - 新增 `TEECapabilityDetector`。
   - 自动探测 SGX、TDX、SEV-SNP、iTrustee 的设备文件和运行时命令信号。
   - `software/software_confidential/none/off` 显式进入普通软件密态沙箱。

3. L1 无硬件降级：
   - 无硬件或未配置硬件 exec hook 时进入 `software_confidential`。
   - fallback 继续使用 bwrap 隔离，但对外不再声明 SGX。
   - attestation 改为 `software_hash`，与 KMS 中 Firecracker/software attestation 解析保持一致。

4. L1 硬件对接路径：
   - 探测到硬件信号且配置 `TEE_HARDWARE_EXEC_CMD` 时进入 `tee_mode=hardware`。
   - 用户代码经 stdin 传递给硬件 runner；session key 和上下文通过环境变量传递，避免写入命令行。
   - `TEE_HARDWARE_ATTEST_CMD` 输出 JSON quote，运行时回填 `attestation_quote/type/measurement`；未配置硬件 attester 时不伪造厂商 quote。
   - provision/terminate hook 支持外部硬件运行时生命周期接入。

5. 测试补齐：
   - 新增 `tests/test_tee_capability.py` 覆盖软件模式、SGX 信号探测和 auto 无信号降级。
   - 新增 TEEAdapter 无硬件软件密态 quote 测试，断言不再产生 SGX quote。
   - 新增硬件 runner/attester 单测，模拟 SGX provider 并验证 exec/attest 路由。
   - 修正 bwrap timeout 测试在受限主机上“提前失败关闭”的兼容断言。

### 验证

| 命令 | 结果 |
|---|---|
| `.venv/bin/python -m compileall -q app tests alembic` | 本机通过 |
| `ssh root@172.22.4.243 'cd /root/cds-sandbox-codex && /root/.local/bin/uv run python -m compileall -q app tests alembic && timeout 240s /root/.local/bin/uv run python -m pytest -q tests/test_tee_capability.py tests/test_sandbox_runtime.py::test_tee_adapter_no_hardware_uses_software_confidential_quote tests/test_sandbox_runtime.py::test_tee_adapter_hardware_runner_executes_when_detected tests/test_sandbox_runtime.py::test_tee_adapter_fails_closed_when_bwrap_missing tests/test_remote_attestation.py::TestQuoteGeneration::test_generate_firecracker_quote'` | 243 通过，7 passed |
| `ssh root@172.22.4.243 'cd /root/cds-sandbox-codex && /root/.local/bin/uv run python -m compileall -q app tests alembic && timeout 240s /root/.local/bin/uv run python -m pytest -q tests/test_sandbox_runtime.py tests/test_tee_capability.py tests/test_remote_attestation.py::TestQuoteGeneration::test_generate_firecracker_quote'` | 243 编译通过，35 passed |

---

## 24. Round 22 修复记录

### 触发条件

继续从产品化后续 gap 表修复 `FG-009`：告警中心只有内存规则引擎和从 `AuditLog` 反推的列表，没有独立告警实体、去重、通知投递状态、确认/解决处置流。该缺口不依赖真实硬件，属于可软件闭环的产品能力。

### 已完成

1. 告警持久化模型：
   - 新增 `AlertRecord`，包含 `dedup_key`、`alert_type`、`severity`、`status`、`occurrence_count`、`first_seen_at`、`last_seen_at`。
   - 增加 `acknowledged_by/at`、`resolved_by/at`、`resolution_note`。
   - 增加 `notification_status` 和 `notification_results`，保留通知投递审计。

2. 告警中心服务：
   - 新增 `AlertCenterService.ingest_alerts()`。
   - 基于 alert type、session、user、resource/rule 生成稳定 dedup key。
   - 重复告警合并为同一记录并递增 occurrence count。
   - 已解决或已确认告警再次出现时自动重新打开。

3. 通知投递闭环：
   - 新增 `ALERT_WEBHOOK_URLS`、`ALERT_NOTIFICATION_TIMEOUT_SECONDS`、`ALERT_NOTIFICATION_RETRIES` 配置。
   - 支持 webhook 投递，并记录每个 target 的状态码、是否成功、attempt 和投递时间。
   - 未配置通知通道时明确标记 `not_configured`，不伪造发送成功。

4. 监控 API：
   - `/monitoring/alerts` 优先返回持久化告警，支持 status/severity/alert_type 过滤。
   - 兼容旧 audit log fallback，避免升级期间前端空白。
   - 新增 `/monitoring/alerts/{id}/acknowledge` 和 `/monitoring/alerts/{id}/resolve`。

5. 测试补齐：
   - 新增 `tests/test_alert_center.py` 覆盖持久化去重、确认/解决/重新打开、webhook 投递记录、API 列表与处置流。
   - 保留 `tests/test_alert_engine.py` 的规则引擎测试，规则层和持久化层职责分离。

### 验证

| 命令 | 结果 |
|---|---|
| `.venv/bin/python -m compileall -q app tests alembic` | 本机通过 |
| `ssh root@172.22.4.243 'cd /root/cds-sandbox-codex && /root/.local/bin/uv run python -m compileall -q app tests alembic && timeout 240s /root/.local/bin/uv run python -m pytest -q tests/test_alert_engine.py tests/test_alert_center.py'` | 243 编译通过，29 passed |

---

## 25. Round 23 修复记录

### 触发条件

继续从产品化后续 gap 表修复 `FG-010`：非结构化/多媒体 pipeline 虽然已有 OCR/ASR/video/document 脚本形状，但产品闭环仍缺少任务级重试、阶段状态、加密产物清单、输出审查和 DICOM 入口。远端聚焦测试还暴露 L3 `BwrapAdapter` 对 243 的 `PR_SET_SECCOMP: Invalid argument` 不会重试，导致 API 路径失败。

### 已完成

1. 非结构化任务状态：
   - `PipelineTask` 增加 `options/max_retries/retry_count/attempts/stage_status/artifacts`。
   - `execute_task()` 记录 workspace/input/sandbox_execution/artifact_encryption/output_review 阶段。
   - sandbox 执行失败可按 `max_retries` 自动重试，保留每次 attempt 的 exit code、duration 和截断状态。

2. 加密 artifact manifest：
   - 输出目录所有非 symlink 文件通过 `StorageService.upload()` envelope 加密。
   - 返回 artifact manifest：相对路径、加密 storage path、SM3 checksum、SHA-256、content type、key id。
   - 拒绝 symlink 或逃逸 output dir 的 artifact，失败关闭。

3. 输出审查：
   - 对 result metadata 和文本 artifact 片段调用统一 `OutputInspector`。
   - 审查报告写入 `output_review`，critical DLP 发现会阻断释放，但 artifact 仍只以加密引用留存。
   - API 返回保持兼容，新增 `stage_status/retry_count/attempts/artifacts/output_review`。

4. DICOM 与 API 入口：
   - 新增 `dicom` 任务类型和 DICOM tag strip 脚本。
   - 未安装 `pydicom` 时明确返回 `partial_success`，不伪造清洗成功。
   - `/data-pipeline/upload-and-process` 和 `/process-path` 支持 `max_retries` 与 `dicom`。

5. L3 bwrap seccomp 兼容：
   - `BwrapAdapter` 增加 `_seccomp_retry_needed()`。
   - 兼容 `EINVAL`、`Invalid argument`、`PR_SET_SECCOMP` stderr，243 上可自动禁用 seccomp 重试。

### 验证

| 命令 | 结果 |
|---|---|
| `.venv/bin/python -m compileall -q app tests alembic` | 本机通过 |
| `ssh root@172.22.4.243 'cd /root/cds-sandbox-codex && /root/.local/bin/uv run python -m compileall -q app tests alembic && timeout 240s /root/.local/bin/uv run python -m pytest -q tests/test_data_pipeline.py tests/test_data_processing.py tests/test_sandbox_runtime.py'` | 243 编译通过，65 passed |

---

## 26. Round 24 修复记录

### 触发条件

继续检查 `FG-011` 训练编排软件侧能力时发现：`TrainingPipelineManager.split_dataset()` 使用 `random.shuffle()`，同一数据集每次运行可能得到不同 train/validation 切分。训练任务需要可审计、可回放，随机切分会导致指标、MIA 检测和故障复现实验不稳定。

### 已完成

1. 可复现数据切分：
   - `split_dataset()` 改为按 record 内容和 seed 计算 SHA-256 稳定排序。
   - 默认 seed 固定，重复运行、输入顺序变化时保持同一切分。
   - 调用方可传入显式 seed，生成另一个可复现切分。

2. 测试补齐：
   - 新增重复调用一致性测试。
   - 新增显式 seed 可复现且可改变切分结果测试。
   - 新增 RAG `chunking` 阶段枚举值断言。

3. RAG 阶段命名：
   - `PipelineStage.CHUNKING` 从误拼的 `chunkding` 修正为 `chunking`。
   - 保持 RAG pipeline 阶段名与规格、前端状态和 API 输出一致。

### 验证

| 命令 | 结果 |
|---|---|
| `.venv/bin/python -m compileall -q app tests alembic` | 本机通过 |
| `ssh root@172.22.4.243 'cd /root/cds-sandbox-codex && /root/.local/bin/uv run python -m compileall -q app tests alembic && timeout 180s /root/.local/bin/uv run python -m pytest -q tests/test_training_pipeline.py'` | 243 编译通过，33 passed |

---

## 27. Round 25 修复记录

### 触发条件

继续扫描产品代码中的薄实现时发现：`CDCAgent.start_connector()/pause_connector()/stop_connector()` 只修改内存状态，注释仍写“生产环境调用 Kafka Connect REST”。这会导致 CDC 配置生成、事件生产已有软件形状，但 connector 生命周期无法对接真实 Kafka Connect 控制面。

### 已完成

1. Kafka Connect 配置：
   - 新增 `CDC_KAFKA_CONNECT_URL`。
   - 新增 `CDC_KAFKA_CONNECT_TIMEOUT_SECONDS`。

2. Connector lifecycle hook：
   - 生成 Debezium / ClickHouse sink config 时保存 connector payload。
   - 配置 Kafka Connect URL 时，`start_connector()` 调用 `PUT /connectors/{name}/config` 做 upsert。
   - `pause_connector()` 调用 `PUT /connectors/{name}/pause`。
   - `stop_connector()` 调用 `DELETE /connectors/{name}`，404 视为已停止。
   - REST 失败时失败关闭，`start_connector()` 将状态标记为 `failed`。

3. 本地 fallback：
   - 未配置 Kafka Connect URL 时继续保持原本内存状态路径，便于单测和无外部环境部署。
   - REST 使用标准库 `urllib`，不引入额外依赖。

4. 测试补齐：
   - 覆盖 start connector upsert payload。
   - 覆盖 REST 失败标记 failed。
   - 覆盖 pause/stop REST 调用路径。

### 验证

| 命令 | 结果 |
|---|---|
| `.venv/bin/python -m compileall -q app tests alembic` | 本机通过 |
| `ssh root@172.22.4.243 'cd /root/cds-sandbox-codex && /root/.local/bin/uv run python -m compileall -q app tests alembic && timeout 180s /root/.local/bin/uv run python -m pytest -q tests/test_cdc_agent.py'` | 243 编译通过，11 passed |

---

## 28. Round 29-31 产品化安全证据修复记录

### 触发条件

产品化安全复核发现：安全能力状态已经分散在配置、运行时探测和文档中，但缺少面向运营/监管的统一披露；同时沙箱会话的 policy hash、attestation、key id、输出签名和审计摘要分散在多个接口/表中，客户试点和监管验收时无法直接下载归档。

### 已完成

1. 安全态势披露：
   - `app/api/monitoring.py` 新增 `GET /api/v1/monitoring/security-posture`。
   - 返回 TEE、GPU-TEE、HSM/Vault、链存证、SIEM、K8s policy、输出审查和 Debug 状态。
   - 输出 Go/Conditional Go/No-Go 发版建议；不返回 Secret 值。
   - 首页为监控读者增加“安全态势”卡片。

2. 会话证明包：
   - `app/api/sandbox_sessions.py` 新增 `GET /api/v1/sandbox-sessions/{session_id}/proof-bundle`。
   - 证明包包含 session 摘要、runtime proof level、attestation quote hash、resource/network/contract policy hash、session key id/KMS 元数据、输出审查摘要、审计事件摘要、稳定 evidence hash 和本次 bundle hash。
   - 执行后新增 `sandbox.output_inspected` 审计摘要，持久化 report hash、released output hash、签名、水印、阻断状态和发现数量。
   - 证明包明确排除 session key 明文、原始沙箱输出、raw attestation quote 和 Secret 配置。

3. 前端交付入口：
   - `cds-frontend/src/services/sandboxApi.ts` 增加 `SessionProofBundle` 与 `getProofBundle()`。
   - `cds-frontend/src/pages/Sandbox/SessionDetail.tsx` 增加证明包抽屉和 JSON 下载入口。

4. 发版文档：
   - `specs/productization-gap-analysis.md` 新增并修复 `P-GAP-012`、`P-GAP-013`。
   - `specs/product-security-release-review.md` 标注 `SD-001`、`SD-002` 已完成。
   - `specs/release-gate-checklist.md` 增加安全态势和会话证明包发版门禁。

5. 高危操作理由必填：
   - 新增 `HighRiskOperationRequest`，强制 `reason` 至少 8 个字符，可选 `ticket_id`。
   - KMS 密钥撤销、证书撤销、合约终止、连接器暂停/恢复/API Key 轮换必须提交 reason。
   - 前端新增统一高危操作确认弹窗，相关页面均要求输入理由。
   - 审计 detail 记录 reason/ticket_id，支持发版门禁抽样检查。

### 验证

| 命令 | 结果 |
|---|---|
| `.venv/bin/python -m compileall -q app alembic` | 本机通过 |
| `cds-frontend npm run build` | 本机通过 |

---

## 29. 最终验证状态

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
| Round 7 前端构建/单测 | 通过 | `npm run build`；3 files passed, 12 tests passed |
| Round 8 前端构建/单测 | 通过 | `npm run build`；3 files passed, 12 tests passed |
| Round 8 聚焦单测 | 通过 | 20 passed |
| Round 9 前端构建/单测 | 通过 | `npm run build`；3 files passed, 12 tests passed |
| Round 9 聚焦单测 | 通过 | 7 passed |
| Round 10 前端构建/单测 | 通过 | `npm run build`；3 files passed, 12 tests passed |
| Round 10 聚焦单测 | 通过 | 10 passed |
| Round 11 前端构建/单测 | 通过 | `npm run build`；3 files passed, 12 tests passed |
| Round 11 聚焦单测 | 通过 | 字段暴露 4 passed；字段/网关套件 38 passed |
| Round 12 前端构建/单测 | 通过 | `npm run build`；3 files passed, 12 tests passed |
| Round 12 聚焦单测 | 通过 | 连接器/合约约束 8 passed；相关回归 41 passed |
| Round 13 前端构建/单测 | 通过 | `npm run build`；3 files passed, 12 tests passed |
| Round 13 聚焦单测 | 通过 | 生命周期/合约/字段/沙箱/connector 74 passed；SecureDuckDB 27 passed；输出控制 23 passed |
| Round 14 聚焦单测 | 通过 | 数据资源/输出控制/字段暴露/产品版本 48 passed；auth/federation/security 144 passed；warning 触发用例 3 passed；rate limiting 17 passed |
| Round 15 编译/聚焦单测 | 通过 | 本机 compileall 通过；243 Python 3.12 compileall 通过；runtime/K8s/session/code-scanning 目标测试 8 passed |
| Round 16 编译/聚焦单测 | 通过 | 本机 compileall 通过；本机 K8s 目标测试 5 passed；243 Python 3.12 compileall 通过；243 K8s 目标测试 5 passed |
| Round 17 编译/聚焦单测 | 通过 | 本机 compileall 通过；本机 K8s lifecycle/API schema 目标测试 15 passed；243 Python 3.12 compileall 通过；243 目标测试 15 passed；前端 build 通过 |
| Round 19 编译/聚焦单测 | 通过 | 本机 compileall 通过；本机 K8s allowlist/kubeconfig 目标测试 13 passed；243 Python 3.12 compileall 通过；243 目标测试 13 passed |
| Round 20 编译/聚焦单测 | 通过 | 本机 compileall 通过；243 Python 3.12 compileall 通过；FederatedRuntime/Firecracker/Alert/DataProcessing 聚焦测试 102 passed |
| Round 21 编译/聚焦单测 | 通过 | 本机 compileall 通过；243 Python 3.12 compileall 通过；TEE capability/L1 runtime/Firecracker software quote 聚焦测试 35 passed |
| Round 22 编译/聚焦单测 | 通过 | 本机 compileall 通过；243 Python 3.12 compileall 通过；AlertEngine/AlertCenter 聚焦测试 29 passed |
| Round 23 编译/聚焦单测 | 通过 | 本机 compileall 通过；243 Python 3.12 compileall 通过；DataPipeline/DataProcessing/SandboxRuntime 聚焦测试 65 passed |
| Round 24 编译/聚焦单测 | 通过 | 本机 compileall 通过；243 Python 3.12 compileall 通过；TrainingPipeline 聚焦测试 33 passed |
| Round 25 编译/聚焦单测 | 通过 | 本机 compileall 通过；243 Python 3.12 compileall 通过；CDCAgent 聚焦测试 11 passed |
| Round 29-31 编译/构建 | 通过 | 本机 `.venv/bin/python -m compileall -q app alembic` 通过；`cds-frontend npm run build` 通过 |
| 非 e2e 单测 | 通过 | 2041 passed, 1 skipped, 91 deselected；1 个延迟回收测试 warning |
| e2e | 未运行 | 按当前任务要求暂不跑 e2e |

测试说明：受限沙箱内 `aiosqlite.connect(":memory:")` 会挂住；已通过最小脚本验证该问题来自执行沙箱限制。因此涉及 aiosqlite 的单元测试在沙箱外、带 `timeout` 运行。
最终非 e2e 仍有 1 个 `PytestUnraisableExceptionWarning` 风格的延迟回收测试 warning；`tests/test_rate_limiting.py` 单独运行 17 passed 且无 warning，已确认不对应当前业务 active gap。

---

## 30. 后续循环规则

1. 任一测试失败，新增或重开 active gap，并记录失败命令和失败点。
2. 修完一轮后必须更新本文件的 Active Gap 表和 Round 记录。
3. 对明显不合理或无法单测验证的硬件/基础设施规格，直接做合理化实现并记录裁剪理由。
4. e2e、真实硬件、真实链节点验证不进入当前完成标准。
