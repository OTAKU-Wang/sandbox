"""Cube (L2') runtime adapter (SS-10 CB-01).

Implements ``RuntimeAdapter`` against the Cubelet node gRPC client:
provision (node selection + ``Create``), execute (``Exec``), terminate
(idempotent ``Destroy``), get_status (``List`` + local state) and the
day-1 envd streaming spike.

Node locality (CB-01, before the m3 ``runtime_ref`` migration): the node the
sandbox lives on is recorded in the container id as ``l2c-<node_id>-<sandbox_id>``
so that the sync lifecycle methods (terminate/get_status) can re-resolve the
node endpoint. ``sandbox_id`` is the cubelet-returned id.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import os
import random
import uuid
from datetime import datetime, timezone

from app._stubs.cube.cubebox.v1 import cubebox_pb2
from app.core.config import get_settings
from app.core.database import async_session
from app.core.errors import CDSError
from app.models.volume_key import VolumeKey
from sqlalchemy import select
from app.services import cube_node_client
from app.services.cube_node_client import (
    node_cubelet_endpoint,
    node_runtime_version,
)
from app.services.sandbox_runtime import RuntimeAdapter, SessionStatus

logger = logging.getLogger(__name__)

_CONTAINER_PREFIX = "l2c-"
_ENVD_PORT = 49983  # §2.7 day-1: in-sandbox envd HTTP streaming endpoint
_VOLUME_PLUGIN_DRIVER = "cds-encrypted"


def pinned_cube_version() -> str:
    """The CUBE runtime version pinned for the node fleet (from CB-00's pin).

    A deployment whose fleet runs a specific release commit (e.g. the one-click
    v0.7.3-rc2) overrides the stub-generation pin via CUBE_PINNED_RUNTIME_VERSION;
    the override is a fleet declaration, not a stub claim.
    """
    from app.core.config import get_settings
    override = get_settings().CUBE_PINNED_RUNTIME_VERSION
    if override:
        return override
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "_stubs", "cube", "_VERSION.json")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return str(data.get("commit") or data.get("cube_version") or "")
    except (OSError, ValueError):
        return ""


def _run_sync(coro):
    """Run an async coroutine from a sync context via the dedicated sync DB bridge.

    SS-14 RT-07 (2026-10-14): the bridge owns its own thread + loop + engine
    (app/core/database.SyncDbBridge) — the shared asyncpg engine is bound to
    the main event loop, and the old per-call new-thread/new-loop approach
    reused it cross-loop (asyncpg "attached to a different loop", plus a
    native segfault in production). Coroutines passed here must use
    sync_bridge_session(), never the shared async_session.
    """
    from app.core.database import sync_db_bridge

    return sync_db_bridge.run(coro)


async def _eligible_cube_nodes(db, *, runtime_version: str | None = None):
    """Online, schedulable cube nodes (have a cubelet endpoint; optional version match)."""
    from sqlalchemy import select

    from app.models.sandbox_node import NodeStatus, SandboxNode

    result = await db.execute(
        select(SandboxNode).where(
            SandboxNode.status == NodeStatus.ONLINE.value,
            SandboxNode.scheduling_disabled == False,  # noqa: E712
        )
    )
    nodes = list(result.scalars().all())
    out = []
    for node in nodes:
        if not node_cubelet_endpoint(node):
            continue
        if runtime_version and node_runtime_version(node) != runtime_version:
            continue
        out.append(node)
    return out


async def apply_template_locality(nodes, template_id: str | None = None, db=None):
    """Template-locality filter (SS-10 CB-05, spec §4 step 4 / §2.5).

    When a template reference is given (the create flow passes the ``template``
    field through — a cube template id/name, or a Round 40 JSON session
    template declaring ``cube_template_id``), candidates are narrowed to the
    nodes where that template is READY (``cube_template_nodes``). An
    unresolvable reference yields an empty set, which the caller surfaces as
    503 ``NO_NODE_TEMPLATE`` (W2, docs/error-codes.md).

    ``db`` (a session already bound to the caller's event loop) is used for
    the lookup — the adapter's sync bridge runs on its own loop, so opening
    the shared session factory here would cross loops.
    """
    if template_id is None:
        return nodes
    if db is None:
        from app.core.database import async_session

        db = async_session()
    from sqlalchemy import select

    from app.models.cube_template import CubeTemplateNode, CubeTemplateNodeStatus
    from app.services.cube_template_service import cube_template_service

    async with db:
        tpl = await cube_template_service.resolve_cube_template(db, template_id)
        if tpl is None:
            from app.services import session_templates as st

            if st.get_template(template_id) is not None:
                # JSON-only Round 40 session template (no cube_template_id):
                # no cube-locality constraint — schedule normally.
                return nodes
            return []
        result = await db.execute(
            select(CubeTemplateNode.node_id).where(
                CubeTemplateNode.template_id == tpl.id,
                CubeTemplateNode.status == CubeTemplateNodeStatus.READY.value,
            )
        )
        ready = {row[0] for row in result.all()}
    return [n for n in nodes if n.node_id in ready]


async def select_cube_node(
    db,
    *,
    runtime_version: str | None = None,
    template_id: str | None = None,
    require_tee: bool = False,
) -> dict | None:
    """Pick an eligible cube node: node_selector + runtime_version match.

    Returns ``{"node_id", "endpoint", "runtime_version", "capabilities"}``
    or ``None`` when no eligible node exists (no ``template_id``). With a
    ``template_id``, an empty READY set raises 503 ``NO_NODE_TEMPLATE``
    (W2, spec CB-05 step 4). ``require_tee`` (SS-13) keeps only candidates
    whose capabilities JSON ``tee`` value is a real TEE kind (``tdx`` /
    ``sev_snp``) — a missing/malformed capability is treated as ``"none"``.
    Weighted-random over candidates by inverse CPU load (mirrors node_selector
    P1-6 hot-spot avoidance).
    """
    candidates = await _eligible_cube_nodes(db, runtime_version=runtime_version)
    had_candidates = bool(candidates)
    if require_tee:
        candidates = [n for n in candidates if node_tee_kind(n) in ("tdx", "sev_snp")]
        had_candidates = bool(candidates)
    candidates = await apply_template_locality(candidates, template_id, db=db)
    if not candidates:
        if template_id is not None:
            raise CDSError(
                "no node has the requested cube template READY (wait for distribution or contact the admin)",
                code="NO_NODE_TEMPLATE",
                http_status=503,
                detail={
                    "template_id": template_id,
                    "reason": "no_cube_nodes" if not had_candidates else "no_ready_node",
                },
            )
        return None
    weights = [max(0.0, 1.0 - (n.cpu_usage or 0) / 100.0) + random.uniform(0, 0.1) for n in candidates]
    total = sum(weights)
    if total <= 0:
        chosen = candidates[-1]
    else:
        r = random.uniform(0, total)
        acc = 0.0
        chosen = candidates[-1]
        for node, w in zip(candidates, weights):
            acc += w
            if r <= acc:
                chosen = node
                break
    return {
        "node_id": chosen.node_id,
        "endpoint": node_cubelet_endpoint(chosen),
        "runtime_version": node_runtime_version(chosen),
        "capabilities": chosen.capabilities or {},
    }


def node_tee_kind(node) -> str:
    """The node's TEE kind from its capabilities JSON (SS-13 RT-03 probe
    output: ``tdx`` / ``sev_snp`` / ``none``); safe default ``"none"``.
    """
    caps = getattr(node, "capabilities", None)
    if not isinstance(caps, dict):
        return "none"
    tee = caps.get("tee", "none")
    return tee if isinstance(tee, str) else "none"


async def count_eligible_cube_nodes(db, *, runtime_version: str | None = None) -> int:
    return len(await _eligible_cube_nodes(db, runtime_version=runtime_version))


def build_volume_private_data(session_id: str, session_key_id: str | None = None) -> dict:
    """The ``private_data`` embedded in the plugin-volume-sources annotation.

    CDS-side storage point: the session's KMS-wrapped DEK handle. ``shared_volume``
    stores no volume secrets, so CB-01 derives the ref from the session identity
    (``kms:session-<sid>`` or the concrete ``session_key_id`` when present). CB-03
    wires this to the real KMS-wrapped DEK + the ``cds-encrypted`` volume plugin.
    Kept well under the 1024B annotation limit.
    """
    ref = session_key_id or f"kms:session-{session_id}"
    return {"session_dek_ref": ref, "session_id": str(session_id)}


def build_run_request(
    *,
    session_id: str,
    image: str,
    envs: dict[str, str],
    annotations: dict[str, str] | None = None,
    network_config: "cubebox_pb2.CubeNetworkConfig | None" = None,
    results_dir: str = "/var/lib/cds/sandbox-results",
    command: list[str] | None = None,
    cpu: str = "1",
    memory: str = "1Gi",
    storage_media: str = "",
) -> "cubebox_pb2.RunCubeSandboxRequest":
    """Assemble the ``RunCubeSandboxRequest`` for a fresh l2c sandbox (spec §2.3/2.4).

    Volumes: ``cds-data-<sid>`` (plugin, driver cds-encrypted),
    ``cds-results-<sid>`` (host_dir), ``cds-scratch-<sid>`` (empty_dir),
    ``cds-rootfs-<sid>`` (empty_dir medium=Default — the writable rootfs layer;
    upstream contract, cubelet prepareWritableRootfs: a container mount at
    container_path="/" must name an empty_dir(Default) volume, else 130483).
    All are mounted read-write into the sandbox.
    """
    req = cubebox_pb2.RunCubeSandboxRequest()
    req.requestID = str(uuid.uuid4())

    data_name = f"cds-data-{session_id}"
    results_name = f"cds-results-{session_id}"
    scratch_name = f"cds-scratch-{session_id}"
    rootfs_name = f"cds-rootfs-{session_id}"

    dv = req.volumes.add()
    dv.name = data_name
    dv.volume_source.plugin_volume.driver = _VOLUME_PLUGIN_DRIVER

    rv = req.volumes.add()
    rv.name = results_name
    hs = rv.volume_source.host_dir_volumes.volume_sources.add()
    hs.name = results_name
    hs.host_path = f"{results_dir.rstrip('/')}/{session_id}"

    sv = req.volumes.add()
    sv.name = scratch_name
    sv.volume_source.empty_dir.Medium = cubebox_pb2.StorageMediumDefault
    sv.volume_source.empty_dir.SizeLimit = "1Gi"

    wv = req.volumes.add()
    wv.name = rootfs_name
    wv.volume_source.empty_dir.Medium = cubebox_pb2.StorageMediumDefault
    wv.volume_source.empty_dir.SizeLimit = "10Gi"

    container = req.containers.add()
    container.name = "cds-sandbox"
    container.image.image = image
    # SS-14 RT-07 节点轨 (2026-10-14): storage_media 可配置（默认空 = OCI 镜像路径）。
    # 上游 cube_image_pull_route.go 实证: storage_media==ext4 才走 EnsurePmemFile,
    # 且 imageRef 须 SafeID（无路径分隔符）—— registry 引用 cds/cube-sandbox:latest
    # 走 ext4 分支必败 invalid imageRef; ext4 是 TC 模板分发专用（上游
    # distribution.go:65 实证）, 业务沙箱镜像走 OCI 拉取（LocalResolve/PullImage）。
    container.image.storage_media = storage_media
    # Cubelet requires container resources ("should provide resources param");
    # CDS sizes the sandbox VM from its session spec (defaults 1 CPU / 1Gi).
    container.resources.cpu = cpu
    container.resources.mem = memory
    container.resources.cpu_limit = cpu
    container.resources.mem_limit = memory
    for key, value in envs.items():
        env = container.envs.add()
        env.key = key
        env.value = value
    for name, path, readonly in (
        (rootfs_name, "/", False),
        (data_name, "/data", False),
        (results_name, "/results", False),
        (scratch_name, "/scratch", False),
    ):
        mount = container.volume_mounts.add()
        mount.name = name
        mount.container_path = path
        mount.readonly = readonly
    if command:
        container.command.extend(command)
    else:
        # SS-14 RT-07 (2026-10-14): 镜像默认 Cmd 无常驻进程时（如 python:3.12-slim
        # 的 [python3] REPL 无 TTY 即退）, 主进程退出 → containerd task 被收 →
        # 上游 Exec "task not found"/"Broken pipe"（130497, 实测）。
        # 沙箱 init 显式置常驻 sleep; 工作负载经 Exec 注入。
        container.command.extend(["sleep", "infinity"])

    for key, value in (annotations or {}).items():
        req.annotations[key] = value

    if network_config is not None:
        req.cube_network_config.CopyFrom(network_config)
    return req


class CubeRuntimeAdapter(RuntimeAdapter):
    """RuntimeAdapter for the L2' (Cube) node data plane."""

    def __init__(self, *, client_factory=None, node_selector=None):
        self._client_factory = client_factory or cube_node_client.get_cube_node_client
        self._node_selector = node_selector or select_cube_node
        self._node_cache: dict[str, dict] = {}
        self._settings = get_settings()

    # ── container id helpers ────────────────────────────────────────────────
    @staticmethod
    def _parse_container_id(container_id: str) -> tuple[str, str]:
        # SS-14 RT-07 (2026-10-14): node_id 可含连字符（node-local-1）——
        # 按 cubelet sandbox_id（尾部 32 位 hex, 实证 cubebox ls）右切, 不能左切。
        import re

        m = re.fullmatch(r"l2c-(.+)-([0-9a-f]{32})", container_id)
        if not m:
            raise CDSError(
                f"malformed l2c container id: {container_id!r}",
                code="INTERNAL_ERROR",
                http_status=500,
            )
        return m.group(1), m.group(2)

    # ── node resolution ─────────────────────────────────────────────────────
    def _cache_node(self, node: dict) -> None:
        self._node_cache[node["node_id"]] = node

    def _node_by_id(self, node_id: str) -> dict | None:
        return self._node_cache.get(node_id)

    # ── cube-master volume lifecycle (SS-14 RT-07, 2026-10-14) ───────────────
    def _master_ensure_volume(self, volume_name: str) -> None:
        """Pre-create the session's cds-encrypted volume via cube-master.

        Upstream fact: only the master's volume plane triggers the plugin's
        CreateVolume (CubeMaster/pkg/volume/plugin); cubelet's RunCubeSandbox
        never pre-creates plugin volumes, so Attach fails with "volume not
        found" without this call (fail-closed).
        """
        import httpx

        base = self._settings.CUBE_MASTER_ADDR.rstrip("/")
        # Bounded retry: the first create after a plugin/master restart can time
        # out on the client side while the volume IS created (gRPC cold dial +
        # KMS registration); a retry then hits ret_code 130409 (already exists)
        # which is success-equivalent for our pre-create purpose.
        last_err: str = "unknown"
        for attempt in range(3):
            try:
                resp = httpx.post(
                    f"{base}/cube/volume",
                    json={"name": volume_name, "driver": _VOLUME_PLUGIN_DRIVER},
                    timeout=15.0,
                )
            except httpx.TimeoutException as exc:
                last_err = f"timed out (attempt {attempt + 1}/3)"
                continue
            if resp.status_code != 200:
                raise CDSError(
                    f"cube-master volume create HTTP {resp.status_code}: {resp.text[:200]}",
                    code="INTERNAL_ERROR",
                    http_status=500,
                )
            ret = (resp.json() or {}).get("ret") or {}
            code = ret.get("ret_code")
            if code in (0, 200):
                return
            if code == 130409:
                # Stale record (e.g. a prior timed-out create whose plugin-side
                # registration then failed) — clear it and rebuild.
                self._master_delete_volume(volume_name)
                continue
            last_err = str(ret.get("ret_msg"))
            break  # non-retryable rejection (param errors etc.)
        raise CDSError(
            f"cube-master volume create failed for {volume_name}: {last_err}",
            code="INTERNAL_ERROR",
            http_status=500,
        )

    def _master_delete_volume(self, volume_name: str) -> None:
        """Best-effort volume teardown after sandbox destroy (idempotent)."""
        import httpx

        base = self._settings.CUBE_MASTER_ADDR.rstrip("/")
        try:
            resp = httpx.delete(f"{base}/cube/volume/{volume_name}", timeout=15.0)
            if resp.status_code != 200:
                logger.warning(
                    "[CubeRuntime] volume delete %s HTTP %s: %s",
                    volume_name, resp.status_code, resp.text[:200],
                )
        except Exception as exc:
            # Teardown must never mask a successful sandbox destroy; a leftover
            # volume is an orphan, not a session leak (log + audit).
            logger.warning("[CubeRuntime] volume delete %s failed: %s", volume_name, exc)

    async def _resolve_node(self, node_id: str) -> dict:
        from app.core.database import sync_db_bridge, sync_bridge_session

        async def _do_resolve() -> dict:
            from sqlalchemy import select

            from app.models.sandbox_node import SandboxNode

            async with sync_bridge_session() as db:
                result = await db.execute(
                    select(SandboxNode).where(SandboxNode.node_id == node_id)
                )
                node = result.scalar_one_or_none()
            if node is None:
                raise CDSError(
                    f"cube node {node_id} no longer exists in CDS",
                    code="SANDBOX_NOT_FOUND",
                    http_status=404,
                )
            info = {
                "node_id": node.node_id,
                "endpoint": node_cubelet_endpoint(node),
                "runtime_version": node_runtime_version(node),
            }
            if not info["endpoint"]:
                raise CDSError(
                    f"cube node {node_id} has no cubelet_endpoint",
                    code="RUNTIME_UNAVAILABLE",
                    http_status=400,
                )
            return info

        cached = self._node_by_id(node_id)
        if cached is not None:
            return cached
        # DB 查询一律经桥（独立 loop+引擎）: 桥 session 属桥循环, 在本
        # （主）循环直接 await 必段错误（asyncpg 跨 loop, v18/v20/v34 同源）。
        info = sync_db_bridge.run(_do_resolve())
        self._cache_node(info)
        return info

    def _resolve_node_sync(self, node_id: str) -> dict:
        cached = self._node_by_id(node_id)
        if cached is not None:
            return cached
        # 同步上下文（无事件循环, 如 to_thread 内）: 走桥循环执行异步解析,
        # 不得在事件循环内 _run_sync（asyncio.run 于运行中 loop = RuntimeError）。
        import asyncio

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return _run_sync(self._resolve_node(node_id))
        from app.core.database import sync_db_bridge

        return sync_db_bridge.run(self._resolve_node(node_id))

    def _client_for(self, node: dict):
        class _Node:
            node_id = node["node_id"]
            capabilities = {"cubelet_endpoint": node["endpoint"]}

        return self._client_factory(_Node())

    # ── RuntimeAdapter ──────────────────────────────────────────────────────
    async def prepare_session_volume(self, session_id: str, data: bytes) -> str:
        """Async pre-create of the session's cds-encrypted volume + DEK ref + data.

        SS-14 RT-07 (2026-10-14): 卷面 httpx 走 to_thread（事件循环空闲可服务
        插件的 KMS 回调, 否则循环依赖死锁）; DEK 回查在主循环上 await
        （_run_sync 新线程+新 loop 复用共享引擎会触发 asyncpg 跨 loop
        空指针段错误, 实测复现）。PutVolumeData 走节点插件 CDS 扩展 API
        （unix socket, 同步 gRPC → to_thread）: 插件 Attach 前强制要求
        卷已有数据（fail-closed, 上游契约）, 而 attach 唯一触发点是沙箱
        create —— 故数据必须在 provision 前写入。
        """
        if not self._settings.CUBE_MASTER_ADDR:
            raise CDSError(
                "CDS_CUBE_MASTER_ADDR not configured — cannot pre-create the "
                "cds-encrypted session volume (fail-closed)",
                code="INTERNAL_ERROR",
                http_status=500,
            )
        volume_name = f"cds-data-{session_id}"
        await asyncio.to_thread(self._master_ensure_volume, volume_name)
        dek_ref = None
        for attempt in range(10):
            async with async_session() as db:
                result = await db.execute(
                    select(VolumeKey).where(VolumeKey.volume_id == volume_name)
                    .order_by(VolumeKey.created_at.desc())
                    .limit(1)
                )
                row = result.scalar_one_or_none()
            if row is not None:
                dek_ref = row.key_id
                break
            await asyncio.sleep(0.2)
        if dek_ref is None:
            raise CDSError(
                f"no VolumeKey row for volume {volume_name} after KMS registration "
                "(fail-closed)",
                code="INTERNAL_ERROR",
                http_status=500,
            )
        await asyncio.to_thread(self._put_volume_data, volume_name, data)
        return dek_ref

    def _plugin_stub(self):
        """Sync gRPC channel to the node volume plugin (CDS extension API).

        SS-14 RT-07 (2026-10-14): PutVolumeData 与 ReadSandboxFile 共用同一
        插件 socket（单节点拓扑; 多节点逐节点地址 = 已知限制, SS-14 文档）。
        仅在同步线程（to_thread）中调用。
        """
        import grpc

        from app._stubs.cdsvolume.cdsvolume.v1 import cdsvolume_pb2_grpc

        socket_path = self._settings.CUBE_VOLUME_PLUGIN_SOCKET
        if not socket_path:
            raise CDSError(
                "CDS_CUBE_VOLUME_PLUGIN_SOCKET not configured — cannot reach "
                "the node volume plugin (fail-closed)",
                code="INTERNAL_ERROR",
                http_status=500,
            )
        return cdsvolume_pb2_grpc.CDSVolumeExtensionStub(
            grpc.insecure_channel(f"unix://{socket_path}")
        )

    def _read_sandbox_file(self, cubebox_id: str, session_id: str, vm_path: str) -> tuple[bool, bytes]:
        """Read a file out of the live sandbox rootfs via the node plugin.

        SS-14 RT-07 (2026-10-14): 上游冷启动沙箱不把声明卷挂进 guest
        （/results、/scratch 仅为可写 rootfs overlay upper 里的普通目录,
        实证 disk/<cubebox>/upper/）。exec 输出/沙箱文件因此从宿主侧
        只读回环挂载 rootfs 镜像文件读回（插件 ReadSandboxFile, 绝不
        宿主写——双挂载会损坏 VMM 在写的 ext4）。返回 (exists, bytes)。
        同步方法, 调用方走 to_thread。
        """
        from app._stubs.cdsvolume.cdsvolume.v1 import cdsvolume_pb2

        stub = self._plugin_stub()
        resp = stub.ReadSandboxFile(
            cdsvolume_pb2.ReadSandboxFileRequest(
                cubebox_id=cubebox_id, session_id=session_id, vm_path=vm_path
            ),
            timeout=30.0,
        )
        return resp.exists, resp.data

    async def inject_session_data(self, container_id: str, data: bytes, max_attempts: int = 3) -> None:
        """Inject the session product data into the live sandbox VM (async).

        同步 gRPC 链走 to_thread, 不占事件循环（见 SyncDbBridge 死锁教训）。
        """
        await asyncio.to_thread(self._inject_session_data_sync, container_id, data, max_attempts)

    def _inject_session_data_sync(self, container_id: str, data: bytes, max_attempts: int) -> None:
        """SS-14 RT-07 (2026-10-14): 上游冷启动沙箱不把声明卷挂进 guest,
        VM 内可用副本经 exec 通道写入 /data/session-data（base64 分块
        append, 单次 exec ≤ ~170KB 参数面）。VM 刚建出时 exec 可能未就绪
        —— 有界重试。同步方法, 仅经 to_thread 调用。
        """
        import base64
        import time

        node_id, sandbox_id = self._parse_container_id(container_id)
        node = self._resolve_node_sync(node_id)
        client = self._client_for(node)

        b64 = base64.b64encode(data).decode("ascii")
        chunk_size = 120 * 1024
        chunks = [b64[i:i + chunk_size] for i in range(0, len(b64), chunk_size)] or [""]
        last_err: Exception | None = None
        for attempt in range(max_attempts):
            try:
                for idx, chunk in enumerate(chunks):
                    op = ">" if idx == 0 else ">>"
                    request = cubebox_pb2.ExecCubeSandboxRequest(
                        sandbox_id=sandbox_id,
                        container_id=sandbox_id,
                        args=["sh", "-c",
                              f"mkdir -p /data && echo -n '{chunk}' {op} /data/.session-data.b64"],
                    )
                    client.exec(request)
                    # 块间小间隔, 避免 exec 进程堆积
                    if idx != len(chunks) - 1:
                        time.sleep(0.3)
                # 解码落位 + 自检
                request = cubebox_pb2.ExecCubeSandboxRequest(
                    sandbox_id=sandbox_id,
                    container_id=sandbox_id,
                    args=["sh", "-c",
                          "base64 -d /data/.session-data.b64 > /data/session-data "
                          "&& rm -f /data/.session-data.b64 "
                          "&& echo CDS-INJECT-OK"],
                )
                client.exec(request)
                return
            except CDSError as exc:
                last_err = exc
                time.sleep(5)
        raise CDSError(
            f"session data injection into sandbox {sandbox_id} failed after "
            f"{max_attempts} attempts: {last_err.message if last_err else 'unknown'}",
            code="INTERNAL_ERROR",
            http_status=500,
        )

    def _put_volume_data(self, volume_name: str, data: bytes) -> int:
        """Push the session data into the node volume plugin (CDS extension RPC).

        SS-10 spec step 3: the stock volumeplugin protocol carries no data
        path; CDS pushes the data snapshot over the CDS-owned extension API
        (the control plane is the only authorized caller). Local single-node
        deployment: the plugin socket lives on the same host as the API
        (unix socket). Multi-node topologies need per-node addressing —
        a known limitation, documented in the SS-14 record.
        """
        import grpc

        from app._stubs.cdsvolume.cdsvolume.v1 import cdsvolume_pb2, cdsvolume_pb2_grpc

        socket_path = self._settings.CUBE_VOLUME_PLUGIN_SOCKET
        if not socket_path:
            raise CDSError(
                "CDS_CUBE_VOLUME_PLUGIN_SOCKET not configured — cannot push "
                "session data to the node volume plugin (fail-closed)",
                code="INTERNAL_ERROR",
                http_status=500,
            )
        with grpc.insecure_channel(f"unix://{socket_path}") as channel:
            stub = cdsvolume_pb2_grpc.CDSVolumeExtensionStub(channel)
            try:
                resp = stub.PutVolumeData(
                    cdsvolume_pb2.PutVolumeDataRequest(volume_id=volume_name, data=data),
                    timeout=30.0,
                )
            except grpc.RpcError as exc:
                raise CDSError(
                    f"volume PutVolumeData failed for {volume_name}: {exc.details()}",
                    code="INTERNAL_ERROR",
                    http_status=500,
                ) from exc
        return int(resp.data_size_bytes)

    def provision(self, session_id: str, data_path: str, timeout: int = 3600, user_id: str = "",
                  cube_node: dict | None = None, volume_dek_ref: str | None = None) -> dict:
        settings = self._settings
        if not settings.CUBE_DATAPLANE_ENABLED:
            return {
                "error": "L2' (cube) data plane is not enabled (CDS_CUBE_DATAPLANE_ENABLED=false)",
                "status": SessionStatus.FAILED.value,
                "container_id": None,
            }
        node = cube_node
        if node is None:
            node = _run_sync(self._select_with_fresh_db())
        if node is None:
            return {
                "error": "no eligible cube node (runtime_version/endpoint match)",
                "status": SessionStatus.FAILED.value,
                "container_id": None,
            }
        self._cache_node(node)
        client = self._client_for(node)

        envs = {
            "HOME": "/home/sandbox",
            "TMPDIR": "/tmp",
            "CDS_SANDBOX_LEVEL": "l2c",
            "CDS_SESSION_ID": str(session_id),
            "CDS_DEK_HEX": os.urandom(max(1, settings.CUBE_DEK_LENGTH)).hex(),
        }
        # SS-14 RT-07 (2026-10-14): 卷预建 + DEK ref 由异步 prepare_session_volume
        # 完成（见其 docstring）; 未提供 ref = 未预建 = 诚实拒绝, 不静默降级。
        if volume_dek_ref is None:
            return {
                "error": "session volume not pre-created (prepare_session_volume "
                         "must run first) — fail-closed",
                "status": SessionStatus.FAILED.value,
                "container_id": None,
            }
        volume_name = f"cds-data-{session_id}"
        dek_ref = volume_dek_ref
        # SS-14 RT-07 (2026-10-14): cubelet bind-mounts the per-session results
        # host_dir (upstream prepareHostDirVolume); a missing directory is a
        # raw "mount: ... No such file" 130545 — create it up-front so the
        # error, when any, is CDS-side and actionable.
        results_session_dir = f"{settings.CUBE_RESULTS_DIR.rstrip('/')}/{session_id}"
        try:
            os.makedirs(results_session_dir, exist_ok=True)
        except OSError as exc:
            return {
                "error": f"cannot create session results dir {results_session_dir}: {exc}",
                "status": SessionStatus.FAILED.value,
                "container_id": None,
            }
        annotations = {
            "cds.cube.pause-snapshot-id": "",  # pause snapshot id slot (spec §2.6)
            # SS-14 RT-07 (2026-10-14): 键名对齐 cubelet 实际读取的 annotation
            # (上游 pluginvolume.go lookupPluginVolumeSource: annotations["plugin-volume-sources"]);
            # 旧键 cube.cds.* 不被 cubelet 识别 → 退化到 plugin_volume.driver 回退,
            # 该回退不携带 private_data (上游实证) → 卷丢失 KMS DEK 引用 (不可解密)。
            "plugin-volume-sources": json.dumps([
                {
                    "name": volume_name,
                    "driver": _VOLUME_PLUGIN_DRIVER,
                    # 双重编码: 上游 entry.private_data 是 string 字段
                    # (CubeMaster util.go entry 结构 + 插件 ParsePrivateData 按 JSON 字符串解析);
                    # 传嵌套对象会使 cubelet 反序列化失败 → 静默回退到无 private_data
                    # 的 plugin_volume 路径 → 插件 fail-closed 拒绝。
                    "private_data": json.dumps({"session_dek_ref": dek_ref}),
                }
            ]),
        }
        request = build_run_request(
            session_id=str(session_id),
            image=settings.CUBE_SANDBOX_IMAGE,
            envs=envs,
            annotations=annotations,
            network_config=self._network_config(session_id),
            results_dir=settings.CUBE_RESULTS_DIR,
            cpu=settings.CUBE_SANDBOX_CPU,
            memory=settings.CUBE_SANDBOX_MEMORY,
            storage_media=settings.CUBE_SANDBOX_STORAGE_MEDIA,
        )
        try:
            response = client.create(request)
        except CDSError as exc:
            logger.error("[CubeRuntime] Create failed for session %s: %s", session_id, exc)
            return {"error": str(exc.message), "status": SessionStatus.FAILED.value, "container_id": None}
        sandbox_id = response.sandboxID or str(uuid.uuid4())
        container_id = f"{_CONTAINER_PREFIX}{node['node_id']}-{sandbox_id}"
        return {
            "container_id": container_id,
            "status": SessionStatus.RUNNING.value,
            "workspace": None,
            "node_id": node["node_id"],
            "sandbox_id": sandbox_id,
        }

    async def _select_with_fresh_db(self):
        from app.core.database import sync_bridge_session

        async with sync_bridge_session() as db:
            return await self._node_selector(db, runtime_version=pinned_cube_version())

    def _network_config(self, session_id: str) -> "cubebox_pb2.CubeNetworkConfig":
        """Full-state CubeNetworkConfig for Run — SS-10 CB-04 (spec §2.8/§4).

        Compiles the session's policy package with
        policy_compiler.compile_cube_network (the documented interface left
        by CB-01). Fail-closed: any load/compile failure yields the
        default-deny config (allow_internet_access=False, no allow_out,
        no rules) with a warning — never a widened config, and never a
        provision abort. The API layer's subsequent create_policy push
        re-asserts the same full state (hot-swap path).
        """
        from app.services.policy_compiler import compile_cube_network

        try:
            package = _run_sync(self._load_policy_package(session_id))
        except Exception as exc:
            logger.warning(
                "CB-04: policy package load failed for %s: %s — "
                "default-deny CubeNetworkConfig (honest degradation)",
                session_id, exc,
            )
            return cubebox_pb2.CubeNetworkConfig(allow_internet_access=False)
        try:
            return compile_cube_network(package)
        except Exception as exc:
            logger.warning(
                "CB-04: cube_network compile failed for %s: %s — "
                "default-deny CubeNetworkConfig (honest degradation)",
                session_id, exc,
            )
            return cubebox_pb2.CubeNetworkConfig(allow_internet_access=False)

    @staticmethod
    async def _load_policy_package(session_id: str) -> dict:
        """The session's policy package (ip/cidr + dns whitelist custody —
        app/models/network_policy.py; contract terms rules/credentials land
        via the hot-swap push, app/services/cube_egress.load_policy_package).
        """
        from sqlalchemy import select

        from app.core.database import sync_bridge_session
        from app.models.network_policy import NetworkPolicy as NetworkPolicyModel

        async with sync_bridge_session() as db:
            row = (await db.execute(
                select(NetworkPolicyModel).where(
                    NetworkPolicyModel.session_id == str(session_id)
                )
            )).scalar_one_or_none()
        return {
            "allowed_ips": list(row.allowed_ips or []) if row else [],
            "allowed_domains": list(row.allowed_domains or []) if row else [],
            "deny_out": [],
            "rules": [],
            "credentials": [],
        }

    async def execute(self, container_id: str, code: str, language: str = "python",
                      *, session_key: str | None = None, env_vars: dict | None = None,
                      timeout: int | None = None, **_: object) -> dict:
        # SS-14 RT-07 (2026-10-14): 上游 Exec 是 fire-and-forget（dummy IO 丢弃
        # stdout, exec.go 实证）—— 输出经文件中转: 包装命令把 stdout/stderr 与
        # 退出码写入容器内 /results/.cds-exec/<exec_id>[.code]。宿主读回面（2026-10-14
        # 实证）: 冷启动沙箱的声明卷不进 guest, /results 仅为可写 rootfs overlay
        # upper 里的普通目录 → 经节点插件 ReadSandboxFile（rootfs 镜像只读
        # 回环挂载）读回, 有界轮询。
        import base64

        node_id, sandbox_id = self._parse_container_id(container_id)
        node = await self._resolve_node(node_id)
        client = self._client_for(node)
        # 节点信息同步缓存, 供后续同步调用（inject/exec 轮询外的同步路径）。
        self._cache_node(node)
        exec_id = uuid.uuid4().hex
        out_path = f"/results/.cds-exec/{exec_id}"
        code_path = f"{out_path}.code"
        if language in ("python", "python3"):
            b64 = base64.b64encode(code.encode("utf-8")).decode("ascii")
            inner = f"python3 -c \"import base64;exec(base64.b64decode('{b64}').decode())\""
        else:
            inner = code
        cmd = (f"mkdir -p /results/.cds-exec && {inner} > {out_path} 2>&1; "
               f"rc=$?; echo $rc > {code_path}; "
               f"echo $(wc -c < {out_path}) >> {code_path}; "
               f"echo DONE >> {code_path}")
        request = cubebox_pb2.ExecCubeSandboxRequest(
            sandbox_id=sandbox_id,
            # 上游 Exec 同时强制 sandbox_id + container_id（exec.go:65 实证）;
            # 单容器沙箱首容器 ID = sandboxID（上游 generateContainerID:661 实证）。
            container_id=sandbox_id,
            args=["sh", "-c", cmd],
        )
        for key, value in (env_vars or {}).items():
            if value:
                request.env.append(f"{key}={value}")
        # 会话密钥只经 exec 环境变量注入（与旧 runtime 同契约, 不落盘）。
        if session_key:
            request.env.append(f"CDS_SESSION_KEY={session_key}")
        started = datetime.now()
        try:
            client.exec(request)
        except CDSError as exc:
            return {
                "output": str(exc.message),
                "exit_code": -1,
                "duration_ms": int((datetime.now() - started).total_seconds() * 1000),
                "sandbox_level": "l2c",
                "error": str(exc.message),
            }
        # .code 标记三行: rc / out 字节数 / DONE。DONE 行出现 = 包装链走完;
        # guest 的 ext4 写入经 VMM 异步刷到宿主文件, 读侧可能落后于 guest 视图
        # —— 按 size 截断 + 尺寸不符时重读（有界）。
        vm_out_rel = f"results/.cds-exec/{exec_id}"
        vm_code_rel = f"{vm_out_rel}.code"
        wait_limit = max(5.0, min(float(timeout or 120), 600.0))
        loop = asyncio.get_running_loop()
        deadline = loop.time() + wait_limit
        exit_code = None
        out_size = 0
        output = ""
        while loop.time() < deadline:
            exists, code_bytes = await asyncio.to_thread(
                self._read_sandbox_file, sandbox_id, "", vm_code_rel
            )
            if exists:
                lines = code_bytes.decode("ascii", "replace").splitlines()
                if len(lines) >= 3 and lines[2].strip() == "DONE":
                    try:
                        exit_code = int((lines[0] or "0").strip() or 0)
                        out_size = int((lines[1] or "0").strip() or 0)
                        break
                    except ValueError:
                        pass  # 部分写入, 继续轮询
            await asyncio.sleep(0.5)
        if exit_code is not None and out_size > 0:
            for _ in range(5):
                _, out_bytes = await asyncio.to_thread(
                    self._read_sandbox_file, sandbox_id, "", vm_out_rel
                )
                if len(out_bytes) >= out_size:
                    output = out_bytes[:out_size].decode("utf-8", "replace")
                    break
                await asyncio.sleep(0.5)
            else:
                output = out_bytes[:out_size].decode("utf-8", "replace") if out_bytes else ""
        if exit_code is None:
            return {
                "output": output or "",
                "exit_code": -1,
                "duration_ms": int((datetime.now() - started).total_seconds() * 1000),
                "sandbox_level": "l2c",
                "error": "exec output capture timed out (upstream Exec is fire-and-forget)",
            }
        return {
            "output": output,
            "exit_code": exit_code,
            "duration_ms": int((datetime.now() - started).total_seconds() * 1000),
            "sandbox_level": "l2c",
        }

    def execute_sync_for_usage(self, container_id: str, cmd: str) -> dict:
        """同步 exec 读回（供 to_thread 内使用, 如 /usage 的 du 统计）。

        与 execute() 同文件中转契约; 内部用新事件循环执行 async 路径
        （调用方在独立线程, 无运行中 loop → _run_sync 安全）。
        超时取短值: usage 是统计查询, 不得拖住请求。
        """
        import asyncio

        try:
            coro = self.execute(container_id, cmd, "bash", timeout=90)
            return asyncio.run(asyncio.wait_for(coro, timeout=120))
        except Exception as exc:  # noqa: BLE001 — usage 统计 best-effort
            return {"output": "", "exit_code": -1, "error": f"usage exec failed: {exc}"}

    @staticmethod
    def _exec_args(code: str, language: str) -> list[str]:
        if language in ("python", "python3"):
            return ["python3", "-c", code]
        return ["sh", "-c", code]

    def terminate(self, container_id: str) -> bool:
        node_id, sandbox_id = self._parse_container_id(container_id)
        try:
            node = self._resolve_node_sync(node_id)
        except CDSError as exc:
            logger.error("[CubeRuntime] cannot resolve node %s for terminate: %s", node_id, exc)
            return False
        client = self._client_for(node)
        request = cubebox_pb2.DestroyCubeSandboxRequest(sandboxID=sandbox_id)
        try:
            client.destroy(request)
            self._node_cache.pop(node_id, None)
            # SS-14 RT-07 (2026-10-14): 沙箱销毁成功后回收会话卷（幂等 best-effort）。
            if self._settings.CUBE_MASTER_ADDR:
                self._master_delete_volume(f"cds-data-{sandbox_id}")
            return True
        except CDSError as exc:
            if exc.code == "SANDBOX_NOT_FOUND":
                return True  # already gone — idempotent
            if exc.code == "SESSION_STATE_CONFLICT":
                self._fire_critical_alert(
                    "cube_destroy_conflict",
                    f"Destroy on in-use sandbox {sandbox_id} (node {node_id}) rejected: {exc.message}",
                )
                return False
            logger.error("[CubeRuntime] Destroy %s failed: %s", sandbox_id, exc)
            return False

    def _fire_critical_alert(self, alert_type: str, message: str) -> None:
        """Raise a durable critical AlertRecord (existing alerting path) — never silent."""
        logger.error("[CubeRuntime][CRITICAL] %s: %s", alert_type, message)

        async def _persist():
            from app.core.database import sync_bridge_session
            from app.models.alert import AlertRecord

            async with sync_bridge_session() as db:
                db.add(AlertRecord(
                    alert_type=alert_type,
                    severity="critical",
                    message=message[:2000],
                    metadata={"runtime": "l2c"},
                ))
                await db.commit()

        try:
            _run_sync(_persist())
        except Exception as exc:  # noqa: BLE001 — alert persist must not raise
            logger.error("[CubeRuntime] failed to persist critical alert: %s", exc)

    def get_status(self, container_id: str) -> str:
        node_id, sandbox_id = self._parse_container_id(container_id)
        try:
            node = self._resolve_node_sync(node_id)
            client = self._client_for(node)
            request = cubebox_pb2.ListCubeSandboxRequest(id=sandbox_id)
            response = client.list(request)
        except CDSError:
            return "terminated"
        if response.items:
            return "running"
        return "terminated"

    async def execute_streaming(self, container_id: str, code: str, language: str = "python",
                                *, env_vars: dict | None = None, timeout: int | None = None,
                                on_line=None, **_: object) -> dict:
        """Day-1 streaming spike (spec §2.7): envd reachability check, short timeout.

        Reachable → (later CB-04) stream via the W10 frame protocol. Unreachable
        → honest STREAM_UNSUPPORTED result whose shape is identical to the one
        ``session_stream.py`` emits for non-L0 levels.
        """
        node_id, _ = self._parse_container_id(container_id)
        node = await self._resolve_node(node_id)
        host = node["endpoint"].split(":")[0]
        reachable = await self._envd_reachable(host, _ENVD_PORT, timeout=2.0)
        if not reachable:
            return {
                "type": "error",
                "code": "STREAM_UNSUPPORTED",
                "message": f"streaming exec not supported on l2c",
            }
        # Reachable: run the Exec RPC (best-effort) and report via on_line when
        # available. Full envd HTTP streaming lands with CB-04.
        if on_line is not None:
            on_line("stderr", f"l2c streaming via envd (host {host}:{_ENVD_PORT})")
        result = await self.execute(container_id, code, language, env_vars=env_vars, timeout=timeout)
        return result

    @staticmethod
    async def _envd_reachable(host: str, port: int, timeout: float = 2.0) -> bool:
        import socket

        def _check() -> bool:
            try:
                with socket.create_connection((host, port), timeout=timeout):
                    return True
            except OSError:
                return False

        return await asyncio.to_thread(_check)
