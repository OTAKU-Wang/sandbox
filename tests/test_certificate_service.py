"""Tests for Certificate Service."""
import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

from app.services.certificate_service import CertificateService
from app.models.certificate import CertType, CertStatus


@pytest.fixture
def service():
    return CertificateService()


class TestCertificateService:
    @pytest.mark.asyncio
    async def test_issue_certificate(self, service):
        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=mock_result)

        with patch("app.services.certificate_service.audit_service") as mock_audit:
            mock_audit.log = AsyncMock()

            cert = await service.issue_certificate(
                mock_db, uuid.uuid4(), CertType.IDENTITY.value, "CN=test,O=CDS,C=CN"
            )
            assert cert.cert_type == CertType.IDENTITY.value
            assert cert.status == CertStatus.ACTIVE.value
            assert cert.serial_number.startswith("CDS-")
            assert "BEGIN CERTIFICATE" in cert.certificate_pem
            assert cert.is_ca is False
            parsed = service.verify_certificate(cert.certificate_pem)
            assert parsed["signature_valid"] is True

    @pytest.mark.asyncio
    async def test_issue_ca_certificate(self, service):
        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=mock_result)

        with patch("app.services.certificate_service.audit_service") as mock_audit:
            mock_audit.log = AsyncMock()

            cert = await service.issue_certificate(
                mock_db, uuid.uuid4(), CertType.PLATFORM.value
            )
            assert cert.is_ca is True
            parsed = service.verify_certificate(cert.certificate_pem)
            assert parsed["signature_valid"] is True

    @pytest.mark.asyncio
    async def test_revoke_certificate(self, service):
        mock_db = AsyncMock()
        cert = MagicMock()
        cert.id = uuid.uuid4()
        cert.status = CertStatus.ACTIVE.value
        cert.serial_number = "CDS-TEST123"

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = cert
        mock_db.execute = AsyncMock(return_value=mock_result)

        with patch("app.services.certificate_service.audit_service") as mock_audit:
            mock_audit.log = AsyncMock()
            result = await service.revoke_certificate(mock_db, cert.id, "key_compromise")
            assert result.status == CertStatus.REVOKED.value
            assert result.revoke_reason == "key_compromise"
            assert result.revoked_at is not None

    @pytest.mark.asyncio
    async def test_revoke_already_revoked(self, service):
        mock_db = AsyncMock()
        cert = MagicMock()
        cert.id = uuid.uuid4()
        cert.status = CertStatus.REVOKED.value

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = cert
        mock_db.execute = AsyncMock(return_value=mock_result)

        with pytest.raises(ValueError, match="already revoked"):
            await service.revoke_certificate(mock_db, cert.id)

    @pytest.mark.asyncio
    async def test_validate_valid_cert(self, service):
        mock_db = AsyncMock()
        cert = MagicMock()
        cert.id = uuid.uuid4()
        cert.status = CertStatus.ACTIVE.value
        cert.not_before = datetime(2020, 1, 1, tzinfo=timezone.utc)
        cert.not_after = datetime(2030, 1, 1, tzinfo=timezone.utc)

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = cert
        mock_db.execute = AsyncMock(return_value=mock_result)

        valid, reason = await service.validate_certificate(mock_db, cert.id)
        assert valid is True

    @pytest.mark.asyncio
    async def test_validate_expired_cert(self, service):
        mock_db = AsyncMock()
        cert = MagicMock()
        cert.id = uuid.uuid4()
        cert.status = CertStatus.ACTIVE.value
        cert.not_before = datetime(2020, 1, 1, tzinfo=timezone.utc)
        cert.not_after = datetime(2021, 1, 1, tzinfo=timezone.utc)

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = cert
        mock_db.execute = AsyncMock(return_value=mock_result)

        valid, reason = await service.validate_certificate(mock_db, cert.id)
        assert valid is False
        assert "expired" in reason.lower()

    @pytest.mark.asyncio
    async def test_validate_revoked_cert(self, service):
        mock_db = AsyncMock()
        cert = MagicMock()
        cert.id = uuid.uuid4()
        cert.status = CertStatus.REVOKED.value
        cert.revoke_reason = "key_compromise"
        cert.not_before = datetime(2020, 1, 1, tzinfo=timezone.utc)
        cert.not_after = datetime(2030, 1, 1, tzinfo=timezone.utc)

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = cert
        mock_db.execute = AsyncMock(return_value=mock_result)

        valid, reason = await service.validate_certificate(mock_db, cert.id)
        assert valid is False
        assert "revoked" in reason.lower()

    @pytest.mark.asyncio
    async def test_generate_crl(self, service):
        mock_db = AsyncMock()
        cert1 = MagicMock()
        cert1.serial_number = "CDS-001"
        cert1.revoked_at = datetime.now(timezone.utc)
        cert1.revoke_reason = "key_compromise"
        cert1.issuer = "CN=CDS Root CA,O=CDS,C=CN"

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [cert1]
        mock_db.execute = AsyncMock(return_value=mock_result)

        with patch("app.services.certificate_service.crypto_service") as mock_crypto:
            mock_crypto.generate_keypair.return_value = MagicMock(
                private_key="a" * 64,
                public_key="b" * 128,
            )
            mock_crypto.sign.return_value = MagicMock(signature="sig123")

            crl = await service.generate_crl(mock_db)
            assert len(crl["revoked_certificates"]) == 1
            assert crl["revoked_certificates"][0]["serial"] == "CDS-001"
            assert "signature" in crl
