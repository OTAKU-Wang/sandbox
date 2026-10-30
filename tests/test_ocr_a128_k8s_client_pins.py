"""A128 — k8s_client.py LEGACY 面缺陷钉（SANDBOX_LEGACY_ENABLED 后才可达；测试打函数级，不接线路径）。

修复组：
  K1  _default_storage_class 无 default 注解时猜 classes[0] → 写 RWX PVC 永久
      Pending，恰好绕过自述 fail-closed。新行为：只接受 default 注解 + RWX 能力
      双条件，错误文案必须点名两个条件。
  K2  default 注解有但类不具 RWX 能力（如 local-path，真实 RWO）→ 同样拒。
      正面：default + RWX 能力（provisioner 白名单或 operator 注解 cds.volume.rwx）
      才放行。
  K3  RWX provisioner 白名单 env 可扩（CDS_K8S_RWX_PROVISIONERS，csv）。
  K4  exec_stream 边界：强制 list[str]（str/空表/非 str 元素/空元素拒）+
      argv[0] 可执行白名单（默认仅现有调用方实际用的 "sh"，宁缺毋滥），
      env CDS_K8S_EXEC_ALLOWLIST 配置化；精确匹配，不接受 /bin/sh 路径旁路。
回归守卫（天然绿）：显式 storage_class 参数 = operator 显式覆盖，不受 K1/K2 影响。
"""
from types import SimpleNamespace

import pytest
from kubernetes.client import ApiException

from app.services.k8s_client import KubernetesClient, KubernetesClientUnavailable


def _fake_api_error(status: int) -> ApiException:
    exc = ApiException(http_resp=None)
    exc.status = status
    return exc


class _FakeV1:
    def __init__(self):
        self.pvc_created = []

    def read_namespaced_persistent_volume_claim(self, name, ns):
        raise _fake_api_error(404)

    def create_namespaced_persistent_volume_claim(self, ns, body):
        self.pvc_created.append(body)
        return body

    def connect_get_namespaced_pod_exec(self, *a, **k):  # pragma: no cover - sentinel
        return "WS-SENTINEL"


class _FakeSC:
    def __init__(self, name, annotations=None, provisioner=""):
        self.metadata = type("M", (), {"name": name, "annotations": annotations or {}})()
        self.provisioner = provisioner


class _FakeStorage:
    def __init__(self, classes):
        self._classes = classes

    def list_storage_class(self):
        return type("R", (), {"items": self._classes})()


_DEFAULT_ANNOT = {"storageclass.kubernetes.io/is-default-class": "true"}


def _install_fakes(monkeypatch, v1=None, storage=None, stream_result="WS"):
    import app.services.k8s_client as mod

    monkeypatch.setattr(mod.k8s_config, "load_kube_config", lambda *a, **k: None)
    monkeypatch.setattr(mod.k8s_config, "load_incluster_config", lambda *a, **k: None)
    monkeypatch.setattr(mod.k8s_client, "ApiClient", lambda: SimpleNamespace(configuration=SimpleNamespace(host="")))
    monkeypatch.setattr(mod.k8s_client, "CoreV1Api", lambda api: v1 or _FakeV1())
    monkeypatch.setattr(mod.k8s_client, "NetworkingV1Api", lambda api: None)
    monkeypatch.setattr(mod.k8s_client, "StorageV1Api", lambda api: storage or _FakeStorage([]))
    calls = []

    def _fake_stream(fn, *a, **k):
        calls.append((a, k))
        return stream_result

    monkeypatch.setattr(mod, "k8s_stream", _fake_stream)
    return calls


def _client(monkeypatch, storage=None, v1=None):
    _install_fakes(monkeypatch, v1=v1, storage=storage)
    return KubernetesClient(namespace="cds-test")


class TestK1K2StorageClassDoubleCondition:
    def test_no_guess_of_first_class_without_default_annotation(self, monkeypatch):
        # classes[0] 无 default 注解——旧代码直接猜它并写 RWX PVC（永久 Pending）。
        # 新行为必须拒，且错误文案点名两个条件（default 注解 + RWX 能力）。
        sc = _FakeSC("fast-rwo", provisioner="ebs.csi.aws.com")
        client = _client(monkeypatch, storage=_FakeStorage([sc]))
        with pytest.raises(KubernetesClientUnavailable) as ei:
            client.ensure_pvc("cds-shared-x")
        msg = str(ei.value)
        assert "default" in msg.lower()
        assert "ReadWriteMany" in msg or "RWX" in msg

    def test_default_annotation_without_rwx_capability_rejected(self, monkeypatch):
        # local-path：default=true 但真实集群 RWO——正是永久 Pending 陷阱本体。
        sc = _FakeSC("local-path", annotations=dict(_DEFAULT_ANNOT),
                     provisioner="rancher.io/local-path")
        client = _client(monkeypatch, storage=_FakeStorage([sc]))
        with pytest.raises(KubernetesClientUnavailable) as ei:
            client.ensure_pvc("cds-shared-x")
        msg = str(ei.value)
        assert "local-path" in msg
        assert "ReadWriteMany" in msg or "RWX" in msg

    def test_default_plus_rwx_provisioner_accepted(self, monkeypatch):
        v1 = _FakeV1()
        sc = _FakeSC("nfs-default", annotations=dict(_DEFAULT_ANNOT),
                     provisioner="nfs.csi.k8s.io")
        client = _client(monkeypatch, storage=_FakeStorage([sc]), v1=v1)
        result = client.ensure_pvc("cds-shared-x")
        assert result["status"] == "created"
        assert result["storage_class"] == "nfs-default"

    def test_operator_rwx_declaration_annotation_accepted(self, monkeypatch):
        v1 = _FakeV1()
        annot = dict(_DEFAULT_ANNOT)
        annot["cds.volume.rwx"] = "true"
        sc = _FakeSC("vendor-sc", annotations=annot, provisioner="vendor.csi.example")
        client = _client(monkeypatch, storage=_FakeStorage([sc]), v1=v1)
        assert client.ensure_pvc("cds-shared-x")["storage_class"] == "vendor-sc"

    def test_rwx_provisioner_allowlist_env_extendable(self, monkeypatch):
        monkeypatch.setenv("CDS_K8S_RWX_PROVISIONERS", "vendor.csi.example,other.io/nfsx")
        v1 = _FakeV1()
        sc = _FakeSC("vendor-sc", annotations=dict(_DEFAULT_ANNOT),
                     provisioner="vendor.csi.example")
        client = _client(monkeypatch, storage=_FakeStorage([sc]), v1=v1)
        assert client.ensure_pvc("cds-shared-x")["storage_class"] == "vendor-sc"

    def test_explicit_storage_class_bypasses_discovery(self, monkeypatch):
        # 回归守卫：operator 显式指定 = 显式决定，不做能力猜谜（天然绿）。
        v1 = _FakeV1()
        client = _client(monkeypatch, storage=_FakeStorage([]), v1=v1)
        result = client.ensure_pvc("cds-shared-x", storage_class="nfs-rwx")
        assert result["storage_class"] == "nfs-rwx"


class TestK4ExecStreamBoundary:
    def test_str_command_rejected(self, monkeypatch):
        client = _client(monkeypatch)
        with pytest.raises((TypeError, ValueError)):
            client.exec_stream("pod-1", "rm -rf /")

    def test_non_list_str_elements_rejected(self, monkeypatch):
        client = _client(monkeypatch)
        with pytest.raises((TypeError, ValueError)):
            client.exec_stream("pod-1", [])
        with pytest.raises((TypeError, ValueError)):
            client.exec_stream("pod-1", ["sh", 1])
        with pytest.raises((TypeError, ValueError)):
            client.exec_stream("pod-1", ["sh", ""])

    def test_default_allowlist_only_sh(self, monkeypatch):
        calls = _install_fakes(monkeypatch)
        client = KubernetesClient(namespace="cds-test")
        # 现有调用方唯一实际形态
        assert client.exec_stream("pod-1", ["sh", "-s"]) == "WS"
        assert len(calls) == 1
        with pytest.raises(PermissionError):
            client.exec_stream("pod-1", ["bash", "-c", "echo hi"])
        with pytest.raises(PermissionError):
            client.exec_stream("pod-1", ["python3", "-c", "import os"])

    def test_allowlist_env_extendable(self, monkeypatch):
        monkeypatch.setenv("CDS_K8S_EXEC_ALLOWLIST", "sh,bash")
        _install_fakes(monkeypatch)
        client = KubernetesClient(namespace="cds-test")
        assert client.exec_stream("pod-1", ["bash", "-s"]) == "WS"

    def test_no_path_bypass_of_allowlist(self, monkeypatch):
        # 宁缺毋滥：精确匹配 argv[0]，/bin/sh 不是白名单成员。
        _install_fakes(monkeypatch)
        client = KubernetesClient(namespace="cds-test")
        with pytest.raises(PermissionError):
            client.exec_stream("pod-1", ["/bin/sh", "-s"])
