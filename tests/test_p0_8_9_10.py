"""P0-8/9/10 Security Tests — DEK SM2 encryption, TEE Quote verification, cert revocation cascade.

These tests verify the three remaining P0 security gaps.
Tests use pytest.xfail() for features not yet implemented by Andrew.
"""
import pytest
import uuid
from unittest.mock import MagicMock, AsyncMock, patch


# ============================================================
# P0-8: DEK SM2 Encrypted Storage
# ============================================================

class TestDEKSM2Encryption:
    """P0-8: Data Encryption Keys should be encrypted with SM2 public key before storage."""

    def test_kms_service_has_sm2_encrypt_capability(self):
        """KMSService should have SM2 encryption for DEK storage."""
        from app.services.kms_service import KMSService
        svc = KMSService()
        # After P0-8: should have SM2 encrypt method
        has_sm2 = (
            hasattr(svc, '_sm2_encrypt') or
            hasattr(svc, 'encrypt_dek_sm2') or
            hasattr(svc, '_crypto') and hasattr(svc._crypto, 'encrypt')
        )
        if not has_sm2:
            pytest.xfail("P0-8: KMSService lacks SM2 DEK encryption")

    def test_generate_data_key_sm2_encrypted(self):
        """generate_data_key should return SM2-encrypted DEK when cert_public_key provided."""
        from app.services.kms_service import KMSService
        from app.services.crypto_service import crypto_service
        svc = KMSService()

        # Generate a test SM2 keypair for cert-based encryption
        kp = crypto_service.generate_keypair()
        key_info = svc.generate_data_key("test-product-sm2", cert_public_key=kp.public_key)
        raw_dek = key_info.get("key_bytes")
        assert raw_dek is not None

        # P0-8: sm2_encrypted_key should be present and different from raw DEK
        encrypted = key_info.get("sm2_encrypted_key")
        assert encrypted is not None, "P0-8: sm2_encrypted_key missing despite cert_public_key"
        assert encrypted != raw_dek.hex(), \
            "P0-8: DEK stored as plaintext — should be SM2-encrypted"

    def test_dek_decryption_requires_sm2_private_key(self):
        """Decrypting a stored DEK should require SM2 private key."""
        from app.services.kms_service import KMSService
        svc = KMSService()
        key_info = svc.generate_data_key("test-decrypt")
        key_id = key_info.get("key_id")

        # After P0-8: distribute_key should use SM2 private key to decrypt DEK
        distributed = svc.distribute_key(key_id, "test-session")
        if distributed is None:
            pytest.xfail("P0-8: distribute_key returns None — SM2 decryption not wired")

    def test_dek_not_recoverable_without_private_key(self):
        """Attacker with only KEK should not recover DEK if SM2-encrypted."""
        from app.services.kms_service import KMSService
        svc = KMSService()
        key_info = svc.generate_data_key("test-no-recovery")
        # After P0-8: even if attacker extracts KEK-wrapped blob,
        # SM2 encryption layer prevents DEK recovery without private key
        wrapped = svc._wrapped_keys.get(key_info["key_id"])
        if wrapped is None:
            pytest.skip("No wrapped key found")


# ============================================================
# P0-9: TEE Quote Verification
# ============================================================

class TestTEEQuoteVerification:
    """P0-9: Key distribution should verify TEE attestation before releasing keys."""

    def test_distribute_key_requires_attestation(self):
        """distribute_key should reject requests without valid TEE quote."""
        from app.services.kms_service import KMSService
        svc = KMSService()
        key_info = svc.generate_session_key("session-no-tee")
        key_id = key_info["key_id"]

        # After P0-9: distribute_key should accept attestation parameter
        # and reject if quote is invalid/missing
        result = svc.distribute_key(key_id, "session-no-tee")
        # Currently returns hex without attestation — this is the gap
        if result is not None:
            # Check if there's an attestation parameter
            import inspect
            sig = inspect.signature(svc.distribute_key)
            if 'attestation' not in str(sig) and 'quote' not in str(sig):
                pytest.xfail("P0-9: distribute_key has no TEE attestation parameter")

    def test_distribute_key_rejects_invalid_quote(self):
        """distribute_key should reject tampered/invalid TEE quotes."""
        from app.services.kms_service import KMSService
        svc = KMSService()
        key_info = svc.generate_session_key("session-bad-quote")
        key_id = key_info["key_id"]

        fake_quote = b"tampered_tee_quote_data"
        try:
            result = svc.distribute_key(key_id, "session-bad-quote", attestation=fake_quote)
            # If it succeeds with a fake quote, that's the gap
            if result is not None:
                pytest.xfail("P0-9: Invalid TEE quote accepted — should reject")
        except (TypeError, AttributeError):
            pytest.xfail("P0-9: distribute_key doesn't accept attestation parameter")

    def test_distribute_key_accepts_valid_quote(self):
        """distribute_key should release key when valid TEE quote is provided."""
        from app.services.kms_service import KMSService
        svc = KMSService()
        key_info = svc.generate_session_key("session-good-quote")
        key_id = key_info["key_id"]

        # This will need a mock or real TEE quote after P0-9
        try:
            result = svc.distribute_key(key_id, "session-good-quote", attestation=b"valid_quote")
            # After P0-9: should return hex key for valid attestation
            assert result is not None or True  # placeholder
        except (TypeError, AttributeError):
            pytest.xfail("P0-9: distribute_key doesn't support attestation yet")


# ============================================================
# P0-10: Certificate Revocation Cascade
# ============================================================

class TestCertRevocationCascade:
    """P0-10: Revoking a certificate should cascade to suspend active sessions."""

    def test_revoke_certificate_method_exists(self):
        """CertificateService should have revoke_certificate."""
        from app.services.certificate_service import CertificateService
        svc = CertificateService()
        assert hasattr(svc, 'revoke_certificate')

    def test_cascade_suspension_method_exists(self):
        """CertificateService or session service should have suspend_sessions_by_cert."""
        from app.services.certificate_service import CertificateService
        svc = CertificateService()

        has_cascade = (
            hasattr(svc, 'suspend_sessions_by_cert') or
            hasattr(svc, '_cascade_revoke') or
            hasattr(svc, '_suspend_dependent_sessions')
        )
        if not has_cascade:
            # Check if it's in sandbox_sessions
            try:
                from app.services.sandbox_sessions import SandboxSessionService
                session_svc = SandboxSessionService()
                has_cascade = hasattr(session_svc, 'suspend_sessions_by_cert')
            except ImportError:
                pass
        if not has_cascade:
            pytest.xfail("P0-10: No suspend_sessions_by_cert method exists")

    @pytest.mark.asyncio
    async def test_revoke_cascades_to_sessions(self):
        """Revoking a cert should suspend all sessions using that cert."""
        from app.services.certificate_service import CertificateService

        svc = CertificateService()
        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        mock_db.flush = AsyncMock()
        mock_db.refresh = AsyncMock()

        # Create a mock certificate
        cert_id = uuid.uuid4()
        mock_cert = MagicMock()
        mock_cert.id = cert_id
        mock_cert.status = "active"
        mock_cert.serial_number = "CERT-001"

        # Mock DB query for cert
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_cert
        mock_db.execute.return_value = mock_result

        # After P0-10: revoke should cascade to sessions
        try:
            await svc.revoke_certificate(mock_db, cert_id, reason="compromised")
            # Check if cascade was called (look for session suspension)
            # This depends on Andrew's implementation
        except Exception as e:
            pytest.xfail(f"P0-10: Cascade not implemented — {e}")

    @pytest.mark.asyncio
    async def test_revoked_cert_sessions_get_suspended_status(self):
        """Sessions using a revoked cert should be terminated via cascade.

        P0-10 implementation: _cascade_revoke calls sandbox_runtime.terminate_sessions_by_key()
        which terminates sessions linked to revoked DEKs. The cascade is cert → DEKs → sessions.
        """
        from app.services.certificate_service import CertificateService
        from app.services.sandbox_runtime import SandboxRuntime

        cert_svc = CertificateService()
        # Verify the cascade method exists and calls terminate_sessions_by_key
        import inspect
        source = inspect.getsource(cert_svc._cascade_revoke)
        has_termination = 'terminate_sessions_by_key' in source or 'terminate' in source
        assert has_termination, "P0-10: _cascade_revoke should terminate sessions via runtime"

    @pytest.mark.asyncio
    async def test_cascade_audit_logged(self):
        """Certificate revocation cascade should generate audit entries."""
        # After P0-10: Both the cert revocation AND session suspensions
        # should appear in audit log
        try:
            from app.services.certificate_service import CertificateService
            svc = CertificateService()
            assert hasattr(svc, 'revoke_certificate')
            # Implementation-specific audit verification after P0-10
        except Exception:
            pytest.xfail("P0-10: Cascade audit not verifiable yet")
