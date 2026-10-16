"""CB-01: Cubelet node gRPC client tests.

Exercises the RET-CODE → W2 mapping layer over a local *insecure* gRPC server
(a direct Servicer implementation) — not real TLS. Covers the 4 happy-path RPCs
(Create/Destroy/Exec/List) + full ret_code enumeration + requestID idempotency
+ deadline tiers + the node health probe.
"""
from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor

import grpc
import pytest

from app._stubs.cube.cubebox.v1 import cubebox_pb2, cubebox_pb2_grpc
from app._stubs.cube.errorcode.v1 import errorcode_pb2
from app.core.errors import CDSError
from app.services import cube_node_client
from app.services.cube_node_client import CubeNodeClient, map_ret_code, _map_grpc_status


def _ret(code) -> errorcode_pb2.Ret:
    r = errorcode_pb2.Ret()
    r.ret_code = code
    r.ret_msg = "ok" if code in (errorcode_pb2.OK, errorcode_pb2.Success) else "upstream error"
    return r


class FakeCubeboxMgr(cubebox_pb2_grpc.CubeboxMgrServicer):
    """In-memory CubeboxMgr: records requests, returns a canned success (or fail_code)."""

    def __init__(self) -> None:
        self.seen: dict[str, object] = {}
        self.fail_code = None

    def _resp(self, name, base, request):
        self.seen[name] = request
        base.ret.ret_code = self.fail_code if self.fail_code is not None else errorcode_pb2.Success
        base.ret.ret_msg = "ok" if self.fail_code in (None, errorcode_pb2.Success) else "boom"
        return base

    def Create(self, request, context):
        return self._resp("Create", cubebox_pb2.RunCubeSandboxResponse(
            requestID=request.requestID, sandboxID="sbx-1", sandboxUUID="u-1"), request)

    def Destroy(self, request, context):
        return self._resp("Destroy", cubebox_pb2.DestroyCubeSandboxResponse(
            requestID=request.requestID, sandboxID=request.sandboxID), request)

    def Exec(self, request, context):
        return self._resp("Exec", cubebox_pb2.ExecCubeSandboxResponse(requestID=request.requestID), request)

    def Update(self, request, context):
        return self._resp("Update", cubebox_pb2.UpdateCubeSandboxResponse(requestID=request.requestID), request)

    def GetStorageMetrics(self, request, context):
        return self._resp("GetStorageMetrics", cubebox_pb2.GetStorageMetricsResponse(requestID=request.requestID), request)

    def List(self, request, context):
        self.seen["List"] = request
        resp = cubebox_pb2.ListCubeSandboxResponse()
        if self.fail_code in (None, errorcode_pb2.Success):
            resp.items.add(id="sbx-1")
        return resp


@pytest.fixture
def fake_server():
    server = grpc.server(ThreadPoolExecutor(max_workers=8))
    servicer = FakeCubeboxMgr()
    cubebox_pb2_grpc.add_CubeboxMgrServicer_to_server(servicer, server)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    yield servicer, f"127.0.0.1:{port}"
    server.stop(grace=0)


@pytest.fixture
def client(fake_server):
    _servicer, target = fake_server
    c = CubeNodeClient(target, insecure=True, deadline_ms=5000)
    yield c
    c.close()


def test_create_destroy_exec_list_happy_path(client, fake_server):
    servicer, _ = fake_server
    created = client.create(cubebox_pb2.RunCubeSandboxRequest())
    assert created.sandboxID == "sbx-1"
    destroyed = client.destroy(cubebox_pb2.DestroyCubeSandboxRequest(sandboxID="sbx-1"))
    assert destroyed.sandboxID == "sbx-1"
    executed = client.exec(cubebox_pb2.ExecCubeSandboxRequest(sandbox_id="sbx-1", args=["python3", "-c", "print(1)"]))
    assert executed.ret.ret_code == errorcode_pb2.Success
    listed = client.list(cubebox_pb2.ListCubeSandboxRequest(id="sbx-1"))
    assert [i.id for i in listed.items] == ["sbx-1"]
    # the four RPCs were dispatched to the servicer
    assert set(servicer.seen) >= {"Create", "Destroy", "Exec", "List"}


def test_all_15_rpc_wrappers_present(client):
    names = ["create", "destroy", "list", "update", "exec", "app_snapshot", "commit_sandbox",
             "rollback_sandbox", "cleanup_template", "list_sandbox_snapshots", "list_local_snapshots",
             "get_local_snapshot", "get_storage_metrics", "inspect_storage_volumes",
             "cleanup_orphan_storage_files"]
    for name in names:
        assert callable(getattr(client, name)), f"missing RPC wrapper: {name}"
    assert len(names) == 15


def test_requestID_present_and_uuid(client, fake_server):
    servicer, _ = fake_server
    client.create(cubebox_pb2.RunCubeSandboxRequest())
    client.destroy(cubebox_pb2.DestroyCubeSandboxRequest(sandboxID="sbx-1"))
    client.exec(cubebox_pb2.ExecCubeSandboxRequest(sandbox_id="sbx-1"))
    client.update(cubebox_pb2.UpdateCubeSandboxRequest(sandboxID="sbx-1"))
    for rpc in ("Create", "Destroy", "Exec", "Update"):
        req = servicer.seen[rpc]
        assert req.requestID, f"{rpc} requestID not set"
        uuid.UUID(req.requestID)  # raises if not a valid UUID


def test_list_has_no_requestid_field(client, fake_server):
    servicer, _ = fake_server
    client.list(cubebox_pb2.ListCubeSandboxRequest())
    assert not hasattr(servicer.seen["List"], "requestID"), "List has no requestID field"


_ALL_CODES = [v for v in errorcode_pb2.DESCRIPTOR.enum_types_by_name["ErrorCode"].values]

_SPEC_BUCKETS = {
    "not_found": (404, "SANDBOX_NOT_FOUND"),
    "conflict": (409, "SESSION_STATE_CONFLICT"),
    "terminal": (410, "SESSION_TERMINATED"),
    "transient": (503, "OPERATION_LOCKED"),
    "timeout": (408, "REQUEST_TIMEOUT"),
}


def test_ret_code_full_enumeration_is_total():
    """Every ErrorCode resolves to exactly one defined W2 bucket (or success)."""
    assert map_ret_code(_ret(errorcode_pb2.OK)) is None
    assert map_ret_code(_ret(errorcode_pb2.Success)) is None
    seen_buckets = set()
    for v in _ALL_CODES:
        err = map_ret_code(_ret(v.number))
        if err is None:
            continue
        bucket = (err.http_status, err.code)
        seen_buckets.add(bucket)
        # total-coverage contract: each non-success code lands in a defined bucket
        assert bucket in set(_SPEC_BUCKETS.values()) | {(500, "INTERNAL_ERROR")}, (
            f"code {v.name}={v.number} mapped to unexpected bucket {bucket}"
        )
    for name, bucket in _SPEC_BUCKETS.items():
        assert bucket in seen_buckets, f"spec bucket {name}={bucket} not exercised by any ret_code"


def test_ret_code_spec_bucket_membership():
    """The exact membership of each spec bucket (the documented mapping)."""
    def bucket_of(code):
        err = map_ret_code(_ret(code))
        return None if err is None else (err.http_status, err.code)

    assert bucket_of(errorcode_pb2.ContainerNotFound) == (404, "SANDBOX_NOT_FOUND")
    assert bucket_of(errorcode_pb2.Conflict) == (409, "SESSION_STATE_CONFLICT")
    assert bucket_of(errorcode_pb2.ContainerStateExitedByUser) == (410, "SESSION_TERMINATED")
    for c in (errorcode_pb2.ConcurrentFailed, errorcode_pb2.ConcurrentLimited):
        assert bucket_of(c) == (503, "OPERATION_LOCKED")
    for c in (errorcode_pb2.DeployCodeTimeout, errorcode_pb2.DeployLayerTimeout,
              errorcode_pb2.DeployCodeLayerTimeout, errorcode_pb2.RunContainerTimeout,
              errorcode_pb2.PullImageTimeOut, errorcode_pb2.LoadUserProcTimeout,
              errorcode_pb2.PreStopTimeOut):
        assert bucket_of(c) == (408, "REQUEST_TIMEOUT")
    # unmapped codes fall through to 500 INTERNAL_ERROR
    assert bucket_of(errorcode_pb2.CreateContainerFailed) == (500, "INTERNAL_ERROR")


def test_transient_bucket_carries_retry_after():
    err = map_ret_code(_ret(errorcode_pb2.ConcurrentFailed))
    assert err.retry_after == 2


def test_ret_code_mapping_via_server(fake_server):
    servicer, target = fake_server
    client = CubeNodeClient(target, insecure=True, deadline_ms=2000)
    cases = {
        errorcode_pb2.Conflict: (409, "SESSION_STATE_CONFLICT"),
        errorcode_pb2.ContainerNotFound: (404, "SANDBOX_NOT_FOUND"),
        errorcode_pb2.ContainerStateExitedByUser: (410, "SESSION_TERMINATED"),
        errorcode_pb2.ConcurrentFailed: (503, "OPERATION_LOCKED"),
        errorcode_pb2.RunContainerTimeout: (408, "REQUEST_TIMEOUT"),
    }
    try:
        for code, (status, w2code) in cases.items():
            servicer.fail_code = code
            with pytest.raises(CDSError) as exc:
                client.create(cubebox_pb2.RunCubeSandboxRequest())
            assert exc.value.http_status == status
            assert exc.value.code == w2code
    finally:
        client.close()


def test_grpc_status_mapping():
    assert _map_grpc_status(grpc.StatusCode.DEADLINE_EXCEEDED) == (408, "REQUEST_TIMEOUT", None)
    assert _map_grpc_status(grpc.StatusCode.UNAVAILABLE) == (503, "OPERATION_LOCKED", 2)
    assert _map_grpc_status(grpc.StatusCode.NOT_FOUND) == (404, "SANDBOX_NOT_FOUND", None)
    assert _map_grpc_status(grpc.StatusCode.INTERNAL) == (500, "INTERNAL_ERROR", None)


def test_deadline_tiers_default_and_update(client):
    seen: dict[str, object] = {}
    original = client._call

    def spy(rpc, method, request, *, deadline_ms=None):
        seen[rpc] = deadline_ms
        return original(rpc, method, request, deadline_ms=deadline_ms)

    client._call = spy
    try:
        client.create(cubebox_pb2.RunCubeSandboxRequest())
        client.update(cubebox_pb2.UpdateCubeSandboxRequest(sandboxID="sbx-1"))
    finally:
        client._call = original
    # SS-14 RT-07: Create 实测 >5s（镜像检查 + 网络分配 + 卷 attach + vmm 启动）
    # → 专用 120s deadline 档（_create_deadline_ms），不再走默认 5s 档。
    assert seen["Create"] == 120000  # Create tier (client._create_deadline_ms)
    assert seen["Update"] == 30000  # Update tier
    assert client._deadline_ms == 5000
    assert client._create_deadline_ms == 120000
    assert client._update_deadline_ms == 30000


@pytest.mark.asyncio
async def test_health_probe_updates_heartbeat(db_session, monkeypatch):
    from datetime import datetime, timedelta, timezone

    from app.core.config import get_settings
    from app.models.sandbox_node import NodeStatus, SandboxNode

    monkeypatch.setattr(get_settings(), "CUBE_DATAPLANE_ENABLED", True)
    node = SandboxNode(
        node_id="cube-node-1", hostname="cube-1", ip_address="10.0.0.1",
        status=NodeStatus.ONLINE.value,
        capabilities={"cubelet_endpoint": "10.0.0.1:9443", "runtime_version": "deadbeef"},
    )
    db_session.add(node)
    await db_session.flush()

    calls: list[str] = []

    class _StubClient:
        def list(self, req):
            calls.append("list")
            return cubebox_pb2.ListCubeSandboxResponse()

        def get_storage_metrics(self, req):
            calls.append("get_storage_metrics")
            return cubebox_pb2.GetStorageMetricsResponse()

    def factory(_node, _settings):
        return _StubClient()

    result = await cube_node_client.cube_node_health_probe(db_session, client_factory=factory)
    assert result == {"cube-node-1": True}
    assert calls == ["list", "get_storage_metrics"]
    assert node.last_heartbeat is not None
    assert node.last_heartbeat > datetime.now(timezone.utc) - timedelta(seconds=5)
    assert node.health_state == "healthy"
