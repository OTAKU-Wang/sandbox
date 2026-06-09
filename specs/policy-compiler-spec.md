# 密态沙箱系统（CDS）· 策略编译器技术规格

> 版本：v1.0
> 作者：@架构师
> 日期：2026-06-03
> 模块：SS-02 策略编译器
> 上游：合约域（Contract Domain）
> 下游：策略执行器（Policy Decision Point）

---

## 1. 概述

策略编译器是 CDS 系统的核心组件，负责将数字合约中的自然语言条款编译为可在 TEE 内执行的机器可读策略包（PolicyBundle）。

### 1.1 设计原则

- **策略即代码**：合约条款 → OPA Rego 策略 → 字节码
- **最小权限**：默认拒绝，显式允许
- **可审计**：策略哈希（SM3）+ 版本管理
- **高性能**：OPA 预编译字节码，TEE 内 μs 级评估

### 1.2 参考实现

- [Open Policy Agent (OPA)](https://www.openpolicyagent.org/) — CNCF 毕业项目
- [OPA Gatekeeper](https://open-policy-agent.github.io/gatekeeper/) — K8s 准入控制
- SS-02 数字合约技术规格

---

## 2. 策略编译流程

```
合约条款 (ContractTerms)
    │
    ▼
┌─────────────────┐
│ Phase 1: 提取约束 │  解析 allowedOps, forbiddenOps, fieldACL, resourceLimits
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Phase 2: 生成Rego │  根据 SandboxMode 选择模板，填充约束参数
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Phase 3: OPA编译  │  opa build --optimize → Plan IR 字节码
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Phase 4: 加密签名 │  SM3 哈希 + SM4-GCM 加密（合约 DEK 保护）
└────────┬────────┘
         │
         ▼
    PolicyBundle
```

---

## 3. 策略模板设计

### 3.1 结构化查询策略（structured_query）

```rego
package cds.policy.structured_query

import future.keywords.in

default allow = false

# 允许聚合查询
allow {
    input.action.type in {"SELECT_AGGREGATE", "COUNT", "SUM", "AVG", "HISTOGRAM"}
    not input.action.returns_row_data
    input.action.output_rows <= data.contract.max_output_rows
    within_time_window
    within_dp_budget
}

# 高敏感字段保护
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

# DP 预算检查
within_dp_budget {
    input.dp_budget_consumed + input.action.estimated_epsilon
        <= data.contract.dp_budget_total
}
```

### 3.2 AI 训练策略（ai_training）

```rego
package cds.policy.ai_training

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
}

# 模型架构白名单
valid_model_architecture {
    arch := input.action.model_config.architecture
    arch in data.contract.allowed_architectures
    input.action.model_config.param_count_b <= data.contract.max_model_params_b
}

# 梯度输出（联邦学习场景）
allow_gradient_output {
    input.contract.sandbox_mode == "joint_federated"
    input.action.gradient_type == "aggregated_clipped"
    input.action.gradient_noise_applied == true
}

# 模型导出
allow_model_export {
    input.action.type == "EXPORT_MODEL_WEIGHTS"
    input.action.format in {"safetensors", "gguf", "onnx"}
    not model_can_reconstruct_training_data
    output_watermark_applied
}
```

### 3.3 数商开发沙箱策略（product_development）

```rego
package cds.policy.product_development

default allow = false

# 数据读取（数商可以读取自己产品的原始数据）
allow {
    input.action.type in {"SELECT", "DESCRIBE", "SAMPLE", "PROFILE"}
    is_product_owner
    sample_within_limit
}

# 脚本执行
allow {
    input.action.type in {
        "EXECUTE_TRANSFORM", "EXECUTE_MASKING",
        "DEFINE_SCHEMA", "CREATE_DERIVED_DATASET"
    }
    is_product_owner
}

# 产品包导出（仅元数据，不含数据）
allow_output {
    input.action.type == "EXPORT_PRODUCT_PACKAGE"
    input.action.export_content == "product_manifest_only"
    is_product_owner
}

# 禁止数据导出
deny_data_export {
    input.action.type in {"EXPORT_CSV", "EXPORT_JSON", "DOWNLOAD_FILE"}
}

is_product_owner {
    input.actor.org_id == data.contract.provider_org_id
}

sample_within_limit {
    input.action.row_limit <= 1000
}
```

---

## 4. 策略编译器实现

```python
from enum import Enum
from typing import Optional
import hashlib

class SandboxMode(str, Enum):
    STRUCTURED_QUERY = "structured_query"
    STRUCTURED_MODELING = "structured_modeling"
    STRUCTURED_APP = "structured_app"
    UNSTRUCTURED_LLM_SFT = "unstructured_llm_sft"
    UNSTRUCTURED_LLM_PT = "unstructured_llm_pt"
    UNSTRUCTURED_VISION = "unstructured_vision"
    UNSTRUCTURED_MULTIMODAL = "unstructured_multimodal"
    UNSTRUCTURED_ANALYSIS = "unstructured_analysis"
    SEMI_STRUCTURED_ETL = "semi_structured_etl"
    PRODUCT_DEVELOPMENT = "product_dev"
    API_SERVICE = "api_service"
    JOINT_FEDERATED = "joint_federated"


class PolicyCompiler:
    """将合约条款编译为可在 TEE 内执行的策略包"""

    def __init__(self, opa_binary: str = "opa"):
        self.opa_binary = opa_binary
        self.templates = self._load_templates()

    def compile(self, contract: dict, sandbox_mode: SandboxMode) -> dict:
        """编译合约条款为策略包"""
        # Phase 1: 提取约束
        constraints = self._extract_constraints(contract, sandbox_mode)

        # Phase 2: 生成 Rego
        rego_policy = self._generate_rego(constraints, sandbox_mode)

        # Phase 3: OPA 编译
        bytecode = self._compile_rego(rego_policy)

        # Phase 4: SM3 哈希 + SM4 加密
        policy_hash = hashlib.sha3_256(bytecode).hexdigest()
        encrypted = self._encrypt_policy(bytecode, contract["dek"])

        return {
            "contract_id": contract["contract_id"],
            "sandbox_mode": sandbox_mode.value,
            "compiled_policy": encrypted,
            "policy_hash_sm3": policy_hash,
            "allowed_ops": constraints["allowed_ops"],
            "forbidden_ops": constraints["forbidden_ops"],
            "field_acl": constraints["field_acl"],
            "resource_limits": constraints["resource_limits"],
        }

    def _extract_constraints(self, contract: dict, mode: SandboxMode) -> dict:
        """从合约中提取约束条款"""
        return {
            "allowed_ops": contract.get("allowed_operations", []),
            "forbidden_ops": contract.get("forbidden_operations", []),
            "field_acl": contract.get("field_acl", {}),
            "resource_limits": {
                "max_cpu_cores": contract.get("max_cpu_cores", 8),
                "max_memory_gb": contract.get("max_memory_gb", 16),
                "max_gpu_count": contract.get("max_gpu_count", 0),
                "max_runtime_s": contract.get("max_runtime_s", 3600),
            },
            "dp_budget": contract.get("dp_budget_total"),
            "max_output_rows": contract.get("max_output_rows", 100),
            "valid_from": contract.get("valid_from"),
            "valid_until": contract.get("valid_until"),
        }

    def _generate_rego(self, constraints: dict, mode: SandboxMode) -> str:
        """根据沙箱模式生成 Rego 策略"""
        template = self.templates.get(mode.value)
        if not template:
            raise ValueError(f"Unsupported sandbox mode: {mode.value}")

        # 填充模板参数
        return template.format(
            allowed_ops=constraints["allowed_ops"],
            forbidden_ops=constraints["forbidden_ops"],
            max_output_rows=constraints["max_output_rows"],
            dp_budget_total=constraints["dp_budget"] or "null",
            **constraints["resource_limits"],
        )

    def _compile_rego(self, rego_policy: str) -> bytes:
        """使用 OPA 编译 Rego 为字节码"""
        import subprocess
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".rego", mode="w") as f:
            f.write(rego_policy)
            f.flush()

            result = subprocess.run(
                [self.opa_binary, "build", "--optimize", f.name],
                capture_output=True,
                check=True,
            )
            return result.stdout

    def _encrypt_policy(self, bytecode: bytes, dek: str) -> bytes:
        """使用 SM4-GCM 加密策略包"""
        # 实现 SM4-GCM 加密
        pass

    def _load_templates(self) -> dict:
        """加载策略模板"""
        return {
            "structured_query": STRUCTURED_QUERY_TEMPLATE,
            "structured_modeling": STRUCTURED_MODELING_TEMPLATE,
            "ai_training": AI_TRAINING_TEMPLATE,
            "product_development": PRODUCT_DEVELOPMENT_TEMPLATE,
            # ... 其他模式
        }


# 策略模板常量
STRUCTURED_QUERY_TEMPLATE = '''
package cds.policy.structured_query

import future.keywords.in

default allow = false

allow {{
    input.action.type in {allowed_ops}
    not input.action.returns_row_data
    input.action.output_rows <= {max_output_rows}
    within_time_window
    within_dp_budget
}}

deny {{
    input.action.type in {forbidden_ops}
}}

within_time_window {{
    now := time.now_ns() / 1000000000
    now >= data.contract.valid_from_unix
    now <= data.contract.valid_until_unix
}}

within_dp_budget {{
    input.dp_budget_consumed + input.action.estimated_epsilon
        <= data.contract.dp_budget_total
}}
'''
```

---

## 5. 策略执行器（PDP）

```python
class PolicyDecisionPoint:
    """在 TEE Enclave 内运行的策略决策点"""

    def __init__(self, bundle: dict, contract_meta: dict):
        # 验证策略包完整性
        computed_hash = hashlib.sha3_256(bundle["compiled_policy"]).hexdigest()
        if computed_hash != bundle["policy_hash_sm3"]:
            raise PolicyTampered("Policy bundle hash mismatch")

        self.opa = OPARuntime(bundle["compiled_policy"])
        self.contract = contract_meta
        self.quota_client = QuotaClient()
        self.dp_budget = DPBudgetTracker(contract_meta.get("dp_budget_total"))

    def evaluate(self, request: dict) -> dict:
        """评估一个操作请求"""
        opa_input = {
            "action": request,
            "actor": {
                "org_id": self.contract["consumer_org_id"],
                "session_id": request.get("session_id"),
            },
            "dp_budget_consumed": self.dp_budget.consumed,
            "contract": self.contract,
            "env": {
                "now_unix": int(time.time()),
            }
        }

        # OPA 评估
        result = self.opa.query("data.cds.policy.allow", opa_input)
        deny_reasons = self.opa.query("data.cds.policy.deny_reasons", opa_input)

        if not result:
            return {"decision": "DENY", "reasons": deny_reasons}

        # 配额检查
        quota_ok = self.quota_client.check_and_consume(
            session_id=request.get("session_id"),
            operation=request.get("action_type"),
            count=request.get("estimated_resource_units", 1)
        )
        if not quota_ok:
            return {"decision": "DENY", "reasons": ["quota_exceeded"]}

        return {
            "decision": "ALLOW",
            "dp_epsilon_to_charge": request.get("estimated_epsilon", 0),
        }
```

---

## 6. 配额管理

```python
class QuotaManager:
    """每个合约+会话维护独立配额计数器"""

    QUOTA_KEYS = {
        "output_rows": "q:{contract}:{session}:rows",
        "output_bytes": "q:{contract}:{session}:bytes",
        "task_count": "q:{contract}:tasks",
        "gpu_hours": "q:{contract}:gpu_h",
        "api_calls": "q:{contract}:{session}:api",
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

    def check_and_consume(self, contract_id: str, session_id: str,
                         quota_type: str, cost: int = 1) -> tuple[bool, str]:
        key_tpl = self.QUOTA_KEYS[quota_type]
        key = key_tpl.format(contract=contract_id, session=session_id)
        limit = self._get_limit(contract_id, quota_type)
        result, msg = redis.eval(self.CHECK_AND_CONSUME_LUA, 1, key, limit, cost)
        return result == 1, msg
```

---

## 7. 测试用例

### 7.1 策略编译测试

```python
def test_structured_query_policy_compilation():
    """测试结构化查询策略编译"""
    contract = {
        "contract_id": "CTR-2026-001",
        "allowed_operations": ["SELECT_AGGREGATE", "COUNT", "SUM"],
        "forbidden_operations": ["SELECT_STAR", "EXPORT_CSV"],
        "max_output_rows": 1000,
        "dp_budget_total": 10.0,
        "valid_from": "2026-01-01T00:00:00Z",
        "valid_until": "2026-12-31T23:59:59Z",
    }

    compiler = PolicyCompiler()
    bundle = compiler.compile(contract, SandboxMode.STRUCTURED_QUERY)

    assert bundle["contract_id"] == "CTR-2026-001"
    assert bundle["sandbox_mode"] == "structured_query"
    assert "SELECT_AGGREGATE" in bundle["allowed_ops"]
    assert "SELECT_STAR" in bundle["forbidden_ops"]
    assert len(bundle["policy_hash_sm3"]) == 64  # SM3 哈希长度


def test_policy_integrity_verification():
    """测试策略包完整性验证"""
    bundle = create_test_bundle()

    # 修改策略包内容
    bundle["compiled_policy"] = b"tampered"

    with pytest.raises(PolicyTampered):
        PolicyDecisionPoint(bundle, {})
```

### 7.2 策略执行测试

```python
def test_allow_aggregate_query():
    """测试允许聚合查询"""
    bundle = create_structured_query_bundle()
    pdp = PolicyDecisionPoint(bundle, test_contract_meta)

    request = {
        "action_type": "SELECT_AGGREGATE",
        "returns_row_data": False,
        "output_rows": 100,
        "estimated_epsilon": 0.1,
    }

    result = pdp.evaluate(request)
    assert result["decision"] == "ALLOW"


def test_deny_export_operation():
    """测试拒绝导出操作"""
    bundle = create_structured_query_bundle()
    pdp = PolicyDecisionPoint(bundle, test_contract_meta)

    request = {
        "action_type": "EXPORT_CSV",
        "returns_row_data": True,
    }

    result = pdp.evaluate(request)
    assert result["decision"] == "DENY"
    assert "forbidden_operation" in result["reasons"]
```

---

## 8. 部署配置

### 8.1 OPA Sidecar 部署

```yaml
# docker-compose.yml
services:
  opa:
    image: openpolicyagent/opa:0.68.0
    ports:
      - "8181:8181"
    command:
      - "run"
      - "--server"
      - "--addr=0.0.0.0:8181"
      - "--log-level=info"
    volumes:
      - ./policies:/policies
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8181/health"]
      interval: 10s
      timeout: 5s
      retries: 3
```

### 8.2 K8s 部署（OPA Gatekeeper）

```yaml
apiVersion: config.gatekeeper.sh/v1alpha1
kind: Config
metadata:
  name: config
  namespace: gatekeeper-system
spec:
  sync:
    syncOnly:
      - group: ""
        version: "v1"
        kind: "Namespace"
  match:
    - excludedNamespaces: ["kube-system"]
      processes: ["audit", "webhook"]
```

---

## 9. 监控指标

| 指标 | 说明 | 告警阈值 |
|------|------|---------|
| `policy_compile_duration_ms` | 策略编译耗时 | > 1000ms |
| `policy_eval_duration_us` | 策略评估耗时（TEE内） | > 100μs |
| `policy_eval_total` | 策略评估总次数 | - |
| `policy_eval_denied` | 策略拒绝次数 | > 10% 比率 |
| `quota_exceeded_total` | 配额超限次数 | > 0 |
| `dp_budget_remaining` | DP 预算剩余 | < 20% |
