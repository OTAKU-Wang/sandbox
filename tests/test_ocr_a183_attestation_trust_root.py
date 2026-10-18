"""ocr A183: attestation trust-root honesty + operator whitelist binding.

Two invariants pinned here:
1. The operator measurement whitelist (CDS_ATTESTATION_ALLOWED_MEASUREMENTS)
   binds on EVERY verification path — including the KMS key-distribution path
   that constructs its own empty AttestationPolicy (previously bypassed the
   whitelist entirely).
2. A quote that chains only to the built-in local simulated root is software
   attestation, never a hardware verification: it carries
   trust_root="local-simulated" + is_simulation=True, and when
   CDS_ALLOW_SIMULATION=false it verifies as False with an explicit reason
   (and the KMS gate therefore withholds the DEK).
"""
import pytest

from app.core.config import get_settings
from app.services.remote_attestation import (
    AttestationPolicy,
    AttestationService,
    AttestationStatus,
    AttestationProvider,
    AttestationResult,
    QuoteType,
    TEEQuote,
    TEEType,
)


# ── operator whitelist binding (KMS-path bypass) ────────────────────────────

def test_operator_whitelist_binds_with_explicit_empty_policy(monkeypatch):
    """The KMS path builds AttestationPolicy() with an empty whitelist; the
    operator-configured whitelist must still be enforced there."""
    settings = get_settings()
    monkeypatch.setattr(settings, "ATTESTATION_ALLOWED_MEASUREMENTS", "deadbeef,cafe0000")
    # KMS-shaped policy: explicit object, measurements left empty.
    svc = AttestationService(policy=AttestationPolicy(require_fresh_quote=True, max_quote_age_seconds=300))
    quote = svc.generate_quote(TEEType.SGX, b"session-a183")

    result = svc.verify_quote(quote)

    assert result.verified is False
    assert result.status == AttestationStatus.INVALID_MEASUREMENT


def test_operator_whitelist_admits_listed_measurement(monkeypatch):
    settings = get_settings()
    svc = AttestationService()
    quote = svc.generate_quote(TEEType.SGX, b"session-a183-ok")
    monkeypatch.setattr(settings, "ATTESTATION_ALLOWED_MEASUREMENTS", quote.measurement)
    svc2 = AttestationService(policy=AttestationPolicy())

    result = svc2.verify_quote(quote)

    assert result.verified is True
    assert result.trust_root == "local-simulated"


def test_explicit_policy_and_operator_whitelist_both_bind(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "ATTESTATION_ALLOWED_MEASUREMENTS", "aaaa")
    svc = AttestationService(policy=AttestationPolicy(allowed_measurements={"bbbb"}))
    quote = svc.generate_quote(TEEType.SGX)
    # measurement matches neither whitelist
    result = svc.verify_quote(quote)
    assert result.verified is False
    assert result.status == AttestationStatus.INVALID_MEASUREMENT


# ── local-simulated trust root is never a hardware verification ─────────────

def test_local_root_quote_carries_simulation_disclosure(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "ALLOW_SIMULATION", True)
    svc = AttestationService()
    quote = svc.generate_quote(TEEType.SGX, b"a183-sim")

    result = svc.verify_quote(quote)

    assert result.trust_root == "local-simulated"
    assert result.verified is True  # simulation explicitly allowed in this posture
    assert result.is_simulation is True
    assert result.policy_checks.get("hardware_signature") is False


@pytest.mark.parametrize("tee", [TEEType.SGX, TEEType.SEV_SNP, TEEType.FIRECRACKER])
def test_local_root_not_verified_when_simulation_disallowed(monkeypatch, tee):
    settings = get_settings()
    monkeypatch.setattr(settings, "ALLOW_SIMULATION", False)
    svc = AttestationService()
    quote = svc.generate_quote(tee, b"a183-nosim")

    result = svc.verify_quote(quote)

    assert result.verified is False
    assert result.trust_root == "local-simulated"
    assert result.is_simulation is True
    assert result.status == AttestationStatus.POLICY_VIOLATION
    assert "local" in (result.error_message or "")


def test_external_provider_still_verifies_without_simulation(monkeypatch):
    """Over-correction guard: a real external-root provider is not gated by
    ALLOW_SIMULATION — only local-root quotes are."""
    class ExternalProvider(AttestationProvider):
        tee_type = TEEType.GPU_CC

        def generate_quote(self, report_data=b""):
            raise NotImplementedError

        def verify_quote(self, quote):
            return AttestationResult(
                quote_id=quote.quote_id,
                status=AttestationStatus.VERIFIED,
                tee_type=quote.tee_type,
                measurement=quote.measurement,
                verified=True,
            )

    settings = get_settings()
    monkeypatch.setattr(settings, "ALLOW_SIMULATION", False)
    monkeypatch.setattr(settings, "ATTESTATION_ALLOWED_MEASUREMENTS", "")
    svc = AttestationService(policy=AttestationPolicy())
    svc.register_provider(ExternalProvider())
    quote = TEEQuote(
        quote_id="gpu-a183",
        quote_type=QuoteType.GPU_NVIDIA,
        tee_type=TEEType.GPU_CC,
        raw_bytes=b"{}",
        measurement="abc",
        report_data="00",
    )

    result = svc.verify_quote(quote)

    assert result.verified is True
    assert result.trust_root == "external"


# ── KMS reachability: the DEK gate consumes the honesty verdict ─────────────

def test_kms_withholds_key_for_local_root_quote_when_simulation_disallowed(monkeypatch):
    """A forged or simulated sgx_ecdsa-shaped quote (verifiable by anyone from
    the public constant) must not unlock a DEK in a no-simulation posture."""
    from app.services.kms_service import KMSService

    settings = get_settings()
    monkeypatch.setattr(settings, "KMS_REQUIRE_ATTESTATION", True)
    monkeypatch.setattr(settings, "ALLOW_SIMULATION", False)
    monkeypatch.setattr(settings, "ATTESTATION_ALLOWED_MEASUREMENTS", "")

    svc = KMSService()
    key_info = svc.generate_session_key("session-a183-nosim")
    quote = AttestationService().generate_quote(TEEType.SGX, b"session-a183-nosim")

    result = svc.distribute_key(
        key_info["key_id"], "session-a183-nosim", attestation=quote.raw_bytes
    )
    assert result is None


def test_kms_withholds_key_when_measurement_not_whitelisted(monkeypatch):
    """Even with simulation allowed, the prod-required measurement whitelist
    binds on the key-distribution path (previously bypassed)."""
    from app.services.kms_service import KMSService

    settings = get_settings()
    monkeypatch.setattr(settings, "KMS_REQUIRE_ATTESTATION", True)
    monkeypatch.setattr(settings, "ALLOW_SIMULATION", True)
    monkeypatch.setattr(settings, "ATTESTATION_ALLOWED_MEASUREMENTS", "deadbeef")

    svc = KMSService()
    key_info = svc.generate_session_key("session-a183-wl")
    quote = AttestationService().generate_quote(TEEType.SGX, b"session-a183-wl")

    result = svc.distribute_key(
        key_info["key_id"], "session-a183-wl", attestation=quote.raw_bytes
    )
    assert result is None


def test_kms_still_distributes_for_whitelisted_quote_with_simulation(monkeypatch):
    """Over-correction guard: the documented dev/sim flow keeps working —
    whitelisted measurement + ALLOW_SIMULATION=true ⇒ key distributes."""
    from app.services.kms_service import KMSService

    settings = get_settings()
    monkeypatch.setattr(settings, "KMS_REQUIRE_ATTESTATION", True)
    monkeypatch.setattr(settings, "ALLOW_SIMULATION", True)
    quote = AttestationService().generate_quote(TEEType.SGX, b"session-a183-ok2")
    monkeypatch.setattr(settings, "ATTESTATION_ALLOWED_MEASUREMENTS", quote.measurement)

    svc = KMSService()
    key_info = svc.generate_session_key("session-a183-ok2")

    result = svc.distribute_key(
        key_info["key_id"], "session-a183-ok2", attestation=quote.raw_bytes
    )
    assert result is not None
