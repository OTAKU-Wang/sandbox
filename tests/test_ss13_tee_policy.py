"""SS-13: TEE policy + trust_root disclosure.

Covers:
(a) default tee_policy is "tee_if_available";
(b) tee_required with a TEE-capable seeded node -> trust_root in {tdx, sev_snp};
(c) tee_required with no TEE node -> 400 TEE_NODE_UNAVAILABLE (while
    tee_if_available stays honest with trust_root "none");
(d) disclosure: session detail, capability matrix and compliance report all
    expose sandbox_engine "cube_microvm" + a trust_root value.
"""
import uuid

import pytest
from sqlalchemy import delete, select

from app.models.sandbox_node import NodeStatus, SandboxNode
from app.services.cube_runtime_adapter import pinned_cube_version

# Kept in sync with tests/conftest.py (SS13_TEST_CUBE_NODE_ID). Hyphen-free:
# _parse_container_id splits the l2c container id on the first "-".
SS13_TEST_CUBE_NODE_ID = "cdstestcubenode"


async def _create_product(client, auth_headers) -> str:
    resp = await client.post(
        "/api/v1/data-products",
        json={"name": f"SS13-TEE-{uuid.uuid4().hex[:6]}"},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


@pytest.mark.asyncio
async def test_default_tee_policy(client, auth_headers, publish_product, l2c_api_env):
    """(a) tee_policy defaults to tee_if_available."""
    product_id = await _create_product(client, auth_headers)
    await publish_product(product_id)
    resp = await client.post(
        "/api/v1/sandbox-sessions",
        json={"data_product_id": product_id},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["tee_policy"] == "tee_if_available"


@pytest.mark.asyncio
async def test_tee_required_with_tee_node_discloses_trust_root(client, auth_headers, publish_product, l2c_api_env):
    """(b) tee_required + the conftest TEE node (tdx) -> trust_root disclosed."""
    product_id = await _create_product(client, auth_headers)
    await publish_product(product_id)
    resp = await client.post(
        "/api/v1/sandbox-sessions",
        json={"data_product_id": product_id, "tee_policy": "tee_required"},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["tee_policy"] == "tee_required"
    assert data["trust_root"] in {"tdx", "sev_snp"}


@pytest.mark.asyncio
async def test_tee_required_without_tee_node_400(client, auth_headers, publish_product, db_session, l2c_api_env):
    """(c) no TEE-capable node -> 400 TEE_NODE_UNAVAILABLE (fail-closed)."""
    from datetime import datetime, timezone

    # Replace the suite-wide TEE node with a non-TEE cube node (no "tee" key:
    # safe default "none" per the adapter's capability reader).
    await db_session.execute(delete(SandboxNode))
    db_session.add(SandboxNode(
        node_id=SS13_TEST_CUBE_NODE_ID,
        hostname="non-tee.local",
        ip_address="10.99.0.2",
        status=NodeStatus.ONLINE.value,
        capabilities={
            "cubelet_endpoint": "10.99.0.2:9443",
            "runtime_version": pinned_cube_version(),
        },
        region="default",
        cpu_cores=8,
        memory_mb=16384,
        last_heartbeat=datetime.now(timezone.utc),
    ))
    await db_session.commit()

    product_id = await _create_product(client, auth_headers)
    await publish_product(product_id)

    denied = await client.post(
        "/api/v1/sandbox-sessions",
        json={"data_product_id": product_id, "tee_policy": "tee_required"},
        headers=auth_headers,
    )
    assert denied.status_code == 400, denied.text
    body = denied.json()
    assert body["code"] == "TEE_NODE_UNAVAILABLE"
    assert body["detail"]["reason"] == "no_tee_node"

    # tee_if_available still succeeds and honestly discloses trust_root "none".
    ok = await client.post(
        "/api/v1/sandbox-sessions",
        json={"data_product_id": product_id},
        headers=auth_headers,
    )
    assert ok.status_code == 201, ok.text
    assert ok.json()["trust_root"] == "none"


@pytest.mark.asyncio
async def test_tee_policy_invalid_value_rejected(client, auth_headers, publish_product):
    resp = await client.post(
        "/api/v1/data-products",
        json={"name": f"SS13-TEE-BAD-{uuid.uuid4().hex[:6]}"},
        headers=auth_headers,
    )
    product_id = resp.json()["id"]
    await publish_product(product_id)
    bad = await client.post(
        "/api/v1/sandbox-sessions",
        json={"data_product_id": product_id, "tee_policy": "tee_always"},
        headers=auth_headers,
    )
    assert bad.status_code == 422


@pytest.mark.asyncio
async def test_disclosure_surfaces(client, auth_headers, operator_headers, publish_product, l2c_api_env):
    """(d) session detail, capability matrix and compliance report disclose."""
    product_id = await _create_product(client, auth_headers)
    await publish_product(product_id)
    created = await client.post(
        "/api/v1/sandbox-sessions",
        json={"data_product_id": product_id, "tee_policy": "tee_required"},
        headers=auth_headers,
    )
    assert created.status_code == 201, created.text
    session_id = created.json()["id"]

    detail = await client.get(f"/api/v1/sandbox-sessions/{session_id}", headers=auth_headers)
    assert detail.status_code == 200
    d = detail.json()
    assert d["tee_policy"] == "tee_required"
    assert d["trust_root"] in {"tdx", "sev_snp"}
    assert d["sandbox_engine"] == "cube_microvm"

    matrix = await client.get("/api/v1/meta/capability-matrix", headers=auth_headers)
    assert matrix.status_code == 200
    m = matrix.json()
    assert m["sandbox_engine"] == "cube_microvm"
    assert "trust_root" in m and m["trust_root"]

    report = await client.get("/api/v1/compliance/reports/quick", headers=operator_headers)
    assert report.status_code == 200, report.text
    # PZ-06 disclosure fields are flat on the report payload
    assert report.json()["sandbox_engine"] == "cube_microvm"
    assert report.json()["trust_root"]
