"""N7: K8sSandboxAdapter python-client path + streaming (mocked KubernetesClient).

Forces ``K8S_USE_PYTHON_CLIENT=true`` and patches the KubernetesClient class
with a fake so the adapter's dispatch logic is exercised without a cluster.
Also covers the W10 pod-exec streaming pump (fake WebSocket) and the honest
kubectl fallback when the client is unavailable.
"""
import json

import pytest

from app.core.config import get_settings


class _FakePod:
    def __init__(self, d):
        self._d = d

    def to_dict(self):
        return self._d


class _FakeWS:
    def __init__(self, chunks=()):
        self._chunks = list(chunks)
        self._idx = 0
        self._stdin = []
        self.closed = False

    def write_stdin(self, data):
        self._stdin.append(data)

    def update(self, timeout=1):
        pass

    def read_stdout(self, timeout=0.2):
        if self._idx < len(self._chunks) and self._chunks[self._idx][0] == "stdout":
            self._idx += 1
            return self._chunks[self._idx - 1][1]
        return ""

    def read_stderr(self, timeout=0.2):
        if self._idx < len(self._chunks) and self._chunks[self._idx][0] == "stderr":
            self._idx += 1
            return self._chunks[self._idx - 1][1]
        return ""

    def is_open(self):
        return self._idx < len(self._chunks)

    def close(self):
        self.closed = True


class _FakeK8sClient:
    def __init__(self, kubeconfig=None, namespace=None, pod=None):
        self.namespace = namespace or "cds-sandbox"
        self.applied = []
        self.deleted = []
        self.pvcs = []
        self._pod = pod or {
            "status": {"phase": "Running", "podIP": "10.42.0.9",
                       "containerStatuses": [{"name": "sandbox", "ready": True, "state": {"running": {}}}]},
        }
        self.ensure_ns_calls = 0
        self.exec_script_result = ("__CDS_EXIT__:0\nok", 0, None)
        self.ws = None

    def ensure_namespace(self):
        self.ensure_ns_calls += 1
        return False

    def apply_manifest(self, manifest):
        self.applied.append(manifest)

    def delete_resource(self, kind, name, api_version="v1"):
        self.deleted.append((kind, name))

    def get_pod(self, name):
        return _FakePod(self._pod)

    def list_pods(self, label_selector=None):
        return []

    def exec_stream(self, name, command):
        if self.ws is None:
            self.ws = _FakeWS()
        return self.ws

    def exec_script(self, pod_name, script_lines, timeout):
        return self.exec_script_result

    def ensure_pvc(self, name, size="1Gi", storage_class=None):
        self.pvcs.append(name)
        return {"status": "created", "storage_class": storage_class}


class _Unavailable(Exception):
    pass


def _enable_client(monkeypatch, fake_client=None, unavailable=False):
    import app.services.k8s_client as kc_mod

    monkeypatch.setenv("CDS_K8S_USE_PYTHON_CLIENT", "true")
    get_settings.cache_clear()
    if unavailable:
        def _ctor(*a, **k):
            raise _Unavailable("no cluster")
    elif fake_client is not None:
        def _ctor(*a, **k):
            return fake_client
    else:
        def _ctor(*a, **k):
            return _FakeK8sClient()
    monkeypatch.setattr(kc_mod, "KubernetesClient", _ctor)
    monkeypatch.setattr(kc_mod, "KubernetesClientUnavailable", _Unavailable)


def _mk_adapter(monkeypatch, fake_client=None, unavailable=False, **kwargs):
    from app.services.k8s_sandbox import K8sSandboxAdapter
    _enable_client(monkeypatch, fake_client, unavailable)
    try:
        return K8sSandboxAdapter(namespace="cds-test", **kwargs)
    finally:
        get_settings.cache_clear()


def test_adapter_uses_python_client_for_provision_and_status(monkeypatch):
    fake = _FakeK8sClient(pod={
        "status": {"phase": "Running", "podIP": "10.42.0.9",
                   "containerStatuses": [{"name": "sandbox", "ready": True, "state": {"running": {}}}]},
    })
    adapter = _mk_adapter(monkeypatch, fake, ready_timeout_seconds=0, poll_interval_seconds=0)

    from app.services.k8s_sandbox import SandboxPodSpec
    result = adapter.provision(SandboxPodSpec(session_id="1234567890abcdef9999", user_id="user-1", sandbox_level="k8s"))

    assert result["status"] == "running"
    assert result["pod_ip"] == "10.42.0.9"
    # Pod + NetworkPolicy + ResourceQuota applied via the client.
    kinds = {m["kind"] for m in fake.applied}
    assert kinds == {"Pod", "NetworkPolicy", "ResourceQuota"}
    pod_manifest = [m for m in fake.applied if m["kind"] == "Pod"][0]
    assert pod_manifest["metadata"]["labels"]["session-id"] == "1234567890abcdef9999"
    assert fake.ensure_ns_calls >= 1

    status = adapter.get_status("sandbox-1234567890abcdef")
    assert status["cds_status"] == "running" and status["ready"] is True


def test_adapter_falls_back_to_kubectl_when_client_unavailable(monkeypatch):
    from app.services.k8s_sandbox import _kubectl as _real_kubectl
    calls = []

    def fake_kubectl(*args, **kwargs):
        calls.append(args)
        return type("R", (), {"stdout": "", "stderr": "", "returncode": 0})()

    monkeypatch.setattr("app.services.k8s_sandbox._kubectl", fake_kubectl)
    adapter = _mk_adapter(monkeypatch, unavailable=True)
    assert adapter._k8s is None
    assert any(c[:2] == ("get", "namespace") for c in calls)


def test_adapter_terminate_uses_client_delete(monkeypatch):
    fake = _FakeK8sClient()
    adapter = _mk_adapter(monkeypatch, fake)
    assert adapter.terminate("sandbox-abc123") is True
    assert ("Pod", "sandbox-abc123") in fake.deleted
    assert ("NetworkPolicy", "sandbox-abc123-policy") in fake.deleted


def test_provision_ensures_pvc_for_volume_mounts(monkeypatch):
    fake = _FakeK8sClient()
    adapter = _mk_adapter(monkeypatch, fake, ready_timeout_seconds=0, poll_interval_seconds=0)

    from app.services.k8s_sandbox import SandboxPodSpec
    result = adapter.provision(SandboxPodSpec(
        session_id="1234567890abcdef9999", user_id="user-1", sandbox_level="k8s",
        volume_mounts=[{"name": "cds-shared-abc", "mountPath": "/workspace/shared/v", "readOnly": True}],
    ))
    assert result["status"] == "running"
    assert "cds-shared-abc" in fake.pvcs
    pod_manifest = [m for m in fake.applied if m["kind"] == "Pod"][0]
    pvc_volumes = [v for v in pod_manifest["spec"]["volumes"] if v["name"] == "cds-shared-abc"]
    assert pvc_volumes[0]["persistentVolumeClaim"]["claimName"] == "cds-shared-abc"
    assert pvc_volumes[0]["persistentVolumeClaim"]["readOnly"] is True
    mounts = pod_manifest["spec"]["containers"][0]["volumeMounts"]
    assert any(m["mountPath"] == "/workspace/shared/v" for m in mounts)


def test_build_pod_manifest_adds_pvc_volumes(monkeypatch):
    adapter = _mk_adapter(monkeypatch)
    from app.services.k8s_sandbox import SandboxPodSpec
    manifest = adapter._build_pod_manifest(
        SandboxPodSpec(session_id="s1", user_id="u1", sandbox_level="k8s",
                       volume_mounts=[{"name": "cds-shared-xyz", "mountPath": "/workspace/shared/x", "readOnly": False}]),
        "sandbox-s1",
        {"cpu_req": "250m", "cpu_lim": "1000m", "mem_req": "256Mi", "mem_lim": "512Mi", "disk": "1Gi"},
    )
    vol = {v["name"]: v for v in manifest["spec"]["volumes"]}
    assert vol["cds-shared-xyz"]["persistentVolumeClaim"]["claimName"] == "cds-shared-xyz"
    assert vol["cds-shared-xyz"]["persistentVolumeClaim"]["readOnly"] is False


@pytest.mark.asyncio
async def test_execute_uses_client_exec_script(monkeypatch):
    fake = _FakeK8sClient()
    fake.ws = _FakeWS([("stdout", "line1\n"), ("stdout", "__CDS_EXIT__:3\n")])
    adapter = _mk_adapter(monkeypatch, fake)
    from app.services.sandbox_runtime import K8sRuntimeAdapter
    runtime = K8sRuntimeAdapter()
    runtime._k8s = adapter

    result = await runtime.execute("k8s-1234567890abcdef9999", "print(1)", "python", timeout=5)
    assert result["exit_code"] == 3
    assert result["output"] == "line1\n__CDS_EXIT__:3\n"
    assert result["sandbox_level"] == "k8s"


@pytest.mark.asyncio
async def test_execute_streaming_pumps_lines_and_sentinel(monkeypatch):
    fake = _FakeK8sClient()
    fake.ws = _FakeWS([
        ("stdout", "line1\n"),
        ("stdout", "line2\n"),
        ("stderr", "warn\n"),
        ("stdout", "__CDS_EXIT__:0\n"),
    ])
    adapter = _mk_adapter(monkeypatch, fake)
    from app.services.sandbox_runtime import K8sRuntimeAdapter
    runtime = K8sRuntimeAdapter()
    runtime._k8s = adapter

    emitted = []

    async def on_line(name, line):
        emitted.append((name, line))

    result = await runtime.execute_streaming(
        "k8s-1234567890abcdef9999", "print(1)", "python", timeout=5, on_line=on_line,
    )
    assert result["exit_code"] == 0
    assert ("stdout", "line1") in emitted
    assert ("stdout", "line2") in emitted
    assert ("stderr", "warn") in emitted
    assert not any(l == "__CDS_EXIT__:0" for _n, l in emitted)  # sentinel stripped
    assert fake.ws.closed


@pytest.mark.asyncio
async def test_execute_streaming_fails_closed_on_callback_raise(monkeypatch):
    class Blocked(Exception):
        pass

    fake = _FakeK8sClient()
    fake.ws = _FakeWS([("stdout", "bad\n"), ("stdout", "__CDS_EXIT__:0\n")])
    adapter = _mk_adapter(monkeypatch, fake)
    from app.services.sandbox_runtime import K8sRuntimeAdapter
    runtime = K8sRuntimeAdapter()
    runtime._k8s = adapter

    async def on_line(name, line):
        raise Blocked("PII")

    with pytest.raises(Blocked):
        await runtime.execute_streaming("k8s-1234567890abcdef9999", "x", "python", timeout=5, on_line=on_line)
    assert fake.ws.closed


@pytest.mark.asyncio
async def test_facade_streaming_dispatch_k8s():
    from app.services.sandbox_runtime import SandboxRuntime

    class _FakeStreamAdapter:
        def __init__(self):
            self.dispatched = {}

        async def execute_streaming(self, container_id, code, language, **kwargs):
            self.dispatched.update({"container_id": container_id, "code": code, "language": language, **kwargs})
            return {"exit_code": 0, "duration_ms": 1, "sandbox_level": "k8s"}

    runtime = SandboxRuntime()
    fake = _FakeStreamAdapter()
    runtime._adapters["k8s"] = fake

    async def on_line(name, line):
        pass

    result = await runtime.execute_streaming("k8s-sess", "echo hi", "bash", on_line=on_line)
    assert result["sandbox_level"] == "k8s"
    assert fake.dispatched["container_id"] == "k8s-sess"
