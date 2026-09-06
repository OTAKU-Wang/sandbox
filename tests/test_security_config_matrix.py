"""Simulation / fallback explicit-switch matrix tests (gap B2/D4/B5/F2 · T12).

Covers validate_security_config warnings, the HSM software-fallback gate, the
seccomp retry gate and the KMS software-quote gate.
"""
import pytest

from app.core.config import get_settings


def test_validate_security_config_warns_on_production_fallbacks(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "DEBUG", False)
    monkeypatch.setattr(settings, "JWT_SECRET_KEY", "k" * 40)
    monkeypatch.delenv("TESTING", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)

    issues = settings.validate_security_config()
    joined = "\n".join(issues)
    assert "ALLOW_SIMULATION=true" in joined
    assert "SECCOMP_FALLBACK_ALLOWED=true" in joined
    assert "HSM_SOFTWARE_FALLBACK_ALLOWED=true" in joined


def test_validate_security_config_silent_in_debug(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "DEBUG", True)
    issues = settings.validate_security_config()
    assert not any("ALLOW_SIMULATION" in i for i in issues)


def test_hsm_software_fallback_gate(monkeypatch):
    from app.services import hsm_adapter as ha

    monkeypatch.setattr(ha.VaultTransitAdapter, "_get_client", lambda self: None)
    settings = get_settings()

    # fail-closed: fallback disabled + Vault unreachable → refuse
    monkeypatch.setattr(settings, "HSM_SOFTWARE_FALLBACK_ALLOWED", False)
    with pytest.raises(RuntimeError):
        ha.create_hsm_adapter()

    # allowed → software adapter
    monkeypatch.setattr(settings, "HSM_SOFTWARE_FALLBACK_ALLOWED", True)
    adapter = ha.create_hsm_adapter()
    assert isinstance(adapter, ha.SoftwareHSMAdapter)


def test_seccomp_fallback_helper(monkeypatch):
    from app.services.sandbox_runtime import _seccomp_fallback_allowed

    settings = get_settings()
    monkeypatch.setattr(settings, "SECCOMP_FALLBACK_ALLOWED", True)
    assert _seccomp_fallback_allowed() is True
    monkeypatch.setattr(settings, "SECCOMP_FALLBACK_ALLOWED", False)
    assert _seccomp_fallback_allowed() is False


@pytest.mark.asyncio
async def test_kms_rejects_simulation_quote_when_disallowed(monkeypatch):
    from app.services.kms_service import KMSService
    from app.services.remote_attestation import AttestationService, TEEType

    settings = get_settings()
    monkeypatch.setattr(settings, "KMS_REQUIRE_ATTESTATION", True)
    monkeypatch.setattr(settings, "ALLOW_SIMULATION", False)
    svc = KMSService()
    key_info = svc.generate_session_key("session-sim-gate")
    quote = AttestationService().generate_quote(TEEType.FIRECRACKER, b"session-sim-gate")
    result = svc.distribute_key(key_info["key_id"], "session-sim-gate", attestation=quote.raw_bytes)
    assert result is None  # software-simulated quote rejected


@pytest.mark.asyncio
async def test_kms_accepts_simulation_quote_when_allowed(monkeypatch):
    from app.services.kms_service import KMSService
    from app.services.remote_attestation import AttestationService, TEEType

    settings = get_settings()
    monkeypatch.setattr(settings, "KMS_REQUIRE_ATTESTATION", True)
    monkeypatch.setattr(settings, "ALLOW_SIMULATION", True)
    svc = KMSService()
    key_info = svc.generate_session_key("session-sim-ok")
    quote = AttestationService().generate_quote(TEEType.FIRECRACKER, b"session-sim-ok")
    result = svc.distribute_key(key_info["key_id"], "session-sim-ok", attestation=quote.raw_bytes)
    assert result is not None
