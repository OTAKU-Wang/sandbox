"""SS-13 (single-path cutover): l2c default + legacy-level gate.

Covers:
(a) create without sandbox_level -> session.sandbox_level == "l2c";
(b) legacy gate OFF + create with sandbox_level="L3" -> 400 with
    W2 detail {"reason": "legacy_level_retired"};
(c) production settings with CDS_SANDBOX_LEGACY_ENABLED=true are rejected
    at startup by validate_security_config (same style as the CUBE_DATAPLANE
    production gate in tests/test_security_config_matrix.py).
"""
import uuid

import pytest

from app.core.config import Settings, get_settings


async def _create_product(client, auth_headers) -> str:
    resp = await client.post(
        "/api/v1/data-products",
        json={"name": f"SS13-{uuid.uuid4().hex[:6]}"},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


@pytest.mark.asyncio
async def test_default_sandbox_level_is_l2c(client, auth_headers, publish_product, l2c_api_env):
    """(a) no sandbox_level in the body -> the session runs on l2c."""
    product_id = await _create_product(client, auth_headers)
    await publish_product(product_id)
    resp = await client.post(
        "/api/v1/sandbox-sessions",
        json={"data_product_id": product_id},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["sandbox_level"] == "l2c"
    # SS-13 disclosure on the session payload
    assert data["tee_policy"] == "tee_if_available"
    assert data["trust_root"] in {"none", "tdx", "sev_snp"}
    assert data["sandbox_engine"] == "cube_microvm"


@pytest.mark.asyncio
async def test_legacy_level_rejected_when_gate_off(client, auth_headers, publish_product, monkeypatch):
    """(b) legacy gate OFF -> L3 create is a 400 legacy_level_retired."""
    settings = get_settings()
    monkeypatch.setattr(settings, "SANDBOX_LEGACY_ENABLED", False)
    monkeypatch.setattr(settings, "LEGACY_L2_ENABLED", False)

    product_id = await _create_product(client, auth_headers)
    await publish_product(product_id)
    resp = await client.post(
        "/api/v1/sandbox-sessions",
        json={"data_product_id": product_id, "sandbox_level": "L3"},
        headers=auth_headers,
    )
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body["code"] == "LEGACY_LEVEL_RETIRED"
    assert body["detail"]["reason"] == "legacy_level_retired"
    assert body["detail"]["requested_level"] == "L3"


@pytest.mark.asyncio
async def test_legacy_level_allowed_when_gate_on(client, auth_headers, publish_product):
    """Gate ON (conftest pins CDS_SANDBOX_LEGACY_ENABLED=true) -> L3 still works."""
    product_id = await _create_product(client, auth_headers)
    await publish_product(product_id)
    resp = await client.post(
        "/api/v1/sandbox-sessions",
        json={"data_product_id": product_id, "sandbox_level": "L3"},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["sandbox_level"] == "L3"


def test_production_rejects_sandbox_legacy_enabled(monkeypatch):
    """(c) production with CDS_SANDBOX_LEGACY_ENABLED=true must refuse to start."""
    monkeypatch.setenv("CDS_DEBUG", "false")
    monkeypatch.setenv("CDS_JWT_SECRET_KEY", "t" * 64)
    monkeypatch.setenv("CDS_KMS_MASTER_KEY", "k" * 64)
    monkeypatch.setenv("CDS_SECCOMP_FALLBACK_ALLOWED", "false")
    monkeypatch.setenv("CDS_HSM_SOFTWARE_FALLBACK_ALLOWED", "false")
    monkeypatch.setenv("CDS_FEDERATION_JWT_KEY_REQUIRED", "true")
    monkeypatch.setenv("CDS_SM3_FALLBACK_ALLOWED", "false")
    monkeypatch.setenv("CDS_COLUMN_ENCRYPTION_KEY", "0" * 32)
    monkeypatch.setenv("CDS_MFA_SECRET_ENCRYPTION_KEY", "m" * 48)
    monkeypatch.setenv("CDS_FEDERATION_ENABLED", "false")
    monkeypatch.setenv("CDS_KMS_REQUIRE_ATTESTATION", "false")
    # D-e2e-01: escape hatch is prod-refused; satisfy it so the target gate
    # (CDS_SANDBOX_LEGACY_ENABLED) is the sole raise.
    monkeypatch.setenv("CDS_ALLOW_ADMIN_SELF_REGISTER", "false")
    monkeypatch.setenv("CDS_ADMIN_PAGES_ENABLED", "false")
    monkeypatch.setenv("CDS_RAG_EMBEDDING_BACKEND", "auto")
    monkeypatch.setenv("CDS_CUBE_DATAPLANE_ENABLED", "false")
    # validate_security_config treats "not TESTING and not PYTEST_CURRENT_TEST"
    # as production.
    monkeypatch.delenv("TESTING", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)

    s = Settings(SANDBOX_LEGACY_ENABLED=True)
    with pytest.raises(ValueError, match="CDS_SANDBOX_LEGACY_ENABLED=true is not permitted in production"):
        s.validate_security_config()
