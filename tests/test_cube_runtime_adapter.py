"""CB-01: Cube (L2') runtime adapter tests.

Proto field-level assertions on the assembled RunCubeSandboxRequest, the RUNTIME
UNAVAILABLE create gate (disabled + no-eligible-node, via the API), node
selection by runtime_version, and the day-1 envd streaming spike (unreachable →
STREAM_UNSUPPORTED, shape identical to session_stream.py's).
"""
from __future__ import annotations

import json
import uuid

import pytest

from app._stubs.cube.cubebox.v1 import cubebox_pb2
from app._stubs.cube.errorcode.v1 import errorcode_pb2
from app.core.config import get_settings
from app.services.cube_runtime_adapter import (
    CubeRuntimeAdapter,
    build_run_request,
    pinned_cube_version,
    select_cube_node,
)


class _CapturingClient:
    def __init__(self) -> None:
        self.captured = None

    def create(self, request):
        self.captured = request
        resp = cubebox_pb2.RunCubeSandboxResponse()
        resp.requestID = request.requestID
        # SS-14 RT-07: 真实 cubelet sandbox_id = 32 位 hex（cubebox ls 实证），
        # container_id 右切解析按此契约。
        resp.sandboxID = "a" * 32
        resp.ret.ret_code = errorcode_pb2.Success
        return resp


def test_build_run_request_proto_fields():
    envs = {"CDS_DEK_HEX": "ab" * 16, "CDS_SANDBOX_LEVEL": "l2c", "CDS_SESSION_ID": "sess-1"}
    annotations = {"cds.cube.pause-snapshot-id": "", "cube.cds.plugin-volume-sources": "[]"}
    req = build_run_request(
        session_id="sess-1",
        image="cds/cube-sandbox:latest",
        envs=envs,
        annotations=annotations,
        network_config=None,
        results_dir="/var/lib/cds/sandbox-results",
    )
    assert req.requestID

    by_name = {v.name: v for v in req.volumes}
    # SS-14 RT-07 (D-e2e-17): +cds-rootfs 可写 rootfs 卷（empty_dir "/" 10Gi）
    assert set(by_name) == {"cds-data-sess-1", "cds-results-sess-1", "cds-scratch-sess-1",
                            "cds-rootfs-sess-1"}
    # data volume = plugin (cds-encrypted); the other oneofs are empty
    assert by_name["cds-data-sess-1"].volume_source.plugin_volume.driver == "cds-encrypted"
    assert by_name["cds-data-sess-1"].volume_source.empty_dir.SizeLimit == ""
    # results volume = host_dir
    hd = by_name["cds-results-sess-1"].volume_source.host_dir_volumes.volume_sources[0]
    assert hd.host_path == "/var/lib/cds/sandbox-results/sess-1"
    assert hd.name == "cds-results-sess-1"
    # scratch volume = empty_dir
    ed = by_name["cds-scratch-sess-1"].volume_source.empty_dir
    assert ed.SizeLimit == "1Gi"

    c = req.containers[0]
    assert c.image.image == "cds/cube-sandbox:latest"
    # SS-14 RT-07 (D-e2e-15/17): 默认 storage_media 空 = OCI 镜像路径（上游
    # cube_image_pull_route.go 实证：ext4 分支要求 SafeID imageRef，registry
    # 引用必败）；ext4 为 TC 模板分发专用，由 CUBE_SANDBOX_STORAGE_MEDIA 配置。
    assert c.image.storage_media == ""
    env_keys = {e.key: e.value for e in c.envs}
    assert env_keys["CDS_DEK_HEX"] == "ab" * 16
    assert env_keys["CDS_SANDBOX_LEVEL"] == "l2c"
    mounts = {(m.name, m.container_path, m.readonly) for m in c.volume_mounts}
    # SS-14 RT-07 (D-e2e-17): +rootfs 可写卷挂载 /
    assert mounts == {
        ("cds-data-sess-1", "/data", False),
        ("cds-results-sess-1", "/results", False),
        ("cds-scratch-sess-1", "/scratch", False),
        ("cds-rootfs-sess-1", "/", False),
    }
    assert req.annotations["cds.cube.pause-snapshot-id"] == ""


def test_provision_assembles_request_and_container_id(monkeypatch, tmp_path):
    monkeypatch.setattr(get_settings(), "CUBE_DATAPLANE_ENABLED", True)
    # SS-14 RT-07 (D-e2e-17): provision 预建会话 results 目录（cubelet
    # bind-mount 契约）—— 测试指向可写临时目录。
    monkeypatch.setattr(get_settings(), "CUBE_RESULTS_DIR", str(tmp_path))
    capturing = _CapturingClient()
    adapter = CubeRuntimeAdapter(client_factory=lambda node: capturing)
    # SS-14 RT-07 (D-e2e-17): 卷预建契约——未提供 volume_dek_ref = 未预建 =
    # 诚实拒绝（fail-closed），provision 不再自行建卷。
    result = adapter.provision(
        "sess-42", "", 3600,
        cube_node={"node_id": "n1", "endpoint": "10.0.0.1:9443", "runtime_version": "v1"},
        volume_dek_ref="vault:v1:precreated-dek",
    )
    assert result["status"] == "running"
    assert result["container_id"] == "l2c-n1-" + "a" * 32
    assert result["node_id"] == "n1"

    req = capturing.captured
    assert req.requestID
    env_keys = {e.key: e.value for e in req.containers[0].envs}
    assert env_keys["CDS_SANDBOX_LEVEL"] == "l2c"
    assert env_keys["CDS_SESSION_ID"] == "sess-42"
    # CDS_DEK_HEX is 2*CUBE_DEK_LENGTH hex chars (default 16 -> 32)
    assert len(env_keys["CDS_DEK_HEX"]) == 2 * get_settings().CUBE_DEK_LENGTH
    # SS-14 RT-07 (D-e2e-17/19): 键名对齐 cubelet 实际读取的 annotation
    # (无 cube.cds. 前缀); private_data 双重编码（上游 string 字段）。
    pvs = json.loads(req.annotations["plugin-volume-sources"])
    assert pvs[0]["name"] == "cds-data-sess-42"
    assert pvs[0]["driver"] == "cds-encrypted"
    pd = json.loads(pvs[0]["private_data"])
    assert pd["session_dek_ref"] == "vault:v1:precreated-dek"
    assert req.annotations["cds.cube.pause-snapshot-id"] == ""


def test_provision_rejects_when_dataplane_disabled():
    adapter = CubeRuntimeAdapter(client_factory=lambda node: _CapturingClient())
    result = adapter.provision("sess-x", "", 3600, cube_node={"node_id": "n1", "endpoint": "e", "runtime_version": "v"})
    assert result["status"] == "failed"
    assert result["container_id"] is None


@pytest.mark.asyncio
async def test_node_selection_excludes_runtime_version_mismatch(db_session):
    from app.models.sandbox_node import NodeStatus, SandboxNode

    pin = pinned_cube_version()
    assert pin, "pinned cube version must be resolvable from CB-00 pin"
    db_session.add(SandboxNode(
        node_id="match", hostname="h1", ip_address="10.0.0.1",
        status=NodeStatus.ONLINE.value,
        capabilities={"cubelet_endpoint": "10.0.0.1:9443", "runtime_version": pin},
    ))
    db_session.add(SandboxNode(
        node_id="mismatch", hostname="h2", ip_address="10.0.0.2",
        status=NodeStatus.ONLINE.value,
        capabilities={"cubelet_endpoint": "10.0.0.2:9443", "runtime_version": "9999999999999999999999"},
    ))
    db_session.add(SandboxNode(
        node_id="no-endpoint", hostname="h3", ip_address="10.0.0.3",
        status=NodeStatus.ONLINE.value, capabilities={"runtime_version": pin},
    ))
    db_session.add(SandboxNode(
        node_id="cordoned", hostname="h4", ip_address="10.0.0.4",
        status=NodeStatus.ONLINE.value, scheduling_disabled=True,
        capabilities={"cubelet_endpoint": "10.0.0.4:9443", "runtime_version": pin},
    ))
    await db_session.flush()

    chosen = await select_cube_node(db_session, runtime_version=pin)
    assert chosen is not None
    assert chosen["node_id"] == "match"
    # with an impossible version pin nothing is eligible
    assert await select_cube_node(db_session, runtime_version="impossible") is None


async def _post_l2c_create(client, auth_headers, publish_product):
    product_resp = await client.post("/api/v1/data-products", json={"name": f"cube-gate-{uuid.uuid4().hex[:6]}"}, headers=auth_headers)
    assert product_resp.status_code == 201, product_resp.text
    product_id = product_resp.json()["id"]
    await publish_product(product_id)
    resp = await client.post(
        "/api/v1/sandbox-sessions",
        json={"data_product_id": product_id, "sandbox_level": "l2c"},
        headers=auth_headers,
    )
    return resp


@pytest.mark.asyncio
async def test_gate_disabled_returns_400_runtime_unavailable(client, auth_headers, publish_product, monkeypatch):
    monkeypatch.setattr(get_settings(), "CUBE_DATAPLANE_ENABLED", False)
    resp = await _post_l2c_create(client, auth_headers, publish_product)
    assert resp.status_code == 400, resp.text
    assert resp.json()["code"] == "RUNTIME_UNAVAILABLE"
    assert resp.json()["detail"]["reason"] == "dataplane_disabled"


@pytest.mark.asyncio
async def test_gate_no_eligible_node_returns_400(client, auth_headers, publish_product, monkeypatch):
    monkeypatch.setattr(get_settings(), "CUBE_DATAPLANE_ENABLED", True)
    # no cube nodes in the db -> no eligible node
    resp = await _post_l2c_create(client, auth_headers, publish_product)
    assert resp.status_code == 400, resp.text
    assert resp.json()["code"] == "RUNTIME_UNAVAILABLE"
    assert resp.json()["detail"]["reason"] == "no_eligible_node"


@pytest.mark.asyncio
async def test_streaming_unreachable_returns_stream_unsupported():
    adapter = CubeRuntimeAdapter(client_factory=lambda node: _CapturingClient())
    adapter._node_cache["n1"] = {"node_id": "n1", "endpoint": "127.0.0.1:1", "runtime_version": "v"}
    result = await adapter.execute_streaming("l2c-n1-" + "b" * 32, "print(1)", "python")
    # shape identical to session_stream.py's STREAM_UNSUPPORTED frame for non-L0 levels
    assert result == {
        "type": "error",
        "code": "STREAM_UNSUPPORTED",
        "message": "streaming exec not supported on l2c",
    }
