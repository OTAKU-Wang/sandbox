# CDS 任务实现状态对比

> 更新时间：2026-06-09
> 对比基准：`docs/implementation-tasks.md` (50 项) vs 实际代码审计 + 测试验证
> QA 验证人：@测试工程师

---

## 一、P0 安全关键 (11 项) — 全部完成 ✅

| # | 任务 | 状态 | 验证方式 |
|---|------|------|---------|
| P0-1 | X.509 DER 证书 + 吊销级联 | **✅ DONE** | 真实 ASN.1 DER 编码，SM2-with-SM3 签名 |
| P0-2 | SM2 HSM 签名 | **✅ DONE** | HSM 优先 + 软件 SM2 降级 + logger.warning |
| P0-3 | AES-GCM AEAD | **✅ DONE** | `cryptography` 库 AES-128-GCM，tamper 检测 |
| P0-4 | 区块链持久化 | **✅ DONE** | PGAppendOnlyAdapter DB 持久化；旧版 BlockchainService 无链节点时本地 hash-chain fallback |
| P0-5 | CODE_SCANNING 集成 | **✅ DONE** | task_pipeline handler + CodeScanner + audit |
| P0-8 | DEK SM2 加密 | **✅ DONE** | 双层保护：KEK wrapping + SM2 cert 加密 |
| P0-9 | TEE Quote 验证 | **✅ DONE** | AttestationService 集成到密钥分发；本地 SGX/SEV/Firecracker quote 带签名与篡改检测 |
| P0-10 | 证书吊销级联 | **✅ DONE** | _cascade_revoke → terminate_sessions_by_key |
| P0-11 | 结果 FULL 验证失败关闭 | **✅ DONE** | hash + SM2 signature + signed attestation quote 三者必需 |
| P0-12 | 证书签名占位清除 | **✅ DONE** | 签名失败不再写零签名 PEM；父 CA 私钥缺失时拒绝签发 |
| P0-13 | 统一输出安全出口 | **✅ DONE** | TaskPipeline、开发沙箱、OutputGateway/契约网关强制审查；critical 阻断，raw output 不进入最终结果 |

**P0 小结**: 11/11 DONE ✅

---

## 二、P1 功能完整 (17 项) — 全部完成 ✅

| # | 任务 | 状态 | 说明 |
|---|------|------|------|
| P1-1 | 多模式 Rego 策略模板 | **✅ DONE** | 4 种场景模板：structured_query/llm_sft/product_dev/application |
| P1-2 | 任务 Pipeline 统一 | **✅ DONE** | TaskPipeline: queue + scheduler + state machine；PREPARING handler + 缺 handler 失败关闭 |
| P1-3 | DuckDB 策略引擎 | **✅ DONE** | `_attach_policy()` 自动加载合约 policy bundle |
| P1-4 | 会话配额初始化 | **✅ DONE** | `quota_manager.reset(session.id)` 在 contract_fulfillment |
| P1-5 | 合约 Redis 原子配额 | **✅ DONE** | quota_manager.py Lua 脚本 + contract_fulfillment 调用 |
| P1-6 | 节点选择加权随机 | **✅ DONE** | Top-3 weighted-random jitter 防热区 |
| P1-7 | 合约区块链锚定 | **✅ DONE** | sign() → blockchain_service.anchor_to_blockchain() |
| P1-8 | PII NER ML 模型 | **✅ DONE** | `pii_ner.py` 双层检测（regex + NER），bert-base-chinese-pii-ner |
| P1-9 | 流式审查 mitmproxy | **✅ DONE** | `app_output_proxy.py` CDSOutputInspector + streaming_inspector |
| P1-10 | KMS 模型去重 | **✅ DONE** | key_metadata.py 单一模型，kms_service.py 使用它 |
| P1-11 | DP 预算物化视图 | **✅ DONE** | `dp_budget_status` 物化快照表 + Ledger 写穿透维护 |
| P1-12 | DuckDB md5→SM3 | **✅ DONE** | SM3 UDF + sha256 fallback |
| P1-13 | 网关真实加载加密产品数据 | **✅ DONE** | encrypted_storage_path → StorageService decrypt → CSV/JSON/JSONL → SecureDuckDB |
| P1-14 | GPU-TEE 本地加密运行时 | **✅ DONE** | AEAD channel、encrypted allocation、signed local attestation report |
| P1-15 | FISCO/AntChain 本地可验证链 | **✅ DONE** | 无外部节点时使用 hash chain + block_number + confirmed |
| P1-16 | 视觉/多模态预处理非直通 | **✅ DONE** | DICOM strip、车牌 mosaic、face blur/normalize 审计标记 |
| P1-17 | LLM SFT PII/MIA/水印真实化 | **✅ DONE** | 训练入口脱敏、确定性 MIA、水印嵌入并验证；SFT API 本地完成并落 checkpoint |
| P1-18 | DP/配额失败关闭 | **✅ DONE** | DP 预算查询异常拒绝；QuotaManager contract_limits 映射和本地 gateway usage fallback |
| P1-19 | 任务代码加密落库 | **✅ DONE** | SandboxTask code_content KMS envelope 加密；扫描/执行前解密，历史明文兼容 |

**P1 小结**: 19/19 DONE ✅

---

## 三、xfail 分布（0 项）🎉

所有 xfail 已收敛：
- Federation 信任/审计持久化 → 注入 DB factory 验证
- BlockchainAdapter 持久化 → 注入 DB factory 验证
- P0-5 审计日志 → 验证 `audit_service.log()` 已集成

---

## 四、待实现任务（无外部依赖）

无。

---

## 五、待实现任务（需安装依赖）

无。

---

## 六、P2 远期（9 项）

| # | 任务 | 状态 | 说明 |
|---|------|------|------|
| P2-3 | MPC 密钥分片 | **✅ DONE** | Shamir 真实实现 |
| P2-5 | 场景运行时工厂 | **✅ DONE** | SandboxMode 枚举 + SceneRuntimeFactory + 4 个运行时 |
| P2-6 | 训练管线 PyTorch | **✅ DONE** | CPUTrainer (torch 2.12.0+cpu) |
| P2-7 | CDC Kafka | **✅ DONE** | CDCEventProducer (aiokafka 0.14.0) |
| P2-8 | TLCP SM2 密码套件 | **✅ DONE** | TLCP 本地 CA 签发真实 DER PEM 证书和 EC private key PEM |
| P2-9 | Carlini 记忆检测 | **✅ DONE** | memorization_check.py + mia_probe.py |
| P2-10 | 模型权重水印 | **✅ DONE** | app/utils/watermark.py |
| P2-11 | 网络层流量控制 | **✅ DONE** | network_policy.py iptables + DNS 代理 |
| P2-12 | 视觉训练本地代理 | **✅ DONE** | 旧版 vision pipeline 和 vision/multimodal runtime 指标由实际 dataloader、标签分布、配对质量与稳定哈希驱动 |

**硬件/外部环境验证**：P2-1 L1 TEE (SGX), P2-2 GPU-TEE (GPU), P2-4 PG-in-TEE (SGX), 生产 FISCO/AntChain 节点、生产 ClickHouse/Kafka/Redis 可用性。当前软件路径均提供本地实现、模拟器或失败关闭语义。

---

## 七、测试基线

| 指标 | 数值 |
|------|------|
| 测试文件 | 108+ 个 |
| 通过 | 1988（非 e2e）；Round 5 聚焦 148 passed |
| 失败 | 0 |
| 跳过 | 1 |
| 预期失败 | 0 🎉 |
| 已知环境限制 | 受限沙箱内 aiosqlite 会挂住，单元测试需沙箱外带 timeout 运行 |

---

## 八、剩余缺口（Spec vs 实现）

| 类别 | 数量 | 说明 |
|------|------|------|
| 软件可修复 | 0 | 当前逻辑/单测范围内无 active gap |
| 硬件/外部环境验证 | 5 | SGX、GPU、PG-in-TEE、生产 FISCO/AntChain、生产中间件保持适配器/模拟器/替代实现 |
| **当前 active gap** | **0** | 已收敛 |

**Spec 实现率**: 当前逻辑/单测范围已收敛；硬件、外部中间件与 e2e 验证按后续阶段处理。
