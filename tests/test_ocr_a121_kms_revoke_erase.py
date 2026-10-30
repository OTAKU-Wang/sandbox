"""A121 — revoke 说「data becomes inaccessible」，但密钥材料的每一份拷贝都还在。

A121 的四处「写了不读/说了不做」里，本文件锁死路由侧可闭环的两处：
① `revoke_dek` 过去只翻 status —— `encrypted_key` / `wrapped_payload` /
`sm2_encrypted_payload` 三份包裹块原地不动，重启恢复器还能把密钥从表里重造出来
（对照 `revoke_volume_key` 的 wrapped_key crypto-erase；内存那份 HEAD 是经
`terminate_sessions_by_key` 间接驱逐的，本文件一并钉死防回归）；
② rotate 是版本化而非销毁：ROTATED-out 的旧密钥必须保留包裹块（版本 N 的密文
在 N+1 之后仍要可解），本文件把这条反向语义一并钉死，防止未来「顺手镜像」。
另：HEAD 的 revoke 会被 `terminate_sessions_by_key`（KeyMetadata≡DataEncryptionKey
同表别名）把最终 status 覆盖成 destroyed，本文件同时钉住 revoked 语义。
HSM 层 revoke 原语与 rotate_kek 接线不在本文件能力面（hsm_adapter.py，NEEDS-OWNER）。
"""
import uuid

import pytest
from sqlalchemy import select

from app.models.kms import DataEncryptionKey
from app.services.kms_service import kms_service

pytestmark = pytest.mark.asyncio


async def _create_key(client, headers) -> str:
    resp = await client.post(
        "/api/v1/kms/keys", params={"product_id": str(uuid.uuid4())}, headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["key_id"]


async def test_revoke_crypto_erases_every_persisted_copy(client, admin_headers, db_session):
    created = await _create_key(client, admin_headers)
    key_id = created
    row = (await db_session.execute(
        select(DataEncryptionKey).where(DataEncryptionKey.key_id == key_id)
    )).scalar_one()
    assert row.encrypted_key and row.wrapped_payload, (
        "前提：无证书创建时 SM2 列本为 NULL，加密面至少要有 wrapped 两份"
    )

    resp = await client.request(
        "DELETE", f"/api/v1/kms/keys/{key_id}",
        json={"reason": "A121 crypto-erase probe", "ticket_id": "T-1"},
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    await db_session.refresh(row)
    assert row.encrypted_key is None, "A121: revoke must erase the persisted wrapped blob"
    assert row.wrapped_payload is None, "A121: boot recovery must find nothing to reload"
    assert row.sm2_encrypted_payload is None, "A121: the SM2 copy is key material too"
    assert row.status == "revoked"


async def test_revoked_key_stops_distributing(client, admin_headers):
    """The product claim is 「data becomes inaccessible」 — memory copy included."""
    key_id = await _create_key(client, admin_headers)
    assert kms_service.distribute_key(key_id, "session-before") is not None

    resp = await client.request(
        "DELETE", f"/api/v1/kms/keys/{key_id}",
        json={"reason": "A121 memory eviction probe"}, headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    assert kms_service.get_key(key_id) is None
    assert kms_service.distribute_key(key_id, "session-after") is None, (
        "A121: a revoked key must not keep handing out plaintext from process memory"
    )


async def test_rotate_keeps_old_key_decryptable(client, admin_headers, db_session):
    """Deliberate contrast (pinned): rotation is versioning, NOT destruction.

    Ciphertext written under version N stays decryptable after N+1 exists — so
    the rotated-out row must KEEP its wrapped blobs. If a future change mirrors
    the revoke-side crypto-erase here, this test is what catches the data loss.
    """
    key_id = await _create_key(client, admin_headers)
    resp = await client.post(f"/api/v1/kms/keys/{key_id}/rotate", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    row = (await db_session.execute(
        select(DataEncryptionKey).where(DataEncryptionKey.key_id == key_id)
    )).scalar_one()
    await db_session.refresh(row)
    assert row.status == "rotated"
    assert row.wrapped_payload is not None, "rotate is not destroy: old ciphertext must stay readable"
    assert row.encrypted_key is not None
