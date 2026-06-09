"""Certificate management API — SM2 certificate lifecycle for TLCP."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user, require_roles
from app.models.user import User, UserRole
from app.services.tlcp_service import tlcp_service
from app.services.audit_service import audit_service

router = APIRouter()


class CertGenerateRequest(BaseModel):
    subject: str = Field(..., min_length=1, max_length=200)
    cert_type: str = Field(default="signing", pattern="^(signing|encryption)$")
    validity_days: int = Field(default=365, ge=1, le=3650)
    organization: str | None = None


class CertResponse(BaseModel):
    cert_id: str
    subject: str
    issuer: str
    serial_number: str
    cert_type: str
    not_before: str
    not_after: str
    fingerprint: str


class CertDetailResponse(CertResponse):
    cert_pem: str
    key_pem: str


@router.post("/generate", response_model=CertDetailResponse)
async def generate_certificate(
    body: CertGenerateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR)),
):
    """Generate an SM2 certificate (signing or encryption)."""
    try:
        cert = tlcp_service.generate_certificate(
            subject=body.subject,
            cert_type=body.cert_type,
            validity_days=body.validity_days,
            organization=body.organization,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))

    await audit_service.log(
        db, action="certificate.generate", resource_type="certificate",
        user_id=current_user.id, resource_id=cert.cert_id,
        detail={"cert_type": body.cert_type, "subject": body.subject},
    )

    return CertDetailResponse(
        cert_id=cert.cert_id,
        subject=cert.subject,
        issuer=cert.issuer,
        serial_number=cert.serial_number,
        cert_type=cert.cert_type,
        not_before=cert.not_before.isoformat(),
        not_after=cert.not_after.isoformat(),
        fingerprint=cert.fingerprint,
        cert_pem=cert.cert_pem,
        key_pem=cert.key_pem,
    )


@router.get("/list")
async def list_certificates(
    subject: str | None = Query(None),
    current_user: User = Depends(get_current_user),
):
    """List certificates, optionally filtered by subject."""
    certs = tlcp_service.list_certificates(subject=subject)
    return [
        CertResponse(
            cert_id=c.cert_id,
            subject=c.subject,
            issuer=c.issuer,
            serial_number=c.serial_number,
            cert_type=c.cert_type,
            not_before=c.not_before.isoformat(),
            not_after=c.not_after.isoformat(),
            fingerprint=c.fingerprint,
        )
        for c in certs
    ]


@router.get("/{cert_id}")
async def get_certificate(
    cert_id: str,
    current_user: User = Depends(get_current_user),
):
    """Get certificate details by ID."""
    cert = tlcp_service.get_certificate(cert_id)
    if not cert:
        raise HTTPException(status_code=404, detail="Certificate not found")
    return CertDetailResponse(
        cert_id=cert.cert_id,
        subject=cert.subject,
        issuer=cert.issuer,
        serial_number=cert.serial_number,
        cert_type=cert.cert_type,
        not_before=cert.not_before.isoformat(),
        not_after=cert.not_after.isoformat(),
        fingerprint=cert.fingerprint,
        cert_pem=cert.cert_pem,
        key_pem=cert.key_pem,
    )


@router.delete("/{cert_id}")
async def revoke_certificate(
    cert_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN)),
):
    """Revoke (delete) a certificate."""
    if not tlcp_service.revoke_certificate(cert_id):
        raise HTTPException(status_code=404, detail="Certificate not found")

    await audit_service.log(
        db, action="certificate.revoke", resource_type="certificate",
        user_id=current_user.id, resource_id=cert_id,
    )

    return {"revoked": True, "cert_id": cert_id}


@router.post("/verify")
async def verify_certificate(
    cert_pem: str,
    current_user: User = Depends(get_current_user),
):
    """Verify a certificate's validity."""
    result = tlcp_service.verify_certificate(cert_pem)
    return result


@router.get("/ssl/context")
async def get_ssl_context_info(
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR)),
):
    """Get TLCP SSL context information (cipher suites, availability)."""
    from app.services.tlcp_service import _HAS_GMSSL
    ctx = tlcp_service.build_ssl_context()
    return {
        "gmssl_available": _HAS_GMSSL,
        "protocol": "TLCP" if _HAS_GMSSL else "TLS",
        "cipher_suites": ctx.get_ciphers(),
        "verify_mode": ctx.verify_mode,
    }


@router.get("/{cert_id}/verify-chain")
async def verify_certificate_chain(
    cert_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Verify the certificate chain of trust for a given certificate."""
    import uuid
    from app.services.certificate_service import certificate_service
    try:
        cert_uuid = uuid.UUID(cert_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid certificate ID")

    result = await certificate_service.verify_chain(db, cert_uuid)
    return result
