"""W15: shared volume API — create/list/delete, attach/detach to sessions."""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.sandbox_session import SandboxSession
from app.models.shared_volume import SharedVolume, SharedVolumeAttachment
from app.models.user import User
from app.services import shared_volumes as volume_service
from app.services.audit_service import audit_service

router = APIRouter()


class SharedVolumeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    size_limit_mb: int = Field(default=1024, ge=1, le=102400)
    read_only: bool = False


class AttachRequest(BaseModel):
    session_id: uuid.UUID
    read_only: bool | None = None


def _volume_payload(volume: SharedVolume, attachments: int) -> dict:
    return {
        "id": str(volume.id),
        "name": volume.name,
        "size_limit_mb": volume.size_limit_mb,
        "read_only": volume.read_only,
        "attachments": attachments,
        "created_at": volume.created_at.isoformat() if volume.created_at else None,
    }


@router.post("", status_code=201)
async def create_volume(
    body: SharedVolumeCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    volume = await volume_service.create_volume(
        db, owner_id=current_user.id, name=body.name,
        size_limit_mb=body.size_limit_mb, read_only=body.read_only,
    )
    await audit_service.log(
        db, action="shared_volume.create", resource_type="shared_volume",
        user_id=current_user.id, resource_id=str(volume.id),
        detail={"name": volume.name},
    )
    await db.flush()
    return _volume_payload(volume, 0)


@router.get("")
async def list_volumes(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(SharedVolume).where(SharedVolume.owner_id == current_user.id)
        .order_by(SharedVolume.created_at.desc())
    )
    volumes = list(result.scalars().all())
    counts: dict[uuid.UUID, int] = {}
    if volumes:
        att = await db.execute(
            select(SharedVolumeAttachment).where(
                SharedVolumeAttachment.volume_id.in_([v.id for v in volumes])
            )
        )
        for row in att.scalars().all():
            counts[row.volume_id] = counts.get(row.volume_id, 0) + 1
    return {"volumes": [_volume_payload(v, counts.get(v.id, 0)) for v in volumes], "total": len(volumes)}


@router.post("/{volume_id}/attach")
async def attach_volume(
    volume_id: uuid.UUID,
    body: AttachRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    session = await db.get(SandboxSession, body.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Sandbox session not found")
    if session.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not your session")
    attachment = await volume_service.attach(
        db, volume_id=volume_id, session_id=body.session_id,
        owner_id=current_user.id, read_only=body.read_only,
    )
    await audit_service.log(
        db, action="shared_volume.attach", resource_type="shared_volume",
        user_id=current_user.id, resource_id=str(volume_id),
        detail={"session_id": str(body.session_id), "read_only": attachment.read_only},
    )
    await db.flush()
    return {
        "volume_id": str(volume_id),
        "session_id": str(body.session_id),
        "read_only": attachment.read_only,
        "guest_path": f"/workspace/shared/",
    }


@router.delete("/{volume_id}/detach/{session_id}")
async def detach_volume(
    volume_id: uuid.UUID,
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    volume = await db.get(SharedVolume, volume_id)
    if not volume or volume.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Volume not found")
    removed = await volume_service.detach(db, volume_id=volume_id, session_id=session_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Attachment not found")
    return {"detached": True}


@router.delete("/{volume_id}")
async def delete_volume(
    volume_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    volume = await db.get(SharedVolume, volume_id)
    if not volume or volume.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Volume not found")
    await volume_service.delete_volume(db, volume)
    await audit_service.log(
        db, action="shared_volume.delete", resource_type="shared_volume",
        user_id=current_user.id, resource_id=str(volume_id),
        detail={"name": volume.name},
    )
    await db.flush()
    return {"deleted": True}
