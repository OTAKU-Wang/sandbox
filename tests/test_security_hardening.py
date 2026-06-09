"""Tests for security hardening — JWT validation, bwrap fallback, audit integration."""
import os
import pytest
from unittest.mock import patch, AsyncMock

from app.core.config import Settings, _JWT_DEFAULT_KEY, _JWT_MIN_KEY_LENGTH


# ── JWT Security Validation ────────────────────────────────────

class TestJWTSecurityValidation:
    def test_default_key_rejected_in_production(self):
        """Production mode with default JWT key should raise ValueError."""
        settings = Settings(JWT_SECRET_KEY=_JWT_DEFAULT_KEY, DEBUG=False)
        with pytest.raises(ValueError, match="default value"):
            settings.validate_jwt_security()

    def test_default_key_allowed_in_debug(self):
        """Debug mode with default JWT key should return warning, not error."""
        settings = Settings(JWT_SECRET_KEY=_JWT_DEFAULT_KEY, DEBUG=True)
        issues = settings.validate_jwt_security()
        assert len(issues) == 1
        assert "WARN" in issues[0]

    def test_short_key_rejected(self):
        """Key shorter than 32 bytes should raise ValueError."""
        settings = Settings(JWT_SECRET_KEY="short", DEBUG=False)
        with pytest.raises(ValueError, match="at least"):
            settings.validate_jwt_security()

    def test_valid_key_passes(self):
        """A proper key should pass validation with no issues."""
        settings = Settings(JWT_SECRET_KEY="a" * 64, DEBUG=False)
        issues = settings.validate_jwt_security()
        assert issues == []

    def test_exactly_min_length_key_passes(self):
        """Key exactly at minimum length should pass."""
        settings = Settings(JWT_SECRET_KEY="b" * _JWT_MIN_KEY_LENGTH, DEBUG=False)
        issues = settings.validate_jwt_security()
        assert issues == []

    def test_one_below_min_length_rejected(self):
        """Key one byte below minimum should be rejected."""
        settings = Settings(JWT_SECRET_KEY="c" * (_JWT_MIN_KEY_LENGTH - 1), DEBUG=False)
        with pytest.raises(ValueError, match="at least"):
            settings.validate_jwt_security()

    def test_unicode_key_length_check(self):
        """Unicode characters should be measured in bytes, not characters."""
        # Each Chinese char is 3 bytes in UTF-8, so 11 chars = 33 bytes ≥ 32
        settings = Settings(JWT_SECRET_KEY="一二三四五六七八九十一二", DEBUG=False)
        issues = settings.validate_jwt_security()
        assert issues == []

    def test_auth_service_rejects_default_key_in_production(self, monkeypatch):
        """Token issuing should fail closed if production reaches the default key."""
        import uuid
        from app.services import auth_service

        monkeypatch.delenv("TESTING", raising=False)
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        monkeypatch.setattr(auth_service, "settings", Settings(JWT_SECRET_KEY=_JWT_DEFAULT_KEY, DEBUG=False))

        with pytest.raises(ValueError, match="default value"):
            auth_service.create_access_token(uuid.uuid4(), "buyer")

    def test_auth_service_uses_long_local_key_for_tests(self, monkeypatch):
        """Testing mode should not sign HS256 tokens with the short sentinel key."""
        import uuid
        from app.services import auth_service

        monkeypatch.setenv("TESTING", "1")
        monkeypatch.setattr(auth_service, "settings", Settings(JWT_SECRET_KEY=_JWT_DEFAULT_KEY, DEBUG=False))

        token = auth_service.create_access_token(uuid.uuid4(), "buyer")
        payload = auth_service.decode_access_token(token)

        assert payload["type"] == "access"


# ── Bwrap Unsandboxed Fallback ─────────────────────────────────

class TestBwrapFallback:
    def test_default_disallows_fallback(self):
        """BwrapAdapter should disallow unsandboxed fallback by default."""
        from app.services.sandbox_runtime import BwrapAdapter
        adapter = BwrapAdapter(workspace_root="/tmp/cds-test-hardening")
        assert adapter._allow_unsandboxed_fallback is False

    def test_explicit_fallback_allowed(self):
        """BwrapAdapter should allow fallback when explicitly enabled."""
        from app.services.sandbox_runtime import BwrapAdapter
        adapter = BwrapAdapter(workspace_root="/tmp/cds-test-hardening", allow_unsandboxed_fallback=True)
        assert adapter._allow_unsandboxed_fallback is True

    @pytest.mark.asyncio
    async def test_fallback_disabled_returns_security_error(self):
        """When bwrap is missing and fallback disabled, should return security error."""
        from app.services.sandbox_runtime import BwrapAdapter
        import uuid
        adapter = BwrapAdapter(workspace_root="/tmp/cds-test-hardening", allow_unsandboxed_fallback=False)
        session_id = str(uuid.uuid4())
        prov = adapter.provision(session_id, "", timeout=60)
        container_id = prov["container_id"]

        # Patch asyncio.to_thread to raise FileNotFoundError (simulating missing bwrap)
        import asyncio

        original_to_thread = asyncio.to_thread

        async def mock_to_thread(func, *args, **kwargs):
            if args and args[0] and args[0][0] == "bwrap":
                raise FileNotFoundError("bwrap not found")
            return await original_to_thread(func, *args, **kwargs)

        with patch("asyncio.to_thread", side_effect=mock_to_thread):
            result = await adapter.execute(container_id, "print(1)", "python")

        assert result["exit_code"] == -1
        assert "SECURITY ERROR" in result["output"]
        assert "bwrap not installed" in result["output"]

        adapter.terminate(container_id)


# ── Audit Service Blockchain Integration ───────────────────────

class TestAuditBlockchainIntegration:
    @pytest.mark.asyncio
    async def test_log_to_blockchain_success(self):
        """log_to_blockchain should use blockchain_adapter and return tx_hash."""
        from app.services.audit_service import AuditService

        svc = AuditService()
        tx_hash = await svc.log_to_blockchain("test-merkle-root-abc123")
        # PGAppendOnlyAdapter is the default — should succeed
        assert tx_hash is not None
        assert len(tx_hash) == 64  # SHA-256 hex

    @pytest.mark.asyncio
    async def test_log_to_blockchain_returns_none_on_failure(self):
        """log_to_blockchain should return None if adapter fails, not raise."""
        from app.services.audit_service import AuditService

        svc = AuditService()
        with patch("app.services.blockchain_adapter.blockchain_adapter") as mock_adapter:
            mock_adapter.anchor = AsyncMock(side_effect=Exception("chain unavailable"))
            tx_hash = await svc.log_to_blockchain("test-root")
            assert tx_hash is None


# ── Config Constants ───────────────────────────────────────────

class TestConfigConstants:
    def test_jwt_default_key_value(self):
        assert _JWT_DEFAULT_KEY == "change-me-in-production"

    def test_jwt_min_key_length(self):
        assert _JWT_MIN_KEY_LENGTH == 32
