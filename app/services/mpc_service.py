"""MPC (Multi-Party Computation) Key Sharing Protocol — DB-persisted.

Implements Shamir's Secret Sharing for secure key distribution with durable
storage (spec N3): keys and shares live in PostgreSQL/SQLite via the
mpc_keys / mpc_key_shares tables instead of process memory, so a restart no
longer loses split secrets. Share values are encrypted at rest with an
SM4-GCM ciphertext blob keyed by the audit master secret (same KEK
derivation chain as egress_audit).

Honest boundary (documented in specs): this is Shamir secret *escrow*, not a
multi-party *computation* protocol. HE/MPC computation is a P2 roadmap item.
"""
import hashlib
import os
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.mpc_key import MPCKeyRecord, MPCKeyShareRecord


# Prime field for Shamir's Secret Sharing (256-bit prime)
_PRIME = 0xFFFFFFFEFFFFFFFFFFFFFFFFFFFFFFFF7203DF6B21C6052B53BBF40939D54123


def _eval_polynomial(coefficients: list[int], x: int, prime: int) -> int:
    """Evaluate polynomial at point x over finite field."""
    result = 0
    for i, coef in enumerate(coefficients):
        result = (result + coef * pow(x, i, prime)) % prime
    return result


def _mod_inverse(a: int, m: int) -> int:
    """Modular inverse using Fermat's little theorem (m must be prime)."""
    return pow(a, m - 2, m)


def _lagrange_interpolate(points: list[tuple[int, int]], prime: int) -> int:
    """Lagrange interpolation to reconstruct secret at x=0."""
    if not points:
        raise ValueError("Need at least one point")

    secret = 0
    k = len(points)

    for i in range(k):
        xi, yi = points[i]
        numerator = 1
        denominator = 1

        for j in range(k):
            if i != j:
                xj, _ = points[j]
                numerator = (numerator * (-xj)) % prime
                denominator = (denominator * (xi - xj)) % prime

        lagrange_coef = (numerator * _mod_inverse(denominator, prime)) % prime
        secret = (secret + yi * lagrange_coef) % prime

    return secret


@dataclass
class KeyShare:
    """A single share of a split secret."""
    share_id: str
    key_id: str
    index: int  # x-coordinate (1-based)
    share_value: str  # hex-encoded y-coordinate
    threshold: int
    total_shares: int
    holder: str | None = None  # party holding this share
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime | None = None


@dataclass
class MPCKey:
    """Managed MPC key with split shares."""
    key_id: str
    algorithm: str  # sm4, aes256
    threshold: int  # minimum shares to reconstruct
    total_shares: int
    shares: list[KeyShare] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict = field(default_factory=dict)
    status: str = "active"
    persisted: bool = True


def _share_cipher_key() -> bytes:
    """Stable KEK for share-at-rest encryption (same chain as egress_audit)."""
    settings = get_settings()
    secret = settings.AUDIT_ENCRYPTION_KEY or settings.JWT_SECRET_KEY
    return hashlib.sha256(secret.encode("utf-8")).digest()


def _encrypt_share_value(plaintext_hex: str) -> bytes:
    from app.utils.crypto import SM4Cipher
    cipher = SM4Cipher(key=_share_cipher_key())
    ct, nonce, tag = cipher.encrypt_gcm(plaintext_hex.encode("ascii"))
    return nonce + tag + ct


def _decrypt_share_value(blob: bytes) -> str:
    from app.utils.crypto import SM4Cipher
    cipher = SM4Cipher(key=_share_cipher_key())
    nonce, tag, ct = blob[:12], blob[12:28], blob[28:]
    return cipher.decrypt_gcm(ct, nonce, tag).decode("ascii")


def _share_from_record(rec: MPCKeyShareRecord, threshold: int, total_shares: int) -> KeyShare:
    return KeyShare(
        share_id=rec.share_id,
        key_id=rec.key_id,
        index=rec.share_index,
        share_value=_decrypt_share_value(rec.share_value),
        threshold=threshold,
        total_shares=total_shares,
        holder=rec.holder_id,
        created_at=rec.created_at,
    )


def _key_from_record(rec: MPCKeyRecord, shares: list[KeyShare]) -> MPCKey:
    return MPCKey(
        key_id=rec.key_id,
        algorithm=rec.algorithm,
        threshold=rec.threshold,
        total_shares=rec.total_shares,
        shares=shares,
        created_at=rec.created_at,
        status=rec.status,
        persisted=True,
    )


class MPCService:
    """Multi-Party Computation key sharing service (DB-persisted)."""

    async def split_key(
        self,
        db: AsyncSession,
        secret: bytes,
        threshold: int,
        total_shares: int,
        algorithm: str = "sm4",
        holders: list[str] | None = None,
    ) -> MPCKey:
        """Split a secret key into shares and persist them."""
        if threshold < 2:
            raise ValueError("Threshold must be at least 2")
        if total_shares < threshold:
            raise ValueError("Total shares must be >= threshold")
        if total_shares > 255:
            raise ValueError("Total shares must be <= 255")

        key_id = str(uuid.uuid4())[:8]

        # Convert secret to integer
        secret_int = int.from_bytes(secret, "big") % _PRIME

        # a0 = secret, a1..a(t-1) = random coefficients
        coefficients = [secret_int]
        for _ in range(threshold - 1):
            coefficients.append(secrets.randbelow(_PRIME))

        # Generate shares
        shares: list[KeyShare] = []
        for i in range(1, total_shares + 1):
            y = _eval_polynomial(coefficients, i, _PRIME)
            share_id = f"{key_id}-s{i}"
            holder = holders[i - 1] if holders and i <= len(holders) else None

            share = KeyShare(
                share_id=share_id,
                key_id=key_id,
                index=i,
                share_value=format(y, "064x"),
                threshold=threshold,
                total_shares=total_shares,
                holder=holder,
            )
            shares.append(share)
            db.add(MPCKeyShareRecord(
                share_id=share_id,
                key_id=key_id,
                share_index=i,
                share_value=_encrypt_share_value(share.share_value),
                holder_id=holder,
            ))

        db.add(MPCKeyRecord(
            key_id=key_id,
            algorithm=algorithm,
            threshold=threshold,
            total_shares=total_shares,
            status="active",
        ))
        await db.commit()

        return MPCKey(
            key_id=key_id,
            algorithm=algorithm,
            threshold=threshold,
            total_shares=total_shares,
            shares=shares,
            status="active",
            persisted=True,
        )

    async def reconstruct_key(self, db: AsyncSession, key_id: str, share_ids: list[str]) -> bytes:
        """Reconstruct the secret from shares (must meet threshold)."""
        key_rec = await self._load_key(db, key_id)
        if key_rec.status != "active":
            raise ValueError(f"Key {key_id} is {key_rec.status} and cannot be reconstructed")

        if len(share_ids) < key_rec.threshold:
            raise ValueError(f"Need at least {key_rec.threshold} shares, got {len(share_ids)}")

        result = await db.execute(select(MPCKeyShareRecord).where(MPCKeyShareRecord.share_id.in_(share_ids)))
        found = {r.share_id: r for r in result.scalars().all()}
        if len(found) < key_rec.threshold:
            raise ValueError(f"Need at least {key_rec.threshold} shares, got {len(found)}")

        points: list[tuple[int, int]] = []
        for sid in share_ids:
            rec = found.get(sid)
            if not rec or rec.key_id != key_id:
                raise ValueError(f"Invalid share {sid} for key {key_id}")
            y = int(_decrypt_share_value(rec.share_value), 16)
            points.append((rec.share_index, y))

        secret_int = _lagrange_interpolate(points, _PRIME)
        return secret_int.to_bytes(32, "big")

    async def get_key(self, db: AsyncSession, key_id: str) -> MPCKey | None:
        key_rec = await self._load_key(db, key_id)
        if not key_rec:
            return None
        shares = await self._load_shares(db, key_id)
        return _key_from_record(key_rec, shares)

    async def get_share(self, db: AsyncSession, share_id: str) -> KeyShare | None:
        result = await db.execute(
            select(MPCKeyShareRecord).where(MPCKeyShareRecord.share_id == share_id)
        )
        rec = result.scalar_one_or_none()
        if not rec:
            return None
        key_rec = await self._load_key(db, rec.key_id)
        return _share_from_record(rec, key_rec.threshold, key_rec.total_shares)

    async def list_keys(self, db: AsyncSession) -> list[MPCKey]:
        result = await db.execute(select(MPCKeyRecord).order_by(MPCKeyRecord.created_at))
        keys = result.scalars().all()
        if not keys:
            return []
        key_ids = [k.key_id for k in keys]
        share_result = await db.execute(
            select(MPCKeyShareRecord).where(MPCKeyShareRecord.key_id.in_(key_ids))
        )
        by_key: dict[str, list[MPCKeyShareRecord]] = {}
        for rec in share_result.scalars().all():
            by_key.setdefault(rec.key_id, []).append(rec)
        out = []
        for k in keys:
            recs = by_key.get(k.key_id, [])
            shares = [_share_from_record(r, k.threshold, k.total_shares) for r in recs]
            out.append(_key_from_record(k, shares))
        return out

    async def list_shares(self, db: AsyncSession, key_id: str) -> list[KeyShare]:
        key_rec = await self._load_key(db, key_id)
        if not key_rec:
            return []
        recs = await self._load_shares(db, key_id)
        return [_share_from_record(r, key_rec.threshold, key_rec.total_shares) for r in recs]

    async def rotate_key(self, db: AsyncSession, key_id: str, new_secret: bytes | None = None) -> MPCKey:
        """Rotate a key — mark old rotated, generate fresh shares."""
        old_key = await self._load_key(db, key_id)
        if not old_key:
            raise ValueError(f"Key {key_id} not found")
        if old_key.status != "active":
            raise ValueError(f"Key {key_id} is {old_key.status} and cannot be rotated")

        old_holders = await self._load_holders(db, key_id)
        now = datetime.now(timezone.utc)
        old_key.status = "rotated"
        old_key.rotated_at = now

        # Crypto-erase old shares (drop the encrypted blobs).
        await db.execute(delete(MPCKeyShareRecord).where(MPCKeyShareRecord.key_id == key_id))

        if new_secret is None:
            new_secret = os.urandom(32)
        await db.commit()

        return await self.split_key(
            db,
            secret=new_secret,
            threshold=old_key.threshold,
            total_shares=old_key.total_shares,
            algorithm=old_key.algorithm,
            holders=old_holders,
        )

    async def destroy_key(self, db: AsyncSession, key_id: str) -> bool:
        """Destroy a key — mark destroyed and crypto-erase all shares."""
        key_rec = await self._load_key(db, key_id)
        if not key_rec:
            return False
        if key_rec.status == "destroyed":
            return True

        key_rec.status = "destroyed"
        await db.execute(delete(MPCKeyShareRecord).where(MPCKeyShareRecord.key_id == key_id))
        await db.commit()
        return True

    async def verify_shares(self, db: AsyncSession, key_id: str, share_ids: list[str]) -> bool:
        key_rec = await self._load_key(db, key_id)
        if not key_rec:
            return False
        result = await db.execute(
            select(MPCKeyShareRecord).where(
                MPCKeyShareRecord.key_id == key_id,
                MPCKeyShareRecord.share_id.in_(share_ids),
            )
        )
        return len(result.scalars().all()) >= key_rec.threshold

    async def _load_key(self, db: AsyncSession, key_id: str) -> MPCKeyRecord | None:
        result = await db.execute(select(MPCKeyRecord).where(MPCKeyRecord.key_id == key_id))
        return result.scalar_one_or_none()

    async def _load_shares(self, db: AsyncSession, key_id: str) -> list[MPCKeyShareRecord]:
        result = await db.execute(
            select(MPCKeyShareRecord).where(MPCKeyShareRecord.key_id == key_id)
        )
        return list(result.scalars().all())

    async def _load_holders(self, db: AsyncSession, key_id: str) -> list[str]:
        result = await db.execute(
            select(MPCKeyShareRecord.holder_id).where(MPCKeyShareRecord.key_id == key_id)
        )
        return [h for h in result.scalars().all() if h]


mpc_service = MPCService()
