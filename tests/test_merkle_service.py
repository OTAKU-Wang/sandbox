"""Tests for Merkle Service."""
import pytest

from app.services.merkle_service import MerkleService, MerkleProof, MerkleBatch


@pytest.fixture
def service():
    return MerkleService()


class TestMerkleTree:
    def test_hash_leaf(self, service):
        h1 = service.hash_leaf(b"event1")
        h2 = service.hash_leaf(b"event2")
        assert h1 != h2
        assert len(h1) == 64  # SHA-256 hex

    def test_hash_internal(self, service):
        h1 = service.hash_leaf(b"a")
        h2 = service.hash_leaf(b"b")
        parent = service.hash_internal(h1, h2)
        assert len(parent) == 64
        assert parent != h1
        assert parent != h2

    def test_build_tree_single_leaf(self, service):
        leaves = [service.hash_leaf(b"single")]
        tree = service.build_tree(leaves)
        assert len(tree) >= 1
        root = service.get_root(leaves)
        assert len(root) == 64  # Valid hash

    def test_build_tree_two_leaves(self, service):
        leaves = [service.hash_leaf(b"a"), service.hash_leaf(b"b")]
        tree = service.build_tree(leaves)
        assert len(tree) == 2
        root = service.get_root(leaves)
        assert len(root) == 64

    def test_build_tree_power_of_two(self, service):
        leaves = [service.hash_leaf(f"e{i}".encode()) for i in range(4)]
        tree = service.build_tree(leaves)
        assert len(tree) == 3  # 4 leaves → 3 levels
        root = service.get_root(leaves)
        assert len(root) == 64

    def test_build_tree_odd_leaves(self, service):
        leaves = [service.hash_leaf(f"e{i}".encode()) for i in range(3)]
        tree = service.build_tree(leaves)
        root = service.get_root(leaves)
        assert len(root) == 64

    def test_deterministic_root(self, service):
        leaves = [service.hash_leaf(f"e{i}".encode()) for i in range(4)]
        root1 = service.get_root(leaves)
        root2 = service.get_root(leaves)
        assert root1 == root2

    def test_different_leaves_different_roots(self, service):
        leaves1 = [service.hash_leaf(b"a"), service.hash_leaf(b"b")]
        leaves2 = [service.hash_leaf(b"c"), service.hash_leaf(b"d")]
        assert service.get_root(leaves1) != service.get_root(leaves2)


class TestMerkleBatch:
    def test_build_batch(self, service):
        events = [b"event1", b"event2", b"event3"]
        batch = service.build_batch(events)
        assert batch.leaf_count == 3
        assert len(batch.root_hash) == 64
        assert len(batch.leaves) == 3

    def test_verify_batch_root(self, service):
        events = [b"e1", b"e2", b"e3", b"e4"]
        batch = service.build_batch(events)
        valid, computed = service.verify_batch_root(events, batch.root_hash)
        assert valid is True
        assert computed == batch.root_hash

    def test_verify_batch_root_mismatch(self, service):
        events = [b"e1", b"e2"]
        valid, computed = service.verify_batch_root(events, "0" * 64)
        assert valid is False


class TestMerkleProof:
    def test_generate_and_verify_proof(self, service):
        leaves = [service.hash_leaf(f"e{i}".encode()) for i in range(4)]
        proof = service.generate_proof(leaves, 0)
        assert proof is not None
        assert proof.leaf_index == 0
        assert proof.leaf_hash == leaves[0]
        assert service.verify_proof(proof) is True

    def test_proof_leaf_1(self, service):
        leaves = [service.hash_leaf(f"e{i}".encode()) for i in range(4)]
        proof = service.generate_proof(leaves, 1)
        assert proof is not None
        assert service.verify_proof(proof) is True

    def test_proof_leaf_3(self, service):
        leaves = [service.hash_leaf(f"e{i}".encode()) for i in range(4)]
        proof = service.generate_proof(leaves, 3)
        assert proof is not None
        assert service.verify_proof(proof) is True

    def test_proof_invalid_index(self, service):
        leaves = [service.hash_leaf(f"e{i}".encode()) for i in range(4)]
        proof = service.generate_proof(leaves, 10)
        assert proof is None

    def test_proof_negative_index(self, service):
        leaves = [service.hash_leaf(f"e{i}".encode()) for i in range(4)]
        proof = service.generate_proof(leaves, -1)
        assert proof is None

    def test_tampered_proof_fails(self, service):
        leaves = [service.hash_leaf(f"e{i}".encode()) for i in range(4)]
        proof = service.generate_proof(leaves, 0)
        assert proof is not None
        # Tamper with proof path
        proof.proof_path[0]["hash"] = "0" * 64
        assert service.verify_proof(proof) is False

    def test_proof_odd_leaves(self, service):
        leaves = [service.hash_leaf(f"e{i}".encode()) for i in range(5)]
        for i in range(5):
            proof = service.generate_proof(leaves, i)
            assert proof is not None
            assert service.verify_proof(proof) is True

    def test_proof_path_length(self, service):
        leaves = [service.hash_leaf(f"e{i}".encode()) for i in range(8)]
        proof = service.generate_proof(leaves, 0)
        assert proof is not None
        # 8 leaves = 3 levels → proof path has 3 steps
        assert len(proof.proof_path) == 3
