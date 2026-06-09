# SS-02：数字合约 & 策略引擎 分系统技术规格

> 版本：v1.0 | 分系统编号：SS-02  
> 上游：SS-01（身份验证）、FISCO BCOS（合约存证）  
> 下游：SS-03（任务调度）、SS-04（沙箱运行时内策略执行）、SS-05（输出审查参数）

---

## 1. 职责边界

| 职责 | 说明 |
|------|------|
| 合约全生命周期管理 | 草稿→协商→签署→备案→履行→完成/违约→存证 |
| 策略编译 | 合约条款 → 可执行策略包（PolicyBundle），下发至 TEE 内 |
| 运行时策略执行 | PDP 评估每个数据操作请求，Permit / Deny（PDP 在普通进程运行，策略包加密传输保证完整性） |
| ε 预算账本 | 差分隐私预算的分配、消耗、续约管理 |
| 沙箱类型绑定 | 合约声明允许的沙箱模式（分析/建模/开发/训练/应用） |
| 数据产品开发授权 | 数商开发沙箱的特殊权限策略（高权限 + 强隔离） |

---

## 2. 合约数据模型

### 2.1 合约主表

```sql
CREATE TYPE contract_type AS ENUM (
  'data_query',         -- 结构化数据查询分析
  'model_training',     -- AI 模型训练（结构化/非结构化）
  'data_application',   -- 应用在沙箱内安全使用数据
  'api_service',        -- 数据服务 API 调用
  'joint_compute',      -- 多数商联合计算
  'product_dev',        -- 数商开发数据产品（开发沙箱）
  'data_modeling'       -- 数据建模（特征工程/标注）
);

CREATE TYPE sandbox_level AS ENUM ('L1','L2','L3');

CREATE TABLE contracts (
  contract_id        TEXT PRIMARY KEY,               -- 格式：CTR-{year}-{seq}
  version            INT NOT NULL DEFAULT 1,
  contract_type      contract_type NOT NULL,
  status             TEXT NOT NULL DEFAULT 'draft'
    CHECK (status IN ('draft','negotiating','signed','active',
                      'suspended','completed','violated','archived')),

  -- 参与方
  provider_org_id    TEXT NOT NULL,
  provider_cert_id   UUID NOT NULL,
  consumer_org_id    TEXT NOT NULL,
  consumer_cert_id   UUID NOT NULL,
  platform_org_id    TEXT NOT NULL,                  -- 数据空间运营方（见证方）

  -- 数据产品
  product_ids        TEXT[] NOT NULL,                -- 一合约可含多产品
  product_versions   JSONB NOT NULL,                 -- {product_id: version}

  -- 沙箱约束
  min_sandbox_level  sandbox_level NOT NULL DEFAULT 'L1',
  allowed_sandbox_modes  TEXT[] NOT NULL,            -- 见 §2.2 沙箱模式枚举
  max_concurrent_sessions INT DEFAULT 3,
  session_max_hours  INT DEFAULT 8,

  -- 时间约束
  valid_from         TIMESTAMPTZ NOT NULL,
  valid_until        TIMESTAMPTZ NOT NULL,
  auto_renew         BOOLEAN DEFAULT FALSE,

  -- 差分隐私预算
  dp_budget_total    FLOAT,                          -- NULL 表示不限（非结构化场景）
  dp_budget_consumed FLOAT DEFAULT 0,

  -- 输出约束
  max_output_rows    INT DEFAULT 100,
  max_output_bytes   BIGINT DEFAULT 10485760,        -- 10MB
  allowed_output_formats  TEXT[] NOT NULL,

  -- 签名
  provider_signature TEXT,                           -- SM2 签名（对合约 SM3 哈希）
  consumer_signature TEXT,
  platform_signature TEXT,
  signed_at          TIMESTAMPTZ,

  -- 链上存证
  blockchain_tx_id   TEXT,                           -- FISCO BCOS 交易哈希
  blockchain_block   BIGINT,

  created_at         TIMESTAMPTZ DEFAULT now(),
  updated_at         TIMESTAMPTZ DEFAULT now(),
  metadata           JSONB DEFAULT '{}'
);
```

### 2.2 沙箱模式枚举

```python
class SandboxMode(str, Enum):
    """
    沙箱模式 — 精简为 6 种核心模式 + 配置参数

    原 13 种模式通过配置参数区分（如 llm_sft vs llm_pretrain 通过 training_type 参数区分）。
    这减少了枚举维护面，同时保持策略差异化能力。
    """
    # 结构化数据场景
    STRUCTURED_QUERY      = "structured_query"       # SQL 查询分析（含 ETL）
    STRUCTURED_MODELING   = "structured_modeling"    # 数据建模/特征工程（含视觉/多模态训练）
    STRUCTURED_APP        = "structured_app"         # 应用在沙箱内使用 DB

    # 非结构化数据场景
    LLM_TRAINING          = "llm_training"           # LLM 训练（SFT/PT/RLHF，通过 training_type 参数区分）

    # 数商特殊场景
    PRODUCT_DEVELOPMENT   = "product_dev"            # 数商开发数据产品（含 API 服务）

    # 多方协作
    JOINT_FEDERATED       = "joint_federated"        # 联邦学习多方（含跨空间协作）
```

**配置参数**（通过 `contract.policy_params` JSON 字段传递）：
- `training_type`: "sft" | "pt" | "rlhf" | "vision" | "multimodal"（仅 LLM_TRAINING 模式）
- `etl_format`: "json" | "csv" | "parquet"（仅 STRUCTURED_QUERY 模式）
- `app_type`: "api_service" | "streaming" | "batch"（仅 STRUCTURED_APP 模式）
- `sample_limit`: int（PRODUCT_DEVELOPMENT 模式，默认 1000 行）
```

### 2.3 策略包（PolicyBundle）

```sql
CREATE TABLE policy_bundles (
  bundle_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  contract_id        TEXT NOT NULL REFERENCES contracts(contract_id),
  bundle_version     INT NOT NULL DEFAULT 1,
  sandbox_mode       TEXT NOT NULL,

  -- 编译后的策略（REGO / XACML 字节码）
  compiled_policy    BYTEA NOT NULL,                 -- 加密存储
  policy_hash_sm3    CHAR(64) NOT NULL,              -- 完整性校验，下发至 TEE 验证

  -- 操作白名单（运行时快速检查用）
  allowed_ops        TEXT[] NOT NULL,
  forbidden_ops      TEXT[] NOT NULL,

  -- 字段级访问控制
  field_acl          JSONB NOT NULL,
  -- {field_name: {read: bool, aggregate_only: bool, mask_pattern: str|null}}

  -- 计算资源限制
  max_cpu_cores      INT DEFAULT 8,
  max_memory_gb      INT DEFAULT 16,
  max_gpu_count      INT DEFAULT 0,
  max_runtime_s      INT DEFAULT 3600,

  -- 网络控制
  allow_outbound_net BOOLEAN DEFAULT FALSE,
  allowed_endpoints  TEXT[],                         -- 白名单出口（如模型仓库）

  created_at         TIMESTAMPTZ DEFAULT now(),
  UNIQUE (contract_id, sandbox_mode, bundle_version)
);
```

### 2.4 DP 预算账本

```sql
CREATE TABLE dp_budget_ledger (
  ledger_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  contract_id        TEXT NOT NULL REFERENCES contracts(contract_id),
  session_id         TEXT,
  task_id            TEXT,
  epsilon_allocated  FLOAT NOT NULL,                 -- 本次分配的 ε
  epsilon_consumed   FLOAT NOT NULL,                 -- 实际消耗（含噪声注入）
  delta_consumed     FLOAT DEFAULT 0,                -- (ε,δ)-DP 的 δ 分量
  query_type         TEXT NOT NULL,                  -- count|sum|avg|histogram|model_release
  sensitivity        FLOAT NOT NULL,                 -- 全局敏感度 Δf
  noise_mechanism    TEXT NOT NULL,                  -- laplace|gaussian|exponential
  created_at         TIMESTAMPTZ DEFAULT now()
);

-- 实时预算视图（供策略引擎查询）
CREATE MATERIALIZED VIEW dp_budget_status AS
SELECT
  c.contract_id,
  c.dp_budget_total,
  COALESCE(SUM(l.epsilon_consumed), 0) AS dp_budget_consumed,
  c.dp_budget_total - COALESCE(SUM(l.epsilon_consumed), 0) AS dp_budget_remaining
FROM contracts c
LEFT JOIN dp_budget_ledger l ON c.contract_id = l.contract_id
GROUP BY c.contract_id, c.dp_budget_total;

-- 每次 task 完成后刷新（非实时，有 ≤1s 延迟）
CREATE UNIQUE INDEX ON dp_budget_status(contract_id);
```

---

## 3. 合约状态机

```
DRAFT ──[数商创建]──────────────────────────────────────┐
  │                                                      │
  │ 提交协商                                              │
  ▼                                                      │
NEGOTIATING ──[条款修改，循环]──→ NEGOTIATING            │
  │                                                      │
  │ 双方确认条款                                          │
  ▼                                                      │
PENDING_SIGNATURE                                        │
  │                                                      │
  │ 数商 SM2 签名 + 买方 SM2 签名                         │
  ▼                                                      │
SIGNED                                                   │
  │                                                      │
  │ 平台见证签名 + 链上备案（FISCO BCOS TX）               │
  ▼                                                      │
ACTIVE ──[valid_from 到达]── 已激活，可创建沙箱会话        │
  │                                                      │
  ├── [valid_until 到达] ──────────────────→ COMPLETED   │
  ├── [DP 预算耗尽] ────────────────────────→ SUSPENDED   │
  │       └── [续约申请] ──────────────────→ ACTIVE      │
  ├── [违约检测] ───────────────────────────→ VIOLATED    │
  │       └── [仲裁完成] ──────────────────→ ARCHIVED    │
  └── [主动终止] ───────────────────────────→ ARCHIVED   │
                                                         │
DRAFT ←──────────────────────────────────────────────────┘
       [任意阶段：删除草稿]
```

---

## 4. 策略编译器

### 4.1 合约条款 → 策略包编译流程

```python
class PolicyCompiler:
    """
    将合约条款编译为可在 TEE 内执行的策略包
    输出：加密的 REGO 策略字节码 + 元数据
    """

    def compile(self, contract: Contract, sandbox_mode: SandboxMode) -> PolicyBundle:
        # Phase 1: 提取约束条款
        constraints = self._extract_constraints(contract, sandbox_mode)

        # Phase 2: 生成 REGO 策略
        rego_policy = self._generate_rego(constraints, sandbox_mode)

        # Phase 3: OPA 编译为字节码（Plan IR）
        bytecode = opa_build(rego_policy, optimize=True)

        # Phase 4: SM3 哈希 + SM4 加密（用合约 DEK 保护）
        policy_hash = sm3_hash(bytecode).hex()
        encrypted = sm4_gcm_encrypt(bytecode, contract_policy_key)

        return PolicyBundle(
            contract_id=contract.contract_id,
            sandbox_mode=sandbox_mode,
            compiled_policy=encrypted,
            policy_hash_sm3=policy_hash,
            allowed_ops=constraints.allowed_ops,
            forbidden_ops=constraints.forbidden_ops,
            field_acl=constraints.field_acl,
            **constraints.resource_limits,
        )

    def _generate_rego(self, c: Constraints, mode: SandboxMode) -> str:
        """根据沙箱模式生成特定 REGO 策略（SPEC-5：6 核心模式）"""
        if mode == SandboxMode.STRUCTURED_QUERY:
            return self._rego_structured_query(c)
        elif mode == SandboxMode.STRUCTURED_MODELING:
            return self._rego_model_training(c)
        elif mode == SandboxMode.PRODUCT_DEVELOPMENT:
            return self._rego_product_dev(c)
        elif mode == SandboxMode.LLM_TRAINING:
            return self._rego_ai_training(c, mode)
        elif mode == SandboxMode.STRUCTURED_APP:
            return self._rego_app(c)
        elif mode == SandboxMode.JOINT_FEDERATED:
            return self._rego_federated(c)
```

### 4.2 各场景策略模板

#### 4.2.1 结构化查询策略（REGO）

```rego
package cds.policy.structured_query

import future.keywords.in

# 默认拒绝
default allow = false

# 允许聚合查询（满足所有条件）
allow {
    input.action.type in {"SELECT_AGGREGATE", "COUNT", "SUM", "AVG", "HISTOGRAM"}
    not input.action.returns_row_data         # 禁止返回行级数据
    input.action.output_rows <= data.contract.max_output_rows
    within_time_window
    within_dp_budget
}

# 允许特征工程操作（数据建模模式专用）
allow {
    input.contract.sandbox_mode == "structured_modeling"
    input.action.type in {"FEATURE_EXTRACT", "TRANSFORM", "JOIN_INTERNAL"}
    # JOIN 仅允许沙箱内部数据集之间 JOIN，禁止与外部数据 JOIN
    not input.action.involves_external_join
}

# 禁止所有导出操作
deny_export {
    input.action.type in {
        "SELECT_STAR", "EXPORT_CSV", "COPY_OUT",
        "CREATE_EXTERNAL_TABLE", "WRITE_FILE"
    }
}

# 高敏感字段保护：这些字段只能以聚合形式出现
sensitive_field_violation {
    field := input.action.accessed_fields[_]
    field in data.contract.sensitive_fields
    not input.action.is_aggregate_only
}

# 时间窗口检查
within_time_window {
    now := time.now_ns() / 1000000000
    now >= data.contract.valid_from_unix
    now <= data.contract.valid_until_unix
}

# DP 预算检查（从外部注入当前消耗值）
within_dp_budget {
    input.dp_budget_consumed + input.action.estimated_epsilon
        <= data.contract.dp_budget_total
}
```

#### 4.2.2 AI 大模型训练策略（REGO）

```rego
package cds.policy.ai_training

import future.keywords.in

default allow = false
default allow_gradient_output = false
default allow_model_export = false

# 允许训练操作
allow {
    input.action.type in {
        "FORWARD_PASS", "BACKWARD_PASS", "OPTIMIZER_STEP",
        "DATA_LOADER_BATCH", "TOKENIZE", "EMBED"
    }
    valid_model_architecture
    within_resource_limits
    within_time_window
}

# 模型架构白名单（防止通过超大模型间接记忆数据）
valid_model_architecture {
    arch := input.action.model_config.architecture
    arch in data.contract.allowed_architectures  -- e.g. ["llama3","qwen2","internvl2"]
    input.action.model_config.param_count_b <= data.contract.max_model_params_b
}

# 梯度输出：仅允许聚合梯度（联邦学习场景），禁止原始梯度导出
allow_gradient_output {
    input.contract.sandbox_mode == "joint_federated"
    input.action.gradient_type == "aggregated_clipped"
    input.action.gradient_noise_applied == true   # 已注入 DP 噪声
    input.action.clip_norm <= data.contract.max_clip_norm
}

# 模型导出：训练完成后仅允许导出模型权重文件（不含训练数据）
allow_model_export {
    input.action.type == "EXPORT_MODEL_WEIGHTS"
    input.action.format in {"safetensors", "gguf", "onnx"}
    not model_can_reconstruct_training_data     # 模型记忆性检测通过
    output_watermark_applied                     # 已注入模型水印
}

# 模型记忆性检测（训练结束后由审查网关调用）
model_can_reconstruct_training_data {
    input.action.memorization_score > data.contract.max_memorization_threshold
    # memorization_score 由 SS-05 输出审查网关的 MIA 探针计算
}
```

#### 4.2.3 数商开发沙箱策略（REGO）

```rego
package cds.policy.product_development

# 开发沙箱：数商在自己的数据上开发数据产品
# 特点：权限更高（可读行级数据），但输出严格受控（只能导出产品包，不能导出原始数据）

default allow = false

# 数据读取：数商开发者可以读取自己产品的原始行数据（用于开发调试）
allow {
    input.action.type in {"SELECT", "DESCRIBE", "SAMPLE", "PROFILE"}
    is_product_owner                             # 操作者是该产品的数商
    sample_within_limit                          # 采样量限制（不允许全量导出）
}

# 脚本执行：允许数商编写数据清洗/脱敏脚本
allow {
    input.action.type in {
        "EXECUTE_TRANSFORM", "EXECUTE_MASKING",
        "DEFINE_SCHEMA", "CREATE_DERIVED_DATASET"
    }
    is_product_owner
}

# 开发沙箱特殊限制：所有数据操作结果只能写入沙箱内部存储
# 不允许任何数据离开沙箱，只允许导出"数据产品包"（元数据+加密策略）
allow_output {
    input.action.type == "EXPORT_PRODUCT_PACKAGE"
    input.action.export_content == "product_manifest_only"  # 只有元数据/策略，不含数据
    is_product_owner
}

# 禁止：开发过程中导出任何数据内容
deny_data_export {
    input.action.type in {"EXPORT_CSV", "EXPORT_JSON", "DOWNLOAD_FILE"}
}

is_product_owner {
    input.actor.org_id == data.contract.provider_org_id
}

sample_within_limit {
    input.action.row_limit <= 1000   # 开发调试最多采样 1000 行
}
```

---

## 5. 运行时策略执行器（TEE 内）

### 5.1 PDP（策略决策点）实现

> **设计决策**：PDP 在普通进程中运行，而非 TEE 内。理由：(1) OPA 是纯计算引擎，无密钥材料需要保护，(2) TEE 内运行增加启动延迟和 EPC 占用，(3) 策略包通过 SM3 哈希验证完整性即可保证不被篡改。

```python
class PolicyDecisionPoint:
    """
    策略决策点 — 在普通进程中运行
    策略包在初始化时加载，验证 SM3 哈希后执行
    策略包加密传输保证完整性（传输层 TLS + SM3 校验）
    """

    def __init__(self, bundle: PolicyBundle, contract_meta: ContractMeta):
        # 验证策略包完整性（防止 TEE 外篡改后传入）
        computed_hash = sm3_hash(bundle.compiled_policy).hex()
        if computed_hash != bundle.policy_hash_sm3:
            raise PolicyTampered("Policy bundle hash mismatch")

        self.opa = OPARuntime(bundle.compiled_policy)
        self.contract = contract_meta
        self.quota_client = QuotaClient()           # 与 TEE 外配额服务安全通信
        self.dp_budget = DPBudgetTracker(contract_meta.dp_budget_total)

    def evaluate(self, request: PolicyRequest) -> PolicyDecision:
        """
        评估一个操作请求
        调用路径：每次数据访问操作 → PolicyEnforcementPoint → evaluate()
        """
        # 构造 OPA 输入（注入当前状态）
        opa_input = {
            "action": request.to_dict(),
            "actor": {
                "org_id": self.contract.consumer_org_id,
                "session_id": request.session_id,
            },
            "dp_budget_consumed": self.dp_budget.consumed,
            "contract": self.contract.to_policy_data(),
            "env": {
                "now_unix": int(time.time()),
            }
        }

        # OPA 评估
        result = self.opa.query("data.cds.policy.allow", opa_input)
        deny_reasons = self.opa.query("data.cds.policy.deny_reasons", opa_input)

        if not result:
            self._emit_deny_event(request, deny_reasons)
            return PolicyDecision.DENY(reasons=deny_reasons)

        # 配额检查（原子操作，防竞态）
        quota_ok = self.quota_client.check_and_consume(
            session_id=request.session_id,
            operation=request.action_type,
            count=request.estimated_resource_units
        )
        if not quota_ok:
            return PolicyDecision.DENY(reasons=["quota_exceeded"])

        self._emit_allow_event(request)
        return PolicyDecision.ALLOW(
            dp_epsilon_to_charge=request.estimated_epsilon,
        )

    def charge_dp_budget(self, epsilon: float, delta: float = 0):
        """任务完成后扣减 DP 预算（由输出审查网关调用）"""
        self.dp_budget.consume(epsilon, delta)
        # 同步至外部账本（通过签名请求，防止篡改）
        signed_update = sm2_sign(
            f"{self.contract.contract_id}:{epsilon}:{self.dp_budget.consumed}",
            self.enclave_private_key
        )
        budget_service.record_consumption(signed_update)
```

### 5.2 配额管理（Quota Manager）

```python
class QuotaManager:
    """
    每个合约+会话维护独立配额计数器（Redis，Lua 原子操作）
    配额类型：行数 / 字节数 / API 调用次数 / 任务次数 / GPU 时（训练场景）
    """

    QUOTA_KEYS = {
        "output_rows":    "q:{contract}:{session}:rows",
        "output_bytes":   "q:{contract}:{session}:bytes",
        "task_count":     "q:{contract}:tasks",       # 合约级别（不含 session）
        "gpu_hours":      "q:{contract}:gpu_h",       # 训练任务专用
        "api_calls":      "q:{contract}:{session}:api",
    }

    CHECK_AND_CONSUME_LUA = """
    local current = redis.call('GET', KEYS[1])
    if current == false then
        return {-1, "quota_not_initialized"}
    end
    local c = tonumber(current)
    local limit = tonumber(ARGV[1])
    local cost = tonumber(ARGV[2])
    if limit > 0 and c + cost > limit then
        return {0, "quota_exceeded:" .. c .. "/" .. limit}
    end
    redis.call('INCRBY', KEYS[1], cost)
    return {1, tostring(c + cost)}
    """

    def check_and_consume(
        self, contract_id: str, session_id: str,
        quota_type: str, cost: int = 1
    ) -> tuple[bool, str]:
        key_tpl = self.QUOTA_KEYS[quota_type]
        key = key_tpl.format(contract=contract_id, session=session_id)
        limit = self._get_limit(contract_id, quota_type)
        result, msg = redis.eval(self.CHECK_AND_CONSUME_LUA, 1, key, limit, cost)
        return result == 1, msg

    def init_session_quotas(self, contract: Contract, session_id: str):
        """会话创建时初始化所有配额计数器"""
        for quota_type, key_tpl in self.QUOTA_KEYS.items():
            key = key_tpl.format(contract=contract.contract_id, session=session_id)
            limit = self._get_limit(contract.contract_id, quota_type)
            redis.set(key, 0, ex=contract.session_max_hours * 3600)
```

---

## 6. 合约链上存证

```python
class ContractBlockchainService:
    """
    合约在 FISCO BCOS 上备案存证
    存证内容：合约摘要（不含原文），双方签名，时间戳
    """

    def submit_contract(self, contract: Contract) -> BlockchainReceipt:
        # 构造存证数据（不上传原文，保护商业机密）
        on_chain_data = {
            "contract_id": contract.contract_id,
            "contract_hash": sm3_hash(contract.to_canonical_json()).hex(),
            "provider_cert_fingerprint": contract.provider_cert_fingerprint,
            "consumer_cert_fingerprint": contract.consumer_cert_fingerprint,
            "product_ids": contract.product_ids,
            "valid_from": contract.valid_from.isoformat(),
            "valid_until": contract.valid_until.isoformat(),
            "contract_type": contract.contract_type,
            "sandbox_modes": contract.allowed_sandbox_modes,
            "provider_signature": contract.provider_signature,
            "consumer_signature": contract.consumer_signature,
        }

        # 调用 FISCO BCOS 合约
        tx_hash = fisco_client.call_contract(
            contract_address=CONTRACT_REGISTRY_ADDRESS,
            method="registerContract",
            args=[json.dumps(on_chain_data)],
            signer_key=PLATFORM_PRIVATE_KEY
        )
        receipt = fisco_client.wait_for_receipt(tx_hash, timeout=30)

        # 更新本地状态
        db.update(Contract, contract.contract_id,
                  blockchain_tx_id=tx_hash,
                  blockchain_block=receipt.block_number,
                  status='active')

        return BlockchainReceipt(tx_hash=tx_hash, block=receipt.block_number)
```

---

## 7. 合约服务对外 API

```python
# REST API（挂载于 API 网关，TLCP 加密）

# POST /api/v1/contracts
# 创建合约草稿（数商发起）
class CreateContractRequest(BaseModel):
    contract_type: ContractType
    consumer_org_id: str
    product_ids: list[str]
    sandbox_modes: list[SandboxMode]
    min_sandbox_level: SandboxLevel
    valid_from: datetime
    valid_until: datetime
    dp_budget_total: Optional[float]
    max_output_rows: int
    custom_policy_clauses: Optional[dict]    # 数商自定义扩展条款

# POST /api/v1/contracts/{id}/negotiate
# 买方提出修改（协商阶段，循环）
class NegotiateRequest(BaseModel):
    proposed_changes: dict                   # 提议修改的字段
    message: str                             # 协商备注

# POST /api/v1/contracts/{id}/sign
# 签署（买方 / 数商各自调用）
class SignContractRequest(BaseModel):
    signer_role: Literal["provider", "consumer"]
    signature: str                           # SM2 签名（对合约 SM3 哈希）
    cert_pem: str                            # 签名方 SM2 证书

# GET /api/v1/contracts/{id}/policy-bundle?mode={sandbox_mode}
# 获取策略包（任务调度器在创建会话时调用）
# 返回：加密的 PolicyBundle
```

---

*文档：SS-02 | 版本：v1.0*
