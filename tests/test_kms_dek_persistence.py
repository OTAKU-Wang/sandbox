"""D4 回归：DEK 持久化 —— api 重启后，旧对象必须仍可解密。

缺陷（2026-11-05 走查 D4）：storage_service.upload 生成 DEK 后只把 wrapped blob
留在 KMS 进程内存，从不落库；api 容器重建后所有已上传对象的信封永久不可解密
（资源 88f9b2c8 即历史损失实例）。修复三层：写侧 upload 落库 wrapped blob 并
mark_persisted；恢复侧 import_wrapped 先 _ensure_kek（boot restore 是重启后第一
个碰 KEK 的动作，否则 A139 unrecoverable 计数被污染）；主密钥文件后端确定性派生
KEK，master_key_persistent 在「文件配了但读不出」时必须报 False（A94 口径）。
"""
from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import select

from app.models.kms import KeyMetadata
from app.services.kms_service import KMSService, kms_service
from app.services.storage_service import StorageService


async def _wait_persisted(key_id: str, timeout: float = 5.0) -> KeyMetadata:
    from app.core.database import async_session

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        async with async_session() as db:
            row = (
                await db.execute(select(KeyMetadata).where(KeyMetadata.key_id == key_id))
            ).scalar_one_or_none()
        if row is not None and row.wrapped_payload:
            return row
        if loop.time() > deadline:
            raise AssertionError(f"DEK {key_id} 的 wrapped_payload 未在 {timeout}s 内落库")
        await asyncio.sleep(0.02)


async def test_upload_persists_wrapped_dek(tmp_path, monkeypatch):
    key_id = None
    try:
        svc = StorageService()
        monkeypatch.setattr(svc, "_local_dir", tmp_path)
        monkeypatch.setattr(svc, "_client", None)
        monkeypatch.setattr("app.services.storage_service.settings.MINIO_ENDPOINT", "")
        monkeypatch.setattr("app.services.storage_service._MINIO_FALLBACK_WARNED", True)

        payload = f"d4-{uuid.uuid4().hex}".encode()
        res = svc.upload(payload, f"d4-{uuid.uuid4().hex}.bin")
        key_id = res["key_id"]
        assert res["encrypted"] is True

        row = await _wait_persisted(key_id)
        plaintext = kms_service.get_key(key_id)
        wrapped = kms_service.export_wrapped(key_id)
        assert plaintext is not None
        assert wrapped is not None
        assert bytes(row.wrapped_payload) == wrapped
        assert bytes(row.wrapped_payload) != plaintext
        assert kms_service.is_persisted(key_id) is True
        assert key_id not in kms_service.unpersisted_key_ids()

        assert svc.download(res["path"]) == payload
        kms_service._wrapped_keys.pop(key_id, None)
        with pytest.raises(ValueError):
            svc.download(res["path"])
        assert kms_service.import_wrapped(key_id, wrapped) is True
        assert svc.download(res["path"]) == payload
    finally:
        if key_id:
            kms_service.destroy_key(key_id)


async def test_restore_import_ensures_kek():
    key_id = kms_service.generate_data_key(f"d4-kek-{uuid.uuid4().hex[:6]}")["key_id"]
    try:
        blob = kms_service.export_wrapped(key_id)
        assert blob is not None

        fresh = KMSService()
        assert fresh._kek_ready is False
        before = fresh.unrecoverable_import_count()
        assert fresh.import_wrapped(key_id, blob) is True
        assert fresh.unrecoverable_import_count() == before
        assert fresh.get_key(key_id) == kms_service.get_key(key_id)
    finally:
        kms_service.destroy_key(key_id)


async def test_master_key_persistent_tracks_loaded_key(tmp_path):
    from app.services.hsm_adapter import SoftwareHSMAdapter

    durable = SoftwareHSMAdapter(master_key_file=str(tmp_path / "kmk.key"))
    assert durable.master_key_persistent is True

    durable._master_key = None
    assert durable.master_key_persistent is False

    ephemeral = SoftwareHSMAdapter()
    assert ephemeral.master_key_persistent is False


async def test_recoverable_by_current_kek_across_processes(tmp_path):
    from app.services.hsm_adapter import SoftwareHSMAdapter

    key_file = tmp_path / "shared-kmk.key"
    first = SoftwareHSMAdapter(master_key_file=str(key_file))
    first.generate_kek("cds-master-kek")
    blob = first.wrap_dek("cds-master-kek", bytes(range(32)))

    fresh = KMSService()
    fresh._hsm = SoftwareHSMAdapter(master_key_file=str(key_file))
    assert fresh.recoverable_by_current_kek(blob) is True
    assert fresh.recoverable_by_current_kek(b"garbage" + blob) is False
