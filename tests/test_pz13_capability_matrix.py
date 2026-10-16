"""PZ-13 (SS-11): capability-matrix authority source tests.

Locks the baseline (spec ss-11-cds-productization.md §2.1, 2026-10-12
code-verified): endpoint shape/auth, matrix invariants, and the additive
/health capability section.
"""
import pytest
from httpx import AsyncClient

MATRIX_URL = "/api/v1/meta/capability-matrix"

LEVELS = ("L0", "L1", "L2", "l2c", "L3", "k8s")
CAPABILITIES = (
    "sync_exec",
    "stream_ws",
    "files_snapshot",
    "pause_resume",
    "tee_proof_gate",
    "network_policy_hotswap",
)
STATES = {"real", "simulated", "unsupported", "planned"}
# SS-13 RT-04: the matrix surface exposes the platform disclosure fields
# (compliance_report.disclosure_fields) at top level alongside the matrix.
DISCLOSURE_KEYS = {"signing_backend", "aead_algorithm", "trust_root", "chain_anchoring", "sandbox_engine"}


@pytest.mark.asyncio
async def test_capability_matrix_endpoint_shape(client: AsyncClient, auth_headers: dict):
    resp = await client.get(MATRIX_URL, headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert set(data.keys()) == {"matrix", "levels"} | DISCLOSURE_KEYS

    # full baseline: every level x capability cell present and well-formed
    assert tuple(data["matrix"].keys()) == LEVELS
    for level, caps in data["matrix"].items():
        assert tuple(caps.keys()) == CAPABILITIES, f"{level} incomplete"
        for cap, cell in caps.items():
            assert cell["state"] in STATES, f"{level}/{cap}: bad state"
            assert cell["note"], f"{level}/{cap}: missing note"

    # level metadata: single source of labels/positioning/deprecation
    assert tuple(data["levels"].keys()) == LEVELS
    for level, meta in data["levels"].items():
        assert meta["label"]
        assert meta["positioning"]
        assert isinstance(meta["deprecated"], bool)


@pytest.mark.asyncio
async def test_capability_matrix_requires_auth(client: AsyncClient):
    resp = await client.get(MATRIX_URL)
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_matrix_invariants_l2c_written_back(client: AsyncClient, auth_headers: dict):
    # D-e2e-10: SS-13 M1+M2 landed 2026-10-13 — the l2c column was written
    # back from all-planned to the code-verified shape below (keep exact:
    # the matrix is the single authority source, no drift allowed).
    data = (await client.get(MATRIX_URL, headers=auth_headers)).json()
    l2c = data["matrix"]["l2c"]
    assert {cap: cell["state"] for cap, cell in l2c.items()} == {
        "sync_exec": "real",
        "stream_ws": "unsupported",
        "files_snapshot": "real",
        "pause_resume": "real",
        "tee_proof_gate": "planned",
        "network_policy_hotswap": "planned",
    }
    assert data["levels"]["l2c"]["positioning"] == "生产默认(软件就绪，需节点)"
    assert data["levels"]["l2c"]["deprecated"] is False


@pytest.mark.asyncio
async def test_matrix_invariants_stream_files(client: AsyncClient, auth_headers: dict):
    data = (await client.get(MATRIX_URL, headers=auth_headers)).json()
    m = data["matrix"]
    # streaming: L0/k8s only (session_stream.py:162-165)
    assert m["L0"]["stream_ws"]["state"] == "real"
    assert m["k8s"]["stream_ws"]["state"] == "real"
    assert m["L3"]["stream_ws"]["state"] == "unsupported"
    assert m["L1"]["stream_ws"]["state"] == "unsupported"
    assert m["L2"]["stream_ws"]["state"] == "unsupported"
    # files/snapshots: L1/L2 400, k8s ROX
    assert m["L1"]["files_snapshot"]["state"] == "unsupported"
    assert m["L2"]["files_snapshot"]["state"] == "unsupported"
    assert m["k8s"]["files_snapshot"]["state"] == "unsupported"
    assert m["L0"]["files_snapshot"]["state"] == "real"
    assert m["L3"]["files_snapshot"]["state"] == "real"
    # TEE proof gate: L1/L2 required, n/a elsewhere
    assert m["L1"]["tee_proof_gate"]["state"] == "real"
    assert m["L2"]["tee_proof_gate"]["state"] == "real"
    for level in ("L0", "L3", "k8s"):
        assert m[level]["tee_proof_gate"]["state"] == "unsupported"


@pytest.mark.asyncio
async def test_matrix_level_metadata_deprecation(client: AsyncClient, auth_headers: dict):
    data = (await client.get(MATRIX_URL, headers=auth_headers)).json()
    levels = data["levels"]
    assert levels["L0"]["positioning"] == "开发级"
    assert levels["L1"]["positioning"] == "软件机密级"
    assert levels["L2"]["positioning"] == "软件增强(弃用观察)"
    assert levels["L2"]["deprecated"] is True
    assert levels["L3"]["positioning"] == "进程隔离"
    assert levels["k8s"]["positioning"] == "Pod隔离(弃用观察)"
    assert levels["k8s"]["deprecated"] is True


@pytest.mark.asyncio
async def test_health_additive_capability_section(client: AsyncClient):
    resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    # pre-existing shape untouched
    assert data["status"] == "ok"
    assert data["service"] == "cds"
    assert data["capability"]["levels"] == list(LEVELS)
    assert data["capability"]["l2c_planned"] is False  # D-e2e-10: SS-13 M1+M2 shipped


@pytest.mark.asyncio
async def test_capability_matrix_service_module_shape():
    from app.services import capability_matrix as cm

    payload = cm.get_capability_matrix()
    assert set(payload.keys()) == {"matrix", "levels"}
    assert tuple(payload["matrix"]) == cm.LEVEL_ORDER
    assert tuple(cm.CAPABILITIES) == CAPABILITIES
    health = cm.health_capability_section()
    assert health["l2c_planned"] is False  # D-e2e-10: SS-13 M1+M2 shipped
    assert health["baseline"] == cm.MATRIX_BASELINE_DATE
