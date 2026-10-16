"""Simulation / fallback explicit-switch matrix tests (gap B2/D4/B5/F2 · T12).

Covers validate_security_config warnings, the HSM software-fallback gate, the
seccomp retry gate and the KMS software-quote gate.
"""
import pytest

from app.core.config import get_settings


def test_validate_security_config_warns_on_hardware_gated_fallbacks(monkeypatch):
    """T12: ALLOW_SIMULATION=true in prod is a WARN, not a crash.

    The PZ-04/PZ-05 gates are additionally tightened to compliant values so
    this test isolates the ALLOW_SIMULATION warning semantics.
    """
    settings = get_settings()
    monkeypatch.setattr(settings, "DEBUG", False)
    monkeypatch.setattr(settings, "JWT_SECRET_KEY", "k" * 40)
    monkeypatch.setattr(settings, "SECCOMP_FALLBACK_ALLOWED", False)
    monkeypatch.setattr(settings, "HSM_SOFTWARE_FALLBACK_ALLOWED", False)
    monkeypatch.setattr(settings, "FEDERATION_JWT_KEY_REQUIRED", True)
    monkeypatch.setattr(settings, "SM3_FALLBACK_ALLOWED", False)
    monkeypatch.setattr(settings, "COLUMN_ENCRYPTION_KEY", "00112233445566778899aabbccddeeff")
    monkeypatch.setattr(settings, "MFA_SECRET_ENCRYPTION_KEY", "mfa-test-master-secret")
    # D-e2e-01: the admin self-register escape hatch (suite env pins it true) is
    # refused in production — satisfy it so only the targeted gate raises.
    monkeypatch.setattr(settings, "ALLOW_ADMIN_SELF_REGISTER", False)
    monkeypatch.setattr(settings, "FEDERATION_ENABLED", False)
    monkeypatch.setattr(settings, "KMS_REQUIRE_ATTESTATION", False)
    monkeypatch.delenv("TESTING", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)

    issues = settings.validate_security_config()
    joined = "\n".join(issues)
    assert "ALLOW_SIMULATION=true" in joined
    assert joined.count("ALLOW_SIMULATION=true") == 1  # exactly one warning, no raises


def test_validate_security_config_raises_on_seccomp_fallback_in_prod(monkeypatch):
    """Seccomp fallback is a pure degradation — production MUST fail closed."""
    settings = get_settings()
    monkeypatch.setattr(settings, "DEBUG", False)
    monkeypatch.setattr(settings, "JWT_SECRET_KEY", "k" * 40)
    monkeypatch.setattr(settings, "HSM_SOFTWARE_FALLBACK_ALLOWED", False)
    monkeypatch.delenv("TESTING", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    with pytest.raises(ValueError, match="SECCOMP_FALLBACK_ALLOWED"):
        settings.validate_security_config()


def test_validate_security_config_raises_on_hsm_software_fallback_in_prod(monkeypatch):
    """Software KEK/signing fallback is a pure degradation — prod fails closed."""
    settings = get_settings()
    monkeypatch.setattr(settings, "DEBUG", False)
    monkeypatch.setattr(settings, "JWT_SECRET_KEY", "k" * 40)
    monkeypatch.setattr(settings, "SECCOMP_FALLBACK_ALLOWED", False)
    monkeypatch.delenv("TESTING", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    with pytest.raises(ValueError, match="HSM_SOFTWARE_FALLBACK_ALLOWED"):
        settings.validate_security_config()


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


def _prod_baseline(monkeypatch):
    """Compliant production baseline (SS-11 §7.1); each gate test relaxes one knob."""
    settings = get_settings()
    monkeypatch.setattr(settings, "DEBUG", False)
    monkeypatch.setattr(settings, "JWT_SECRET_KEY", "k" * 40)
    monkeypatch.setattr(settings, "SECCOMP_FALLBACK_ALLOWED", False)
    monkeypatch.setattr(settings, "HSM_SOFTWARE_FALLBACK_ALLOWED", False)
    monkeypatch.setattr(settings, "ALLOW_SIMULATION", False)
    monkeypatch.setattr(settings, "FEDERATION_JWT_KEY_REQUIRED", True)
    monkeypatch.setattr(settings, "SM3_FALLBACK_ALLOWED", False)
    monkeypatch.setattr(settings, "COLUMN_ENCRYPTION_KEY", "00112233445566778899aabbccddeeff")
    monkeypatch.setattr(settings, "MFA_SECRET_ENCRYPTION_KEY", "mfa-test-master-secret")
    # D-e2e-01: the admin self-register escape hatch (suite env pins it true for
    # fixture helpers) is refused in production — satisfy it in the baseline.
    monkeypatch.setattr(settings, "ALLOW_ADMIN_SELF_REGISTER", False)
    monkeypatch.setattr(settings, "FEDERATION_ENABLED", False)
    monkeypatch.setattr(settings, "KMS_REQUIRE_ATTESTATION", False)
    monkeypatch.delenv("TESTING", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    return settings


def test_prod_sm3_fallback_allowed_raises(monkeypatch):
    """PZ-04/MR-A14: the SM3→SHA-256 fallback may never be armed in production."""
    settings = _prod_baseline(monkeypatch)
    monkeypatch.setattr(settings, "SM3_FALLBACK_ALLOWED", True)
    with pytest.raises(ValueError, match="SM3_FALLBACK_ALLOWED"):
        settings.validate_security_config()


def test_prod_column_encryption_key_empty_raises(monkeypatch):
    """PZ-04/MR-A9: the built-in dev DEK must never encrypt production data."""
    settings = _prod_baseline(monkeypatch)
    monkeypatch.setattr(settings, "COLUMN_ENCRYPTION_KEY", "")
    with pytest.raises(ValueError, match="COLUMN_ENCRYPTION_KEY"):
        settings.validate_security_config()


def test_prod_federation_jwt_key_not_required_raises(monkeypatch):
    """PZ-04/MR-A5: static federation JWT key was WARN-only; now a hard RAISE."""
    settings = _prod_baseline(monkeypatch)
    monkeypatch.setattr(settings, "FEDERATION_JWT_KEY_REQUIRED", False)
    with pytest.raises(ValueError, match="FEDERATION_JWT_KEY_REQUIRED"):
        settings.validate_security_config()


def test_prod_federation_enabled_without_mtls_material_raises(monkeypatch):
    """PZ-05/MR-A8: enabled federation needs real cert/key/CA in prod."""
    settings = _prod_baseline(monkeypatch)
    monkeypatch.setattr(settings, "FEDERATION_ENABLED", True)
    with pytest.raises(ValueError, match="Federation"):
        settings.validate_security_config()


def test_prod_federation_enabled_with_mtls_material_passes(monkeypatch):
    settings = _prod_baseline(monkeypatch)
    monkeypatch.setattr(settings, "FEDERATION_ENABLED", True)
    monkeypatch.setattr(settings, "FEDERATION_CA_BUNDLE", "/etc/cds/federation/ca-bundle.pem")
    monkeypatch.setattr(settings, "FEDERATION_CERT", "/etc/cds/federation/cert.pem")
    monkeypatch.setattr(settings, "FEDERATION_KEY", "/etc/cds/federation/key.pem")
    issues = settings.validate_security_config()
    assert not any("Federation" in i for i in issues)


def test_prod_attestation_measurements_empty_raises(monkeypatch):
    """PZ-05/MR-B1: an empty measurement whitelist is not a production posture."""
    settings = _prod_baseline(monkeypatch)
    monkeypatch.setattr(settings, "KMS_REQUIRE_ATTESTATION", True)
    monkeypatch.setattr(settings, "ATTESTATION_ALLOWED_MEASUREMENTS", "")
    with pytest.raises(ValueError, match="ATTESTATION_ALLOWED_MEASUREMENTS"):
        settings.validate_security_config()


def test_prod_attestation_measurements_set_passes(monkeypatch):
    settings = _prod_baseline(monkeypatch)
    monkeypatch.setattr(settings, "KMS_REQUIRE_ATTESTATION", True)
    monkeypatch.setattr(settings, "ATTESTATION_ALLOWED_MEASUREMENTS", "aa11,bb22")
    issues = settings.validate_security_config()
    assert not any("ATTESTATION" in i for i in issues)


def test_debug_mode_pz04_pz05_gates_are_inert(monkeypatch):
    """Dev keeps the relaxed defaults working (fallbacks, dev DEK, no mTLS)."""
    settings = get_settings()
    monkeypatch.setattr(settings, "DEBUG", True)
    monkeypatch.setattr(settings, "JWT_SECRET_KEY", "k" * 40)
    monkeypatch.setattr(settings, "SM3_FALLBACK_ALLOWED", True)
    monkeypatch.setattr(settings, "COLUMN_ENCRYPTION_KEY", "")
    monkeypatch.setattr(settings, "FEDERATION_JWT_KEY_REQUIRED", False)
    monkeypatch.setattr(settings, "FEDERATION_ENABLED", True)
    # no raise, and none of the PZ-04/PZ-05 gates fire in dev
    issues = settings.validate_security_config()
    joined = "\n".join(issues)
    for token in ("SM3", "COLUMN_ENCRYPTION", "Federation", "ATTESTATION"):
        assert token not in joined


def test_tee_simulation_mode_key_removed():
    """PZ-04/MR-A6: the dead TEE_SIMULATION_MODE config key is gone (M-06)."""
    from app.core.config import Settings

    settings = get_settings()
    assert not hasattr(settings, "TEE_SIMULATION_MODE")
    assert "TEE_SIMULATION_MODE" not in Settings.model_fields


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


# ── PZ-10 (spec ss-11): auth enhancement config matrix ───────────────

def test_pz10_auth_cert_login_default_true():
    """SM2 cert login track is additive and on by default."""
    from app.core.config import Settings

    settings = get_settings()
    assert settings.AUTH_CERT_LOGIN is True
    assert "AUTH_CERT_LOGIN" in Settings.model_fields


def test_pz10_mfa_defaults():
    """MFA ships enabled with RFC 6238 defaults and 8 one-shot recovery codes."""
    settings = get_settings()
    assert settings.AUTH_MFA_ENABLED is True
    assert settings.MFA_TOTP_PERIOD == 30
    assert settings.MFA_TOTP_DIGITS == 6
    assert settings.MFA_RECOVERY_CODE_COUNT == 8
    assert settings.MFA_SECRET_ENCRYPTION_KEY == ""  # dev fallback; prod gate below


def test_prod_mfa_secret_key_empty_raises(monkeypatch):
    """The dev fallback (JWT secret) must never encrypt production MFA secrets."""
    settings = _prod_baseline(monkeypatch)
    monkeypatch.setattr(settings, "MFA_SECRET_ENCRYPTION_KEY", "")
    with pytest.raises(ValueError, match="MFA_SECRET_ENCRYPTION_KEY"):
        settings.validate_security_config()


def test_prod_mfa_secret_key_set_passes(monkeypatch):
    settings = _prod_baseline(monkeypatch)  # baseline already sets the key
    issues = settings.validate_security_config()
    assert not any("MFA_SECRET" in i for i in issues)
