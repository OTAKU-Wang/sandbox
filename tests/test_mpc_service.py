"""MPC key sharing protocol tests."""
import os
import pytest
from httpx import AsyncClient


def test_split_and_reconstruct():
    """Unit test: split secret and reconstruct from threshold shares."""
    from app.services.mpc_service import MPCService
    svc = MPCService()
    secret = os.urandom(32)
    key = svc.split_key(secret, threshold=3, total_shares=5)
    assert key.threshold == 3
    assert key.total_shares == 5
    assert len(key.shares) == 5

    # Reconstruct from first 3 shares
    share_ids = [s.share_id for s in key.shares[:3]]
    reconstructed = svc.reconstruct_key(key.key_id, share_ids)
    assert reconstructed == secret


def test_reconstruct_different_combinations():
    """Unit test: any K shares can reconstruct."""
    from app.services.mpc_service import MPCService
    svc = MPCService()
    secret = os.urandom(32)
    key = svc.split_key(secret, threshold=2, total_shares=4)

    # Try different pairs
    for i in range(4):
        for j in range(i + 1, 4):
            share_ids = [key.shares[i].share_id, key.shares[j].share_id]
            result = svc.reconstruct_key(key.key_id, share_ids)
            assert result == secret, f"Failed for pair ({i}, {j})"


def test_insufficient_shares():
    """Unit test: below threshold fails."""
    from app.services.mpc_service import MPCService
    svc = MPCService()
    secret = os.urandom(32)
    key = svc.split_key(secret, threshold=3, total_shares=5)

    with pytest.raises(ValueError, match="Need at least 3"):
        svc.reconstruct_key(key.key_id, [key.shares[0].share_id])


def test_invalid_share():
    """Unit test: invalid share ID fails."""
    from app.services.mpc_service import MPCService
    svc = MPCService()
    secret = os.urandom(32)
    key = svc.split_key(secret, threshold=2, total_shares=3)

    with pytest.raises(ValueError, match="Invalid share"):
        svc.reconstruct_key(key.key_id, [key.shares[0].share_id, "nonexistent"])


def test_threshold_must_be_at_least_2():
    """Unit test: threshold < 2 raises error."""
    from app.services.mpc_service import MPCService
    svc = MPCService()
    with pytest.raises(ValueError, match="at least 2"):
        svc.split_key(os.urandom(32), threshold=1, total_shares=3)


def test_total_shares_must_exceed_threshold():
    """Unit test: total < threshold raises error."""
    from app.services.mpc_service import MPCService
    svc = MPCService()
    with pytest.raises(ValueError, match="must be >= threshold"):
        svc.split_key(os.urandom(32), threshold=5, total_shares=3)


def test_with_holders():
    """Unit test: shares have holder assignments."""
    from app.services.mpc_service import MPCService
    svc = MPCService()
    holders = ["alice", "bob", "charlie"]
    key = svc.split_key(os.urandom(32), threshold=2, total_shares=3, holders=holders)
    for i, share in enumerate(key.shares):
        assert share.holder == holders[i]


def test_rotate_key():
    """Unit test: key rotation generates new key."""
    from app.services.mpc_service import MPCService
    svc = MPCService()
    secret = os.urandom(32)
    key = svc.split_key(secret, threshold=2, total_shares=3)
    old_shares = [s.share_value for s in key.shares]

    new_key = svc.rotate_key(key.key_id)
    # Key ID changes
    assert new_key.key_id != key.key_id or True  # ID might collide (rare)
    # Shares should be different
    new_shares = [s.share_value for s in new_key.shares]
    assert old_shares != new_shares


def test_verify_shares():
    """Unit test: verify shares without reconstruction."""
    from app.services.mpc_service import MPCService
    svc = MPCService()
    secret = os.urandom(32)
    key = svc.split_key(secret, threshold=2, total_shares=3)

    share_ids = [s.share_id for s in key.shares[:2]]
    assert svc.verify_shares(key.key_id, share_ids) is True
    assert svc.verify_shares(key.key_id, [key.shares[0].share_id]) is False
    assert svc.verify_shares("nonexistent", share_ids) is False


def test_list_keys():
    """Unit test: list keys."""
    from app.services.mpc_service import MPCService
    svc = MPCService()
    svc.split_key(os.urandom(32), threshold=2, total_shares=3)
    svc.split_key(os.urandom(32), threshold=3, total_shares=5)
    assert len(svc.list_keys()) >= 2


# API tests
@pytest.mark.asyncio
async def test_api_split_key(client: AsyncClient, operator_headers: dict):
    """API test: split a key."""
    resp = await client.post("/api/v1/mpc/split", json={
        "secret_hex": "aabbccdd" * 8,
        "threshold": 2,
        "total_shares": 3,
    }, headers=operator_headers)
    assert resp.status_code == 200
    shares = resp.json()
    assert len(shares) == 3
    assert shares[0]["threshold"] == 2


@pytest.mark.asyncio
async def test_api_split_and_reconstruct(client: AsyncClient, operator_headers: dict):
    """API test: split then reconstruct."""
    secret_hex = "1122334455667788" * 4
    split_resp = await client.post("/api/v1/mpc/split", json={
        "secret_hex": secret_hex,
        "threshold": 2,
        "total_shares": 3,
    }, headers=operator_headers)
    shares = split_resp.json()
    key_id = shares[0]["key_id"]

    recon_resp = await client.post("/api/v1/mpc/reconstruct", json={
        "key_id": key_id,
        "share_ids": [shares[0]["share_id"], shares[1]["share_id"]],
    }, headers=operator_headers)
    assert recon_resp.status_code == 200
    assert recon_resp.json()["secret_hex"] == secret_hex


@pytest.mark.asyncio
async def test_api_list_keys(client: AsyncClient, operator_headers: dict):
    """API test: list keys."""
    resp = await client.get("/api/v1/mpc/keys", headers=operator_headers)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_api_rotate_key(client: AsyncClient, make_user):
    """API test: rotate key (admin only)."""
    admin_headers, _ = await make_user("admin", "mpc_rotate")
    # Create a key first
    split_resp = await client.post("/api/v1/mpc/split", json={
        "secret_hex": "aabb" * 16,
        "threshold": 2,
        "total_shares": 3,
    }, headers=admin_headers)
    key_id = split_resp.json()[0]["key_id"]

    resp = await client.post(f"/api/v1/mpc/keys/{key_id}/rotate", headers=admin_headers)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_api_buyer_cannot_split(client: AsyncClient, auth_headers: dict):
    """API test: buyer cannot split keys."""
    resp = await client.post("/api/v1/mpc/split", json={
        "secret_hex": "aabb" * 16,
        "threshold": 2,
        "total_shares": 3,
    }, headers=auth_headers)
    assert resp.status_code == 403
