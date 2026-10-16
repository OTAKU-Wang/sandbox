"""Control-plane KMS volume-key endpoints (SS-10 CB-03 / CB-01) — the REST
surface consumed by the Go volume plugin (cds-volume-plugin/kms.go).

Wire contract (field names hardcoded from the Go client, machine-consumed):
  Register:   POST   /api/v1/kms/volume-keys                   {volume_id, name, dek_hex, cipher} -> {key_id}
  Distribute: POST   /api/v1/kms/volume-keys/{ref}/distribute  {session_id}              -> {key_hex}
  Revoke:     DELETE /api/v1/kms/volume-keys/{ref}                          -> 204 (idempotent)
Auth is the service token (machine caller), NOT user JWT.
"""
import uuid

import pytest
from sqlalchemy import select

from app.models.volume_key import VolumeKey

# Go client (kms.go) wire field names — machine-consumed; do not rename.
GO_REGISTER_RESP_KEY = "key_id"
GO_DISTRIBUTE_REQ_KEY = "session_id"
GO_DISTRIBUTE_RESP_KEY = "key_hex"

SERVICE_TOKEN = "test-volume-plugin-service-token"


@pytest.fixture
def service_token(monkeypatch):
    """Enable the KMS volume-key service endpoint with a known token."""
    from app.core.config import get_settings
    monkeypatch.setattr(get_settings(), "KMS_SERVICE_TOKEN", SERVICE_TOKEN)
    return SERVICE_TOKEN


def _auth(token: str = SERVICE_TOKEN) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_volume_key_happy_path(client, service_token):
    dek = uuid.uuid4().bytes  # 16 bytes == CUBE_DEK_LENGTH
    dek_hex = dek.hex()

    reg = await client.post("/api/v1/kms/volume-keys", json={
        "volume_id": "vol-1", "name": "vol-1", "dek_hex": dek_hex,
    }, headers=_auth())
    assert reg.status_code == 201, reg.text
    body = reg.json()
    assert set(body.keys()) == {GO_REGISTER_RESP_KEY}
    key_id = body[GO_REGISTER_RESP_KEY]
    assert key_id
    assert dek_hex not in reg.text  # raw DEK is never returned at registration

    dist = await client.post(
        f"/api/v1/kms/volume-keys/{key_id}/distribute",
        json={GO_DISTRIBUTE_REQ_KEY: "sess-1"},
        headers=_auth(),
    )
    assert dist.status_code == 200, dist.text
    dbody = dist.json()
    assert set(dbody.keys()) == {GO_DISTRIBUTE_RESP_KEY}
    assert bytes.fromhex(dbody[GO_DISTRIBUTE_RESP_KEY]) == dek

    rev = await client.delete(f"/api/v1/kms/volume-keys/{key_id}", headers=_auth())
    assert rev.status_code == 204, rev.text
    # Idempotent: an already-revoked ref and an unknown ref are 204, not 404.
    assert (await client.delete(f"/api/v1/kms/volume-keys/{key_id}", headers=_auth())).status_code == 204
    assert (await client.delete(f"/api/v1/kms/volume-keys/{uuid.uuid4().hex}", headers=_auth())).status_code == 204


@pytest.mark.asyncio
async def test_distribute_after_revoke_is_rejected(client, service_token):
    dek = uuid.uuid4().bytes
    reg = await client.post("/api/v1/kms/volume-keys", json={
        "volume_id": "vol-2", "name": "vol-2", "dek_hex": dek.hex(),
    }, headers=_auth())
    assert reg.status_code == 201, reg.text
    key_id = reg.json()[GO_REGISTER_RESP_KEY]
    rev = await client.delete(f"/api/v1/kms/volume-keys/{key_id}", headers=_auth())
    assert rev.status_code == 204
    dist = await client.post(
        f"/api/v1/kms/volume-keys/{key_id}/distribute",
        json={GO_DISTRIBUTE_REQ_KEY: "sess-x"},
        headers=_auth(),
    )
    assert dist.status_code == 404


@pytest.mark.asyncio
async def test_volume_key_auth_wrong_or_missing_token(client, service_token):
    payload = {"volume_id": "v", "name": "v", "dek_hex": "00" * 16}
    r1 = await client.post("/api/v1/kms/volume-keys", json=payload)  # no credential
    assert r1.status_code in (401, 403), r1.text
    r2 = await client.post("/api/v1/kms/volume-keys", json=payload, headers=_auth("wrong-token"))
    assert r2.status_code in (401, 403), r2.text


@pytest.mark.asyncio
async def test_volume_key_token_not_configured_is_fail_closed(client, monkeypatch):
    from app.core.config import get_settings
    monkeypatch.setattr(get_settings(), "KMS_SERVICE_TOKEN", None)
    r = await client.post("/api/v1/kms/volume-keys", json={
        "volume_id": "v", "name": "v", "dek_hex": "00" * 16,
    }, headers=_auth())
    assert r.status_code == 401, r.text


@pytest.mark.asyncio
async def test_register_rejects_wrong_dek_length(client, service_token):
    r = await client.post("/api/v1/kms/volume-keys", json={
        "volume_id": "v", "name": "v", "dek_hex": "00" * 8,  # 8 bytes != CUBE_DEK_LENGTH
    }, headers=_auth())
    assert r.status_code == 400, r.text


# Exact Cipher.Name() strings from cds-volume-plugin/cipher.go — the plugin owns
# the cipher choice, the control plane only records what it was told.
PLUGIN_SM4_NAME = "sm4-gcm(tongsuo)"
PLUGIN_AES_FALLBACK_NAME = "aes-128-gcm(fallback,non-GM)"


async def _registered_algorithm(client, db_session, volume_id: str, cipher: str | None) -> str:
    payload = {"volume_id": volume_id, "name": volume_id, "dek_hex": uuid.uuid4().bytes.hex()}
    if cipher is not None:
        payload["cipher"] = cipher
    reg = await client.post("/api/v1/kms/volume-keys", json=payload, headers=_auth())
    assert reg.status_code == 201, reg.text
    row = (await db_session.execute(
        select(VolumeKey).where(VolumeKey.key_id == reg.json()["key_id"])
    )).scalar_one()
    return row.algorithm


@pytest.mark.asyncio
async def test_register_records_the_cipher_the_plugin_declared(client, service_token, db_session):
    """CR-2026-10-15: the compliance record must state the cipher that actually
    sealed the volume — the plugin default backend is the AES fallback."""
    assert await _registered_algorithm(client, db_session, "vol-sm4", PLUGIN_SM4_NAME) == "SM4-GCM"
    assert await _registered_algorithm(
        client, db_session, "vol-aes", PLUGIN_AES_FALLBACK_NAME) == "AES-128-GCM"


@pytest.mark.asyncio
async def test_register_without_declared_cipher_is_honest_unknown(client, service_token, db_session):
    """An old plugin that declares nothing is recorded as `unknown`, never as a
    SM4-GCM claim (禁伪造: no unverified GM-crypto claim in the audit record)."""
    assert await _registered_algorithm(client, db_session, "vol-silent", None) == "unknown"
