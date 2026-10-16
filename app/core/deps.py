import hmac
import logging
import uuid
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.errors import CDSError
from app.core import scopes as _scopes
from app.services.auth_service import decode_access_token, get_user_by_id
from app.models.user import User
from app.models.api_key import ApiKey

logger = logging.getLogger(__name__)


def _record_audit_write_failure(op: str) -> None:
    """An audit write that could not be persisted must never be silently lost.

    The request itself is allowed to continue (an audit-store outage must not
    lock out every machine call — deliberate availability choice, same posture
    as the fail-open rate limiter), but the lost event is logged with full
    context and counted, so `cds_audit_write_failures_total` > 0 is alertable
    instead of invisible.
    """
    from app.core.metrics import AUDIT_WRITE_FAILURES

    AUDIT_WRITE_FAILURES.labels(op=op).inc()
    logger.exception("[AUDIT-LOSS] audit write failed for %s — the event is NOT in the audit trail", op)


@dataclass
class AuthSubject:
    """Unified request subject (SS-12): human JWT user or machine API key."""
    kind: str  # "user" | "api_key"
    user: User | None = None
    api_key: ApiKey | None = None


async def _resolve_api_key(credential: str, db: AsyncSession, request: Request | None) -> AuthSubject:
    """SS-12 key path (spec 4.3): sm3_hash lookup, status/expiry/cooldown,
    per-key rate limit, throttled last_used_at write, and audit of the call."""
    from app.utils.crypto import sm3_hash
    from app.services.audit_service import audit_service

    key_hash = sm3_hash(credential.encode("utf-8"))
    ip = request.client.host if request is not None and request.client else None
    user_agent = request.headers.get("user-agent") if request is not None else None

    async def _denied(code: str, detail: dict, http_status: int = 401, audit: bool = True) -> AuthSubject:
        if audit:
            _scopes.record_auth_failure(key_hash)
            try:
                await audit_service.log(
                    db,
                    action="auth.api_key.denied",
                    resource_type="api_key",
                    resource_id=detail.get("key_prefix"),
                    user_id=None,
                    detail={"reason": detail.get("reason"), "actor_type": "api_key",
                            "actor_id": detail.get("actor_id"), "path": request.url.path if request else None,
                            "method": request.method if request else None,
                            "status_code": http_status, "denied_reason": detail.get("reason")},
                    ip_address=ip,
                    user_agent=user_agent,
                    actor_type="api_key",
                    actor_id=detail.get("actor_id"),
                )
                await db.commit()
            except Exception:
                await db.rollback()
                _record_audit_write_failure("api_key.denied")
        raise CDSError(
            detail.get("reason", "api key authentication failed"),
            code=code,
            http_status=http_status,
            detail={**detail, "path": request.url.path if request else None},
        )

    if _scopes.cooldown_remaining_seconds(key_hash) > 0:
        return await _denied("API_KEY_INACTIVE", {"reason": "cooldown"})

    key = (await db.execute(select(ApiKey).where(ApiKey.key_hash == key_hash))).scalar_one_or_none()
    if key is None:
        return await _denied("API_KEY_INVALID", {"reason": "not_found", "key_prefix": credential[:12]})

    if key.status != "active":
        return await _denied("API_KEY_INACTIVE",
                             {"reason": key.status, "key_prefix": key.key_prefix, "actor_id": str(key.id)})
    if key.is_expired():
        return await _denied("API_KEY_INVALID", {"reason": "expired", "key_prefix": key.key_prefix,
                                                 "actor_id": str(key.id)})

    allowed, retry_after = await _scopes.check_key_rate_limit(str(key.id), key.rate_limit)
    if not allowed:
        raise CDSError(
            f"API key rate limit exceeded ({key.rate_limit} req/min)",
            code="RATE_LIMITED", http_status=429, retry_after=retry_after,
            detail={"key_prefix": key.key_prefix, "retry_after": retry_after},
        )

    _scopes.clear_auth_failures(key_hash)
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    last_used = key.last_used_at
    if last_used is not None and last_used.tzinfo is None:
        last_used = last_used.replace(tzinfo=timezone.utc)
    if last_used is None or (now - last_used).total_seconds() >= 60:
        key.last_used_at = now
        await db.flush()

    try:
        # SAVEPOINT 包裹审计行：写入失败只回滚这一段，不把整个请求事务拖回起点
        # （直接 db.rollback() 会 expire 会话内对象，下游同步的 scope 检查取
        #  subject.api_key.scopes 时会在无 greenlet 的上下文里重查 → MissingGreenlet）
        async with db.begin_nested():
            await audit_service.log(
                db,
                action="api_key.call",
                resource_type="api_key",
                resource_id=str(key.id),
                user_id=None,
                detail={"actor_type": "api_key", "actor_id": str(key.id),
                        "path": request.url.path if request else None,
                        "method": request.method if request else None,
                        "key_prefix": key.key_prefix},
                ip_address=ip,
                user_agent=user_agent,
                actor_type="api_key",
                actor_id=str(key.id),
            )
        await db.commit()
    except Exception:
        _record_audit_write_failure("api_key.call")
        # 失败语句已随 SAVEPOINT 回滚，不对请求事务做整事务 rollback：
        # 实测（远程 py3.12）begin_nested 回滚不会 expire 会话内对象，而
        # db.rollback() 会 expire key → 下游同步的 scope 检查必现 MissingGreenlet。
        # 若失败出在 commit（真实事务已中止），会话在 get_db 关闭时释放，
        # 请求后续若还需查库会真实失败——但不会因为 expire 而假死。

    return AuthSubject(kind="api_key", api_key=key)


async def resolve_auth_subject(credential: str, db: AsyncSession, request: Request | None = None) -> AuthSubject:
    """Route one Bearer credential to the key path (`cds_ak_` prefix) or the
    byte-preserved JWT path, and build the AuthSubject."""
    if credential.startswith("cds_ak_"):
        return await _resolve_api_key(credential, db, request)

    try:
        payload = decode_access_token(credential)
        user_id = uuid.UUID(payload["sub"])
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    user = await get_user_by_id(db, user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")
    return AuthSubject(kind="user", user=user)


async def get_auth_subject(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_scopes.subject_security),
    db: AsyncSession = Depends(get_db),
) -> AuthSubject:
    """Unified subject resolution (SS-12 spec 4.3).

    Missing credentials → 401 (no degradation); `cds_ak_` credentials take
    the key path, everything else the JWT path. Results are cached on
    request.state so a request authenticates at most once (the router-level
    scope gate and the route-level auth dependency share it).
    """
    if credentials is None or not credentials.credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    cached = getattr(request.state, "_ss12_subject", None)
    if cached is not None:
        return cached
    subject = await resolve_auth_subject(credentials.credentials, db, request)
    request.state._ss12_subject = subject
    return subject


async def get_current_user(
    request: Request,
    subject: AuthSubject = Depends(get_auth_subject),
) -> User:
    """Backward-compatible user resolver — now routes through get_auth_subject.

    JWT subjects behave byte-identically to the previous implementation;
    key subjects have no user identity, so routes that require a user 401
    (human-only surfaces are additionally gated by require_human, SS-12 §4.3).
    """
    if subject.kind != "user" or subject.user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key credentials cannot authenticate as a user",
        )
    return subject.user


async def get_business_user(
    request: Request,
    subject: AuthSubject = Depends(get_auth_subject),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Subject resolver for machine-capable business surfaces (SS-12, D-e2e-09).

    User subjects behave byte-identically to get_current_user. Key subjects
    act under their ISSUER's business identity (api_keys.issued_by): the
    machine performs the operation, ownership/created_by lands on the issuing
    admin, and the audit trail already records subject kind=api_key (scope
    gate + per-call audit, SS-12 §4.3) so key actions stay distinguishable.
    """
    if subject.kind == "user" and subject.user is not None:
        return subject.user
    key = subject.api_key
    if key is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key credentials cannot authenticate as a user",
        )
    issuer = await get_user_by_id(db, key.issued_by)
    if not issuer or not issuer.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key issuer account is missing or inactive",
        )
    return issuer


def require_roles(*allowed_roles: str):
    """Dependency factory: reject with 403 if current user's role is not in allowed_roles.

    Resolves via get_business_user (D-e2e-09): key subjects are role-checked
    against their ISSUER (delegated business identity); JWT subjects behave
    byte-identically to the previous get_current_user path.
    """
    async def checker(current_user: User = Depends(get_business_user)) -> User:
        if current_user.role not in allowed_roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
        return current_user
    return checker


# auto_error=False so a missing Authorization header reaches the check below
# (and is rejected with 401) instead of HTTPBearer's default 403.
service_security = HTTPBearer(auto_error=False)


async def verify_service_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(service_security),
) -> None:
    """Machine-to-machine service-token auth for service-facing endpoints.

    Checked against settings.KMS_SERVICE_TOKEN. Fail-closed: a token that is
    not configured, or a missing/wrong bearer token, is rejected with 401.
    Used by the KMS volume-key endpoints (Go volume plugin), NOT user JWT.
    """
    from app.core.config import get_settings
    settings = get_settings()
    expected = settings.KMS_SERVICE_TOKEN
    if not expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Service token not configured")
    if credentials is None or not hmac.compare_digest(credentials.credentials, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid service token")
    return None


async def verify_cube_audit_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(service_security),
) -> None:
    """Machine-to-machine auth for the cube egress audit ingestion endpoint
    (POST /internal/audit/egress, SS-10 CB-06). Same fail-closed posture as
    verify_service_token; checked against settings.CUBE_AUDIT_SERVICE_TOKEN
    so the audit surface does not share the KMS volume-plugin credential."""
    from app.core.config import get_settings
    settings = get_settings()
    expected = settings.CUBE_AUDIT_SERVICE_TOKEN
    if not expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Service token not configured")
    if credentials is None or not hmac.compare_digest(credentials.credentials, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid service token")
    return None
