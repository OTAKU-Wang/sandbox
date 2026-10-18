"""A15 — the ops views never awaited the K8s adapter factory.

``app/services/k8s_sandbox.get_k8s_adapter`` is ``async def`` (it constructs the
adapter on first use), but ``app/api/k8s_ops.py::_cluster_view`` called it
without ``await``. ``adapter`` was therefore a *coroutine object*:
``getattr(adapter, "_k8s", None)`` is always ``None``, so every ops endpoint
answered ``cluster_available: false`` on a cluster that is up, and the coroutine
was destroyed unawaited (RuntimeWarning, the factory body never ran). No
exception is raised, so the ``except`` branch never noticed either.

The pre-existing suite asserted ``cluster_available is False`` four times — an
assertion that cannot fail while the bug exists. These cases pin the live
branch (fake python client attached) *and* the honest-degradation branch, and
they pin that the factory was actually awaited.
"""
import uuid

import pytest

from app.models.sandbox_session import SandboxSession
from app.models.user import User
from app.services.auth_service import create_access_token

POD = "sandbox-abc0123"


class FakeMeta:
    def __init__(self, name):
        self.name = name


class FakeStatus:
    phase = "Bound"
    capacity = {"storage": "1Gi"}


class FakePvcSpec:
    access_modes = ["ReadWriteMany"]
    storage_class_name = "local-path"


class FakePvc:
    def __init__(self, name):
        self.metadata = FakeMeta(name)
        self.status = FakeStatus()
        self.spec = FakePvcSpec()


class FakeK8sClient:
    """Stand-in for ``app.services.k8s_client.KubernetesClient``."""

    def list_pods(self, label_selector=None):
        return [type("Pod", (), {"metadata": FakeMeta(POD)})()]

    def list_pvcs(self):
        return [FakePvc("cds-shared-vol")]

    def pod_logs(self, pod_name):
        return "first line\n\nsecond line\n"


class FakeAdapter:
    def __init__(self, with_client: bool):
        self.namespace = "cds-ops-test"
        self._k8s = FakeK8sClient() if with_client else None

    def list_sandboxes(self, user_id=None):
        return [{"pod_name": POD, "user_id": "u-1", "session_id": "s-1", "phase": "Running"}]

    def get_status(self, pod_name):
        return {"phase": "Running", "cds_status": "running", "ready": True,
                "pod_ip": "10.0.0.9", "reason": ""}


def _install_adapter(monkeypatch, adapter) -> list:
    """Patch the factory the ops router calls and record every (awaited) call."""
    import app.services.k8s_sandbox as k8s_sandbox

    calls: list[int] = []

    async def fake_get_k8s_adapter():
        calls.append(1)
        return adapter

    monkeypatch.setattr(k8s_sandbox, "get_k8s_adapter", fake_get_k8s_adapter)
    return calls


async def _operator_headers(db_session) -> dict:
    from app.models.user import UserRole

    user = User(email=f"ops-{uuid.uuid4().hex[:8]}@test", username=f"ops{uuid.uuid4().hex[:6]}",
                password_hash="x", role=UserRole.OPERATOR.value)
    db_session.add(user)
    await db_session.flush()
    await db_session.refresh(user)
    return {"Authorization": f"Bearer {create_access_token(user.id, user.role)}"}


@pytest.mark.asyncio
async def test_deployments_use_the_live_cluster_when_the_client_is_attached(
    client, db_session, monkeypatch
):
    calls = _install_adapter(monkeypatch, FakeAdapter(with_client=True))
    headers = await _operator_headers(db_session)

    resp = await client.get("/api/v1/ops/deployments", headers=headers)

    assert resp.status_code == 200, resp.text
    assert calls, (
        "get_k8s_adapter() is `async def` — calling it without `await` hands "
        "back a coroutine, so the live branch never runs (A15)"
    )
    body = resp.json()
    assert body["cluster_available"] is True
    assert body["namespace"] == "cds-ops-test"
    assert body["count"] == 1
    assert body["items"][0]["pod_name"] == POD
    assert body["items"][0]["ready"] is True
    assert body["items"][0]["phase"] == "Running"


@pytest.mark.asyncio
async def test_pvcs_report_live_claims_when_attached(client, db_session, monkeypatch):
    calls = _install_adapter(monkeypatch, FakeAdapter(with_client=True))
    headers = await _operator_headers(db_session)

    body = (await client.get("/api/v1/ops/pvcs", headers=headers)).json()

    assert calls
    assert body["cluster_available"] is True


@pytest.mark.asyncio
async def test_k8s_session_logs_come_from_the_pod(client, db_session, monkeypatch):
    calls = _install_adapter(monkeypatch, FakeAdapter(with_client=True))
    headers = await _operator_headers(db_session)
    session = SandboxSession(
        user_id=uuid.uuid4(), status="running", timeout_seconds=60,
        sandbox_level="k8s", container_id=f"k8s-{POD}",
    )
    db_session.add(session)
    await db_session.flush()
    await db_session.refresh(session)

    body = (await client.get(f"/api/v1/ops/logs/{session.id}", headers=headers)).json()

    assert calls
    assert body["source"] == "k8s"
    assert body["cluster_available"] is True
    assert [line["line"] for line in body["logs"]] == ["first line", "second line"]


@pytest.mark.asyncio
async def test_ops_still_degrade_honestly_without_a_python_client(
    client, db_session, monkeypatch
):
    """Reverse case: with no attached client the views must stay `false` — and
    the factory must still have been awaited (the bug was silent either way)."""
    calls = _install_adapter(monkeypatch, FakeAdapter(with_client=False))
    headers = await _operator_headers(db_session)

    for path in ("/api/v1/ops/deployments", "/api/v1/ops/network-policies", "/api/v1/ops/pvcs"):
        resp = await client.get(path, headers=headers)
        assert resp.status_code == 200, path
        assert resp.json()["cluster_available"] is False, path
    assert calls == [1, 1, 1]


@pytest.mark.asyncio
async def test_role_gate_unchanged(client, db_session, monkeypatch):
    """Ops stays operator/admin-only after the fix."""
    _install_adapter(monkeypatch, FakeAdapter(with_client=True))
    buyer, _ = await _make(client, db_session, "buyer", "ops_buyer")
    resp = await client.get("/api/v1/ops/deployments", headers=buyer)
    assert resp.status_code == 403
    assert resp.json()["code"] == "HTTP_403"


async def _make(client, db_session, role: str, name: str):
    from app.models.user import UserRole

    user = User(email=f"{name}-{uuid.uuid4().hex[:8]}@test", username=f"{name}{uuid.uuid4().hex[:6]}",
                password_hash="x", role=UserRole(role).value)
    db_session.add(user)
    await db_session.flush()
    await db_session.refresh(user)
    return {"Authorization": f"Bearer {create_access_token(user.id, user.role)}"}, str(user.id)
