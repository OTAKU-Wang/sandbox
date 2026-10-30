"""A143（high）— MPC 实现族：分片静态密钥、列表即解密、holder 无序、空指针、32 位主键。

台账: docs/ocr-review-2026-10-18/ocr-verified-findings.md §A143

① 分片静态加密的 KEK 与出口审计共用 `SHA256(AUDIT_ENCRYPTION_KEY or JWT_SECRET_KEY)`，
   无域分隔、无版本号；② `list_keys` 只为数分片个数却把全库分片解密到内存；
③ `_load_holders` 无 order_by 且滤掉 None，按位置绑 holder↔分片下标会错位；
④ `get_share` 在孤立分片（key 行已没）上直接 AttributeError；
⑤ `key_id = uuid4[:8]` 只有 32 位，碰撞后 `_load_key` 的 scalar_one_or_none 抛
   MultipleResultsFound ⇒ 整把密钥不可用；⑥ 重复 x 坐标时 `pow(0, m-2, m)` 恒为 0，
   Lagrange 该项静默贡献 0 ⇒ 不报错的错密钥；`% _PRIME` 还会悄悄改掉 DEK。
"""
from __future__ import annotations

import hashlib

import pytest
from sqlalchemy import delete, select, update

from app.core.config import get_settings
from app.models.mpc_key import MPCKeyRecord, MPCKeyShareRecord
from app.services import mpc_service as mpc_mod
from app.services.mpc_service import (
    MPCService,
    _PRIME,
    _decrypt_share_value,
    _encrypt_share_value,
    _lagrange_interpolate,
    _share_cipher_key,
)


async def _rows(db, key_id: str) -> list[MPCKeyShareRecord]:
    res = await db.execute(
        select(MPCKeyShareRecord).where(MPCKeyShareRecord.key_id == key_id)
        .order_by(MPCKeyShareRecord.share_index)
    )
    return list(res.scalars().all())


def test_share_kek_is_domain_separated_from_egress_audit():
    """① 分片 KEK 不得等于「审计主秘密的裸 SHA-256」——那是出口审计行用的同一把钥匙。"""
    settings = get_settings()
    secret = settings.AUDIT_ENCRYPTION_KEY or settings.JWT_SECRET_KEY
    legacy_shared_kek = hashlib.sha256(secret.encode("utf-8")).digest()

    assert _share_cipher_key() != legacy_shared_kek, (
        "share-at-rest KEK is byte-identical to the egress-audit KEK: one rotation "
        "breaks the other consumer and both purposes share one key"
    )


def test_share_blob_is_versioned_and_legacy_blob_still_reads():
    """① 新写入自带版本前缀；没有前缀的历史密文仍可解（不要求停机迁移）。"""
    settings = get_settings()
    legacy_key = hashlib.sha256(
        (settings.AUDIT_ENCRYPTION_KEY or settings.JWT_SECRET_KEY).encode("utf-8")
    ).digest()

    blob = _encrypt_share_value("ab" * 32)
    assert blob[:4] == b"MPC1", "new share blobs must carry a KEK-scheme version tag"

    from app.utils.crypto import SM4Cipher

    cipher = SM4Cipher(key=legacy_key)
    ct, nonce, tag = cipher.encrypt_gcm(("cd" * 32).encode("ascii"))
    legacy_blob = nonce + tag + ct
    assert _decrypt_share_value(legacy_blob) == "cd" * 32


async def test_listing_never_decrypts_share_values(db_session, monkeypatch):
    """② list_keys / get_key 只数个数，不得把明文分片拉进内存。"""
    def boom(_blob):
        raise AssertionError("listing path decrypted a share value")

    monkeypatch.setattr(mpc_mod, "_decrypt_share_value", boom)

    svc = MPCService()
    key = await svc.split_key(db_session, b"\x55" * 32, 2, 3, holders=["a", "b", "c"])

    keys = await svc.list_keys(db_session)
    mine = next(k for k in keys if k.key_id == key.key_id)
    assert len(mine.shares) == 3
    assert all(s.share_value == mpc_mod.SHARE_VALUE_REDACTED for s in mine.shares)

    got = await svc.get_key(db_session, key.key_id)
    assert got is not None and len(got.shares) == 3
    assert all(s.share_value == mpc_mod.SHARE_VALUE_REDACTED for s in got.shares)

    with pytest.raises(AssertionError, match="decrypted a share value"):
        await svc.list_shares(db_session, key.key_id)


async def test_loaded_holders_follow_share_index_not_row_order(db_session):
    """③ 物理插入顺序被打乱后，holder 列表仍按 share_index 排列。"""
    svc = MPCService()
    key = await svc.split_key(db_session, b"\x66" * 32, 2, 3, holders=["a", "b", "c"])
    rows = await _rows(db_session, key.key_id)

    await db_session.execute(
        delete(MPCKeyShareRecord).where(MPCKeyShareRecord.key_id == key.key_id)
    )
    await db_session.commit()
    for rec in reversed(rows):
        db_session.add(MPCKeyShareRecord(
            share_id=rec.share_id, key_id=key.key_id, share_index=rec.share_index,
            share_value=rec.share_value, holder_id=rec.holder_id,
        ))
    await db_session.commit()

    assert await svc._load_holders(db_session, key.key_id) == ["a", "b", "c"]


async def test_empty_holder_slot_keeps_its_position(db_session):
    """③ 中间 holder 为空时必须占住下标，否则轮换把 c 绑到 2 号分片上。"""
    svc = MPCService()
    key = await svc.split_key(db_session, b"\x77" * 32, 2, 3, holders=["a", "b", "c"])
    await db_session.execute(
        update(MPCKeyShareRecord)
        .where(MPCKeyShareRecord.key_id == key.key_id, MPCKeyShareRecord.share_index == 2)
        .values(holder_id=None)
    )
    await db_session.commit()

    assert await svc._load_holders(db_session, key.key_id) == ["a", None, "c"]

    rotated = await svc.rotate_key(db_session, key.key_id)
    rows = await _rows(db_session, rotated.key_id)
    assert [r.holder_id for r in rows] == ["a", None, "c"]


async def test_get_share_on_orphan_share_returns_none(db_session):
    """④ key 行已没的孤立分片：不得 AttributeError 冒成 500。"""
    svc = MPCService()
    key = await svc.split_key(db_session, b"\x88" * 32, 2, 2, holders=["a", "b"])
    share_id = key.shares[0].share_id
    await db_session.execute(
        delete(MPCKeyRecord).where(MPCKeyRecord.key_id == key.key_id)
    )
    await db_session.commit()

    assert await svc.get_share(db_session, share_id) is None


async def test_key_id_has_128_bits_of_entropy(db_session):
    """⑤ 32 位主键撞上就是 MultipleResultsFound ⇒ 整把密钥永久不可用。"""
    svc = MPCService()
    a = await svc.split_key(db_session, b"\x99" * 32, 2, 2)
    b = await svc.split_key(db_session, b"\xaa" * 32, 2, 2)

    assert len(a.key_id) >= 32, f"key_id carries only {len(a.key_id) * 4} bits"
    assert a.key_id != b.key_id
    assert len(a.shares[0].share_id) <= 128


async def test_duplicate_x_coordinate_is_rejected_not_silently_wrong():
    """⑥ 重复 x：pow(0, m-2, m) 恒为 0，HEAD 会算出一把「不报错的错密钥」。"""
    with pytest.raises(ValueError, match="duplicate"):
        _lagrange_interpolate([(1, 11), (1, 22), (2, 33)], _PRIME)


async def test_secret_at_or_above_prime_is_rejected(db_session):
    """⑥ `% _PRIME` 悄悄改掉 DEK：托管方拿到手的已经不是它交进来的密钥。"""
    svc = MPCService()
    with pytest.raises(ValueError, match="out of range"):
        await svc.split_key(db_session, b"\xff" * 32, 2, 3)
