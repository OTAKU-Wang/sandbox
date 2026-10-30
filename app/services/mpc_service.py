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
import hmac
import logging
import os
import secrets
import uuid
from collections.abc import Sequence
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
    """Modular inverse using Fermat's little theorem (m must be prime).

    A143-6: `pow(0, m-2, m)` is 0, not an error, so a non-invertible difference
    (two shares on the same x-coordinate) used to contribute 0 to the sum and
    hand back a silently wrong secret. Fail instead.
    """
    if a % m == 0:
        raise ValueError("Not invertible in the share field (duplicate x-coordinate?)")
    return pow(a, m - 2, m)


def _lagrange_interpolate(points: list[tuple[int, int]], prime: int) -> int:
    """Lagrange interpolation to reconstruct secret at x=0."""
    if not points:
        raise ValueError("Need at least one point")

    xs = [x for x, _ in points]
    if len(set(xs)) != len(xs):
        raise ValueError("duplicate x coordinate in share set — refusing to derive a key")

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


_SHARE_KEK_INFO = b"cds/mpc/share-at-rest/v1"
_SHARE_KEK_TAG = b"MPC1"
_SHARE_KEK_LEN = len(_SHARE_KEK_TAG)

# A143-2: listing surfaces get metadata only; a share value that was never
# decrypted is marked with this sentinel so it cannot be mistaken for plaintext.
SHARE_VALUE_REDACTED = "<redacted:not-loaded>"

logger = logging.getLogger(__name__)
_fallback_kek_warned = False


def _audit_master_secret() -> bytes:
    global _fallback_kek_warned
    settings = get_settings()
    if not settings.AUDIT_ENCRYPTION_KEY and not _fallback_kek_warned:
        _fallback_kek_warned = True
        logger.warning(
            "SECURITY: AUDIT_ENCRYPTION_KEY is unset — MPC share-at-rest KEK falls back "
            "to the JWT signing secret (disclosed fallback, set CDS_AUDIT_ENCRYPTION_KEY)."
        )
    secret = settings.AUDIT_ENCRYPTION_KEY or settings.JWT_SECRET_KEY
    return secret.encode("utf-8")


def _legacy_share_cipher_key() -> bytes:
    """Pre-A143 KEK: bare SHA-256 of the audit master secret (shared with egress_audit).

    Read-only — kept so historical share rows stay decryptable without a stop-the-world
    re-encryption; nothing writes with it anymore.
    """
    return hashlib.sha256(_audit_master_secret()).digest()


def _share_cipher_key() -> bytes:
    """Domain-separated KEK for share-at-rest encryption (A143-1).

    HKDF-extract style (HMAC over a purpose label) so the MPC share KEK and the
    egress-audit row KEK are different keys even though both hang off the same
    master secret; rotating either consumer's key material no longer silently
    breaks the other, and new blobs carry _SHARE_KEK_TAG as the scheme version.
    """
    return hmac.new(_audit_master_secret(), _SHARE_KEK_INFO, hashlib.sha256).digest()


def _encrypt_share_value(plaintext_hex: str) -> bytes:
    from app.utils.crypto import SM4Cipher
    cipher = SM4Cipher(key=_share_cipher_key())
    ct, nonce, tag = cipher.encrypt_gcm(plaintext_hex.encode("ascii"))
    return _SHARE_KEK_TAG + nonce + tag + ct


def _decrypt_share_value(blob: bytes) -> str:
    from app.utils.crypto import SM4Cipher
    if blob[:_SHARE_KEK_LEN] == _SHARE_KEK_TAG:
        cipher = SM4Cipher(key=_share_cipher_key())
        body = blob[_SHARE_KEK_LEN:]
    else:
        cipher = SM4Cipher(key=_legacy_share_cipher_key())
        body = blob
    nonce, tag, ct = body[:12], body[12:28], body[28:]
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


def _share_meta_from_record(rec: MPCKeyShareRecord, threshold: int, total_shares: int) -> KeyShare:
    """Metadata view of a share — never touches the ciphertext (A143-2).

    Used by the listing surfaces, which only need identity/ownership/count.
    """
    return KeyShare(
        share_id=rec.share_id,
        key_id=rec.key_id,
        index=rec.share_index,
        share_value=SHARE_VALUE_REDACTED,
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

    def _build_key_records(
        self,
        secret: bytes,
        threshold: int,
        total_shares: int,
        algorithm: str,
        holders: Sequence[str | None] | None,
    ) -> tuple[MPCKeyRecord, list[MPCKeyShareRecord], list[KeyShare]]:
        """Validate + split + build ORM rows; touches no session and commits nothing.

        A142: keeping the crypto work and the row construction side-effect-free is
        what lets rotate_key stage the replacement shares before the old ones are
        crypto-erased, inside one transaction.
        """
        if threshold < 2:
            raise ValueError("Threshold must be at least 2")
        if total_shares < threshold:
            raise ValueError("Total shares must be >= threshold")
        if total_shares > 255:
            raise ValueError("Total shares must be <= 255")

        secret_int = int.from_bytes(secret, "big")
        if secret_int >= _PRIME:
            # A143-6: `% _PRIME` used to fold such a secret into the field silently,
            # so the escrow held a different key than the caller handed over.
            raise ValueError("secret out of range for the share field (>= 2**256-…) — refusing to alter it")

        # 128-bit key_id: the 8 hex chars this used to generate collide often enough
        # to matter (birthday bound ~2**16 keys), and a duplicate primary key makes
        # _load_key's scalar_one_or_none raise MultipleResultsFound, i.e. the whole
        # key becomes unreachable (A143-5).
        key_id = uuid.uuid4().hex

        coefficients = [secret_int]
        for _ in range(threshold - 1):
            coefficients.append(secrets.randbelow(_PRIME))

        shares: list[KeyShare] = []
        share_recs: list[MPCKeyShareRecord] = []
        for i in range(1, total_shares + 1):
            y = _eval_polynomial(coefficients, i, _PRIME)
            share_id = f"{key_id}-s{i}"
            holder = holders[i - 1] if holders and i <= len(holders) else None

            share_hex = format(y, "064x")
            shares.append(KeyShare(
                share_id=share_id,
                key_id=key_id,
                index=i,
                share_value=share_hex,
                threshold=threshold,
                total_shares=total_shares,
                holder=holder,
            ))
            share_recs.append(MPCKeyShareRecord(
                share_id=share_id,
                key_id=key_id,
                share_index=i,
                share_value=_encrypt_share_value(share_hex),
                holder_id=holder,
            ))

        key_rec = MPCKeyRecord(
            key_id=key_id,
            algorithm=algorithm,
            threshold=threshold,
            total_shares=total_shares,
            status="active",
        )
        return key_rec, share_recs, shares

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
        key_rec, share_recs, shares = self._build_key_records(
            secret, threshold, total_shares, algorithm, holders,
        )
        for rec in share_recs:
            db.add(rec)
        db.add(key_rec)
        await db.commit()

        return MPCKey(
            key_id=key_rec.key_id,
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
        if not key_rec:
            raise ValueError(f"Key {key_id} not found")
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
        return _key_from_record(
            key_rec,
            [_share_meta_from_record(r, key_rec.threshold, key_rec.total_shares) for r in shares],
        )

    async def get_share(self, db: AsyncSession, share_id: str) -> KeyShare | None:
        result = await db.execute(
            select(MPCKeyShareRecord).where(MPCKeyShareRecord.share_id == share_id)
        )
        rec = result.scalar_one_or_none()
        if not rec:
            return None
        key_rec = await self._load_key(db, rec.key_id)
        if not key_rec:
            # A143-4: orphaned share row (key row already gone) used to raise
            # AttributeError on key_rec.threshold and surface as a 500.
            return None
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
            shares = [_share_meta_from_record(r, k.threshold, k.total_shares) for r in recs]
            out.append(_key_from_record(k, shares))
        return out

    async def list_shares(self, db: AsyncSession, key_id: str) -> list[KeyShare]:
        key_rec = await self._load_key(db, key_id)
        if not key_rec:
            return []
        recs = await self._load_shares(db, key_id)
        return [_share_from_record(r, key_rec.threshold, key_rec.total_shares) for r in recs]

    async def rotate_key(self, db: AsyncSession, key_id: str, new_secret: bytes | None = None) -> MPCKey:
        """Rotate a key — stage the replacement shares BEFORE the old ones are erased.

        A142: the old shares are the only durable copy of the escrowed key. Building
        and staging the new shares first, then marking the old key rotated and
        crypto-erasing its rows, and committing that once means any failure (share
        validation, DB error, process kill) leaves the previous generation intact.
        """
        old_key = await self._load_key(db, key_id)
        if not old_key:
            raise ValueError(f"Key {key_id} not found")
        if old_key.status != "active":
            raise ValueError(f"Key {key_id} is {old_key.status} and cannot be rotated")

        old_holders = await self._load_holders(db, key_id)
        if new_secret is None:
            new_secret = os.urandom(32)

        try:
            new_key_rec, new_share_recs, shares = self._build_key_records(
                new_secret, old_key.threshold, old_key.total_shares,
                old_key.algorithm, old_holders,
            )
            for rec in new_share_recs:
                db.add(rec)
            db.add(new_key_rec)

            old_key.status = "rotated"
            old_key.rotated_at = datetime.now(timezone.utc)
            await db.execute(delete(MPCKeyShareRecord).where(MPCKeyShareRecord.key_id == key_id))
            await db.commit()
        except BaseException:
            await db.rollback()
            raise

        return MPCKey(
            key_id=new_key_rec.key_id,
            algorithm=new_key_rec.algorithm,
            threshold=new_key_rec.threshold,
            total_shares=new_key_rec.total_shares,
            shares=shares,
            status="active",
            persisted=True,
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

    async def _load_holders(self, db: AsyncSession, key_id: str) -> list[str | None]:
        """Holders positional-by-share_index (A143-3).

        rotate_key feeds this straight back into split, which binds holders by list
        position — so it must be ordered by share_index and must keep an empty slot
        as None instead of dropping it (dropping shifts every later holder onto the
        wrong x-coordinate).
        """
        result = await db.execute(
            select(MPCKeyShareRecord.share_index, MPCKeyShareRecord.holder_id)
            .where(MPCKeyShareRecord.key_id == key_id)
            .order_by(MPCKeyShareRecord.share_index)
        )
        holders: list[str | None] = []
        for share_index, holder_id in result.all():
            while len(holders) < share_index - 1:
                holders.append(None)
            holders.append(holder_id)
        return holders


mpc_service = MPCService()
