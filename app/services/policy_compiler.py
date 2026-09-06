"""Policy Compiler — compiles contract terms into OPA Rego policy bundles.

Supports 4 scenario-specific Rego templates (P1-1):
- structured_query: field ACL, row limits, DP budget
- llm_training: GPU quota, epoch limits, model output safety
- product_dev: output format restrictions, no raw data leakage
- structured_app: API rate limits, response streaming controls
"""
import json
from dataclasses import dataclass, field

from app.utils.crypto import SM4Cipher, sm3_hash


@dataclass
class PolicyBundle:
    contract_id: str
    rego_source: str
    rules: dict
    metadata: dict = field(default_factory=dict)
    sm3_hash: str | None = None
    encrypted_source: bytes | None = None


class PolicyCompiler:
    """Compiles contract terms into executable OPA Rego policies."""

    def compile(self, contract: dict) -> PolicyBundle:
        """Compile contract terms into a Rego policy bundle."""
        contract_id = contract.get("contract_id", "unknown")
        sandbox_levels = contract.get("allowed_sandbox_levels", ["L3"])
        operations = contract.get("allowed_operations", ["read"])
        max_duration = contract.get("max_duration_seconds", 3600)
        dp_epsilon = contract.get("dp_epsilon_budget")

        rego = self._generate_rego(contract_id, sandbox_levels, operations, max_duration, dp_epsilon)

        rules = {
            "sandbox_levels": sandbox_levels,
            "operations": operations,
            "max_duration": max_duration,
            "dp_epsilon": dp_epsilon,
            "status": contract.get("status", "draft"),
        }

        return PolicyBundle(
            contract_id=contract_id,
            rego_source=rego,
            rules=rules,
            sm3_hash=sm3_hash(rego.encode()),
            metadata={"version": "1.1", "compiler": "cds-policy-compiler"},
        )

    def compile_encrypted(self, contract: dict, cipher: SM4Cipher) -> PolicyBundle:
        """Compile and SM4-GCM encrypt the Rego source."""
        bundle = self.compile(contract)
        ciphertext, nonce, tag = cipher.encrypt_gcm(bundle.rego_source.encode())
        bundle.encrypted_source = nonce + tag + ciphertext
        return bundle

    def compile_for_mode(self, contract: dict, sandbox_mode: str) -> PolicyBundle:
        """Compile contract terms into a mode-specific Rego policy bundle (P1-1)."""
        contract_id = contract.get("contract_id", "unknown")
        mode = sandbox_mode.lower()

        if mode == "structured_query":
            rego = self._generate_query_rego(contract_id, contract)
        elif mode == "llm_training":
            rego = self._generate_training_rego(contract_id, contract)
        elif mode == "product_dev":
            rego = self._generate_product_dev_rego(contract_id, contract)
        elif mode == "structured_app":
            rego = self._generate_app_rego(contract_id, contract)
        else:
            rego = self._generate_rego(
                contract_id,
                contract.get("allowed_sandbox_levels", ["L3"]),
                contract.get("allowed_operations", ["read"]),
                contract.get("max_duration_seconds", 3600),
                contract.get("dp_epsilon_budget"),
            )

        rules = {
            "sandbox_mode": mode,
            "sandbox_levels": contract.get("allowed_sandbox_levels", ["L3"]),
            "operations": contract.get("allowed_operations", ["read"]),
            "max_duration": contract.get("max_duration_seconds", 3600),
            "dp_epsilon": contract.get("dp_epsilon_budget"),
            "status": contract.get("status", "draft"),
        }

        return PolicyBundle(
            contract_id=contract_id,
            rego_source=rego,
            rules=rules,
            sm3_hash=sm3_hash(rego.encode()),
            metadata={"version": "1.2", "compiler": "cds-policy-compiler", "mode": mode},
        )

    def _generate_rego(self, contract_id: str, levels: list, operations: list, max_duration: int, dp_epsilon: float | None) -> str:
        """Generate generic OPA Rego source with AND-combined conditions."""
        levels_str = json.dumps(levels)
        ops_str = json.dumps(operations)
        dp_check = f"\n    input.dp_epsilon <= {dp_epsilon}" if dp_epsilon else ""

        return f"""package cds.policy.{contract_id.replace("-", "_")}

default allow = false

allow {{
    input.sandbox_level in {levels_str}
    input.operation in {ops_str}
    input.duration_seconds <= {max_duration}{dp_check}
}}"""

    def _generate_query_rego(self, contract_id: str, contract: dict) -> str:
        """Structured query mode: field ACL, row limits, DP budget enforcement."""
        levels = json.dumps(contract.get("allowed_sandbox_levels", ["L3"]))
        allowed_fields = json.dumps(contract.get("allowed_fields", ["*"]))
        max_rows = contract.get("max_output_rows", 10000)
        dp_epsilon = contract.get("dp_epsilon_budget")
        dp_check = f"\n    input.dp_epsilon_remaining >= {dp_epsilon}" if dp_epsilon else ""

        return f"""package cds.policy.{contract_id.replace("-", "_")}.query

default allow = false

allow {{
    input.sandbox_level in {levels}
    input.operation == "query"
    input.row_count <= {max_rows}
    field_allowed(input.field){dp_check}
}}

field_allowed(field) {{
    "*" in {allowed_fields}
}}

field_allowed(field) {{
    field in {allowed_fields}
}}"""

    def _generate_training_rego(self, contract_id: str, contract: dict) -> str:
        """LLM/model training mode: GPU quota, epoch limits, output format."""
        levels = json.dumps(contract.get("allowed_sandbox_levels", ["L1", "L2"]))
        max_gpu_hours = contract.get("max_gpu_hours", 100)
        max_epochs = contract.get("max_epochs", 100)
        allowed_formats = json.dumps(contract.get("allowed_output_formats", ["safetensors", "onnx"]))

        return f"""package cds.policy.{contract_id.replace("-", "_")}.training

default allow = false

allow {{
    input.sandbox_level in {levels}
    input.operation == "train"
    input.gpu_hours <= {max_gpu_hours}
    input.epochs <= {max_epochs}
    input.output_format in {allowed_formats}
}}

allow {{
    input.sandbox_level in {levels}
    input.operation == "evaluate"
    input.output_format in {allowed_formats}
}}"""

    def _generate_product_dev_rego(self, contract_id: str, contract: dict) -> str:
        """Product development mode: output format restrictions, no raw data."""
        levels = json.dumps(contract.get("allowed_sandbox_levels", ["L3"]))
        allowed_formats = json.dumps(contract.get("allowed_output_formats", ["zip", "tar.gz"]))

        return f"""package cds.policy.{contract_id.replace("-", "_")}.product_dev

default allow = false

allow {{
    input.sandbox_level in {levels}
    input.operation in ["build", "package", "test"]
    input.output_format in {allowed_formats}
    not input.contains_raw_data
}}"""

    def _generate_app_rego(self, contract_id: str, contract: dict) -> str:
        """Application mode: API rate limits, response size limits."""
        levels = json.dumps(contract.get("allowed_sandbox_levels", ["L3"]))
        max_rps = contract.get("max_requests_per_second", 100)
        max_response_mb = contract.get("max_response_mb", 10)

        return f"""package cds.policy.{contract_id.replace("-", "_")}.app

default allow = false

allow {{
    input.sandbox_level in {levels}
    input.operation == "api_call"
    input.requests_per_second <= {max_rps}
    input.response_size_mb <= {max_response_mb}
}}

allow {{
    input.sandbox_level in {levels}
    input.operation == "serve"
    input.requests_per_second <= {max_rps}
}}"""

    def field_rules_from_classifications(self, classifications: dict | None) -> dict:
        """Gap A5/T7: map field-level classifications to output policy rules.

        Level semantics (1=public, 2=internal, 3=confidential, 4=secret):
        - level >= 4 → deny_out (field must never leave the sandbox)
        - level == 3 → mask (field is force-masked on any output)
        - level <= 2 → no restriction

        Returns {"mask_fields": [...], "deny_out_fields": [...]}.
        """
        if not classifications:
            return {"mask_fields": [], "deny_out_fields": []}
        mask_fields: list[str] = []
        deny_out_fields: list[str] = []
        for field, raw_level in classifications.items():
            try:
                level = int(raw_level)
            except (TypeError, ValueError):
                continue
            if level >= 4:
                deny_out_fields.append(str(field))
            elif level == 3:
                mask_fields.append(str(field))
        return {"mask_fields": mask_fields, "deny_out_fields": deny_out_fields}

    def verify_integrity(self, bundle: PolicyBundle) -> bool:
        """Verify the SM3 hash of a policy bundle matches its rego_source."""
        if not bundle.sm3_hash or not bundle.rego_source:
            return False
        return sm3_hash(bundle.rego_source.encode()) == bundle.sm3_hash

    def decrypt_source(self, encrypted_source: bytes, cipher: SM4Cipher) -> str:
        """Decrypt an encrypted policy bundle back to Rego source."""
        nonce = encrypted_source[:12]
        tag = encrypted_source[12:28]
        ciphertext = encrypted_source[28:]
        return cipher.decrypt_gcm(ciphertext, nonce, tag).decode()


class PolicyEvaluator:
    """Runtime policy evaluation (PDP — Policy Decision Point)."""

    def evaluate(self, policy: PolicyBundle, request: dict) -> dict:
        """Evaluate a request against a policy bundle.
        Returns {allowed: bool, reason: str}.
        """
        rules = policy.rules

        if rules.get("status") not in ("active", "signed"):
            return {"allowed": False, "reason": "Contract not active"}

        level = request.get("sandbox_level", "L3")
        if level not in rules.get("sandbox_levels", []):
            return {"allowed": False, "reason": f"Sandbox level {level} not permitted"}

        operation = request.get("operation", "read")
        if operation not in rules.get("operations", []):
            return {"allowed": False, "reason": f"Operation {operation} not permitted"}

        duration = request.get("duration_seconds", 0)
        if duration > rules.get("max_duration", 3600):
            return {"allowed": False, "reason": "Duration exceeds limit"}

        dp_epsilon = rules.get("dp_epsilon")
        if dp_epsilon is not None:
            requested_dp = request.get("dp_epsilon", 0)
            if requested_dp > dp_epsilon:
                return {"allowed": False, "reason": "DP epsilon budget exceeded"}

        return {"allowed": True, "reason": "All checks passed"}


policy_compiler = PolicyCompiler()
policy_evaluator = PolicyEvaluator()
