"""N7: official kubernetes python client backend for the sandbox adapter.

.. deprecated:: SS-10 CB-01
    Backend for the deprecated k8s level (see k8s_sandbox.py). Gated behind
    CDS_LEGACY_L2_ENABLED (default false); retained for the transition.

Replaces shelling out to ``kubectl`` with the typed client (CoreV1Api for
pods/PVCs, dynamic client for NetworkPolicy / ResourceQuota / CRDs such as
CiliumNetworkPolicy). Loads kubeconfig (or in-cluster config); raises
``KubernetesClientUnavailable`` when no cluster is reachable so the adapter
falls back to kubectl honestly.
"""
import logging
import os

from kubernetes import client as k8s_client
from kubernetes import config as k8s_config
from kubernetes import dynamic
from kubernetes.client import ApiException as _ApiException
from kubernetes.dynamic.exceptions import NotFoundError as _DynamicNotFound
from kubernetes.stream import stream as k8s_stream

logger = logging.getLogger(__name__)

# A128①：StorageClass 的 RWX 能力无法从 API 对象直接读出（spec 无 accessModes），
# 因此只信两个来源：operator 显式注解 cds.volume.rwx="true"，或 provisioner 命中
# 已知 RWX 白名单（宁缺毋滥：默认只收公认共享文件系统类 provisioner）。
# 白名单 env 可扩：CDS_K8S_RWX_PROVISIONERS（逗号分隔）。
_RWX_DECLARATION_ANNOTATION = "cds.volume.rwx"
_RWX_PROVISIONER_DEFAULTS = (
    "kubernetes.io/nfs",
    "nfs.csi.k8s.io",
    "cephfs.csi.ceph.com",
    "glusterfs",
    "glusterfs/kubernetes",
    "kubernetes.io/aws-efs",
)
_DEFAULT_CLASS_ANNOTATION = "storageclass.kubernetes.io/is-default-class"

# A128②：exec 可执行白名单。LEGACY 面姿态宁缺毋滥——默认只放现有调用方
# （k8s_sandbox.open_exec_stream/exec_script 实际只发 ["sh", "-s"]）用到的 "sh"。
# env CDS_K8S_EXEC_ALLOWLIST（逗号分隔）显式扩容；argv[0] 精确匹配，
# 不做 basename/路径旁路（"/bin/sh" 不在名单就是不在）。
_EXEC_ALLOWLIST_ENV = "CDS_K8S_EXEC_ALLOWLIST"
_EXEC_ALLOWLIST_DEFAULT = frozenset({"sh"})


def _exec_allowlist() -> frozenset[str]:
    raw = os.environ.get(_EXEC_ALLOWLIST_ENV, "").strip()
    if not raw:
        return _EXEC_ALLOWLIST_DEFAULT
    return frozenset(tok.strip() for tok in raw.split(",") if tok.strip())


def _rwx_provisioners() -> frozenset[str]:
    raw = os.environ.get("CDS_K8S_RWX_PROVISIONERS", "").strip()
    if not raw:
        return frozenset(_RWX_PROVISIONER_DEFAULTS)
    return frozenset(tok.strip() for tok in raw.split(",") if tok.strip())


def _storage_class_is_rwx(sc) -> bool:
    """A128①：RWX 能力判定——operator 显式注解优先，其次 provisioner 白名单。"""
    if (sc.metadata.annotations or {}).get(_RWX_DECLARATION_ANNOTATION) == "true":
        return True
    return getattr(sc, "provisioner", "") in _rwx_provisioners()


class KubernetesClientUnavailable(RuntimeError):
    """Raised when the kubernetes cluster/kubeconfig cannot be loaded."""


class KubernetesClient:
    """Thin, fail-closed wrapper over the official kubernetes python client."""

    def __init__(self, kubeconfig: str | None = None, namespace: str | None = None):
        try:
            if kubeconfig:
                k8s_config.load_kube_config(config_file=kubeconfig)
            elif os.environ.get("KUBERNETES_SERVICE_HOST"):
                k8s_config.load_incluster_config()
            else:
                k8s_config.load_kube_config()
        except Exception as e:
            raise KubernetesClientUnavailable(f"unable to load kubernetes config: {e}")
        self._api = k8s_client.ApiClient()
        self._v1 = k8s_client.CoreV1Api(self._api)
        self._net = k8s_client.NetworkingV1Api(self._api)
        self._storage = k8s_client.StorageV1Api(self._api)
        self._dyn = None
        self.namespace = namespace or "cds-sandbox"

    def _dynamic(self):
        if self._dyn is None:
            self._dyn = dynamic.DynamicClient(self._api)
        return self._dyn

    def ensure_namespace(self) -> bool:
        """Create the namespace if missing. Returns True when created."""
        try:
            self._v1.read_namespace(self.namespace)
            return False
        except _ApiException as e:
            if e.status != 404:
                raise
        body = {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": self.namespace}}
        self._v1.create_namespace(body)
        logger.info("[k8s-client] Created namespace %s", self.namespace)
        return True

    def create_pod(self, manifest: dict):
        return self._v1.create_namespaced_pod(self.namespace, manifest)

    def get_pod(self, name: str):
        return self._v1.read_namespaced_pod(name, self.namespace)

    def delete_pod(self, name: str, grace_period_seconds: int = 5) -> None:
        self._v1.delete_namespaced_pod(
            name, self.namespace,
            grace_period_seconds=grace_period_seconds,
            propagation_policy="Background",
        )

    def list_pods(self, label_selector: str | None = None) -> list:
        return self._v1.list_namespaced_pod(self.namespace, label_selector=label_selector).items

    def list_pvcs(self) -> list:
        return self._v1.list_namespaced_persistent_volume_claim(self.namespace).items

    def pod_logs(self, name: str) -> str:
        return self._v1.read_namespaced_pod_log(name, self.namespace)

    def exec_stream(self, name: str, command: list[str]):
        """Open a pod exec WebSocket for streaming (stdin/stdout/stderr).

        A128②边界强制（LEGACY 面，SANDBOX_LEGACY_ENABLED 后才可达）：
        command 必须是非空 list[str]，argv[0] 必须在可执行白名单内——
        默认仅 "sh"（现有调用方唯一实际用法 ["sh", "-s"]），env
        CDS_K8S_EXEC_ALLOWLIST 显式扩容。宁可拒绝合法新用法，不静默放行
        任意可执行文件。
        """
        if isinstance(command, str) or not isinstance(command, list) or not command:
            raise ValueError(
                "exec_stream requires a non-empty list[str] command, got "
                f"{type(command).__name__} of length {len(command) if isinstance(command, (list, str)) else '?'} (A128)"
            )
        if not all(isinstance(a, str) and a for a in command):
            raise ValueError("exec_stream command elements must be non-empty str (A128)")
        allow = _exec_allowlist()
        if command[0] not in allow:
            raise PermissionError(
                f"exec rejected: {command[0]!r} is not in the exec allowlist "
                f"{sorted(allow)} (extend via env {_EXEC_ALLOWLIST_ENV}; A128 LEGACY fail-closed)"
            )
        return k8s_stream(
            self._v1.connect_get_namespaced_pod_exec,
            name,
            self.namespace,
            command=command,
            container="sandbox",
            stdin=True,
            stdout=True,
            stderr=True,
            tty=False,
            _preload_content=False,
        )

    def apply_manifest(self, manifest: dict):
        """Create-or-replace a namespaced resource (kubectl apply semantics).

        Uses the dynamic client so CRDs (e.g. CiliumNetworkPolicy) and typed
        resources (Pod/NetworkPolicy/ResourceQuota) are handled uniformly.
        """
        resource = self._dynamic().resources.get(
            api_version=manifest.get("apiVersion", "v1"),
            kind=manifest.get("kind"),
        )
        namespace = manifest.get("metadata", {}).get("namespace") or self.namespace
        name = manifest["metadata"]["name"]
        try:
            resource.get(name=name, namespace=namespace)
        except _DynamicNotFound:
            return resource.create(body=manifest, namespace=namespace)
        return resource.patch(
            body=manifest, namespace=namespace,
            content_type="application/merge-patch+json",
        )

    def delete_resource(self, kind: str, name: str, api_version: str = "v1") -> None:
        resource = self._dynamic().resources.get(api_version=api_version, kind=kind)
        try:
            resource.delete(name=name, namespace=self.namespace)
        except _DynamicNotFound:
            pass

    def ensure_pvc(self, name: str, size: str = "1Gi", storage_class: str | None = None) -> dict:
        """Create a ReadWriteMany PVC if missing. Returns {status, storage_class}.

        Fails closed with KubernetesClientUnavailable when no storage class
        (explicit or cluster default) is available — shared volumes must be
        writable from every sandbox pod on any node.
        """
        try:
            self._v1.read_namespaced_persistent_volume_claim(name, self.namespace)
            return {"status": "exists", "storage_class": storage_class}
        except _ApiException as e:
            if e.status != 404:
                raise

        sc = storage_class or self._default_storage_class()
        if not sc:
            raise KubernetesClientUnavailable(
                "no storage class available for shared volumes (set "
                "SANDBOX_K8S_STORAGE_CLASS to a ReadWriteMany-capable class)"
            )
        body = {
            "apiVersion": "v1",
            "kind": "PersistentVolumeClaim",
            "metadata": {"name": name, "namespace": self.namespace},
            "spec": {
                "accessModes": ["ReadWriteMany"],
                "resources": {"requests": {"storage": size}},
                "storageClassName": sc,
            },
        }
        self._v1.create_namespaced_persistent_volume_claim(self.namespace, body)
        return {"status": "created", "storage_class": sc}

    def _default_storage_class(self) -> str:
        """Discover the shared-volume storage class — double condition, fail-closed.

        A128①: 只接受同时满足两个条件的 class——①带 k8s default 注解
        （storageclass.kubernetes.io/is-default-class="true"）②具 RWX 能力
        （operator 注解 cds.volume.rwx="true" 或 provisioner 命中 RWX 白名单）。
        旧代码在无 default 注解时猜 classes[0] 并照常写 ReadWriteMany PVC——
        若该类只支持 RWO，PVC 永久 Pending，恰好绕过本模块自述的 fail-closed。
        两条件不满足一律 raise，错误文案点名两个条件。
        """
        try:
            classes = list(self._storage.list_storage_class().items)
        except Exception as e:
            raise KubernetesClientUnavailable(
                "no storage class available for shared volumes: discovery failed "
                f"({e}); set SANDBOX_K8S_STORAGE_CLASS to a ReadWriteMany-capable class (A128)"
            )
        defaults = [
            sc for sc in classes
            if (sc.metadata.annotations or {}).get(_DEFAULT_CLASS_ANNOTATION) == "true"
        ]
        if not defaults:
            names = [sc.metadata.name for sc in classes]
            raise KubernetesClientUnavailable(
                f"no storage class available for shared volumes: none of {names} carries the default annotation "
                f"({_DEFAULT_CLASS_ANNOTATION}=\"true\") AND RWX capability "
                f"(annotation {_RWX_DECLARATION_ANNOTATION}=\"true\" or provisioner in "
                f"CDS_K8S_RWX_PROVISIONERS); refusing to guess classes[0] — an RWO-only guess "
                f"yields a permanently Pending ReadWriteMany PVC (A128). Set "
                f"SANDBOX_K8S_STORAGE_CLASS explicitly"
            )
        rwx = [sc for sc in defaults if _storage_class_is_rwx(sc)]
        if not rwx:
            raise KubernetesClientUnavailable(
                f"no storage class available for shared volumes: default class(es) "
                f"{[sc.metadata.name for sc in defaults]} are not ReadWriteMany-capable "
                f"(need annotation {_RWX_DECLARATION_ANNOTATION}=\"true\" or provisioner in "
                f"CDS_K8S_RWX_PROVISIONERS) (A128). Set SANDBOX_K8S_STORAGE_CLASS to a "
                f"ReadWriteMany-capable class"
            )
        return rwx[0].metadata.name
