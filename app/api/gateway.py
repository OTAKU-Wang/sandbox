"""Contract Execution Gateway API — unified data product access.

Provides secure, metered, audited access to data products via app credentials.
All access is governed by contract terms and OPA policies.
"""
import json
import uuid
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Header
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user, require_roles
from app.models.user import User, UserRole
from app.models.app_credential import AppCredential, CredentialStatus
from app.models.contract import Contract
from app.services.gateway_service import gateway_service, GatewayRequest, MeteringRecord, sm3_hash
from app.services.audit_service import audit_service
from app.services.quota_manager import quota_manager

router = APIRouter()


# === Request/Response Models ===

class CreateCredentialRequest(BaseModel):
    contract_id: str = Field(..., description="Contract UUID")
    description: str = Field(default="", max_length=500)
    rate_limit: int = Field(default=100, ge=1, le=10000)
    quota_rows: int = Field(default=1000000, ge=1)
    quota_bytes: int = Field(default=1073741824, ge=1)
    allowed_ips: list[str] | None = Field(default=None)
    expires_hours: int = Field(default=720, ge=1, le=8760)  # 30 days default, max 1 year


class CredentialResponse(BaseModel):
    id: str
    app_id: str
    app_secret: str | None = None  # Only returned on creation
    contract_id: str
    status: str
    rate_limit: int
    quota_rows: int
    quota_bytes: int
    allowed_ips: list[str] | None
    created_at: str
    expires_at: str | None


class QueryRequest(BaseModel):
    sql: str = Field(..., min_length=1, max_length=10000)
    product_id: str = Field(..., description="Target data product UUID")
    format: str = Field(default="json", pattern="^(json|csv|parquet)$")


class AccessRequest(BaseModel):
    product_id: str = Field(..., description="Target data product UUID")


# === Credential Management ===

@router.post("/credentials", response_model=CredentialResponse)
async def create_credential(
    body: CreateCredentialRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR)),
):
    """Create an application credential for contract gateway access."""
    # Verify contract exists and is active
    try:
        contract_uuid = uuid.UUID(body.contract_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid contract ID")

    result = await db.execute(select(Contract).where(Contract.id == contract_uuid))
    contract = result.scalar_one_or_none()
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")
    if contract.status not in ("active", "signed"):
        raise HTTPException(status_code=400, detail=f"Contract is {contract.status}, must be active")

    # Generate credentials
    app_id = AppCredential.generate_app_id()
    app_secret = AppCredential.generate_app_secret()
    secret_hash = sm3_hash(app_secret)

    cred = AppCredential(
        app_id=app_id,
        app_secret_hash=secret_hash,
        contract_id=contract_uuid,
        consumer_id=current_user.id,
        status=CredentialStatus.ACTIVE.value,
        rate_limit=body.rate_limit,
        quota_rows=body.quota_rows,
        quota_bytes=body.quota_bytes,
        allowed_ips=body.allowed_ips,
        description=body.description,
        expires_at=datetime.now(timezone.utc).replace(
            hour=23, minute=59, second=59
        ) + __import__("datetime").timedelta(hours=body.expires_hours),
    )
    db.add(cred)
    await db.flush()
    await db.refresh(cred)

    await audit_service.log(
        db, action="gateway.create_credential", resource_type="app_credential",
        user_id=current_user.id, resource_id=str(cred.id),
        detail={"contract_id": body.contract_id, "app_id": app_id},
    )

    return CredentialResponse(
        id=str(cred.id),
        app_id=app_id,
        app_secret=app_secret,  # Only returned on creation
        contract_id=str(cred.contract_id),
        status=cred.status,
        rate_limit=cred.rate_limit,
        quota_rows=cred.quota_rows,
        quota_bytes=cred.quota_bytes,
        allowed_ips=cred.allowed_ips,
        created_at=cred.created_at.isoformat() if cred.created_at else "",
        expires_at=cred.expires_at.isoformat() if cred.expires_at else None,
    )


@router.get("/credentials")
async def list_credentials(
    contract_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List application credentials for the current user."""
    query = select(AppCredential).where(AppCredential.consumer_id == current_user.id)
    if contract_id:
        query = query.where(AppCredential.contract_id == uuid.UUID(contract_id))

    result = await db.execute(query)
    creds = result.scalars().all()
    return [
        {
            "id": str(c.id),
            "app_id": c.app_id,
            "contract_id": str(c.contract_id),
            "status": c.status,
            "rate_limit": c.rate_limit,
            "created_at": c.created_at.isoformat() if c.created_at else "",
            "last_used_at": c.last_used_at.isoformat() if c.last_used_at else None,
        }
        for c in creds
    ]


@router.delete("/credentials/{credential_id}")
async def revoke_credential(
    credential_id: str,
    reason: str = "manual_revocation",
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR)),
):
    """Revoke an application credential."""
    result = await db.execute(select(AppCredential).where(AppCredential.id == uuid.UUID(credential_id)))
    cred = result.scalar_one_or_none()
    if not cred:
        raise HTTPException(status_code=404, detail="Credential not found")

    cred.status = CredentialStatus.REVOKED.value
    cred.revoked_at = datetime.now(timezone.utc)
    cred.revoke_reason = reason

    await audit_service.log(
        db, action="gateway.revoke_credential", resource_type="app_credential",
        user_id=current_user.id, resource_id=str(cred.id),
        detail={"reason": reason},
    )

    return {"id": str(cred.id), "status": "revoked", "reason": reason}


# === Gateway Access Endpoints ===

@router.post("/{contract_id}/query")
async def gateway_query(
    contract_id: str,
    body: QueryRequest,
    request: Request,
    x_app_id: str = Header(..., alias="X-App-Id"),
    x_app_secret: str = Header(..., alias="X-App-Secret"),
    db: AsyncSession = Depends(get_db),
):
    """Execute a query against a data product via the contract gateway.

    Requires X-App-Id and X-App-Secret headers for authentication.
    """
    start_time = time.monotonic()
    client_ip = request.client.host if request.client else ""
    request_id = str(uuid.uuid4())

    # 1. Authenticate
    cred, auth_error = await gateway_service.authenticate(db, x_app_id, x_app_secret, client_ip)
    if auth_error:
        raise HTTPException(status_code=401, detail=auth_error)

    # 2. Authorize
    contract, authz_error = await gateway_service.authorize(db, cred, "query", body.product_id)
    if authz_error:
        raise HTTPException(status_code=403, detail=authz_error)

    # 3. Check quota
    allowed, quota_reason = await gateway_service.check_quota(db, cred)
    if not allowed:
        raise HTTPException(status_code=429, detail=f"Quota exceeded: {quota_reason}")

    # 4. Execute query
    response = await gateway_service.execute_query(db, contract, body.product_id, body.sql, body.format)

    # 5. Content security
    if response.success and response.data:
        response.data, security_report = gateway_service.apply_content_security(response.data, contract)
        response.content_security = security_report
        if security_report.get("inspection_blocked"):
            response.success = False
            response.error = "Output inspection blocked release"
            response.status_code = 403

    # 6. Metering
    duration_ms = int((time.monotonic() - start_time) * 1000)
    rows_returned = response.metering.get("rows", 0)
    bytes_returned = response.metering.get("bytes", 0)
    metering = MeteringRecord(
        request_id=request_id,
        contract_id=str(contract.id),
        app_id=cred.app_id,
        consumer_id=str(cred.consumer_id),
        product_id=body.product_id,
        operation="query",
        rows_returned=rows_returned,
        bytes_returned=bytes_returned,
        duration_ms=duration_ms,
        status_code=response.status_code,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
    gateway_service.record_metering(metering)

    # Increment daily quota counters in Redis
    await gateway_service.increment_quota(cred, rows=rows_returned, bytes_count=bytes_returned)

    # 7. Audit
    await audit_service.log(
        db, action="gateway.query", resource_type="data_product",
        user_id=cred.consumer_id, resource_id=body.product_id,
        detail={
            "request_id": request_id,
            "contract_id": contract_id,
            "app_id": cred.app_id,
            "rows": metering.rows_returned,
            "duration_ms": duration_ms,
        },
    )

    if not response.success:
        raise HTTPException(status_code=response.status_code, detail=response.error)

    return {
        "request_id": request_id,
        "data": response.data,
        "metering": {
            "rows": metering.rows_returned,
            "bytes": metering.bytes_returned,
            "duration_ms": duration_ms,
        },
        "content_security": response.content_security,
    }


@router.post("/{contract_id}/access")
async def gateway_access(
    contract_id: str,
    body: AccessRequest,
    request: Request,
    x_app_id: str = Header(..., alias="X-App-Id"),
    x_app_secret: str = Header(..., alias="X-App-Secret"),
    db: AsyncSession = Depends(get_db),
):
    """Access an unstructured data product (get download URL).

    For unstructured data products (files, images, etc.) — returns a presigned URL.
    """
    client_ip = request.client.host if request.client else ""

    # Authenticate
    cred, auth_error = await gateway_service.authenticate(db, x_app_id, x_app_secret, client_ip)
    if auth_error:
        raise HTTPException(status_code=401, detail=auth_error)

    # Authorize
    contract, authz_error = await gateway_service.authorize(db, cred, "access", body.product_id)
    if authz_error:
        raise HTTPException(status_code=403, detail=authz_error)

    # Get presigned URL
    response = await gateway_service.get_presigned_url(body.product_id)

    # Audit
    await audit_service.log(
        db, action="gateway.access", resource_type="data_product",
        user_id=cred.consumer_id, resource_id=body.product_id,
        detail={"contract_id": contract_id, "app_id": cred.app_id},
    )

    if not response.success:
        raise HTTPException(status_code=response.status_code, detail=response.error)

    return {"data": response.data}


@router.get("/{contract_id}/metering")
async def get_metering(
    contract_id: str,
    x_app_id: str = Header(..., alias="X-App-Id"),
    x_app_secret: str = Header(..., alias="X-App-Secret"),
    db: AsyncSession = Depends(get_db),
):
    """Get metering data for a contract."""
    client_ip = ""
    cred, auth_error = await gateway_service.authenticate(db, x_app_id, x_app_secret, client_ip)
    if auth_error:
        raise HTTPException(status_code=401, detail=auth_error)

    contract, authz_error = await gateway_service.authorize(db, cred, "metering")
    if authz_error:
        raise HTTPException(status_code=403, detail=authz_error)

    # Query metering from ClickHouse
    ch_stats = {"total_requests": 0, "total_rows": 0, "total_bytes": 0, "avg_latency_ms": 0}
    try:
        client = gateway_service._get_clickhouse()
        if client:
            rows = client.execute(
                """SELECT
                    count() as total_requests,
                    sum(rows_returned) as total_rows,
                    sum(bytes_returned) as total_bytes,
                    avg(duration_ms) as avg_latency_ms
                   FROM gateway_metering
                   WHERE contract_id = %(cid)s AND app_id = %(aid)s
                   AND timestamp >= today()""",
                {"cid": str(contract.id), "aid": cred.app_id},
            )
            if rows:
                r = rows[0]
                ch_stats = {
                    "total_requests": r[0],
                    "total_rows": r[1],
                    "total_bytes": r[2],
                    "avg_latency_ms": round(r[3], 2),
                }
    except Exception:
        pass

    # Get real-time Redis usage for daily quota
    redis_usage = await quota_manager.get_gateway_usage(cred.app_id)

    return {
        "contract_id": contract_id,
        "app_id": cred.app_id,
        "today": ch_stats,
        "usage": {
            "rows_today": redis_usage["rows"],
            "bytes_today": redis_usage["bytes"],
        },
        "quota": {
            "quota_rows": cred.quota_rows,
            "quota_bytes": cred.quota_bytes,
            "rate_limit": cred.rate_limit,
            "rows_remaining": max(0, cred.quota_rows - redis_usage["rows"]),
            "bytes_remaining": max(0, cred.quota_bytes - redis_usage["bytes"]),
        },
    }


@router.get("/{contract_id}/status")
async def get_contract_gateway_status(
    contract_id: str,
    x_app_id: str = Header(..., alias="X-App-Id"),
    x_app_secret: str = Header(..., alias="X-App-Secret"),
    db: AsyncSession = Depends(get_db),
):
    """Get contract execution status via gateway."""
    client_ip = ""
    cred, auth_error = await gateway_service.authenticate(db, x_app_id, x_app_secret, client_ip)
    if auth_error:
        raise HTTPException(status_code=401, detail=auth_error)

    contract, authz_error = await gateway_service.authorize(db, cred, "status")
    if authz_error:
        raise HTTPException(status_code=403, detail=authz_error)

    return {
        "contract_id": contract_id,
        "contract_status": contract.status,
        "contract_type": contract.contract_type,
        "product_count": len(contract.product_ids),
        "credential_status": cred.status,
        "expires_at": cred.expires_at.isoformat() if cred.expires_at else None,
    }
