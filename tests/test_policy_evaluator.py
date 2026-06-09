"""Tests for Policy Evaluator (PDP runtime)."""
import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.policy_evaluator import (
    PolicyEvaluator, AccessRequest, Decision, PolicyDecision,
)
from app.models.policy_bundle import PolicyBundle


@pytest.fixture
def evaluator():
    return PolicyEvaluator()


@pytest.fixture
def sample_bundle():
    return PolicyBundle(
        id=uuid.uuid4(),
        policy_id="policy-test-001",
        contract_id=uuid.uuid4(),
        rego_source='package test\ndefault allow = true',
        integrity_hash="placeholder",  # Will be patched
        field_acl={
            "name": {"read": True, "mask_pattern": "***"},
            "ssn": {"read": True, "mask_pattern": "prefix:3", "aggregate_only": True},
            "secret_field": {"read": False},
        },
        allowed_ops=["read", "query", "train"],
        sandbox_modes=["query", "train"],
    )


class TestPolicyEvaluator:
    @pytest.mark.asyncio
    async def test_no_bundle_denies(self, evaluator):
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=mock_result)

        request = AccessRequest(
            user_id=uuid.uuid4(),
            contract_id=uuid.uuid4(),
            operation="read",
            sandbox_mode="query",
        )
        decision = await evaluator.evaluate(request, mock_db)
        assert decision.decision == Decision.DENY
        assert "No active policy bundle" in decision.reason

    @pytest.mark.asyncio
    async def test_operation_not_allowed(self, evaluator, sample_bundle):
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sample_bundle
        mock_db.execute = AsyncMock(return_value=mock_result)

        with patch("app.utils.crypto.sm3_hash", return_value="placeholder"):
            request = AccessRequest(
                user_id=uuid.uuid4(),
                contract_id=sample_bundle.contract_id,
                operation="export",  # Not in allowed_ops
                sandbox_mode="query",
            )
            decision = await evaluator.evaluate(request, mock_db)
            assert decision.decision == Decision.DENY
            assert "not in allowed ops" in decision.reason

    @pytest.mark.asyncio
    async def test_sandbox_mode_not_allowed(self, evaluator, sample_bundle):
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sample_bundle
        mock_db.execute = AsyncMock(return_value=mock_result)

        with patch("app.utils.crypto.sm3_hash", return_value="placeholder"):
            request = AccessRequest(
                user_id=uuid.uuid4(),
                contract_id=sample_bundle.contract_id,
                operation="read",
                sandbox_mode="develop",  # Not in sandbox_modes
            )
            decision = await evaluator.evaluate(request, mock_db)
            assert decision.decision == Decision.DENY
            assert "not allowed" in decision.reason

    @pytest.mark.asyncio
    async def test_allowed_request_with_field_acl(self, evaluator, sample_bundle):
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sample_bundle
        mock_db.execute = AsyncMock(return_value=mock_result)

        with patch("app.utils.crypto.sm3_hash", return_value="placeholder"):
            request = AccessRequest(
                user_id=uuid.uuid4(),
                contract_id=sample_bundle.contract_id,
                operation="query",
                sandbox_mode="query",
                fields=["name", "ssn", "secret_field"],
            )
            decision = await evaluator.evaluate(request, mock_db)
            assert decision.decision == Decision.ALLOW
            assert "name" in decision.allowed_fields
            assert "ssn" in decision.allowed_fields
            assert "secret_field" in decision.denied_fields
            assert decision.field_masks["name"] == "***"
            assert decision.field_masks["ssn"] == "prefix:3"

    @pytest.mark.asyncio
    async def test_all_fields_denied(self, evaluator, sample_bundle):
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sample_bundle
        mock_db.execute = AsyncMock(return_value=mock_result)

        with patch("app.utils.crypto.sm3_hash", return_value="placeholder"):
            request = AccessRequest(
                user_id=uuid.uuid4(),
                contract_id=sample_bundle.contract_id,
                operation="read",
                sandbox_mode="query",
                fields=["secret_field"],
            )
            decision = await evaluator.evaluate(request, mock_db)
            assert decision.decision == Decision.DENY
            assert "All requested fields are denied" in decision.reason

    @pytest.mark.asyncio
    async def test_integrity_check_failure(self, evaluator, sample_bundle):
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sample_bundle
        mock_db.execute = AsyncMock(return_value=mock_result)

        with patch("app.utils.crypto.sm3_hash", return_value="different_hash"):
            request = AccessRequest(
                user_id=uuid.uuid4(),
                contract_id=sample_bundle.contract_id,
                operation="read",
                sandbox_mode="query",
            )
            decision = await evaluator.evaluate(request, mock_db)
            assert decision.decision == Decision.DENY
            assert "integrity" in decision.reason.lower()

    @pytest.mark.asyncio
    async def test_no_field_acl_allows_all(self, evaluator):
        bundle = PolicyBundle(
            id=uuid.uuid4(),
            policy_id="policy-open",
            contract_id=uuid.uuid4(),
            rego_source="package open",
            integrity_hash="placeholder",
            field_acl=None,
            allowed_ops=["read"],
            sandbox_modes=["query"],
        )
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = bundle
        mock_db.execute = AsyncMock(return_value=mock_result)

        with patch("app.utils.crypto.sm3_hash", return_value="placeholder"):
            request = AccessRequest(
                user_id=uuid.uuid4(),
                contract_id=bundle.contract_id,
                operation="read",
                sandbox_mode="query",
                fields=["any_field"],
            )
            decision = await evaluator.evaluate(request, mock_db)
            assert decision.decision == Decision.ALLOW
            assert "any_field" in decision.allowed_fields

    @pytest.mark.asyncio
    async def test_dp_budget_check_exception_fails_closed(self, evaluator):
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(side_effect=RuntimeError("db unavailable"))
        request = AccessRequest(
            user_id=uuid.uuid4(),
            contract_id=uuid.uuid4(),
            operation="query",
            sandbox_mode="query",
            epsilon_cost=0.1,
        )

        allowed = await evaluator._check_dp_budget(request, mock_db)

        assert allowed is False


class TestFieldACLRules:
    def test_evaluate_field_acl_open(self, evaluator):
        allowed, denied, masks = evaluator._evaluate_field_acl(
            ["a", "b", "c"], {}
        )
        assert allowed == ["a", "b", "c"]
        assert denied == []

    def test_evaluate_field_acl_with_rules(self, evaluator):
        acl = {
            "a": {"read": True},
            "b": {"read": True, "mask_pattern": "***"},
            "c": {"read": False},
        }
        allowed, denied, masks = evaluator._evaluate_field_acl(
            ["a", "b", "c", "d"], acl
        )
        assert "a" in allowed
        assert "b" in allowed
        assert "d" in allowed  # No rule = allow
        assert "c" in denied
        assert masks["b"] == "***"
