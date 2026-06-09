"""MPC (Multi-Party Computation) Key Sharing Protocol.

Implements Shamir's Secret Sharing for secure key distribution.
Supports key splitting, share distribution, and reconstruction.
"""
import os
import uuid
import hashlib
import secrets
from datetime import datetime, timedelta
from dataclasses import dataclass, field


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
    created_at: datetime = field(default_factory=datetime.now)
    expires_at: datetime | None = None


@dataclass
class MPCKey:
    """Managed MPC key with split shares."""
    key_id: str
    algorithm: str  # sm4, aes256
    threshold: int  # minimum shares to reconstruct
    total_shares: int
    shares: list[KeyShare] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    metadata: dict = field(default_factory=dict)


class MPCService:
    """Multi-Party Computation key sharing service."""

    def __init__(self):
        self._keys: dict[str, MPCKey] = {}
        self._shares: dict[str, KeyShare] = {}

    def split_key(
        self,
        secret: bytes,
        threshold: int,
        total_shares: int,
        algorithm: str = "sm4",
        holders: list[str] | None = None,
    ) -> MPCKey:
        """Split a secret key into shares using Shamir's Secret Sharing.

        Args:
            secret: The secret key bytes to split
            threshold: Minimum number of shares needed to reconstruct
            total_shares: Total number of shares to generate
            algorithm: Key algorithm identifier
            holders: Optional list of party names holding shares

        Returns:
            MPCKey with generated shares
        """
        if threshold < 2:
            raise ValueError("Threshold must be at least 2")
        if total_shares < threshold:
            raise ValueError("Total shares must be >= threshold")
        if total_shares > 255:
            raise ValueError("Total shares must be <= 255")

        key_id = str(uuid.uuid4())[:8]

        # Convert secret to integer
        secret_int = int.from_bytes(secret, "big")
        secret_int = secret_int % _PRIME

        # Generate random polynomial coefficients
        # a0 = secret, a1..a(t-1) = random
        coefficients = [secret_int]
        for _ in range(threshold - 1):
            coefficients.append(secrets.randbelow(_PRIME))

        # Generate shares
        shares = []
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
            self._shares[share_id] = share

        mpc_key = MPCKey(
            key_id=key_id,
            algorithm=algorithm,
            threshold=threshold,
            total_shares=total_shares,
            shares=shares,
        )
        self._keys[key_id] = mpc_key
        return mpc_key

    def reconstruct_key(self, key_id: str, share_ids: list[str]) -> bytes:
        """Reconstruct the secret from shares.

        Args:
            key_id: The key ID
            share_ids: List of share IDs (must meet threshold)

        Returns:
            Reconstructed secret key bytes
        """
        mpc_key = self._keys.get(key_id)
        if not mpc_key:
            raise ValueError(f"Key {key_id} not found")

        if len(share_ids) < mpc_key.threshold:
            raise ValueError(f"Need at least {mpc_key.threshold} shares, got {len(share_ids)}")

        # Collect share points
        points = []
        for sid in share_ids:
            share = self._shares.get(sid)
            if not share or share.key_id != key_id:
                raise ValueError(f"Invalid share {sid} for key {key_id}")
            y = int(share.share_value, 16)
            points.append((share.index, y))

        # Reconstruct using Lagrange interpolation
        secret_int = _lagrange_interpolate(points, _PRIME)

        # Convert back to bytes (32 bytes for 256-bit key)
        secret_bytes = secret_int.to_bytes(32, "big")
        return secret_bytes

    def get_key(self, key_id: str) -> MPCKey | None:
        return self._keys.get(key_id)

    def get_share(self, share_id: str) -> KeyShare | None:
        return self._shares.get(share_id)

    def list_keys(self) -> list[MPCKey]:
        return list(self._keys.values())

    def list_shares(self, key_id: str) -> list[KeyShare]:
        mpc_key = self._keys.get(key_id)
        if not mpc_key:
            return []
        return mpc_key.shares

    def rotate_key(self, key_id: str, new_secret: bytes | None = None) -> MPCKey:
        """Rotate a key — generate new shares with same parameters."""
        old_key = self._keys.get(key_id)
        if not old_key:
            raise ValueError(f"Key {key_id} not found")

        if new_secret is None:
            new_secret = os.urandom(32)

        # Remove old shares
        for share in old_key.shares:
            self._shares.pop(share.share_id, None)
        self._keys.pop(key_id)

        # Create new key with same parameters
        return self.split_key(
            secret=new_secret,
            threshold=old_key.threshold,
            total_shares=old_key.total_shares,
            algorithm=old_key.algorithm,
            holders=[s.holder for s in old_key.shares if s.holder],
        )

    def verify_shares(self, key_id: str, share_ids: list[str]) -> bool:
        """Verify that shares are valid for a key (without reconstructing)."""
        mpc_key = self._keys.get(key_id)
        if not mpc_key:
            return False

        valid_count = 0
        for sid in share_ids:
            share = self._shares.get(sid)
            if share and share.key_id == key_id:
                valid_count += 1

        return valid_count >= mpc_key.threshold


mpc_service = MPCService()
