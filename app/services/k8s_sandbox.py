"""K8s Sandbox Adapter — manages sandbox sessions as Kubernetes pods.

Implements task #131: K8s/K3s distributed scaling + tenant isolation.
Each sandbox session = one K8s pod with:
- Resource limits (CPU/memory/disk)
- NetworkPolicy for tenant isolation
- ResourceQuota for per-tenant limits
- Lifecycle management (create/delete/status)

Architecture:
  CDS API → K8sSandboxAdapter → K8s API → Sandbox Pod
                                    ↓
                              NetworkPolicy (tenant isolation)
                              ResourceQuota (per-tenant limits)
"""
import asyncio
import json
import logging
import os
import subprocess
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class SandboxPodPhase(str, Enum):
    PENDING = "Pending"
    RUNNING = "Running"
    SUCCEEDED = "Succeeded"
    FAILED = "Failed"
    UNKNOWN = "Unknown"


@dataclass
class SandboxPodSpec:
    """Specification for a sandbox K8s pod."""
    session_id: str
    user_id: str
    sandbox_level: str  # L0, L1, L2, L3
    image: str = "python:3.12-slim"
    cpu_request: str = "500m"
    cpu_limit: str = "1000m"
    memory_request: str = "256Mi"
    memory_limit: str = "512Mi"
    disk_limit: str = "1Gi"
    timeout_seconds: int = 3600
    env_vars: dict[str, str] = field(default_factory=dict)
    volume_mounts: list[dict] = field(default_factory=list)
    network_policy_mode: str = "deny_all"  # deny_all or allowlist
    allowed_domains: list[str] = field(default_factory=list)
    allowed_ips: list[str] = field(default_factory=list)


# Resource limits per sandbox level
LEVEL_RESOURCES = {
    "L0": {"cpu_req": "250m", "cpu_lim": "1000m", "mem_req": "128Mi", "mem_lim": "256Mi", "disk": "512Mi"},
    "L1": {"cpu_req": "1000m", "cpu_lim": "4000m", "mem_req": "2Gi", "mem_lim": "4Gi", "disk": "10Gi"},
    "L2": {"cpu_req": "500m", "cpu_lim": "2000m", "mem_req": "512Mi", "mem_lim": "1Gi", "disk": "5Gi"},
    "L3": {"cpu_req": "250m", "cpu_lim": "1000m", "mem_req": "256Mi", "mem_lim": "512Mi", "disk": "1Gi"},
}


def _kubectl(*args: str, capture_output: bool = True, timeout: int = 30) -> subprocess.CompletedProcess:
    """Execute kubectl command."""
    cmd = ["kubectl"] + list(args)
    return subprocess.run(cmd, capture_output=capture_output, text=True, timeout=timeout)


class K8sSandboxAdapter:
    """Manages sandbox sessions as Kubernetes pods."""

    def __init__(self, namespace: str = "cds-sandbox", kubeconfig: str | None = None):
        self.namespace = namespace
        self._kubeconfig = kubeconfig
        self._ensure_namespace()

    def _ensure_namespace(self):
        """Ensure the sandbox namespace exists."""
        try:
            result = _kubectl("get", "namespace", self.namespace, "--ignore-not-found")
            if self.namespace not in (result.stdout or ""):
                _kubectl("create", "namespace", self.namespace)
                logger.info(f"[k8s-sandbox] Created namespace %s", self.namespace)
        except FileNotFoundError:
            logger.warning("[k8s-sandbox] kubectl not found — K8s adapter unavailable")
        except Exception as e:
            logger.warning("[k8s-sandbox] Failed to ensure namespace: %s", e)

    def provision(self, spec: SandboxPodSpec) -> dict:
        """Create a sandbox pod for a session.

        Returns pod details (name, status, IP).
        """
        pod_name = f"sandbox-{spec.session_id[:16]}"
        resources = LEVEL_RESOURCES.get(spec.sandbox_level, LEVEL_RESOURCES["L3"])

        # Build pod manifest
        pod_manifest = self._build_pod_manifest(spec, pod_name, resources)

        # Create pod
        manifest_json = json.dumps(pod_manifest)
        result = _kubectl("apply", "-f", "-", "-n", self.namespace,
                         input=manifest_json, capture_output=True)

        if result.returncode != 0:
            logger.error(f"[k8s-sandbox] Failed to create pod {pod_name}: {result.stderr}")
            return {"error": result.stderr, "status": "failed"}

        # Create network policy
        if spec.network_policy_mode == "deny_all":
            self._create_deny_all_policy(pod_name, spec.user_id)
        elif spec.network_policy_mode == "allowlist":
            self._create_allowlist_policy(pod_name, spec.user_id, spec.allowed_ips, spec.allowed_domains)

        # Ensure tenant resource quota
        self._ensure_tenant_quota(spec.user_id)

        logger.info(f"[k8s-sandbox] Created pod {pod_name} for session {spec.session_id}")
        return {
            "pod_name": pod_name,
            "namespace": self.namespace,
            "status": "Pending",
            "container_id": f"k8s-{spec.session_id}",
        }

    def terminate(self, pod_name: str) -> bool:
        """Delete a sandbox pod and its associated resources."""
        try:
            # Delete pod
            _kubectl("delete", "pod", pod_name, "-n", self.namespace, "--grace-period=5")

            # Delete associated network policy
            _kubectl("delete", "networkpolicy", f"{pod_name}-policy", "-n", self.namespace,
                    "--ignore-not-found")

            logger.info(f"[k8s-sandbox] Terminated pod {pod_name}")
            return True
        except Exception as e:
            logger.error(f"[k8s-sandbox] Failed to terminate pod {pod_name}: {e}")
            return False

    def get_status(self, pod_name: str) -> dict:
        """Get pod status."""
        result = _kubectl("get", "pod", pod_name, "-n", self.namespace, "-o", "json")
        if result.returncode != 0:
            return {"phase": "Unknown", "error": result.stderr}

        pod = json.loads(result.stdout)
        phase = pod.get("status", {}).get("phase", "Unknown")
        pod_ip = pod.get("status", {}).get("podIP", "")

        container_statuses = pod.get("status", {}).get("containerStatuses", [])
        ready = all(cs.get("ready", False) for cs in container_statuses) if container_statuses else False

        return {
            "phase": phase,
            "pod_ip": pod_ip,
            "ready": ready,
            "container_statuses": container_statuses,
        }

    def list_sandboxes(self, user_id: str | None = None) -> list[dict]:
        """List sandbox pods, optionally filtered by user."""
        label_selector = "app=cds-sandbox"
        if user_id:
            label_selector += f",user-id={user_id}"

        result = _kubectl("get", "pods", "-n", self.namespace,
                         "-l", label_selector, "-o", "json")
        if result.returncode != 0:
            return []

        pods = json.loads(result.stdout).get("items", [])
        return [
            {
                "pod_name": pod["metadata"]["name"],
                "phase": pod.get("status", {}).get("phase", "Unknown"),
                "user_id": pod["metadata"].get("labels", {}).get("user-id", ""),
                "session_id": pod["metadata"].get("labels", {}).get("session-id", ""),
            }
            for pod in pods
        ]

    def _build_pod_manifest(self, spec: SandboxPodSpec, pod_name: str, resources: dict) -> dict:
        """Build K8s Pod manifest for a sandbox session."""
        env = [
            {"name": "CDS_SESSION_ID", "value": spec.session_id},
            {"name": "CDS_SANDBOX_LEVEL", "value": spec.sandbox_level},
            {"name": "CDS_USER_ID", "value": spec.user_id},
        ]
        for k, v in spec.env_vars.items():
            env.append({"name": k, "value": v})

        return {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {
                "name": pod_name,
                "namespace": self.namespace,
                "labels": {
                    "app": "cds-sandbox",
                    "user-id": spec.user_id,
                    "session-id": spec.session_id,
                    "sandbox-level": spec.sandbox_level,
                },
                "annotations": {
                    "cds/timeout": str(spec.timeout_seconds),
                    "cds/network-policy": spec.network_policy_mode,
                },
            },
            "spec": {
                "restartPolicy": "Never",
                "activeDeadlineSeconds": spec.timeout_seconds,
                "containers": [
                    {
                        "name": "sandbox",
                        "image": spec.image,
                        "command": ["sleep", str(spec.timeout_seconds)],
                        "env": env,
                        "resources": {
                            "requests": {
                                "cpu": resources["cpu_req"],
                                "memory": resources["mem_req"],
                            },
                            "limits": {
                                "cpu": resources["cpu_lim"],
                                "memory": resources["mem_lim"],
                            },
                        },
                        "volumeMounts": spec.volume_mounts,
                        "securityContext": {
                            "runAsNonRoot": True,
                            "runAsUser": 1000,
                            "readOnlyRootFilesystem": True,
                            "allowPrivilegeEscalation": False,
                            "capabilities": {"drop": ["ALL"]},
                        },
                    }
                ],
                "securityContext": {
                    "fsGroup": 1000,
                },
            },
        }

    def _create_deny_all_policy(self, pod_name: str, user_id: str):
        """Create a deny-all NetworkPolicy for a sandbox pod."""
        policy = {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": {
                "name": f"{pod_name}-policy",
                "namespace": self.namespace,
                "labels": {"user-id": user_id},
            },
            "spec": {
                "podSelector": {
                    "matchLabels": {"app": "cds-sandbox", "session-id": pod_name.replace("sandbox-", "")},
                },
                "policyTypes": ["Ingress", "Egress"],
                "ingress": [],  # Deny all ingress
                "egress": [
                    # Allow DNS only
                    {
                        "ports": [{"port": 53, "protocol": "UDP"}, {"port": 53, "protocol": "TCP"}],
                    }
                ],
            },
        }
        manifest_json = json.dumps(policy)
        _kubectl("apply", "-f", "-", "-n", self.namespace, input=manifest_json)

    def _create_allowlist_policy(self, pod_name: str, user_id: str,
                                 allowed_ips: list[str], allowed_domains: list[str]):
        """Create an allowlist NetworkPolicy for a sandbox pod."""
        egress_rules = [
            # Allow DNS
            {"ports": [{"port": 53, "protocol": "UDP"}, {"port": 53, "protocol": "TCP"}]},
        ]

        # Allow specified IPs
        for ip_cidr in allowed_ips:
            egress_rules.append({
                "to": [{"ipBlock": {"cidr": ip_cidr}}],
                "ports": [{"port": 443, "protocol": "TCP"}, {"port": 80, "protocol": "TCP"}],
            })

        # Note: Domain-based filtering requires DNS-aware proxy (not native K8s NetworkPolicy)
        # For domains, we rely on the DNS proxy inside the sandbox

        policy = {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": {
                "name": f"{pod_name}-policy",
                "namespace": self.namespace,
                "labels": {"user-id": user_id},
            },
            "spec": {
                "podSelector": {
                    "matchLabels": {"app": "cds-sandbox", "session-id": pod_name.replace("sandbox-", "")},
                },
                "policyTypes": ["Ingress", "Egress"],
                "ingress": [],
                "egress": egress_rules,
            },
        }
        manifest_json = json.dumps(policy)
        _kubectl("apply", "-f", "-", "-n", self.namespace, input=manifest_json)

    def _ensure_tenant_quota(self, user_id: str):
        """Ensure a ResourceQuota exists for a tenant."""
        quota_name = f"quota-{user_id[:16]}"

        # Check if quota exists
        result = _kubectl("get", "resourcequota", quota_name, "-n", self.namespace, "--ignore-not-found")
        if quota_name in (result.stdout or ""):
            return

        quota = {
            "apiVersion": "v1",
            "kind": "ResourceQuota",
            "metadata": {
                "name": quota_name,
                "namespace": self.namespace,
                "labels": {"user-id": user_id},
            },
            "spec": {
                "hard": {
                    "requests.cpu": "8",
                    "requests.memory": "8Gi",
                    "limits.cpu": "16",
                    "limits.memory": "16Gi",
                    "pods": "10",
                },
                "scopeSelector": {
                    "matchExpressions": [
                        {
                            "scopeName": "LabelSelector",
                            "operator": "In",
                            "values": [f"user-id={user_id}"],
                        }
                    ],
                },
            },
        }
        manifest_json = json.dumps(quota)
        _kubectl("apply", "-f", "-", "-n", self.namespace, input=manifest_json)
        logger.info(f"[k8s-sandbox] Created resource quota {quota_name} for user {user_id}")


# Global singleton (lazy-initialized)
_k8s_adapter: K8sSandboxAdapter | None = None


async def get_k8s_adapter() -> K8sSandboxAdapter:
    """Get or create the K8s sandbox adapter singleton."""
    global _k8s_adapter
    if _k8s_adapter is None:
        _k8s_adapter = K8sSandboxAdapter()
    return _k8s_adapter
