"""Certificate management API — SM2 certificate lifecycle for TLCP."""
from datetime import datetime

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user, require_roles
from app.models.user import User, UserRole
from app.schemas.high_risk_operation import HighRiskOperationRequest
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


class CertGeneratedResponse(CertDetailResponse):
    """生成响应 — 唯一会带私钥的形态。

    生成是密钥材应当离开服务的唯一时刻（调用方下一步就装它），而
    ``/generate`` 本来就是 ADMIN/OPERATOR 门。
    """
    key_pem: str


class CertPrivateKeyResponse(BaseModel):
    """显式私钥导出（仅 ADMIN、必上审计）— 见 OCR-A1。"""
    cert_id: str
    subject: str
    cert_type: str
    key_pem: str


@router.post("/generate", response_model=CertGeneratedResponse)
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

    return CertGeneratedResponse(
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


@router.get("/{cert_id}", response_model=CertDetailResponse)
async def get_certificate(
    cert_id: str,
    current_user: User = Depends(get_current_user),
):
    """Get certificate details — the public PEM only, never the private key.

    OCR-A1: 本路由过去对**任意已登录用户**返回 ``key_pem``。cert_id 只是 uuid 前 8
    位，一个 buyer 账号就能枚举并拖走平台签过的每一个 SM2 私钥（即所有 TLCP
    端点的私钥半边）。密钥材现在只能走下面那条具名、仅 ADMIN、必上审计的导出。
    """
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
    )


@router.get("/{cert_id}/private-key", response_model=CertPrivateKeyResponse)
async def export_certificate_private_key(
    cert_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN)),
):
    """Export one certificate's private key — ADMIN only, and recorded.

    比 ``/generate``（ADMIN/OPERATOR）故意更窄：给一个正要装证书的调用方现成
    铸一把新密钥，不等于能拖走平台已签发的全部私钥。每次调用以
    ``certificate.export_key`` 落审计。
    """
    cert = tlcp_service.get_certificate(cert_id)
    if not cert:
        raise HTTPException(status_code=404, detail="Certificate not found")

    await audit_service.log(
        db, action="certificate.export_key", resource_type="certificate",
        user_id=current_user.id, resource_id=cert_id,
        detail={"subject": cert.subject, "cert_type": cert.cert_type},
    )

    return CertPrivateKeyResponse(
        cert_id=cert.cert_id,
        subject=cert.subject,
        cert_type=cert.cert_type,
        key_pem=cert.key_pem,
    )


@router.delete("/{cert_id}")
async def revoke_certificate(
    cert_id: str,
    body: HighRiskOperationRequest = Body(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN)),
):
    """Revoke (delete) a certificate."""
    if not tlcp_service.revoke_certificate(cert_id):
        raise HTTPException(status_code=404, detail="Certificate not found")

    await audit_service.log(
        db, action="certificate.revoke", resource_type="certificate",
        user_id=current_user.id, resource_id=cert_id,
        detail={"reason": body.reason, "ticket_id": body.ticket_id},
    )

    return {"revoked": True, "cert_id": cert_id}


@router.post("/verify")
async def verify_certificate(
    cert_pem: str,
    current_user: User = Depends(get_current_user),
):
    """Check a certificate's validity window (see ``assurance`` for what that means)."""
    result = tlcp_service.verify_certificate(cert_pem)
    return {**result, "assurance": _verification_assurance("in-process-certificate-store")}


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


# ─── OCR-A106：验证结论的口径披露 ────────────────────────────────────
# 两个验证明白端点过去直接转发服务层结论，而结论远比字面弱：
#   * tlcp_service.verify_certificate 只做指纹查表 / X.509 解析 + 有效期比较；
#   * certificate_service.verify_chain（certificate_service.py:467-536）只顺着
#     parent_cert_id 取 DB 行、比对 issuer/subject 字符串与状态列，既不对任何
#     签名做密码学校验，也不校验可信锚（“任意 is_ca 且 issuer==subject 的行”就是根）。
# 铁律：拿不出的结论不能报成通过。根因在 certificate_service（不在本任务
# 文件集），本层先把“验了什么/没验什么”随结论一同发出，使 ``valid: true``
# 不会被读成“这条链验过签”（那正是 A257 担心后端会谎报的那一点）。

def _verification_assurance(revocation_checked_against: str) -> dict:
    """机器可读地声明该验证结论**不**覆盖什么。"""
    return {
        "level": "metadata-only",
        "signature_verified": False,
        "trust_anchor": "none",
        "revocation_checked_against": revocation_checked_against,
        "meaning": "仅比较有效期、状态列与 issuer/subject 字符串；未验证任何签名，也无信任锚",
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
    return {**result, "assurance": _verification_assurance("database-row-status")}
