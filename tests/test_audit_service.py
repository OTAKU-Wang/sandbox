"""Audit service tests — Merkle tree computation."""
import pytest
from app.services.merkle_service import merkle_service


def _hex_hashes(*items):
    """Convert strings to hex-encoded leaf hashes."""
    return [merkle_service.hash_leaf(s.encode()) for s in items]


def test_merkle_root_4_items():
    hashes = _hex_hashes("a", "b", "c", "d")
    root = merkle_service.get_root(hashes)
    assert isinstance(root, str)
    assert len(root) == 64  # SM3/SHA-256 hex


def test_merkle_root_single():
    hashes = _hex_hashes("onlyone")
    root = merkle_service.get_root(hashes)
    assert isinstance(root, str)
    assert len(root) == 64


def test_merkle_root_empty():
    root = merkle_service.get_root([])
    assert root == ""


def test_merkle_root_2_items():
    hashes = _hex_hashes("x", "y")
    root = merkle_service.get_root(hashes)
    assert isinstance(root, str)
    assert len(root) == 64


def test_merkle_root_3_items():
    hashes = _hex_hashes("a", "b", "c")
    root = merkle_service.get_root(hashes)
    assert isinstance(root, str)
    assert len(root) == 64


def test_merkle_root_deterministic():
    hashes = _hex_hashes("a", "b", "c", "d")
    r1 = merkle_service.get_root(hashes)
    r2 = merkle_service.get_root(hashes)
    assert r1 == r2


def test_merkle_root_order_sensitive():
    r1 = merkle_service.get_root(_hex_hashes("a", "b"))
    r2 = merkle_service.get_root(_hex_hashes("b", "a"))
    assert r1 != r2  # Order matters


def test_merkle_root_odd_count():
    hashes = _hex_hashes("a", "b", "c", "d", "e")
    root = merkle_service.get_root(hashes)
    assert isinstance(root, str)
    assert len(root) == 64
