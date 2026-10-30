"""Blockchain attestation service — process-local hash chain with an optional chain client.

Durable anchoring for CDS lives in ``app/services/blockchain_adapter.py`` (append-only
PG log + optional FISCO BCOS / AntChain backend, each response carrying the
``chain_anchoring_state`` / ``is_consortium_chain`` disclosure). This module keeps the
legacy ``anchor_to_blockchain()`` call shape for callers that only need
success/failure semantics, and it must never be mistaken for a chain receipt:

* without an injected client (``set_client``) an anchor lands in an in-process dict
  that a restart wipes — such a ``tx_hash`` is not verifiable by any later process,
  so it must never be persisted as an on-chain credential (A93: that is exactly how
  ``contracts.blockchain_tx_hash`` became unverifiable);
* every result and ``backend_disclosure()`` state ``is_simulation`` /
  ``is_consortium_chain`` so a caller cannot present the local chain as a consortium chain.
"""
import json
import logging
import threading
from collections import OrderedDict
from dataclasses import dataclass

from app.services.crypto_service import crypto_service

logger = logging.getLogger(__name__)

LOCAL_BACKEND = "local-inprocess"
CHAIN_BACKEND = "fisco-bcos"
# The in-process table is not durable, so growing it forever buys memory, not proof.
MAX_LOCAL_ANCHORS = 10_000


@dataclass
class AttestationResult:
    tx_hash: str | None
    merkle_root: str
    block_number: int | None
    success: bool
    error: str | None = None
    backend: str = LOCAL_BACKEND
    is_simulation: bool = True


class BlockchainService:
    """Blockchain attestation service with a disclosed local hash-chain fallback."""

    def __init__(self):
        self._client = None
        self._local_anchors: "OrderedDict[str, dict[str, object]]" = OrderedDict()
        self._last_hash = "blockchain-service-genesis"
        self._block_number = 0
        self._lock = threading.Lock()
        self._fallback_warned = False
        self.dropped_local_anchors = 0

    def set_client(self, client) -> None:
        """Wire a FISCO BCOS client (or pass None to return to the local chain)."""
        self._client = client

    def _get_client(self):
        return self._client

    def backend_disclosure(self) -> dict[str, object]:
        client = self._get_client()
        if client is not None:
            return {"backend": CHAIN_BACKEND, "is_consortium_chain": True,
                    "durable": True, "in_process_anchors": 0,
                    "dropped_anchors": 0}
        return {"backend": LOCAL_BACKEND, "is_consortium_chain": False,
                "durable": False, "in_process_anchors": len(self._local_anchors),
                "dropped_anchors": self.dropped_local_anchors}

    def compute_merkle_root(self, data_hashes: list[str]) -> str:
        """Compute Merkle tree root from a list of data hashes."""
        if not data_hashes:
            return ""
        if len(data_hashes) == 1:
            return data_hashes[0]

        current = data_hashes[:]
        while len(current) > 1:
            next_level = []
            for i in range(0, len(current), 2):
                left = current[i]
                right = current[i + 1] if i + 1 < len(current) else left
                combined = crypto_service.sm3_hash((left + right).encode())
                next_level.append(combined)
            current = next_level
        return current[0]

    def anchor_to_blockchain(self, merkle_root: str, metadata: dict | None = None) -> AttestationResult:
        """Anchor a Merkle root — on-chain when a client is wired, else locally disclosed."""
        client = self._get_client()
        if client is None:
            self._warn_local_fallback_once()
            return self._anchor_local(merkle_root, metadata)

        try:
            tx_hash = client.call_contract("AuditContract", "anchor", [merkle_root, metadata or {}])
            return AttestationResult(
                tx_hash=tx_hash,
                merkle_root=merkle_root,
                block_number=None,
                success=True,
                backend=CHAIN_BACKEND,
                is_simulation=False,
            )
        except Exception as e:
            logger.exception("chain anchoring failed for root %s", merkle_root[:16])
            return AttestationResult(
                tx_hash=None,
                merkle_root=merkle_root,
                block_number=None,
                success=False,
                error=str(e),
                backend=CHAIN_BACKEND,
                is_simulation=False,
            )

    def verify_attestation(self, tx_hash: str) -> bool:
        """Verify an attestation — against the chain when wired, else this process only."""
        client = self._get_client()
        if client is not None:
            return bool(client.call_contract("AuditContract", "exists", [tx_hash]))
        with self._lock:
            found = tx_hash in self._local_anchors
        if not found:
            logger.warning(
                "verify_attestation(%s…) = False: this backend can only see the %d "
                "anchors of the CURRENT process — a tx_hash anchored before a restart "
                "can never verify here (A93); use blockchain_adapter for durable verification",
                tx_hash[:18], len(self._local_anchors),
            )
        return found

    def _warn_local_fallback_once(self) -> None:
        if self._fallback_warned:
            return
        self._fallback_warned = True
        logger.warning(
            "blockchain_service: no chain client configured — anchoring into the "
            "IN-PROCESS hash chain. This is NOT a consortium-chain attestation: it is "
            "not durable and its tx_hash cannot be verified after a restart "
            "(A93). Configure blockchain_adapter (or call set_client) for real anchoring."
        )

    def _anchor_local(self, merkle_root: str, metadata: dict | None = None) -> AttestationResult:
        payload = json.dumps(metadata or {}, sort_keys=True, separators=(",", ":"))
        with self._lock:
            prev_hash = self._last_hash
            self._block_number += 1
            block_number = self._block_number
            chain_hash = crypto_service.sm3_hash(
                f"{prev_hash}:{block_number}:{merkle_root}:{payload}".encode()
            )
            tx_hash = f"0x{chain_hash}"
            self._local_anchors[tx_hash] = {
                "merkle_root": merkle_root,
                "metadata": metadata or {},
                "prev_hash": prev_hash,
                "chain_hash": chain_hash,
                "block_number": block_number,
            }
            self._last_hash = chain_hash
            while len(self._local_anchors) > MAX_LOCAL_ANCHORS:
                self._local_anchors.popitem(last=False)
                self.dropped_local_anchors += 1
                logger.warning(
                    "local anchor store capped at %d — %d oldest anchors are no longer "
                    "verifiable in this process", MAX_LOCAL_ANCHORS, self.dropped_local_anchors,
                )
        return AttestationResult(
            tx_hash=tx_hash,
            merkle_root=merkle_root,
            block_number=block_number,
            success=True,
            backend=LOCAL_BACKEND,
            is_simulation=True,
        )


blockchain_service = BlockchainService()
