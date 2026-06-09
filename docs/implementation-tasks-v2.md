# CDS 密态沙箱系统 · 剩余缺口实现任务清单 v2

> 日期：2026-06-05（三轮复核后）
> 基于：[spec-implementation-analysis.md](./spec-implementation-analysis.md) 三轮复核结果
> 当前实现率：~70%
> 剩余缺口：20 项（1 P0 + 4 P1 + 10 P2 + 3 前端 + 1 部署 + 1 架构）

---

## 一、P0 安全关键（1 项）

### P0-5: 任务 CODE_SCANNING handler 注册

**问题**：`task_pipeline.py` 调用 CODE_SCANNING 阶段但无 handler 注册，静默跳过。

**规格来源**：SS-03 §3.2

**当前状态**：STUB — pipeline 框架存在但无处理函数

**代码现状**：
- `task_pipeline.py:191-193` 调用 `self._run_stage(task, TaskStatus.CODE_SCANNING)`
- `task_pipeline.py:244-249` 无 handler 时静默 `return {}`
- `task_pipeline.py:349` 仅注册 `RUNNING` handler
- `code_scanner.py:106` 有 `scan(code, language, sandbox_mode) -> ScanResult` 方法
- `code_scanner.py:30-43` `ScanResult` 有 `passed: bool` 和 `issues: list[ScanIssue]`

**实现步骤**：

1. **在 `task_pipeline.py` 底部注册 CODE_SCANNING handler**
   - 文件：`app/services/task_pipeline.py`（约 349 行之后）
   - 添加：
     ```python
     async def _code_scanning_handler(task: PipelineTask) -> tuple[TaskStatus, dict | None]:
         from app.services.code_scanner import code_scanner
         code = task.payload.get("code", "")
         language = task.payload.get("language", "python")
         sandbox_mode = task.payload.get("sandbox_mode", "structured_query")
         result = code_scanner.scan(code, language, sandbox_mode)
         if not result.passed:
             issues = [{"rule": i.rule, "message": i.message, "line": i.line} for i in result.issues]
             return TaskStatus.FAILED, {"error": "Code scanning failed", "issues": issues}
         return TaskStatus.CODE_SCANNING, {"scan_passed": True, "issue_count": len(result.issues)}

     task_pipeline.register_handler(TaskStatus.CODE_SCANNING, _code_scanning_handler)
     ```

2. **在 `task_worker.py` 中也集成代码扫描**（保留旧 worker 的兼容性）
   - 文件：`app/services/task_worker.py`（约 78 行）
   - 在 `task.status = TaskStatus.RUNNING.value` 之前插入：
     ```python
     from app.services.code_scanner import code_scanner
     scan_result = code_scanner.scan(task.code, task.language, task.sandbox_mode)
     if not scan_result.passed:
         task.status = TaskStatus.FAILED.value
         task.error = f"Code scanning failed: {[i.message for i in scan_result.issues]}"
         return
     ```

3. **添加审计日志**
   - 扫描结果写入 audit_log（通过 `audit_service.log()`）

**验收标准**：
- 包含 `os.system("rm -rf /")` 的 Python 代码被拒绝，任务状态为 FAILED
- 扫描结果写入审计日志
- pipeline 和 worker 两条路径都经过代码扫描

**预计工时**：1 天

---

## 二、P1 功能完整（4 项）

### P1-4: 会话配额从合约预填充

**问题**：`quota_manager.reset()` 仅清除 Redis 键，不从合约 max_output_rows 等字段初始化。

**规格来源**：SS-02 §5.2

**当前状态**：PARTIAL

**代码现状**：
- `sandbox_sessions.py:87-88` 调用 `await quota_manager.reset(session.id)`
- `quota_manager.py:138-147` `reset()` 仅 `redis.delete(key)`
- `contract.py:58-62` 有 `max_duration_hours`, `dp_epsilon_budget`, `max_output_rows`, `allowed_output_formats`
- `quota_manager.py:14-19` `QuotaType` 枚举：ROWS, BYTES, API_CALLS, GPU_SECONDS
- `quota_manager.py:82` `check_and_increment(session_id, quota_type, amount, limit)` — limit 由调用方传入

**实现步骤**：

1. **添加 `set_limits()` 方法到 QuotaManager**
   - 文件：`app/services/quota_manager.py`（约 138 行之前）
   - 添加：
     ```python
     async def set_limits(self, session_id: uuid.UUID, limits: dict[QuotaType, int]) -> None:
         """Store quota limits in Redis for a session."""
         for qt, limit in limits.items():
             key = self._limit_key(session_id, qt)
             if self._redis:
                 await self._redis.set(key, limit, ex=86400 * 7)  # 7 days TTL

     def _limit_key(self, session_id: uuid.UUID, quota_type: QuotaType) -> str:
         return f"cds:quota_limit:{session_id}:{quota_type.value}"
     ```

2. **修改 session 创建端点预填充配额**
   - 文件：`app/api/sandbox_sessions.py`（约 87-88 行）
   - 替换 `await quota_manager.reset(session.id)` 为：
     ```python
     await quota_manager.reset(session.id)
     # Pre-fill quota limits from contract
     contract = await db.get(Contract, session.contract_id)
     if contract:
         await quota_manager.set_limits(session.id, {
             QuotaType.ROWS: contract.max_output_rows or 10000,
             QuotaType.BYTES: 100 * 1024 * 1024,  # 100MB default
             QuotaType.API_CALLS: 1000,
             QuotaType.GPU_SECONDS: int((contract.max_duration_hours or 24) * 3600),
         })
     ```

3. **修改 `check_and_increment()` 支持从 Redis 读取 limit**
   - 文件：`app/services/quota_manager.py`（约 82 行）
   - 当 `limit` 参数为 0 或 None 时，从 Redis 读取预设 limit：
     ```python
     if not limit and self._redis:
         stored = await self._redis.get(self._limit_key(session_id, quota_type))
         limit = int(stored) if stored else 0
     ```

**验收标准**：
- 会话创建后 Redis 中有 `cds:quota_limit:{session_id}:rows` 等键
- 不传 limit 参数时自动使用合约配额
- 配额超限时操作被拒绝

**预计工时**：2 天

---

### P1-8: PII NER ML 模型集成

**问题**：PII 检测仅正则+规则 NER，无 ML 模型。

**规格来源**：SS-05 §3.2

**当前状态**：STUB

**代码现状**：
- `pii_ner.py:162-165` `__init__` 仅存储 `enable_ner`，实例化 `RegexPatterns` + `NERPatterns`
- `pii_ner.py:221-316` `_detect_ner()` 为纯正则匹配（PERSON_NAME/ADDRESS/ORGANIZATION/DOB 模式）
- `pii_ner.py:114` 注释："For production use, replace with ML-based NER"
- `config.py:13-58` 无 PII NER 配置
- `requirements.txt` 无 transformers/torch

**实现步骤**：

1. **添加配置字段**
   - 文件：`app/core/config.py`（约 57 行之后）
   - 添加：
     ```python
     # PII NER ML
     PII_NER_USE_ML: bool = False
     PII_NER_MODEL_NAME: str = "bert-base-chinese-pii-ner"
     PII_NER_CONFIDENCE_THRESHOLD: float = 0.5
     ```

2. **添加依赖**
   - 文件：`requirements.txt`
   - 添加：
     ```
     transformers>=4.36.0
     torch>=2.0.0
     ```

3. **修改 `PIINERService.__init__` 加载 ML 模型**
   - 文件：`app/services/pii_ner.py`（约 162 行）
   - 添加：
     ```python
     def __init__(self, enable_ner: bool = True, use_ml: bool = False, model_name: str = "", threshold: float = 0.5):
         self.enable_ner = enable_ner
         self._use_ml = use_ml
         self._ml_pipeline = None
         self._threshold = threshold
         if use_ml:
             try:
                 from transformers import pipeline
                 self._ml_pipeline = pipeline("ner", model=model_name or "bert-base-chinese-pii-ner", aggregation_strategy="simple")
             except Exception as e:
                 logger.warning(f"ML NER model load failed, falling back to regex: {e}")
                 self._use_ml = False
     ```

4. **替换 `_detect_ner()` 方法**
   - 文件：`app/services/pii_ner.py`（约 221-316 行）
   - ML 路径：
     ```python
     def _detect_ner(self, text: str, regex_matches: list[PIIMatch]) -> list[PIIMatch]:
         if self._use_ml and self._ml_pipeline:
             return self._detect_ner_ml(text, regex_matches)
         return self._detect_ner_regex(text, regex_matches)  # 原有逻辑重命名
     ```
   - 新增 `_detect_ner_ml()`：
     ```python
     def _detect_ner_ml(self, text: str, regex_matches: list[PIIMatch]) -> list[PIIMatch]:
         entities = self._ml_pipeline(text)
         matches = []
         label_map = {"PER": PIIType.PERSON_NAME, "LOC": PIIType.ADDRESS, "ORG": PIIType.ORGANIZATION}
         for ent in entities:
             if ent["score"] < self._threshold:
                 continue
             pii_type = label_map.get(ent["entity_group"])
             if not pii_type:
                 continue
             # Check overlap with regex matches
             if any(m.start <= ent["start"] < m.end or ent["start"] <= m.start < ent["end"] for m in regex_matches):
                 continue
             matches.append(PIIMatch(type=pii_type, value=ent["word"], start=ent["start"], end=ent["end"], confidence=ent["score"]))
         return matches
     ```

5. **更新单例初始化**
   - 文件：`app/services/pii_ner.py`（约 365 行）
   - 从 config 读取参数：
     ```python
     from app.core.config import get_settings
     _settings = get_settings()
     pii_ner_service = PIINERService(
         enable_ner=True,
         use_ml=_settings.PII_NER_USE_ML,
         model_name=_settings.PII_NER_MODEL_NAME,
         threshold=_settings.PII_NER_CONFIDENCE_THRESHOLD,
     )
     ```

**验收标准**：
- `CDS_PII_NER_USE_ML=true` 时使用 ML 模型推理
- 模型不可用时自动回退到正则
- 中文人名/地址/组织识别准确率 > 80%

**预计工时**：3 天

---

### P1-10: 模型去重 (kms.py / key_metadata.py)

**问题**：两个 DEK 模型字段重叠，cascade 代码同时更新两者。

**规格来源**：架构问题

**当前状态**：DUPLICATED

**代码现状**：
- `kms.py` 定义 `DataEncryptionKey`（15 字段）、`KeyAuditLog`、`KeyDistribution`
- `key_metadata.py` 定义 `KeyMetadata`（12 字段）
- 重叠字段：id, key_id, product_id, algorithm, status, rotated_to, created_at, expires_at
- `certificate_service.py:276-299` 同时查询 `DataEncryptionKey` 并更新 `KeyMetadata`
- `KeyMetadata` 被 4 个文件引用：sandbox_sessions.py, contract_fulfillment.py, sandbox_runtime.py, certificate_service.py
- `DataEncryptionKey` 被 api/kms.py, certificate_service.py, kms_service.py 等引用

**实现步骤**：

1. **合并模型到 `kms.py`**
   - 文件：`app/models/kms.py`
   - 将 `KeyMetadata` 独有字段迁移到 `DataEncryptionKey`：
     - 添加 `key_type: Mapped[str] = mapped_column(String(32), default="DEK")`
     - 添加 `session_id: Mapped[uuid.UUID | None]`（FK sandbox_sessions）
     - 添加 `destroy_reason: Mapped[str | None]`
     - 添加 `rotated_at: Mapped[datetime | None]`
     - 添加 `destroyed_at: Mapped[datetime | None]`
   - 统一 Status 枚举为 `KeyStatus`（ACTIVE, ROTATED, REVOKED, DESTROYED, SUSPENDED）
   - 删除 `key_metadata.py`

2. **添加兼容性别名**
   - 文件：`app/models/kms.py`
   - 添加：`KeyMetadata = DataEncryptionKey`（过渡期兼容）

3. **更新所有引用**
   - `app/api/sandbox_sessions.py:23,133,264,617` — 改 import 为 `from app.models.kms import DataEncryptionKey as KeyMetadata`
   - `app/services/contract_fulfillment.py:30,142,221` — 同上
   - `app/services/sandbox_runtime.py:956,985,1056,1098` — 同上
   - `app/services/certificate_service.py:277,297,299` — 移除 `KeyMetadata` 引用，统一用 `DataEncryptionKey`

4. **添加 Alembic 迁移**
   - 文件：`alembic/versions/`（新建）
   - 将 `key_metadata` 表数据迁移到 `data_encryption_keys`
   - 删除 `key_metadata` 表

**验收标准**：
- 只有一个 DEK 模型 `DataEncryptionKey`
- 所有服务正常工作
- 数据迁移无丢失

**预计工时**：2 天

---

### P1-TLCP: TLCP SM2 密码套件

**问题**：TLCP 尝试 SM2 密码套件后始终回退到 RSA。

**规格来源**：SS-01 §4.2

**当前状态**：PARTIAL

**代码现状**：
- `tlcp_service.py:246` 尝试 `"ECDHE-SM2-WITH-SM4-SM3:SM2-WITH-SM4-SM3"`
- `tlcp_service.py:248-250` `ssl.SSLError` 时回退到 RSA
- `tlcp_service.py:290-307` `_build_cert_pem()` 生成占位符 PEM，非真实 X.509

**实现步骤**：

1. **集成铜锁 (Tongsuo) TLS 库**
   - 文件：`requirements.txt`
   - 添加 `tongsuo>=1.0.0`（或使用 OpenSSL 3.x SM2 engine）

2. **修改 `TLCPService.build_ssl_context()`**
   - 文件：`app/services/tlcp_service.py`（约 233-262 行）
   - 使用 Tongsuo 的 SSLContext 替代标准 `ssl.SSLContext`
   - 移除 RSA 回退

3. **修复 `_build_cert_pem()` 使用真实 X.509**
   - 文件：`app/services/tlcp_service.py`（约 290-307 行）
   - 复用 `certificate_service._build_cert_pem()` 的 ASN.1 DER 编码

**验收标准**：
- TLCP 握手使用 SM2 密码套件
- 不回退到 RSA
- 测试客户端能成功连接

**预计工时**：5 天

---

## 三、P2 高级场景（10 项）

### P2-1: L1 TEE (SGX/Occlum) 支持

**当前状态**：STUB — `TEEAdapter.provision()` 返回 FAILED

**实现步骤**：

1. **实现 SGX 注册**
   - 文件：`app/services/sandbox_runtime.py`（约 748-762 行，TEEAdapter 类）
   - `provision()` 调用 SGX SDK 创建 enclave
   - 获取 `MRENCLAVE`, `MRSIGNER`, `report`

2. **实现远程证明验证**
   - 文件：`app/services/remote_attestation.py`
   - 验证 Intel IAS/DCAP 报告
   - 检查 MRENCLAVE 白名单

3. **密钥分发到 TEE**
   - 文件：`app/services/kms_service.py`
   - `distribute_key_to_tee()` 用 TEE 公钥加密 DEK

**验收标准**：SGX enclave 成功创建，远程证明验证通过，DEK 安全分发

**预计工时**：10 天（需 SGX 硬件）

---

### P2-2: GPU-TEE 运行时

**当前状态**：STUB — 仅 `GPUTeeRuntimeStub`

**实现步骤**：

1. **实现 NVIDIA CC 模式激活**
   - 文件：`app/services/gpu_tee_runtime.py`（约 225 行，GPUTeeRuntimeStub）
   - 替换为真实 `GPUTeeRuntimeNVIDIA` 实现

2. **实现 CPU-GPU 加密通道**
   - 通道密钥协商 + 数据加密传输到 GPU 显存

3. **集成 PyTorch 训练**
   - 在 GPU-TEE 环境中运行 PyTorch + LoRA 微调

**验收标准**：GPU CC 模式激活，训练数据在 GPU 显存中加密

**预计工时**：15 天（需 H100/A100）

---

### P2-4: PostgreSQL-in-TEE

**当前状态**：NOT_IMPLEMENTED

**实现步骤**：

1. **部署 PG 到 TEE**
   - 使用 Occlum/Gramine 运行 PostgreSQL
   - 配置 `pgcrypto` 扩展

2. **实现 RLS**
   - 文件：`app/services/db_access_proxy.py`（扩展）
   - 每个沙箱会话创建受限角色 + RLS 策略

**验收标准**：PG 在 TEE 中运行，RLS 正确隔离

**预计工时**：12 天

---

### P2-5: 6 种缺失场景运行时

**当前状态**：PARTIAL — 3/9 存在（llm_sft, vision, multimodal）

**缺失**：structured_query, data_modeling, product_dev, llm_pretrain, semi_etl, federated

**实现步骤**：

1. **统一 SandboxMode 枚举**
   - 文件：`app/models/sandbox_session.py`（新建 SandboxMode 枚举）
   - 合并 `output_inspection.py:22-27` 和 `code_scanner.py:46-56` 的两个不一致枚举
   - 统一值：structured_query, structured_modeling, structured_app, llm_sft, llm_pretrain, vision, multimodal, analysis, semi_etl, product_dev, api_service, joint_federated

2. **添加 SandboxMode 字段到 SandboxSession 模型**
   - 文件：`app/models/sandbox_session.py`
   - 添加 `sandbox_mode: Mapped[str] = mapped_column(String(32), default="structured_query")`

3. **实现场景路由工厂**
   - 文件：`app/services/sandbox_runtime.py`（约 887 行）
   - 添加 `SceneRuntimeFactory`：
     ```python
     class SceneRuntimeFactory:
         _runtimes: dict[str, type] = {}

         @classmethod
         def register(cls, mode: str, runtime_cls: type):
             cls._runtimes[mode] = runtime_cls

         @classmethod
         def create(cls, mode: str):
             return cls._runtimes.get(mode, DefaultRuntime)()
     ```

4. **实现第一期 3 种运行时**（预计 10 天）
   - `StructuredQueryRuntime`：DuckDB 策略引擎 + 脱敏视图（已有基础）
   - `DataModelingRuntime`：DuckDB UDF + Python 建模脚本执行
   - `ProductDevRuntime`：采样限制 ≤1000 行 + 产品包导出

**验收标准**：
- SandboxMode 枚举统一
- 3 种核心运行时可独立执行
- 场景路由按 mode 选择运行时

**预计工时**：10 天（第一期）

---

### P2-6: 训练管线 PyTorch

**当前状态**：STUB — `_simulate_training_step()` 返回合成 loss

**实现步骤**：

1. **添加依赖**
   - 文件：`requirements.txt`
   - `torch>=2.0.0`, `transformers>=4.36.0`, `peft>=0.7.0`

2. **替换 `_simulate_training_step()`**
   - 文件：`app/services/llm_sft_runtime.py`（约 425-435 行）
   - 实现真实 PyTorch forward/backward：
     ```python
     def _training_step(self, batch, model, optimizer) -> float:
         outputs = model(**batch)
         loss = outputs.loss
         loss.backward()
         optimizer.step()
         optimizer.zero_grad()
         return loss.item()
     ```

3. **实现 SecureSFTTrainer**
   - 文件：`app/services/secure_sft_trainer.py`（新建）
   - 继承 HuggingFace `Trainer`
   - 每 epoch 后检查记忆性 + 梯度裁剪 + DP-SGD

**验收标准**：能加载基座模型并微调，训练过程有记忆性检查

**预计工时**：10 天

---

### P2-7: CDC Kafka 生产者

**当前状态**：STUB — `_kafka_producer = None`

**实现步骤**：

1. **集成 aiokafka**
   - 文件：`requirements.txt`
   - `aiokafka>=0.10.0`

2. **修改 `CDCEventProducer.__init__`**
   - 文件：`app/services/cdc_agent.py`（约 128 行）
   - 初始化 Kafka producer：
     ```python
     if backend == "kafka":
         from aiokafka import AIOKafkaProducer
         self._kafka_producer = AIOKafkaProducer(bootstrap_servers=kafka_brokers)
     ```

3. **添加连接管理**
   - `start()` / `stop()` 方法管理 producer 生命周期

**验收标准**：DB 变更实时同步到 Kafka，敏感字段加密传输

**预计工时**：3 天

---

### P2-9: 文本记忆检测 Carlini 方法

**当前状态**：PARTIAL — n-gram 重叠，阈值 0.3

**实现步骤**：

1. **实现 loss-based memorization**
   - 文件：`app/services/mia_probe.py`（约 256-297 行）
   - 添加 `_compute_loss_based_memorization()`：
     - 前缀样本构造
     - 模型贪婪解码
     - Token 重叠率 p95 检查

2. **调整阈值**
   - 文件：`app/services/memorization_check.py`
   - 默认阈值 0.3 → 0.15-0.20

**验收标准**：Carlini 方法可选启用，阈值可配置

**预计工时**：4 天

---

### P2-10: 模型权重水印

**当前状态**：STUB — SM3 哈希记录日志，不修改权重

**实现步骤**：

1. **实现 Uchida 2017 水印方案**（替代虚构的 EmbMarker）
   - 文件：`app/services/model_watermark.py`（新建）
   - 在指定层权重中嵌入水印
   - 嵌入不影响模型精度（< 1% 下降）

2. **水印验证**
   - 从权重中提取水印并验证所有权

**验收标准**：水印嵌入后精度下降 < 1%，可正确提取验证

**预计工时**：5 天

---

### P2-12: FISCO BCOS 真实客户端

**当前状态**：STUB — SDK 注释掉，PG append-only 为替代

**实现步骤**：

1. **集成 FISCO BCOS Python SDK**
   - 文件：`app/services/blockchain_service.py`（约 23-30 行）
   - 取消注释 SDK 导入，配置连接参数

2. **部署 FISCO BCOS 节点**
   - 文件：`docker-compose.blockchain.yml`（新建）
   - 单节点开发环境

3. **部署审计注册智能合约**
   - 文件：`contracts/AuditRegistry.sol`（新建）

**验收标准**：链上锚定成功，tx_hash 可验证

**预计工时**：5 天

---

### P2-mitmproxy: 流式审查 mitmproxy 集成

**当前状态**：PARTIAL — 纯 Python 块扫描

**实现步骤**：

1. **实现 mitmproxy 插件**
   - 文件：`app/services/app_output_proxy.py`（新建）
   - 使用 mitmproxy Python API 创建代理
   - 拦截 HTTP/HTTPS 出向流量

2. **集成 StreamingInspector**
   - `response` 事件 → 4KB 块审查 → 放行/阻断

**验收标准**：代理拦截流量，PII 被检测，熔断器工作

**预计工时**：4 天

---

## 四、前端补全（3 项）

### FE-1: 身份/密钥管理页面

**当前状态**：NOT_IMPLEMENTED

**实现步骤**：

1. **创建页面组件**
   - 文件：`cds-frontend/src/pages/Identity/KeyManagement.tsx`（新建）
   - 使用 Ant Design Table 展示密钥列表
   - 支持创建 SM2 密钥对、查看证书、证书续期

2. **添加 API 服务**
   - 文件：`cds-frontend/src/services/identityApi.ts`（新建）
   - 调用 `/api/v1/kms/*` 端点

3. **注册路由**
   - 文件：`cds-frontend/src/App.tsx`
   - 添加 `/identity` 和 `/identity/keys` 路由

**验收标准**：密钥列表可查看，SM2 密钥对可创建

**预计工时**：2 天

---

### FE-2: 证书管理页面

**当前状态**：NOT_IMPLEMENTED

**实现步骤**：

1. **创建页面组件**
   - 文件：`cds-frontend/src/pages/Identity/Certificates.tsx`（新建）
   - 证书列表 + 吊销操作 + CRL 查看

2. **注册路由**
   - `/certificates` 路由

**验收标准**：证书列表可查看，可吊销

**预计工时**：2 天

---

### FE-3: 连接器管理页面

**当前状态**：NOT_IMPLEMENTED

**实现步骤**：

1. **创建页面组件**
   - 文件：`cds-frontend/src/pages/Connectors/index.tsx`（新建）
   - 连接器注册/删除、测试连接、同步状态

2. **注册路由**
   - `/connectors` 路由

**验收标准**：连接器可注册、测试、查看同步状态

**预计工时**：2 天

---

## 五、部署补全（1 项）

### DEP-3: FISCO BCOS 节点

**当前状态**：NOT_IMPLEMENTED

**实现步骤**：

1. **创建 compose 文件**
   - 文件：`docker-compose.blockchain.yml`（新建）
   - 单节点 FISCO BCOS 开发环境

2. **初始化脚本**
   - 文件：`scripts/fisco-init.sh`（新建）
   - 部署审计注册智能合约

**验收标准**：节点启动，合约部署成功

**预计工时**：2 天

---

## 六、任务依赖图

```
P0-5 (CODE_SCANNING) ← 无依赖，立即开始
    │
    └──→ P2-5 (场景运行时) ← 依赖 SandboxMode 统一

P1-4 (配额预填充) ← 无依赖，立即开始

P1-8 (PII NER) ← 无依赖，立即开始
    │
    └──→ P2-mitmproxy (流式审查)

P1-10 (模型去重) ← 无依赖，立即开始

P1-TLCP ← 依赖 Tongsuo 库

P2-5 (场景运行时) ← 依赖 SandboxMode 统一
    │
    └──→ P2-6 (训练 PyTorch)

FE-1/FE-2/FE-3 ← 无依赖，可并行
```

---

## 七、Sprint 规划

### Sprint 4（2 周）— 当前 Sprint
| 任务 | 工时 | 负责 |
|------|------|------|
| P0-5: CODE_SCANNING handler | 1 天 | 后端 |
| P1-4: 会话配额预填充 | 2 天 | 后端 |
| P1-10: 模型去重 | 2 天 | 后端 |
| P1-8: PII NER ML | 3 天 | 后端 |
| P1-TLCP: SM2 密码套件 | 5 天 | 后端 |
| **小计** | **13 天** | |

### Sprint 5（2 周）— 前端 + 场景运行时
| 任务 | 工时 | 负责 |
|------|------|------|
| P2-5: SandboxMode 统一 + 3 种运行时 | 10 天 | 后端 |
| FE-1: 身份/密钥页面 | 2 天 | 前端 |
| FE-2: 证书管理页面 | 2 天 | 前端 |
| FE-3: 连接器管理页面 | 2 天 | 前端 |
| **小计** | **16 天** | |

### Sprint 6+（远期）
| 任务 | 工时 |
|------|------|
| P2-1: L1 TEE | 10 天 |
| P2-2: GPU-TEE | 15 天 |
| P2-4: PG-in-TEE | 12 天 |
| P2-6: 训练 PyTorch | 10 天 |
| P2-7: CDC Kafka | 3 天 |
| P2-9: Carlini 记忆检测 | 4 天 |
| P2-10: 模型权重水印 | 5 天 |
| P2-12: FISCO BCOS | 5 天 |
| P2-mitmproxy: 流式审查 | 4 天 |
| DEP-3: FISCO Compose | 2 天 |
| **小计** | **70 天** |

---

## 八、工时汇总

| 阶段 | 任务数 | 工时 | 说明 |
|------|--------|------|------|
| Sprint 4 | 5 | 13 天 | P0 收尾 + P1 核心 |
| Sprint 5 | 4 | 16 天 | 场景运行时 + 前端 |
| Sprint 6+ | 10 | 70 天 | P2 高级场景 |
| **总计** | **19** | **99 天** | 2 人约 10 周 |
