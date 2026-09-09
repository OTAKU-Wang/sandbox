"""N7: official kubernetes python client backend for the sandbox adapter.

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
        """Open a pod exec WebSocket for streaming (stdin/stdout/stderr)."""
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

    def _default_storage_class(self) -> str | None:
        try:
            classes = self._storage.list_storage_class().items
        except Exception as e:
            logger.warning("[k8s-client] storage class discovery failed: %s", e)
            return None
        for sc in classes:
            if (sc.metadata.annotations or {}).get("storageclass.kubernetes.io/is-default-class") == "true":
                return sc.metadata.name
        return classes[0].metadata.name if classes else None
