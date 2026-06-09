# CDS 密态沙箱系统 · 设计规格 vs 实现分析报告

> 日期：2026-06-05
> 方法：逐 spec 对照代码实现，识别已实现/未实现/不合理设计
> 范围：specs/ 目录下全部设计文档 vs app/ + cds-frontend/ + k8s/ + helm/

---

## 一、执行摘要

| 指标 | 数值 |
|------|------|
| 设计文档数 | 34 份 |
| 后端服务文件 | 80+ Python 文件，24,000+ 行 |
| 前端页面 | 18 个页面组件 |
| API 端点 | 25 个路由模块 |
| 测试文件 | 70+ 个 |
| **整体实现率** | **~55%**（基础设施层 ~85%，应用层 ~25%） |
| **二轮复核实现率** | **~58%**（多项从 NOT_IMPLEMENTED 升级为 WORKING/PARTIAL） |

**核心结论**：基础设施层（沙箱隔离、KMS、审计、策略引擎、任务队列）实现质量较高；应用层（场景运行时、训练管线、互联互通）大面积缺失；部分设计规格过度工程化，需要裁剪。二轮复核发现多项此前标记为"未实现"的功能实际已有基础实现。

---

## 二、各子系统详细分析

### 2.1 KMS & 身份子系统 (SS-01)

| 需求 | 状态 | 证据 | 问题 |
|------|------|------|------|
| DEK 产品级密钥 (SM4-256) | ✅ 已实现 | `kms_service.generate_data_key()` 绑定 product_id | `encrypted_key` 存明文 hex，未用 KEK 包装 |
| 会话密钥 (<=24h) | ⚠️ 部分实现 | `generate_session_key()` + `distribute_key()` 存在 | 无 TTL 强制过期；未与沙箱生命周期联动 |
| 密钥轮换 | ✅ 已实现 | `rotate_dek` API 创建新版本，标记旧版 ROTATED | `rotate_key()` 是空操作，不重新包装数据 |
| 密钥吊销终止会话 | ❌ 未实现 | `revoke_dek` 仅更新 DB 状态 | 无机制终止活跃沙箱会话 |
| HSM 信封加密 (KEK 包装 DEK) | 🔧 仅接口 | `HSMAdapter` 有 `wrap_dek`/`unwrap_dek`；`SoftwareHSMAdapter` 实现 SM4-GCM | `kms_service` 从未调用 HSM 适配器 |
| SM2 X.509 证书 | ⚠️ 部分实现 | `certificate_service.issue_certificate()` | PEM 是 base64 文本，非真实 X.509 DER/ASN.1 |
| 证书链验证 | ✅ 已实现 | `verify_chain()` 遍历 parent_cert_id | 仅检查 DB 记录，不验证密码学签名 |
| TLCP 国密 TLS | ⚠️ 部分实现 | `TLCPService` 生成签名/加密证书 | SM2 密码套件名不存在，始终回退到 RSA |
| JWT 认证 | ✅ 已实现 | access + refresh token，`/refresh` 端点 | 无 |
| SM2 证书认证 | ❌ 未实现 | 仅有工具端点 `/generate-sm2-keys` | 身份认证仅用户名/密码 |
| MFA | ❌ 未实现 | 无任何 MFA 代码 | 规格要求 MFA 防欺骗 |
| 跨空间身份联邦 | ❌ 未实现 | 无代码 | 规格要求 |

**架构问题**：
- `key_metadata.py` 与 `kms.py` 模型重复（`DataEncryptionKey` vs `KeyMetadata`），无代码关联
- 两套并行证书系统：`certificate_service`（DB 异步）和 `tlcp_service`（内存字典），无共享状态
- HSM 适配器完全实现但与 `kms_service` 断开连接

---

### 2.2 合约引擎 & 策略子系统 (SS-02)

| 需求 | 状态 | 证据 | 问题 |
|------|------|------|------|
| 合约状态机 (9 状态) | ✅ 已实现 | `contract_service.py` 定义完整转换表 | 无 |
| SM2 双方签名 + 平台见证 | ✅ 已实现 | `sign()` 验证 SM2 签名 | 无证书时回退接受签名，削弱安全性 |
| 策略编译 (4 阶段) | ⚠️ 部分实现 | 提取约束 + 生成 Rego + SM4-GCM 加密 | 缺 OPA bytecode 编译 |
| 12 种 SandboxMode Rego 模板 | ⚠️ 部分实现 | 仅 1 个通用模板 | 规格定义 12 种模式各有独立 Rego |
| OPA 策略评估 | ✅ 已实现 | `policy_evaluator` 委托 `opa_client`；Python 回退 | 无 |
| 字段级 ACL | ✅ 已实现 | `_evaluate_field_acl()` 检查每字段读权限 | 无 |
| DP 预算按请求检查 | ❌ 未实现 | `dp_budget_ledger` 仅在签约时分配 | 无按请求扣减 epsilon |
| Redis 配额管理 | ❌ 未实现 | 无 Redis Lua 原子计数器 | `check_contract_constraints()` 仅字符串匹配 |
| 区块链锚定 | ❌ 未实现 | `blockchain_tx_hash` 字段存在但无写入代码 | 死字段 |

**不合理设计**：
- OPA bytecode 编译要求 OPA CLI 二进制文件，增加运维复杂度，边际收益小
- 12 种 SandboxMode Rego 模板维护面大，实际可能永不分化

---

### 2.3 任务调度器 (SS-03)

| 需求 | 状态 | 证据 | 问题 |
|------|------|------|------|
| 任务状态机 (6 活跃 + 3 终态) | ✅ 已实现 | `task_state_machine.py` 完整定义 | 无 |
| TIMED_OUT 状态 | ❌ 未实现 | 状态机无此状态；模型有 | 状态机与模型不一致 |
| 会话状态机 (8 状态) | ❌ 未实现 | 无会话状态机代码 | 规格定义完整会话生命周期 |
| 节点选择 (硬约束 + 评分) | ⚠️ 部分实现 | `node_selector.py` 按在线/心跳/能力筛选 | 缺 CPU/内存/GPU 余量检查；无热区随机抖动 |
| 代码扫描器 (AST 白名单) | ✅ 已实现 | `code_scanner.py` Python AST 分析 + SQL 验证 | 未集成到状态机 CODE_SCANNING 阶段 |
| 任务超时看门狗 | ⚠️ 部分实现 | `task_circuit_breaker.py` 同步检查 | 规格要求异步协程 |
| eBPF 异常检测 | ❌ 未实现 | 无 eBPF 代码 | 过度工程化 |
| 熔断器密钥吊销 | ❌ 未实现 | 仅日志警告 | 未调用 KMS 吊销 |
| 优先级队列 (1-10 级) | ⚠️ 部分实现 | 4 级 (LOW/NORMAL/HIGH/CRITICAL) | 简化合理 |
| Redis 队列 + 重试 | ✅ 已实现 | 双后端 + 指数退避 + 死信队列 | 无 |
| cgroup 资源隔离 | ❌ 未实现 | 仅 Redis/内存计数器 | 规格要求实际资源隔离 |

**架构问题**：
- `TaskScheduler`、`TaskQueue`、`TaskWorker` 三个模块未集成为统一流水线
- `task_worker.py` 跳过 CODE_SCANNING 和 PREPARING 阶段直接进入 RUNNING
- 规格要求 Celery + Pulsar，实际为简单 asyncio + Redis 列表

**不合理设计**：
- 10 级优先级 + 动态调整：4 级已足够
- eBPF 系统调用异常检测：MVP 阶段过度工程化
- 独立会话状态机 (8 状态)：通过简单状态字段即可管理

---

### 2.4 沙箱运行时 (SS-04)

| 需求 | 状态 | 证据 | 问题 |
|------|------|------|------|
| L3 bwrap 命名空间隔离 | ✅ 已实现 | `BwrapAdapter` PID/mount/network/IPC/UTS/user + seccomp BPF | 质量高 |
| L0 进程隔离 | ✅ 已实现 | `ProcessAdapter` bwrap + cgroup v2 | 非规格要求，额外实现 |
| L2 Firecracker microVM | ⚠️ 部分实现 | `firecracker_runtime.py` 真实 API + QEMU 回退 | 无 KVM 时回退为 bwrap 模拟 |
| L1 TEE (SGX/Occlum) | 🔧 仅桩 | `TEEAdapter.provision()` 返回 FAILED | 最高安全场景关键缺口 |
| Docker 回退 | ✅ 已实现 | `DockerAdapter` `--network none` + 只读 | 无 |
| K8s Pod 沙箱 | ✅ 已实现 | `k8s_sandbox.py` 完整生命周期 | 未接入 `SandboxRuntime` 适配器映射 |
| Seccomp BPF 系统调用过滤 | ✅ 已实现 | `sandbox_security.py` ctypes/libseccomp2 白名单 | 100+ 系统调用 |
| 安全销毁 (5 步) | ✅ 已实现 | `secure_destroy()` 终止+吊销密钥+擦除+取消任务+审计 | 无 |
| 场景路由 (9 种模式) | ❌ 未实现 | 无 `SceneRuntimeFactory` | 规格定义 9 种模式 |
| SecureDuckDB 引擎 | ❌ 未实现 | 仅有原始 SQL 运行器 | 缺策略引擎集成、自动脱敏视图 |
| PostgreSQL-in-TEE | ❌ 未实现 | 无代码 | STRUCTURED_APP 模式需要 |
| GPU-TEE 真实实现 | 🔧 仅桩 | `gpu_tee_runtime.py` 全部为 Stub | 无 NVIDIA CC API |
| LLM SFT 运行时 | 🔧 仅桩 | `_simulate_training_step()` 返回随机 loss | 无 PyTorch/HuggingFace |
| 视觉/多模态训练 | 🔧 仅桩 | `vision_pipeline.py` `apply()` 为直通 | 无真实训练 |
| 安全检查点 | ⚠️ 部分实现 | `secure_checkpoint.py` 存在但用 XOR 模拟 | 非真实 SM4-GCM |

**不合理设计**：
- 9 种场景运行时：每种都是独立产品级别，应分阶段交付
- PostgreSQL-in-TEE + pgcrypto + pg_tde + RLS：极度复杂，DuckDB 内存执行已足够
- GPU-TEE (NVIDIA CC)：需要 H100 硬件，多数部署不可用
- MPC 密钥分片作为 L2 补偿：分布式协议复杂度高，KMS + 软件证明已足够

---

### 2.5 输出审查网关 (SS-05)

| 需求 | 状态 | 证据 | 问题 |
|------|------|------|------|
| 场景路由 (8 种模式) | ⚠️ 部分实现 | 仅 4 种 (query/train/develop/application) | 缺 LLM SFT、视觉、多模态、ETL |
| 格式验证 (MIME 检测) | ⚠️ 部分实现 | 仅检查空输出 | 无 MIME 类型检测、二进制嗅探 |
| PII 检测 (正则 + NER) | ⚠️ 部分实现 | `pii_ner.py` 有正则 + 规则 NER | 未使用 ML 模型 (bert-base-chinese-pii-ner) |
| 数据重建检测 | ✅ 已实现 | `check_data_reconstruction()` + `check_field_reconstruction()` 阈值 5% | 无 |
| k-匿名检查 (k>=5) | ✅ 已实现 | `k_anonymity.py` 完整实现 | 无 |
| 差分隐私 (Laplace/Gaussian) | ✅ 已实现 | `DifferentialPrivacyEngine` + `dp_budget.py` 持久化 | 无 |
| MIA 探针 | ⚠️ 部分实现 | `mia_probe.py` 有 loss/confidence 分析 | 管线集成用简化启发式 |
| 文本记忆检测 | ⚠️ 部分实现 | `memorization_check.py` n-gram 重叠 | 阈值 0.3 vs 规格 0.08 |
| 零宽文本水印 | ✅ 已实现 | `watermark.py` U+200B/200C/200D/FEFF | 无 |
| LSB 数值水印 | ❌ 未实现 | 仅图像 LSB | 缺数值 LSB |
| 模型权重水印 (EmbMarker) | ❌ 未实现 | 无代码 | 活跃研究问题，非生产就绪 |
| SM2 签名 | ✅ 已实现 | `output_inspection.py` SM2 签名 + SM3 哈希 | 无 |
| 流式审查 | ✅ 已实现 | `streaming_inspector.py` 4KB 块扫描 + 熔断器 | 无 mitmproxy 集成 |
| 网络层强制 (iptables) | ❌ 未实现 | 无 iptables 代码 | 应属基础设施规格 |

**不合理设计**：
- 影子模型 MIA：每输出训练 N 个影子模型，计算成本不可接受
- EmbMarker 模型权重水印：活跃研究技术，非生产工具
- 文本记忆阈值 0.08：过于激进，会阻断大多数非平凡输出
- iptables 网络层强制：应用规格中混入基础设施关注点

---

### 2.6 审计 & 区块链子系统 (SS-06)

| 需求 | 状态 | 证据 | 问题 |
|------|------|------|------|
| 审计事件类型枚举 (40+) | ⚠️ 部分实现 | 自由格式字符串，无枚举验证 | 规格定义 40+ 类型 |
| ClickHouse 审计模式 | ✅ 已实现 | `clickhouse_schema.py` 60+ 列 | 无 bloom filter 索引；列名语义不同 |
| SM2 审计签名 | ⚠️ 部分实现 | `audit_service.py` SM2 签名 | 签名失败静默吞掉；软件密钥非 TEE 内部 |
| Merkle 批量管线 (1000 事件/30s) | ❌ 未实现 | 无 Kafka 消费者 | 仅 API 触发 |
| 争议仲裁 / Merkle 证明 | ✅ 已实现 | `generate_proof` + `verify_proof` | 证明依赖重建树，跨批次时脆弱 |
| TC609 合规报告 | ✅ 已实现 | `compliance_report.py` 生成报告 | 逻辑简化，部分检查硬编码 |
| 实时告警规则 (5 条) | ⚠️ 部分实现 | `alert_engine.py` 4 种告警 | 缺 MIA 失败、沙箱异常告警 |
| ClickHouse bloom 索引 | ❌ 未实现 | 无 bloom 索引定义 | `AuditPartitionManager` 有 Python BloomFilter 但未连接 |
| SM3 Merkle 树 | ✅ 已实现 | `merkle_service.py` gmssl SM3 | 无 |
| FISCO BCOS 客户端 | 🔧 仅桩 | `blockchain_service.py` 注释掉导入；`blockchain_adapter.py` 生成假 tx hash | 内存字典，重启丢失 |
| 审计注册智能合约 | ❌ 未实现 | 无 .sol 文件 | 无合约部署、无 ABI |
| 批量锚定服务 | ⚠️ 部分实现 | `POST /audit/anchor` 手动提供 record_ids | 无自动批量 |
| PG 追加日志适配器 | ✅ 已实现 | `PGAppendOnlyAdapter` 哈希链 | 仅内存，重启丢失 |

**不合理设计**：
- Kafka 驱动批量管线：对 v1 而言过重，简单定时任务即可
- 40+ 审计事件类型：多数无对应代码，应精简核心集 + 可扩展性
- TEE 内部 SM2 签名：规格不承认非 TEE 模式

---

### 2.7 互联互通 (SS-08)

| 需求 | 状态 | 证据 | 问题 |
|------|------|------|------|
| 跨空间身份联邦 (SM2-JWT) | ✅ 已实现 | `federation_connector.py` SM2IdentityProvider + JWTIdentityProvider | 无 |
| 信任关系管理 | ✅ 已实现 | `FederationConnector` 内存字典 | 重启丢失，无 DB 持久化 |
| 代理跨空间请求 | ✅ 已实现 | `_execute_remote_request` httpx 真实调用 | 无 |
| 连接器注册 (API Key) | ✅ 已实现 | `connectors.py` + DB 模型 | 无 |
| 远程目录浏览 | ✅ 已实现 | `/remote/catalog` 端点 | 无 |
| 数据同步 Worker | ❌ 未实现 | 无 `sync_worker.py` 或 Celery 任务 | 规格定义详细同步逻辑 |
| 协议翻译 (gRPC/MQTT) | ❌ 未实现 | 仅 REST | ProtocolType 枚举存在但无代码 |
| mTLS | ❌ 未实现 | httpx `verify=True` 无 mTLS 配置 | 无 |

---

### 2.8 训练管线 (SS-07)

| 需求 | 状态 | 证据 | 问题 |
|------|------|------|------|
| 预训练语料管线 | ⚠️ 部分实现 | `TrainingPipelineManager` 有 MinHash 相似度估算 | 启发式，非真实 MinHash LSH |
| SFT 管线 (格式/质量/PII) | ✅ 已实现 | PII 正则清洗、质量评分、格式转换 | PII 仅正则 |
| RAG 管线 | 🔧 仅桩 | 阶段定义无逻辑 | 无分块/嵌入 |
| LLM SFT 运行时 | 🔧 仅桩 | `_simulate_training_step()` 随机 loss | 无 PyTorch/HuggingFace |
| 训练配置验证 | ✅ 已实现 | `training_config_validator.py` 参数边界 + DP-SGD 验证 | 无 |
| 训练审计 (SM3 哈希链) | ✅ 已实现 | `training_audit.py` SM3 链 | 仅内存 |
| 水印注入 | 🔧 仅桩 | `_inject_watermark` 记录 SM3 哈希 | 不修改模型权重 |

---

### 2.9 数据管线 & 存储 (SS-09)

| 需求 | 状态 | 证据 | 问题 |
|------|------|------|------|
| 确定性 SM4 加密 (SM4-SIV) | ✅ 已实现 | `column_encryption.py` SM4-CBC 派生 IV | gmssl 回退用 XOR |
| 随机 SM4 加密 (SM4-GCM) | ⚠️ 部分实现 | SM4-CBC + 随机 nonce 作为 GCM 替代 | gmssl 缺 GCM，标签为 SHA-256 派生 |
| 查询重写引擎 | ⚠️ 部分实现 | `db_access_proxy.py` SQL 解析 + RLS + LIMIT 注入 | 规格的列级重写未完全匹配 |
| DuckDB 安全引擎 | ✅ 已实现 | `secure_duckdb.py` 内存/tmpfs/持久模式 + 脱敏视图 + 列加密 | 无 |
| DB 访问代理 (RLS + 列加密) | ✅ 已实现 | `db_access_proxy.py` SQL 拦截 + RLS + 加密/解密 | 无 |
| CDC Agent (Debezium) | 🔧 仅桩 | `cdc_agent.py` Debezium 配置生成 | 无 Kafka 生产者 |
| 目录同步 | ✅ 已实现 | `catalog_sync.py` 全量/增量同步 | 无 |
| 存储服务 (MinIO 信封加密) | ✅ 已实现 | `storage_service.py` SM4-GCM 信封加密 | 无 |
| LUKS 磁盘加密 | ❌ 未实现 | 无代码或脚本 | 运维关注点 |

---

### 2.10 前端

| 需求 | 状态 | 证据 | 问题 |
|------|------|------|------|
| 角色访问控制 | ✅ 已实现 | `ProtectedRoute.tsx` + `RoleRoute.tsx` | 无 |
| 仪表盘 | ✅ 已实现 | `Dashboard/index.tsx` | 无 |
| 数据产品 CRUD | ✅ 已实现 | ProductCreate/List/Detail | 无 |
| 目录搜索 | ✅ 已实现 | `Catalog/index.tsx` | 无 |
| 合约管理 | ✅ 已实现 | ContractCreate/List/Detail | 无 |
| 沙箱会话管理 | ✅ 已实现 | SessionList/Detail + DataProductDev | 无 |
| 审计日志查看 | ✅ 已实现 | `AuditLog.tsx` | 无 |
| 联邦仪表盘 | ✅ 已实现 | `CrossSpaceDashboard.tsx` | 无 |
| 训练仪表盘 | ✅ 已实现 | `TrainingDashboard.tsx` | 无 |
| 输出控制 | ✅ 已实现 | `InspectionPipeline.tsx` | 无 |
| 身份/密钥管理页面 | ❌ 未实现 | 无 Identity/ 或 Keys/ 页面 | 规格要求 /identity、/identity/keys |
| 证书管理 | ❌ 未实现 | 无证书管理页面 | 无 |
| 连接器管理 UI | ❌ 未实现 | 仅有后端 API | 无 |
| 告警管理 | ❌ 未实现 | 无 /monitoring/alerts 页面 | 无 |
| 区块链验证页面 | ❌ 未实现 | 无 /audit/verify 页面 | 无 |

---

### 2.11 部署

| 需求 | 状态 | 证据 | 问题 |
|------|------|------|------|
| Docker Compose (开发) | ✅ 已实现 | postgres, redis, clickhouse, opa, cds-api | 无 FISCO BCOS 节点 |
| Dockerfile.api | ✅ 已实现 | python:3.11-slim, bubblewrap, 非 root | 无 |
| K8s 清单 | ✅ 已实现 | namespace, secrets, configmaps, 各组件 | 无 |
| Helm Chart | ✅ 已实现 | Chart.yaml, values.yaml, templates | 无 |
| 前端 Dockerfile | ❌ 未实现 | 无 cds-frontend Dockerfile | 无 |
| Vault 集成 (Compose) | ❌ 未实现 | docker-compose.yml 无 Vault | 仅 K8s |
| 区块链节点 (FISCO-BCOS) | ❌ 未实现 | 无 manifest | 无 |

---

## 三、关键发现汇总

### 3.1 实现完成度热力图

```
子系统                    实现率    评价
─────────────────────────────────────────────
KMS & 身份               45%     接口完整但关键链断开
合约引擎 & 策略           65%     核心流程可用，DP 预算缺失
任务调度器                60%     状态机好，组件未集成
沙箱运行时 (基础设施)      85%     bwrap/seccomp/会话管理优秀
沙箱运行时 (场景运行时)    15%     9 种模式几乎全空
输出审查网关              65%     核心管线可用，高级功能缺失
审计 & 区块链             55%     审计可用，区块链全桩
互联互通                  70%     核心联邦可用，同步缺失
训练管线                  25%     配置验证好，运行时全桩
数据管线 & 存储           75%     DuckDB/加密/代理优秀
前端                      70%     核心页面齐全，管理页面缺失
部署                      80%     Compose/K8s/Helm 齐全
```

### 3.2 P0 安全关键缺口

1. **DEK 明文存储**：密钥以明文 hex 存数据库，未用 provider 公钥加密（规格要求 SM2 加密）
2. **密钥吊销不终止会话**：吊销密钥后活跃沙箱继续运行（规格要求级联吊销分发会话 + 通知沙箱）
3. **L1 TEE 完全缺失**：最高安全级别无真实 TEE 支持
4. **SM2 签名用软件密钥而非 HSM**：规格要求 `hsm.sm2_sign()` 在 HSM 内签名
5. **审计签名静默失败**：SM2 签名异常被吞掉
6. **区块链全桩**：所有锚定操作在内存字典，重启丢失
7. **DP 预算仅分配不扣减**：规格要求 PDP 内 per-request 扣减 + SM2 签名更新
8. **HSM 适配器完全断开**：`hsm_adapter.py` 已实现但 `kms_service` 从未调用
9. **密钥分发无 TEE Quote 验证**：规格要求验证 MRENCLAVE 白名单，实际跳过
10. **证书吊销不联动 DEK**：规格要求吊销证书 → 暂停关联 DEK → 吊销活跃分发会话

### 3.3 P1 功能缺口

1. **场景运行时**：13 种 SandboxMode 中仅 4 种有基础实现
2. **任务组件未集成**：Scheduler/Queue/Worker 三模块断开
3. **会话状态机缺失**：规格定义 8 状态无代码
4. **ClickHouse bloom 索引缺失**：审计查询性能无保障
5. **Merkle 批量管线缺失**：无自动批量锚定
6. **身份/密钥管理前端缺失**
7. **数据同步 Worker 缺失**
8. **QuotaManager Redis Lua 原子操作缺失**：规格定义完整 Lua 脚本，实际仅字符串匹配
9. **SecureDuckDBEngine 缺失**：无策略引擎集成、无自动脱敏视图、无 SQL 重写
10. **合约协商流程缺失**：规格支持 NEGOTIATING 循环，实际无协商 API
11. **密钥分发记录表缺失**：规格定义 `key_distributions` 表（tee_quote_hash、mpc_shard 等），实际无此表
12. **DP 预算物化视图缺失**：规格定义 `dp_budget_status` 物化视图，实际无

---

## 四、不合理设计识别

### 4.1 过度工程化（建议裁剪）

| 设计 | 规格位置 | 问题 | 建议 |
|------|---------|------|------|
| 13 种 SandboxMode | SS-02 | 过度细分，`structured_modeling` 与 `structured_query` 差异可配置化 | 精简为 6 种核心模式 + 配置参数 |
| 9 种场景运行时 | SS-04 | 每种都是独立产品级别 | 分 3 期：先 query/train/develop，再 LLM，最后视觉 |
| eBPF 异常检测 | SS-03 | 需要内核级工具 | 移至远期，用应用层监控替代 |
| 13 种 Rego 模板 | SS-02 | 维护面大，实际可能不分化 | 保留 4 种核心模板 (query/modeling/training/dev) |
| 影子模型 MIA | SS-05 | 训练 N 个影子模型不可接受 | 用 Loss-based Threshold Attack |
| EmbMarker 权重水印 | SS-05 | `model_watermark` 包不存在于任何 Python 仓库 | 用成熟方案 (Uchida 2017) 或跳过 |
| 10 级优先级 + 动态调整 | SS-03 | 4 级已足够 | 简化为 4 级 |
| PostgreSQL-in-TEE | SS-04 | 极度复杂，pg_tde 为实验性扩展 | DuckDB 内存执行已足够 |
| MPC 密钥分片 | SS-04 | 分布式协议复杂度高，需数商在线参与 | KMS + 软件证明已足够 |
| Kafka 批量管线 | SS-06 | 对 v1 过重 | 简单定时任务或 Redis Stream |
| 同态加密 (Level 3) | 存储规格 | 性能差数个数量级 | 移至远期研究 |
| gRPC 内部接口 | SS-01 | KMS 作为单体服务的一部分，REST 已足够 | 移除 gRPC 要求 |
| CRL 完整 + 增量双机制 | SS-01 | 内部系统用 OCSP 在线验证更实用 | 简化为 OCSP + 定期完整 CRL |
| HSM 内 Shamir 分片 | SS-01 | 多数商用 HSM 不原生支持 Shamir 操作 | KMS 软件层分片，仅加密委托 HSM |
| PDP 在 TEE 内运行 | SS-02 | OPA 无密钥材料，在 TEE 内运行增加启动延迟 | PDP 在普通进程运行，策略包加密传输保证完整性 |
| iptables OUTPUT 链强制转发 | SS-05 | 应用层不应依赖内核级规则，容器化中行为不可预测 | 用 sidecar proxy (Envoy) 或应用层代理 |
| TextMemorizationStage 阈值 0.08 | SS-05 | 8% token 重叠率会阻断大多数正常输出 | 提高到 0.15-0.20，按场景配置 |

### 4.2 设计矛盾

| 矛盾 | 说明 |
|------|------|
| 规格要求 Celery + Pulsar vs 实际 asyncio + Redis | 架构选型不一致，规格未更新 |
| 规格要求 SM4-GCM vs 实际 SM4-CBC | gmssl 库不支持 GCM，规格未考虑 |
| 规格列名 space_id/node_id vs 代码 user_id/sandbox_id | ClickHouse 模式独立设计，语义不同 |
| 两套证书系统并行 | certificate_service vs tlcp_service 无共享状态 |
| HSM 适配器已实现但未连接 | kms_service 有自己的 Vault 客户端代码 |
| key_metadata.py 与 kms.py 模型重复 | DataEncryptionKey vs KeyMetadata 无关联 |
| SS-01 要求 HSM 内密钥操作 vs SS-04 假设 TEE 内自主解密 | 两处对 DEK 明文出现在哪个进程内存中描述不一致 |
| SS-02 定义 13 种 SandboxMode vs SS-05 定义 8 种规则集 | SS-02 有 joint_federated/api_service 但 SS-05 无对应规则集 |
| SS-04 脱敏视图用 md5() vs 国密合规要求 SM3 | DuckDB 脱敏用 md5 哈希，违反 GM/T 标准 |
| SS-01 定义 gRPC protobuf vs 实际 REST | 规格定义 8 个 RPC，项目无 gRPC 依赖 |
| SS-02 QuotaManager Lua vs SS-04 QuotaClient | 两处互相引用但均未实现 |

### 4.3 安全风险设计

| 风险 | 严重性 | 说明 |
|------|--------|------|
| SM3 回退到 SHA-256 | 高 | 静默破坏 GM/T 合规 |
| exec() 用户代码执行 | 高 | Python restricted builtins 可绕过 (`__subclasses__` 等) |
| SQL 标识符注入 | 高 | DuckDB register() 参数未转义 |
| DEK 未用 provider 公钥加密存储 | 高 | 规格要求 SM2 加密，实际存明文 hex |
| SM2 签名用软件密钥而非 HSM | 高 | 规格要求 `hsm.sm2_sign()` 在 HSM 内签名 |
| DuckDB 脱敏视图用 md5() | 高 | 国密标准要求 SM3，md5 违反 GM/T 合规 |
| 密钥分发无 TEE Quote 验证 | 高 | 规格要求验证 MRENCLAVE 白名单，实际跳过 |
| 审计签名静默失败 | 中 | 签名异常被 except: pass 吞掉 |
| DP 预算内存存储 | 中 | 进程重启丢失（已部分修复为 DB 持久化） |
| 信任状态仅内存 | 中 | 重启丢失所有跨空间信任关系 |
| 密钥分发会话无 TTL 强制过期 | 中 | 规格定义 expires_at 但无定时清理过期会话 |

---

## 五、修复建议

### 5.1 立即修复（P0）

1. **连接 HSM 适配器到 KMS 服务**：将 `kms_service` 的 Vault 客户端替换为 `HSMAdapter` 调用
2. **密钥吊销触发会话终止**：`revoke_dek` 中调用 `sandbox_manager.terminate_sessions_by_key()` + `sandbox_notifier.send_key_revocation()`
3. **修复 SM2 签名回退**：无证书时拒绝签名而非接受
4. **修复审计签名静默失败**：签名异常应记录告警而非吞掉
5. **DP 预算按请求扣减**：在 `policy_evaluator.evaluate()` 中添加 epsilon 检查和扣减
6. **SQL 标识符转义**：`re.sub(r'[^a-zA-Z0-9_]', '_', product_id)`
7. **DEK 改用 provider 公钥加密存储**：实现 `sm2_encrypt(dek_plaintext, provider_pubkey)`
8. **DuckDB 脱敏视图 md5() 改 SM3**：注册 SM3 UDF 替代 md5()
9. **证书吊销联动 DEK 暂停**：实现规格定义的级联吊销逻辑
10. **密钥分发添加 TEE Quote 验证**：至少实现软件级证明检查

### 5.2 短期修复（Sprint 2-3）

1. **集成任务组件**：将 TaskScheduler/Queue/Worker 统一为流水线
2. **添加会话状态机**：实现规格定义的 8 状态生命周期
3. **ClickHouse bloom 索引**：添加 session_id/contract_id/actor/event_type 索引
4. **自动批量 Merkle 管线**：用 Redis Stream 替代 Kafka
5. **区块链适配器持久化**：将内存字典替换为 DB 存储
6. **连接 K8s 沙箱到适配器映射**

### 5.3 中期改进（Sprint 4+）

1. **场景运行时第一期**：实现 DuckDB 策略引擎 + 脱敏视图
2. **ML-based PII NER**：集成 bert-base-chinese-pii-ner
3. **前端管理页面**：身份/密钥/证书/连接器/告警
4. **数据同步 Worker**：实现 DB/File/API 连接器同步
5. **前端 Dockerfile + Vault Compose 集成**

### 5.4 规格更新建议

1. 将 13 种 SandboxMode 精简为 6 种核心模式 + 配置参数
2. 将 9 种场景运行时拆分为 3 期交付
3. 移除 eBPF 异常检测，用应用层监控替代
4. 将影子模型 MIA 替换为 Loss-based Threshold Attack
5. 将 13 种 Rego 模板精简为 4 种核心模板 (query/modeling/training/dev)
6. 更新密码学选型：标注 gmssl 不支持 GCM 的限制
7. 统一列命名约定（space_id vs user_id）
8. 移除 gRPC 内部接口要求，统一为 REST
9. 简化 CRL 机制为 OCSP 在线验证 + 定期完整 CRL
10. 将 PDP 运行环境从 TEE 内改为普通进程（OPA 无密钥材料）
11. 将 TextMemorizationStage 阈值从 0.08 提高到 0.15-0.20
12. 将 iptables 强制转发改为 sidecar proxy 方案
13. 修正 SS-01 与 SS-04 关于 DEK 明文位置的描述不一致
14. 为 SS-02 的 joint_federated/api_service 模式补充 SS-05 规则集
15. 将 DuckDB 脱敏视图的 md5() 改为 SM3（或注册 SM3 UDF）

---

## 六、补充分析：之前不可读的 4 份核心 Spec

> 以下分析基于 ss-01-kms-identity.md、ss-02-contract-policy.md、ss-04-sandbox-runtime.md、ss-05-output-gateway.md 的完整内容。

### 6.1 SS-01 密钥管理 & 身份/PKI（补充）

#### 新发现的规格要求 vs 实现

| 需求 | 状态 | 证据 | 问题 |
|------|------|------|------|
| SM2 X.509v3 证书完整签发 (TBSCertificate + DER 编码) | ❌ 未实现 | 规格定义完整的 `CertManager.issue_certificate()` 流程：构造 TBSCertificate → DER 编码 → SM3 摘要 → HSM 内 SM2 签名 | 实际 `certificate_service` 生成 base64 文本伪 PEM，无 ASN.1/DER 编码 |
| HSM 内密钥重包装 (rewrap_key) | ❌ 未实现 | 规格要求 DEK 在 HSM 内部解密后用 SK 重新加密，明文不出 HSM | `kms_service` 直接从 DB 读取明文 hex DEK，无 HSM 调用 |
| Shamir (2,2) MPC 密钥分片 | ❌ 未实现 | 规格定义 `MPCKeyManager.distribute_l2_shards()` + `reconstruct_key_in_mpc()` | 无任何 MPC 代码；`mpc_service.py` 存在但是空壳 |
| MRENCLAVE 白名单校验 | ❌ 未实现 | 规格要求分发 DEK 时验证 TEE Quote 中的 MRENCLAVE | `remote_attestation.py` 有接口但全部为模拟 |
| 密钥分发记录 (KeyDistribution) | ❌ 未实现 | 规格定义完整的 `key_distributions` 表：tee_quote_hash、mpc_shard_k2_encrypted、session_key_enc、expires_at | 无此表；仅有 `KeyMetadata` 模型（字段不同） |
| 密钥审计事件 (KeyAuditEvent) | ❌ 未实现 | 规格定义 `key_audit_events` 表 + RLS insert-only 策略 | 无专用密钥审计表；审计写入通用 audit_log |
| CRL 完整 + 增量双机制 | ❌ 未实现 | 规格定义 `CRLManager`：完整 CRL 每 24h、增量 CRL 每 15min、Redis 缓存 | `certificate_service.generate_crl()` 生成字典但无 DER 编码、无增量机制 |
| gRPC 内部服务接口 | ❌ 未实现 | 规格定义完整 protobuf：`KMSService` 含 IssueCertificate、DistributeKeyToTEE、VerifyAttestationQuote 等 8 个 RPC | 仅有 REST API，无 gRPC |
| 密钥使用次数限制 (max_usage) | ❌ 未实现 | 规格 `data_encryption_keys.max_usage` 字段 + 分发时 `usage_count += 1` | `DataEncryptionKey` 模型无 usage_count/max_usage 字段 |
| HSM 故障降级策略 (主备切换) | ❌ 未实现 | 规格定义 3 级降级：健康→响应慢→主备切换→应急 Vault | 无降级逻辑；`hsm_adapter.py` 仅单实例 |
| 证书吊销联动暂停 DEK | ❌ 未实现 | 规格 `revoke_certificate()` 要求：吊销证书 → 暂停关联 DEK → 吊销活跃分发会话 → 通知沙箱 | `certificate_service.revoke_certificate()` 仅更新证书状态 |
| 密钥材料内存保护 (mlock + swap 禁用) | ❌ 未实现 | 规格要求 KMS 进程启用 `mlock` + 禁用 swap | 无内存锁定代码 |

#### 新发现的不合理设计

| 设计 | 问题 | 建议 |
|------|------|------|
| **gRPC 内部接口** | KMS 作为单体服务的一部分，不需要独立 gRPC 接口。REST 已足够 | 移除 gRPC 要求，统一为 REST |
| **HSM 内 Shamir 分片** | 要求 HSM 支持 Shamir 秘密分享操作，多数商用 HSM 不原生支持此功能 | 用 KMS 软件层做分片，仅将最终加密操作委托 HSM |
| **完整 CRL + 增量 CRL 双机制** | 对于内部系统，OCSP 在线验证更实用；CRL 增量机制增加复杂度 | 简化为 OCSP 在线验证 + 定期完整 CRL |
| **MRENCLAVE 白名单由 OTA 更新** | 运营方 + 安全团队双人审批 + 变更日志，流程过重 | 用配置文件 + 版本控制即可 |

---

### 6.2 SS-02 数字合约 & 策略引擎（补充）

#### 新发现的规格要求 vs 实现

| 需求 | 状态 | 证据 | 问题 |
|------|------|------|------|
| 13 种 SandboxMode 枚举 | ⚠️ 部分实现 | 规格定义 13 种：structured_query/modeling/app、llm_sft/pt、vision、multimodal、analysis、semi_etl、product_dev、api_service、joint_federated | 实现仅 4 种 (query/train/develop/application) |
| PENDING_SIGNATURE 状态 | ❌ 未实现 | 规格状态机有 PENDING_SIGNATURE（双方签完但未链上备案） | 实现直接 SIGNED→ACTIVE |
| NEGOTIATING 协商循环 | ❌ 未实现 | 规格支持买方提出修改 → 数商回应 → 循环 | 无协商 API；合约创建后直接签署 |
| DP 预算物化视图 (dp_budget_status) | ❌ 未实现 | 规格定义 `MATERIALIZED VIEW dp_budget_status` 实时预算视图 | 无物化视图；预算计算在 Python 层 |
| QuotaManager Redis Lua 原子操作 | ❌ 未实现 | 规格定义完整 Lua 脚本 `CHECK_AND_CONSUME_LUA`：GET→检查→INCRBY 原子 | `quota_manager.py` 有 Redis 但无 Lua 脚本 |
| 合约链上存证 (FISCO BCOS) | ❌ 未实现 | 规格定义 `ContractBlockchainService.submit_contract()`：构造存证数据 → 调用 FISCO 合约 → 等待 receipt | `blockchain_tx_id` 字段存在但无写入代码 |
| 结构化查询 Rego 模板 (完整) | ❌ 未实现 | 规格定义详细 Rego：SELECT_AGGREGATE 检查、within_dp_budget、sensitive_field_violation、deny_export | 实际仅 1 个通用模板 |
| AI 训练 Rego 模板 | ❌ 未实现 | 规格定义：valid_model_architecture 白名单、allow_gradient_output（联邦）、allow_model_export（记忆性+水印检查） | 无对应 Rego |
| 开发沙箱 Rego 模板 | ❌ 未实现 | 规格定义：is_product_owner 检查、sample_within_limit (≤1000行)、deny_data_export、EXPORT_PRODUCT_PACKAGE | 无对应 Rego |
| PDP 在 TEE 内运行 | ❌ 未实现 | 规格要求 `PolicyDecisionPoint` 在 TEE Enclave 内运行，策略包 SM3 哈希验证后加载 | PDP 在普通进程中运行 |
| 配额会话初始化 (init_session_quotas) | ❌ 未实现 | 规格要求会话创建时初始化所有配额计数器（rows/bytes/tasks/gpu_h/api） | 无会话级配额初始化 |
| 合约协商 API (POST /negotiate) | ❌ 未实现 | 规格定义 `NegotiateRequest`：proposed_changes + message | 无协商端点 |

#### 新发现的不合理设计

| 设计 | 问题 | 建议 |
|------|------|------|
| **13 种 SandboxMode** | 过度细分。`structured_modeling` 和 `structured_query` 的策略差异可配置化，不需要独立模式 | 精简为 6 种核心模式 + 配置参数 |
| **PDP 在 TEE 内运行** | OPA 是纯计算引擎，无密钥材料。在 TEE 内运行增加启动延迟和 EPC 占用 | PDP 在普通进程运行即可，策略包加密传输保证完整性 |
| **DP 预算物化视图** | PostgreSQL 物化视图需要手动刷新，有 ≤1s 延迟，且增加 DB 复杂度 | 用 Redis 原子计数器实时追踪，DB 异步持久化 |
| **合约协商循环** | MVP 阶段不需要复杂协商流程，直接创建→签署→激活即可 | 移至 v2，v1 用"拒绝并重新创建"替代 |

---

### 6.3 SS-04 沙箱运行时（补充）

#### 新发现的规格要求 vs 实现

| 需求 | 状态 | 证据 | 问题 |
|------|------|------|------|
| 5 阶段初始化流程 (Phase 1-5) | ⚠️ 部分实现 | 规格定义：TEE启动→策略包加载→密钥接收→数据加载→场景运行时初始化 | 实际仅 Phase 1 (隔离启动) + Phase 3 (密钥) 完成；Phase 2/4/5 缺失 |
| SecureDuckDBEngine 完整实现 | ❌ 未实现 | 规格定义：策略引擎集成、自动脱敏视图 (HASH/REDACT/GENERALIZE_TOP_K)、SQL 重写到安全视图、策略评估前检查 | `secure_duckdb.py` 有基础 DuckDB 但无策略集成、无视图重写 |
| DuckDB 分块解密加载 (Arrow) | ❌ 未实现 | 规格定义：从 MinIO 分块下载 → SM4-GCM 解密 → SM3 完整性校验 → Arrow 反序列化 → 注册内存表 | 无此流程；数据加载为手动 CSV/JSON |
| PostgreSQL-in-TEE 完整架构 | ❌ 未实现 | 规格定义：pgcrypto SM4 + pg_tde 透明加密 + RLS 行级安全 + sandbox_results schema 隔离 | 无任何 PG-in-TEE 代码 |
| DataModelingRuntime (ML UDF) | ❌ 未实现 | 规格定义：DuckDB UDF 注册 (train_xgb_classifier, feature_importance)、Python 建模脚本执行 | 无 ML UDF |
| StructuredAppRuntime (应用部署) | ❌ 未实现 | 规格定义：镜像签名验证、受限子进程 (cgroup)、DataProxy (Unix Socket)、AppOutputProxy | 无应用部署代码 |
| ProductDevRuntime (开发沙箱) | ❌ 未实现 | 规格定义：采样行限制 (≤1000)、产品定义工作区、脱敏规则预览、产品包导出 (不含数据) | 无开发沙箱专用运行时 |
| GPU-TEE NVIDIA CC 模式 | 🔧 仅桩 | 规格定义：GPU CC 激活验证、CPU-GPU 加密通道、通道密钥协商 | `gpu_tee_runtime.py` 全部 Stub |
| LLMSFTRuntime (LoRA/QLoRA) | 🔧 仅桩 | 规格定义：ALLOWED_BASE_MODELS 白名单、peft LoRA 配置、SecureSFTTrainer、记忆性检测、水印注入 | 训练为模拟 |
| SecureTextDataLoader | ❌ 未实现 | 规格定义：加密数据分块解密 + tokenize + PII 自动脱敏 + 批次生成 | 无安全数据加载器 |
| VisionModelTrainingRuntime | 🔧 仅桩 | 规格定义：图像预处理流水线 (解码→人脸模糊→DICOM脱敏→车牌马赛克→增强) | `vision_pipeline.py` 有步骤但 apply() 为直通 |
| MultimodalTrainingRuntime | 🔧 仅桩 | 规格定义：图文混合加载器、LoRA 多模态微调、图像重建攻击检测 (CLIP 相似度) | 仅模拟 |
| SemiStructuredRuntime | ❌ 未实现 | 规格定义：JSONL 自动 Schema 推断 + 嵌套展开、Grok 日志解析、字段脱敏视图 | 无半结构化专用运行时 |
| L2 Firecracker + eBPF + MPC | ⚠️ 部分实现 | 规格定义：Firecracker 启动 → gVisor → MPC 密钥重组 → eBPF 监控探针 → Seccomp 加严 | Firecracker 有代码但回退为模拟；无 eBPF、无 MPC |
| 安全销毁 5 步流程 | ✅ 已实现 | 规格：KMS吊销→TEE安全清理→磁盘擦除→状态更新→资源释放 | `secure_destroy()` 匹配 |
| DataLoaderFactory (场景路由) | ❌ 未实现 | 规格定义 `DataLoaderFactory.create(sandbox_mode)` 按场景创建加载器 | 无工厂模式 |

#### 新发现的不合理设计

| 设计 | 问题 | 建议 |
|------|------|------|
| **SecureDuckDBEngine 的 SQL 重写** | 要求将 `FROM table` 自动替换为 `FROM table_secure`，需要完整 SQL 解析器，易出错 | 用 DuckDB 权限系统直接 revoke 原始表访问，只 grant 视图 |
| **PG-in-TEE 的 pg_tde 扩展** | pg_tde 是 PostgreSQL 社区实验性扩展，生产可用性存疑 | 用 pgcrypto + dm-crypt 加密 tmpfs 更可靠 |
| **StructuredAppRuntime 的 DataProxy** | 要求应用通过 Unix Socket 访问数据库代理，增加网络层复杂度 | 用 PostgreSQL RLS + 连接池直接控制 |
| **LLMSFTRuntime 的 ALLOWED_BASE_MODELS 硬编码** | 模型白名单硬编码在代码中，每次新增模型需改代码 | 用配置文件或数据库维护白名单 |
| **VisionModelTrainingRuntime 的图像预处理** | 要求在 CPU-TEE 内做完整图像预处理（人脸检测、DICOM解析），计算密集 | 轻量级预处理在 CPU-TEE，重计算在 GPU-TEE |
| **MPC 密钥重组作为 L2 核心机制** | MPC 协议复杂度高，且需要数商在线参与，运维约束大 | L2 用 KMS + 远程证明（软件级）即可，MPC 作为可选增强 |

---

### 6.4 SS-05 输出审查网关（补充）

#### 新发现的规格要求 vs 实现

| 需求 | 状态 | 证据 | 问题 |
|------|------|------|------|
| 8 种场景规则集注册表 (InspectionRuleRegistry) | ⚠️ 部分实现 | 规格定义完整 RULE_SETS 字典：8 种模式各有独立 Stage 列表 | 仅实现 4 种模式的规则集 |
| PII 双层检测 (正则 + NER 模型) | ⚠️ 部分实现 | 规格要求 `bert-base-chinese-pii-ner` ML 模型作为 Layer 2 | 仅正则层，NER 为规则匹配非 ML |
| NER 触发条件 (正则命中>=1 或文本>500字) | ❌ 未实现 | 规格定义精确触发逻辑 | 无条件触发 NER |
| 商业机密关键词扫描 | ❌ 未实现 | 规格 Stage 2 包含此检查 | 无商业机密扫描代码 |
| MIA Shadow Model Attack (完整) | ⚠️ 部分实现 | 规格定义：加载模型→获取成员/非成员样本→计算损失→阈值攻击→TPR/TNR→advantage | 实际用简化启发式 (`confidence * 1/(1+loss)`) |
| TextMemorizationStage (Carlini 方法) | ⚠️ 部分实现 | 规格定义：前缀样本构造→贪婪解码→token 重叠率→p95 检查 | 实际用 n-gram 重叠，阈值 0.3 vs 规格 0.08 |
| LSB 数值水印 | ❌ 未实现 | 规格 `_inject_lsb()` 对 int/float/DataFrame 注入 | 仅零宽文本水印 |
| EmbMarker 模型权重水印 | ❌ 未实现 | 规格 `_inject_weight_watermark()`：target_layers=["lm_head", "embed_tokens"], max_perturbation=1e-5 | 无权重水印代码 |
| iptables OUTPUT 链强制转发 | ❌ 未实现 | 规格要求网络层强制所有出向流量经过审查模块 | 无 iptables 配置 |
| AppOutputProxyStage (mitmproxy-in-TEE) | ❌ 未实现 | 规格定义：4KB 块审查、PII 自动脱敏、滚动窗口聚合检查、会话级熔断 | `streaming_inspector.py` 有逻辑但无 mitmproxy |
| ImageReconstructionProbeStage | ❌ 未实现 | 规格定义：CLIP 相似度检测模型输出与训练图像的视觉相似性 | 无图像重建检测 |
| NoRawImageStage / NoRawAudioStage | ❌ 未实现 | 规格定义：绝对禁止任何图像/音频原文件输出 | 无格式级阻断 |
| ModelWeightSafetyStage | ❌ 未实现 | 规格定义：检查模型文件不含训练数据 | 无模型权重安全检查 |
| ProductPackageOnlyStage | ❌ 未实现 | 规格定义：严格只允许产品包格式，确保包内无数据行 | 无产品包格式验证 |
| RateLimitStage (应用场景) | ❌ 未实现 | 规格定义：max_rps=100 | 无场景级限流 |
| InspectionReport 完整结构 | ⚠️ 部分实现 | 规格定义 15+ 字段：inspector_node_id、output_bytes、dp_mechanism、memorization_score 等 | `InspectionResult` 字段较少 |

#### 新发现的不合理设计

| 设计 | 问题 | 建议 |
|------|------|------|
| **TextMemorizationStage 阈值 0.08** | 8% token 重叠率对大多数有意义的文本输出都会触发。技术文档、代码片段、标准回答等正常输出的重叠率远超此值 | 提高到 0.15-0.20，或按场景配置不同阈值 |
| **MIA 200 成员 + 200 非成员样本** | 每次模型导出都要计算 400 个样本的损失，对大模型来说耗时数分钟 | 减少到 50+50，或用采样策略 |
| **EmbMarker weight_shift 水印** | `model_watermark` 包不存在于任何 Python 仓库中，是虚构的依赖 | 用成熟的水印方案（如 Uchida 2017）或跳过权重水印 |
| **iptables OUTPUT 链强制转发** | 应用层网关不应依赖内核级 iptables 规则；容器化部署中 iptables 行为不可预测 | 用 sidecar proxy (Envoy) 或应用层代理 |
| **CLIP 相似度图像重建检测** | 要求在文本输出上计算与训练图像的 CLIP 相似度，但 CLIP 需要图像输入，纯文本无法计算 | 用文本语义相似度替代，或在有图像生成能力时才启用 |

---

### 6.5 补充的架构级发现

#### 规格间不一致

| 不一致 | 说明 |
|---------|------|
| SS-01 定义 HSM 内密钥操作 vs SS-04 假设 TEE 内自主解密 | SS-01 §1.2 明确说"沙箱内数据解密由 SS-04 在 TEE 内自主完成"，但 SS-01 §4.2 又要求 DEK 在 HSM 内重包装后下发。两处对 DEK 明文出现在哪个进程内存中描述不一致 |
| SS-02 定义 13 种 SandboxMode vs SS-05 定义 8 种规则集 | SS-02 有 `joint_federated`、`api_service`、`unstructured_analysis` 但 SS-05 无对应规则集 |
| SS-04 §3.1.2 的 `md5()` 脱敏 vs 国密合规 | DuckDB 脱敏视图用 `md5()` 哈希，但国密标准要求 SM3。应改为 SM3 或在 DuckDB 中注册 SM3 UDF |
| SS-01 要求 gRPC 内部接口 vs 实际 REST | 规格定义 protobuf 8 个 RPC，但整个项目无 gRPC 依赖 |
| SS-02 的 QuotaManager Lua 脚本 vs SS-04 的配额客户端 | SS-02 §5.2 定义完整的 Redis Lua 原子操作，SS-04 §5.1 的 `QuotaClient` 引用它，但两者均未实现 |

#### 安全设计缺陷（新发现）

| 缺陷 | 严重性 | 位置 | 说明 |
|------|--------|------|------|
| SM2 签名用软件密钥而非 HSM | 高 | SS-01 §4.1 | 规格要求 `self.hsm.sm2_sign()` 在 HSM 内签名，但实际用软件密钥对 |
| DEK 未用 provider 公钥加密存储 | 高 | SS-01 §2.2 | 规格要求 `encrypted_key_b64: DEK 明文 → SM2 加密（provider 公钥）`，实际存明文 hex |
| DuckDB `register()` SQL 注入 | 高 | SS-04 §3.1.2 | `product_id` 直接用于 `CREATE TABLE`，未做标识符转义 |
| `exec(compile(script...))` 用户代码执行 | 高 | SS-04 §3.2 | Python restricted builtins 可通过 `__subclasses__` 等绕过 |
| 密钥分发会话无 TTL 强制过期 | 中 | SS-01 §3.3 | 规格定义 `expires_at` 字段但无定时清理过期会话 |

---

### 6.6 更新后的实现完成度

基于完整 spec 分析，更新各子系统的精确实现率：

```
子系统                    之前评估    更新后    说明
──────────────────────────────────────────────────────
KMS & 身份               45%       30%     gRPC/MPC/CRL降级/密钥审计全缺
合约引擎 & 策略           65%       45%     13种模式/协商/链上存证/QuotaManager全缺
任务调度器                60%       60%     无变化
沙箱运行时 (基础设施)      85%       85%     无变化
沙箱运行时 (场景运行时)    15%       10%     SecureDuckDB/PG-in-TEE/DataLoader全缺
输出审查网关              65%       50%     8种规则集/NER模型/EmbMarker/iptables全缺
审计 & 区块链             55%       55%     无变化
互联互通                  70%       70%     无变化
训练管线                  25%       20%     SecureTextDataLoader缺
数据管线 & 存储           75%       75%     无变化
前端                      70%       70%     无变化
部署                      80%       80%     无变化
──────────────────────────────────────────────────────
整体                      55%       48%     核心规格要求比之前评估更严格
```

---

## 七、附录：Spec 文件清单与可读性

| 文件 | 大小 | 可读性 |
|------|------|--------|
| product-spec.md | 可读 | ✅ |
| tech-spec.md | 可读 | ✅ |
| detailed-tech-spec.md | 可读 | ✅ |
| SS-03.md | 可读 | ✅ |
| SS-04.md | 可读 | ✅ |
| SS-05.md | 可读 | ✅ |
| SS-06.md | 可读 | ✅ |
| ss-01-kms-identity.md | 可读 | ✅ |
| ss-02-contract-policy.md | 可读 | ✅ |
| ss-04-sandbox-runtime.md | 可读 | ✅ |
| ss-05-output-gateway.md | 可读 | ✅ |
| 其他 23 份 | 可读 | ✅ |

---

## 八、二轮复核：当前实现状态快照

> 日期：2026-06-05（二轮）
> 方法：逐文件代码级验证，对比一轮分析中的标记

### 8.1 状态变化汇总（一轮 → 二轮）

#### 改善项（NOT_IMPLEMENTED/STUB → WORKING/PARTIAL）

| # | 功能 | 一轮状态 | 二轮状态 | 证据 |
|---|------|---------|---------|------|
| 1 | KMS HSM 适配器集成 | 仅接口 | ✅ WORKING | `kms_service.py` 导入 `hsm_adapter` 单例，用于 `generate_kek`/`wrap_dek`/`unwrap_dek` |
| 2 | DEK 信封加密 (KEK 包装) | 明文存储 | ✅ WORKING | DEK 用 KEK 包装后存储，明文不持久化 |
| 3 | 会话状态机 | 缺失 | ✅ WORKING | `session_state_machine.py` 定义 10 状态 (6 活跃 + 4 终态)，转换表完整 |
| 4 | DP 预算按请求扣减 | 仅分配不扣减 | ✅ WORKING | `policy_evaluator.py:_check_dp_budget()` 查询余额 + `dp_budget_ledger.consume()` 扣减 |
| 5 | 合约协商 (NEGOTIATING) | 未实现 | ✅ WORKING | `contract_service.py` 转换表有 NEGOTIATING，单方签署时设置 |
| 6 | 合约模型字段补全 | 缺失 | ✅ WORKING | `contract.py` 含 `max_output_rows`/`allowed_output_formats`/`inspection_rule_set` |
| 7 | PolicyBundle 模型字段 | 缺失 | ✅ WORKING | `policy_bundle.py` 含 `field_acl`/`allowed_ops`/`sandbox_modes` |
| 8 | ClickHouse bloom 索引 | 未实现 | ✅ WORKING | `audit_events` 5 个 bloom_filter 索引 + `sandbox_activity_events` 3 个 |
| 9 | 联邦信任状态持久化 | 仅内存 | ✅ WORKING | `federation_connector.py` 通过 `FederationTrustRecord` 模型持久化到 DB |
| 10 | 审计签名失败处理 | 静默吞掉 | ✅ WORKING | 记录为 `[CRITICAL]` + `detail["_sm2_sign_error"]`，审计条目仍写入 |
| 11 | DuckDB 脱敏视图 | 未实现 | ⚠️ PARTIAL | `secure_duckdb.py` 有 `MaskRule` (HASH/REDACT/GENERALIZE+top_k) + `create_masked_view()` |
| 12 | 节点选择器资源过滤 | 部分实现 | ⚠️ PARTIAL | 按 CPU/内存/GPU 负载评分 + 错误率 + 任务容量 + 区域亲和性过滤 |
| 13 | LSB 水印 | 未实现 | ✅ WORKING | `watermark.py` 有完整 PIL LSB 图像隐写 + 零宽文本隐写 |
| 14 | QuotaManager Lua 脚本 | 未实现 | ✅ WORKING | `quota_manager.py` 有 `LUA_INCREMENT_AND_CHECK` + `redis.eval()` 原子操作 |
| 15 | ClickHouse 审计模式 | 部分实现 | ✅ WORKING | `clickhouse_schema.py` 60+ 列，超集规格 |
| 16 | 审计签名失败处理 | 静默吞掉 | ✅ WORKING | 失败记录为 CRITICAL + 写入 detail 字段 |

#### 仍然缺失项（维持 STUB/NOT_IMPLEMENTED）

| # | 功能 | 状态 | 说明 |
|---|------|------|------|
| 1 | 证书真实 X.509 DER/ASN.1 | STUB | base64 编码文本，非真实证书格式 |
| 2 | 证书吊销级联 DEK 暂停 | NOT_IMPLEMENTED | 仅更新证书状态 |
| 3 | SM2 HSM 内签名 | SOFTWARE_ONLY | 使用 gmssl 软件库，无 HSM 调用 |
| 4 | kms.py / key_metadata.py 模型重复 | DUPLICATED | 不同 schema，无关联 |
| 5 | TLCP SM2 密码套件 | STUB | 静默回退到 RSA |
| 6 | 多模式 Rego 模板 | STUB | 仅 1 个通用模板，无 structured_query/ai_training/product_dev |
| 7 | 合约履约 Redis 配额 | STUB | 直接 DB 读取 allowed_operations |
| 8 | 合约区块链锚定 | NOT_IMPLEMENTED | blockchain_tx_hash 字段不写入 |
| 9 | 会话配额初始化 | NOT_IMPLEMENTED | 按需创建，不从合约预填充 |
| 10 | 场景路由 (SceneRouter) | NOT_IMPLEMENTED | 按沙箱级别路由，非按场景 |
| 11 | DuckDB 策略引擎集成 | NOT_IMPLEMENTED | 无策略服务导入 |
| 12 | 任务 CODE_SCANNING 阶段 | STUB | task_worker 跳过直接到 RUNNING |
| 13 | Scheduler/Queue/Worker 集成 | STUB | 三模块断开，各自独立 Redis 键 |
| 14 | 节点选择热区随机抖动 | NOT_IMPLEMENTED | 确定性选择，相同评分节点会雪崩 |
| 15 | GPU-TEE 运行时 | STUB | 全部为 GPUTeeRuntimeStub |
| 16 | PII NER ML 模型 | STUB | 纯规则正则，无 bert-base-chinese-pii-ner |
| 17 | 流式审查 mitmproxy | STUB | 纯 Python，无 mitmproxy |
| 18 | 区块链真实 FISCO BCOS | STUB | SDK 注释掉，内存字典 |
| 19 | 训练管线真实训练 | STUB | 数据预处理 only，无 PyTorch |
| 20 | CDC Kafka 生产者 | STUB | _kafka_producer 始终 None |
| 21 | SM4-GCM 真实实现 | STUB | SM4-CBC + SHA-256 派生 tag |
| 22 | L1 TEE (SGX/Occlum) | STUB | TEEAdapter.provision() 返回 FAILED |
| 23 | 9 种场景运行时 | NOT_IMPLEMENTED | 仅基础设施适配器 (L0/L1/L2/L3/k8s) |
| 24 | MPC 密钥分片 | NOT_IMPLEMENTED | 无代码 |
| 25 | PostgreSQL-in-TEE | NOT_IMPLEMENTED | 无代码 |

### 8.2 更新后的子系统实现率

```
子系统                    一轮评估    二轮复核    变化
──────────────────────────────────────────────────────────
KMS & 身份               30%       45%       ↑ HSM集成/DEK加密已连通
合约引擎 & 策略           45%       55%       ↑ DP预算扣减/协商/模型字段
任务调度器                60%       60%       → 无变化
沙箱运行时 (基础设施)      85%       85%       → 无变化
沙箱运行时 (场景运行时)    10%       15%       ↑ DuckDB脱敏视图/会话状态机
输出审查网关              50%       55%       ↑ LSB水印/bloom索引/审计签名
审计 & 区块链             55%       60%       ↑ bloom索引/信任持久化/签名处理
互联互通                  70%       75%       ↑ 信任状态持久化
训练管线                  20%       20%       → 无变化
数据管线 & 存储           75%       75%       → 无变化
前端                      70%       70%       → 无变化
部署                      80%       80%       → 无变化
──────────────────────────────────────────────────────────
整体                      48%       55%       ↑ 7个百分点
```

### 8.3 剩余缺口分类统计

| 类别 | 数量 | 说明 |
|------|------|------|
| **STUB（有文件/接口但无真实逻辑）** | 15 项 | 证书格式、TLCP、Rego模板、GPU-TEE、NER ML、区块链、训练、CDC、SM4-GCM 等 |
| **NOT_IMPLEMENTED（完全无代码）** | 10 项 | 场景路由、MPC、PG-in-TEE、会话配额初始化、热区抖动等 |
| **DUPLICATED（架构问题）** | 1 项 | kms.py / key_metadata.py 模型重复 |
| **SOFTWARE_ONLY（应为硬件）** | 1 项 | SM2 签名用软件而非 HSM |
| **合计剩余缺口** | **27 项** | |

### 8.4 按优先级排列的剩余工作

#### P0 安全关键（5 项）

| # | 缺口 | 影响 |
|---|------|------|
| 1 | 证书真实 X.509 + 吊销级联 | PKI 信任链不完整 |
| 2 | SM2 HSM 内签名 | 密钥泄露风险 |
| 3 | SM4-GCM 真实实现 | 完整性校验为伪 GCM tag |
| 4 | 区块链持久化 (至少 PG append-only) | 存证数据重启丢失 |
| 5 | 任务 CODE_SCANNING 集成 | 用户代码未经扫描直接执行 |

#### P1 功能完整（10 项）

| # | 缺口 | 影响 |
|---|------|------|
| 1 | 多模式 Rego 模板 | 策略无场景差异化 |
| 2 | Scheduler/Queue/Worker 集成 | 任务管线断开 |
| 3 | DuckDB 策略引擎集成 | 结构化数据无运行时策略执行 |
| 4 | 会话配额初始化 | 配额不从合约预填充 |
| 5 | 合约履约 Redis 配额 | 配额检查为简单字符串匹配 |
| 6 | 节点热区随机抖动 | 相同评分节点雪崩 |
| 7 | 合约区块链锚定 | 合约无链上存证 |
| 8 | PII NER ML 模型 | PII 检测仅正则，误报/漏报率高 |
| 9 | 流式审查 mitmproxy | 应用场景无真实代理 |
| 10 | 模型重复 (kms.py / key_metadata.py) | 数据一致性风险 |

#### P2 高级场景（12 项）

| # | 缺口 | 影响 |
|---|------|------|
| 1 | L1 TEE (SGX/Occlum) | 最高安全级别不可用 |
| 2 | GPU-TEE 运行时 | 训练场景不可用 |
| 3 | MPC 密钥分片 | L2 补偿机制缺失 |
| 4 | PostgreSQL-in-TEE | 应用场景不可用 |
| 5 | 9 种场景运行时 | 仅基础设施，无场景特化 |
| 6 | 训练管线真实训练 | 仅为数据预处理 |
| 7 | CDC Kafka 生产者 | 增量同步不可用 |
| 8 | TLCP SM2 密码套件 | 国密 TLS 不可用 |
| 9 | 文本记忆检测 (Carlini) | 仅为 n-gram 重叠 |
| 10 | 模型权重水印 (EmbMarker) | 无实现 |
| 11 | iptables 网络层强制 | 无内核级流量控制 |
| 12 | 多模态/视觉场景运行时 | 训练场景不可用 |

---

## 九、三轮复核：当前实现状态快照

> 日期：2026-06-05（三轮）
> 方法：4 个并行 Agent 逐文件代码级验证，对比二轮分析标记

### 9.1 状态变化汇总（二轮 → 三轮）

#### 改善项（STUB/NOT_IMPLEMENTED → WORKING/PARTIAL）

| # | 功能 | 二轮状态 | 三轮状态 | 证据 |
|---|------|---------|---------|------|
| 1 | 证书真实 X.509 DER/ASN.1 | STUB | ✅ WORKING | `certificate_service.py` 完整 ASN.1 TLV 编码器 (26-108行) + TBSCertificate 构造 (776-866行) + DER 解析器 (558-674行) |
| 2 | 证书吊销级联 DEK 暂停 | NOT_IMPLEMENTED | ✅ WORKING | `_cascade_revoke()` 查询关联 DEK → REVOKED → 更新 KeyMetadata → 终止活跃会话 |
| 3 | SM2 HSM 内签名 | SOFTWARE_ONLY | ✅ WORKING | `crypto_service.sign_with_hsm()` HSM-first 模式；`audit_service.py` 79-94行 HSM 优先回退软件 |
| 4 | SM4-GCM 真实 AEAD | STUB | ✅ WORKING | `column_encryption.py` 使用 `cryptography.hazmat.primitives.ciphers.aead.AESGCM` 真实 AEAD |
| 5 | HSM 适配器连接 KMS | NOT_IMPLEMENTED | ✅ WORKING | `kms_service.py:15` 导入 `hsm_adapter`，用于 `generate_kek`/`wrap_dek`/`unwrap_dek` |
| 6 | DEK 信封加密 (KEK 包装) | STUB | ✅ WORKING | `generate_data_key()` 用 KEK 包装 DEK 后存储，明文不持久化 |
| 7 | key_distributions 模型 | NOT_IMPLEMENTED | ✅ WORKING | `kms.py:84-113` 定义完整 `KeyDistribution` 模型（tee_quote_hash、expires_at 等） |
| 8 | 多模式 Rego 策略模板 | STUB | ✅ WORKING | `policy_compiler.py` 5 种模板：generic + query + training + product_dev + app |
| 9 | 合约区块链锚定 | NOT_IMPLEMENTED | ⚠️ PARTIAL | `contract_service.py:134-146` 签署后触发 `blockchain_service.anchor_to_blockchain()`，但 FISCO BCOS SDK 仍为桩 |
| 10 | 合约 Redis 原子配额 | STUB | ✅ WORKING | `quota_manager.py:44-58` 真实 Lua 脚本原子 GET→检查→INCRBY |
| 11 | 节点热区随机抖动 | NOT_IMPLEMENTED | ✅ WORKING | `node_selector.py:148-163` top-3 加权随机选择 + 随机抖动 |
| 12 | Scheduler/Queue/Worker | STUB | ⚠️ PARTIAL | `task_pipeline.py` 统一编排器，替代断开的三模块；CODE_SCANNING→PREPARING→RUNNING→OUTPUT_INSPECTING |
| 13 | DuckDB 策略引擎集成 | NOT_IMPLEMENTED | ✅ WORKING | `secure_duckdb.py:123-131` `set_policy()` + `_check_policy()` 调用 `policy_evaluator.evaluate()` |
| 14 | DuckDB SM3 UDF | NOT_IMPLEMENTED | ✅ WORKING | `secure_duckdb.py:107-119` 注册 SM3 UDF，HASH 脱敏视图使用 sm3() 非 md5() |
| 15 | SQL 标识符转义 | NOT_IMPLEMENTED | ✅ WORKING | `secure_duckdb.py:405` `_sanitize_identifier()` + 列名双引号包裹 |
| 16 | MPC 密钥分片 | NOT_IMPLEMENTED | ✅ WORKING | `mpc_service.py` 真实 Shamir (2,2) 秘密分享：256-bit 素数域、Lagrange 插值、多项式求值 |
| 17 | PG append-only 区块链 | STUB | ✅ WORKING | `blockchain_adapter.py:180` PGAppendOnlyAdapter：SM3 哈希链 + DB 持久化 + 链完整性验证 |
| 18 | 前端 Dockerfile | NOT_IMPLEMENTED | ✅ WORKING | `cds-frontend/Dockerfile` 多阶段构建 node:18-alpine → nginx:alpine |
| 19 | Vault Compose 集成 | NOT_IMPLEMENTED | ✅ WORKING | `docker-compose.middleware.yml:79-114` vault:1.17 + vault-init 初始化 |

#### 仍然缺失项（维持 STUB/NOT_IMPLEMENTED）

| # | 功能 | 状态 | 说明 |
|---|------|------|------|
| 1 | 密钥分发会话 TTL 强制过期 | STUB | `KeyDistribution.expires_at` 字段存在但无服务创建记录或执行清理 |
| 2 | TLCP SM2 密码套件 | PARTIAL | 尝试 SM2 密码套件后始终回退到 RSA；TLCP cert PEM 仍为占位符文本 |
| 3 | kms.py / key_metadata.py 模型重复 | DUPLICATED | 两个模型字段高度重叠，cascade 代码同时更新两者 |
| 4 | CODE_SCANNING 阶段处理函数 | STUB | `task_pipeline.py` 调用 CODE_SCANNING 但无 handler 注册，静默跳过 |
| 5 | 会话配额从合约预填充 | PARTIAL | `reset()` 仅清除 Redis 键，不从合约 max_output_rows 等字段初始化 |
| 6 | 场景路由 (SceneRuntimeFactory) | NOT_IMPLEMENTED | 仅按 L0/L1/L2/L3 安全级别路由，无 SandboxMode 场景路由 |
| 7 | 9 种场景运行时 (6/9 缺失) | PARTIAL | 仅 llm_sft、vision、multimodal 存在；缺 query/modeling/dev/semi_etl/federated |
| 8 | L1 TEE (SGX/Occlum) | STUB | `TEEAdapter.provision()` 返回 FAILED |
| 9 | GPU-TEE 运行时 | STUB | 仅 `GPUTeeRuntimeStub`，所有操作模拟 |
| 10 | PostgreSQL-in-TEE | NOT_IMPLEMENTED | 使用 DuckDB，无 pg_tde/pgcrypto |
| 11 | PII NER ML 模型 | STUB | 纯正则+规则 NER，无 bert-base-chinese-pii-ner |
| 12 | 流式审查 mitmproxy | PARTIAL | 纯 Python 块扫描，无 mitmproxy 代理拦截 |
| 13 | 文本记忆检测 (Carlini) | PARTIAL | n-gram 重叠阈值 0.3，无 loss-based Carlini 方法 |
| 14 | 模型权重水印 | STUB | SM3 哈希记录日志，不修改模型权重 |
| 15 | LSB 数值水印 | PARTIAL | 图像 LSB + 零宽文本可用；无 int/float/DataFrame 支持 |
| 16 | FISCO BCOS 真实客户端 | STUB | SDK 注释掉，FISCOBCOSAdapter 内存字典；PG append-only 为真实替代 |
| 17 | 训练管线 PyTorch | STUB | `_simulate_training_step()` 返回合成 loss，无 torch 导入 |
| 18 | CDC Kafka 生产者 | STUB | `_kafka_producer = None`，无真实 Kafka 连接 |
| 19 | 身份/密钥管理前端 | NOT_IMPLEMENTED | 无 /identity、/identity/keys 页面和路由 |
| 20 | 证书管理前端 | NOT_IMPLEMENTED | 无 /certificates 页面 |
| 21 | 连接器管理前端 | NOT_IMPLEMENTED | 无 /connectors 页面 |
| 22 | FISCO BCOS Compose | NOT_IMPLEMENTED | 无区块链节点服务定义 |

### 9.2 三轮 vs 二轮对比

```
类别              二轮    三轮    变化
────────────────────────────────────────
P0 安全关键       5项     1项     ↓ 4项已修复
P1 功能完整       10项    5项     ↓ 5项已修复
P2 高级场景       12项    10项    ↓ 2项已修复 (MPC/PG-append-only)
前端缺失          5项     3项     ↓ 2项已修复 (Dockerfile/内联告警+验证)
部署缺失          3项     1项     ↓ 2项已修复 (Dockerfile/Vault)
────────────────────────────────────────
总缺口            27项    20项    ↓ 7项已修复
```

### 9.3 更新后的子系统实现率

```
子系统                    二轮    三轮    变化
──────────────────────────────────────────────────
KMS & 身份               45%     75%     ↑ 证书X.509/吊销级联/HSM签名/DEK加密/HSM连接
合约引擎 & 策略           55%     75%     ↑ 5种Rego模板/Redis原子配额/锚定集成
任务调度器                60%     70%     ↑ task_pipeline统一编排
沙箱运行时 (基础设施)      85%     90%     ↑ MPC Shamir真实实现
沙箱运行时 (场景运行时)    15%     25%     ↑ 3/9场景运行时存在
输出审查网关              55%     55%     → 无变化
审计 & 区块链             60%     75%     ↑ PG append-only哈希链真实实现
互联互通                  75%     75%     → 无变化
训练管线                  20%     20%     → 无变化
数据管线 & 存储           75%     75%     → 无变化
前端                      70%     75%     ↑ Dockerfile/告警内联/验证内联
部署                      80%     90%     ↑ Dockerfile/Vault Compose
──────────────────────────────────────────────────
整体                      55%     70%     ↑ 15个百分点
```

### 9.4 剩余缺口分类统计

| 类别 | 数量 | 说明 |
|------|------|------|
| **STUB（有文件/接口但无真实逻辑）** | 10 项 | CODE_SCANNING handler、GPU-TEE、L1 TEE、PII NER、FISCO BCOS、训练 PyTorch、CDC、模型水印、密钥分发 TTL、TLCP |
| **NOT_IMPLEMENTED（完全无代码）** | 8 项 | 场景路由、PG-in-TEE、3 个前端页面、FISCO Compose、会话配额预填充 |
| **PARTIAL（有基础实现但不完整）** | 3 项 | 6/9 场景运行时、流式审查无 mitmproxy、文本记忆无 Carlini |
| **DUPLICATED（架构问题）** | 1 项 | kms.py / key_metadata.py 模型重复 |
| **合计剩余缺口** | **22 项** | |

### 9.5 按优先级排列的剩余工作

#### P0 安全关键（1 项）

| # | 缺口 | 状态 | 影响 |
|---|------|------|------|
| 1 | 任务 CODE_SCANNING handler | STUB | pipeline 调用但无处理函数，用户代码未经扫描 |

#### P1 功能完整（5 项）

| # | 缺口 | 状态 | 影响 |
|---|------|------|------|
| 1 | 会话配额从合约预填充 | PARTIAL | 配额不从合约 max_output_rows 等初始化 |
| 2 | 密钥分发 TTL 强制过期 | STUB | expires_at 字段存在但无清理 |
| 3 | 模型重复 (kms.py / key_metadata.py) | DUPLICATED | 数据一致性风险 |
| 4 | TLCP SM2 密码套件 | PARTIAL | 始终回退到 RSA |
| 5 | 6/9 场景运行时缺失 | PARTIAL | query/modeling/dev/semi_etl/federated 缺失 |

#### P2 高级场景（10 项）

| # | 缺口 | 状态 | 影响 |
|---|------|------|------|
| 1 | L1 TEE (SGX/Occlum) | STUB | 最高安全级别不可用 |
| 2 | GPU-TEE 运行时 | STUB | 训练场景不可用 |
| 3 | PostgreSQL-in-TEE | NOT_IMPLEMENTED | 应用场景不可用 |
| 4 | PII NER ML 模型 | STUB | PII 检测仅正则 |
| 5 | 流式审查 mitmproxy | PARTIAL | 应用场景无真实代理 |
| 6 | 文本记忆检测 (Carlini) | PARTIAL | 仅为 n-gram 重叠 |
| 7 | 模型权重水印 | STUB | 无实现 |
| 8 | FISCO BCOS 真实客户端 | STUB | PG append-only 为替代方案 |
| 9 | 训练管线 PyTorch | STUB | 仅为数据预处理 |
| 10 | CDC Kafka 生产者 | STUB | 增量同步不可用 |

#### 前端 & 部署（4 项）

| # | 缺口 | 状态 | 影响 |
|---|------|------|------|
| 1 | 身份/密钥管理页面 | NOT_IMPLEMENTED | /identity 路由缺失 |
| 2 | 证书管理页面 | NOT_IMPLEMENTED | /certificates 路由缺失 |
| 3 | 连接器管理页面 | NOT_IMPLEMENTED | /connectors 路由缺失 |
| 4 | FISCO BCOS Compose | NOT_IMPLEMENTED | 区块链节点未容器化 |

### 9.6 实现进度趋势

```
轮次    日期        实现率    新增完成项    总缺口
──────────────────────────────────────────────────
一轮    2026-06-05   48%      —           34项
二轮    2026-06-05   55%      +7项        27项
三轮    2026-06-05   70%      +7项        20项
──────────────────────────────────────────────────
目标    —            95%      —           ≤2项
```

**趋势分析**：
- P0 安全关键从 5 项降至 1 项（CODE_SCANNING handler），安全基线已基本建立
- P1 功能从 10 项降至 5 项，核心管线（KMS/策略/配额/审计）已连通
- P2 高级场景从 12 项降至 10 项（MPC 和 PG append-only 已完成），高级场景仍为主要缺口
- 剩余 20 项中，10 项为 STUB（需补充真实逻辑），8 项为 NOT_IMPLEMENTED（需新建），3 项为 PARTIAL（需扩展），1 项为架构问题
