"""A142（high）— MPC 轮换先提交「删除旧分片」，再生成新分片。

台账: docs/ocr-review-2026-10-18/ocr-verified-findings.md §A142

`rotate_key` 在 HEAD 先把 `status="rotated"` + `delete(MPCKeyShareRecord)` 用一次
`await db.commit()` 落盘，之后才生成新分片。中间任何异常（新分片的参数校验、DB 错误、
进程被 kill）都把旧分片永久 crypto-erase 掉而没有新分片 ⇒ 托管密钥不可用且无回滚路径。

本文件钉的是修复后的契约：轮换是原子的——新分片生成失败时，旧密钥必须仍然是
`active` 且分片齐全、可继续重构；成功时新旧两套各自完整。
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.mpc_key import MPCKeyRecord, MPCKeyShareRecord
from app.services import mpc_service as mpc_mod
from app.services.mpc_service import MPCService


async def _share_rows(db, key_id: str) -> list[MPCKeyShareRecord]:
    res = await db.execute(
        select(MPCKeyShareRecord).where(MPCKeyShareRecord.key_id == key_id)
    )
    return list(res.scalars().all())


async def test_rotate_failure_keeps_old_key_recoverable(db_session, monkeypatch):
    """新分片生成中途失败 ⇒ 旧分片一行都不能少、状态不得变、仍可重构。"""
    svc = MPCService()
    secret = b"\x11" * 32
    key = await svc.split_key(db_session, secret, 2, 3, holders=["a", "b", "c"])
    old_share_ids = [s.share_id for s in key.shares]

    def boom(coefficients, x, prime):
        raise RuntimeError("simulated failure while generating the replacement shares")

    monkeypatch.setattr(mpc_mod, "_eval_polynomial", boom)
    with pytest.raises(RuntimeError, match="simulated failure"):
        await svc.rotate_key(db_session, key.key_id)
    monkeypatch.undo()

    rec = await db_session.get(MPCKeyRecord, key.key_id)
    assert rec is not None, "old key row vanished during a failed rotation"
    assert rec.status == "active", f"old key was left {rec.status!r} by a FAILED rotation"

    rows = await _share_rows(db_session, key.key_id)
    assert len(rows) == 3, f"failed rotation crypto-erased the old shares ({len(rows)} left)"

    assert await svc.reconstruct_key(db_session, key.key_id, old_share_ids) == secret


async def test_rotate_success_is_single_commit(db_session, monkeypatch):
    """成功路径：新密钥可用，旧密钥标 rotated 且旧分片清空——但只在提交点之后可见。"""
    svc = MPCService()
    secret = b"\x22" * 32
    key = await svc.split_key(db_session, secret, 2, 3, holders=["a", "b", "c"])

    commits = {"n": 0}
    real_commit = db_session.commit

    async def counting_commit():
        commits["n"] += 1
        await real_commit()

    monkeypatch.setattr(db_session, "commit", counting_commit)
    new_key = await svc.rotate_key(db_session, key.key_id)
    monkeypatch.undo()

    assert commits["n"] == 1, (
        f"rotation took {commits['n']} commits — a multi-commit rotation has a "
        "crash window in which the old shares are gone and the new ones never landed"
    )
    assert new_key.key_id != key.key_id
    old = await db_session.get(MPCKeyRecord, key.key_id)
    assert old is not None and old.status == "rotated"
    assert await _share_rows(db_session, key.key_id) == []
    assert len(await _share_rows(db_session, new_key.key_id)) == 3
    rows = await _share_rows(db_session, new_key.key_id)
    assert sorted(r.holder_id or "-" for r in rows) == ["a", "b", "c"]


async def test_rotate_validation_error_leaves_nothing_behind(db_session, monkeypatch):
    """台账点名的触发条件之一：split_key 自己的参数校验抛错。"""
    svc = MPCService()
    key = await svc.split_key(db_session, b"\x33" * 32, 2, 2, holders=["a", "b"])

    rec = await db_session.get(MPCKeyRecord, key.key_id)
    rec.total_shares = 1
    await db_session.commit()

    with pytest.raises(ValueError):
        await svc.rotate_key(db_session, key.key_id, new_secret=b"\x44" * 32)

    rec = await db_session.get(MPCKeyRecord, key.key_id)
    assert rec.status == "active"
    assert len(await _share_rows(db_session, key.key_id)) == 2
