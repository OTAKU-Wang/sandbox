"""KMS recovery — restore persisted wrapped keys on startup (gap B3).

Wrapped DEK/session-key blobs persisted in ``key_metadata.wrapped_payload``
are reloaded into the KMS in-memory store so sessions and encrypted data
survive process restarts. Only ACTIVE (non-destroyed) keys are restored;
destroyed keys keep their NULL payload (crypto-erase on termination).

Multi-replica note: with the software HSM fallback the KEK is process-local,
so cross-replica recovery requires a real Vault/HSM backend (tracked as
environment acceptance item P-GAP-004).
"""
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.kms import KeyMetadata, KeyStatus, KeyType

logger = logging.getLogger(__name__)


async def restore_wrapped_keys(db: AsyncSession) -> int:
    """Reload persisted wrapped key blobs into the KMS in-memory store.

    Returns the number of keys restored.
    """
    from app.services.kms_service import kms_service

    result = await db.execute(
        select(KeyMetadata).where(
            KeyMetadata.status == KeyStatus.ACTIVE.value,
            KeyMetadata.wrapped_payload.isnot(None),
        )
    )
    restored = 0
    for meta in result.scalars().all():
        try:
            ok = kms_service.import_wrapped(
                meta.key_id,
                meta.wrapped_payload,
                meta.sm2_encrypted_payload,
            )
            if ok:
                restored += 1
        except Exception as e:
            logger.warning("[KMS-RECOVERY] Failed to import key %s: %s", meta.key_id, e)
    if restored:
        logger.info("[KMS-RECOVERY] Restored %d wrapped keys from persistence", restored)
    return restored


async def upsert_wrapped_payload(
    db: AsyncSession, key_id: str, wrapped_payload: bytes,
    sm2_encrypted_payload: bytes | None = None,
) -> None:
    """Persist (or refresh) the wrapped blob for one key_id (D4 持久化面).

    调用方在自己的事务里 commit；本函数只 flush。已存在 ACTIVE 行且 blob 相同时
    幂等跳过；行存在但 blob 不同只刷新 payload（KEK 轮换语义属 rotate 流程，
    不在这里动 status）。不存在则插最小 ACTIVE 行——DEK 持久化不要求先有
    api/kms.py 建的行（storage 信封 DEK 就走这条路）。
    """
    existing = (
        await db.execute(select(KeyMetadata).where(KeyMetadata.key_id == key_id))
    ).scalar_one_or_none()
    if existing is not None:
        if existing.wrapped_payload == wrapped_payload:
            return
        existing.wrapped_payload = wrapped_payload
        if sm2_encrypted_payload is not None:
            existing.sm2_encrypted_payload = sm2_encrypted_payload
        await db.flush()
        return
    db.add(KeyMetadata(
        key_id=key_id,
        key_type=KeyType.DEK.value,
        algorithm="SM4-GCM",
        status=KeyStatus.ACTIVE.value,
        wrapped_payload=wrapped_payload,
        sm2_encrypted_payload=sm2_encrypted_payload,
    ))
    await db.flush()
