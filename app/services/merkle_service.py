"""Merkle Service — tamper-evident audit log with Merkle tree proofs.

Provides Merkle tree construction, proof generation, and verification
for audit events. Uses SM3 per GM/T 0004 for national crypto compliance.
Supports FISCO BCOS blockchain anchoring.
"""
import hashlib
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Use SM3 if available, fall back to SHA-256 for dev
try:
    from gmssl import sm3 as _sm3
    _GMSSL_AVAILABLE = True
    _HASH_NAME = "SM3"
except ImportError:
    _GMSSL_AVAILABLE = False
    _HASH_NAME = "SHA-256"

# PZ-04 (MR-A14): one process-lifetime warning when the Merkle layer runs on
# the SHA-256 fallback instead of real SM3.
_hash_fallback_warned = False


def _sm3_fallback_allowed() -> bool:
    """Whether the SHA-256 fallback for SM3 (gmssl missing) may be used.

    A148: an unreadable config must not flip this honesty gate open — the
    production contract (PZ-04/MR-A14) is that SHA-256 masquerading as SM3 is
    opt-in, so any failure here returns False (fail closed).
    """
    try:
        from app.core.config import get_settings
        return bool(get_settings().SM3_FALLBACK_ALLOWED)
    except Exception:
        logger.warning("SM3_FALLBACK_ALLOWED unreadable; refusing SHA-256 fallback (fail closed)")
        return False


def _hash(data: bytes) -> bytes:
    """Merkle leaf/node hash — real SM3 when gmssl is installed.

    Fallback (dev only): SHA-256, gated by CDS_SM3_FALLBACK_ALLOWED and
    surfaced with one process-lifetime warning (PZ-04/MR-A14).
    """
    global _hash_fallback_warned
    if _GMSSL_AVAILABLE:
        return bytes.fromhex(_sm3.sm3_hash(list(data)))
    if not _sm3_fallback_allowed():
        raise RuntimeError(
            "gmssl is not installed and CDS_SM3_FALLBACK_ALLOWED=false: "
            "Merkle hashing cannot fall back to SHA-256. Install gmssl (or "
            "Tongsuo) to run real SM3."
        )
    if not _hash_fallback_warned:
        logger.warning(
            "gmssl not installed — Merkle service falls back to SHA-256 "
            "(CDS_SM3_FALLBACK_ALLOWED=true). Install gmssl for real SM3."
        )
        _hash_fallback_warned = True
    return hashlib.sha256(data).digest()


# ─── Audit Merkle leaf preimage (single source of truth) ────────────────────
#
# The leaf string format below is FROZEN. /api/v1/audit/anchor, /verify and
# /merkle-proof all recompute it, and the resulting Merkle root is anchored
# immutably on-chain (contracts/AuditRegistry.sol). Any change to this string —
# separator, field order, or None-rendering — silently makes every previously
# anchored root unverifiable. Do NOT "improve" it; fix ambiguity on the write
# side with escape_leaf_field() instead.

LEAF_FIELD_SEP = "|"
LEAF_ESCAPE_CHAR = "\\"


def merkle_leaf_preimage(log) -> bytes:
    """Return the exact bytes hashed into the Merkle tree for one audit row.

    Byte-for-byte identical to the f-string that app/api/audit.py and
    app/services/cube_egress_audit.py used to duplicate, including `None`
    rendering for a null resource_id (f"{None}" == "None"). Duck-typed: any
    object exposing id/action/resource_type/resource_id/created_at works, which
    lets the format be pinned without a DB round-trip.

    Pinned literally by tests/test_merkle_leaf_preimage.py.
    """
    return f"{log.id}|{log.action}|{log.resource_type}|{log.resource_id}|{log.created_at}".encode()


def escape_leaf_field(value: str | None) -> str | None:
    """Make one free-text leaf field unambiguous under the frozen "|" join.

    The join is neither length-prefixed nor escaped, so a raw "|" inside a field
    lets two different field tuples produce one preimage (action="a|b",
    resource_type="c" vs action="a", resource_type="b|c") — the same anchored
    root for two different facts.

    Encoding: escape char first, then the separator (``\\`` -> ``\\\\``,
    ``|`` -> ``\\|``). That ordering makes it injective and information
    preserving, unlike substituting another character. Values without "|" are
    returned untouched, and a pass-through value can never contain the ``\\|``
    digraph (it holds no "|" at all), so escaped and unescaped fields cannot
    collide with each other either.
    """
    if not isinstance(value, str) or LEAF_FIELD_SEP not in value:
        return value
    return value.replace(LEAF_ESCAPE_CHAR, LEAF_ESCAPE_CHAR * 2).replace(
        LEAF_FIELD_SEP, LEAF_ESCAPE_CHAR + LEAF_FIELD_SEP
    )


@dataclass
class MerkleProof:
    """A Merkle proof for a single leaf."""
    leaf_index: int
    leaf_hash: str
    proof_path: list[dict]  # [{hash, position: "left"|"right"}]
    root_hash: str


@dataclass
class MerkleBatch:
    """A batch of leaves with their computed root."""
    leaves: list[str]
    root_hash: str
    leaf_count: int


class MerkleService:
    """Merkle tree for tamper-evident audit logs.

    Uses SM3 for hashing (GM/T 0004). Supports:
    - Tree construction from audit event hashes
    - Proof generation for individual events
    - Proof verification
    - Batch root computation for blockchain anchoring
    """

    def __init__(self):
        pass  # Uses module-level _hash function

    def hash_leaf(self, data: bytes) -> str:
        """Hash a single leaf (audit event) using SM3."""
        return _hash(b"\x00" + data).hex()

    def hash_internal(self, left: str, right: str) -> str:
        """Hash two child nodes into a parent node using SM3."""
        left_bytes = bytes.fromhex(left)
        right_bytes = bytes.fromhex(right)
        return _hash(b"\x01" + left_bytes + right_bytes).hex()

    def build_tree(self, leaves: list[str]) -> list[list[str]]:
        """Build a Merkle tree from leaf hashes.

        Returns the full tree as a list of levels.
        Level 0 = leaves, Level N = root.
        """
        if not leaves:
            return [[]]

        # Ensure even number of leaves (duplicate last if odd)
        working = list(leaves)
        if len(working) % 2 == 1:
            working.append(working[-1])

        tree = [working]
        current = working

        while len(current) > 1:
            next_level = []
            for i in range(0, len(current), 2):
                left = current[i]
                right = current[i + 1] if i + 1 < len(current) else current[i]
                parent = self.hash_internal(left, right)
                next_level.append(parent)
            tree.append(next_level)
            current = next_level

        return tree

    def get_root(self, leaves: list[str]) -> str:
        """Compute the Merkle root from leaf hashes."""
        tree = self.build_tree(leaves)
        if not tree or not tree[-1]:
            return ""
        return tree[-1][0]

    def build_batch(self, events: list[bytes]) -> MerkleBatch:
        """Build a Merkle batch from raw audit events."""
        leaves = [self.hash_leaf(e) for e in events]
        root = self.get_root(leaves)
        return MerkleBatch(leaves=leaves, root_hash=root, leaf_count=len(leaves))

    def generate_proof(self, leaves: list[str], leaf_index: int) -> MerkleProof | None:
        """Generate a Merkle proof for a specific leaf.

        Args:
            leaves: List of leaf hashes.
            leaf_index: Index of the leaf to prove.

        Returns:
            MerkleProof with the authentication path, or None if invalid.
        """
        if leaf_index < 0 or leaf_index >= len(leaves):
            return None

        tree = self.build_tree(leaves)
        if not tree:
            return None

        proof_path = []
        current_index = leaf_index

        for level in range(len(tree) - 1):
            level_nodes = tree[level]
            if current_index % 2 == 0:
                sibling_index = current_index + 1
                position = "right"
            else:
                sibling_index = current_index - 1
                position = "left"

            # Handle odd-length levels
            if sibling_index >= len(level_nodes):
                sibling_hash = level_nodes[current_index]
            else:
                sibling_hash = level_nodes[sibling_index]

            proof_path.append({
                "hash": sibling_hash,
                "position": position,
            })
            current_index //= 2

        return MerkleProof(
            leaf_index=leaf_index,
            leaf_hash=leaves[leaf_index],
            proof_path=proof_path,
            root_hash=tree[-1][0] if tree[-1] else "",
        )

    def verify_proof(self, proof: MerkleProof) -> bool:
        """Verify a Merkle proof.

        Recomputes the root from the leaf hash and proof path,
        then checks if it matches the claimed root.
        """
        current = proof.leaf_hash

        for step in proof.proof_path:
            if step["position"] == "right":
                current = self.hash_internal(current, step["hash"])
            else:
                current = self.hash_internal(step["hash"], current)

        return current == proof.root_hash

    def verify_batch_root(
        self,
        events: list[bytes],
        expected_root: str,
    ) -> tuple[bool, str]:
        """Verify that a set of events produces the expected root.

        Returns: (is_valid, computed_root)
        """
        batch = self.build_batch(events)
        return batch.root_hash == expected_root, batch.root_hash


# Singleton
merkle_service = MerkleService()
