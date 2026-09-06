# AI 数据沙箱系统 · 实现差距审查与修复计划

> 审查日期：2026-09-06
> 审查基线：《数隐山海可信数据空间》PPT 20260901 版 P17–P20（AI 数据沙箱系统设计）
> 审查范围：`app/` 后端实现（约 93 个 service、24 个 API router），对照 P18 架构分层（策略控制层 / 隐私计算层 / AI 任务层）、P20 沙箱隔离与生命周期，以及可信数据空间对沙箱的三原则：**原始数据不出域、权限随任务生效、全过程可审计**。
> 方法：定向代码阅读 + 全局 mock/stub 普查。基于静态阅读，未运行全部 123 个测试文件。

---

## 一、总体结论

项目骨架完整：合约-策略-沙箱-任务-输出-审计的链路全部有代码，多级沙箱运行时（L0 bwrap / K8s / Firecracker / TEE adapter）、SM4-GCM 存储加密、SM2 合约双签、DP 预算账本、模型水印等是真实现。

但对照 P18 设计与可信数据空间要求，存在 **4 个高危闭环缺口**（权限回收不闭环、dev 沙箱绕过合约、密钥分发验证可选、输出网关非强制）和 **3 个能力性缺失**（同态加密/MPC 计算缺失、RAG 与智能体缺失、链上存证为本地模拟）。

---

## 二、问题清单

### A. 策略控制层（P18：身份与授权 / 分类分级 / 用途与期限 / 审计存证）

| # | 严重度 | 问题 | 证据 | 说明 |
|---|--------|------|------|------|
| A1 | **高** | **"权限随任务生效"不闭环：合约终止/到期不回收运行中会话与已下发资源** ✅已修复(2026-09-06) | `app/services/contract_service.py:173-184` | `terminate()` 仅把状态改为 TERMINATED 并 flush，不级联终止该合约下的 sandbox session、不吊销 policy bundle、不销毁已分发 session key。合约终止后买方已有会话可继续运行 |
| A2 | **高** | **会话过期清理无调度，仅手动触发** ✅已修复(2026-09-06) | `app/api/sandbox_sessions.py:1241`（`POST /cleanup-expired`，注释"Called by background scheduler or admin"）；`app/main.py` lifespan 只启动 quota/task worker/KMS TTL/CDC，未注册 session 清理循环 | 过期会话（及容器、密钥、网络策略）会一直存活到管理员手动调用，产生孤儿沙箱与资源泄漏 |
| A3 | 高 | **dev 沙箱绕过合约授权（原始数据不出域在该路径失效）** ✅已修复(2026-09-06) | `app/api/dev_sandbox.py:32-60` | `CreateDevSessionRequest` 无 contract_id；提供 `data_product_id` 即自动解密数据资源加载进沙箱，只校验登录用户，不校验合约/产品覆盖关系/DP 预算 |
| A4 | 中 | **合同无"用途限定"（purpose limitation）字段与执行** | `app/models/contract.py`（全文件无 purpose 字段；仅 `terms` JSON 泛型） | P18"用途与期限"中用途维度缺失：任务提交时不声明/不校验用途，无法阻止"约定用于统计、实际用于成员推断"类滥用 |
| A5 | 中 | **分类分级仅一个 4 级字符串，未落到字段级策略执行** | `app/models/data_product.py:48`（`security_level: public/internal/confidential/secret`）；分级相关代码只在 `rls_engine.py`/`clickhouse_schema.py` 内部使用 | P18 要求"供方分类分级→授权入表"：缺少字段级分级元数据（如身份证号=3级→强制脱敏）与分级→策略的自动映射 |
| A6 | 低 | OPA 不可用时的策略执行回退语义需明确 | `app/services/opa_client.py`（push/evaluate 均捕获异常返回 False/None） | 需确认"OPA 失败=拒绝（fail-closed）"还是回退本地 evaluator，避免策略静默失效 |

### B. 隐私计算层（P18：TEE / 同态加密·MPC / 加密传输）

| # | 严重度 | 问题 | 证据 | 说明 |
|---|--------|------|------|------|
| B1 | **高** | **KMS 密钥分发的 TEE 证明验证是可选的** ✅已修复(2026-09-06) | `app/services/kms_service.py:203-250`（`distribute_key`："if attestation or tee_quote: verify..."，不提供即直接返回密钥） | 任何调用方不带 attestation 即可取得会话密钥，P0-9 防护形同虚设。应强制：无有效证明拒绝分发 |
| B2 | 高 | **TEE 全部为软件模拟，远程证明无外部信任根** | `app/services/tee_simulator.py`（全文件）；`app/services/remote_attestation.py:161-264`（"Simulate SGX quote"，measurement=`sm3_hash(f"sgx-enclave-{quote_id}")` 自指生成、本地密钥自签自验；`AttestationPolicy.allowed_measurements` 默认空=全部放行，`remote_attestation.py:122`） | P18"可信执行环境（TEE）"当前只有模拟器形态。模拟可用于 CI，但必须在部署清单/API 响应中显式标注 `simulation=true` 并在生产禁用（部分路径已有 `is_simulation()`，覆盖需核对） |
| B3 | 中 | **KMS wrapped keys 存进程内存，重启即丢** ✅已修复(2026-09-06) | `app/services/kms_service.py`（`self._wrapped_keys` dict；`rotate_key`/`generate_session_key` 只写内存） | 会话密钥无法在进程重启后解密历史数据/恢复会话；多副本部署不共享。需持久化到 DB（KeyMetadata 表已有但未用于 wrapped 存储）或 Vault |
| B4 | **高** | **同态加密完全缺失，MPC 仅 Shamir 秘密分享（无计算协议）** | 全局 grep `paillier/ckks/homomorphic` 无结果；`app/services/mpc_service.py`（只有 split/reconstruct） | P18"同态加密/安全多方计算"两项均未落地。mpc API 当前只支持密钥托管场景，不支持多方联合计算 |
| B5 | 中 | HSM/Vault 为可选依赖，默认软件 fallback 且静默 | `app/services/hsm_adapter.py`（Vault 认证失败→warning→软件 fallback）；`contract_service.py` 平台见证签名同样 HSM 失败回退软件密钥 | 生产环境需配置"禁用软件 fallback"开关，否则加密强度静默降级 |
| B6 | 低 | 加密传输：mTLS/Tongsuo 适配存在，但沙箱-平台间通道未全程强制 | `app/services/mutual_tls.py`、`Tongsuo/` | 需确认 sandbox agent/connector 通信是否默认走 mTLS（当前 `get_tls_context` 仅提供参数，未强制） |

### C. AI 任务层（P18：隐私训练 / 隐私推理 / RAG 检索 / 智能体执行；P19：GPU/ASIC 加速）

| # | 严重度 | 问题 | 证据 | 说明 |
|---|--------|------|------|------|
| C1 | 中 | **隐私训练为"真训练+模拟回退"双模，模拟分支静默** | `app/services/cpu_trainer.py:147-155`（torch 不可用→`_simulate_fallback`）；`llm_sft_runtime.py:330-354` | torch 未安装时训练任务返回的是模拟结果。生产环境应启动时检测并拒绝训练任务（fail-closed），或强制标注 simulated |
| C2 | 中 | MIA 检测是确定性代理而非真影子模型 | `app/services/llm_sft_runtime.py:200-221`（`_sample_confidence`："Deterministic proxy for shadow-model confidence"） | 记忆风险检测结论不可信，作为训练门禁（超过阈值抛错）会给出虚假保证 |
| C3 | **高** | **RAG 检索未实现** | 全局 grep `rag/vector_store/faiss/chroma/embedding` 无命中（仅个别注释） | P18 AI 任务层四项之一完全缺失：无向量库、无沙箱内检索、无语料不出域的 RAG 链路 |
| C4 | **高** | **智能体执行未实现**：`cdc_agent` 是 CDC 数据同步（Debezium/Kafka），非 AI 智能体 | `app/services/cdc_agent.py`（CDCConnectorConfig/produce_event）；无供方/需方智能体运行时 | P18"供方智能体/需方智能体/智能体执行"缺失。当前"agent"命名易造成已实现错觉 |
| C5 | 高 | **隐私推理未实现**：无推理服务/端点 | grep `def .*(inference|predict)` 无命中 | P18"隐私推理"缺失；训练出的模型无受控推理出口，也没有推理输出保护路径 |
| C6 | 中 | GPU 加速（P19）仅有 gpu_tee_simulator，无 TensorFHE/算子库集成 | `app/services/gpu_tee_simulator.py`、`gpu_tee_runtime.py` | P19 宣称的 GPU/ASIC 加速能力未落地，架构上 runtime 可插拔但无加速实现 |

### D. 沙箱生命周期与计算隔离（P20：启动判定→策略执行→状态观察→恢复或清理）

| # | 严重度 | 问题 | 证据 | 说明 |
|---|--------|------|------|------|
| D1 | 高 | 同 A2：过期会话无自动回收（孤儿容器/密钥/网络策略） ✅已修复(2026-09-06) | `app/api/sandbox_sessions.py:1241`；`app/main.py` lifespan | 与 A2 合并修复 |
| D2 | 中 | **P20"快照回滚/恢复与清理"未实现**：无沙箱文件系统快照、无状态回滚 | grep `snapshot/rollback`（仅 `secure_checkpoint.py` 为训练 checkpoint，非沙箱快照） | P20 生命周期闭环的最后一环缺失；任务失败后无法回滚到初始态，只能销毁重建 |
| D3 | 中 | K8s 适配器依赖宿主机 kubectl 子进程，非 client-go/python client | `app/services/k8s_sandbox.py:147-149`（"kubectl not found — K8s adapter unavailable"） | 功能可用但部署脆弱；与 helm/k8s 目录的部署形态需统一（进程内 kubectl 需要挂载 kubeconfig 与 RBAC） |
| D4 | 低 | L0/L2 的 seccomp 失败回退路径（`cmd_no_seccomp`）会降低隔离强度 | `app/services/sandbox_runtime.py:286,582` | seccomp 加载失败时静默降级继续执行，应改为可配置（生产 fail-closed） |

### E. 输出脱敏与效果验证（P18：输入受控/输出脱敏与效果验证）

| # | 严重度 | 问题 | 证据 | 说明 |
|---|--------|------|------|------|
| E1 | **高** | **输出审查网关非强制路径**：`OutputGateway` 仅被 `output-control/process-output-gateway` 显式调用 ✅已修复(2026-09-06) | `app/api/output_control.py:292-319`；`app/api/sandbox_tasks.py`（`get_task_result` 不经过网关，仅检查 worker 填充的 `inspection_report.passed` 标志，`sandbox_tasks.py:304-305`） | 沙箱任务结果可通过 `GET /sandbox-tasks/{id}/result` 直接取回，DP 扣减/DLP 扫描/水印取决于 worker 是否自愿填充 inspection_report——存在绕过面 |
| E2 | 中 | PII NER 双层实为"正则+正则"，无真实 NER 模型 | `app/services/pii_ner.py:110-138`（`NERPatterns` 全部是 `re.compile`） | 姓名/地址/机构识别靠正则，漏检率高；建议接入 LAC/HanLP 轻量中文 NER |
| E3 | 低 | DP 预算账本完善（真扣减），但 epsilon 数值由合约侧自定，缺下限校验口径 | `app/services/dp_budget.py:113-115`（consume 不足返回 False ✓） | 建议增加全局最小噪声规模/单次 epsilon 上限策略 |
| E4 | 中 | "效果验证"（P18：结果正确性/质量验证）未见系统化实现 | `app/services/result_verifier.py` 含 simulated 标记，未接入主链路 | 输出审查后有签名/存证，但缺少"结果可验证"（如查询可复算性/抽样复核）机制 |

### F. 审计存证与中控平台（P18：消息路由 / 策略下发 / 交易审计）

| # | 严重度 | 问题 | 证据 | 说明 |
|---|--------|------|------|------|
| F1 | **高** | **链上存证为本地模拟：FISCO BCOS / AntChain 适配器是"本地可验证哈希链回退"，无真实链上交易** | `app/services/blockchain_adapter.py:89-127`（"FISCO-shaped local hash chain"）、`:157-194`；`contracts/AuditRegistry.sol` 未接 web3 | P18"审计存证"与三原则"全过程可审计"的可信根基缺失。本地哈希链防应用内篡改，但服务器被攻破即可重写。至少要：真链适配接入 + 本地模式在存证凭证中显式标注 backend=local |
| F2 | 中 | 联邦身份 JWT 密钥有静态常量回退 | `app/services/federation_connector.py:42-50`（非 DEBUG 才强制校验，`_LOCAL_FEDERATION_JWT_KEY` 常量） | 跨空间互通的令牌签名密钥若走回退分支为公开常量，可伪造跨空间令牌；应强制走 SM2 IdentityProvider 或 HSM |
| F3 | 中 | 审计 SM2 签名密钥在 HSM 不可用时回退软件密钥 | `app/services/audit_service.py:75-117`（与 contract_service 同模式） | 审计防篡改强度随部署静默降级，需生产开关 |
| F4 | 低 | 策略下发到 connector 的执行回执/版本核对未见强制确认 | `app/models/policy_bundle.py`、`app/api/connectors.py` | P18 中控"策略下发"应闭环：下发→回执→版本比对→不一致告警，当前只 push 不验执 |

---

## 三、修复计划

> **P0 执行状态：已全部完成（2026-09-06）**，落实于 `docs/ai-sandbox-gap-remediation-plan.md`，进度记入 `specs/SS-gap-analysis.md` Round 32（G-126~G-130），含单测。

### P0 · 安全闭环（1–2 周，先行）

1. **会话生命周期调度器**（修 A2/D1）
   - `main.py` lifespan 增加后台循环（复用 `kms_service._ttl_cleanup_loop` 模式），周期调用 `cleanup_expired_sessions` 逻辑（抽成 service 函数，不依赖 admin HTTP 调用）。
   - 补测试：过期会话→容器终止→密钥销毁→网络策略移除→配额释放。

2. **合约终止/到期级联回收**（修 A1）
   - `contract_service.terminate()` 与定时合约到期检查中：查询该合约下非终态 session → 逐个走状态机终止；吊销 policy bundle 与 OPA 策略；销毁已分发 session key；审计存证。

3. **dev 沙箱接入合约校验**（修 A3）
   - `dev_sandbox.py`：`data_product_id` 非空时强制校验有效合约覆盖 + DP 预算 + 产品状态；或提供配置项 `DEV_SANDBOX_REQUIRE_CONTRACT=true`，生产默认拒绝无合约 dev 会话。

4. **KMS 密钥分发强制证明验证**（修 B1）+ **wrapped keys 持久化**（修 B3）
   - `distribute_key` 去掉可选分支：无有效 TEE 证明（或配置显式 `ALLOW_UNATTESTED_KEY_DISTRIBUTION`，仅 dev）一律拒绝。
   - `_wrapped_keys` 写入 `KeyMetadata`（wrapped 载荷列），启动时懒加载。

5. **沙箱任务结果强制过输出网关**（修 E1）
   - `get_task_result` / `task_pipeline` 完成回调统一调用 `output_gateway.process`（按合约 `OutputPolicy`），inspection 报告落库后才允许取结果；移除对 worker 自愿填充的依赖。

### P1 · 设计对齐（2–4 周）

6. **合约用途限定**（A4）：Contract 增加 `purpose` / `purpose_scope` 字段（进合约签名原文与存证），任务提交时声明用途并与合约比对，不匹配拒绝。
7. **字段级分类分级→策略联动**（A5）：DataResource/DataProduct 增加字段分级元数据，`policy_compiler` 按分级自动生成脱敏/掩码/禁出规则，接入 `field_acl`/`rls_engine`。
8. **链上存证真实现**（F1）：接入 FISCO BCOS Java/Python SDK 或通过 connector 服务代理发链；未配置真链时，AnchorResult 显式 `backend=local-hashchain` 并在前端/合规报告标注，禁止冒充"已上链"。
9. **真实中文 NER**（E2）：接入 LAC/HanLP，正则层保留为快速通道。
10. **训练 fail-closed 与真 MIA**（C1/C2）：torch 缺失时启动拒绝训练任务；MIA 用真实影子模型（小规模）或降级为"不可评估"而不是确定性代理。
11. **隐私推理与 RAG**（C3/C5）：最小闭环——沙箱内向量库（DuckDB VSS/FAISS），语料加密入库、检索-推理在沙箱 runtime 内执行、结果走输出网关；推理端点复用 session/task 生命周期与审计。
12. **seccomp/TEE 模拟显式化**（B2/D4/B5）：增加生产配置开关 `ALLOW_SIMULATION=false` / `SECCOMP_FALLBACK=false` / `HSM_SOFTWARE_FALLBACK=false`，默认生产 fail-closed。

### P2 · 能力补齐（1–2 月，按 roadmap）

13. **同态加密/MPC 计算**（B4）：评估复用 SecretFlow（SPU HEU）做 HE 统计与两方 MPC，`mpc_service` 保留 Shamir 用于密钥托管。
14. **智能体执行框架**（C4）：定义供方/需方智能体运行时（沙箱内受限执行 + 工具白名单 + 全程审计），与任务层复用 code_scanner/网络策略。
15. **沙箱快照与回滚**（D2）：基于 workspace 目录快照（L0/bwrap 可用 overlayfs/copy-up，K8s 用 VolumeSnapshot），接入 session 状态机。
16. **TEE 硬件路径**（B2）：Gramine（SGX）/ CoCo（SEV-SNP）适配 `TEEAdapter._provision_hardware`，远程证明接外部信任根（Intel PCS / AMD KDS），度量白名单强制非空。
17. **K8s 适配器改用官方 Python client**（D3）。
18. **GPU 加速 runtime 插件**（C6）：对齐 P19，预留 `AccelerationRuntime` 扩展点。

---

## 四、已确认的真实现（无需重做）

- SM4-GCM 全链路存储加密、SM2/SM3 国密签名（gmssl）与合约三方签名（供/需/平台见证）
- 合约→OPA Rego 策略编译与下发（`policy_compiler`/`opa_client`）
- 多级沙箱运行时：L0 bwrap+seccomp+cgroup / K8s Pod+NetPol / Firecracker / TEE adapter，K8s 网络策略真实下发与回收
- DP 预算持久化账本（分配/扣减/不足拒绝）
- 模型 LSB 权重水印（真嵌入+验证）、Merkle 审计链、审计 SM2 签名、PG append-only 后端
- 会话/任务状态机、租户配额、KMS TTL 分发清理循环
