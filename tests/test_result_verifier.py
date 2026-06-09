"""Tests for Result Verifier — SM2 certificate chain + DCAP attestation (S6-2)."""
import pytest
import json

from app.services.result_verifier import (
    ResultVerifier, VerificationLevel, AttestationStatus,
    CertificateInfo, result_verifier,
)
from app.services.crypto_service import crypto_service


@pytest.fixture
def verifier():
    return ResultVerifier()


class TestHashVerification:
    def test_valid_hash(self, verifier):
        data = b"test result data"
        expected_hash = crypto_service.sm3_hash(data)
        result = verifier.verify_result(data, expected_hash, level=VerificationLevel.HASH_ONLY)
        assert result.hash_valid is True
        assert result.verified is True

    def test_invalid_hash(self, verifier):
        data = b"test result data"
        result = verifier.verify_result(data, "0000deadbeef", level=VerificationLevel.HASH_ONLY)
        assert result.hash_valid is False
        assert result.verified is False
        assert "Hash mismatch" in result.errors[0]

    def test_none_level_skips_hash(self, verifier):
        data = b"test result data"
        result = verifier.verify_result(data, "wrong_hash", level=VerificationLevel.NONE)
        assert result.verified is True  # NONE level doesn't check hash


class TestSignatureVerification:
    def test_valid_signature(self, verifier):
        data = b"signed result"
        hash_val = crypto_service.sm3_hash(data)
        # Sign with a keypair
        kp = crypto_service.generate_keypair()
        sig = crypto_service.sign(data, kp.private_key, kp.public_key)

        # Create JSON certificate
        cert = json.dumps({"subject": "test", "issuer": "ca", "public_key": kp.public_key})

        result = verifier.verify_result(
            data, hash_val, signature=sig.signature,
            certificate=cert, level=VerificationLevel.SIGNATURE,
        )
        assert result.hash_valid is True
        assert result.signature_valid is True
        assert result.verified is True

    def test_tampered_signature(self, verifier):
        data = b"original data"
        hash_val = crypto_service.sm3_hash(data)
        kp = crypto_service.generate_keypair()
        sig = crypto_service.sign(data, kp.private_key, kp.public_key)

        # Verify with different data (tampered)
        cert = json.dumps({"subject": "test", "issuer": "ca", "public_key": kp.public_key})
        result = verifier.verify_result(
            b"tampered data", hash_val, signature=sig.signature,
            certificate=cert, level=VerificationLevel.SIGNATURE,
        )
        assert result.hash_valid is False  # Hash doesn't match tampered data

    def test_no_signature_warning(self, verifier):
        data = b"unsigned result"
        hash_val = crypto_service.sm3_hash(data)
        result = verifier.verify_result(data, hash_val, level=VerificationLevel.SIGNATURE)
        assert result.hash_valid is True
        assert result.verified is False
        assert len(result.warnings) > 0


class TestCertificateChain:
    def test_parse_json_certificate(self, verifier):
        cert = json.dumps({"subject": "test-subject", "issuer": "test-ca", "serial": "12345", "public_key": "abc"})
        info = verifier._parse_certificate(cert)
        assert info.subject == "test-subject"
        assert info.issuer == "test-ca"
        assert info.serial_number == "12345"

    def test_parse_raw_certificate(self, verifier):
        info = verifier._parse_certificate("raw-hex-data")
        assert info.subject == "raw-certificate"

    def test_certificate_chain_valid(self, verifier):
        ca_cert = json.dumps({"subject": "trusted-ca", "issuer": "root", "is_ca": True})
        user_cert = json.dumps({"subject": "user1", "issuer": "trusted-ca", "public_key": "abc"})
        verifier.register_trusted_ca("trusted-ca", "ca-pub-key")

        info = verifier.verify_certificate_chain(user_cert)
        assert info.trust_chain_valid is True

    def test_certificate_chain_invalid(self, verifier):
        user_cert = json.dumps({"subject": "user1", "issuer": "unknown-ca", "public_key": "abc"})
        info = verifier.verify_certificate_chain(user_cert)
        assert info.trust_chain_valid is False


class TestAttestation:
    def test_local_attestation_quote(self, verifier):
        data_hash = crypto_service.sm3_hash(b"some-result")
        quote = verifier.generate_attestation_quote(data_hash)
        status = verifier._verify_attestation(data_hash, quote)
        assert status == AttestationStatus.VERIFIED

    def test_tampered_attestation_quote_fails(self, verifier):
        data_hash = crypto_service.sm3_hash(b"some-result")
        quote = verifier.generate_attestation_quote(data_hash)
        quote.report_data = "0" * 64
        status = verifier._verify_attestation(data_hash, quote)
        assert status == AttestationStatus.FAILED


class TestResultHashing:
    def test_generate_hash(self, verifier):
        data = b"test data"
        h = verifier.generate_result_hash(data)
        assert len(h) == 64  # SM3 hex output

    def test_sign_result(self, verifier):
        data = b"sign me"
        sig = verifier.sign_result(data)
        assert len(sig) == 128  # SM2 signature hex


class TestFullVerification:
    def test_full_level_requires_signed_attestation(self, verifier):
        data = b"full verification test"
        hash_val = crypto_service.sm3_hash(data)
        kp = crypto_service.generate_keypair()
        sig = crypto_service.sign(data, kp.private_key, kp.public_key)
        cert = json.dumps({"subject": "test", "issuer": "ca", "public_key": kp.public_key})
        quote = verifier.generate_attestation_quote(hash_val)
        result = verifier.verify_result(
            data,
            hash_val,
            signature=sig.signature,
            certificate=cert,
            level=VerificationLevel.FULL,
            attestation_quote=quote,
        )
        assert result.hash_valid is True
        assert result.signature_valid is True
        assert result.attestation_status == AttestationStatus.VERIFIED
        assert result.verified is True

    def test_full_level_without_attestation_fails(self, verifier):
        data = b"full verification test"
        hash_val = crypto_service.sm3_hash(data)
        kp = crypto_service.generate_keypair()
        sig = crypto_service.sign(data, kp.private_key, kp.public_key)
        cert = json.dumps({"subject": "test", "issuer": "ca", "public_key": kp.public_key})
        result = verifier.verify_result(
            data,
            hash_val,
            signature=sig.signature,
            certificate=cert,
            level=VerificationLevel.FULL,
        )
        assert result.attestation_status == AttestationStatus.FAILED
        assert result.verified is False


class TestSingleton:
    def test_singleton_exists(self):
        assert result_verifier is not None
        assert isinstance(result_verifier, ResultVerifier)
