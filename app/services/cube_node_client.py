"""Cubelet node gRPC client (SS-10 CB-01).

Per-node channel pool + typed wrappers for the 15 ``CubeboxMgr`` RPCs +
``Ret.ret_code`` → W2 error mapping + the node health probe.

Transport model (spec §2.10):
- The Cubelet *main* gRPC is plaintext-only upstream. CDS dials the node's TLS
  front-proxy **sidecar** (``CDS_CUBE_NODE_TLS_PROXY_PORT``, default 9443) with
  mTLS. There is **no silent plaintext fallback**: with ``CDS_CUBE_NODE_GRPC_TLS``
  True (production) a missing/unloadable credential set fails closed (raises
  ``RUNTIME_UNAVAILABLE``); with it False the client uses an insecure channel,
  which is the explicit dev-only allowance (grpc.dev).
- Sidecar unreachable == node down (drives the W14 chain via the health probe).

Idempotency + deadlines (spec §2.2):
- Every RPC whose request carries a ``requestID`` field is stamped with
  ``requestID=uuid4``. ``ListCubeSandboxRequest`` has no ``requestID`` field.
- Deadline tiers: default ``CDS_CUBE_NODE_GRPC_DEADLINE_MS`` (5000); ``Update``
  runs at 30000.

The generated stub is synchronous (``import grpc``); the async runtime adapter
wraps these blocking calls with ``asyncio.to_thread``.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

import grpc

from app._stubs.cube.cubebox.v1 import cubebox_pb2, cubebox_pb2_grpc
from app._stubs.cube.errorcode.v1 import errorcode_pb2
from app.core.config import Settings, get_settings
from app.core.errors import CDSError

logger = logging.getLogger(__name__)

# ── Ret.ret_code (ErrorCode) → W2 error-bucket constants ────────────────────
# Success: no error raised (the RPC completed).
_SUCCESS_RET_CODES = frozenset({errorcode_pb2.OK, errorcode_pb2.Success})
# Resource does not exist on the node → 404 SANDBOX_NOT_FOUND.
_NOT_FOUND_RET_CODES = frozenset({errorcode_pb2.ContainerNotFound})
# State conflict (already in a different/terminal state) → 409.
_CONFLICT_RET_CODES = frozenset({errorcode_pb2.Conflict})
# Terminal sandbox state (exited by user) → 410 SESSION_TERMINATED (stop retries).
_TERMINAL_RET_CODES = frozenset({errorcode_pb2.ContainerStateExitedByUser})
# Transient concurrency lock → 503 + Retry-After.
_TRANSIENT_LOCK_RET_CODES = frozenset(
    {errorcode_pb2.ConcurrentFailed, errorcode_pb2.ConcurrentLimited}
)
# Upstream timeouts → 408 REQUEST_TIMEOUT.
_TIMEOUT_RET_CODES = frozenset(
    {
        errorcode_pb2.DeployCodeTimeout,
        errorcode_pb2.DeployLayerTimeout,
        errorcode_pb2.DeployCodeLayerTimeout,
        errorcode_pb2.RunContainerTimeout,
        errorcode_pb2.PullImageTimeOut,
        errorcode_pb2.LoadUserProcTimeout,
        errorcode_pb2.PreStopTimeOut,
    }
)

# Reverse map of every ErrorCode value → name (for honest, readable 500s).
_ERROR_CODE_NAMES: dict[int, str] = {
    v.number: v.name for v in errorcode_pb2.DESCRIPTOR.enum_types_by_name["ErrorCode"].values
}


def error_code_name(code: int) -> str:
    """Human-readable name for an ErrorCode int (``Unknown`` for unmapped)."""
    return _ERROR_CODE_NAMES.get(int(code), "Unknown")


def map_ret_code(ret: Any) -> CDSError | None:
    """Map a Cubelet ``Ret`` (ret_code/ret_msg) to a W2 ``CDSError``.

    Returns ``None`` when the code is a success code (0/200). Every non-success
    code maps to exactly one W2 bucket:

    ┌ ret_code bucket          ┬ W2 code               ├ HTTP ┬ Retry-After
    ├──────────────────────────┼───────────────────────┼──────┼─────────────
    │ OK(0), Success(200)     │ (no error)            │  –   │  –
    │ ContainerNotFound       │ SANDBOX_NOT_FOUND     │ 404  │  –
    │ Conflict                │ SESSION_STATE_CONFLICT│ 409  │  –
    │ ContainerStateExitedByUser│ SESSION_TERMINATED  │ 410  │  –
    │ Concurrent{Failed,Limited}│ OPERATION_LOCKED    │ 503  │ 2
    │ *Timeout/TimeOut codes  │ REQUEST_TIMEOUT       │ 408  │  –
    │ everything else         │ INTERNAL_ERROR        │ 500  │  –
    └──────────────────────────┴───────────────────────┴──────┴─────────────
    """
    if ret is None:
        return None
    code = int(ret.ret_code)
    msg = ret.ret_msg or f"cubelet upstream error {error_code_name(code)} ({code})"
    detail = {"ret_code": code, "ret_msg": ret.ret_msg}
    if code in _SUCCESS_RET_CODES:
        return None
    if code in _NOT_FOUND_RET_CODES:
        return CDSError(msg, code="SANDBOX_NOT_FOUND", http_status=404, detail=detail)
    if code in _CONFLICT_RET_CODES:
        return CDSError(msg, code="SESSION_STATE_CONFLICT", http_status=409, detail=detail)
    if code in _TERMINAL_RET_CODES:
        return CDSError(msg, code="SESSION_TERMINATED", http_status=410, detail=detail)
    if code in _TRANSIENT_LOCK_RET_CODES:
        return CDSError(msg, code="OPERATION_LOCKED", http_status=503, retry_after=2, detail=detail)
    if code in _TIMEOUT_RET_CODES:
        return CDSError(msg, code="REQUEST_TIMEOUT", http_status=408, detail=detail)
    return CDSError(
        f"cubelet upstream error {error_code_name(code)} ({code}): {msg}",
        code="INTERNAL_ERROR",
        http_status=500,
        detail=detail,
    )


def _map_grpc_status(status: "grpc.StatusCode") -> tuple[int, str, int | None]:
    """Map a gRPC *transport* status (distinct from ``Ret.ret_code``) to W2.

    DEADLINE_EXCEEDED → 408 (the §2.2 timeout tier); UNAVAILABLE/ABORTED are
    transient → 503 + Retry-After (a single failure is not, on its own, node
    down — repeated probe failures drive the W14 chain); everything else → 500.
    """
    if status == grpc.StatusCode.DEADLINE_EXCEEDED:
        return 408, "REQUEST_TIMEOUT", None
    if status in (grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.ABORTED):
        return 503, "OPERATION_LOCKED", 2
    if status == grpc.StatusCode.NOT_FOUND:
        return 404, "SANDBOX_NOT_FOUND", None
    if status in (grpc.StatusCode.CANCELLED,):
        return 408, "REQUEST_TIMEOUT", None
    return 500, "INTERNAL_ERROR", None


def _load_pem(value: str | None) -> str | None:
    """Return PEM bytes-as-str from an inline PEM or a file path; None on failure."""
    if not value:
        return None
    value = value.strip()
    if "-----BEGIN" in value:
        return value
    try:
        with open(value, "r", encoding="utf-8") as fh:
            data = fh.read()
        return data if data.strip() else None
    except OSError:
        return None


def build_node_channel_creds(settings: Settings | None = None) -> grpc.ChannelCredentials | None:
    """Build mTLS channel credentials for the node sidecar.

    Returns ``None`` when the insecure (dev) path is selected — the caller then
    uses ``grpc.insecure_channel``. When TLS is required but material is missing
    or unloadable it **fails closed** (raises ``RUNTIME_UNAVAILABLE``) rather
    than silently downgrading to plaintext.
    """
    settings = settings or get_settings()
    if not settings.CUBE_NODE_GRPC_TLS:
        return None
    ca_pem = _load_pem(settings.CUBE_NODE_TLS_CA)
    if not ca_pem:
        raise CDSError(
            "node mTLS enabled but CDS_CUBE_NODE_TLS_CA is not set or loadable",
            code="RUNTIME_UNAVAILABLE",
            http_status=400,
            detail={"reason": "missing_node_ca"},
        )
    client_cert = _load_pem(settings.CUBE_NODE_TLS_CERT)
    client_key = _load_pem(settings.CUBE_NODE_TLS_KEY)
    if not (client_cert and client_key):
        raise CDSError(
            "node mTLS enabled but node-client cert/key unavailable "
            "(CDS_CUBE_NODE_TLS_CERT / CDS_CUBE_NODE_TLS_KEY); refusing plaintext fallback",
            code="RUNTIME_UNAVAILABLE",
            http_status=400,
            detail={"reason": "missing_node_client_cert"},
        )
    return grpc.ssl_channel_credentials(
        root_certificates=ca_pem,
        private_key=client_key,
        certificate_chain=client_cert,
    )


class CubeNodeClient:
    """Synchronous gRPC client for a single Cubelet node (the 15 RPCs)."""

    def __init__(
        self,
        target: str,
        *,
        insecure: bool = False,
        creds: grpc.ChannelCredentials | None = None,
        deadline_ms: int | None = None,
    ):
        self._target = target
        self._deadline_ms = deadline_ms if deadline_ms is not None else get_settings().CUBE_NODE_GRPC_DEADLINE_MS
        self._update_deadline_ms = 30000  # §2.2: Update runs at 30s
        # SS-14 RT-07 (2026-10-14): Create 实测 >5s（镜像检查 + 网络分配 +
        # 卷 attach + vmm 启动）→ 专用长 deadline, 对齐 Update 模式；
        # 其余短 RPC 保持 §2.2 默认 5s 分级。
        self._create_deadline_ms = 120000
        if insecure:
            self._channel = grpc.insecure_channel(target)
        else:
            self._channel = grpc.secure_channel(target, creds or build_node_channel_creds())
        self._stub = cubebox_pb2_grpc.CubeboxMgrStub(self._channel)

    # ── internal helpers ────────────────────────────────────────────────────
    @staticmethod
    def _stamp(request: Any) -> None:
        if hasattr(request, "requestID"):
            request.requestID = str(uuid.uuid4())

    def _call(self, rpc: str, method, request: Any, *, deadline_ms: int | None = None) -> Any:
        timeout = (deadline_ms or self._deadline_ms) / 1000.0
        try:
            return method(request, timeout=timeout)
        except grpc.RpcError as exc:
            status, code, retry_after = _map_grpc_status(exc.code())
            raise CDSError(
                f"cubelet {rpc} RPC failed: {exc.details()}",
                code=code,
                http_status=status,
                retry_after=retry_after,
                detail={"rpc": rpc, "grpc_status": str(exc.code())},
            ) from exc

    def _rpc(self, rpc: str, method, request: Any, *, deadline_ms: int | None = None) -> Any:
        self._stamp(request)
        resp = self._call(rpc, method, request, deadline_ms=deadline_ms)
        err = map_ret_code(getattr(resp, "ret", None))
        if err is not None:
            raise err
        return resp

    def close(self) -> None:
        try:
            self._channel.close()
        except Exception:  # noqa: BLE001 — best-effort teardown
            pass

    # ── the 15 CubeboxMgr RPCs ──────────────────────────────────────────────
    def create(self, request: "cubebox_pb2.RunCubeSandboxRequest") -> "cubebox_pb2.RunCubeSandboxResponse":
        return self._rpc("Create", self._stub.Create, request, deadline_ms=self._create_deadline_ms)

    def destroy(self, request: "cubebox_pb2.DestroyCubeSandboxRequest") -> "cubebox_pb2.DestroyCubeSandboxResponse":
        return self._rpc("Destroy", self._stub.Destroy, request)

    def list(self, request: "cubebox_pb2.ListCubeSandboxRequest") -> "cubebox_pb2.ListCubeSandboxResponse":
        # ListCubeSandboxRequest has no requestID field; stamp() is a no-op.
        return self._call("List", self._stub.List, request)

    def update(self, request: "cubebox_pb2.UpdateCubeSandboxRequest") -> "cubebox_pb2.UpdateCubeSandboxResponse":
        return self._rpc("Update", self._stub.Update, request, deadline_ms=self._update_deadline_ms)

    def exec(self, request: "cubebox_pb2.ExecCubeSandboxRequest") -> "cubebox_pb2.ExecCubeSandboxResponse":
        return self._rpc("Exec", self._stub.Exec, request)

    def app_snapshot(self, request: "cubebox_pb2.AppSnapshotRequest") -> "cubebox_pb2.AppSnapshotResponse":
        return self._rpc("AppSnapshot", self._stub.AppSnapshot, request)

    def commit_sandbox(self, request: "cubebox_pb2.CommitSandboxRequest") -> "cubebox_pb2.CommitSandboxResponse":
        return self._rpc("CommitSandbox", self._stub.CommitSandbox, request)

    def rollback_sandbox(self, request: "cubebox_pb2.RollbackSandboxRequest") -> "cubebox_pb2.RollbackSandboxResponse":
        return self._rpc("RollbackSandbox", self._stub.RollbackSandbox, request)

    def cleanup_template(self, request: "cubebox_pb2.CleanupTemplateRequest") -> "cubebox_pb2.CleanupTemplateResponse":
        return self._rpc("CleanupTemplate", self._stub.CleanupTemplate, request)

    def list_sandbox_snapshots(self, request: "cubebox_pb2.ListSandboxSnapshotsRequest") -> "cubebox_pb2.ListSandboxSnapshotsResponse":
        return self._rpc("ListSandboxSnapshots", self._stub.ListSandboxSnapshots, request)

    def list_local_snapshots(self, request: "cubebox_pb2.ListLocalSnapshotsRequest") -> "cubebox_pb2.ListLocalSnapshotsResponse":
        return self._rpc("ListLocalSnapshots", self._stub.ListLocalSnapshots, request)

    def get_local_snapshot(self, request: "cubebox_pb2.GetLocalSnapshotRequest") -> "cubebox_pb2.GetLocalSnapshotResponse":
        return self._rpc("GetLocalSnapshot", self._stub.GetLocalSnapshot, request)

    def get_storage_metrics(self, request: "cubebox_pb2.GetStorageMetricsRequest") -> "cubebox_pb2.GetStorageMetricsResponse":
        return self._rpc("GetStorageMetrics", self._stub.GetStorageMetrics, request)

    def inspect_storage_volumes(self, request: "cubebox_pb2.InspectStorageVolumesRequest") -> "cubebox_pb2.InspectStorageVolumesResponse":
        return self._rpc("InspectStorageVolumes", self._stub.InspectStorageVolumes, request)

    def cleanup_orphan_storage_files(self, request: "cubebox_pb2.CleanupOrphanStorageFilesRequest") -> "cubebox_pb2.CleanupOrphanStorageFilesResponse":
        return self._rpc("CleanupOrphanStorageFiles", self._stub.CleanupOrphanStorageFiles, request)


def node_cubelet_endpoint(node: Any) -> str | None:
    """The node's cubelet dial target from its capability JSON.

    CB-01 carries node-side metadata in the existing ``SandboxNode.capabilities``
    JSON (``cubelet_endpoint`` / ``runtime_version``) because the dedicated
    columns are the m1 migration (CB-02/CB-05 territory, out of scope here).
    """
    caps = getattr(node, "capabilities", None) or {}
    endpoint = caps.get("cubelet_endpoint")
    return endpoint if endpoint else None


def node_runtime_version(node: Any) -> str | None:
    caps = getattr(node, "capabilities", None) or {}
    version = caps.get("runtime_version")
    return version if version else None


def build_node_target(endpoint: str, settings: Settings | None = None) -> str:
    """Compose the dial target: host[:port] (default port = TLS proxy port)."""
    settings = settings or get_settings()
    endpoint = endpoint.strip()
    if ":" in endpoint:
        return endpoint
    return f"{endpoint}:{settings.CUBE_NODE_TLS_PROXY_PORT}"


class CubeNodeClientPool:
    """Per-node gRPC channel cache (one channel per node target)."""

    def __init__(self, settings: Settings | None = None):
        self._settings = settings or get_settings()
        self._clients: dict[str, CubeNodeClient] = {}

    def for_node(self, node: Any) -> CubeNodeClient:
        endpoint = node_cubelet_endpoint(node)
        if not endpoint:
            raise CDSError(
                f"node {getattr(node, 'node_id', '?')} has no cubelet_endpoint (capabilities)",
                code="RUNTIME_UNAVAILABLE",
                http_status=400,
                detail={"node_id": getattr(node, "node_id", None)},
            )
        target = build_node_target(endpoint, self._settings)
        client = self._clients.get(target)
        if client is None:
            insecure = not self._settings.CUBE_NODE_GRPC_TLS
            creds = None if insecure else build_node_channel_creds(self._settings)
            client = CubeNodeClient(
                target,
                insecure=insecure,
                creds=creds,
                deadline_ms=self._settings.CUBE_NODE_GRPC_DEADLINE_MS,
            )
            self._clients[target] = client
        return client

    def close_all(self) -> None:
        for client in self._clients.values():
            client.close()
        self._clients.clear()


_pool: CubeNodeClientPool | None = None


def get_cube_node_client_pool(settings: Settings | None = None) -> CubeNodeClientPool:
    global _pool
    if _pool is None:
        _pool = CubeNodeClientPool(settings)
    return _pool


def get_cube_node_client(node: Any, settings: Settings | None = None) -> CubeNodeClient:
    return get_cube_node_client_pool(settings).for_node(node)


# ── Node health probe (merged into the session cleanup loop) ─────────────────
# Last successful probe timestamp per node (in-memory throttle so the probe
# fires at most every CDS_CUBE_NODE_HEARTBEAT_SECONDS even if the cleanup loop
# wakes faster). This is deliberately NOT a new event loop — it rides the
# existing ``session_cleanup_loop``.
_last_probe: dict[str, float] = {}


def _dataplane_nodes(nodes: list[Any]) -> list[Any]:
    """Nodes that are schedulable cube nodes (online, not cordoned, have an endpoint)."""
    from app.models.sandbox_node import NodeStatus

    out = []
    for node in nodes:
        if node.status != NodeStatus.ONLINE.value:
            continue
        if getattr(node, "scheduling_disabled", False):
            continue
        if not node_cubelet_endpoint(node):
            continue
        out.append(node)
    return out


async def cube_node_health_probe(
    db,
    *,
    client_factory=None,
    settings: Settings | None = None,
) -> dict[str, bool]:
    """Probe each eligible cube node with ``List`` (empty) + ``GetStorageMetrics``.

    Returns ``{node_id: healthy}``. Healthy nodes get a fresh ``last_heartbeat``;
    unhealthy nodes are left for the W14 stale/offline chain (``detect_stale_nodes``)
    to mark — this probe does not auto-recover. Errors are isolated per node so
    one bad node never breaks the loop.
    """
    settings = settings or get_settings()
    if not settings.CUBE_DATAPLANE_ENABLED:
        return {}
    from datetime import datetime, timezone

    from sqlalchemy import select

    from app.models.sandbox_node import SandboxNode

    result = await db.execute(select(SandboxNode))
    nodes = list(result.scalars().all())
    eligible = _dataplane_nodes(nodes)
    if not eligible:
        return {}
    now = time.monotonic()
    min_interval = max(0, settings.CUBE_NODE_HEARTBEAT_SECONDS)
    ok: dict[str, bool] = {}
    for node in eligible:
        node_id = node.node_id
        last = _last_probe.get(node_id)
        if last is not None and (now - last) < min_interval:
            ok[node_id] = True  # throttled — keep last known-good
            continue
        try:
            client = (client_factory or get_cube_node_client)(node, settings)
            await asyncio.to_thread(client.list, cubebox_pb2.ListCubeSandboxRequest())
            await asyncio.to_thread(
                client.get_storage_metrics, cubebox_pb2.GetStorageMetricsRequest()
            )
            node.last_heartbeat = datetime.now(timezone.utc)
            if getattr(node, "health_state", None) != "healthy":
                node.health_state = "healthy"
            _last_probe[node_id] = time.monotonic()
            ok[node_id] = True
        except Exception as exc:  # noqa: BLE001 — per-node isolation
            ok[node_id] = False
            if getattr(node, "health_state", None) in (None, "healthy"):
                node.health_state = "stale"
            logger.warning(
                "[CubeNode] health probe failed for node %s: %s", node_id, exc
            )
    return ok
