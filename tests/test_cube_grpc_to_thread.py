"""Cube M4 (cr-2026-10-15 §4.2 *生产门*) — request-path gRPC must leave the loop.

``cube_node_client.CubeNodeClient`` wraps a *synchronous* generated stub: the
RPC blocks whatever thread calls it. Called bare from an ``async def`` request
path it freezes the whole API event loop for the RPC deadline (慢节点停摆整个
worker). Both fix sites are pinned with the same STRUCTURAL probe: while the
fake client is blocked it calls back into the event loop via
``asyncio.run_coroutine_threadsafe`` and awaits the future — that future can
only resolve when the loop is free, i.e. when the RPC actually ran on a
``asyncio.to_thread`` worker. A regression to a bare call deadlocks the probe
against its 2s timeout and the test fails loudly; the recorded thread ident
must additionally differ from the loop thread's.

Also pins the source-level shape (same-line ``to_thread`` wrapping) so a
rewrite that silently drops the offload turns red even without a real node.
"""
from __future__ import annotations

import asyncio
import inspect
import threading
import time
import uuid

import pytest

from app._stubs.cube.cubebox.v1 import cubebox_pb2
from app.models.data_product import DataProduct
from app.models.sandbox_node import SandboxNode
from app.models.sandbox_session import SandboxSession
from app.models.user import User
from app.services import cube_egress, cube_node_client
from app.services.cube_runtime_adapter import CubeRuntimeAdapter
from app.services.network_policy import NetworkPolicyConfig


class _BlockingProbe:
    """Fake gRPC client that blocks and reports WHERE it ran.

    ``loop_alive_during_call`` is the structural assertion: scheduling a
    coroutine onto the loop and collecting its result only succeeds while the
    loop is unblocked — impossible if this method ran on the loop thread.
    """

    BLOCK_S = 0.25
    PROBE_TIMEOUT_S = 2.0

    def __init__(self, loop: asyncio.AbstractEventLoop, response_factory) -> None:
        self.loop = loop
        self.response_factory = response_factory
        self.calls: list[dict] = []

    def _probe(self, rpc: str, request) -> object:
        ident = threading.get_ident()

        async def _beat() -> bool:
            return True

        fut = asyncio.run_coroutine_threadsafe(_beat(), self.loop)
        loop_alive = fut.result(timeout=self.PROBE_TIMEOUT_S)
        time.sleep(self.BLOCK_S)
        self.calls.append({
            "rpc": rpc,
            "thread_ident": ident,
            "loop_alive_during_call": loop_alive,
        })
        return self.response_factory()

    def update(self, request):
        return self._probe("Update", request)

    def exec(self, request):
        return self._probe("Exec", request)


# ── cube_egress: Update(CubeNetworkConfig) hot-swap push ───────────────────


async def _mk_l2c_session(db_session, container_id: str) -> SandboxSession:
    unique = uuid.uuid4().hex[:8]
    user = User(
        username=f"m4_{unique}", email=f"m4_{unique}@example.com",
        hashed_password="x", role="data_provider",
    )
    db_session.add(user)
    await db_session.flush()
    product = DataProduct(provider_id=user.id, name=f"m4-product-{unique}")
    db_session.add(product)
    await db_session.flush()
    session = SandboxSession(
        user_id=user.id, data_product_id=product.id, sandbox_level="l2c",
        status="running", container_id=container_id,
    )
    db_session.add(session)
    node = SandboxNode(
        node_id="node1", hostname="cube-node-1", ip_address="10.1.1.1",
        status="online",
        capabilities={"cubelet_endpoint": "10.1.1.1:9443", "runtime_version": "9b75d33"},
    )
    db_session.add(node)
    await db_session.commit()
    return session


@pytest.mark.asyncio
async def test_egress_update_runs_off_the_event_loop(db_session, monkeypatch):
    session = await _mk_l2c_session(db_session, "l2c-node1-" + "c" * 32)
    probe = _BlockingProbe(
        asyncio.get_running_loop(), lambda: cubebox_pb2.UpdateCubeSandboxResponse(),
    )
    monkeypatch.setattr(
        cube_node_client, "get_cube_node_client", lambda node, settings=None: probe,
    )

    result = await cube_egress.apply_network_policy_to_cube(
        str(session.id),
        NetworkPolicyConfig(mode="allowlist", allowed_ips=["1.2.3.4"]),
        db=db_session,
    )
    assert result["applied"] is True
    assert len(probe.calls) == 1
    call = probe.calls[0]
    # structural: the loop kept serving while the RPC was in flight …
    assert call["loop_alive_during_call"] is True, (
        "client.update blocked the event loop — the RPC did NOT run via "
        "asyncio.to_thread (Cube M4 regression)"
    )
    # … and it demonstrably ran on another thread.
    assert call["thread_ident"] != threading.get_ident()


def test_egress_module_has_no_bare_client_update_left():
    """Source lock: every client.update( line is a to_thread dispatch."""
    offenders = [
        line.strip()
        for line in inspect.getsource(cube_egress).splitlines()
        if "client.update(" in line and "to_thread" not in line
    ]
    assert offenders == [], f"bare client.update call sites: {offenders}"


# ── cube_runtime_adapter: Exec on the execute() request path ───────────────


@pytest.mark.asyncio
async def test_adapter_execute_offloads_rpc_off_the_event_loop(monkeypatch):
    probe = _BlockingProbe(
        asyncio.get_running_loop(), lambda: cubebox_pb2.ExecCubeSandboxResponse(),
    )
    adapter = CubeRuntimeAdapter(client_factory=lambda node: probe)
    adapter._node_cache["n1"] = {
        "node_id": "n1", "endpoint": "10.0.0.1:9443", "runtime_version": "v",
    }

    # 宿主读回面短路：.code 三行 (rc / size / DONE) + 输出体。
    def _fake_read(cubebox_id: str, session_id: str, vm_path: str):
        if vm_path.endswith(".code"):
            return True, b"0\n6\nDONE\n"
        return True, b"hello\n"

    monkeypatch.setattr(adapter, "_read_sandbox_file", _fake_read)

    result = await adapter.execute("l2c-n1-" + "e" * 32, "print('hello')", "python")
    assert result["exit_code"] == 0
    assert result["output"] == "hello\n"
    assert len(probe.calls) == 1
    call = probe.calls[0]
    assert call["loop_alive_during_call"] is True, (
        "client.exec blocked the event loop — the RPC did NOT run via "
        "asyncio.to_thread (Cube M4 regression)"
    )
    assert call["thread_ident"] != threading.get_ident()


def test_execute_method_has_no_bare_client_exec_left():
    """Source lock scoped to the async execute() request path.

    Scoped on purpose: ``_inject_session_data_sync`` is a sync helper that is
    documented (and entered) only through ``inject_session_data``'s to_thread,
    so its bare ``client.exec`` lines there are correct as-is.
    """
    offenders = [
        line.strip()
        for line in inspect.getsource(CubeRuntimeAdapter.execute).splitlines()
        if "client.exec(" in line and "to_thread" not in line
    ]
    assert offenders == [], f"bare client.exec in execute(): {offenders}"
