"""PZ-05 federation / attestation gates (spec ss-11 §3.1 PZ-05; MR-A8 + MR-B1).

Covers:
- federation mTLS material loader (real PEM validation, partial config, invalid files)
- FederationConnector fail-closed: disabled channel and prod-without-material raise
- attestation results expose trust_root (local-simulated for the built-in local
  static root, external by default for custom providers)
"""
import pytest

from app.core.config import get_settings


# ─────────────────── federation mTLS material (MR-A8) ──────────────────────

def test_mtls_material_none_when_unconfigured(monkeypatch):
    from app.services.mutual_tls import load_federation_mtls_material

    settings = get_settings()
    monkeypatch.setattr(settings, "FEDERATION_CA_BUNDLE", "")
    monkeypatch.setattr(settings, "FEDERATION_CERT", "")
    monkeypatch.setattr(settings, "FEDERATION_KEY", "")
    assert load_federation_mtls_material() is None


def test_mtls_material_partial_config_raises(monkeypatch):
    from app.services.mutual_tls import load_federation_mtls_material

    settings = get_settings()
    monkeypatch.setattr(settings, "FEDERATION_CA_BUNDLE", "")
    monkeypatch.setattr(settings, "FEDERATION_CERT", "/etc/ssl/certs/ca-certificates.crt")
    monkeypatch.setattr(settings, "FEDERATION_KEY", "")
    with pytest.raises(ValueError, match="partially configured"):
        load_federation_mtls_material()


def test_mtls_material_missing_file_raises(monkeypatch, tmp_path):
    from app.services.mutual_tls import load_federation_mtls_material

    settings = get_settings()
    monkeypatch.setattr(settings, "FEDERATION_CA_BUNDLE", str(tmp_path / "ca.pem"))
    monkeypatch.setattr(settings, "FEDERATION_CERT", str(tmp_path / "cert.pem"))
    monkeypatch.setattr(settings, "FEDERATION_KEY", str(tmp_path / "key.pem"))
    with pytest.raises(ValueError, match="not found"):
        load_federation_mtls_material()


def test_mtls_material_invalid_pem_raises(monkeypatch, tmp_path):
    from app.services.mutual_tls import load_federation_mtls_material

    ca = tmp_path / "ca.pem"
    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    ca.write_text("not a pem\n")
    cert.write_text("-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----\n")
    key.write_text("-----BEGIN PRIVATE KEY-----\nAAAA\n-----END PRIVATE KEY-----\n")
    settings = get_settings()
    monkeypatch.setattr(settings, "FEDERATION_CA_BUNDLE", str(ca))
    monkeypatch.setattr(settings, "FEDERATION_CERT", str(cert))
    monkeypatch.setattr(settings, "FEDERATION_KEY", str(key))
    with pytest.raises(ValueError, match="CA bundle"):
        load_federation_mtls_material()


def _make_self_signed_pair(tmp_path):
    """Generate a real self-signed cert/key pair with the stdlib ssl-friendly
    cryptography-free path: openssl if available, else skip marker."""
    import shutil
    import subprocess

    if shutil.which("openssl") is None:
        pytest.skip("openssl not available")
    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
         "-keyout", str(key), "-out", str(cert), "-days", "2",
         "-subj", "/CN=cds-federation-test"],
        check=True, capture_output=True,
    )
    return cert, key


def test_mtls_material_valid_pair_loads(monkeypatch, tmp_path):
    from app.services.mutual_tls import load_federation_mtls_material

    cert, key = _make_self_signed_pair(tmp_path)
    ca = tmp_path / "ca.pem"
    ca.write_text(cert.read_text())  # self-signed: the cert IS its own CA
    settings = get_settings()
    monkeypatch.setattr(settings, "FEDERATION_CA_BUNDLE", str(ca))
    monkeypatch.setattr(settings, "FEDERATION_CERT", str(cert))
    monkeypatch.setattr(settings, "FEDERATION_KEY", str(key))
    mat = load_federation_mtls_material()
    assert mat == {"ca_bundle": str(ca), "cert": str(cert), "key": str(key)}


# ─────────────── FederationConnector fail-closed (MR-A8) ───────────────────

def _mk_request_and_trust():
    from app.services.federation_connector import (
        FederationStatus,
        FederationTrust,
        GatewayRequest,
        SpaceIdentity,
        TrustLevel,
    )

    local = SpaceIdentity(space_id="local-1", space_name="Local", endpoint="http://local:8000")
    remote = SpaceIdentity(space_id="remote-1", space_name="Remote", endpoint="https://remote.example")
    trust = FederationTrust(
        trust_id="trust-1",
        local_space=local,
        remote_space=remote,
        trust_level=TrustLevel.VERIFIED,
        status=FederationStatus.ACTIVE,
        allowed_operations=["read"],
    )
    request = GatewayRequest(request_id="req-1", source_space="local-1",
                             target_space="remote-1", operation="read",
                             resource="/data/products")
    return request, trust


def _prod_settings(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "DEBUG", False)
    monkeypatch.delenv("TESTING", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    return settings


def test_federation_disabled_channel_refuses(monkeypatch):
    from app.services.federation_connector import federation_connector

    settings = get_settings()
    monkeypatch.setattr(settings, "FEDERATION_ENABLED", False)
    request, trust = _mk_request_and_trust()
    with pytest.raises(RuntimeError, match="FEDERATION_ENABLED"):
        federation_connector._execute_remote_request(request, trust)


def test_federation_prod_without_mtls_material_raises(monkeypatch):
    from app.services.federation_connector import federation_connector

    settings = _prod_settings(monkeypatch)
    monkeypatch.setattr(settings, "FEDERATION_ENABLED", True)
    monkeypatch.setattr(settings, "FEDERATION_CA_BUNDLE", "")
    monkeypatch.setattr(settings, "FEDERATION_CERT", "")
    monkeypatch.setattr(settings, "FEDERATION_KEY", "")
    request, trust = _mk_request_and_trust()
    with pytest.raises(RuntimeError, match="mTLS"):
        federation_connector._execute_remote_request(request, trust)


def test_federation_dev_without_material_still_works(monkeypatch):
    """Dev: channel with no mTLS material keeps working (plain verified TLS)."""
    import httpx

    from app.services.federation_connector import federation_connector

    settings = get_settings()  # TESTING=1 / DEBUG default: not prod
    monkeypatch.setattr(settings, "FEDERATION_ENABLED", True)
    monkeypatch.setattr(settings, "FEDERATION_CA_BUNDLE", "")
    monkeypatch.setattr(settings, "FEDERATION_CERT", "")
    monkeypatch.setattr(settings, "FEDERATION_KEY", "")

    original_client = httpx.Client

    def fake_client(*args, **kwargs):
        resp = httpx.Response(200, json={"ok": True}, request=httpx.Request("POST", "https://remote.example/x"))
        client = original_client(*args, **kwargs)
        client.post = lambda *a, **kw: resp
        return client

    monkeypatch.setattr(httpx, "Client", fake_client)
    request, trust = _mk_request_and_trust()
    response = federation_connector._execute_remote_request(request, trust)
    assert response.status_code == 200


# ─────────────── attestation trust_root disclosure (MR-B1) ─────────────────

@pytest.mark.asyncio
async def test_local_quotes_report_local_simulated_trust_root():
    from app.services.remote_attestation import (
        AttestationPolicy,
        AttestationService,
        TEEType,
    )

    svc = AttestationService(policy=AttestationPolicy())
    for tee in (TEEType.SGX, TEEType.SEV_SNP, TEEType.FIRECRACKER):
        quote = svc.generate_quote(tee, b"session-trust-root")
        result = svc.verify_quote(quote)
        assert result.verified is True, tee
        assert result.trust_root == "local-simulated", tee


def test_custom_provider_result_defaults_to_external_trust_root():
    from app.services.remote_attestation import (
        AttestationPolicy,
        AttestationProvider,
        AttestationResult,
        AttestationService,
        AttestationStatus,
        QuoteType,
        TEEQuote,
        TEEType,
    )

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

    svc = AttestationService(policy=AttestationPolicy())
    svc.register_provider(ExternalProvider())
    quote = TEEQuote(
        quote_id="gpu-1",
        quote_type=QuoteType.GPU_NVIDIA,
        tee_type=TEEType.GPU_CC,
        raw_bytes=b"{}",
        measurement="abc",
        report_data="00",
    )
    result = svc.verify_quote(quote)
    assert result.verified is True
    assert result.trust_root == "external"


def test_default_policy_loads_measurement_whitelist_from_settings(monkeypatch):
    from app.services.remote_attestation import AttestationService

    settings = get_settings()
    monkeypatch.setattr(settings, "ATTESTATION_ALLOWED_MEASUREMENTS", "aa11,bb22")
    svc = AttestationService()
    assert svc._policy.allowed_measurements == {"aa11", "bb22"}
