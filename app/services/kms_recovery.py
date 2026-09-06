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

from app.models.kms import KeyMetadata, KeyStatus

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
