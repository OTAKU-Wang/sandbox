"""Federation API — Cross-space trust and request proxying."""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.deps import require_roles
from app.models.user import User, UserRole
from app.services.federation_connector import (
    federation_connector,
    SpaceIdentity,
    TrustLevel,
    FederationStatus,
)

router = APIRouter()


class EstablishTrustRequest(BaseModel):
    space_id: str = Field(..., min_length=1, max_length=64)
    space_name: str = Field(..., min_length=1, max_length=128)
    endpoint: str = Field(..., min_length=1)
    public_key: str = ""
    certificate: str = ""
    trust_level: str = Field("basic", pattern="^(none|basic|verified|full)$")
    allowed_operations: list[str] | None = None
    policy_sync: bool = False


class SendRequestInput(BaseModel):
    trust_id: str
    operation: str = Field(..., min_length=1, max_length=64)
    resource: str = Field(..., min_length=1, max_length=512)
    payload: dict | None = None


def _ensure_local_space():
    """Ensure local space is configured."""
    if not federation_connector._local_space:
        federation_connector.set_local_space(SpaceIdentity(
            space_id="cds-local",
            space_name="Local CDS",
            endpoint="https://local.cds.example.com",
        ))


@router.post("/trust")
async def establish_trust(
    body: EstablishTrustRequest,
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR)),
):
    """Establish a trust relationship with a remote space."""
    _ensure_local_space()

    remote = SpaceIdentity(
        space_id=body.space_id,
        space_name=body.space_name,
        endpoint=body.endpoint,
        public_key=body.public_key,
        certificate=body.certificate,
    )

    trust_level = TrustLevel(body.trust_level)
    trust = federation_connector.establish_trust(
        remote, trust_level,
        allowed_operations=body.allowed_operations,
        policy_sync=body.policy_sync,
    )

    return {
        "trust_id": trust.trust_id,
        "remote_space": trust.remote_space.space_id,
        "trust_level": trust.trust_level.value,
        "status": trust.status.value,
        "allowed_operations": trust.allowed_operations,
        "policy_sync_enabled": trust.policy_sync_enabled,
    }


@router.get("/trusts")
async def list_trusts(
    status: str | None = None,
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR)),
):
    """List all trust relationships."""
    _ensure_local_space()
    status_filter = FederationStatus(status) if status else None
    trusts = federation_connector.list_trusts(status=status_filter)
    return [
        {
            "trust_id": t.trust_id,
            "remote_space": t.remote_space.space_id,
            "trust_level": t.trust_level.value,
            "status": t.status.value,
            "allowed_operations": t.allowed_operations,
            "created_at": t.created_at.isoformat(),
        }
        for t in trusts
    ]


@router.post("/trusts/{trust_id}/revoke")
async def revoke_trust(
    trust_id: str,
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR)),
):
    """Revoke a trust relationship."""
    if not federation_connector.revoke_trust(trust_id):
        raise HTTPException(status_code=404, detail="Trust not found")
    return {"trust_id": trust_id, "status": "revoked"}


@router.post("/trusts/{trust_id}/suspend")
async def suspend_trust(
    trust_id: str,
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR)),
):
    """Suspend a trust relationship."""
    if not federation_connector.suspend_trust(trust_id):
        raise HTTPException(status_code=404, detail="Trust not found")
    return {"trust_id": trust_id, "status": "suspended"}


@router.post("/request")
async def send_federation_request(
    body: SendRequestInput,
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR)),
):
    """Send a proxied request to a remote space."""
    trust = federation_connector.get_trust(body.trust_id)
    if not trust:
        raise HTTPException(status_code=404, detail="Trust not found")

    response = federation_connector.send_request(
        trust, body.operation, body.resource,
        payload=body.payload, user_id=str(current_user.id),
    )

    return {
        "request_id": response.request_id,
        "status_code": response.status_code,
        "data": response.data,
        "error": response.error,
        "duration_ms": response.duration_ms,
        "source_space": response.source_space,
    }


@router.get("/audit")
async def get_federation_audit(
    space_id: str | None = None,
    operation: str | None = None,
    limit: int = 100,
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR, UserRole.REGULATOR)),
):
    """Get federation audit log."""
    entries = federation_connector.get_audit_log(space_id=space_id, operation=operation, limit=limit)
    return [
        {
            "entry_id": e.entry_id,
            "request_id": e.request_id,
            "source_space": e.source_space,
            "target_space": e.target_space,
            "operation": e.operation,
            "resource": e.resource,
            "status": e.status,
            "user_id": e.user_id,
            "timestamp": e.timestamp.isoformat(),
        }
        for e in entries
    ]


@router.get("/stats")
async def get_federation_stats(
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR)),
):
    """Get federation statistics."""
    return federation_connector.get_stats()
