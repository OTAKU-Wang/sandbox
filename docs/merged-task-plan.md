# CDS 密态沙箱 — 合并任务清单

> 生成日期：2026-06-05
> 基于：implementation-tasks.md + 当前实现审计
> 整体实现率：~55% → 目标 90%

---

## 实现状态总览

| 分类 | 总数 | 已完成 | 部分完成 | 未完成 |
|------|------|--------|----------|--------|
| P0 安全关键 | 5 | 5 | 0 | 0 |
| P1 功能完整 | 10 | 8 | 0 | 2 |
| P2 高级场景 | 12 | 5 | 0 | 7 |
| 规格更新 | 15 | 15 | 0 | 0 |
| 前端补全 | 5 | 5 | 0 | 0 |
| 部署补全 | 3 | 0 | 0 | 3 |
| **总计** | **50** | **36** | **0** | **14** |

---

## 一、P0 安全关键任务（剩余 3 项）

### P0-1: 证书真实 X.509 签发 + 吊销级联 ✅ 已完成

**状态**：吊销级联 ✅ 已完成（Andrew #128），X.509 DER 编码 ✅ 已完成（Andrew 2026-06-05）
**负责人**：@Andrew
**剩余工作**：
1. 引入 `cryptography>=42.0.0` 依赖
2. 重写 `_build_cert_pem()` 使用 `cryptography.x509` 构造真实 X.509v3 DER 证书
3. 重写 `verify_certificate()` 解析 X.509 结构 + 验证签名链
4. 密钥分发记录表 `key_distributions`（新建模型）
5. 密钥分发会话 TTL 清理
**预计工时**：5 天

### P0-2: SM2 HSM 内签名

**状态**：❌ 未完成 — HSM adapter 仅用于 KEK wrap/unwrap，签名仍用 gmssl 软件
**负责人**：@Andrew
**剩余工作**：
1. `crypto_service.py` 添加 `sign_with_hsm(data, key_id)` 方法
2. 审计签名、合约签名切换为 HSM 优先
3. HSM 不可用时降级策略 + 告警
**预计工时**：3 天

### P0-3: SM4-GCM 真实实现 ✅ 已完成

**状态**：✅ Andrew 2026-06-05 — AES-128-GCM 真实 AEAD（gmssl 不支持 GCM，用 cryptography 库 AES 替代）
**改动**：column_encryption.py + deterministic_sm4.py encrypt_gcm/decrypt_gcm 替换为 AES-GCM

---

## 二、P1 功能完整任务（剩余 8 项）

### P1-1: 多模式 Rego 策略模板 ✅ 已完成

**状态**：✅ Cindy 2026-06-05 — 4 种场景模板 + compile_for_mode()
**改动**：policy_compiler.py 新增 _generate_query_rego / _generate_training_rego / _generate_product_dev_rego / _generate_app_rego

### P1-2: 任务 Scheduler/Queue/Worker 统一 ✅ 已完成

**状态**：✅ Andrew 2026-06-05 — TaskPipeline 统一 queue + scheduler + state machine
**改动**：main.py lifespan + sandbox_tasks.py submit 路由 + task_pipeline.py 默认 handler

### P1-3: DuckDB 策略引擎集成 ✅ 已完成

**状态**：✅ Cindy 2026-06-05 — set_policy() + _check_policy() + 字段级 ACL
**改动**：secure_duckdb.py 新增 set_policy/_check_policy/_extract_select_fields，execute_query() 前调用 policy_evaluator.evaluate()

### P1-4: 会话配额初始化 ✅ 已完成

**状态**：✅ Cindy 2026-06-05 — 3 个会话创建路径全部接入 quota_manager.reset()
**改动**：sandbox_sessions.py + connectors.py + contract_fulfillment.py

### P1-6: 节点选择随机抖动 ✅ 已完成

**状态**：✅ Cindy 2026-06-05 — top-3 加权随机选择
**改动**：node_selector.py select() 方法

### P1-7: 合约区块链锚定 ✅ 已完成

**状态**：✅ Cindy 2026-06-05 — 签署后 SM3 哈希 → blockchain_service.anchor_to_blockchain()
**改动**：contract_service.py sign() 方法

### P1-8: PII NER ML 模型集成

**状态**：❌ 未完成 — 纯正则，无 ML
**负责人**：@Andrew
**剩余工作**：
1. 集成 bert-base-chinese-pii-ner
2. 双层检测逻辑（正则 + NER）
3. 添加依赖
**预计工时**：3 天

### P1-9: 流式审查 mitmproxy 集成

**状态**：❌ 未完成 — 纯 Python 扫描器
**负责人**：@Andrew
**剩余工作**：
1. 应用输出代理（mitmproxy Python API）
2. 集成到流式审查管线
3. 会话级熔断器
**预计工时**：4 天

---

## 三、已确认完成（无需重复工作）

| 任务 | 状态 | 证据 |
|------|------|------|
| P0-4: 区块链审计持久化 | ✅ | PG append-only + Merkle + Redis Stream 批量 |
| P0-5: 任务 CODE_SCANNING | ✅ | TaskPipeline 全集成，enum + transitions + handler |
| P1-5: QuotaManager Redis Lua | ✅ | Lua 脚本 + eval() 原子操作 |
| P1-10: KMS/KeyMetadata 去重 | ✅ | Andrew 已合并（kms.py → key_metadata.py） |
| P1-12: DuckDB md5→SM3 | ✅ | Cindy 完成，sha256 fallback，测试通过 |
| #128 安全审计修复 | ✅ | 5 项修复全部完成 |
| I3 mTLS + I5 速率限制 | ✅ | 114/114 测试通过 |
| Sprint 8 P1-1~P1-6 | ✅ | 1933 passed / 0 failed |

---

## 四、规格更新任务（剩余 12 项）

| # | 任务 | 状态 |
|---|------|------|
| SPEC-1 | SandboxMode 13→6 | ✅ Cindy 2026-06-05 |
| SPEC-2 | 场景运行时分期标注 | ✅ Cindy 2026-06-05 |
| SPEC-3 | 移除 eBPF | ✅ Cindy 2026-06-05 |
| SPEC-4 | MIA 方案替换 | ✅ Cindy 2026-06-05 |
| SPEC-5 | Rego 模板精简 | ✅ Cindy 2026-06-05 |
| SPEC-6 | gmssl GCM 限制标注 | ✅ Cindy 2026-06-05 |
| SPEC-7 | 列命名统一 | ✅ Cindy 2026-06-05 |
| SPEC-8 | 移除 gRPC 要求 | ✅ Cindy 2026-06-05 |
| SPEC-9 | 简化 CRL 机制 | ✅ Cindy 2026-06-05 |
| SPEC-10 | PDP 运行环境修正 | ✅ Cindy 2026-06-05 |
| SPEC-11 | 记忆性阈值调整 | ✅ 已调整为 0.3 |
| SPEC-12 | iptables→sidecar | ✅ 已修复（3 chain） |
| SPEC-13 | DEK 明文位置一致性 | ✅ Cindy 2026-06-05 |
| SPEC-14 | SandboxMode 规则集补全 | ✅ Cindy 2026-06-05 |
| SPEC-15 | DuckDB md5→SM3 | ✅ 完成 |

---

## 五、前端 + 部署任务

### 前端（5 项）✅ 全部完成
- FE-1: 身份/密钥管理页面 ✅ Andrew — `/admin/identities`
- FE-2: 证书管理页面 ✅ Andrew — `/admin/certificates`
- FE-3: 连接器管理 UI ✅ Andrew — `/admin/connectors`
- FE-4: 告警管理页面 ✅ Andrew — `/admin/alerts`
- FE-5: 区块链验证页面 ✅ Andrew — `/admin/blockchain`

### 部署（3 项）
- DEP-1: 前端 Dockerfile
- DEP-2: Vault Compose 集成
- DEP-3: FISCO BCOS 节点

---

## 六、执行计划（自主迭代）

### Sprint 6 [done] — P0 安全收尾 + P1 功能
- P0 5/5 ✅ | P1 8/10 | SPEC 15/15 ✅
- xfail 收敛: 7→4（P0-8 SM2 加密, P0-10 SUSPENDED 级联, P0-5 审计日志）

### Sprint 7 [done] — xfail 收敛 + P1 收尾
- @Andrew: P0-10 suspend_sessions_by_cert ✅, P0-5 CODE_SCANNING 审计 ✅
- @测试工程师: P0-8 SM2 加密测试修复 ✅
- 回归: 1977 passed / 0 failed / 4 xfailed

### Sprint 9 [done] — P2 高级场景 + 前端 + xfail 归零
- @Andrew: P2-5 场景运行时工厂 ✅ | P2-9 Carlini 记忆检测 ✅ | P2-10 模型水印 ✅
- @Andrew: FE-1~FE-5 前端管理页面 ✅（Jinja2 暗色主题 + 5 页面）
- P2-7 CDC Kafka ❌ 阻塞（aiokafka 未安装）
- @测试工程师: xfail 收敛 ✅ 4→0（Federation/BlockchainAdapter 持久化 + P0-5 审计日志）
- 回归: 2034 passed / 0 failed / 0 xfailed
- 任务编号: #140~#145

---

## 七、阻塞项

| 阻塞项 | 影响 | 需要 |
|--------|------|------|
| SGX 硬件 | P2-1 L1 TEE | 远期，不阻塞当前迭代 |
| H100/A100 GPU | P2-2, P2-6, P2-12 | 远期，不阻塞当前迭代 |
| Vault 环境 | DEP-2 | 需部署 Vault 实例 |
| FISCO BCOS 节点 | DEP-3 | 需部署区块链节点 |

**已解除阻塞**（2026-06-05 安装完成）：
- ✅ `transformers 5.10.2` + `torch 2.12.0+cpu` → P1-8 PII NER 解锁
- ✅ `mitmproxy 12.2.3` → P1-9 流式审查解锁
- ✅ `aiokafka 0.14.0` → P2-7 CDC Kafka 解锁
