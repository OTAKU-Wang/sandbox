# AI 数据沙箱系统 · Gap 修复落地方案

> 日期：2026-09-06
> 基线：[ai-sandbox-gap-review-20260906.md](./ai-sandbox-gap-review-20260906.md)（实现差距审查与修复计划）
> 性质：可执行落地方案。文中所有"代码现状"均已于 2026-09-06 对 `app/` 源码逐条复核，含行号锚点；与基线文档结论有出入处已显式标注【修正】。
> 范围：P0 安全闭环 5 项（详细到函数级改动）、P1 设计对齐 7 项、P2 能力补齐 6 项（路线图级）、横切面（配置矩阵 / Alembic 迁移 / 测试 / 发布回滚 / 排期）。

---

## 一、基线复核结论（方案可信度前提）

| 基线问题编号 | 复核结果 | 备注 |
|---|---|---|
| A1 合约终止无级联 | **属实** | `app/services/contract_service.py:173-183` `terminate()` 仅改状态 + flush，无任何回收动作 |
| A2 过期会话无调度清理 | **属实** | `app/main.py:20-103` lifespan 仅启动 TaskPipeline / KMS TTL 循环 / CDC agent；`app/api/sandbox_sessions.py:1240-1309` 的 `cleanup_expired_sessions` 是 admin-only HTTP 端点，只能手动触发 |
| A3 dev 沙箱绕过合约 | **属实，但范围收窄**【修正】 | `app/api/dev_sandbox.py:73-74` 有产品属主校验（仅 provider 可用）；execute/output 端点已有 `inspect_text_output` 且异常时 fail-closed（`:179-204`、`:260-287`）。真实缺口是：**无合约关联、`dp_epsilon_budget` 由请求自报（`:34`）、无生产禁用开关**。买方不能经此路径绕过合约，但 DP 预算与用途约束在该路径失效 |
| A4 合约无用途限定字段 | **属实** | `app/models/contract.py` 全文无 purpose 字段，仅有泛型 `terms: JSON` |
| B1 TEE 证明验证可选 | **属实** | `app/services/kms_service.py:189-194` `distribute_key` 中 `if attestation or tee_quote:` —— 不提供证明则直接返回密钥 hex |
| B3 wrapped keys 仅内存 | **属实** | `app/services/kms_service.py:40-42` 三个内存 dict；`KeyMetadata` 模型存在（`app/models/key_metadata.py:26`）但未用于 wrapped 持久化 |
| E1 输出网关非强制 | **属实，但形态不同**【修正】 | 任务管线**已有** `_output_inspecting_handler`（`app/services/task_pipeline.py:491-564`）：PII/DP/水印检查 + 结论落库 + 异常 fail-closed，四个阶段 handler 均已注册（`:615-618`）。真实缺口是：① 检查走裸 `inspect_text_output`，**未构造合约 `OutputPolicy`**（行数截断/格式白名单不强制）；② `app/api/output_control.py:282-329` `/gateway` 端点的限制参数来自**请求体自报**，不从合约加载；③ `app/api/sandbox_tasks.py:235-270` `get_task_result` 只看 status，不校验落库的检查结论，也不返回脱敏输出；④ pipeline 启动失败回退到 legacy `task_worker` 时以上保障全部缺失 |
| F1 链上存证本地模拟 | **属实** | `app/services/blockchain_adapter.py:22-25` `ChainBackend` 枚举含 FISCO_BCOS/ANT_CHAIN/PG_APPEND_ONLY；仓库根有 `docker-compose.blockchain.yml`（链环境编排已备）但适配器未接真实链 |

**复核总结**：基线文档的 4 个高危闭环缺口全部成立，方向正确；A3/E1 两项按修正后的精确范围实施，避免过度改造。

---

## 二、P0 安全闭环（5 项 · 1-2 周）

### T1 · 会话生命周期调度器（修 A2/D1）

**目标**：过期会话自动走完整回收链（状态机 → 容器终止 → 密钥销毁 → 网络策略移除 → 配额释放），不依赖管理员手动调端点。

**代码现状**：
- 回收逻辑已完整存在于 `app/api/sandbox_sessions.py:1246-1307`（含 `session_state_machine.validate_transition`、`runtime.terminate`、`kms_service.destroy_key`、`network_policy_engine.remove_policy`、`release_tenant_usage`）
- 后台循环模式已有先例：`app/main.py:62-66` 的 KMS `_ttl_cleanup_loop`（`asyncio.create_task` + shutdown `cancel()`）
- 判定函数 `is_session_expired()` 在 `app/services/sandbox_manager.py:79-88`

**实现步骤**：

1. 新建 `app/services/session_lifecycle.py`：
   - `async def terminate_session(session, db, reason: str) -> bool` —— 把 `sandbox_sessions.py:1246-1307` 的单会话回收逻辑抽为**唯一终止原语**（状态机校验失败返回 False 并记 warning）
   - `async def cleanup_expired_sessions(db) -> int` —— 扫 `status in (pending, provisioning, running)` 且 `is_session_expired()`，逐个调 `terminate_session(reason="session_expired")`，写审计 `action="session.expired_cleanup"`
   - `async def session_cleanup_loop(interval: float) -> None` —— 仿 `kms_service._ttl_cleanup_loop`：每轮 `async with async_session() as db` 执行清理 + `commit`，单轮异常捕获不退出
2. `app/api/sandbox_sessions.py` 的 `POST /cleanup-expired` 端点改为调 service 函数（保持 API 兼容，去重逻辑）
3. `app/main.py` lifespan：注册 `_session_cleanup_task = asyncio.create_task(session_cleanup_loop(settings.SESSION_CLEANUP_INTERVAL_SECONDS))`，shutdown 时 cancel（照抄 `:62-82` 的 `_ttl_task` 模式）
4. `app/core/config.py` 新增：`SESSION_CLEANUP_INTERVAL_SECONDS: int = 300`
5. dev 沙箱会话（内存态 `dev_sandbox` 单例，`app/services/data_product_sandbox.py:489`）：同一循环内调用 dev_sandbox 的过期清理（若该服务无 TTL 逻辑，按 `config.max_duration_seconds` 判定后 `terminate_session`）

**测试**（新建 `tests/test_session_lifecycle.py`）：
- 过期 session → `terminate_session` → 断言：status=terminated、`KeyMetadata.status=destroyed`、`NetworkPolicy.active=False`、租户配额回落
- 未过期 session 不被触碰；状态机非法迁移（终态 session）返回 False 且不抛异常
- `TESTING=1` 时 lifespan 不注册循环

**验收命令**：`pytest tests/test_session_lifecycle.py -v`
**工时**：1.5 人日

---

### T2 · 合约终止/到期级联回收（修 A1）

**目标**：合约 TERMINATED 后，其名下运行中会话、已分发密钥、策略一并回收，审计留痕。

**代码现状**：
- `SandboxSession.contract_id` 关联字段存在（`app/models/sandbox_session.py:59`，`String(255)`，存合约 ID 字符串）
- `sandbox_runtime.secure_destroy()`（`app/services/sandbox_runtime.py:1676+`）已实现容器终止 + 密钥销毁 + 任务取消 + 审计，但**不含网络策略移除与配额释放** —— 因此级联应复用 T1 的 `terminate_session` 原语（更完整），而非 `secure_destroy`
- Contract 模型无到期时间字段（`app/models/contract.py` 全文）

**实现步骤**：

1. `app/services/contract_service.py` `terminate()` 改造：
   ```python
   contract.status = ContractStatus.TERMINATED.value
   # 级联回收（新增）
   from app.services.session_lifecycle import terminate_session
   sessions = (await db.execute(
       select(SandboxSession).where(
           SandboxSession.contract_id == str(contract_id),
           SandboxSession.status.in_([ACTIVE 三态]),
       ))).scalars().all()
   for s in sessions:
       ok = await terminate_session(s, db, reason="contract_terminated")
       ...
   ```
2. policy bundle 撤销：查 `PolicyBundle`（按 contract_id 关联）标记 revoked/inactive；`opa_client` 增加删除接口（若现有 push/evaluate 之外无 delete，补 `delete_policy(policy_id)`，OPA REST `DELETE /v1/policies/...`）
3. 审计：`audit_service.log(action="contract.terminate_cascade", detail={"sessions_terminated": n, "keys_destroyed": m, "policy_bundles_revoked": k})`
4. 到期自动终止：Alembic 迁移给 `contracts` 加 `valid_until: DateTime(timezone=True) | None`（可选列，签署/激活时由 terms 写入）；T1 的 `session_cleanup_loop` 每轮顺带扫 `Contract.status == ACTIVE AND valid_until < now()` → 走同一 `terminate()` 级联
5. API 层无需新端点（`POST /contracts/{id}/terminate` 已存在）

**测试**（新建 `tests/test_contract_terminate_cascade.py`）：
- 合约 + 2 个 ACTIVE 关联会话 → terminate → 断言会话 TERMINATED、KeyMetadata DESTROYED、policy bundle revoked、审计含级联明细
- 无关联会话的合约 terminate → 幂等成功
- 到期合约（valid_until 过去）被循环扫描回收

**验收命令**：`pytest tests/test_contract_terminate_cascade.py -v`
**工时**：2 人日（含迁移）

---

### T3 · dev 沙箱接入合约校验（修 A3，按修正范围）

**目标**：生产环境 dev 会话携带数据产品时必须挂在有效合约下，DP 预算从合约继承而非自报。

**实现步骤**：

1. `app/core/config.py`：`DEV_SANDBOX_REQUIRE_CONTRACT: bool = True`（**安全默认 fail-closed**）
   - `.env.example` / `docker-compose.yml`（dev）显式 `DEV_SANDBOX_REQUIRE_CONTRACT=false` 并注释说明
   - `docker-compose.prod.yml` / `helm/cds/values.yaml` 保持 true
2. `CreateDevSessionRequest` 增加 `contract_id: uuid.UUID | None = None`（`app/api/dev_sandbox.py:28-34`）
3. `create_dev_session` 校验链（`data_product_id` 非空且开关开启时）：
   - `contract_id` 必填，否则 400
   - 合约存在且 `status in (ACTIVE, SIGNED)`，`str(data_product.id) in (contract.product_ids or [])`，否则 403 带原因
   - `contract.provider_id == current_user.id`（dev 模式仅供方使用，与现有属主校验 `:73-74` 语义对齐）
   - DP 预算：忽略请求自报的 `dp_epsilon_budget`，改从 `contract.dp_epsilon_budget` 继承；合约无预算且请求声称要用 DP → 拒绝
4. `session.metadata` 记录 `contract_id`；审计 `action="dev_session.create"` 带合约上下文
5. 纯开发模式（`data_product_id=None`）不受影响

**测试**（新建 `tests/test_dev_sandbox_contract.py`）：
- 开关开 + 有 data_product_id 无 contract_id → 400
- 合约不覆盖该产品 / 非 ACTIVE → 403
- 合约有效 + provider 本人 → 201，metadata 含 contract_id
- 请求自报 `dp_epsilon_budget=999` 但合约为 1.0 → 会话预算为 1.0

**验收命令**：`pytest tests/test_dev_sandbox_contract.py -v`
**工时**：1.5 人日

---

### T4 · KMS 证明强制 + wrapped keys 持久化（修 B1 + B3）

**目标**：无有效 TEE 证明不得分发会话密钥；wrapped 密钥重启可恢复。

**代码现状**：
- `distribute_key`（`app/services/kms_service.py:168-204`）证明可选
- `generate_session_key/generate_data_key/rotate_key` 只写内存 dict（`:40-42, :78-94, :127-148, :150-166`）
- KMS 服务是同步方法、调用方（`sandbox_sessions.py` 等）是 async —— 持久化必须由调用方完成

**实现步骤**：

1. `app/core/config.py`：`KMS_REQUIRE_ATTESTATION: bool = True`（安全默认；`.env.example`/dev compose 设 false，`tests/conftest.py` 统一设 false 保持现有 123 个测试不破坏）
2. `distribute_key` 开头插入 fail-closed 分支：
   ```python
   if not (attestation or tee_quote) and settings.KMS_REQUIRE_ATTESTATION:
       logger.warning("[KMS] Rejected unattested key distribution: key=%s session=%s", key_id, session_id)
       return None
   ```
   同时补审计（经调用方 async 侧记 `kms.distribution_rejected`）
3. 持久化（调用方写入模式，避免重构同步签名）：
   - Alembic 迁移：`key_metadata` 表加 `wrapped_payload: LargeBinary | None`、`sm2_encrypted_payload: LargeBinary | None`
   - `kms_service` 新增两个同步方法：`export_wrapped(key_id) -> bytes | None`、`import_wrapped(key_id, blob: bytes) -> bool`
   - 调用方接线：`app/api/sandbox_sessions.py`（创建会话拿到 key 后）与 `app/services/contract_fulfillment.py` 把 `export_wrapped()` 结果写入该 key 的 `KeyMetadata.wrapped_payload`
   - 恢复：新建 `app/services/kms_recovery.py` `async def restore_wrapped_keys(db)` —— 启动时把所有非 DESTROYED 的 `KeyMetadata.wrapped_payload` `import_wrapped` 回内存；lifespan 注册调用
4. **多副本边界如实声明**：wrapped blob 落 DB 后，跨副本恢复依赖共享 KEK —— 真实 Vault/HSM 后端天然共享；HSM 软件 fallback 模式下 KEK 为进程内生成，此时标注"单副本限制"并在部署清单注明（与 P-GAP-004 的 HSM 环境验收项合并跟踪）

**测试**（新建 `tests/test_kms_attestation_required.py`）：
- require=true 无证明 → `distribute_key` 返回 None
- require=true 伪造证明 → 返回 None（走 `_verify_attestation` 失败分支）
- require=false（dev 配置）→ 返回 hex（回归现有 `tests/test_kms*.py` 不破坏）
- 持久化：generate → export → 新 KMS 实例 import → `get_key` 恢复一致

**验收命令**：`pytest tests/test_kms_attestation_required.py tests/test_kms.py tests/test_kms_lifecycle.py -v`
**工时**：2.5 人日（含迁移与恢复路径）

---

### T5 · 沙箱任务结果强制过输出网关（修 E1，按修正范围）

**目标**：合约 `OutputPolicy`（行数上限/格式白名单/检查规则集）在任务完成路径强制执行；结果取回必须校验落库检查结论；消除自报参数。

**代码现状**（修正后）：
- 管线 OUTPUT_INSPECTING handler 存在但走裸 `inspect_text_output`（`task_pipeline.py:491-564`）
- 落库机制 `_finalize_orm_output_inspection`（`:567-611`）已把报告写进 `resource_usage.output_security`
- `output_gateway.process(data, output_format, policy, user_id, session_id)`（`app/services/output_gateway.py:52-152`）完备：格式白名单 → 行数截断 → 检查 fail-closed → 水印/签名
- `/output-control/gateway` 端点限制参数自报（`output_control.py:282-329`）
- `get_task_result`（`sandbox_tasks.py:235-270`）只看 status

**实现步骤**：

1. 新建 `app/services/output_policy.py`：
   ```python
   async def build_output_policy(db, contract_id: str | None) -> OutputPolicy:
       # 无合约 → 保守默认（max_output_rows=10000, formats=["csv","json"]）
       # 有合约 → 从 Contract 读 max_output_rows / allowed_output_formats("csv,json"→list)
       #          / inspection_rule_set / dp_epsilon_budget
   ```
2. `_output_inspecting_handler` 改造（`task_pipeline.py`）：
   - 经 session → `contract_id` 构造 `OutputPolicy`
   - 结构化输出（rows 列表）→ 调 `output_gateway.process(rows, fmt, policy, user_id, session_id)`；`GatewayResult.success=False` → `TaskStatus.REJECTED`
   - 文本输出 → 保留 `inspect_text_output`，叠加 policy 的行数上限截断与格式校验
   - `_finalize_orm_output_inspection` 落库报告增加 `watermark` / `signature` / `truncated` 字段
3. `/output-control/gateway` 端点：请求体的 `max_output_rows` 等仅作申请上限，实际 `policy = min(申请, 合约)`；**移除"请求体直接决定限制"**
4. `get_task_result` 门禁：
   - COMPLETED 任务校验 `resource_usage.output_security.inspection_report.passed is True`，缺失或不通过 → HTTP 409 `"inspection verdict missing/failed"`
   - 返回体增加 `redacted_output` / `watermark` / `signature`（取自落库报告），不再暴露未脱敏原始输出
5. legacy `task_worker` 回退路径：抽取公共函数 `inspect_and_finalize(...)`（即步骤 2 的核心逻辑），worker 完成 task 前同样调用 —— 消除"pipeline 启动失败即无输出保障"的缺口

**测试**（新建 `tests/test_output_gateway_enforcement.py`）：
- 合约 `max_output_rows=10`、任务产出 100 行 → 结果 10 行 + `truncated=true`
- 合约格式白名单不含 parquet → parquet 输出 REJECTED
- 手工清空 `resource_usage` 后调 `get_task_result` → 409
- 伪造 status=COMPLETED 但报告 `passed=False` → 409

**验收命令**：`pytest tests/test_output_gateway_enforcement.py tests/test_task_state_machine.py -v`
**工时**：3 人日

---

## 三、P1 设计对齐（7 项 · 2-4 周）

### T6 · 合约用途限定（A4）

- `app/models/contract.py` 加 `purpose: String(255) | None`、`purpose_scope: JSON | None`（枚举建议：`statistical_analysis / model_training / member_matching / api_service`）+ Alembic 迁移
- 用途进三方签名原文（`_platform_witness_sign` 及供需签名的 payload）—— 仅对新签署合约生效，存量合约 purpose 为空视为"不限"，避免破坏既有签名
- 任务提交（`app/api/sandbox_tasks.py` submit）：payload 声明 `purpose`，与合约 `purpose` 比对，不匹配 → 400 拒绝并审计 `task.purpose_mismatch`
- 测试：`tests/test_contract_purpose.py`

**工时**：2 人日

### T7 · 字段级分类分级 → 策略联动（A5）

- `app/models/data_resource.py` 加 `field_classifications: JSON | None`（`{"id_card": 3, "phone": 2}`，1-4 级）
- `app/services/policy_compiler.py` 增加 `field_rules_from_classifications()`：level≥3 → 强制脱敏/禁出规则，level=4 → 禁止出沙箱
- 输出侧接线：`output_gateway` / `field_acl` / `rls_engine` 按分级执行掩码；DataProduct 发布时（`app/api/data_products.py`）校验分级完整性
- 测试：`tests/test_field_classification_policy.py`

**工时**：3 人日

### T8 · 链上存证真实现（F1）

- `app/core/config.py`：`BLOCKCHAIN_BACKEND: str = "pg_append_only"`（可选 `fisco_bcos` / `ant_chain`）
- FISCO 路径：复用根目录 `docker-compose.blockchain.yml` 起 4 节点链；适配 `FISCOBCOSAdapter`（`blockchain_adapter.py:89+`）走 bcos3sdk（Python SDK）或独立 connector 服务代理发链；`AnchorResult` 必须携带真实 `tx_hash` + `block_number`
- **诚实标注（最小可先行）**：未配置真链时 `AnchorResult.metadata.backend = "pg_append_only"`，证明包（P-GAP-013 evidence bundle）与前端合规报告显式透出 backend，禁止冒充"已上链"；`AuditRegistry.sol`（`contracts/`）编译部署到测试链做 e2e
- 测试：`tests/test_blockchain_adapter.py` 扩展（真链为环境验收项，CI 中仅验证 backend 标注逻辑）

**工时**：5 人日（真链 e2e 另计环境）

### T9 · 真实中文 NER（E2）

- `requirements.txt` 加 `LAC`（或 HanLP，LAC 更轻）
- `app/services/pii_ner.py` 改双层：L1 正则快速通道（保留 `NERPatterns`）→ L2 模型 NER（姓名/地址/机构）
- 模型缺失时降级正则，报告标注 `ner_engine: "regex"`（不冒充模型识别）
- 测试：`tests/test_pii_ner.py` 扩展（模型安装为环境验收项）

**工时**：2 人日

### T10 · 训练 fail-closed 与真 MIA（C1/C2）

- `app/core/config.py`：`TRAINING_REQUIRE_TORCH: bool = True`；`app/services/cpu_trainer.py:147-155` 与 `llm_sft_runtime.py:330-354` 的模拟回退在开关开时抛错拒绝任务（而不是静默返回模拟结果）
- MIA（`llm_sft_runtime.py:200-221` `_sample_confidence`）：替换确定性代理 —— 小规模真实影子模型（同架构、少 epoch）；无法训练影子模型时输出 `mia_status: "not_evaluable"` 并跳过阈值门禁（明确"不可评估"优于"虚假保证"）
- 测试：`tests/test_training_fail_closed.py`

**工时**：3 人日

### T11 · RAG 与隐私推理最小闭环（C3/C5）

分期实施，第一期只做"语料不出域的最小检索问答"：

- **向量库**：沙箱内 DuckDB（项目已依赖）+ `sqlite-vec`/自实现余弦检索（FAISS 为备选）；embedding 模型随沙箱镜像分发，语料加密入库（SM4，复用现有 storage_service）
- **检索-推理**：检索与生成全部在 sandbox runtime 内执行（复用 L0/L3 执行通道与网络策略），新任务类型 `TaskType.RAG_QUERY`
- **隐私推理端点**：复用 session/task 生命周期与输出网关（T5 之后的强制路径），推理结果走 `build_output_policy` 检查后放行
- 第二期（P2 衔接）：模型水印（已有 LSB 水印可复用）、MIA 门禁、推理计量计费

**工时**：第一期 8 人日

### T12 · 模拟/降级显式化开关矩阵（B2/D4/B5）

`app/core/config.py` 统一新增（生产默认全部 fail-closed）：

| 开关 | 默认 | 控制 |
|---|---|---|
| `ALLOW_SIMULATION` | `True`（prod 模板显式 false） | TEE/远程证明模拟器（`tee_simulator.py`、`remote_attestation.py`） |
| `SECCOMP_FALLBACK_ALLOWED` | `True`（prod false） | `sandbox_runtime.py:286,582` seccomp 失败回退 `cmd_no_seccomp` |
| `HSM_SOFTWARE_FALLBACK_ALLOWED` | `True`（prod false） | `hsm_adapter.py` / `audit_service.py:75-117` / `contract_service.py` 三处静默降级 |
| `FEDERATION_JWT_KEY_REQUIRED` | `True` | `federation_connector.py:42-50` 静态常量回退 |

- 扩展 `settings.validate_jwt_security()`（`main.py:23` 已在启动时调用）为通用 `validate_security_config()`：生产模式下上述开关未关闭 → 启动拒绝
- 各服务响应/证明包透出 `simulation: true/false` 与实际 backend

**工时**：2 人日

---

## 四、P2 能力补齐（路线图 · 1-2 月）

| # | 方向 | 路线 | 前置 |
|---|---|---|---|
| 1 | 同态加密 / MPC 计算（B4） | 评估 SecretFlow（HEU 做 HE 统计、SPU 做两方 MPC）；`mpc_service`（`app/services/mpc_service.py`，现仅 Shamir 秘密分享）保留密钥托管场景，新增 HE/MPC 计算服务与 API；不重复造轮 | SecretFlow 许可与部署评估 |
| 2 | 智能体执行框架（C4） | 供方/需方智能体运行时：沙箱内受限执行 + 工具白名单 + 全程审计，复用 code_scanner 与网络策略；**改名 `cdc_agent` 依赖注释澄清其 CDC 语义**，避免误导 | T11 任务类型扩展机制 |
| 3 | 沙箱快照与回滚（D2） | L0/L3 用 overlayfs/copy-up 做 workspace 快照；K8s 用 VolumeSnapshot；接入 session 状态机新增 `snapshotting/rolling_back` | T1 生命周期原语 |
| 4 | TEE 硬件路径（B2） | Gramine（SGX）/ CoCo（SEV-SNP）适配 `TEEAdapter._provision_hardware`；远程证明接 Intel PCS / AMD KDS 外部信任根；`AttestationPolicy.allowed_measurements` 强制非空 | 真实硬件（P-GAP-004 环境验收） |
| 5 | K8s 适配器进程内化（D3） | `k8s_sandbox.py:147-149` 的 kubectl 子进程改为官方 Python client（kubernetes pkg） | 集群环境 |
| 6 | GPU 加速 runtime 插件（C6） | 预留 `AccelerationRuntime` 扩展点对齐 P19 | TEE 硬件路径 |

---

## 五、横切面

### 5.1 配置开关矩阵（本方案新增的 Settings 字段汇总）

| 字段 | 默认值 | dev/test | 生产 | 所属任务 |
|---|---|---|---|---|
| `SESSION_CLEANUP_INTERVAL_SECONDS` | 300 | 同默认 | 同默认 | T1 |
| `DEV_SANDBOX_REQUIRE_CONTRACT` | **True** | 显式 false | true | T3 |
| `KMS_REQUIRE_ATTESTATION` | **True** | 显式 false | true | T4 |
| `TRAINING_REQUIRE_TORCH` | **True** | 显式 false | true | T10 |
| `ALLOW_SIMULATION` / `SECCOMP_FALLBACK_ALLOWED` / `HSM_SOFTWARE_FALLBACK_ALLOWED` | True | true | **显式 false** | T12 |
| `BLOCKCHAIN_BACKEND` | pg_append_only | 同默认 | fisco_bcos | T8 |

原则：**新开关安全默认 fail-closed，宽松只在显式配置处发生**；`.env.example`、`docker-compose.yml`、`docker-compose.prod.yml`、`helm/cds/values.yaml` 同步更新（沿用 P-GAP-002 的 CDS_ 前缀规范）。

### 5.2 Alembic 迁移清单（按依赖顺序）

| 迁移 | 表/列 | 任务 |
|---|---|---|
| m1 | `contracts` + `valid_until: DateTime(tz) | None` | T2 |
| m2 | `key_metadata` + `wrapped_payload: LargeBinary | None` + `sm2_encrypted_payload: LargeBinary | None` | T4 |
| m3 | `contracts` + `purpose: String(255) | None` + `purpose_scope: JSON | None` | T6 |
| m4 | `data_resources` + `field_classifications: JSON | None` | T7 |

命令：`alembic revision --autogenerate -m "..."` → 人工核对 → `alembic upgrade head`。全部为可空新列，**存量数据零迁移风险**；dev/test 环境 `create_all` 自动建列（`main.py:36-44` 双轨），CI 需跑一次 `alembic upgrade head` 验证迁移正确性。

### 5.3 测试计划

- 新增：`tests/test_session_lifecycle.py`、`tests/test_contract_terminate_cascade.py`、`tests/test_dev_sandbox_contract.py`、`tests/test_kms_attestation_required.py`、`tests/test_output_gateway_enforcement.py`、`tests/test_contract_purpose.py`、`tests/test_field_classification_policy.py`、`tests/test_training_fail_closed.py`
- 回归：P0 合入后全量 `pytest tests/ -x`（现有 123 个测试文件基线）；`tests/conftest.py` 统一覆盖新开关的宽松值，保证存量测试语义不变
- 前端：`cds-frontend npm run build` 编译级验证（涉及结果返回体新增字段——纯增量，向后兼容）

### 5.4 发布顺序与依赖

```
T4 (KMS) ──┐
T1 (调度器) ─┼─→ T2 (级联，复用 T1 的 terminate_session 原语) ─→ P0 全量回归 → 发版
T3 (dev合约) ┤
T5 (输出网关) ┘（与 T2 并行，无依赖）
```

每项独立成 PR， revert 边界干净；所有新开关支持配置级快速回退（如 `KMS_REQUIRE_ATTESTATION=false` 即恢复旧行为）。

### 5.5 里程碑

| 里程碑 | 内容 | 时点 |
|---|---|---|
| M1 | T1 + T4 合入（调度器 + KMS 强制） | 第 1 周末 |
| M2 | T2 + T3 + T5 合入，P0 清零，全量回归 | 第 2 周末 |
| M3 | P1 七项（T6-T12），按 T6→T12→T9→T10→T7→T8→T11 顺序滚动 | 第 3-6 周 |
| M4 | P2 按业务优先级启动（建议先快照回滚与智能体框架） | 第 2 月起 |

**工时汇总**：P0 ≈ 10.5 人日；P1 ≈ 25 人日（不含 T11 第二期与真链 e2e 环境）；P2 按立项另估。

### 5.6 风险表

| 风险 | 影响 | 缓解 |
|---|---|---|
| T3/T4/T10 改变现有 dev/test 工作流 | 存量测试与本地开发中断 | conftest 统一宽松值；`.env.example` 显式注释；发布说明高亮 |
| T4 多副本 wrapped keys 依赖共享 KEK | 水平扩展时密钥不可恢复 | 软件KEK 场景标注单副本；生产用 Vault（P-GAP-004 验收项） |
| T5 与 legacy task_worker 兼容 | 回退路径行为不一致 | 公共函数 `inspect_and_finalize` 双路径复用；e2e 覆盖回退分支 |
| T2 级联误杀（合约误终止→会话批量回收） | 业务中断 | 级联前审计留痕；终止 API 已有 reason 强制（P-GAP-014）；valid_until 为可空列不自动生效 |
| 存量合约无 purpose | 用途校验失效 | 空值视为"不限"，仅新合约生效 |
| Alembic 与 create_all 双轨漂移 | 迁移遗漏列 | CI 增加 `alembic upgrade head` + `alembic check` |

---

## 六、执行边界声明

- 本方案只覆盖**软件闭环**改动；真实 TEE 硬件、真链 e2e、HSM/Vault 生产、性能长稳按既有 P-GAP-004/005/006/007/008 环境验收项跟踪，不在本方案内伪造完成。
- 所有"已有实现可复用"的判断均基于 2026-09-06 源码复核；实施时若与现状冲突，以代码为准并回写本文件。

---

## 七、执行记录（2026-09-06）

### P0 已实现（T1–T5，全部含测试）

| 任务 | 状态 | 关键产出 |
|---|---|---|
| T1 会话生命周期调度器 | ✅ 已实现 | `app/services/session_lifecycle.py`（`terminate_session` 唯一终止原语 + `cleanup_expired_sessions` + `session_cleanup_loop`）；main.py lifespan 注册（`TESTING=1` 不注册）；cleanup-expired 端点改调 service；修复 `is_session_expired` 的 SQLite naive-datetime 潜在 bug；清扫范围从 3 态扩到全部非终态（修复 READY 孤儿沙箱泄漏） |
| T2 合约终止级联 | ✅ 已实现 | `contract_service.terminate()` 级联终止会话（复用 T1 原语）+ 撤销 policy bundle（DB `revoked_at` + OPA `delete_policy`）+ 审计 `contract.terminate_cascade`；`valid_until` 列 + 激活时从 terms 取值 + 到期自动扫描；`terminate_contract_sessions` 死代码收敛到统一原语 |
| T3 dev 沙箱合约门禁 | ✅ 已实现 | `DEV_SANDBOX_REQUIRE_CONTRACT`（默认 true）+ `contract_id` 字段 + 状态/产品覆盖/当事方校验 + DP 预算改为合约继承（弃用请求自报） |
| T4 KMS 证明强制 + 持久化 | ✅ 已实现 | `KMS_REQUIRE_ATTESTATION`（默认 true）fail-closed 分支；`export/import_wrapped`；`DataEncryptionKey` 加 `wrapped_payload`/`sm2_encrypted_payload`；三个 session-key 创建点持久化；`kms_recovery.restore_wrapped_keys` 启动恢复；销毁时 crypto-erase |
| T5 输出网关强制 | ✅ 已实现 | `app/services/output_policy.py`（`build_output_policy`/`clamp_gateway_request`/`enforce_text_output_policy`）；pipeline OUTPUT_INSPECTING 与 legacy worker 双路径接入合约行数截断；`get_task_result` 检查结论门禁（缺判定 → 409）+ 返回脱敏输出/水印/签名；`/gateway` 端点自报参数改为合约钳制（只能收窄不能放宽） |

**新增测试**（5 文件 24 用例全绿）：`tests/test_session_lifecycle.py`、`tests/test_contract_terminate_cascade.py`、`tests/test_dev_sandbox_contract.py`、`tests/test_kms_attestation_required.py`、`tests/test_output_gateway_enforcement.py`

**配置/交付物**：`.env.example`、`docker-compose.yml`（dev 宽松）、`docker-compose.prod.yml` + `helm/cds/values.yaml`（prod fail-closed）均已同步三开关；首个 Alembic 迁移 `alembic/versions/0001_p0_security_hardening.py`（仅 additive 可空列，含 purpose 预留列）。

### 实现中发现并修正的方案偏差

1. **KeyMetadata 模型去重**：`app/models/kms.py:131` 有 `KeyMetadata = DataEncryptionKey` 兼容别名，调用方实际使用 `data_encryption_keys` 表。持久化列加在该模型上，而非遗留的 `models/key_metadata.py`（后者未使用）。
2. **A3 的 DP 继承**：按修正后范围实施 —— dev 会话有效 DP 预算一律取合约值，`dp_epsilon_budget` 请求字段在受合约治理的会话中完全失效。

### 回归结论（Windows 环境）

- 受影响测试文件基线 vs 改动后**结果完全一致**（同组 171 通过 / 29 失败）→ **零回归**。
- 全量（排除 4 个收集错误文件）：**1974 通过 / 104 失败 / 16 error**。失败与 error 全部为既有环境问题，非本次改动引入：
  - `import resource`：Windows 无 Unix `resource` 模块（sandbox_security.py，本方案未触碰）→ 沙箱 provision 相关测试
  - `test_e2e_full_lifecycle.py`：打真实部署集群 `172.21.0.2:30080` 的网络 e2e，本环境无集群
  - `column_encryption.py:270` 既有 `NameError: Any`（本方案未触碰）→ 4 个收集错误
  - tmpfs / TEE 设备检测 / Windows 路径等 Linux 专属断言
- 本方案新增代码以 `py_compile` 全绿 + 5 个新测试文件通过作为证据。

### 未实施（按计划留待后续）

- P1（T6–T12）与 P2（6 方向）未动；`api/kms.py` 的 `DataEncryptionKey.encrypted_key` 存明文 hex 属既有独立问题（与 B3 不同模型），已记录未修（scope 纪律）。

---

### P1 批次执行记录（2026-09-06 追加）

| 任务 | 状态 | 关键产出 | 对应 specs |
|---|---|---|---|
| T6 合约用途限定 | ✅ 已实现 | `Contract.purpose/purpose_scope` + 签名原文（`contract_sign_data` 扩展，向后兼容）+ `SandboxTask.purpose` + 任务创建用途门禁 | G-131 |
| T7 字段级分类分级 | ✅ 已实现 | `DataResource.field_classifications` + `policy_compiler.field_rules_from_classifications` + 网关掩码/禁出 + `build_output_policy` 端到端注入 | G-132 |
| T10 训练 fail-closed + MIA 诚实化 | ✅ 已实现 | `TRAINING_REQUIRE_TORCH`（默认 true）；`MIAProbeResult.mia_status`，硬门禁仅对真实影子模型生效 | G-133 |
| T12 模拟/降级显式化矩阵 | ✅ 已实现 | 4 开关 + `validate_security_config` + HSM/seccomp/KMS 三处失败关闭接线 | G-134 |
| E3 DP epsilon 上下限 | ✅ 已实现 | 3 个 DP 护栏配置 + `consume/allocate` 越界失败关闭 | G-135 |
| 顺带修复 | ✅ | `column_encryption.py` 补 `from typing import Any`（解除 2 个测试文件收集阻断，1 行安全改动） | — |

**新增测试**（5 文件 24 用例全绿）：`tests/test_contract_purpose.py`、`tests/test_security_config_matrix.py`、`tests/test_training_fail_closed.py`、`tests/test_field_classification_policy.py`、`tests/test_dp_guardrails.py`

**真实 Linux 环境验证**（用户提供 100.112.3.247:22022，openEuler 22.03 / Python 3.11.9，装 bwrap/poppler-utils 后）：`pytest tests/` → **2211 passed / 10 failed / 16 error**。剩余全部为既有环境/测试问题（16 error = 打真实集群的 e2e；5×L0 = 容器 cgroup 只读；2×L0 bwrap = 单跑通过；3×e2e = 水印 JSON / 产品删除守卫 / 会话 owner 授权，均在未改动路径），**零回归**。

**仍未实施**（T8 真链、T9 重 NER 依赖、T11 RAG 二期、P2 六方向），依赖外部环境/硬件或另立项。`api/kms.py` 明文 hex 既有问题仍记录未修。

---

### Round 34 执行记录（2026-09-06 追加）

| 任务 | 状态 | 关键产出 | 对应 specs |
|---|---|---|---|
| KMS DEK 明文落库修复 | ✅ 已实现 | `app/api/kms.py` `create_dek`/`rotate_dek` 的 `encrypted_key` 从 `key_bytes.hex()`（明文）改为 `export_wrapped(key_id).hex()`（KEK 封装 blob）；列无读取方，纯存储格式加固 | G-136 |
| T9 PII NER 模型层诚实化（最小可行） | ✅ 已实现 | `PIINERService.ner_engine`（auto/rule/lac/transformers/regex）+ `PIIDetectionResult.ner_engine` 诚实标注；新增 LAC 模型层（layer=`ner_lac`）；模型不可用如实降级 rule；修复不存在的默认模型名；`PII_NER_ENGINE` 配置 | G-137 |
| T8 链存证 backend 诚实披露（最小可行） | ✅ 已实现 | audit anchor/verify/merkle-proof/compliance-report 新增 `backend`/`backend_label`/`is_consortium_chain`；verify 加 `verification_note`；合规报告 service 加 `anchoring`；前端 auditApi.ts 类型扩展 | G-138 |

**新增测试**（3 文件 13 用例全绿）：`tests/test_kms_dek_wrapped.py`（3）、`tests/test_pii_ner_engine.py`（10）、`tests/test_blockchain_backend_disclosure.py`（5）

**受影响既有文件回归**：test_kms / test_kms_lifecycle / test_pii_ner / test_compliance_report / test_audit_api / test_audit_enhanced —— 56 + 36 passed 全绿。

**全量回归**（排除 2 个 Windows `import resource` 收集错误文件与 e2e 集群文件）：**2103 passed / 75 failed / 16 error**。剩余失败 100% 为既有环境问题（`resource` 模块缺失、tmpfs/磁盘加密、bwrap/firecracker、TEE 设备检测、e2e 集群、Windows GBK 读 UTF-8），**零回归**，改动文件零命中。

**仍未实施**：T11 RAG 一期（约 8 人日大特性，用户确认留待独立轮次）、T8 真链 e2e、T9 模型实际安装（LAC 可选依赖，环境验收项）、P2 六方向（同态/MPC、智能体框架、快照回滚、TEE 硬件、K8s python client、GPU 插件）。

---

### Round 35 执行记录（2026-09-06 追加）—— T11 RAG 一期

| 任务 | 状态 | 关键产出 | 对应 specs |
|---|---|---|---|
| T11 一期 · 语料不出域的最小检索问答 | ✅ 已实现 | `TaskType.RAG_QUERY` + `create_task` 的 `rag_query` 分支（生成自包含 runner 加密落库）；`POST /api/v1/rag/corpus` 摄入端点；`app/services/rag_embedding.py`（tf/regex 确定性嵌入 + transformers 宿主侧后端 + 诚实标注）；`app/services/rag_service.py`（分块/建索引/检索/抽取式答案/`build_rag_runner`/`prepare_corpus_for_task`/`validate_rag_runner`）；`SandboxRuntime.get_workspace`；`_default_running_handler` RAG 语料物化；`RAG_*` 配置 + `validate_security_config` 校验；前端 `ragApi.ts`/TaskType 枚举 | G-139 / G-140 / G-141 |

**关键决策（源自计划 .omo/plans/t11-rag-phase1.md）**：
1. **一期为依赖最轻 + 诚实标注**：嵌入用自实现 char n-gram TF（确定性、沙箱内可复现），`auto` 一期解析为 tf；transformers 仅宿主侧实验，`build_corpus` 对其 fail-closed（runner 不可复现，属二期）。
2. **一期为抽取式答案**：`answer_mode="extractive_retrieval"`，绝不冒充生成式 LLM。
3. **语料进沙箱**：摄入信封加密持久化（storage_service）→ 任务执行前 `prepare_corpus_for_task` 物化到 `workspace/input` 并用会话 DEK 重加密（与 provision 同构）→ runner 用 `CDS_DEK_HEX` 内嵌解密读取（明文/加密双形态验证通过）。
4. **系统代码不经通用扫描器**：`validate_rag_runner` 模板字节级校验（AST 提取字面量 → 重新生成逐字节比对），篡改/注入即拒，**扫描器白名单零放宽**。

**新增测试**（5 文件 40 用例全绿）：`tests/test_rag_embedding.py`（11）、`tests/test_rag_service.py`（13）、`tests/test_rag_runner.py`（6，子进程端到端 + 加密 + host/runner 一致性）、`tests/test_rag_task.py`（6，RAG 任务 API + purpose 门禁 + T5 409 门禁）、`tests/test_rag_ingest.py`（4）。

**验证**：受影响回归 111 passed 全绿；`compileall -q app tests alembic` 通过；全量 `pytest tests/`（排除 2 个 Windows `import resource` 收集错误文件）→ **2143 passed / 75 failed / 16 error / 3 skipped**，与基线 2103/75/16 相比新增恰为 40 个 RAG 用例，**零回归**。前端新增为编译级（本机无 node_modules 未构建）。

**仍未实施**：T11 二期（沙箱镜像内生成式 LLM、transformers 检索、模型水印、MIA 门禁、推理计量计费）、T8 真链 e2e、T9 LAC 模型实际安装、P2 六方向（同态/MPC、智能体框架、快照回滚、TEE 硬件、K8s python client、GPU 插件）。

### Round 36 执行记录（2026-09-06 追加）—— 部署 fail-closed 姿态

| 任务 | 状态 | 关键产出 | 对应 specs |
|---|---|---|---|
| T12 后续 · 生产部署 fail-closed 姿态 | ✅ 已实现 | `docker-compose.prod.yml`：修 `CDS_DEBUG=true→false`（原静默禁用生产安全校验）+ 显式声明全部仿真/回退开关（SECCOMP/HSM=false，硬件门禁项=true 含注释）；`.env.prod` 同步；`validate_security_config` 对纯降级项（SECCOMP/HSM）从 WARN 升级为生产 RAISE（启动即失败），硬件门禁项保持 WARN | Round 36 |

**背景与决策（源自目标"补齐软件层所有可做特性"）**：
1. 全量探查确认：141 个软件 gap（G-001..G-141）已全部关闭（`active software gap=0`）；剩余 FG-001..FG-016 / P2 六方向 / T8 / T9 均依赖真实硬件/外部系统/e2e，按项目约束作为产品化验收项持续跟踪，本机（Windows、无 TEE/GPU/K8s/链）不可伪造实现。
2. **软件层可真实落地的剩余项是生产部署 fail-closed 姿态**：仿真/回退开关的 fail-closed 机制已实现且有测试（KMS/HSM/seccomp 三处消费点全绿），但生产部署文件未显式 fail-closed，且 prod compose 误设 `CDS_DEBUG=true` 使 `validate_security_config` 的 `is_prod` 门被关闭 → 生产降级被静默接受。
3. **WARN→RAISE 边界**：`SECCOMP_FALLBACK_ALLOWED`（禁止无 seccomp 重试）与 `HSM_SOFTWARE_FALLBACK_ALLOWED`（禁止内存软件 KEK）为无硬件依赖的纯降级项，生产必须启动即失败；`ALLOW_SIMULATION`/`TEE_*`/`GPU_TEE_SIMULATION` 为硬件门禁项，真实 TEE/GPU 落地前软件机密是唯一可部署姿态，保持 WARN 并如实披露。

**新增测试**（`tests/test_security_config_matrix.py` 扩展）：SECCOMP 生产 raise、HSM 生产 raise、硬件门禁项保持 WARN 共 3 用例；连同 `test_kms_attestation_required` 14 passed 全绿。

**验证**：受影响回归 67 passed / 7 既有 Windows `import resource` 收集错误（零新增回归）；`compileall -q app/core/config.py` 通过；部署文件（docker-compose.prod.yml/.env.prod）非测试覆盖路径，全量基线 2143/75/16 不变。

**仍未实施**：T11 二期、T8 真链 e2e、T9 LAC、P2 六方向、FG-001..FG-016（硬件/外部系统/e2e 门禁，持续跟踪为产品化验收项）。

### Round 37 执行记录（2026-09-06 追加）—— 远程 Linux e2e 闭环与合约签名可用性修复

| 任务 | 状态 | 关键产出 | 对应 specs |
|---|---|---|---|
| 远程 e2e 闭环（100.112.3.247） | ✅ 已实现 | 3 个过时 e2e 期望对齐（G-072 仅 DRAFT 可删 / G-073 会话所有权 / T5 零宽水印首行解析）；实时 API（独立 SQLite + 端口 18765，不触碰 fabric 栈）full-lifecycle **16/16 全绿**；admin fixture 429 步进等待 | Round 37 |
| 合约 SM2 签名客户端可用性修复 | ✅ 已实现 | 缺陷：规范签名串嵌入服务端 `now()` 微秒时间戳，客户端不可构造 → 全客户端不可用。修复：`ContractSign.timestamp` 必填 + ISO 校验，服务端用它构造规范串并强制 ±300s 新鲜度防重放 | Round 37 |

**关键决策**：
1. **3 个 e2e 失败全部判定为"测试期望过时"而非代码缺陷**——代码行为分别对应 G-072（发布后仅归档不可删）、G-073（sandbox-db 所有权强制）、T5 输出水印（隐形零宽字符）的既有安全设计；测试随设计对齐而非放松设计。
2. **合约签名缺陷是真缺陷**：fail-closed 加固（Round 32/33 拒绝 demo 签名）无意中使合法客户端也无法签名（无挑战/时间戳通道）。修复保持 fail-closed：时间戳客户端供给 + 服务端新鲜度窗口 + 状态机阻断同方重签，外部客户端按文档化规范串格式即可完成真实 SM2 双方签名闭环。
3. **实时 API e2e 的环境隔离**：独立 SQLite 文件 + 空闲端口，admin 直接播种；该机 fabric_* 服务栈与共享端口全程未动，验证后 API 进程已停止。

**验证**：三文件 e2e 57 passed（原 3 failed 清零）；合约 6 文件 53 passed 零回归；实时 full-lifecycle 16/16；test_p0 15 passed；全量 2275 passed / 8 failed / 16 errors / 1 skipped——8 failed 全为 cgroup v1 只读环境门禁（bwrap 命名空间/seccomp 隔离本身正常），16 errors 为进程内套件无实时 API 的预期形态（已由实时 API 16/16 单独覆盖）。

### Round 39 执行记录（2026-09-06 追加）—— 沙箱可用性 P0 三件套

| 任务 | 状态 | 关键产出 | 对应 specs |
|---|---|---|---|
| 会话文件 API（对照 CubeSandbox files / Sandboxie 写入虚拟化） | ✅ 已实现 | `app/services/session_files.py` + 4 端点；上传 DEK 加密落盘（provision 同构）、下载过 T5（critical→409 永不释放）、遍历/大小/数量防护、`.files_index.json` 元数据 | Round 39 |
| 快照/回滚（对照 Sandboxie 快照回滚） | ✅ 已实现 | `app/services/session_snapshots.py` + 4 端点；确定性 tar.gz 存于 bind 外、sha256 完整性强制校验、保留策略驱逐；copy-snapshot 先行、overlayfs 为 P2 优化 | Round 39 |
| 暂停/恢复/续期 | ✅ 已实现 | 复用状态机 SUSPENDED 转换（零状态机改动）+ `pre_pause_status`/`extended_seconds` 两列（Alembic 0002 远程实跑）；`is_session_expired` 尊重 extended；execute 门禁天然拦截暂停态 | Round 39 |
| e2e 扩展 | ✅ 已实现 | `test_16_sandbox_usability`（实时 API 全链：文件→PII 阻断→快照→回滚验证→暂停→续期→恢复→清理）；cleanup 顺延 test_17；auth fixture 429 步进重试 | Round 39 |

**验证**：新单测 21 passed（远程 Linux）；实时 full-lifecycle **17/17**（85.99s）；本地 Windows 13 passed + 8 POSIX 项 skip（远程全跑）。

### Round 40 执行记录（2026-09-06 追加）—— 交互与生态（exec/logs/usage/模板/脱敏/GC/SDK）

| 任务 | 状态 | 关键产出 | 对应 specs |
|---|---|---|---|
| exec 交互命令 API | ✅ 已实现 | `POST /{id}/exec`：bash + 硬超时 120s + T5 输出网关 + 命令哈希审计；DEK 注入、原始密钥不进 shell | Round 40 |
| logs / usage 观测 | ✅ 已实现 | `GET /{id}/logs`（since 增量 tail）+ `GET /{id}/usage`（工作区/文件/快照用量） | Round 40 |
| 会话模板 | ✅ 已实现 | 内置 3 模板 + JSON 扩展 + 编程注册；创建前校验防孤儿容器，provision 后 best-effort 种子；env 注入 execute/exec | Round 40 |
| 下载脱敏 | ✅ 已实现 | 非 critical → inspector 重写 `[REDACTED:*]` 释放；二进制原文+header 披露；critical 仍 409 | Round 40 |
| 快照 GC | ✅ 已实现 | terminate 移除归档（开关 `SESSION_SNAPSHOT_GC_ON_TERMINATE`，false=保留终止后恢复） | Round 40 |
| Python SDK | ✅ 已实现 | `sdk/` cds-sdk 包：会话/文件/快照/exec/logs/usage 全覆盖 + wait_for_status；MockTransport 测试 | Round 40 |

**验证**：本地 28 passed（15 SDK/模板 + 13 端点）；远程统一验证（实时 e2e + 全量）后台脱离运行，落 verify_r40.log。

**仍未实施**：WebSocket PTY 终端、前端会话工作台（Round 41）、overlayfs 快照（P2）、定时自动快照、输出文件浏览 API。

### Round 41 执行记录（2026-09-06 追加）—— 统一测试缺陷修复 + 前端会话工作台

**统一测试价值实证**：Round 40 的实时 e2e（远程）抓出三个单测无法覆盖的真实缺陷，全部修复：

| 缺陷 | 根因 | 修复 |
|---|---|---|
| create-with-template 500 | 模板种子分支缺 flush+refresh，`updated_at` 过期后 pydantic 懒加载触发 MissingGreenlet | 响应校验前 flush+refresh（对齐 create 流既有模式） |
| 回滚后 exec 报 FileNotFoundError | 快照只归档文件、丢弃空目录，`tmp/`（exec 暂存）消失 | 归档目录项 + 回滚后兜底重建 tmp//files/ |
| 上传文件沙箱内不可见 | 三个 bwrap 构建器均未挂载 `files/`（R39 e2e 只验了主机侧往返） | 全部构建器加 `/workspace/files` 读写挂载 |

**前端工作台**（visual-engineering 委托）：sandboxApi.ts 全端点类型化（exec/logs/usage/templates/files/snapshots/lifecycle）；SessionDetail 升级为完整工作台（文件/快照/exec 终端/审计日志/用量/生命周期控制）；SessionList 创建弹窗加模板选择。`tsc -b` 零错误。

**仍未实施**：PTY 流式终端 UI、执行历史时间线、overlayfs 快照（P2）、模板管理界面。
