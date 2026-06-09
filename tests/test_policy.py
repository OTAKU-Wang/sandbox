import pytest
from app.services.policy_compiler import PolicyCompiler, PolicyEvaluator
from app.utils.crypto import SM4Cipher


def test_compile_policy():
    compiler = PolicyCompiler()
    bundle = compiler.compile({
        "contract_id": "test-001",
        "allowed_sandbox_levels": ["L2", "L3"],
        "allowed_operations": ["read", "analyze"],
        "max_duration_seconds": 7200,
        "dp_epsilon_budget": 5.0,
        "status": "active",
    })
    assert bundle.contract_id == "test-001"
    assert "L2" in bundle.rego_source
    assert "L3" in bundle.rego_source
    assert bundle.sm3_hash is not None


def test_compile_rego_and_logic():
    """Rego should use AND logic — all conditions in one allow block."""
    compiler = PolicyCompiler()
    bundle = compiler.compile({
        "contract_id": "test-and",
        "allowed_sandbox_levels": ["L3"],
        "allowed_operations": ["read"],
        "max_duration_seconds": 3600,
        "status": "active",
    })
    # Should have exactly one "allow {" block (not multiple independent ones)
    assert bundle.rego_source.count("allow {") == 1


def test_sm3_hash_integrity():
    compiler = PolicyCompiler()
    bundle = compiler.compile({
        "contract_id": "test-hash",
        "allowed_sandbox_levels": ["L3"],
        "allowed_operations": ["read"],
        "status": "active",
    })
    assert compiler.verify_integrity(bundle) is True

    # Tamper with source
    bundle.rego_source = "tampered"
    assert compiler.verify_integrity(bundle) is False


def test_sm4_encryption_roundtrip():
    compiler = PolicyCompiler()
    cipher = SM4Cipher()
    bundle = compiler.compile_encrypted({
        "contract_id": "test-encrypt",
        "allowed_sandbox_levels": ["L2", "L3"],
        "allowed_operations": ["read", "query"],
        "max_duration_seconds": 3600,
        "status": "active",
    }, cipher)

    assert bundle.encrypted_source is not None
    assert bundle.rego_source is not None

    # Decrypt and verify
    decrypted = compiler.decrypt_source(bundle.encrypted_source, cipher)
    assert decrypted == bundle.rego_source


def test_evaluate_policy_allow():
    compiler = PolicyCompiler()
    evaluator = PolicyEvaluator()
    bundle = compiler.compile({
        "contract_id": "test-002",
        "allowed_sandbox_levels": ["L3"],
        "allowed_operations": ["read"],
        "max_duration_seconds": 3600,
        "status": "active",
    })
    result = evaluator.evaluate(bundle, {"sandbox_level": "L3", "operation": "read", "duration_seconds": 1800})
    assert result["allowed"] is True


def test_evaluate_policy_deny_level():
    compiler = PolicyCompiler()
    evaluator = PolicyEvaluator()
    bundle = compiler.compile({
        "contract_id": "test-003",
        "allowed_sandbox_levels": ["L3"],
        "allowed_operations": ["read"],
        "max_duration_seconds": 3600,
        "status": "active",
    })
    result = evaluator.evaluate(bundle, {"sandbox_level": "L1", "operation": "read", "duration_seconds": 1800})
    assert result["allowed"] is False
    assert "not permitted" in result["reason"]


def test_evaluate_policy_deny_inactive():
    compiler = PolicyCompiler()
    evaluator = PolicyEvaluator()
    bundle = compiler.compile({
        "contract_id": "test-004",
        "allowed_sandbox_levels": ["L3"],
        "allowed_operations": ["read"],
        "status": "draft",
    })
    result = evaluator.evaluate(bundle, {"sandbox_level": "L3", "operation": "read"})
    assert result["allowed"] is False
    assert "not active" in result["reason"]


def test_evaluate_policy_deny_operation():
    compiler = PolicyCompiler()
    evaluator = PolicyEvaluator()
    bundle = compiler.compile({
        "contract_id": "test-005",
        "allowed_sandbox_levels": ["L3"],
        "allowed_operations": ["read"],
        "max_duration_seconds": 3600,
        "status": "active",
    })
    result = evaluator.evaluate(bundle, {"sandbox_level": "L3", "operation": "export", "duration_seconds": 1800})
    assert result["allowed"] is False
    assert "not permitted" in result["reason"]


def test_evaluate_policy_deny_duration():
    compiler = PolicyCompiler()
    evaluator = PolicyEvaluator()
    bundle = compiler.compile({
        "contract_id": "test-006",
        "allowed_sandbox_levels": ["L3"],
        "allowed_operations": ["read"],
        "max_duration_seconds": 3600,
        "status": "active",
    })
    result = evaluator.evaluate(bundle, {"sandbox_level": "L3", "operation": "read", "duration_seconds": 7200})
    assert result["allowed"] is False
    assert "Duration" in result["reason"]


def test_evaluate_policy_deny_dp_budget():
    compiler = PolicyCompiler()
    evaluator = PolicyEvaluator()
    bundle = compiler.compile({
        "contract_id": "test-007",
        "allowed_sandbox_levels": ["L3"],
        "allowed_operations": ["read"],
        "max_duration_seconds": 3600,
        "dp_epsilon_budget": 5.0,
        "status": "active",
    })
    result = evaluator.evaluate(bundle, {"sandbox_level": "L3", "operation": "read", "duration_seconds": 1800, "dp_epsilon": 10.0})
    assert result["allowed"] is False
    assert "DP epsilon" in result["reason"]


# --- P1-1: Mode-specific Rego template tests ---

class TestCompileForMode:

    def _base_contract(self, **overrides):
        base = {
            "contract_id": "mode-test-001",
            "allowed_sandbox_levels": ["L2", "L3"],
            "allowed_operations": ["read", "query"],
            "max_duration_seconds": 3600,
            "dp_epsilon_budget": 5.0,
            "status": "active",
        }
        base.update(overrides)
        return base

    def test_structured_query_mode(self):
        compiler = PolicyCompiler()
        bundle = compiler.compile_for_mode(
            self._base_contract(allowed_fields=["name", "email"], max_output_rows=5000),
            "structured_query",
        )
        assert "query" in bundle.rego_source
        assert "name" in bundle.rego_source
        assert "5000" in bundle.rego_source
        assert "field_allowed" in bundle.rego_source
        assert bundle.metadata["mode"] == "structured_query"

    def test_llm_training_mode(self):
        compiler = PolicyCompiler()
        bundle = compiler.compile_for_mode(
            self._base_contract(max_gpu_hours=50, max_epochs=20),
            "llm_training",
        )
        assert "train" in bundle.rego_source
        assert "50" in bundle.rego_source
        assert "20" in bundle.rego_source
        assert "safetensors" in bundle.rego_source or "onnx" in bundle.rego_source
        assert bundle.metadata["mode"] == "llm_training"

    def test_product_dev_mode(self):
        compiler = PolicyCompiler()
        bundle = compiler.compile_for_mode(
            self._base_contract(allowed_output_formats=["zip"]),
            "product_dev",
        )
        assert "build" in bundle.rego_source
        assert "package" in bundle.rego_source
        assert "not input.contains_raw_data" in bundle.rego_source
        assert bundle.metadata["mode"] == "product_dev"

    def test_structured_app_mode(self):
        compiler = PolicyCompiler()
        bundle = compiler.compile_for_mode(
            self._base_contract(max_requests_per_second=200, max_response_mb=50),
            "structured_app",
        )
        assert "api_call" in bundle.rego_source
        assert "200" in bundle.rego_source
        assert "50" in bundle.rego_source
        assert bundle.metadata["mode"] == "structured_app"

    def test_unknown_mode_falls_back_to_generic(self):
        compiler = PolicyCompiler()
        bundle = compiler.compile_for_mode(self._base_contract(), "unknown_mode")
        # Should use generic _generate_rego
        assert "allow {" in bundle.rego_source
        assert bundle.metadata["mode"] == "unknown_mode"

    def test_mode_specific_sm3_hash(self):
        compiler = PolicyCompiler()
        bundle = compiler.compile_for_mode(self._base_contract(), "structured_query")
        assert compiler.verify_integrity(bundle) is True

    def test_mode_specific_encrypted(self):
        compiler = PolicyCompiler()
        cipher = SM4Cipher()
        contract = self._base_contract(allowed_fields=["col1"])
        bundle = compiler.compile_for_mode(contract, "structured_query")
        # Verify encryption still works with mode-specific bundles
        ct, nonce, tag = cipher.encrypt_gcm(bundle.rego_source.encode())
        decrypted = cipher.decrypt_gcm(ct, nonce, tag).decode()
        assert decrypted == bundle.rego_source
