"""Cube egress audit ingestion endpoint (SS-10 CB-06).

POST /internal/audit/egress — node audit tailer batch ingestion.  Auth:
service token (CDS_CUBE_AUDIT_SERVICE_TOKEN, fail-closed) + mTLS transport
enforced at the deployment layer (same posture as the volume-plugin KMS
surface, which uses verify_service_token).
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import verify_cube_audit_token
from app.services.cube_egress_audit import ingest_egress_batch

router = APIRouter()


class EgressEntry(BaseModel):
    offset: int = Field(ge=0)
    line_no: int = Field(ge=1)
    record: dict


class EgressBatch(BaseModel):
    node_id: str = Field(min_length=1, max_length=128)
    file_name: str = Field(min_length=1, max_length=512)
    entries: list[EgressEntry] = Field(min_length=1)


@router.post("/egress")
async def receive_egress_audit(
    batch: EgressBatch,
    db: AsyncSession = Depends(get_db),
    _svc: None = Depends(verify_cube_audit_token),
):
    """Ingest one tailer batch.  Duplicates (same idempotency key) are
    counted, not re-stored; new lines are persisted to the existing audit
    path and anchored through the synchronous Merkle flow."""
    result = await ingest_egress_batch(
        db,
        batch.node_id,
        batch.file_name,
        [e.model_dump() for e in batch.entries],
    )
    return result
