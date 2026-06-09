"""TLCP (国密 TLS) service and API tests."""
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_generate_signing_certificate(client: AsyncClient, operator_headers: dict):
    """Generate an SM2 signing certificate."""
    resp = await client.post("/api/v1/certificates/generate", json={
        "subject": "Test Sign Cert",
        "cert_type": "signing",
        "validity_days": 365,
    }, headers=operator_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["subject"] == "Test Sign Cert"
    assert data["cert_type"] == "signing"
    assert data["cert_id"]
    assert data["fingerprint"]
    assert "BEGIN CERTIFICATE" in data["cert_pem"]
    assert "BEGIN EC PRIVATE KEY" in data["key_pem"]


@pytest.mark.asyncio
async def test_generate_encryption_certificate(client: AsyncClient, operator_headers: dict):
    """Generate an SM2 encryption certificate."""
    resp = await client.post("/api/v1/certificates/generate", json={
        "subject": "Test Enc Cert",
        "cert_type": "encryption",
    }, headers=operator_headers)
    assert resp.status_code == 200
    assert resp.json()["cert_type"] == "encryption"


@pytest.mark.asyncio
async def test_list_certificates(client: AsyncClient, operator_headers: dict):
    """List certificates after generating one."""
    await client.post("/api/v1/certificates/generate", json={
        "subject": "List Test",
    }, headers=operator_headers)
    resp = await client.get("/api/v1/certificates/list", headers=operator_headers)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_get_certificate_by_id(client: AsyncClient, operator_headers: dict):
    """Get certificate by ID."""
    gen_resp = await client.post("/api/v1/certificates/generate", json={
        "subject": "Get Test",
    }, headers=operator_headers)
    cert_id = gen_resp.json()["cert_id"]
    resp = await client.get(f"/api/v1/certificates/{cert_id}", headers=operator_headers)
    assert resp.status_code == 200
    assert resp.json()["cert_id"] == cert_id


@pytest.mark.asyncio
async def test_get_nonexistent_certificate(client: AsyncClient, operator_headers: dict):
    """Get nonexistent certificate returns 404."""
    resp = await client.get("/api/v1/certificates/nonexistent", headers=operator_headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_revoke_certificate(client: AsyncClient, make_user):
    """Revoke a certificate (admin only)."""
    admin_headers, _ = await make_user("admin", "revoke")
    gen_resp = await client.post("/api/v1/certificates/generate", json={
        "subject": "Revoke Test",
    }, headers=admin_headers)
    cert_id = gen_resp.json()["cert_id"]
    resp = await client.delete(f"/api/v1/certificates/{cert_id}", headers=admin_headers)
    assert resp.status_code == 200
    assert resp.json()["revoked"] is True

    # Verify it's gone
    resp2 = await client.get(f"/api/v1/certificates/{cert_id}", headers=admin_headers)
    assert resp2.status_code == 404


@pytest.mark.asyncio
async def test_ssl_context_info(client: AsyncClient, operator_headers: dict):
    """Get SSL context information."""
    resp = await client.get("/api/v1/certificates/ssl/context", headers=operator_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "gmssl_available" in data
    assert "protocol" in data
    assert "cipher_suites" in data


@pytest.mark.asyncio
async def test_buyer_cannot_generate_cert(client: AsyncClient, auth_headers: dict):
    """Buyer cannot generate certificates."""
    resp = await client.post("/api/v1/certificates/generate", json={
        "subject": "Unauthorized",
    }, headers=auth_headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_no_auth_cannot_list(client: AsyncClient):
    """Unauthenticated cannot list certificates."""
    resp = await client.get("/api/v1/certificates/list")
    assert resp.status_code in (401, 403)


def test_tlcp_build_ssl_context():
    """Unit test: build SSL context."""
    from app.services.tlcp_service import TLCPService
    svc = TLCPService(cert_store_path="/tmp/test-tlcp-ctx")
    ctx = svc.build_ssl_context()
    assert ctx is not None
    assert ctx.verify_mode is not None


def test_tlcp_generate_certificate():
    """Unit test: generate SM2 certificate."""
    from app.services.tlcp_service import TLCPService
    svc = TLCPService(cert_store_path="/tmp/test-tlcp-gen")
    cert = svc.generate_certificate(subject="Unit Test", cert_type="signing")
    assert cert.cert_id
    assert cert.subject == "Unit Test"
    assert cert.cert_type == "signing"
    assert cert.fingerprint
    assert cert.public_key
    assert "BEGIN CERTIFICATE" in cert.cert_pem
    assert "BEGIN EC PRIVATE KEY" in cert.key_pem
    verification = svc.verify_certificate(cert.cert_pem)
    assert verification["valid"] is True
    assert verification["fingerprint"] == cert.fingerprint


def test_tlcp_list_certificates():
    """Unit test: list certificates."""
    from app.services.tlcp_service import TLCPService
    svc = TLCPService(cert_store_path="/tmp/test-tlcp-list")
    certs = svc.list_certificates()
    assert isinstance(certs, list)


def test_tlcp_revoke_nonexistent():
    """Unit test: revoke nonexistent returns False."""
    from app.services.tlcp_service import TLCPService
    svc = TLCPService(cert_store_path="/tmp/test-tlcp-revoke")
    assert svc.revoke_certificate("nonexistent") is False
