"""N7: kubernetes python client backend unit tests (mocked, no cluster)."""
from types import SimpleNamespace

import pytest
from kubernetes.client import ApiException


def _fake_api_error(status: int) -> ApiException:
    exc = ApiException(http_resp=None)
    exc.status = status
    return exc


class _FakeNamespaceResp:
    metadata = None


class _FakeV1:
    def __init__(self):
        self.read_namespace_calls = 0
        self.create_namespace_calls = 0
        self.pvc_reads = 0
        self.pvc_created = []

    def read_namespace(self, name):
        self.read_namespace_calls += 1
        raise _fake_api_error(404)

    def create_namespace(self, body):
        self.create_namespace_calls += 1
        return body

    def read_namespaced_persistent_volume_claim(self, name, ns):
        self.pvc_reads += 1
        raise _fake_api_error(404)

    def create_namespaced_persistent_volume_claim(self, ns, body):
        self.pvc_created.append(body)
        return body


class _FakeSC:
    def __init__(self, name, annotations=None):
        self.metadata = type("M", (), {"name": name, "annotations": annotations or {}})()


class _FakeStorage:
    def __init__(self, classes):
        self._classes = classes

    def list_storage_class(self):
        return type("R", (), {"items": self._classes})()


def _install_fakes(monkeypatch, v1=None, storage=None, load_error=None):
    import app.services.k8s_client as mod

    if load_error:
        def _raise():
            raise load_error
        monkeypatch.setattr(mod.k8s_config, "load_kube_config", _raise)
    else:
        monkeypatch.setattr(mod.k8s_config, "load_kube_config", lambda *a, **k: None)
    monkeypatch.setattr(mod.k8s_config, "load_incluster_config", lambda *a, **k: None)
    monkeypatch.setattr(mod.k8s_client, "ApiClient", lambda: SimpleNamespace(configuration=SimpleNamespace(host="")))
    monkeypatch.setattr(mod.k8s_client, "CoreV1Api", lambda api: v1 or _FakeV1())
    monkeypatch.setattr(mod.k8s_client, "NetworkingV1Api", lambda api: None)
    monkeypatch.setattr(mod.k8s_client, "StorageV1Api", lambda api: storage or _FakeStorage([]))


def test_init_unavailable_without_kubeconfig(monkeypatch):
    from app.services.k8s_client import KubernetesClient, KubernetesClientUnavailable
    _install_fakes(monkeypatch, load_error=RuntimeError("no kubeconfig"))
    with pytest.raises(KubernetesClientUnavailable):
        KubernetesClient(namespace="cds-test")


def test_ensure_namespace_creates_when_missing(monkeypatch):
    from app.services.k8s_client import KubernetesClient
    v1 = _FakeV1()
    _install_fakes(monkeypatch, v1=v1)
    client = KubernetesClient(namespace="cds-test")
    assert client.ensure_namespace() is True
    assert v1.create_namespace_calls == 1
    assert v1.create_namespace_calls == 1


def test_ensure_pvc_honest_error_without_storage_class(monkeypatch):
    from app.services.k8s_client import KubernetesClient, KubernetesClientUnavailable
    _install_fakes(monkeypatch, storage=_FakeStorage([]))
    client = KubernetesClient(namespace="cds-test")
    with pytest.raises(KubernetesClientUnavailable, match="no storage class"):
        client.ensure_pvc("cds-shared-x")


def test_ensure_pvc_creates_rwx_with_default_class(monkeypatch):
    from app.services.k8s_client import KubernetesClient
    v1 = _FakeV1()
    default_sc = _FakeSC("local-path", annotations={"storageclass.kubernetes.io/is-default-class": "true"})
    _install_fakes(monkeypatch, v1=v1, storage=_FakeStorage([default_sc]))
    client = KubernetesClient(namespace="cds-test")
    result = client.ensure_pvc("cds-shared-x")
    assert result["status"] == "created"
    assert result["storage_class"] == "local-path"
    body = v1.pvc_created[0]
    assert body["spec"]["accessModes"] == ["ReadWriteMany"]
    assert body["spec"]["storageClassName"] == "local-path"
    assert body["spec"]["resources"]["requests"]["storage"] == "1Gi"


def test_ensure_pvc_prefers_configured_class(monkeypatch):
    from app.services.k8s_client import KubernetesClient
    v1 = _FakeV1()
    _install_fakes(monkeypatch, v1=v1, storage=_FakeStorage([]))
    client = KubernetesClient(namespace="cds-test")
    result = client.ensure_pvc("cds-shared-x", storage_class="nfs-rwx")
    assert result["status"] == "created"
    assert result["storage_class"] == "nfs-rwx"
    assert v1.pvc_created[0]["spec"]["storageClassName"] == "nfs-rwx"
