"""W15: shared volume lifecycle + sandbox bind resolution."""
import shutil
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import CDSError
from app.models.shared_volume import SharedVolume, SharedVolumeAttachment


def volume_dir(volume_id: uuid.UUID) -> Path:
    root = Path(get_settings().SHARED_VOLUME_ROOT)
    path = root / str(volume_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


async def create_volume(
    db: AsyncSession, *, owner_id: uuid.UUID, name: str,
    size_limit_mb: int = 1024, read_only: bool = False,
) -> SharedVolume:
    if not name or len(name) > 64 or "/" in name or name.startswith("."):
        raise CDSError(
            "Volume name must be 1-64 chars without path separators",
            code="VALIDATION_ERROR", http_status=400,
        )
    existing = await db.execute(
        select(SharedVolume).where(SharedVolume.owner_id == owner_id, SharedVolume.name == name)
    )
    if existing.scalar_one_or_none():
        raise CDSError(f"Volume '{name}' already exists", code="VOLUME_EXISTS", http_status=409)
    volume = SharedVolume(owner_id=owner_id, name=name, size_limit_mb=size_limit_mb, read_only=read_only)
    db.add(volume)
    await db.flush()
    volume_dir(volume.id)
    return volume


async def attach(
    db: AsyncSession, *, volume_id: uuid.UUID, session_id: uuid.UUID,
    owner_id: uuid.UUID, read_only: bool | None = None,
) -> SharedVolumeAttachment:
    result = await db.execute(select(SharedVolume).where(SharedVolume.id == volume_id))
    volume = result.scalar_one_or_none()
    if not volume:
        raise CDSError(f"Volume not found: {volume_id}", code="VOLUME_NOT_FOUND", http_status=404)
    if volume.owner_id != owner_id:
        raise CDSError("Not your volume", code="FORBIDDEN", http_status=403)
    effective_ro = volume.read_only if read_only is None else read_only
    attachment = SharedVolumeAttachment(
        session_id=session_id, volume_id=volume_id, read_only=effective_ro
    )
    db.add(attachment)
    await db.flush()
    return attachment


async def detach(db: AsyncSession, *, volume_id: uuid.UUID, session_id: uuid.UUID) -> int:
    result = await db.execute(
        select(SharedVolumeAttachment).where(
            SharedVolumeAttachment.volume_id == volume_id,
            SharedVolumeAttachment.session_id == session_id,
        )
    )
    attachment = result.scalar_one_or_none()
    if not attachment:
        return 0
    await db.delete(attachment)
    await db.flush()
    return 1


async def delete_volume(db: AsyncSession, volume: SharedVolume) -> None:
    await db.execute(
        select(SharedVolumeAttachment).where(SharedVolumeAttachment.volume_id == volume.id)
    )
    attachments = (await db.execute(
        select(SharedVolumeAttachment).where(SharedVolumeAttachment.volume_id == volume.id)
    )).scalars().all()
    for attachment in attachments:
        await db.delete(attachment)
    await db.delete(volume)
    await db.flush()
    shutil.rmtree(volume_dir(volume.id), ignore_errors=True)


async def resolve_binds(db: AsyncSession, session_id: uuid.UUID) -> list[tuple[Path, str, bool]]:
    """Bind spec for the sandbox runtime: (host_dir, guest_path, read_only)."""
    result = await db.execute(
        select(SharedVolume, SharedVolumeAttachment)
        .join(SharedVolumeAttachment, SharedVolumeAttachment.volume_id == SharedVolume.id)
        .where(SharedVolumeAttachment.session_id == session_id)
    )
    binds: list[tuple[Path, str, bool]] = []
    for volume, attachment in result.all():
        binds.append((volume_dir(volume.id), f"/workspace/shared/{volume.name}", attachment.read_only))
    return binds


async def resolve_mounts(db: AsyncSession, session_id: uuid.UUID) -> list[dict]:
    """K8s PVC mount spec for a session's attached shared volumes.

    Returns ``[{name: <pvc-claim>, mountPath, readOnly}, ...]`` where the PVC
    claim name is ``cds-shared-<volume_id>`` (N7). The sandbox pod spec embeds
    these as persistentVolumeClaim mounts.
    """
    result = await db.execute(
        select(SharedVolume, SharedVolumeAttachment)
        .join(SharedVolumeAttachment, SharedVolumeAttachment.volume_id == SharedVolume.id)
        .where(SharedVolumeAttachment.session_id == session_id)
    )
    mounts: list[dict] = []
    for volume, attachment in result.all():
        mounts.append({
            "name": f"cds-shared-{volume.id}",
            "mountPath": f"/workspace/shared/{volume.name}",
            "readOnly": attachment.read_only,
        })
    return mounts
