"""SS-13 Phase 2: MicroVM single path + TEE-optional — end-to-end over the API.

Proves the cutover across the real API surface using the conftest fixtures
(client + autouse-seeded cube node + TestCubeNodeClient stub):

 1. default create (no sandbox_level, no tee_policy) → l2c + tee_if_available
    + honest trust_root "none" on a non-TEE node + "l2c-" container id;
 2. session detail discloses trust_root + tee_policy;
 3. capability matrix (GET /api/v1/meta/capability-matrix) discloses
    sandbox_engine "cube_microvm";
 4. monitoring security posture (GET /api/v1/monitoring/security-posture,
    embeds disclosure_fields()) discloses sandbox_engine "cube_microvm";
 5. TEE-capable node (capabilities.tee="tdx") → created session's
    trust_root == "tdx";
 6. tee_policy=tee_required with no TEE node → 400 TEE_NODE_UNAVAILABLE.

The conftest seed makes the node TEE-capable ("tdx") by default; the
non-TEE scenarios below swap the node row in-test (same delete+re-add
pattern as tests/test_ss13_tee_policy.py) — no shared files touched.
"""
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import delete, select

from app.models.sandbox_node import NodeStatus, SandboxNode
from app.services.cube_runtime_adapter import pinned_cube_version

# Kept in sync with tests/conftest.py (SS13_TEST_CUBE_NODE_ID). Hyphen-free:
# _parse_container_id splits the l2c container id on the first "-".
SS13_TEST_CUBE_NODE_ID = "cdstestcubenode"


async def _set_node_tee(db_session, tee: str | None) -> None:
    """Replace the suite-wide node with one whose capabilities.tee is `tee`
    (None → capability absent; the adapter's safe default is "none")."""
    await db_session.execute(delete(SandboxNode))
    caps = {
        "cubelet_endpoint": "10.99.0.1:9443",
        "runtime_version": pinned_cube_version(),
    }
    if tee is not None:
        caps["tee"] = tee
    db_session.add(SandboxNode(
        node_id=SS13_TEST_CUBE_NODE_ID,
        hostname=f"{SS13_TEST_CUBE_NODE_ID}.local",
        ip_address="10.99.0.1",
        status=NodeStatus.ONLINE.value,
        capabilities=caps,
        region="default",
        cpu_cores=16,
        memory_mb=32768,
        gpu_count=0,
        cpu_usage=0.05,
        memory_usage=0.05,
        gpu_usage=0.0,
        active_tasks=0,
        max_tasks=100,
        error_rate=0.0,
        last_heartbeat=datetime.now(timezone.utc),
    ))
    await db_session.commit()
    # Sanity: exactly one node, with the intended tee capability.
    node = (await db_session.execute(
        select(SandboxNode).where(SandboxNode.node_id == SS13_TEST_CUBE_NODE_ID)
    )).scalar_one()
    assert node.capabilities.get("tee") == tee or (tee is None and "tee" not in node.capabilities)


async def _create_product(client, auth_headers) -> str:
    resp = await client.post(
        "/api/v1/data-products",
        json={"name": f"SS13-E2E-{uuid.uuid4().hex[:6]}"},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


@pytest.mark.asyncio
async def test_single_path_default_create_and_disclosure(
    client, auth_headers, operator_headers, db_session, publish_product, l2c_api_env,
):
    """(1)+(2)+(3)+(4) on a non-TEE node: default level, honest trust_root,
    container id, detail disclosure, capability matrix, security posture."""
    # Non-TEE node: the conftest seed is TEE-capable by default; assertion
    # 1 requires the honest trust_root "none" path.
    await _set_node_tee(db_session, None)

    product_id = await _create_product(client, auth_headers)
    await publish_product(product_id)

    # (1) No sandbox_level, no tee_policy → the single path is chosen.
    created = await client.post(
        "/api/v1/sandbox-sessions",
        json={"data_product_id": product_id},
        headers=auth_headers,
    )
    assert created.status_code == 201, created.text
    data = created.json()
    assert data["sandbox_level"] == "l2c"
    assert data["tee_policy"] == "tee_if_available"
    assert data["trust_root"] == "none"  # node has no TEE — never upgraded
    assert data["container_id"] is not None
    assert data["container_id"].startswith("l2c-")
    assert data["sandbox_engine"] == "cube_microvm"

    # (2) Session detail discloses trust_root + tee_policy.
    detail = await client.get(
        f"/api/v1/sandbox-sessions/{data['id']}", headers=auth_headers
    )
    assert detail.status_code == 200, detail.text
    d = detail.json()
    assert d["trust_root"] == "none"
    assert d["tee_policy"] == "tee_if_available"

    # (3) Capability matrix authority source discloses the engine.
    matrix = await client.get("/api/v1/meta/capability-matrix", headers=auth_headers)
    assert matrix.status_code == 200, matrix.text
    assert matrix.json()["sandbox_engine"] == "cube_microvm"

    # (4) Monitoring security posture embeds disclosure_fields().
    posture = await client.get(
        "/api/v1/monitoring/security-posture", headers=operator_headers
    )
    assert posture.status_code == 200, posture.text
    assert posture.json()["sandbox_engine"] == "cube_microvm"


@pytest.mark.asyncio
async def test_tee_capable_node_discloses_trust_root_tdx(
    client, auth_headers, db_session, publish_product, l2c_api_env,
):
    """(5) node capabilities.tee="tdx" → created session's trust_root "tdx"."""
    await _set_node_tee(db_session, "tdx")

    product_id = await _create_product(client, auth_headers)
    await publish_product(product_id)

    created = await client.post(
        "/api/v1/sandbox-sessions",
        json={"data_product_id": product_id},
        headers=auth_headers,
    )
    assert created.status_code == 201, created.text
    assert created.json()["trust_root"] == "tdx"


@pytest.mark.asyncio
async def test_tee_required_without_tee_node_400(
    client, auth_headers, db_session, publish_product, l2c_api_env,
):
    """(6) tee_required while no node is TEE-capable → 400 TEE_NODE_UNAVAILABLE."""
    await _set_node_tee(db_session, None)

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
