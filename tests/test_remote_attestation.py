"""Tests for TEE Remote Attestation service (SS-04)."""
import json
import pytest
from datetime import datetime, timezone, timedelta

from app.services.remote_attestation import (
    AttestationService,
    AttestationPolicy,
    SGXProvider,
    SEVProvider,
    FirecrackerProvider,
    AttestationProvider,
    TEEType,
    AttestationStatus,
    QuoteType,
    TEEQuote,
    AttestationResult,
)


@pytest.fixture
def service():
    return AttestationService()


@pytest.fixture
def sgx_provider():
    return SGXProvider()


@pytest.fixture
def sev_provider():
    return SEVProvider()


@pytest.fixture
def fc_provider():
    return FirecrackerProvider()


# ── Quote Generation ──────────────────────────────────────────

class TestQuoteGeneration:
    def test_generate_sgx_quote(self, service):
        quote = service.generate_quote(TEEType.SGX, b"session-123")
        assert quote.tee_type == TEEType.SGX
        assert quote.quote_type == QuoteType.SGX_ECDSA
        assert len(quote.measurement) == 64  # SHA-256 hex
        assert quote.raw_bytes is not None

    def test_generate_sev_quote(self, service):
        quote = service.generate_quote(TEEType.SEV_SNP, b"session-456")
        assert quote.tee_type == TEEType.SEV_SNP
        assert quote.quote_type == QuoteType.SEV_SNP

    def test_generate_firecracker_quote(self, service):
        quote = service.generate_quote(TEEType.FIRECRACKER, b"session-789")
        assert quote.tee_type == TEEType.FIRECRACKER
        assert quote.quote_type == QuoteType.SOFTWARE_HASH

    def test_generate_quote_no_provider(self, service):
        with pytest.raises(ValueError, match="No attestation provider"):
            service.generate_quote(TEEType.ITRUSTEE)

    def test_generate_quote_unique_ids(self, service):
        q1 = service.generate_quote(TEEType.SGX)
        q2 = service.generate_quote(TEEType.SGX)
        assert q1.quote_id != q2.quote_id

    def test_generate_quote_with_report_data(self, service):
        data = b"custom-report-data"
        quote = service.generate_quote(TEEType.SGX, data)
        assert quote.report_data == data.hex()

    def test_generate_quote_empty_report_data(self, service):
        quote = service.generate_quote(TEEType.SGX)
        assert quote.report_data == "00" * 64


# ── Quote Verification ────────────────────────────────────────

class TestQuoteVerification:
    def test_verify_sgx_quote(self, service):
        quote = service.generate_quote(TEEType.SGX, b"test")
        result = service.verify_quote(quote)
        assert result.verified is True
        assert result.status == AttestationStatus.VERIFIED
        assert len(result.signer_chain) > 0
        assert result.policy_checks["signature_valid"] is True

    def test_verify_sev_quote(self, service):
        quote = service.generate_quote(TEEType.SEV_SNP, b"test")
        result = service.verify_quote(quote)
        assert result.verified is True
        assert "VCEK" in result.signer_chain

    def test_verify_firecracker_quote(self, service):
        quote = service.generate_quote(TEEType.FIRECRACKER, b"test")
        result = service.verify_quote(quote)
        assert result.verified is True
        assert result.tee_type == TEEType.FIRECRACKER

    def test_verify_records_in_log(self, service):
        quote = service.generate_quote(TEEType.SGX)
        service.verify_quote(quote)
        log = service.get_verification_log()
        assert len(log) == 1

    def test_verify_has_duration(self, service):
        quote = service.generate_quote(TEEType.SGX)
        result = service.verify_quote(quote)
        assert result.duration_ms >= 0

    def test_tampered_sgx_quote_signature_fails(self, service):
        quote = service.generate_quote(TEEType.SGX, b"test")
        payload = json.loads(quote.raw_bytes)
        payload["report_data"] = "ff" * 32
        quote.raw_bytes = json.dumps(payload, sort_keys=True).encode()
        result = service.verify_quote(quote)
        assert result.verified is False
        assert result.status == AttestationStatus.INVALID_SIGNATURE

    def test_tampered_sev_measurement_fails(self, service):
        quote = service.generate_quote(TEEType.SEV_SNP, b"test")
        payload = json.loads(quote.raw_bytes)
        payload["measurement"] = "0" * 64
        quote.raw_bytes = json.dumps(payload, sort_keys=True).encode()
        result = service.verify_quote(quote)
        assert result.verified is False
        assert result.status == AttestationStatus.INVALID_MEASUREMENT


# ── Policy Enforcement ────────────────────────────────────────

class TestPolicyEnforcement:
    def test_policy_rejects_disallowed_tee(self):
        policy = AttestationPolicy(allowed_tee_types={TEEType.SGX})
        service = AttestationService(policy=policy)
        quote = service.generate_quote(TEEType.FIRECRACKER)
        result = service.verify_quote(quote)
        assert result.verified is False
        assert result.status == AttestationStatus.POLICY_VIOLATION

    def test_policy_measurement_whitelist_pass(self):
        service = AttestationService()
        quote = service.generate_quote(TEEType.SGX)
        policy = AttestationPolicy(allowed_measurements={quote.measurement})
        svc = AttestationService(policy=policy)
        result = svc.verify_quote(quote)
        assert result.verified is True

    def test_policy_measurement_whitelist_fail(self):
        policy = AttestationPolicy(allowed_measurements={"known-good-hash"})
        service = AttestationService(policy=policy)
        quote = service.generate_quote(TEEType.SGX)
        result = service.verify_quote(quote)
        assert result.verified is False
        assert result.status == AttestationStatus.INVALID_MEASUREMENT

    def test_policy_freshness_pass(self):
        service = AttestationService()
        quote = service.generate_quote(TEEType.SGX)
        result = service.verify_quote(quote)
        assert result.verified is True

    def test_policy_freshness_fail(self):
        policy = AttestationPolicy(max_quote_age_seconds=0)
        service = AttestationService(policy=policy)
        quote = service.generate_quote(TEEType.SGX)
        # Quote timestamp is "now", but max_age=0 means it's already expired
        # (any positive age > 0)
        import time
        time.sleep(0.01)
        result = service.verify_quote(quote)
        assert result.verified is False
        assert result.status == AttestationStatus.EXPIRED

    def test_policy_allows_all_tees_by_default(self):
        service = AttestationService()
        for tee in [TEEType.SGX, TEEType.SEV_SNP, TEEType.FIRECRACKER]:
            quote = service.generate_quote(tee)
            result = service.verify_quote(quote)
            assert result.verified is True


# ── SGX Provider ──────────────────────────────────────────────

class TestSGXProvider:
    def test_generate(self, sgx_provider):
        quote = sgx_provider.generate_quote(b"test")
        assert quote.tee_type == TEEType.SGX
        assert quote.quote_type == QuoteType.SGX_ECDSA

    def test_verify(self, sgx_provider):
        quote = sgx_provider.generate_quote()
        result = sgx_provider.verify_quote(quote)
        assert result.verified is True
        assert "Intel" in result.signer_chain[-1]

    def test_verify_wrong_tee_type(self, sgx_provider):
        quote = TEEQuote(
            quote_id="q1",
            quote_type=QuoteType.SEV_SNP,
            tee_type=TEEType.SEV_SNP,
            raw_bytes=b"{}",
            measurement="abc",
            report_data="",
        )
        result = sgx_provider.verify_quote(quote)
        assert result.verified is False
        assert result.status == AttestationStatus.ERROR


# ── SEV Provider ──────────────────────────────────────────────

class TestSEVProvider:
    def test_generate(self, sev_provider):
        quote = sev_provider.generate_quote(b"test")
        assert quote.tee_type == TEEType.SEV_SNP

    def test_verify(self, sev_provider):
        quote = sev_provider.generate_quote()
        result = sev_provider.verify_quote(quote)
        assert result.verified is True
        assert "AMD" in result.signer_chain[-1]


# ── Firecracker Provider ─────────────────────────────────────

class TestFirecrackerProvider:
    def test_generate(self, fc_provider):
        quote = fc_provider.generate_quote(b"test")
        assert quote.tee_type == TEEType.FIRECRACKER
        assert quote.quote_type == QuoteType.SOFTWARE_HASH

    def test_verify(self, fc_provider):
        quote = fc_provider.generate_quote()
        result = fc_provider.verify_quote(quote)
        assert result.verified is True

    def test_verify_component_mismatch(self, fc_provider):
        quote = fc_provider.generate_quote()
        # Tamper with raw bytes
        import json
        data = json.loads(quote.raw_bytes)
        data["components"] = "tampered"
        quote.raw_bytes = json.dumps(data).encode()
        result = fc_provider.verify_quote(quote)
        assert result.verified is False
        assert result.status == AttestationStatus.INVALID_MEASUREMENT


# ── Custom Provider ───────────────────────────────────────────

class TestCustomProvider:
    def test_register_custom_provider(self):
        class CustomProvider(AttestationProvider):
            @property
            def tee_type(self):
                return TEEType.ITRUSTEE

            def generate_quote(self, report_data=b""):
                return TEEQuote(
                    quote_id="custom-1",
                    quote_type=QuoteType.SOFTWARE_HASH,
                    tee_type=TEEType.ITRUSTEE,
                    raw_bytes=b"custom",
                    measurement="abc123",
                    report_data="",
                )

            def verify_quote(self, quote):
                return AttestationResult(
                    quote_id=quote.quote_id,
                    status=AttestationStatus.VERIFIED,
                    tee_type=TEEType.ITRUSTEE,
                    measurement=quote.measurement,
                    verified=True,
                )

        policy = AttestationPolicy(allowed_tee_types={TEEType.SGX, TEEType.SEV_SNP, TEEType.FIRECRACKER, TEEType.ITRUSTEE, TEEType.GPU_CC})
        service = AttestationService(policy=policy)
        service.register_provider(CustomProvider())
        quote = service.generate_quote(TEEType.ITRUSTEE)
        assert quote.tee_type == TEEType.ITRUSTEE
        result = service.verify_quote(quote)
        assert result.verified is True


# ── Verification Log & Stats ──────────────────────────────────

class TestLogAndStats:
    def test_log_filter_by_status(self, service):
        quote = service.generate_quote(TEEType.SGX)
        service.verify_quote(quote)
        verified = service.get_verification_log(status=AttestationStatus.VERIFIED)
        assert len(verified) == 1
        invalid = service.get_verification_log(status=AttestationStatus.INVALID_MEASUREMENT)
        assert len(invalid) == 0

    def test_log_limit(self, service):
        for _ in range(5):
            quote = service.generate_quote(TEEType.SGX)
            service.verify_quote(quote)
        log = service.get_verification_log(limit=3)
        assert len(log) == 3

    def test_stats(self, service):
        quote = service.generate_quote(TEEType.SGX)
        service.verify_quote(quote)
        stats = service.get_stats()
        assert stats["total_verifications"] == 1
        assert stats["verified"] == 1
        assert stats["failed"] == 0
        assert "sgx" in stats["registered_providers"]

    def test_stats_counts_failures(self):
        policy = AttestationPolicy(allowed_tee_types={TEEType.SGX})
        service = AttestationService(policy=policy)
        quote = service.generate_quote(TEEType.SGX)
        service.verify_quote(quote)  # pass
        bad_quote = service.generate_quote(TEEType.FIRECRACKER)
        service.verify_quote(bad_quote)  # fail
        stats = service.get_stats()
        assert stats["total_verifications"] == 2
        assert stats["verified"] == 1
        assert stats["failed"] == 1


# ── Enums ─────────────────────────────────────────────────────

class TestEnums:
    def test_tee_type_values(self):
        assert TEEType.SGX.value == "sgx"
        assert TEEType.SEV_SNP.value == "sev_snp"
        assert TEEType.FIRECRACKER.value == "firecracker"
        assert TEEType.GPU_CC.value == "gpu_cc"

    def test_attestation_status_values(self):
        assert AttestationStatus.VERIFIED.value == "verified"
        assert AttestationStatus.INVALID_SIGNATURE.value == "invalid_signature"
        assert AttestationStatus.EXPIRED.value == "expired"

    def test_quote_type_values(self):
        assert QuoteType.SGX_ECDSA.value == "sgx_ecdsa"
        assert QuoteType.SEV_SNP.value == "sev_snp"
        assert QuoteType.SOFTWARE_HASH.value == "software_hash"


# ── Dataclass Construction ────────────────────────────────────

class TestDataclasses:
    def test_attestation_policy_defaults(self):
        p = AttestationPolicy()
        assert TEEType.SGX in p.allowed_tee_types
        assert TEEType.SEV_SNP in p.allowed_tee_types
        assert p.require_fresh_quote is True
        assert p.max_quote_age_seconds == 300

    def test_attestation_result_defaults(self):
        r = AttestationResult(
            quote_id="q1",
            status=AttestationStatus.VERIFIED,
            tee_type=TEEType.SGX,
            measurement="abc",
        )
        assert r.verified is False
        assert r.signer_chain == []
        assert r.policy_checks == {}
