import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import uuid
from datetime import datetime, timezone
from typing import NoReturn

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.errors import CDSError
from app.core import challenge_store
from app.models.refresh_session import RefreshSession
from app.models.user import User, UserRole
from app.models.user_mfa import UserMFA
from app.schemas.user import UserCreate, UserLogin, UserResponse, TokenResponse, UserRoleChangeRequest
from app.services.auth_service import (
    register_user, authenticate_user, create_access_token,
    decode_refresh_token, decode_cert_challenge, create_cert_challenge, get_user_by_id,
    issue_refresh_session, revoke_refresh_session,
)
from app.services.audit_service import audit_service
from app.services.crypto_service import crypto_service

settings = get_settings()

router = APIRouter()

ALL_USER_ROLES = frozenset({
    UserRole.DATA_PROVIDER, UserRole.BUYER, UserRole.OPERATOR,
    UserRole.REGULATOR, UserRole.ADMIN,
})


def _as_utc(value: datetime) -> datetime:
    """SQLite round-trips DateTime(timezone=True) as naive UTC; PostgreSQL is
    already aware. Normalize before any comparison (same convention as
    app/api/audit.py::_as_utc)."""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


# ── PZ-10: MFA (TOTP) helpers ────────────────────────────────────────

def _totp_code(secret_b32: str, at: float | None = None, period: int = 30, digits: int = 6) -> str:
    """RFC 6238 TOTP (HMAC-SHA1), pure stdlib — pyotp is not a project dep."""
    key = base64.b32decode(secret_b32)
    counter = int((at if at is not None else time.time()) // period)
    digest = hmac.new(key, counter.to_bytes(8, "big"), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = int.from_bytes(digest[offset:offset + 4], "big") & 0x7FFFFFFF
    return str(value % (10 ** digits)).zfill(digits)


def _verify_totp(secret_b32: str, code: str, period: int = 30, digits: int = 6) -> bool:
    code = code.strip()
    if not code.isdigit() or len(code) != digits:
        return False
    now = time.time()
    # ±1 step tolerance for clock skew
    return any(
        _totp_code(secret_b32, now + delta, period, digits) == code
        for delta in (-period, 0, period)
    )


def _mfa_cipher():
    """SM4-GCM cipher for at-rest MFA secrets. Key = SHA-256 of the
    configured master secret (CDS_MFA_SECRET_ENCRYPTION_KEY); the JWT-secret
    fallback is dev/test only — validate_security_config refuses it in prod."""
    from app.utils.crypto import SM4Cipher
    secret = settings.MFA_SECRET_ENCRYPTION_KEY or settings.JWT_SECRET_KEY
    return SM4Cipher(key=hashlib.sha256(secret.encode("utf-8")).digest())


def _encrypt_mfa(plaintext: str) -> str:
    ct, nonce, tag = _mfa_cipher().encrypt_gcm(plaintext.encode("utf-8"))
    return base64.b64encode(nonce + tag + ct).decode("ascii")


def _decrypt_mfa(blob: str) -> str:
    raw = base64.b64decode(blob)
    nonce, tag, ct = raw[:12], raw[12:28], raw[28:]
    return _mfa_cipher().decrypt_gcm(ct, nonce, tag).decode("utf-8")


def _generate_recovery_codes(count: int) -> list[str]:
    return [secrets.token_hex(4) for _ in range(count)]


async def _get_mfa(db: AsyncSession, user_id: uuid.UUID) -> UserMFA | None:
    result = await db.execute(select(UserMFA).where(UserMFA.user_id == user_id))
    return result.scalar_one_or_none()


async def _consume_recovery_code(db: AsyncSession, mfa: UserMFA, code: str) -> list[str] | None:
    """One-shot recovery code: returns the remaining codes, or None on failure."""
    if not mfa.recovery_codes_encrypted:
        return None
    try:
        codes = json.loads(_decrypt_mfa(mfa.recovery_codes_encrypted))
    except (ValueError, TypeError):
        return None
    if code not in codes:
        return None
    codes.remove(code)
    mfa.recovery_codes_encrypted = _encrypt_mfa(json.dumps(codes)) if codes else None
    await db.flush()
    return codes


def _new_totp_secret() -> str:
    return base64.b32encode(os.urandom(20)).decode("ascii")


def _otpauth_uri(username: str, secret: str) -> str:
    return (
        f"otpauth://totp/CDS:{username}"
        f"?secret={secret}&issuer=CDS"
        f"&period={settings.MFA_TOTP_PERIOD}&digits={settings.MFA_TOTP_DIGITS}"
    )


class MfaVerifyRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=32)


class MfaEnrollRequest(BaseModel):
    """A14: body optional — first/in-flight enrollment keeps the old no-body
    contract; resetting a CONFIRMED enrollment must carry the current second
    factor (live TOTP or an unspent recovery code) as proof of possession."""
    code: str | None = Field(None, min_length=1, max_length=32)


class MfaRecoverRequest(BaseModel):
    recovery_code: str = Field(..., min_length=1, max_length=32)


class LogoutRequest(BaseModel):
    """Revoke the refresh session carried by this token (M3).

    Optional by design (lead 拍板 2026-10-30): existing front-end logout calls
    post an empty body — that stays a legal logout + audit, it simply cannot
    name a jti to revoke (the FE clears its stored refresh token locally, so
    the renewal path dies client-side too). The token-carrying form is what
    revocation-aware clients (SDK) should use.
    """
    refresh_token: str | None = Field(None, min_length=16, max_length=4096)


class CertLoginRequest(BaseModel):
    username: str
    cert_pem: str
    # Server-issued challenge token (POST /auth/login/cert/challenge) — NOT a
    # client-chosen nonce. It is signed, short-lived, single-use and bound to
    # `username`; the SM2 signature must cover this exact string.
    nonce: str = Field(..., min_length=16, max_length=1024)
    signature: str


class CertChallengeRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)


class CertChallengeResponse(BaseModel):
    challenge: str
    expires_in: int



@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(body: UserCreate, request: Request, db: AsyncSession = Depends(get_db)):
    role = body.role or UserRole.BUYER  # PZ-10: omitted role defaults to buyer
    # D-e2e-01: admin is the bootstrap role — self-selectable ONLY by the first
    # account on an empty deployment. The escape hatch (CDS_ALLOW_ADMIN_SELF_REGISTER,
    # test/dev bootstrap convenience) is refused at startup in production.
    if role == UserRole.ADMIN:
        from sqlalchemy import func
        existing = await db.scalar(select(func.count()).select_from(User))
        if existing and existing > 0 and not settings.ALLOW_ADMIN_SELF_REGISTER:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admin is only self-selectable for the first (bootstrap) account; "
                       "create further admins via admin-assigned role change",
            )
    try:
        user = await register_user(db, body.username, body.email, body.password, role, body.organization)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    await audit_service.log(
        db, action="user.register", resource_type="user", user_id=user.id,
        detail={"username": body.username, "role": role},
        ip_address=request.client.host if request.client else None,
    )
    access_token = create_access_token(user.id, user.role)
    _, refresh_token = await issue_refresh_session(db, user.id)
    return TokenResponse(access_token=access_token, refresh_token=refresh_token, user=UserResponse.model_validate(user))


@router.post("/login", response_model=TokenResponse)
async def login(body: UserLogin, request: Request, db: AsyncSession = Depends(get_db)):
    user = await authenticate_user(db, body.username, body.password)
    if not user:
        await audit_service.log_standalone(
            action="user.login_failed", resource_type="user",
            detail={"username": body.username, "reason": "invalid_credentials"},
            ip_address=request.client.host if request.client else None,
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    # PZ-10: MFA gate — confirmed enrollments require a valid TOTP or a
    # one-shot recovery code after the password check.
    if settings.AUTH_MFA_ENABLED:
        mfa = await _get_mfa(db, user.id)
        if mfa and mfa.confirmed:
            if not body.mfa_code:
                await audit_service.log_standalone(
                    action="user.login_mfa_missing", resource_type="user", user_id=user.id,
                    detail={"username": body.username},
                    ip_address=request.client.host if request.client else None,
                )
                raise CDSError(
                    "MFA code required: provide mfa_code (TOTP) or a recovery code",
                    code="AUTH_MFA_REQUIRED", http_status=401,
                    detail={"username": body.username},
                )
            ok = False
            if mfa.totp_secret_encrypted:
                ok = _verify_totp(
                    _decrypt_mfa(mfa.totp_secret_encrypted), body.mfa_code,
                    settings.MFA_TOTP_PERIOD, settings.MFA_TOTP_DIGITS,
                )
            if not ok:
                ok = (await _consume_recovery_code(db, mfa, body.mfa_code)) is not None
            if not ok:
                await audit_service.log_standalone(
                    action="user.login_mfa_failed", resource_type="user", user_id=user.id,
                    detail={"username": body.username},
                    ip_address=request.client.host if request.client else None,
                )
                raise CDSError(
                    "Invalid MFA code", code="AUTH_MFA_REQUIRED", http_status=401,
                    detail={"username": body.username},
                )

    await audit_service.log(
        db, action="user.login", resource_type="user", user_id=user.id,
        detail={"username": body.username},
        ip_address=request.client.host if request.client else None,
    )
    # M3: a successful login — including passing the MFA gate above — is born
    # as a revocable server-side session (jti row), never as a bare JWT.
    access_token = create_access_token(user.id, user.role)
    _, refresh_token = await issue_refresh_session(db, user.id)
    return TokenResponse(access_token=access_token, refresh_token=refresh_token, user=UserResponse.model_validate(user))


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    """Exchange a refresh token for a new access+refresh token pair.

    CR-2026-10-15 §4.10 (M3, 生产门): signature verification alone proved the
    token was minted here, not that it is still honoured. Every pass now goes
    through the ``refresh_sessions`` row named by the token's ``jti``:

    * the row must exist, belong to the token subject, be unrevoked and
      unexpired — logout/rotation revocation takes effect on the next use;
    * **rotation**: the presenting session is revoked and a fresh jti/session
      is minted in the same transaction (single-use refresh tokens);
    * **MFA re-verification** (user 拍板 2026-10-30): if the account's MFA is
      confirmed and this session was issued before the MFA confirmation, the
      token predates the second factor — renewing it would let a pre-MFA
      (password-only) capture keep the account alive forever, so the request
      returns 401 ``AUTH_MFA_REQUIRED`` and a fresh MFA login is mandatory.

    Legacy compat policy (pre-0032 tokens carry no jti): rejected
    **fail-closed** with 401. An un-revocable refresh token is exactly the
    defect being fixed; grandfathering it back in would defer the fix for the
    token's remaining lifetime (max 7 days, self-healing). Users re-login once.

    Audit discipline: only *signature-valid* refusals are audited
    (``user.refresh_denied``) — auditing undecodable garbage would hand an
    unauthenticated caller a cheap audit-table write amplifier.
    """
    token = body.get("refresh_token")
    if not token:
        raise HTTPException(status_code=400, detail="refresh_token required")
    ip = request.client.host if request.client else None
    try:
        payload = decode_refresh_token(token)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")

    signed = {
        "detail": {"username": None, "reason": None, "jti": payload.get("jti")},
    }

    async def _deny(reason: str, exc: HTTPException | CDSError, user_id=None) -> NoReturn:
        signed["detail"]["reason"] = reason
        await audit_service.log_standalone(
            action="user.refresh_denied", resource_type="user", user_id=user_id,
            detail=signed["detail"], ip_address=ip,
        )
        raise exc

    # ── jti is mandatory: pre-0032 tokens are refused (see policy above) ──
    jti = payload.get("jti")
    if not jti:
        await _deny("missing_jti", HTTPException(
            status_code=401,
            detail="Refresh token predates session binding (no jti); log in again",
        ))

    user = await get_user_by_id(db, uuid.UUID(payload["sub"]))
    if not user or not user.is_active:
        await _deny("user_not_found_or_inactive",
                    HTTPException(status_code=401, detail="User not found or inactive"),
                    user_id=payload.get("sub"))
    signed["detail"]["username"] = user.username

    row = (await db.execute(
        select(RefreshSession).where(RefreshSession.jti == jti)
    )).scalar_one_or_none()
    if row is None:
        # Valid signature but no row: forged jti, or the row's owner changed
        # under a re-provisioned DB. Either way there is nothing to honour.
        await _deny("unknown_jti",
                    HTTPException(status_code=401, detail="Invalid or expired refresh token"),
                    user_id=user.id)
    if row.user_id != user.id:
        await _deny("jti_subject_mismatch",
                    HTTPException(status_code=401, detail="Invalid or expired refresh token"),
                    user_id=user.id)
    if row.revoked:
        # Revoked: post-rotation replay or a logout that beat this request.
        await _deny("revoked_jti",
                    HTTPException(status_code=401, detail="Refresh token revoked"),
                    user_id=user.id)
    if _as_utc(row.expires_at) <= datetime.now(timezone.utc):
        await _deny("session_expired",
                    HTTPException(status_code=401, detail="Invalid or expired refresh token"),
                    user_id=user.id)

    # ── MFA re-verification gate ──
    mfa = await _get_mfa(db, user.id)
    if mfa and mfa.confirmed:
        # confirmed_at is NULL only for rows confirmed before column 0032 —
        # created_at is the honest lower bound (row precedes confirmation).
        confirmed_at = _as_utc(mfa.confirmed_at or mfa.created_at)
        if _as_utc(row.issued_at) < confirmed_at:
            await _deny("mfa_confirmed_after_session", CDSError(
                "This session predates the account's MFA confirmation; "
                "re-authenticate with the second factor",
                code="AUTH_MFA_REQUIRED", http_status=401,
                detail={"username": user.username, "reason": "mfa_revalidation_required"},
            ), user_id=user.id)

    # ── rotation: this session is spent the moment it is honoured ──
    # Conditional UPDATE, not a read-modify-write: two concurrent refreshes of
    # the same jti must not BOTH rotate. `revoked=false` in the WHERE clause
    # makes single-use a database-level guarantee; the loser gets rowcount 0.
    rotation = await db.execute(
        update(RefreshSession)
        .where(RefreshSession.id == row.id, RefreshSession.revoked.is_(False))
        .values(revoked=True)
    )
    await db.flush()
    if rotation.rowcount != 1:
        await _deny("revoked_jti_race",
                    HTTPException(status_code=401, detail="Refresh token revoked"),
                    user_id=user.id)
    await audit_service.log(
        db, action="user.refresh", resource_type="user", user_id=user.id,
        detail={"username": user.username, "rotated_jti": jti}, ip_address=ip,
    )
    access_token = create_access_token(user.id, user.role)
    _, refresh_token_new = await issue_refresh_session(db, user.id)
    return TokenResponse(
        access_token=access_token, refresh_token=refresh_token_new,
        user=UserResponse.model_validate(user),
    )


@router.post("/logout")
async def logout(
    body: LogoutRequest | None = None,
    request: Request = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Revoke the refresh session carried by the presented token (M3).

    Two shapes, both audited as ``user.logout``:
    * with ``refresh_token`` — requires a signature-valid token whose subject
      is the caller (logging out must itself be authenticated; one stolen
      refresh token cannot evict its owner's other sessions) and revokes that
      one jti. Idempotent: already-revoked or legacy jti-less tokens return
      ``revoked=false`` without error.
    * without a body — the pre-existing front-end logout contract: nothing
      server-side is revoked (``revoked=false``), the client drops its tokens.
      Kept honest and legal on purpose so wiring the FE later is opt-in, not a
      breaking change (lead 拍板 2026-10-30).

    Honest limits (disclosed, not faked): the ACCESS token is a stateless JWT
    and stays valid until its short ``JWT_EXPIRE_MINUTES`` expiry — this
    endpoint revokes the *renewal path*, not in-flight bearer use. There is no
    push/email channel in this repo, so revocation is observable through the
    audit trail (``user.logout``), not through a notification.
    """
    ip = request.client.host if request and request.client else None
    jti = None
    revoked = False
    if body and body.refresh_token:
        try:
            payload = decode_refresh_token(body.refresh_token)
        except Exception:
            await audit_service.log_standalone(
                action="user.logout_denied", resource_type="user", user_id=current_user.id,
                detail={"username": current_user.username, "reason": "invalid_refresh_token"},
                ip_address=ip,
            )
            raise HTTPException(status_code=401, detail="Invalid or expired refresh token")
        if payload.get("sub") != str(current_user.id):
            await audit_service.log_standalone(
                action="user.logout_denied", resource_type="user", user_id=current_user.id,
                detail={"username": current_user.username, "reason": "token_subject_mismatch"},
                ip_address=ip,
            )
            raise HTTPException(status_code=403, detail="Refresh token belongs to another account")
        jti = payload.get("jti")
        if jti:
            revoked = await revoke_refresh_session(db, current_user.id, jti, reason="logout")
    await audit_service.log(
        db, action="user.logout", resource_type="user", user_id=current_user.id,
        detail={"username": current_user.username, "jti": jti, "revoked": revoked},
        ip_address=ip,
    )
    return {"revoked": revoked}


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)):
    return UserResponse.model_validate(current_user)


@router.post("/ws-ticket")
async def create_ws_ticket(current_user: User = Depends(get_current_user)):
    """W10: mint a 30s single-use WebSocket ticket for the exec stream."""
    from app.api.session_stream import issue_ws_ticket

    result = await issue_ws_ticket(str(current_user.id), current_user.role)
    if "error" in result:
        raise HTTPException(status_code=503, detail=result["error"])
    return result


@router.patch("/users/{user_id}/role", response_model=UserResponse)
async def update_user_role(
    user_id: uuid.UUID,
    body: UserRoleChangeRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Admin-only: update a user's role.

    PZ-10 high-risk discipline (P-GAP-014): the change requires a reason
    (>=8 chars) and an optional ticket_id; both are persisted into the audit
    trail. The FE high-risk modal (utils/highRiskOperation.tsx) enforces the
    same contract; this is the server-side authority.
    """
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    new_role = body.role
    if not new_role:
        raise HTTPException(status_code=400, detail="role required")
    # PZ-10: enum validation against UserRole — free-text roles are rejected.
    if new_role not in ALL_USER_ROLES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid role: must be one of {', '.join(sorted(ALL_USER_ROLES))}",
        )
    user = await get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    old_role = user.role
    user.role = new_role
    await db.flush()
    await db.refresh(user)
    await audit_service.log(
        db, action="user.role_changed", resource_type="user", user_id=user.id,
        resource_id=str(user.id),
        detail={
            "old_role": old_role,
            "new_role": new_role,
            "actor_id": str(current_user.id),
            # P-GAP-014: reason + ticket_id are part of the audit record.
            "reason": body.reason,
            "ticket_id": body.ticket_id,
        },
    )
    return UserResponse.model_validate(user)


@router.post("/generate-sm2-keys")
async def generate_sm2_keys(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate SM2 key pair for the current user and store the public key.

    Returns the private key (hex) — user must save it securely, it won't be stored.
    """
    try:
        keypair = crypto_service.generate_keypair()
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

    # Store public key as the user's SM2 certificate
    current_user.sm2_certificate = keypair.public_key
    await db.flush()

    return {
        "public_key": keypair.public_key,
        "private_key": keypair.private_key,
        "message": "SM2密钥对已生成。请妥善保管私钥，系统不会存储。",
    }


@router.post("/sign-data")
async def sign_data(
    data: dict,
    current_user: User = Depends(get_current_user),
):
    """Sign arbitrary data with user's SM2 private key.

    Request body: { "data": "text to sign", "private_key": "hex..." }
    Returns: { "signature": "hex..." }
    """
    text = data.get("data")
    private_key = data.get("private_key")
    if not text or not private_key:
        raise HTTPException(status_code=400, detail="data and private_key required")

    if not current_user.sm2_certificate:
        raise HTTPException(status_code=400, detail="用户未生成SM2密钥对，请先调用 /generate-sm2-keys")

    try:
        sig = crypto_service.sign(text.encode("utf-8"), private_key, current_user.sm2_certificate)
        return {"signature": sig.signature}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"签名失败: {e}")


# ── PZ-10: MFA (TOTP) endpoints ─────────────────────────────────────

@router.post("/mfa/enroll")
async def mfa_enroll(
    request: Request,
    body: MfaEnrollRequest | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Start/replace a TOTP enrollment. The secret is shown ONCE here;
    confirm it via /mfa/verify.

    A14: the login gate (models/user_mfa.py) only enforces while
    ``confirmed`` is true, so resetting a CONFIRMED enrollment silently
    downgrades the account to password-only login. Therefore re-enrolling a
    confirmed enrollment now requires proof of possession of the current
    second factor (live TOTP or an unspent recovery code) in the body —
    a password-only attacker can no longer disarm MFA with one POST. First
    or in-flight (unconfirmed) enrollments stay body-optional; the gate
    never covered them, so replacing them is not a downgrade."""
    ip = request.client.host if request.client else None
    secret = _new_totp_secret()
    mfa = await _get_mfa(db, current_user.id)
    was_confirmed = mfa is not None and mfa.confirmed
    if mfa is not None and mfa.confirmed:
        code = body.code if body else None
        proved = False
        if code:
            if mfa.totp_secret_encrypted:
                proved = _verify_totp(
                    _decrypt_mfa(mfa.totp_secret_encrypted), code,
                    settings.MFA_TOTP_PERIOD, settings.MFA_TOTP_DIGITS,
                )
            if not proved:
                proved = (await _consume_recovery_code(db, mfa, code)) is not None
        if not proved:
            await audit_service.log_standalone(
                action="user.mfa_enroll_denied", resource_type="user", user_id=current_user.id,
                detail={"username": current_user.username, "reason": "second_factor_proof_required"},
                ip_address=ip,
            )
            raise CDSError(
                "Re-enrolling a confirmed MFA enrollment requires the current TOTP "
                "code or a recovery code",
                code="AUTH_MFA_REQUIRED", http_status=401,
                detail={"username": current_user.username},
            )
    if mfa is None:
        mfa = UserMFA(user_id=current_user.id)
        db.add(mfa)
    mfa.totp_secret_encrypted = _encrypt_mfa(secret)
    mfa.confirmed = False
    mfa.confirmed_at = None
    mfa.recovery_codes_encrypted = None
    await db.flush()

    await audit_service.log(
        db, action="mfa.enroll", resource_type="user", user_id=current_user.id,
        ip_address=request.client.host if request.client else None,
    )
    # §4.11 (用户拍板 2026-10-30: 允许关闭，但必须留痕): overwriting a
    # CONFIRMED enrollment takes the account out of second-factor enforcement
    # for as long as it stays unconfirmed. The A14 proof gate means this can
    # only be the owner acting with the live factor — but a legitimate
    # downgrade is still a downgrade, so it gets its own named, high-risk
    # audit event instead of hiding inside mfa.enroll.
    if was_confirmed:
        await audit_service.log(
            db, action="user.mfa_disabled", resource_type="user", user_id=current_user.id,
            resource_id=str(current_user.id),
            detail={
                "username": current_user.username,
                "reason": "confirmed_enrollment_reset_via_re_enroll",
                "high_risk": True,
                "note": "账号自此次起在 /mfa/verify 前回到仅密码登录；未确认窗口不影响已登录会话的既有鉴权。",
            },
            ip_address=ip,
        )
    return {"secret": secret, "otpauth_uri": _otpauth_uri(current_user.username, secret), "confirmed": False}


@router.post("/mfa/verify")
async def mfa_verify(
    body: MfaVerifyRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Confirm the current TOTP enrollment with a live code. On success the
    enrollment becomes active and fresh one-shot recovery codes are issued
    (returned once, stored encrypted)."""
    mfa = await _get_mfa(db, current_user.id)
    if not mfa or not mfa.totp_secret_encrypted:
        raise HTTPException(status_code=400, detail="Not enrolled: call POST /auth/mfa/enroll first")
    if not _verify_totp(
        _decrypt_mfa(mfa.totp_secret_encrypted), body.code,
        settings.MFA_TOTP_PERIOD, settings.MFA_TOTP_DIGITS,
    ):
        raise HTTPException(status_code=400, detail="Invalid TOTP code")

    recovery_codes = _generate_recovery_codes(settings.MFA_RECOVERY_CODE_COUNT)
    mfa.confirmed = True
    # M3 MFA-gate input: sessions issued before THIS instant stop being
    # renewable (see refresh_token). Deliberately stamped even on
    # re-confirmation — re-confirming is re-establishing enforcement.
    mfa.confirmed_at = datetime.now(timezone.utc)
    mfa.recovery_codes_encrypted = _encrypt_mfa(json.dumps(recovery_codes))
    await db.flush()

    await audit_service.log(
        db, action="mfa.confirm", resource_type="user", user_id=current_user.id,
        ip_address=request.client.host if request.client else None,
    )
    return {
        "confirmed": True,
        "recovery_codes": recovery_codes,
        "message": "保存好这些一次性恢复码，系统不会再明文返回。",
    }


@router.post("/mfa/recover")
async def mfa_recover(
    body: MfaRecoverRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Spend one recovery code and re-enroll: a fresh secret is returned and the
    enrollment drops to UNCONFIRMED until /mfa/verify confirms it.

    A14 honesty note: while unconfirmed the login gate is NOT enforced (see
    ``login`` above and the ``confirmed`` contract in models/user_mfa.py — an
    enrolled-but-unverified row never blocks password login, pinned by
    tests/test_pz10_mfa.py::test_unconfirmed_enrollment_does_not_block_login),
    so verify the new secret promptly. That window is exactly why this endpoint
    spends a recovery code first: the recovery code IS the second factor, so a
    password alone can never take an account out of MFA enforcement — the same
    rule the A14 gate enforces on /mfa/enroll."""
    mfa = await _get_mfa(db, current_user.id)
    if not mfa:
        raise HTTPException(status_code=400, detail="Not enrolled: call POST /auth/mfa/enroll first")
    was_confirmed = bool(mfa.confirmed)
    remaining = await _consume_recovery_code(db, mfa, body.recovery_code)
    if remaining is None:
        raise HTTPException(status_code=400, detail="Invalid or already-used recovery code")

    secret = _new_totp_secret()
    mfa.totp_secret_encrypted = _encrypt_mfa(secret)
    mfa.confirmed = False
    mfa.confirmed_at = None
    await db.flush()

    await audit_service.log(
        db, action="mfa.recover", resource_type="user", user_id=current_user.id,
        detail={"remaining_recovery_codes": len(remaining)},
        ip_address=request.client.host if request.client else None,
    )
    # §4.11 留痕: spending a recovery code also ends second-factor enforcement
    # until /mfa/verify (the unconfirmed window the docstring above names).
    # High-risk audit event, same contract as the re-enroll downgrade.
    if was_confirmed:
        await audit_service.log(
            db, action="user.mfa_disabled", resource_type="user", user_id=current_user.id,
            resource_id=str(current_user.id),
            detail={
                "username": current_user.username,
                "reason": "confirmed_enrollment_reset_via_recovery_code",
                "high_risk": True,
                "remaining_recovery_codes": len(remaining),
                "note": "恢复码重登记后在 /mfa/verify 前回到仅密码登录。",
            },
            ip_address=request.client.host if request.client else None,
        )
    return {
        "secret": secret,
        "otpauth_uri": _otpauth_uri(current_user.username, secret),
        "confirmed": False,
        "remaining_recovery_codes": len(remaining),
    }


# ── PZ-10: SM2 certificate login (second factor track) ──────────────

@router.post("/login/cert/challenge", response_model=CertChallengeResponse)
async def cert_login_challenge(body: CertChallengeRequest, request: Request):
    """Issue a single-use SM2 cert-login challenge (B1 fix, CR 2026-10-15).

    Deliberately performs NO user lookup: the token only binds the submitted
    username, so this endpoint cannot be used to enumerate accounts. Unknown
    usernames simply fail later at /login/cert like any bad credential.
    """
    challenge, expires_in = create_cert_challenge(body.username)
    return CertChallengeResponse(challenge=challenge, expires_in=expires_in)


@router.post("/login/cert", response_model=TokenResponse)
async def cert_login(body: CertLoginRequest, request: Request, db: AsyncSession = Depends(get_db)):
    """SM2 cert second-factor login: a currently-valid SM2 certificate plus an
    SM2 signature over a SERVER-issued, single-use challenge (see
    /login/cert/challenge). The cert's public key must match the user's stored
    sm2_certificate (bound at /auth/generate-sm2-keys), so possession of the
    private key is required. Reuses the real cert service (tlcp/
    certificate_service) — no simulated path.

    Why the challenge exists: a client-chosen nonce let an attacker who captured
    one login request replay that exact body forever (the signature covered the
    attacker's own nonce), which would bypass password + TOTP entirely.
    """
    if not settings.AUTH_CERT_LOGIN:
        raise HTTPException(status_code=403, detail="Certificate login is disabled (CDS_AUTH_CERT_LOGIN=false)")

    from app.services.certificate_service import certificate_service
    from app.services.tlcp_service import tlcp_service

    async def _deny(reason: str, detail: dict | None = None) -> None:
        await audit_service.log_standalone(
            action="user.cert_login_failed", resource_type="user",
            detail={"username": body.username, "reason": reason},
            ip_address=request.client.host if request.client else None,
        )
        raise HTTPException(status_code=401, detail=detail or reason)

    try:
        challenge = decode_cert_challenge(body.nonce)
    except Exception:
        await _deny("invalid_challenge", "Missing or invalid certificate login challenge")
    if challenge.get("sub") != body.username:
        await _deny("challenge_subject_mismatch", "Challenge does not belong to this account")

    result = await db.execute(select(User).where(User.username == body.username))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        await _deny("invalid_credentials", "Invalid credentials")

    cert_check = tlcp_service.verify_certificate(body.cert_pem)
    if not cert_check.get("valid") or cert_check.get("error"):
        await _deny("invalid_certificate")

    try:
        der = tlcp_service._pem_to_der(body.cert_pem, "CERTIFICATE")
        parsed = certificate_service._parse_x509_der(der)
        cert_pub = parsed.get("public_key", "")
    except Exception:
        await _deny("invalid_certificate")
    if len(cert_pub) == 130 and cert_pub[:2].lower() == "04":
        cert_pub = cert_pub[2:]  # strip the uncompressed-point 04 prefix
    if not cert_pub:
        await _deny("invalid_certificate")

    try:
        sig_ok = crypto_service.verify(body.nonce.encode("utf-8"), body.signature, cert_pub)
    except RuntimeError:
        raise HTTPException(status_code=503, detail="SM2 verification unavailable (gmssl missing)")
    if not sig_ok:
        await _deny("signature_mismatch")

    if not user.sm2_certificate or user.sm2_certificate.lower() != cert_pub.lower():
        await _deny("certificate_not_bound", "Certificate not bound to this account")

    # Everything verified — spend the challenge now, so a captured request body
    # can never be replayed (a second use of the same challenge is rejected).
    if not await challenge_store.consume(body.nonce):
        await _deny("challenge_consumed", "Certificate login challenge was already used")

    await audit_service.log(
        db, action="user.cert_login", resource_type="user", user_id=user.id,
        detail={"username": body.username, "cert_subject": cert_check.get("subject", "")},
        ip_address=request.client.host if request.client else None,
    )
    access_token = create_access_token(user.id, user.role)
    _, refresh_token = await issue_refresh_session(db, user.id)
    return TokenResponse(access_token=access_token, refresh_token=refresh_token, user=UserResponse.model_validate(user))
