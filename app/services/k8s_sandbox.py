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
import ipaddress
import json
import logging
import os
import subprocess
import time
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
    image_pull_policy: str = "IfNotPresent"
    runtime_class_name: str = ""


# Resource limits per sandbox level
LEVEL_RESOURCES = {
    "L0": {"cpu_req": "250m", "cpu_lim": "1000m", "mem_req": "128Mi", "mem_lim": "256Mi", "disk": "512Mi"},
    "L1": {"cpu_req": "1000m", "cpu_lim": "4000m", "mem_req": "2Gi", "mem_lim": "4Gi", "disk": "10Gi"},
    "L2": {"cpu_req": "500m", "cpu_lim": "2000m", "mem_req": "512Mi", "mem_lim": "1Gi", "disk": "5Gi"},
    "L3": {"cpu_req": "250m", "cpu_lim": "1000m", "mem_req": "256Mi", "mem_lim": "512Mi", "disk": "1Gi"},
    "k8s": {"cpu_req": "250m", "cpu_lim": "1000m", "mem_req": "256Mi", "mem_lim": "512Mi", "disk": "1Gi"},
}


def _kubectl(
    *args: str,
    capture_output: bool = True,
    timeout: int = 30,
    input: str | None = None,
    kubeconfig: str | None = None,
) -> subprocess.CompletedProcess:
    """Execute kubectl command."""
    cmd = ["kubectl"]
    if kubeconfig:
        cmd += ["--kubeconfig", kubeconfig]
    cmd += list(args)
    return subprocess.run(cmd, capture_output=capture_output, text=True, timeout=timeout, input=input)


def _env_var_name(name: str) -> bool:
    return bool(name) and all(ch.isalnum() or ch == "_" for ch in name) and not name[0].isdigit()


def _valid_domain_pattern(value: str) -> bool:
    if not value:
        return False
    domain = value[2:] if value.startswith("*.") else value
    if not domain or len(domain) > 253 or ".." in domain:
        return False
    labels = domain.rstrip(".").split(".")
    if len(labels) < 2:
        return False
    for label in labels:
        if not label or len(label) > 63:
            return False
        if label.startswith("-") or label.endswith("-"):
            return False
        if not all(ch.isalnum() or ch == "-" for ch in label):
            return False
    return True


class K8sSandboxAdapter:
    """Manages sandbox sessions as Kubernetes pods."""

    def __init__(
        self,
        namespace: str | None = None,
        kubeconfig: str | None = None,
        ready_timeout_seconds: int | None = None,
        poll_interval_seconds: float | None = None,
    ):
        from app.core.config import get_settings

        settings = get_settings()
        self.namespace = namespace or settings.SANDBOX_K8S_NAMESPACE
        self.default_image = settings.SANDBOX_K8S_IMAGE
        self.default_image_pull_policy = settings.SANDBOX_K8S_IMAGE_PULL_POLICY
        self.default_runtime_class = settings.SANDBOX_K8S_RUNTIME_CLASS
        self.fqdn_policy_provider = settings.SANDBOX_K8S_FQDN_POLICY_PROVIDER.lower().strip()
        self.ready_timeout_seconds = (
            settings.SANDBOX_K8S_READY_TIMEOUT_SECONDS
            if ready_timeout_seconds is None
            else ready_timeout_seconds
        )
        self.poll_interval_seconds = (
            settings.SANDBOX_K8S_POLL_INTERVAL_SECONDS
            if poll_interval_seconds is None
            else poll_interval_seconds
        )
        self._kubeconfig = kubeconfig or settings.SANDBOX_K8S_KUBECONFIG or None
        self._ensure_namespace()

    def _kubectl(self, *args: str, **kwargs) -> subprocess.CompletedProcess:
        return _kubectl(*args, kubeconfig=self._kubeconfig, **kwargs)

    def _ensure_namespace(self):
        """Ensure the sandbox namespace exists."""
        try:
            result = self._kubectl("get", "namespace", self.namespace, "--ignore-not-found")
            if self.namespace not in (result.stdout or ""):
                self._kubectl("create", "namespace", self.namespace)
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
        if spec.image == "python:3.12-slim":
            spec.image = self.default_image
        if spec.image_pull_policy == "IfNotPresent":
            spec.image_pull_policy = self.default_image_pull_policy
        if not spec.runtime_class_name:
            spec.runtime_class_name = self.default_runtime_class
        invalid_env = [name for name in spec.env_vars if not _env_var_name(name)]
        if invalid_env:
            return {
                "error": f"Invalid environment variable name(s): {', '.join(sorted(invalid_env))}",
                "status": "failed",
            }
        policy_error = self._validate_network_policy_spec(spec)
        if policy_error:
            return {"error": policy_error, "status": "failed"}

        pod_created = False
        try:
            # Build pod manifest
            pod_manifest = self._build_pod_manifest(spec, pod_name, resources)

            # Create pod
            manifest_json = json.dumps(pod_manifest)
            result = self._kubectl("apply", "-f", "-", "-n", self.namespace,
                                  input=manifest_json, capture_output=True)

            if result.returncode != 0:
                logger.error(f"[k8s-sandbox] Failed to create pod {pod_name}: {result.stderr}")
                return {"error": result.stderr, "status": "failed"}
            pod_created = True

            # Create network policy
            policy_error = None
            if spec.network_policy_mode == "deny_all":
                policy_error = self._create_deny_all_policy(pod_name, spec.user_id, spec.session_id)
            elif spec.network_policy_mode == "allowlist":
                policy_error = self._create_allowlist_policy(
                    pod_name, spec.user_id, spec.session_id, spec.allowed_ips, spec.allowed_domains,
                )
            else:
                policy_error = f"Unsupported network policy mode: {spec.network_policy_mode}"
            if policy_error:
                self.terminate(pod_name)
                return {"error": policy_error, "status": "failed"}

            # Ensure tenant resource quota
            quota_error = self._ensure_tenant_quota(spec.user_id)
            if quota_error:
                self.terminate(pod_name)
                return {"error": quota_error, "status": "failed"}

            ready, pod_status = self.wait_until_ready(pod_name, timeout_seconds=self.ready_timeout_seconds)
        except FileNotFoundError:
            return {"error": "kubectl not found", "status": "failed"}
        except subprocess.TimeoutExpired as e:
            if pod_created:
                self.terminate(pod_name)
            return {"error": f"kubectl timed out: {e}", "status": "failed"}
        except Exception as e:
            if pod_created:
                self.terminate(pod_name)
            logger.error("[k8s-sandbox] Failed to provision pod %s: %s", pod_name, e)
            return {"error": str(e), "status": "failed"}

        if not ready:
            reason = pod_status.get("reason") or pod_status.get("error") or pod_status.get("message") or "not ready"
            self.terminate(pod_name)
            logger.error("[k8s-sandbox] Pod %s failed readiness: %s", pod_name, reason)
            return {
                "pod_name": pod_name,
                "namespace": self.namespace,
                "status": "failed",
                "container_id": f"k8s-{spec.session_id}",
                "error": f"K8s pod did not become ready: {reason}",
                "pod_status": pod_status,
            }

        logger.info(f"[k8s-sandbox] Created pod {pod_name} for session {spec.session_id}")
        return {
            "pod_name": pod_name,
            "namespace": self.namespace,
            "status": "running",
            "container_id": f"k8s-{spec.session_id}",
            "pod_ip": pod_status.get("pod_ip", ""),
        }

    def terminate(self, pod_name: str) -> bool:
        """Delete a sandbox pod and its associated resources."""
        try:
            # Delete pod
            pod_result = self._kubectl(
                "delete", "pod", pod_name, "-n", self.namespace,
                "--grace-period=5", "--ignore-not-found",
            )
            if pod_result.returncode != 0:
                logger.error("[k8s-sandbox] Failed to delete pod %s: %s", pod_name, pod_result.stderr)
                return False

            # Delete associated network policy
            policy_result = self._kubectl(
                "delete", "networkpolicy", f"{pod_name}-policy", "-n", self.namespace,
                "--ignore-not-found",
            )
            if policy_result.returncode != 0:
                logger.error(
                    "[k8s-sandbox] Failed to delete network policy for %s: %s",
                    pod_name,
                    policy_result.stderr,
                )
                return False

            if self.fqdn_policy_provider == "cilium":
                fqdn_policy_result = self._kubectl(
                    "delete", "ciliumnetworkpolicy", f"{pod_name}-fqdn-policy", "-n", self.namespace,
                    "--ignore-not-found",
                )
                if fqdn_policy_result.returncode != 0:
                    logger.error(
                        "[k8s-sandbox] Failed to delete FQDN network policy for %s: %s",
                        pod_name,
                        fqdn_policy_result.stderr,
                    )
                    return False

            logger.info(f"[k8s-sandbox] Terminated pod {pod_name}")
            return True
        except Exception as e:
            logger.error(f"[k8s-sandbox] Failed to terminate pod {pod_name}: {e}")
            return False

    def get_status(self, pod_name: str) -> dict:
        """Get pod status."""
        result = self._kubectl("get", "pod", pod_name, "-n", self.namespace, "-o", "json")
        if result.returncode != 0:
            return {"phase": "Unknown", "cds_status": "failed", "ready": False, "error": result.stderr}

        try:
            pod = json.loads(result.stdout)
        except json.JSONDecodeError as e:
            return {
                "phase": "Unknown",
                "cds_status": "failed",
                "ready": False,
                "error": f"Invalid kubectl JSON output: {e}",
            }
        phase = pod.get("status", {}).get("phase", "Unknown")
        pod_ip = pod.get("status", {}).get("podIP", "")

        container_statuses = pod.get("status", {}).get("containerStatuses", [])
        ready = all(cs.get("ready", False) for cs in container_statuses) if container_statuses else False
        reason = ""
        message = ""
        for cs in container_statuses:
            state = cs.get("state", {})
            waiting = state.get("waiting")
            terminated = state.get("terminated")
            if waiting:
                reason = waiting.get("reason", reason)
                message = waiting.get("message", message)
            if terminated:
                reason = terminated.get("reason", reason)
                message = terminated.get("message", message)

        if phase == SandboxPodPhase.RUNNING.value and ready:
            cds_status = "running"
        elif phase in {SandboxPodPhase.FAILED.value, SandboxPodPhase.UNKNOWN.value}:
            cds_status = "failed"
        elif phase == SandboxPodPhase.SUCCEEDED.value:
            cds_status = "completed"
        else:
            cds_status = "provisioning"

        return {
            "phase": phase,
            "cds_status": cds_status,
            "pod_ip": pod_ip,
            "ready": ready,
            "reason": reason,
            "message": message,
            "container_statuses": container_statuses,
        }

    def wait_until_ready(self, pod_name: str, timeout_seconds: int | None = None) -> tuple[bool, dict]:
        """Wait until a pod is running and all containers are ready."""
        deadline = time.monotonic() + max(timeout_seconds if timeout_seconds is not None else self.ready_timeout_seconds, 0)
        last_status: dict = {"phase": "Unknown", "cds_status": "failed", "ready": False}
        while True:
            last_status = self.get_status(pod_name)
            if last_status.get("cds_status") == "running":
                return True, last_status
            if last_status.get("cds_status") in {"failed", "completed"}:
                return False, last_status
            if time.monotonic() >= deadline:
                return False, last_status
            time.sleep(max(min(self.poll_interval_seconds, deadline - time.monotonic()), 0.0))

    def list_sandboxes(self, user_id: str | None = None) -> list[dict]:
        """List sandbox pods, optionally filtered by user."""
        label_selector = "app=cds-sandbox"
        if user_id:
            label_selector += f",user-id={user_id}"

        result = self._kubectl("get", "pods", "-n", self.namespace,
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
        image = self.default_image if spec.image == "python:3.12-slim" else spec.image
        image_pull_policy = (
            self.default_image_pull_policy
            if spec.image_pull_policy == "IfNotPresent"
            else spec.image_pull_policy
        )
        runtime_class_name = spec.runtime_class_name or self.default_runtime_class
        env_values = {
            "CDS_SESSION_ID": spec.session_id,
            "CDS_SANDBOX_LEVEL": spec.sandbox_level,
            "CDS_USER_ID": spec.user_id,
            "HOME": "/home/sandbox",
            "TMPDIR": "/tmp",
            "PYTHONPYCACHEPREFIX": "/tmp/pycache",
        }
        env_values.update({k: str(v) for k, v in spec.env_vars.items()})
        env = [{"name": k, "value": v} for k, v in env_values.items()]

        manifest = {
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
                "automountServiceAccountToken": False,
                "enableServiceLinks": False,
                "containers": [
                    {
                        "name": "sandbox",
                        "image": image,
                        "imagePullPolicy": image_pull_policy,
                        "command": ["sleep", str(spec.timeout_seconds)],
                        "workingDir": "/workspace",
                        "env": env,
                        "resources": {
                            "requests": {
                                "cpu": resources["cpu_req"],
                                "memory": resources["mem_req"],
                                "ephemeral-storage": resources["disk"],
                            },
                            "limits": {
                                "cpu": resources["cpu_lim"],
                                "memory": resources["mem_lim"],
                                "ephemeral-storage": resources["disk"],
                            },
                        },
                        "volumeMounts": [
                            {"name": "workspace", "mountPath": "/workspace"},
                            {"name": "tmp", "mountPath": "/tmp"},
                            {"name": "home", "mountPath": "/home/sandbox"},
                        ] + spec.volume_mounts,
                        "securityContext": {
                            "runAsNonRoot": True,
                            "runAsUser": 1000,
                            "runAsGroup": 1000,
                            "readOnlyRootFilesystem": True,
                            "allowPrivilegeEscalation": False,
                            "privileged": False,
                            "seccompProfile": {"type": "RuntimeDefault"},
                            "capabilities": {"drop": ["ALL"]},
                        },
                    }
                ],
                "securityContext": {
                    "fsGroup": 1000,
                },
                "volumes": [
                    {"name": "workspace", "emptyDir": {"medium": "Memory", "sizeLimit": resources["disk"]}},
                    {"name": "tmp", "emptyDir": {"medium": "Memory", "sizeLimit": "256Mi"}},
                    {"name": "home", "emptyDir": {"medium": "Memory", "sizeLimit": "64Mi"}},
                ],
            },
        }
        if runtime_class_name:
            manifest["spec"]["runtimeClassName"] = runtime_class_name
        return manifest

    def _create_deny_all_policy(self, pod_name: str, user_id: str, session_id: str) -> str | None:
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
                    "matchLabels": {"app": "cds-sandbox", "session-id": session_id},
                },
                "policyTypes": ["Ingress", "Egress"],
                "ingress": [],  # Deny all ingress
                "egress": [],
            },
        }
        manifest_json = json.dumps(policy)
        result = self._kubectl("apply", "-f", "-", "-n", self.namespace, input=manifest_json)
        if result.returncode != 0:
            return result.stderr or f"Failed to apply NetworkPolicy {pod_name}-policy"
        return None

    def _create_allowlist_policy(self, pod_name: str, user_id: str, session_id: str,
                                 allowed_ips: list[str], allowed_domains: list[str]) -> str | None:
        """Create an allowlist NetworkPolicy for a sandbox pod."""
        egress_rules = []

        # Allow specified IPs
        for ip_cidr in allowed_ips:
            egress_rules.append({
                "to": [{"ipBlock": {"cidr": ip_cidr}}],
                "ports": [{"port": 443, "protocol": "TCP"}, {"port": 80, "protocol": "TCP"}],
            })

        if allowed_domains and self.fqdn_policy_provider != "cilium":
            return "K8s domain allowlist requires CDS_SANDBOX_K8S_FQDN_POLICY_PROVIDER=cilium"

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
                    "matchLabels": {"app": "cds-sandbox", "session-id": session_id},
                },
                "policyTypes": ["Ingress", "Egress"],
                "ingress": [],
                "egress": egress_rules,
            },
        }
        manifest_json = json.dumps(policy)
        result = self._kubectl("apply", "-f", "-", "-n", self.namespace, input=manifest_json)
        if result.returncode != 0:
            return result.stderr or f"Failed to apply NetworkPolicy {pod_name}-policy"
        if allowed_domains:
            fqdn_error = self._create_cilium_fqdn_policy(pod_name, user_id, session_id, allowed_domains)
            if fqdn_error:
                return fqdn_error
        return None

    def _create_cilium_fqdn_policy(
        self,
        pod_name: str,
        user_id: str,
        session_id: str,
        allowed_domains: list[str],
    ) -> str | None:
        policy = {
            "apiVersion": "cilium.io/v2",
            "kind": "CiliumNetworkPolicy",
            "metadata": {
                "name": f"{pod_name}-fqdn-policy",
                "namespace": self.namespace,
                "labels": {"user-id": user_id},
            },
            "spec": {
                "endpointSelector": {
                    "matchLabels": {"app": "cds-sandbox", "session-id": session_id},
                },
                "egress": [
                    {
                        "toFQDNs": [
                            {"matchPattern": domain} if domain.startswith("*.") else {"matchName": domain}
                            for domain in allowed_domains
                        ],
                        "toPorts": [
                            {
                                "ports": [
                                    {"port": "443", "protocol": "TCP"},
                                    {"port": "80", "protocol": "TCP"},
                                ],
                            }
                        ],
                    }
                ],
            },
        }
        result = self._kubectl("apply", "-f", "-", "-n", self.namespace, input=json.dumps(policy))
        if result.returncode != 0:
            return result.stderr or f"Failed to apply FQDN policy {pod_name}-fqdn-policy"
        return None

    def _validate_network_policy_spec(self, spec: SandboxPodSpec) -> str | None:
        if spec.network_policy_mode not in {"deny_all", "allowlist"}:
            return f"Unsupported network policy mode: {spec.network_policy_mode}"
        if spec.network_policy_mode == "deny_all":
            return None
        if not spec.allowed_ips and not spec.allowed_domains:
            return "allowlist network policy requires at least one allowed IP/CIDR or domain"
        invalid_ips = []
        for value in spec.allowed_ips:
            try:
                ipaddress.ip_network(value, strict=False)
            except ValueError:
                invalid_ips.append(value)
        if invalid_ips:
            return f"Invalid allowed IP/CIDR value(s): {', '.join(sorted(invalid_ips))}"
        invalid_domains = [domain for domain in spec.allowed_domains if not _valid_domain_pattern(domain)]
        if invalid_domains:
            return f"Invalid allowed domain value(s): {', '.join(sorted(invalid_domains))}"
        if spec.allowed_domains and self.fqdn_policy_provider != "cilium":
            return "K8s domain allowlist requires CDS_SANDBOX_K8S_FQDN_POLICY_PROVIDER=cilium"
        return None

    def _ensure_tenant_quota(self, user_id: str) -> str | None:
        """Ensure a namespace-level ResourceQuota exists for sandbox pods.

        Kubernetes ResourceQuota cannot select arbitrary tenant labels. Per-user
        quota is enforced by the CDS control plane before provisioning.
        """
        quota_name = "quota-cds-sandbox"

        # Check if quota exists
        result = self._kubectl("get", "resourcequota", quota_name, "-n", self.namespace, "--ignore-not-found")
        if result.returncode != 0:
            return result.stderr or f"Failed to read ResourceQuota {quota_name}"
        if quota_name in (result.stdout or ""):
            return None

        quota = {
            "apiVersion": "v1",
            "kind": "ResourceQuota",
            "metadata": {
                "name": quota_name,
                "namespace": self.namespace,
                "labels": {"app": "cds-sandbox"},
            },
            "spec": {
                "hard": {
                    "requests.cpu": "8",
                    "requests.memory": "8Gi",
                    "limits.cpu": "16",
                    "limits.memory": "16Gi",
                    "requests.ephemeral-storage": "20Gi",
                    "limits.ephemeral-storage": "20Gi",
                    "pods": "10",
                },
            },
        }
        manifest_json = json.dumps(quota)
        result = self._kubectl("apply", "-f", "-", "-n", self.namespace, input=manifest_json)
        if result.returncode != 0:
            return result.stderr or f"Failed to apply ResourceQuota {quota_name}"
        logger.info(f"[k8s-sandbox] Created resource quota {quota_name} in namespace {self.namespace}")
        return None


# Global singleton (lazy-initialized)
_k8s_adapter: K8sSandboxAdapter | None = None


async def get_k8s_adapter() -> K8sSandboxAdapter:
    """Get or create the K8s sandbox adapter singleton."""
    global _k8s_adapter
    if _k8s_adapter is None:
        _k8s_adapter = K8sSandboxAdapter()
    return _k8s_adapter
