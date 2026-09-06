"""KMS attestation enforcement + wrapped-key persistence tests (gap B1 + B3).

Covers the fail-closed distribution gate (CDS_KMS_REQUIRE_ATTESTATION) and
the export/import wrapped-blob roundtrip used for restart recovery.
"""
import pytest

from app.core.config import get_settings


@pytest.mark.asyncio
async def test_distribute_key_rejected_without_attestation_when_required(monkeypatch):
    from app.services.kms_service import KMSService

    monkeypatch.setattr(get_settings(), "KMS_REQUIRE_ATTESTATION", True)
    svc = KMSService()
    key_info = svc.generate_session_key("session-att-required")
    result = svc.distribute_key(key_info["key_id"], "session-att-required")
    assert result is None


@pytest.mark.asyncio
async def test_distribute_key_allowed_when_not_required(monkeypatch):
    from app.services.kms_service import KMSService

    monkeypatch.setattr(get_settings(), "KMS_REQUIRE_ATTESTATION", False)
    svc = KMSService()
    key_info = svc.generate_session_key("session-att-relaxed")
    result = svc.distribute_key(key_info["key_id"], "session-att-relaxed")
    assert result is not None


@pytest.mark.asyncio
async def test_distribute_key_rejects_invalid_attestation(monkeypatch):
    from app.services.kms_service import KMSService

    monkeypatch.setattr(get_settings(), "KMS_REQUIRE_ATTESTATION", True)
    svc = KMSService()
    key_info = svc.generate_session_key("session-att-invalid")
    result = svc.distribute_key(
        key_info["key_id"], "session-att-invalid", attestation=b"not-a-quote"
    )
    assert result is None


@pytest.mark.asyncio
async def test_distribute_key_accepts_valid_software_quote(monkeypatch):
    """A genuine software (Firecracker) quote must satisfy the gate.

    Uses the same quote-generation path as FirecrackerAdapter so the KMS
    attestation plumbing is exercised end to end.
    """
    from app.services.kms_service import KMSService
    from app.services.remote_attestation import AttestationService, TEEType

    monkeypatch.setattr(get_settings(), "KMS_REQUIRE_ATTESTATION", True)
    svc = KMSService()
    key_info = svc.generate_session_key("session-att-valid")
    quote = AttestationService().generate_quote(TEEType.FIRECRACKER, b"session-att-valid")
    result = svc.distribute_key(
        key_info["key_id"], "session-att-valid", attestation=quote.raw_bytes
    )
    assert result is not None


def test_wrapped_key_export_import_roundtrip():
    """Persisted wrapped blobs restore plaintext after a restart (gap B3)."""
    from app.services.kms_service import KMSService

    svc1 = KMSService()
    key_info = svc1.generate_session_key("session-persist")
    wrapped = svc1.export_wrapped(key_info["key_id"])
    sm2 = svc1.export_sm2_ciphertext(key_info["key_id"])
    assert wrapped is not None

    # Simulate a fresh process — a new KMS instance with no memory store
    svc2 = KMSService()
    assert svc2.get_key(key_info["key_id"]) is None
    ok = svc2.import_wrapped(key_info["key_id"], wrapped, sm2)
    assert ok is True
    assert svc2.get_key(key_info["key_id"]) == key_info["key_bytes"]


def test_import_wrapped_rejects_empty():
    from app.services.kms_service import KMSService

    svc = KMSService()
    assert svc.import_wrapped("", b"") is False
    assert svc.import_wrapped("key-x", b"") is False
