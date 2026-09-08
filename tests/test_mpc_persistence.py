"""N3: MPC persistence — shares survive service-instance restart, destroy
crypto-erases, threshold semantics are preserved (specs/sandbox-productization-round3-spec.md N3).
"""
import os

import pytest

from app.services.mpc_service import MPCService


@pytest.mark.asyncio
async def test_split_survives_restart(db_session):
    """A fresh service instance (simulating a process restart) can still
    reconstruct a key split by a previous instance, because shares are
    persisted in the DB — not process memory."""
    secret = os.urandom(32)
    svc1 = MPCService()
    key = await svc1.split_key(db_session, secret, threshold=2, total_shares=3)
    share_ids = [s.share_id for s in key.shares[:2]]

    # New instance + new session = process restart with no shared memory.
    svc2 = MPCService()
    reconstructed = await svc2.reconstruct_key(db_session, key.key_id, share_ids)
    assert reconstructed == secret

    # Listed keys also survive the restart.
    keys = await svc2.list_keys(db_session)
    assert any(k.key_id == key.key_id for k in keys)


@pytest.mark.asyncio
async def test_destroy_prevents_reconstruct(db_session):
    """Destroyed keys cannot be reconstructed (shares crypto-erased)."""
    svc = MPCService()
    secret = os.urandom(32)
    key = await svc.split_key(db_session, secret, threshold=2, total_shares=3)
    share_ids = [s.share_id for s in key.shares[:2]]

    assert await svc.destroy_key(db_session, key.key_id) is True

    with pytest.raises(ValueError, match="destroyed"):
        await svc.reconstruct_key(db_session, key.key_id, share_ids)

    # Shares are gone from the store.
    assert await svc.list_shares(db_session, key.key_id) == []


@pytest.mark.asyncio
async def test_destroy_unknown_key_returns_false(db_session):
    svc = MPCService()
    assert await svc.destroy_key(db_session, "nonexistent") is False


@pytest.mark.asyncio
async def test_threshold_insufficient_still_fails(db_session):
    """Regression: below-threshold reconstruction fails even with persisted keys."""
    svc = MPCService()
    key = await svc.split_key(db_session, os.urandom(32), threshold=3, total_shares=5)
    with pytest.raises(ValueError, match="Need at least 3"):
        await svc.reconstruct_key(db_session, key.key_id, [key.shares[0].share_id])


@pytest.mark.asyncio
async def test_rotate_marks_old_key_rotated(db_session):
    """Rotate marks the old key rotated and makes it non-reconstructable."""
    svc = MPCService()
    secret = os.urandom(32)
    key = await svc.split_key(db_session, secret, threshold=2, total_shares=3)
    old_share_ids = [s.share_id for s in key.shares[:2]]

    new_key = await svc.rotate_key(db_session, key.key_id)
    assert new_key.key_id != key.key_id

    with pytest.raises(ValueError, match="rotated"):
        await svc.reconstruct_key(db_session, key.key_id, old_share_ids)

    # New key reconstructs to a fresh random secret.
    new_share_ids = [s.share_id for s in new_key.shares[:2]]
    assert await svc.reconstruct_key(db_session, new_key.key_id, new_share_ids) != secret
