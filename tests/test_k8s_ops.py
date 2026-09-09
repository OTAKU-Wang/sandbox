"""N8: K8s runtime ops API tests (read-only, ADMIN/OPERATOR).

Without a live cluster the endpoints degrade honestly to DB-backed state with
``cluster_available: false`` — this suite pins that contract plus role gating.
"""
import uuid

import pytest


@pytest.mark.asyncio
async def test_deployments_degrade_honestly_without_cluster(client, db_session, make_user):
    headers, _uid = await make_user("operator", "op")
    resp = await client.get("/api/v1/ops/deployments", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["cluster_available"] is False
    assert body["items"] == []
    assert body["count"] == 0
    assert body["namespace"]


@pytest.mark.asyncio
async def test_ops_requires_operator_role(client, db_session, make_user):
    headers, _uid = await make_user("buyer", "buy")
    for path in ("/api/v1/ops/deployments", "/api/v1/ops/network-policies", "/api/v1/ops/pvcs"):
        resp = await client.get(path, headers=headers)
        assert resp.status_code == 403, path


@pytest.mark.asyncio
async def test_network_policies_list_from_db(client, db_session, make_user):
    from app.models.network_policy import NetworkPolicy

    headers, _uid = await make_user("operator", "op")
    policy = NetworkPolicy(
        session_id=f"sess-{uuid.uuid4().hex[:8]}",
        user_id=str(uuid.uuid4()),
        mode="allowlist",
        allowed_ips=["10.0.0.0/8"],
        allowed_domains=["api.example.com"],
        allowed_ports=[443, 80],
        dns_proxy_enabled=True,
    )
    db_session.add(policy)
    await db_session.flush()

    resp = await client.get("/api/v1/ops/network-policies", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 1
    item = next(i for i in body["items"] if i["session_id"] == policy.session_id)
    assert item["mode"] == "allowlist"
    assert item["allowed_ips"] == ["10.0.0.0/8"]
    assert item["allowed_domains"] == ["api.example.com"]
    assert item["live_pod"] is False
    assert body["cluster_available"] is False


@pytest.mark.asyncio
async def test_pvcs_list_from_db(client, db_session, make_user):
    from app.models.shared_volume import SharedVolume, SharedVolumeAttachment

    headers, owner = await make_user("operator", "op")
    volume = SharedVolume(
        owner_id=uuid.UUID(owner), name="vol-ops", size_limit_mb=512, read_only=True,
    )
    db_session.add(volume)
    await db_session.flush()
    sid = uuid.uuid4()
    db_session.add(SharedVolumeAttachment(session_id=sid, volume_id=volume.id, read_only=True))
    await db_session.flush()

    resp = await client.get("/api/v1/ops/pvcs", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    item = next(i for i in body["items"] if i["name"] == "vol-ops")
    assert item["claim_name"] == f"cds-shared-{volume.id}"
    assert item["size_limit_mb"] == 512
    assert item["read_only"] is True
    assert item["pvc_status"] == "unavailable"
    assert item["attachments"][0]["session_id"] == str(sid)
    assert body["cluster_available"] is False


@pytest.mark.asyncio
async def test_logs_fall_back_to_audit_trail(client, db_session, make_user):
    from app.models.sandbox_session import SandboxSession, SessionStatus
    from app.models.audit_log import AuditLog

    headers, uid = await make_user("operator", "op")
    session = SandboxSession(
        user_id=uuid.UUID(uid), data_product_id=uuid.uuid4(), sandbox_level="L3",
        status=SessionStatus.READY.value, container_id=f"bwrap-{uuid.uuid4().hex}",
        timeout_seconds=300,
    )
    db_session.add(session)
    await db_session.flush()
    db_session.add(AuditLog(
        session_id=session.id, user_id=uuid.UUID(uid), action="sandbox.exec",
        resource_type="sandbox_session",
        detail='{"code": "print(1)"}',
    ))
    await db_session.flush()

    resp = await client.get(f"/api/v1/ops/logs/{session.id}", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "audit"
    assert body["cluster_available"] is False
    assert body["count"] >= 1
    assert any("sandbox.exec" in ln["line"] for ln in body["logs"])


@pytest.mark.asyncio
async def test_logs_404_for_missing_session(client, db_session, make_user):
    headers, _uid = await make_user("operator", "op")
    resp = await client.get(f"/api/v1/ops/logs/{uuid.uuid4()}", headers=headers)
    assert resp.status_code == 404
