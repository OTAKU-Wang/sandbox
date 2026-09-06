"""KMS DEK at-rest protection (Round 34, gap: api/kms.py stored plaintext DEK).

The ``DataEncryptionKey.encrypted_key`` column must persist the KEK-wrapped
envelope blob (useless without the KEK in HSM/Vault), never the plaintext key
hex. The column is write-only (no reader decrypts from it), so the change is
purely a storage-format hardening.
"""
import uuid

import pytest
from sqlalchemy import select

from app.models.kms import DataEncryptionKey
from app.services.kms_service import kms_service


@pytest.mark.asyncio
async def test_create_dek_persists_wrapped_blob_not_plaintext(client, auth_headers, db_session):
    product_id = uuid.uuid4()
    resp = await client.post(
        "/api/v1/kms/keys", params={"product_id": str(product_id)}, headers=auth_headers
    )
    assert resp.status_code == 201
    key_id = resp.json()["key_id"]

    row = (
        await db_session.execute(
            select(DataEncryptionKey).where(DataEncryptionKey.key_id == key_id)
        )
    ).scalar_one()

    plaintext = kms_service.get_key(key_id)
    assert plaintext is not None

    # At-rest blob must NOT be the plaintext key material.
    assert row.encrypted_key is not None
    assert row.encrypted_key != plaintext.hex()

    # It must equal the KEK-wrapped envelope export.
    wrapped = kms_service.export_wrapped(key_id)
    assert wrapped is not None
    assert row.encrypted_key == wrapped.hex()

    # Round-trip through the HSM KEK still recovers the key.
    assert kms_service.get_key(key_id) == plaintext


@pytest.mark.asyncio
async def test_rotate_dek_persists_wrapped_blob(client, auth_headers, db_session):
    product_id = uuid.uuid4()
    resp = await client.post(
        "/api/v1/kms/keys", params={"product_id": str(product_id)}, headers=auth_headers
    )
    assert resp.status_code == 201
    key_id = resp.json()["key_id"]

    rot = await client.post(f"/api/v1/kms/keys/{key_id}/rotate", headers=auth_headers)
    assert rot.status_code == 200
    new_key_id = rot.json()["new_key_id"]

    new_row = (
        await db_session.execute(
            select(DataEncryptionKey).where(DataEncryptionKey.key_id == new_key_id)
        )
    ).scalar_one()

    assert new_row.encrypted_key is not None
    assert new_row.encrypted_key != kms_service.get_key(new_key_id).hex()
    assert new_row.encrypted_key == kms_service.export_wrapped(new_key_id).hex()


@pytest.mark.asyncio
async def test_dek_list_and_get_do_not_expose_key_material(client, auth_headers):
    """List/get endpoints must keep returning metadata only (no encrypted blob)."""
    product_id = uuid.uuid4()
    resp = await client.post(
        "/api/v1/kms/keys", params={"product_id": str(product_id)}, headers=auth_headers
    )
    assert resp.status_code == 201
    key_id = resp.json()["key_id"]

    detail = await client.get(f"/api/v1/kms/keys/{key_id}", headers=auth_headers)
    assert detail.status_code == 200
    body = detail.json()
    assert "encrypted_key" not in body
    assert body["key_id"] == key_id
