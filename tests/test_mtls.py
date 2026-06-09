"""I3 mTLS tests — SM2 CA, certificate issuance, verification, identity management."""
import pytest
from datetime import datetime, timezone, timedelta

from app.services.mutual_tls import (
    SM2CertificateAuthority,
    MTLSManager,
    CertificateInfo,
    MTLSIdentity,
)


class TestSM2CertificateAuthority:
    """Tests for SM2CertificateAuthority."""

    def test_ca_initializes_with_sm2(self):
        ca = SM2CertificateAuthority()
        assert ca.available is True

    def test_generate_ca_certificate_self_signed(self):
        ca = SM2CertificateAuthority()
        cert = ca.generate_ca_certificate()
        assert cert.is_ca is True
        assert cert.subject == "CDS Federation CA"
        assert cert.issuer == cert.subject  # self-signed
        assert cert.serial_number  # non-empty
        assert cert.public_key  # non-empty
        assert cert.signature  # non-empty
        assert cert.not_after > cert.not_before

    def test_generate_ca_certificate_custom_subject(self):
        ca = SM2CertificateAuthority()
        cert = ca.generate_ca_certificate(subject="Custom CA Name")
        assert cert.subject == "Custom CA Name"
        assert cert.issuer == "Custom CA Name"

    def test_ca_certificate_10_year_validity(self):
        ca = SM2CertificateAuthority()
        cert = ca.generate_ca_certificate()
        delta = cert.not_after - cert.not_before
        assert delta.days == 3650  # 10 years

    def test_issue_certificate_for_space(self):
        ca = SM2CertificateAuthority()
        ca_cert = ca.generate_ca_certificate()
        keypair = ca._crypto.generate_keypair()

        cert = ca.issue_certificate("space-alpha", keypair.public_key)
        assert cert.is_ca is False
        assert cert.space_id == "space-alpha"
        assert cert.issuer == "CDS Federation CA"
        assert cert.public_key == keypair.public_key
        assert cert.not_after > datetime.now(timezone.utc)

    def test_issue_certificate_custom_validity(self):
        ca = SM2CertificateAuthority()
        keypair = ca._crypto.generate_keypair()
        cert = ca.issue_certificate("space-beta", keypair.public_key, validity_days=30)
        delta = cert.not_after - cert.not_before
        assert delta.days == 30

    def test_verify_certificate_valid(self):
        ca = SM2CertificateAuthority()
        ca_cert = ca.generate_ca_certificate()
        keypair = ca._crypto.generate_keypair()
        cert = ca.issue_certificate("space-gamma", keypair.public_key)

        assert ca.verify_certificate(cert, ca_cert) is True

    def test_verify_certificate_tampered_signature(self):
        ca = SM2CertificateAuthority()
        ca_cert = ca.generate_ca_certificate()
        keypair = ca._crypto.generate_keypair()
        cert = ca.issue_certificate("space-delta", keypair.public_key)

        # Tamper with signature — invalid hex causes ValueError, which is a verification failure
        cert.signature = "ff" * 64  # valid hex, correct length, but wrong signature
        assert ca.verify_certificate(cert, ca_cert) is False

    def test_verify_certificate_tampered_subject(self):
        ca = SM2CertificateAuthority()
        ca_cert = ca.generate_ca_certificate()
        keypair = ca._crypto.generate_keypair()
        cert = ca.issue_certificate("space-epsilon", keypair.public_key)

        # Tamper with subject (changes signed payload)
        cert.subject = "EVIL SUBJECT"
        assert ca.verify_certificate(cert, ca_cert) is False

    def test_verify_expired_certificate(self):
        ca = SM2CertificateAuthority()
        ca_cert = ca.generate_ca_certificate()
        keypair = ca._crypto.generate_keypair()
        cert = ca.issue_certificate("space-zeta", keypair.public_key)

        # Force expiry
        cert.not_after = datetime.now(timezone.utc) - timedelta(days=1)
        assert ca.verify_certificate(cert, ca_cert) is False

    def test_verify_not_yet_valid_certificate(self):
        ca = SM2CertificateAuthority()
        ca_cert = ca.generate_ca_certificate()
        keypair = ca._crypto.generate_keypair()
        cert = ca.issue_certificate("space-eta", keypair.public_key)

        # Force not-before to future
        cert.not_before = datetime.now(timezone.utc) + timedelta(days=1)
        assert ca.verify_certificate(cert, ca_cert) is False

    def test_issue_without_ca_raises(self):
        ca = SM2CertificateAuthority()
        ca._crypto = None  # simulate unavailable
        keypair = "dummy"
        with pytest.raises(RuntimeError, match="SM2 not available"):
            ca.issue_certificate("space-x", keypair)

    def test_generate_ca_without_sm2_raises(self):
        ca = SM2CertificateAuthority()
        ca._crypto = None
        with pytest.raises(RuntimeError, match="SM2 not available"):
            ca.generate_ca_certificate()

    def test_unique_serial_numbers(self):
        ca = SM2CertificateAuthority()
        cert1 = ca.generate_ca_certificate()
        cert2 = ca.generate_ca_certificate()
        assert cert1.serial_number != cert2.serial_number

    def test_unique_signatures_per_cert(self):
        ca = SM2CertificateAuthority()
        keypair = ca._crypto.generate_keypair()
        cert1 = ca.issue_certificate("s1", keypair.public_key)
        cert2 = ca.issue_certificate("s2", keypair.public_key)
        assert cert1.signature != cert2.signature


class TestMTLSManager:
    """Tests for MTLSManager."""

    def test_manager_initializes(self):
        mgr = MTLSManager()
        assert mgr.available is True

    def test_initialize_ca(self):
        mgr = MTLSManager()
        ca_cert = mgr.initialize_ca()
        assert ca_cert.is_ca is True
        assert mgr._ca_cert is ca_cert

    def test_initialize_ca_idempotent(self):
        mgr = MTLSManager()
        cert1 = mgr.initialize_ca()
        cert2 = mgr.initialize_ca()
        assert cert1 is cert2  # same object

    def test_create_identity(self):
        mgr = MTLSManager()
        identity = mgr.create_identity("space-1")
        assert isinstance(identity, MTLSIdentity)
        assert identity.space_id == "space-1"
        assert identity.private_key
        assert identity.public_key
        assert identity.certificate.is_ca is False
        assert identity.certificate.space_id == "space-1"
        assert identity.ca_certificate is not None

    def test_create_identity_auto_initializes_ca(self):
        mgr = MTLSManager()
        assert mgr._ca_cert is None
        mgr.create_identity("space-2")
        assert mgr._ca_cert is not None

    def test_get_identity(self):
        mgr = MTLSManager()
        identity = mgr.create_identity("space-3")
        assert mgr.get_identity("space-3") is identity

    def test_get_identity_missing(self):
        mgr = MTLSManager()
        assert mgr.get_identity("nonexistent") is None

    def test_verify_peer_certificate(self):
        mgr = MTLSManager()
        identity = mgr.create_identity("space-4")
        assert mgr.verify_peer_certificate(identity.certificate) is True

    def test_verify_peer_certificate_wrong_ca(self):
        mgr1 = MTLSManager()
        mgr2 = MTLSManager()
        identity = mgr1.create_identity("space-5")
        # mgr2 has a different CA, should fail
        assert mgr2.verify_peer_certificate(identity.certificate) is False

    def test_verify_peer_without_ca(self):
        mgr = MTLSManager()
        cert = CertificateInfo(
            subject="test", issuer="test", serial_number="123",
            not_before=datetime.now(timezone.utc),
            not_after=datetime.now(timezone.utc) + timedelta(days=1),
            public_key="key", signature="sig",
        )
        assert mgr.verify_peer_certificate(cert) is False

    def test_get_tls_context(self):
        mgr = MTLSManager()
        identity = mgr.create_identity("space-6")
        ctx = mgr.get_tls_context("space-6")
        assert ctx is not None
        assert ctx["space_id"] == "space-6"
        assert "cert_pem" in ctx
        assert "key_der" in ctx
        assert "ca_cert_pem" in ctx
        assert ctx["verify"] is True

    def test_get_tls_context_missing(self):
        mgr = MTLSManager()
        assert mgr.get_tls_context("nonexistent") is None

    def test_multiple_spaces_independent(self):
        mgr = MTLSManager()
        id_a = mgr.create_identity("space-a")
        id_b = mgr.create_identity("space-b")
        assert id_a.private_key != id_b.private_key
        assert id_a.certificate.serial_number != id_b.certificate.serial_number
        # Cross-verify: space-a cert verifiable by manager CA
        assert mgr.verify_peer_certificate(id_a.certificate) is True
        assert mgr.verify_peer_certificate(id_b.certificate) is True

    def test_cert_to_pem_format(self):
        mgr = MTLSManager()
        identity = mgr.create_identity("space-pem")
        ctx = mgr.get_tls_context("space-pem")
        pem = ctx["cert_pem"]
        assert "-----BEGIN CERTIFICATE-----" in pem
        assert "-----END CERTIFICATE-----" in pem
        assert "Subject:" in pem
        assert "PublicKey:" in pem
