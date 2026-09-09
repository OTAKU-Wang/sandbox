"""N11: honest HE/MPC disclosure — capabilities endpoint, response attachments,
and the monitoring posture mpc entry. Verifies the service never implies
computation that is not deployed.
"""
import uuid

import pytest


@pytest.mark.asyncio
async def test_mpc_capabilities_endpoint_honest(client, db_session, make_user):
    headers, _uid = await make_user("operator", "op")
    resp = await client.get("/api/v1/mpc/capabilities", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "custody"
    assert body["compute"] == "evaluating"
    assert body["backend"] is None
    assert "Shamir" in body["label"]
    assert "SecretFlow" in body["backend_label"]


@pytest.mark.asyncio
async def test_verify_response_carries_disclosure(client, db_session, make_user):
    from app.services.mpc_service import mpc_service

    headers, uid = await make_user("operator", "op")
    key = await mpc_service.split_key(
        db_session, secret=b"\x11" * 32, threshold=2, total_shares=3,
    )
    await db_session.flush()
    share_ids = [s.share_id for s in key.shares[:2]]

    resp = await client.post(
        "/api/v1/mpc/verify",
        params={"key_id": key.key_id},
        json=share_ids,
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["valid"] is True
    cap = body["mpc_capabilities"]
    assert cap["mode"] == "custody"
    assert cap["compute"] == "evaluating"


@pytest.mark.asyncio
async def test_reconstruct_response_carries_disclosure(client, db_session, make_user):
    from app.services.mpc_service import mpc_service

    headers, uid = await make_user("operator", "op")
    secret = b"\x22" * 32
    key = await mpc_service.split_key(
        db_session, secret=secret, threshold=2, total_shares=3,
    )
    await db_session.flush()
    share_ids = [s.share_id for s in key.shares[:2]]

    resp = await client.post(
        "/api/v1/mpc/reconstruct",
        json={"key_id": key.key_id, "share_ids": share_ids},
        headers=headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["secret_hex"] == secret.hex()
    assert body["mpc_capabilities"]["compute"] == "evaluating"


@pytest.mark.asyncio
async def test_security_posture_has_mpc_custody_entry(client, db_session, make_user):
    headers, _uid = await make_user("operator", "op")
    resp = await client.get("/api/v1/monitoring/security-posture", headers=headers)
    assert resp.status_code == 200
    caps = {c["id"]: c for c in resp.json()["capabilities"]}
    assert "mpc" in caps
    assert caps["mpc"]["status"] == "custody"
    assert "评估" in caps["mpc"]["summary"]
    assert "compute=evaluating" in caps["mpc"]["evidence"]
    assert caps["mpc"]["release_gate"] == "pass"
