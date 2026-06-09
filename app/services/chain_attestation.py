"""Chain Attestation — full-chain audit trail for trusted data circulation.

Every significant event in the data product lifecycle is attested:
1. Data resource upload → hash chain entry
2. Data product creation → linked to resource attestation
3. Contract signing → SM2 signatures on chain
4. Sandbox session → execution attestation
5. Output delivery → output hash attestation
6. Key lifecycle → key generation/destruction attestation

Uses PostgreSQL append-only hash chain (blockchain alternative):
- Each record contains previous_hash for tamper-evidence
- SM3-based Merkle tree for batch anchoring
- Periodic Merkle root publication for external verification
"""
import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class ChainAttestationService:
    """Manages the append-only hash chain for audit attestation."""

    async def attest_event(
        self,
        db: AsyncSession,
        event_type: str,
        resource_type: str,
        resource_id: str,
        actor_id: str | None,
        detail: dict[str, Any] | None = None,
        previous_hash: str | None = None,
    ) -> dict:
        """Create a new attestation entry in the hash chain.

        Args:
            event_type: e.g. "data_product.create", "contract.sign"
            resource_type: e.g. "data_product", "contract", "sandbox_session"
            resource_id: UUID of the resource
            actor_id: User who performed the action
            detail: Additional event details
            previous_hash: Hash of the previous chain entry (auto-fetched if None)

        Returns:
            Dict with attestation_id, hash, chain_position
        """
        if previous_hash is None:
            previous_hash = await self._get_latest_hash(db)

        timestamp = datetime.now(timezone.utc)

        # Build the attestation payload
        payload = {
            "event_type": event_type,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "actor_id": actor_id,
            "detail": detail or {},
            "timestamp": timestamp.isoformat(),
            "previous_hash": previous_hash,
        }

        payload_bytes = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        from app.services.crypto_service import crypto_service
        attestation_hash = crypto_service.sm3_hash(payload_bytes)

        # Insert into the attestation chain
        await db.execute(
            text("""
                INSERT INTO chain_attestations
                (event_type, resource_type, resource_id, actor_id, detail, attestation_hash, previous_hash, created_at)
                VALUES (:event_type, :resource_type, :resource_id, :actor_id, :detail, :hash, :prev_hash, :ts)
            """),
            {
                "event_type": event_type,
                "resource_type": resource_type,
                "resource_id": resource_id,
                "actor_id": actor_id,
                "detail": json.dumps(detail or {}),
                "hash": attestation_hash,
                "prev_hash": previous_hash,
                "ts": timestamp,
            },
        )

        return {
            "attestation_hash": attestation_hash,
            "previous_hash": previous_hash,
            "event_type": event_type,
            "timestamp": timestamp.isoformat(),
        }

    async def verify_chain(self, db: AsyncSession, limit: int = 100) -> dict:
        """Verify the integrity of the attestation chain.

        Returns {valid: bool, checked: int, broken_at: int|None}.
        """
        result = await db.execute(
            text("""
                SELECT id, event_type, resource_type, resource_id, actor_id, detail,
                       attestation_hash, previous_hash, created_at
                FROM chain_attestations
                ORDER BY id DESC
                LIMIT :limit
            """),
            {"limit": limit},
        )
        rows = result.fetchall()

        if not rows:
            return {"valid": True, "checked": 0, "broken_at": None}

        # Reverse to check from oldest to newest
        rows = list(reversed(rows))
        checked = 0
        prev_hash = None

        for i, row in enumerate(rows):
            # Reconstruct the payload
            payload = {
                "event_type": row[1],
                "resource_type": row[2],
                "resource_id": row[3],
                "actor_id": row[4],
                "detail": json.loads(row[5]) if row[5] else {},
                "timestamp": row[8].isoformat() if row[8] else "",
                "previous_hash": row[7],
            }

            # Verify hash
            payload_bytes = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
            from app.services.crypto_service import crypto_service
            expected_hash = crypto_service.sm3_hash(payload_bytes)

            if expected_hash != row[6]:
                return {"valid": False, "checked": checked, "broken_at": i, "expected": expected_hash, "actual": row[6]}

            # Verify chain linkage
            if i > 0 and row[7] != prev_hash:
                return {"valid": False, "checked": checked, "broken_at": i, "reason": "previous_hash mismatch"}

            prev_hash = row[6]
            checked += 1

        return {"valid": True, "checked": checked, "broken_at": None}

    async def get_attestation(self, db: AsyncSession, resource_id: str) -> list[dict]:
        """Get all attestations for a specific resource."""
        result = await db.execute(
            text("""
                SELECT id, event_type, resource_type, resource_id, actor_id, detail,
                       attestation_hash, previous_hash, created_at
                FROM chain_attestations
                WHERE resource_id = :resource_id
                ORDER BY id ASC
            """),
            {"resource_id": resource_id},
        )
        rows = result.fetchall()

        return [
            {
                "id": row[0],
                "event_type": row[1],
                "resource_type": row[2],
                "resource_id": row[3],
                "actor_id": row[4],
                "detail": json.loads(row[5]) if row[5] else {},
                "attestation_hash": row[6],
                "previous_hash": row[7],
                "created_at": row[8].isoformat() if row[8] else None,
            }
            for row in rows
        ]

    async def _get_latest_hash(self, db: AsyncSession) -> str:
        """Get the hash of the latest attestation entry."""
        result = await db.execute(
            text("SELECT attestation_hash FROM chain_attestations ORDER BY id DESC LIMIT 1")
        )
        row = result.fetchone()
        return row[0] if row else "0" * 64  # Genesis hash


# Singleton
chain_attestation = ChainAttestationService()
