import pytest
from app.services.blockchain_service import BlockchainService


def test_merkle_root():
    service = BlockchainService()
    hashes = ["a", "b", "c", "d"]
    root = service.compute_merkle_root(hashes)
    assert isinstance(root, str)
    assert len(root) == 64  # SHA-256 hex


def test_merkle_root_single():
    service = BlockchainService()
    root = service.compute_merkle_root(["abc"])
    assert root == "abc"


def test_merkle_root_empty():
    service = BlockchainService()
    root = service.compute_merkle_root([])
    assert root == ""


def test_anchor_blockchain():
    service = BlockchainService()
    result = service.anchor_to_blockchain("test-root-hash")
    # Local fallback should anchor successfully even if FISCO is unavailable.
    assert result.merkle_root == "test-root-hash"
    assert result.success is True
    assert result.tx_hash is not None
    assert result.block_number == 1
    assert service.verify_attestation(result.tx_hash) is True
