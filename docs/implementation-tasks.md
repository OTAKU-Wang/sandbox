# CDS 密态沙箱系统 · 实现任务清单

> 生成日期：2026-06-05
> 最后更新：2026-06-05（三轮复核）
> 基于：[spec-implementation-analysis.md](./spec-implementation-analysis.md)
> 当前整体实现率：~70%（三轮复核）
> 目标：补齐 P0 安全关键 → P1 功能完整 → P2 高级场景

---

## 一、P0 安全关键任务（1 项剩余）

> 原 5 项中 4 项已完成。必须在任何生产部署前完成。

### ~~P0-1: 证书真实 X.509 签发 + 吊销级联~~ ✅ 已完成

**三轮复核确认**：`certificate_service.py` 已实现完整 ASN.1 DER 编码 + TBSCertificate 构造 + SM2 签名 + DER 解析器。吊销级联逻辑 `_cascade_revoke()` 已连通 DEK 暂停 + 会话终止。

**实现任务**：

1. **引入 `cryptography` 库**（替代纯 gmssl 手动 ASN.1 编码）
   - 文件：`requirements.txt`
   - 添加 `cryptography>=42.0.0`

2. **重写 `CertificateService.issue_certificate()`**
   - 文件：`app/services/certificate_service.py`
   - 使用 `cryptography.x509` 构造真实 X.509v3 证书
   - 扩展项：KeyUsage, ExtendedKeyUsage, SubjectKeyIdentifier, AuthorityKeyIdentifier
   - SM2 签名：先用 `gmssl` SM3 摘要 + SM2 签名，再嵌入 X.509 SignatureValue
   - 输出真实 PEM 格式（DER 编码 + base64 + PEM headers）

3. **重写 `CertificateService.verify_certificate()`**
   - 文件：`app/services/certificate_service.py`
   - 从 PEM 解析 X.509 结构
   - 验证签名链（CA → Intermediate → Leaf）
   - 检查有效期、CRL/OCSP 状态

4. **实现证书吊销级联逻辑**
   - 文件：`app/services/certificate_service.py`
   - `revoke_certificate()` 流程：
     - 更新证书状态为 REVOKED
     - 查询关联的 DEK（通过 `data_encryption_keys.certificate_id`）
     - 暂停关联 DEK 状态为 SUSPENDED
     - 查询活跃的密钥分发会话（`key_distributions` 表）
     - 终止关联的沙箱会话（调用 `sandbox_manager.terminate_sessions_by_key()`）
     - 写入审计事件

5. **添加 `key_distributions` 模型**
   - 文件：`app/models/key_distribution.py`（新建）
   - 字段：id, session_id, dek_id, tee_quote_hash, mpc_shard_k2_encrypted, session_key_enc, expires_at, status
   - 关联到 `app/main.py` 的 `create_all`

6. **添加密钥分发会话 TTL 清理**
   - 文件：`app/services/kms_service.py`
   - 在 `distribute_key()` 中设置 `expires_at`
   - 添加定时任务清理过期分发会话

**验收标准**：
- `openssl x509 -in cert.pem -text -noout` 能正确解析
- 吊销证书后关联 DEK 状态变为 SUSPENDED
- 吊销证书后关联沙箱会话被终止
- 过期分发会话被自动清理

**预计工时**：5 天

---

### ~~P0-2: SM2 HSM 内签名~~ ✅ 已完成

**三轮复核确认**：`crypto_service.sign_with_hsm()` HSM-first 模式已实现。`audit_service.py` 79-94 行 HSM 优先回退软件。Vault Transit 不支持 SM2 时自动降级为 SoftwareHSMAdapter。

---

### ~~P0-3: SM4-GCM 真实实现~~ ✅ 已完成

**三轮复核确认**：`column_encryption.py` 使用 `cryptography.hazmat.primitives.ciphers.aead.AESGCM` 真实 AEAD 加密。`deterministic_sm4.py` 同样使用 AESGCM。

---

### ~~P0-4: 区块链审计持久化~~ ✅ 已完成

**三轮复核确认**：`blockchain_adapter.py:180` PGAppendOnlyAdapter 已实现 SM3 哈希链 + DB 持久化 + 链完整性验证。`blockchain_service.py` 的 FISCO BCOS SDK 仍为桩，但 PG append-only 作为生产可用替代方案。

---

### P0-5: 任务 CODE_SCANNING 集成

**问题**：`task_worker.py` 跳过 CODE_SCANNING 阶段直接进入 RUNNING，用户代码未经扫描。

**规格来源**：SS-03 §3.2

**当前状态**：STUB — `code_scanner.py` 已实现但未集成到状态机

**实现任务**：

1. **在任务状态机中启用 CODE_SCANNING 阶段**
   - 文件：`app/services/task_state_machine.py`
   - 转换：PENDING → CODE_SCANNING → PREPARING → RUNNING
   - 当前直接 PENDING → RUNNING

2. **在 `task_worker.py` 中调用代码扫描**
   - 文件：`app/services/task_worker.py`
   - `_execute_task()` 开头调用 `code_scanner.scan_code(task.code)`
   - 扫描失败时转换到 FAILED 状态，记录原因
   - 扫描通过后继续 PREPARING → RUNNING

3. **扩展 `CodeScanner` 支持更多语言**
   - 文件：`app/services/code_scanner.py`
   - 当前仅支持 Python AST
   - 添加 SQL 语句白名单验证（已部分实现）
   - 添加 Shell 命令白名单（用于 ETL 脚本）

4. **添加扫描结果到审计日志**
   - 文件：`app/services/task_worker.py`
   - 记录扫描耗时、发现的违规项、最终判定

**验收标准**：
- 包含 `os.system("rm -rf /")` 的代码被拒绝执行
- 扫描结果写入审计日志
- 任务状态机转换完整

**预计工时**：2 天

---

## 二、P1 功能完整任务（5 项剩余）

> 原 10 项中 5 项已完成。

### ~~P1-1: 多模式 Rego 策略模板~~ ✅ 已完成

**三轮复核确认**：`policy_compiler.py` 已实现 5 种模板（generic + query + training + product_dev + app），通过 `compile_for_mode()` 分发。

---

### ~~P1-2: 任务 Scheduler/Queue/Worker 集成~~ ✅ 已完成

**三轮复核确认**：`task_pipeline.py` 作为统一编排器替代断开的三模块，实现 CODE_SCANNING → PREPARING → RUNNING → OUTPUT_INSPECTING 流水线。

---

### ~~P1-3: DuckDB 策略引擎集成~~ ✅ 已完成

**三轮复核确认**：`secure_duckdb.py` 的 `set_policy()` + `_check_policy()` 已连通 `policy_evaluator.evaluate()`。SM3 UDF 已注册替换 md5。SQL 标识符已转义。

---

### ~~P1-5: 合约履约 Redis 原子配额~~ ✅ 已完成

**三轮复核确认**：`quota_manager.py:44-58` 真实 Lua 脚本原子 GET→检查→INCRBY。

---

### ~~P1-6: 节点选择热区随机抖动~~ ✅ 已完成

**三轮复核确认**：`node_selector.py:148-163` top-3 加权随机选择 + 随机抖动。

---

### P1-4: 会话配额初始化

**问题**：配额按需创建，不从合约预填充。规格要求会话创建时初始化所有配额计数器。

**规格来源**：SS-02 §5.2

**当前状态**：PARTIAL — `reset()` 清除 Redis 键但不从合约预填充

**实现任务**：

1. **在会话创建时初始化配额**
   - 文件：`app/services/sandbox_session_service.py`（或 `sandbox_sessions.py` API）
   - `create_session()` 流程：
     - 读取关联合约的配额配置（`max_output_rows`, `dp_epsilon_budget` 等）
     - 初始化 Redis 计数器：`cds:quota:{session_id}:rows/bytes/tasks/gpu_h/api`
     - 设置初始值为合约配额上限

2. **配额消耗更新**
   - 文件：`app/services/quota_manager.py`
   - `consume_quota(session_id, quota_type, amount)` 使用 Lua 原子操作
   - 超限时拒绝操作并返回 429

3. **会话结束时清理配额**
   - 文件：`app/services/sandbox_session_service.py`
   - `terminate_session()` 时将剩余配额写回 DB（用于审计）
   - 清理 Redis 计数器

**验收标准**：
- 会话创建后 Redis 中有对应配额计数器
- 配额超限时操作被拒绝
- 会话结束后配额数据持久化到 DB

**预计工时**：2 天

---

### ~~P1-7: 合约区块链锚定~~ ✅ 已完成

**三轮复核确认**：`contract_service.py:134-146` 签署后触发 `blockchain_service.anchor_to_blockchain()`，`blockchain_tx_hash` 字段已写入。FISCO BCOS SDK 仍为桩但 PG append-only 为真实替代。

---

### P1-8: PII NER ML 模型集成

**问题**：PII 检测仅正则，无 ML 模型，误报/漏报率高。

**规格来源**：SS-05 §3.2

**当前状态**：STUB — 纯规则正则

**实现任务**：

1. **集成 `bert-base-chinese-pii-ner` 模型**
   - 文件：`app/services/pii_ner.py`
   - 使用 `transformers` 库加载预训练模型
   - 添加模型缓存（避免每次推理都加载）

2. **实现双层检测逻辑**
   - 文件：`app/services/pii_ner.py`
   - Layer 1：正则快速筛选（已有）
   - Layer 2：当正则命中 ≥1 或文本 > 500 字时触发 NER
   - 合并两层结果，去重

3. **添加模型配置**
   - 文件：`app/core/config.py`
   - `PII_NER_MODEL_PATH`：本地模型路径
   - `PII_NER_THRESHOLD`：置信度阈值（默认 0.8）
   - `PII_NER_ENABLED`：开关（测试环境可关闭）

4. **添加依赖**
   - 文件：`requirements.txt`
   - `transformers>=4.35.0`
   - `torch>=2.0.0`（CPU 版本即可）

**验收标准**：
- 中文手机号、身份证号、银行卡号被正确识别
- 正则 + NER 双层检测漏报率 < 5%
- 模型加载不超过 10 秒

**预计工时**：3 天

---

### P1-9: 流式审查 mitmproxy 集成

**问题**：应用场景无真实代理，流式审查为纯 Python 实现。

**规格来源**：SS-05 §4.3

**当前状态**：STUB — 纯 Python，无 mitmproxy

**实现任务**：

1. **实现应用输出代理**
   - 文件：`app/services/app_output_proxy.py`（新建）
   - 使用 `mitmproxy` 的 Python API 创建代理
   - 拦截所有出向 HTTP/HTTPS 流量
   - 4KB 块审查：分块检查 PII、数据重建风险

2. **集成到流式审查管线**
   - 文件：`app/services/streaming_inspector.py`
   - 将现有的 4KB 块扫描逻辑接入 mitmproxy 事件
   - `response` 事件 → 块审查 → 放行/阻断/脱敏

3. **会话级熔断器**
   - 文件：`app/services/streaming_inspector.py`
   - 单会话累计 PII 命中超过阈值时熔断
   - 熔断后拒绝该会话的所有输出

4. **添加依赖**
   - 文件：`requirements.txt`
   - `mitmproxy>=10.0.0`

**验收标准**：
- 代理能拦截 HTTP/HTTPS 流量
- PII 在响应中被检测到
- 熔断器正常工作

**预计工时**：4 天

---

### P1-10: 模型去重 (kms.py / key_metadata.py)

**问题**：两个模型定义相同概念（数据加密密钥），字段不同，无关联。

**规格来源**：架构问题

**当前状态**：DUPLICATED

**实现任务**：

1. **统一为单一模型**
   - 决策：保留 `key_metadata.py` 的 `DataEncryptionKey`（字段更完整）
   - 迁移 `kms.py` 的 `KeyMetadata` 中独有字段到 `DataEncryptionKey`

2. **更新所有引用**
   - 文件：`app/services/kms_service.py`、`app/services/certificate_service.py` 等
   - 将 `KeyMetadata` 引用替换为 `DataEncryptionKey`

3. **添加数据库迁移**
   - 文件：`alembic/versions/`（新建迁移）
   - 合并数据、删除旧表

4. **删除 `kms.py` 模型**
   - 文件：`app/models/kms.py`
   - 删除或标记为 deprecated

**验收标准**：
- 只有一个 DEK 模型
- 所有服务使用统一模型
- 数据迁移无丢失

**预计工时**：2 天

---

## 三、P2 高级场景任务（12 项）

> 高级场景与远期目标。预计总工时：12-16 周（2 人）

### P2-1: L1 TEE (SGX/Occlum) 支持

**问题**：最高安全级别无真实 TEE 支持。

**规格来源**：SS-04 §2.2

**当前状态**：STUB — `TEEAdapter.provision()` 返回 FAILED

**实现任务**：

1. **实现 SGX 注册流程**
   - 文件：`app/services/tee_adapter.py`
   - `provision()` 调用 SGX SDK 创建 enclave
   - 获取 `MRENCLAVE`、`MRSIGNER`、`report`

2. **实现远程证明验证**
   - 文件：`app/services/remote_attestation.py`
   - 验证 Intel IAS/DCAP 报告
   - 检查 MRENCLAVE 白名单

3. **密钥分发到 TEE**
   - 文件：`app/services/kms_service.py`
   - `distribute_key_to_tee()` 用 TEE 公钥加密 DEK
   - TEE 内部解密使用

**验收标准**：
- SGX enclave 成功创建
- 远程证明验证通过
- DEK 安全分发到 TEE

**预计工时**：10 天（需要 SGX 硬件环境）

---

### P2-2: GPU-TEE 运行时

**问题**：训练场景不可用，全部为 Stub。

**规格来源**：SS-04 §3.5

**当前状态**：STUB — `GPUTeeRuntimeStub`

**实现任务**：

1. **实现 NVIDIA CC 模式激活**
   - 文件：`app/services/gpu_tee_runtime.py`
   - 检查 GPU 是否支持 Confidential Computing
   - 激活 CC 模式

2. **实现 CPU-GPU 加密通道**
   - 文件：`app/services/gpu_tee_runtime.py`
   - 通道密钥协商
   - 数据加密传输到 GPU 显存

3. **集成 PyTorch 训练**
   - 文件：`app/services/gpu_tee_runtime.py`
   - 在 GPU-TEE 环境中运行 PyTorch 训练
   - 支持 LoRA/QLoRA 微调

**验收标准**：
- GPU CC 模式成功激活
- 训练数据在 GPU 显存中加密
- 训练完成后模型安全导出

**预计工时**：15 天（需要 H100/A100 硬件）

---

### ~~P2-3: MPC 密钥分片~~ ✅ 已完成

**三轮复核确认**：`mpc_service.py` 已实现真实 Shamir (2,2) 秘密分享：256-bit 素数域、多项式求值、Lagrange 插值重建。限制：内存存储，无持久化。

**实现任务**：

1. **实现 Shamir (2,2) 秘密分享**
   - 文件：`app/services/mpc_service.py`
   - `split_key(dek)` → (shard_1, shard_2)
   - `reconstruct_key(shard_1, shard_2)` → dek

2. **分片分发**
   - shard_1 → 数商 A（用其公钥加密）
   - shard_2 → 数商 B（用其公钥加密）
   - 记录到 `key_distributions` 表

3. **在线重组**
   - 沙箱启动时请求两方提供分片
   - TEE 内重组 DEK

**验收标准**：
- 分片后单方无法恢复 DEK
- 两方在线时可成功重组
- 分片记录持久化

**预计工时**：8 天

---

### P2-4: PostgreSQL-in-TEE

**问题**：应用场景不可用。

**规格来源**：SS-04 §3.3

**当前状态**：NOT_IMPLEMENTED

**实现任务**：

1. **部署 PostgreSQL 到 TEE 环境**
   - 使用 Occlum 或 Gramine 运行 PostgreSQL
   - 配置 `pgcrypto` 扩展（SM4 加密）

2. **实现行级安全 (RLS)**
   - 文件：`app/services/db_access_proxy.py`（扩展）
   - 为每个沙箱会话创建受限角色
   - RLS 策略按 `session_id` 过滤

3. **数据导入导出**
   - 导入：SM4 加密数据 → TEE 内解密 → 写入 PG
   - 导出：PG 查询 → 审查网关 → 加密输出

**验收标准**：
- PostgreSQL 在 TEE 中运行
- RLS 策略正确隔离不同会话
- 数据在 TEE 外始终加密

**预计工时**：12 天

---

### P2-5: 9 种场景运行时

**问题**：仅基础设施适配器，无场景特化运行时。

**规格来源**：SS-04 §3.1-3.9

**当前状态**：PARTIAL — 3/9 已有基础实现（llm_sft、vision、multimodal）

**分 3 期实现**：

**第一期（核心 3 种）** — 预计 10 天：

1. `StructuredQueryRuntime`：DuckDB 策略引擎 + 脱敏视图 + SQL 重写
2. `DataModelingRuntime`：DuckDB UDF (train_xgb_classifier, feature_importance)
3. `ProductDevRuntime`：采样限制 ≤1000 行 + 产品包导出

**第二期（LLM 2 种）** — 预计 12 天：

4. `LLMSFTRuntime`：PyTorch + peft LoRA + SecureSFTTrainer
5. `LLMPretrainRuntime`：大规模语料加载 + 分布式训练

**第三期（高级 4 种）** — 预计 15 天：

6. `VisionRuntime`：图像预处理管线（人脸模糊、DICOM 脱敏）
7. `MultimodalRuntime`：图文混合加载 + CLIP 相似度检测
8. `SemiStructuredRuntime`：JSONL Schema 推断 + 嵌套展开
9. `FederatedRuntime`：跨空间模型聚合

---

### P2-6: 训练管线真实训练

**问题**：仅为数据预处理，无 PyTorch 训练。

**规格来源**：SS-07 §3.1

**当前状态**：STUB — `_simulate_training_step()`

**实现任务**：

1. **集成 PyTorch + HuggingFace**
   - 文件：`app/services/llm_sft_runtime.py`
   - 加载基座模型（白名单校验）
   - LoRA/QLoRA 微调

2. **实现 SecureSFTTrainer**
   - 文件：`app/services/secure_sft_trainer.py`（新建）
   - 继承 HuggingFace `Trainer`
   - 每个 epoch 后检查记忆性
   - 梯度裁剪 + DP-SGD

3. **添加依赖**
   - `torch>=2.0.0`
   - `transformers>=4.35.0`
   - `peft>=0.7.0`

**验收标准**：
- 能加载基座模型并微调
- 训练过程中有记忆性检查
- 模型导出经过审查网关

**预计工时**：10 天

---

### P2-7: CDC Kafka 生产者

**问题**：增量同步不可用。

**规格来源**：SS-09 §3.3

**当前状态**：STUB — `_kafka_producer` 始终 None

**实现任务**：

1. **集成 `confluent-kafka` 或 `aiokafka`**
   - 文件：`app/services/cdc_agent.py`
   - 初始化 Kafka 生产者
   - Debezium CDC 事件 → Kafka topic

2. **实现事件转换**
   - CDC 事件 → 标准化格式
   - 敏感字段加密（SM4-GCM）

3. **添加错误处理**
   - Kafka 不可用时降级为 DB 轮询
   - 重试 + 死信队列

**验收标准**：
- DB 变更实时同步到 Kafka
- 敏感字段在传输中加密
- Kafka 不可用时有降级方案

**预计工时**：5 天

---

### P2-8: TLCP SM2 密码套件

**问题**：国密 TLS 不可用，静默回退到 RSA。

**规格来源**：SS-01 §4.2

**当前状态**：STUB — 回退到 RSA

**实现任务**：

1. **集成铜锁 (Tongsuo) TLS 库**
   - 使用 `tongsuo` Python 绑定或 OpenSSL 3.x + SM2 engine
   - 配置 TLCP 密码套件：`ECC_SM4_CBC_SM3`、`ECDHE_SM4_CBC_SM3`

2. **修改 `TLCPService`**
   - 文件：`app/services/tlcp_service.py`
   - 使用真实 SM2 密码套件名
   - 移除 RSA 回退

3. **添加 TLCP 握手测试**
   - 文件：`tests/test_tlcp.py`（新建）

**验收标准**：
- TLCP 握手使用 SM2 密码套件
- 不回退到 RSA
- 测试用 TLS 客户端能成功连接

**预计工时**：5 天

---

### P2-9: 文本记忆检测 (Carlini 方法)

**问题**：仅为 n-gram 重叠，阈值 0.3 vs 规格 0.08。

**规格来源**：SS-05 §3.5

**当前状态**：STUB — n-gram 重叠

**实现任务**：

1. **实现 Carlini extractable memorization**
   - 文件：`app/services/memorization_check.py`
   - 前缀样本构造：取训练数据前 N 个 token 作为前缀
   - 贪婪解码：模型生成后续 token
   - Token 重叠率：生成结果与原始数据的重叠比例

2. **调整阈值**
   - 默认阈值：0.15（从 0.08 提高，避免误报）
   - 按场景配置：query 场景 0.20，training 场景 0.15

**验收标准**：
- 记忆性检测使用 Carlini 方法
- 阈值可配置
- 误报率 < 10%

**预计工时**：4 天

---

### P2-10: 模型权重水印

**问题**：无实现。EmbMarker 依赖不存在。

**规格来源**：SS-05 §3.6

**当前状态**：NOT_IMPLEMENTED

**实现任务**：

1. **实现 Uchida 2017 水印方案**（替代虚构的 EmbMarker）
   - 文件：`app/services/model_watermark.py`（新建）
   - 在指定层的权重中嵌入水印
   - 嵌入不影响模型精度

2. **水印验证**
   - 从模型权重中提取水印
   - 验证所有权

**验收标准**：
- 水印嵌入后模型精度下降 < 1%
- 水印可正确提取和验证

**预计工时**：5 天

---

### P2-11: 网络层流量控制

**问题**：无内核级流量控制。

**规格来源**：SS-05 §4.4

**当前状态**：NOT_IMPLEMENTED

**实现任务**：

1. **实现 sidecar proxy 方案**（替代 iptables）
   - 使用 Envoy 或 Nginx 作为 sidecar
   - 所有出向流量经 proxy 转发

2. **应用层代理集成**
   - 文件：`app/services/network_policy.py`（已有 API）
   - 策略规则 → proxy 配置
   - 动态更新代理规则

**验收标准**：
- 沙箱内所有出向流量经过代理
- 违规流量被阻断
- 策略动态更新

**预计工时**：5 天

---

### P2-12: 多模态/视觉场景运行时

**问题**：训练场景不可用。

**规格来源**：SS-04 §3.6, §3.7

**当前状态**：STUB — `apply()` 为直通

**实现任务**：

1. **视觉预处理管线**
   - 文件：`app/services/vision_pipeline.py`
   - 图像解码 → 人脸检测+模糊 → DICOM 脱敏 → 车牌马赛克 → 增强

2. **多模态训练**
   - 文件：`app/services/multimodal_runtime.py`（新建）
   - 图文混合加载器
   - LoRA 多模态微调
   - CLIP 相似度检测（图像重建攻击防护）

**验收标准**：
- 人脸在训练图像中被模糊
- DICOM 元数据被脱敏
- 模型输出与训练图像的 CLIP 相似度 < 阈值

**预计工时**：10 天

---

## 四、规格更新任务（15 项）

> 与代码实现并行，更新设计文档使其反映实际架构决策。

### SPEC-1: SandboxMode 精简
- 文件：`specs/ss-02-contract-policy.md`
- 将 13 种模式精简为 6 种核心模式 + 配置参数
- 核心模式：structured_query, structured_modeling, structured_app, llm_sft, product_dev, joint_federated

### SPEC-2: 场景运行时分期
- 文件：`specs/ss-04-sandbox-runtime.md`
- 标注 9 种运行时的交付分期（P1/P2/P3）

### SPEC-3: 移除 eBPF
- 文件：`specs/SS-03.md`
- 移除 eBPF 异常检测要求
- 替换为应用层监控（Prometheus metrics）

### SPEC-4: MIA 方案替换
- 文件：`specs/ss-05-output-gateway.md`
- 将影子模型 MIA 替换为 Loss-based Threshold Attack
- 减少样本量：200+200 → 50+50

### SPEC-5: Rego 模板精简
- 文件：`specs/ss-02-contract-policy.md`
- 13 种模板 → 4 种核心模板 + 通用回退

### SPEC-6: gmssl GCM 限制标注
- 文件：`specs/tech-spec.md`, `specs/detailed-tech-spec.md`
- 标注 gmssl 不支持 GCM 的限制
- 记录使用 `cryptography` 库 AES-GCM 替代方案

### SPEC-7: 列命名统一
- 文件：`specs/SS-06.md` (ClickHouse schema)
- 统一 `space_id` → `user_id`，`node_id` → `sandbox_id`

### SPEC-8: 移除 gRPC 要求
- 文件：`specs/ss-01-kms-identity.md`
- 移除 protobuf 定义和 gRPC 要求
- 统一为 REST API

### SPEC-9: 简化 CRL 机制
- 文件：`specs/ss-01-kms-identity.md`
- 简化为 OCSP 在线验证 + 定期完整 CRL
- 移除增量 CRL 要求

### SPEC-10: PDP 运行环境修正
- 文件：`specs/ss-02-contract-policy.md`
- PDP 在普通进程运行
- 策略包加密传输保证完整性

### SPEC-11: 记忆性阈值调整
- 文件：`specs/ss-05-output-gateway.md`
- TextMemorizationStage 阈值 0.08 → 0.15-0.20
- 按场景配置不同阈值

### SPEC-12: iptables 替换为 sidecar
- 文件：`specs/ss-05-output-gateway.md`
- iptables OUTPUT 链 → sidecar proxy (Envoy)

### SPEC-13: DEK 明文位置一致性
- 文件：`specs/ss-01-kms-identity.md`, `specs/ss-04-sandbox-runtime.md`
- 明确 DEK 明文仅出现在 TEE 内部进程内存
- HSM 下发时用 TEE 公钥加密

### SPEC-14: SandboxMode 规则集补全
- 文件：`specs/ss-05-output-gateway.md`
- 为 joint_federated、api_service 补充对应规则集

### ~~SPEC-15: DuckDB 脱敏 md5 → SM3~~ ✅ 已完成
- 三轮复核确认：`secure_duckdb.py` 已使用 SM3 UDF 替换 md5

---

## 五、前端补全任务（3 项剩余）

> 原 5 项中 2 项已完成（告警内联于 /monitoring、验证内联于 /audit）。

### FE-1: 身份/密钥管理页面
- 路由：`/identity`, `/identity/keys`
- 功能：查看/创建 SM2 密钥对、查看证书、证书续期

### FE-2: 证书管理页面
- 路由：`/certificates`
- 功能：查看证书列表、吊销证书、CRL 查看

### FE-3: 连接器管理 UI
- 路由：`/connectors`
- 功能：注册/删除连接器、测试连接、查看同步状态

### ~~FE-4: 告警管理页面~~ ✅ 已完成
- 三轮复核确认：告警表格已内联于 `/monitoring` 页面

### ~~FE-5: 区块链验证页面~~ ✅ 已完成
- 三轮复核确认：Merkle 证明和区块链验证已内联于 `/audit` 页面

---

## 六、部署补全任务（1 项剩余）

> 原 3 项中 2 项已完成。

### ~~DEP-1: 前端 Dockerfile~~ ✅ 已完成
- 三轮复核确认：`cds-frontend/Dockerfile` 多阶段构建 node:18-alpine → nginx:alpine

### ~~DEP-2: Vault Compose 集成~~ ✅ 已完成
- 三轮复核确认：`docker-compose.middleware.yml` 包含 vault:1.17 + vault-init

### DEP-3: FISCO BCOS 节点
- 文件：`docker-compose.blockchain.yml`（新建）
- 单节点 FISCO BCOS 开发环境
- 部署审计注册智能合约

---

## 七、任务依赖关系（更新后）

```
✅ P0-1 (证书) ──→ ✅ P0-2 (HSM签名) ──→ ✅ P1-7 (合约锚定)
    │
    └──→ ✅ SPEC-13 (DEK位置) ──→ P2-1 (L1 TEE)

✅ P0-3 (SM4-GCM) ──→ ✅ P1-3 (DuckDB策略)

✅ P0-4 (区块链) ──→ ✅ P1-7 (合约锚定) ──→ ✅ SPEC-12 (sidecar)

P0-5 (CODE_SCANNING) ──→ ✅ P1-2 (任务集成)

✅ P1-1 (Rego模板) ──→ ✅ P1-3 (DuckDB策略)

P1-8 (PII NER) ──→ P1-9 (mitmproxy)

P2-5 (场景运行时) ──→ P2-6 (训练管线) ──→ P2-12 (多模态)
```

---

## 八、Sprint 规划建议（更新后）

### ~~Sprint 1~~ ✅ 已完成
- ✅ P0-1: 证书真实 X.509 + 吊销级联
- ✅ P0-3: SM4-GCM 真实实现
- ⚠️ P0-5: 任务 CODE_SCANNING 集成（pipeline 有框架但无 handler）

### ~~Sprint 2~~ ✅ 已完成
- ✅ P0-2: SM2 HSM 内签名
- ✅ P0-4: 区块链审计持久化 (PG append-only)
- ⚠️ P1-10: 模型去重（仍存在）
- ✅ SPEC-1, SPEC-5, SPEC-13, SPEC-15

### ~~Sprint 3~~ ✅ 大部分完成
- ✅ P1-1: 多模式 Rego 模板
- ✅ P1-2: 任务组件集成 (task_pipeline)
- ✅ P1-3: DuckDB 策略引擎
- ⚠️ P1-4: 会话配额初始化（PARTIAL — reset 但无预填充）

### Sprint 4（2 周）— 当前 Sprint
- P0-5: CODE_SCANNING handler 注册
- P1-4: 会话配额从合约预填充
- P1-10: 模型去重 (kms.py / key_metadata.py)
- SPEC-6 ~ SPEC-10

### Sprint 5（2 周）— 功能收尾
- P1-8: PII NER ML 模型
- P1-9: 流式审查 mitmproxy
- FE-1 ~ FE-3
- DEP-3 (FISCO BCOS Compose)

### Sprint 6+（远期）— 高级场景
- P2-1, P2-2, P2-4 ~ P2-12 按优先级和硬件可用性排序

---

## 九、工时汇总（三轮复核更新）

| 优先级 | 原任务数 | 已完成 | 剩余 | 剩余工时 | 说明 |
|--------|---------|--------|------|---------|------|
| P0 安全关键 | 5 | 4 | 1 | 2 天 | 仅 CODE_SCANNING handler |
| P1 功能完整 | 10 | 6 | 4 | 11 天 | 配额预填充/模型去重/PII NER/mitmproxy |
| P2 高级场景 | 12 | 2 | 10 | 71 天 | MPC+PG append-only 已完成 |
| 规格更新 | 15 | 4 | 11 | ~4 天 | 与代码并行 |
| 前端补全 | 5 | 2 | 3 | ~6 天 | 与后端并行 |
| 部署补全 | 3 | 2 | 1 | ~2 天 | FISCO BCOS Compose |
| **总计** | **50** | **20** | **20** | **~96 天** | **2 人约 10 周** |
