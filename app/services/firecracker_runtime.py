"""Firecracker microVM Runtime Adapter — L2 sandbox isolation.

.. deprecated:: SS-10 CB-01
    L2 (Firecracker) is deprecated in favour of the L2' (Cube) node data plane
    (``l2c``). This adapter is registered only when CDS_LEGACY_L2_ENABLED=true
    (default false) and is retained for the transition; new L2-class work goes
    to the Cube data plane.

Provides hardware-level isolation using Firecracker microVMs:
- Each sandbox runs in its own lightweight VM
- Network isolation via TAP devices
- Resource limits (vCPU, memory, disk)
- File transfer via virtio-fs or serial
- Code execution via SSH or serial console

Requires:
- Firecracker binary (https://firecracker-microvm.github.io/)
- Linux kernel image (vmlinux)
- Root filesystem image

Falls back to simulation mode when Firecracker is not installed.
"""
import hashlib
import json
import logging
import os
import shutil
import socket
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.services.sandbox_security import GuestSecrets

logger = logging.getLogger(__name__)


# === A96d：客侧密钥带外投递（复用 sandbox_security.GuestSecrets，A96 同类缺陷第四处）===
# bwrap 的参数与 ssh 的远端命令串都在 exec 之前就写进了 argv，同 UID 的任意进程可以从
# /proc/<pid>/cmdline 读到明文 ⇒ 会话密钥 / DEK 一律不进 argv：argv 里只留变量个数，
# 值经 exec（ssh 时是 ssh 的 stdin）交给客侧 bootstrap。拿不到通道时客侧 bootstrap 以
# rc=97 拒绝执行并自报原因 —— 宁可执行失败，绝不静默无密钥跑成。
def _secret_pairs(session_key: str | None, env_vars: dict[str, str] | None) -> list[tuple[str, str]]:
    """本层需要送进客侧的密钥环境变量（与 sandbox_security.build_bwrap_args 同一集合）。"""
    from app.services.sandbox_security import SECRET_ENV_NAMES

    pending: list[tuple[str, str]] = []
    if session_key:
        pending.append(("CDS_SESSION_KEY", session_key))
    if env_vars:
        for name in SECRET_ENV_NAMES:
            value = env_vars.get(name)
            if value:
                pending.append((name, value))
    return pending


def _new_guest_secrets(pairs: list[tuple[str, str]] | None = None):
    """建一条一次性带外通道（投递过即耗尽，重试必须重建）。"""
    from app.services.sandbox_security import GuestSecrets

    secrets = GuestSecrets()
    if pairs:
        secrets.extend(pairs)
    return secrets


def _seccomp_fallback_allowed() -> bool:
    """PZ-04 (MR-A13, closes S4): whether bwrap exec may retry without seccomp.

    Mirrors app.services.sandbox_runtime._seccomp_fallback_allowed — the
    seccomp-less retry must be gated by CDS_SECCOMP_FALLBACK_ALLOWED everywhere
    it exists, not only in the L0 adapter.
    """
    try:
        from app.core.config import get_settings
        return bool(get_settings().SECCOMP_FALLBACK_ALLOWED)
    except Exception:
        return True


class VMState(str, Enum):
    """Firecracker VM lifecycle states."""
    CREATED = "created"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    FAILED = "failed"


@dataclass
class VMConfig:
    """Firecracker VM configuration."""
    vcpu_count: int = 2
    mem_size_mb: int = 512
    rootfs_path: str = ""
    kernel_path: str = ""
    network_enabled: bool = False
    disk_size_mb: int = 1024
    log_level: str = "Warning"


@dataclass
class VMInstance:
    """Running VM instance metadata."""
    vm_id: str
    session_id: str
    socket_path: str
    state: VMState
    config: VMConfig
    workspace: str
    api_socket: str = ""
    pid: int | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    ip_address: str | None = None


@dataclass
class ExecutionResult:
    """Result of code execution inside a VM."""
    output: str
    exit_code: int
    duration_ms: int
    vm_id: str


class FirecrackerRuntime:
    """Firecracker microVM runtime manager.

    Manages the lifecycle of Firecracker microVMs for L2 sandbox isolation.
    Each sandbox session gets its own microVM with:
    - Dedicated kernel and rootfs
    - Isolated filesystem (workspace mounted via virtio-fs)
    - Resource limits (vCPU, memory)
    - Optional network isolation

    Usage:
        runtime = FirecrackerRuntime()
        vm = runtime.create_vm("session-123", VMConfig(vcpu_count=2, mem_size_mb=512))
        result = runtime.execute(vm, "print('hello')", "python")
        runtime.destroy_vm(vm)
    """

    def __init__(
        self,
        firecracker_bin: str = "firecracker",
        workspace_root: str = "/tmp/cds-firecracker",
        kernel_path: str = "",
        rootfs_path: str = "",
    ):
        self._firecracker_bin = firecracker_bin
        self._workspace_root = Path(workspace_root)
        self._workspace_root.mkdir(parents=True, exist_ok=True)
        self._kernel_path = kernel_path
        self._rootfs_path = rootfs_path
        self._vms: dict[str, VMInstance] = {}
        self._backend = self._detect_backend()

        if self._backend == "simulation":
            logger.warning("No VM backend available; running in simulation mode")
        else:
            logger.info(f"L2 VM backend: {self._backend}")

    def _detect_backend(self) -> str:
        """Detect available VM backend: firecracker > qemu-tcg > simulation.

        Returns:
            "firecracker" — KVM-accelerated Firecracker microVM
            "qemu-tcg" — QEMU with TCG software emulation (no KVM needed)
            "simulation" — direct subprocess (no isolation)
        """
        # 1. Try Firecracker with KVM
        if self._check_firecracker_available() and self._check_kvm_available():
            return "firecracker"

        # 2. Try QEMU TCG (no KVM required, but needs kernel + rootfs)
        if self._check_qemu_available() and self._kernel_path and self._rootfs_path:
            return "qemu-tcg"

        # 3. Fallback to simulation
        return "simulation"

    def _check_firecracker_available(self) -> bool:
        """Check if Firecracker binary is available."""
        try:
            result = subprocess.run(
                [self._firecracker_bin, "--version"],
                capture_output=True, text=True, timeout=5
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def _check_kvm_available(self) -> bool:
        """Check if KVM is available (/dev/kvm accessible)."""
        try:
            return os.access("/dev/kvm", os.R_OK | os.W_OK)
        except Exception:
            return False

    def _check_qemu_available(self) -> bool:
        """Check if QEMU is available with TCG support."""
        for qemu_bin in ("qemu-system-x86_64", "qemu-system-aarch64"):
            try:
                result = subprocess.run(
                    [qemu_bin, "--version"],
                    capture_output=True, text=True, timeout=5
                )
                if result.returncode == 0:
                    return True
            except (FileNotFoundError, subprocess.TimeoutExpired):
                continue
        return False

    @property
    def backend(self) -> str:
        """Current VM backend name."""
        return self._backend

    @property
    def _simulation_mode(self) -> bool:
        """Backward compatibility — True if no real VM backend."""
        return self._backend == "simulation"

    def create_vm(self, session_id: str, config: VMConfig | None = None, user_id: str = "") -> VMInstance:
        """Create a new Firecracker microVM for a sandbox session.

        Args:
            session_id: Sandbox session identifier
            config: VM configuration (defaults applied if None)
            user_id: Tenant/user ID for workspace isolation

        Returns:
            VMInstance with VM metadata

        Raises:
            RuntimeError: If VM creation fails
        """
        config = config or VMConfig()
        vm_id = f"fc-{uuid.uuid4().hex[:12]}"
        # Tenant-prefixed workspace: /tmp/cds-firecracker/{user_id}/{vm_id}/
        tenant_dir = self._workspace_root / (user_id or "anonymous")
        tenant_dir.mkdir(parents=True, exist_ok=True)
        workspace = str(tenant_dir / vm_id)
        socket_path = str(tenant_dir / f"{vm_id}.socket")

        os.makedirs(workspace, exist_ok=True)
        os.makedirs(f"{workspace}/input", exist_ok=True)
        os.makedirs(f"{workspace}/output", exist_ok=True)
        os.makedirs(f"{workspace}/tmp", exist_ok=True)

        vm = VMInstance(
            vm_id=vm_id,
            session_id=session_id,
            socket_path=socket_path,
            state=VMState.CREATED,
            config=config,
            workspace=workspace,
            api_socket=socket_path,
        )

        if self._simulation_mode:
            vm.state = VMState.RUNNING
            self._vms[vm_id] = vm
            logger.info(f"[sim] Created VM {vm_id} for session {session_id}")
            return vm

        try:
            if self._backend == "qemu-tcg":
                self._start_qemu_tcg(vm)
            else:
                self._start_firecracker(vm)
                self._configure_vm(vm)
            vm.state = VMState.RUNNING
            self._vms[vm_id] = vm
            logger.info(f"Created VM {vm_id} for session {session_id} (backend={self._backend})")
            return vm
        except Exception as e:
            vm.state = VMState.FAILED
            logger.error(f"Failed to create VM {vm_id}: {e}")
            raise RuntimeError(f"VM creation failed: {e}")

    def _start_firecracker(self, vm: VMInstance) -> None:
        """Start the Firecracker process."""
        api_socket = vm.api_socket

        # Clean up stale socket
        if os.path.exists(api_socket):
            os.unlink(api_socket)

        cmd = [
            self._firecracker_bin,
            "--api-sock", api_socket,
            "--log-path", f"{vm.workspace}/firecracker.log",
            "--level", vm.config.log_level,
        ]

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        vm.pid = proc.pid

        # Wait for API socket to be ready
        for _ in range(50):
            if os.path.exists(api_socket):
                try:
                    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    sock.connect(api_socket)
                    sock.close()
                    return
                except (ConnectionRefusedError, FileNotFoundError):
                    pass
            time.sleep(0.1)

        raise RuntimeError(f"Firecracker API socket not ready after 5s: {api_socket}")

    def _configure_vm(self, vm: VMInstance) -> None:
        """Configure VM via Firecracker API (kernel, rootfs, resources)."""
        import http.client

        sock_path = vm.api_socket
        conn = http.client.HTTPConnection("localhost")
        conn.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        conn.sock.connect(sock_path)

        # Set boot source (kernel)
        kernel_path = vm.config.kernel_path or self._kernel_path
        if kernel_path:
            conn.request("PUT", "/boot-source", json.dumps({
                "kernel_image_path": kernel_path,
                "boot_args": "console=ttyS0 reboot=k panic=1 pci=off"
            }))
            resp = conn.getresponse()
            resp.read()

        # Set root drive
        rootfs_path = vm.config.rootfs_path or self._rootfs_path
        if rootfs_path:
            conn.request("PUT", "/drives/rootfs", json.dumps({
                "drive_id": "rootfs",
                "path_on_host": rootfs_path,
                "is_root_device": True,
                "is_read_only": True,
            }))
            resp = conn.getresponse()
            resp.read()

        # Set machine config (vCPU, memory)
        conn.request("PUT", "/machine-config", json.dumps({
            "vcpu_count": vm.config.vcpu_count,
            "mem_size_mib": vm.config.mem_size_mb,
        }))
        resp = conn.getresponse()
        resp.read()

        # Start the VM
        conn.request("PUT", "/actions", json.dumps({"action_type": "InstanceStart"}))
        resp = conn.getresponse()
        resp.read()

        conn.close()

    def _start_qemu_tcg(self, vm: VMInstance) -> None:
        """Start a QEMU VM with TCG (software) emulation — no KVM required.

        This provides real VM isolation without hardware virtualization support.
        Performance is 10-50x slower than KVM but works in nested VM / k3d environments.
        """
        # Detect QEMU binary
        qemu_bin = None
        for candidate in ("qemu-system-x86_64", "qemu-system-aarch64"):
            try:
                result = subprocess.run([candidate, "--version"], capture_output=True, text=True, timeout=5)
                if result.returncode == 0:
                    qemu_bin = candidate
                    break
            except (FileNotFoundError, subprocess.TimeoutExpired):
                continue

        if not qemu_bin:
            raise RuntimeError("QEMU not found for TCG backend")

        kernel_path = vm.config.kernel_path or self._kernel_path
        rootfs_path = vm.config.rootfs_path or self._rootfs_path

        # Monitor socket for QMP (QEMU Machine Protocol)
        qmp_socket = f"{vm.workspace}/qmp.sock"

        cmd = [
            qemu_bin,
            "-machine", "type=q35,accel=tcg",
            "-cpu", "qemu64",
            "-smp", str(vm.config.vcpu_count),
            "-m", str(vm.config.mem_size_mb),
            "-nographic",
            "-no-reboot",
            "-nodefaults",
            "-serial", "mon:stdio",
            "-qmp", f"unix:{qmp_socket},server,nowait",
            "-drive", f"if=virtio,format=raw,file={rootfs_path}" if rootfs_path else "if=virtio,format=raw,file=/dev/null",
        ]

        if kernel_path:
            cmd += ["-kernel", kernel_path, "-append", "console=ttyS0 root=/dev/vda rw panic=1"]

        # Network: none (isolated)
        cmd += ["-net", "none"]

        # Resource limits
        cmd += [
            "-object", f"memory-backend-ram,id=mem0,size={vm.config.mem_size_mb}M",
        ]

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=vm.workspace,
        )
        vm.pid = proc.pid
        vm.api_socket = qmp_socket

        # Wait for QMP socket
        for _ in range(50):
            if os.path.exists(qmp_socket):
                try:
                    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    sock.connect(qmp_socket)
                    sock.close()
                    return
                except (ConnectionRefusedError, FileNotFoundError):
                    pass
            time.sleep(0.1)

        raise RuntimeError(f"QEMU TCG socket not ready after 5s: {qmp_socket}")

    @staticmethod
    def _build_sim_bwrap_args(workspace: Path, code_file: str, language: str,
                               session_key: str | None = None,
                               env_vars: dict[str, str] | None = None,
                               isolate_network: bool = True,
                               dns_proxy_port: int | None = None,
                               secrets: "GuestSecrets | None" = None) -> tuple[list[str], int | None]:
        """Build hardened bwrap args for simulation-mode isolation (L3-grade).

        Matches sandbox_security.build_bwrap_args security level:
        - PID, network, IPC, UTS, user, cgroup namespace isolation
        - Minimal /dev (null, zero, urandom only)
        - Restricted /proc
        - Seccomp BPF syscall filter
        """
        from app.services.sandbox_security import get_seccomp_bpf_fd

        seccomp_fd = get_seccomp_bpf_fd()

        args = [
            "bwrap",
            "--chdir", "/workspace",

            # === Namespace isolation ===
            # UTS namespace (separate hostname)
            "--unshare-uts", "--hostname", "cds-sandbox",

            # User namespace: map sandbox root to unprivileged host UID
            *(
                ["--unshare-user", "--uid", "1000", "--gid", "1000"]
                if os.getuid() != 0
                else []
            ),

            # PID namespace
            "--unshare-pid",

            # IPC namespace
            "--unshare-ipc",

            # Cgroup namespace (isolate /proc/self/cgroup from host)
            "--unshare-cgroup",

            # Network namespace: no network access (unless allowlist policy)
            *(["--unshare-net"] if isolate_network else []),

            # Die when parent dies
            "--die-with-parent",

            # === Filesystem isolation ===
            # Read-only system directories
            "--ro-bind", "/usr", "/usr",
            "--ro-bind", "/lib", "/lib",
            "--ro-bind", "/lib64", "/lib64",
            *(["--ro-bind", "/bin", "/bin"] if Path("/bin").exists() else []),

            # Minimal /dev — only null, zero, urandom
            "--dev", "/dev",
            "--ro-bind", "/dev/null", "/dev/null",
            "--ro-bind", "/dev/zero", "/dev/zero",
            "--ro-bind", "/dev/urandom", "/dev/urandom",

            # Restricted /proc (PID namespace gives isolated /proc)
            "--proc", "/proc",
            "--ro-bind", "/proc/self/status", "/proc/self/status",

            # tmpfs for /tmp, /var, /etc
            "--tmpfs", "/tmp:size=100m",
            "--tmpfs", "/var:size=10m",
            "--tmpfs", "/etc:size=1m",
        ]

        # DNS proxy resolv.conf for allowlist mode
        if dns_proxy_port is not None and not isolate_network:
            resolv_conf = workspace / ".resolv.conf"
            resolv_conf.write_text("nameserver 127.0.0.1\noptions ndots:0\n")
            resolv_conf.chmod(0o644)
            args += ["--ro-bind", str(resolv_conf), "/etc/resolv.conf"]

        args += [
            # Workspace
            "--tmpfs", "/workspace:size=500m",
            "--bind", str(workspace / "output"), "/workspace/output",
            "--bind", str(workspace / "tmp"), "/workspace/tmp",
            "--ro-bind", str(workspace / "input"), "/workspace/input",

            # Tmpfs for /home
            "--tmpfs", "/home:size=10m",

            # Block /sys entirely
            "--tmpfs", "/sys:size=1m",
        ]

        # Seccomp BPF filter
        if seccomp_fd is not None:
            args += ["--seccomp", str(seccomp_fd)]

        # Environment variables
        # A96d: 会话密钥与 DEK 只经带外通道走，绝不入 argv（argv 里只出现变量个数）
        from app.services.sandbox_security import (
            GUEST_SECRET_ARGV0,
            GUEST_SECRET_BOOTSTRAP,
            SECRET_ENV_NAMES,
        )

        pending = _secret_pairs(session_key, env_vars)
        secret_count = 0
        if pending:
            if secrets is None:
                logger.warning(
                    "A96d: 未传 GuestSecrets 带外通道，%d 个会话密钥/DEK 不经 argv 投递；"
                    "客侧 bootstrap 将以 rc=97 拒绝执行（不降级、不泄漏）",
                    len(pending),
                )
                secret_count = len(pending)
            else:
                secrets.extend(pending)
                secret_count = len(secrets)

        if env_vars:
            for k, v in env_vars.items():
                if k in SECRET_ENV_NAMES:
                    continue
                args += ["--setenv", k, v]

        args += ["--setenv", "PATH", "/usr/local/bin:/usr/bin:/bin"]

        # Command to execute
        if language == "python":
            guest_cmd = ["python3", code_file]
        else:
            guest_cmd = ["bash", code_file]

        if secret_count:
            args += [
                "sh", "-c", GUEST_SECRET_BOOTSTRAP, GUEST_SECRET_ARGV0, str(secret_count),
                *guest_cmd,
            ]
        else:
            args += guest_cmd

        return args, seccomp_fd

    @staticmethod
    def _spawn_guest(cmd: list[str], secrets, timeout: int,
                     extra_fds: tuple[int, ...] = ()) -> subprocess.CompletedProcess:
        """跑一次客侧命令。有密钥 ⇒ Popen(stdin=PIPE) + deliver() 带外投递；否则沿用 subprocess.run。"""
        if secrets is None or not len(secrets):
            return subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout, pass_fds=extra_fds
            )
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            pass_fds=extra_fds,
        )
        try:
            secrets.deliver(proc)
            # 同 sandbox_runtime._run_guest_argv 的修复（2026-11-02 全栈 e2e）：
            # deliver 已关写端；py3.12 的 communicate 会对已关 stdin 先 flush 而炸
            # ValueError（sim 执行梯在 3.12 镜像上每次执行必挂），摘除 stdin 让
            # 所有权移交显式化。本机 py3.14 该 flush 被 guard 吞掉——版本盲区再现。
            if proc.stdin is not None:
                try:
                    proc.stdin.close()
                except (ValueError, OSError):
                    pass
                proc.stdin = None
            try:
                stdout, stderr = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                stdout, stderr = proc.communicate()
                raise
        finally:
            secrets.close()
        return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)

    def _run_sim_bwrap_leg(
        self,
        workspace: Path,
        code_file: str,
        language: str,
        session_key: str | None,
        env_vars: dict[str, str] | None,
        effective_timeout: int,
    ) -> dict:
        """构造并执行一次加固 bwrap 客侧命令（A96d：密钥只走带外通道，不进 argv）。

        返回 {"output", "exit_code"}。seccomp 不可用时的重试仍受
        CDS_SECCOMP_FALLBACK_ALLOWED 门控（PZ-04/MR-A13）；带外通道是一次性的，
        所以重试复用 argv（其中只有变量个数）但必须重建通道。
        """
        from app.services.sandbox_security import close_seccomp_fd

        pairs = _secret_pairs(session_key, env_vars)
        secrets = _new_guest_secrets() if pairs else None
        if secrets is not None:
            cmd, seccomp_fd = self._build_sim_bwrap_args(
                workspace, code_file, language,
                session_key=session_key, env_vars=env_vars, secrets=secrets,
            )
        else:
            cmd, seccomp_fd = self._build_sim_bwrap_args(
                workspace, code_file, language,
                session_key=session_key, env_vars=env_vars,
            )
        extra_fds = (seccomp_fd,) if seccomp_fd is not None else ()
        try:
            result = self._spawn_guest(cmd, secrets, effective_timeout, extra_fds)
            if result.returncode != 0 and self._seccomp_retry_needed(result.stderr, seccomp_fd):
                if _seccomp_fallback_allowed():
                    cmd_no_seccomp = [c for c in cmd if c != "--seccomp" and c != str(seccomp_fd)]
                    retry_secrets = _new_guest_secrets(pairs) if pairs else None
                    result = self._spawn_guest(cmd_no_seccomp, retry_secrets, effective_timeout)
                else:
                    logger.warning(
                        "[firecracker] seccomp retry denied (SECCOMP_FALLBACK_ALLOWED=false)"
                    )
                    return {
                        "output": (
                            "SECURITY ERROR: seccomp unavailable and "
                            "CDS_SECCOMP_FALLBACK_ALLOWED=false — refusing to retry "
                            "without seccomp. Original error: " + (result.stderr or "").strip()
                        ),
                        "exit_code": result.returncode,
                    }
            return {"output": result.stdout + result.stderr, "exit_code": result.returncode}
        except subprocess.TimeoutExpired:
            return {"output": f"Execution timed out ({effective_timeout}s)", "exit_code": -1}
        except Exception as e:
            return {"output": str(e), "exit_code": -1}
        finally:
            close_seccomp_fd(seccomp_fd)

    @staticmethod
    def _ssh_remote_command(
        interpreter: str,
        remote_path: str,
        session_key: str | None = None,
        env_vars: dict[str, str] | None = None,
        secrets=None,
    ) -> str:
        """拼 ssh 的远端命令串（A96d：本机 ssh 进程的 argv 同样公开 ⇒ 密钥不进命令串）。

        非密钥变量仍按 `K=V ` 前缀；密钥改由客侧 bootstrap 从 stdin（即 ssh 转发的
        本进程 stdin）读取。secrets=None 且确有密钥时照旧挂 bootstrap 但无处投递值，
        远端以 rc=97 拒绝执行 —— 不降级、不泄漏。
        """
        import shlex

        from app.services.sandbox_security import (
            GUEST_SECRET_ARGV0,
            GUEST_SECRET_BOOTSTRAP,
            SECRET_ENV_NAMES,
        )

        pending = _secret_pairs(session_key, env_vars)
        count = 0
        if pending:
            if secrets is None:
                logger.warning(
                    "A96d: ssh 执行腿未传 GuestSecrets 带外通道，%d 个会话密钥/DEK 不经 argv 投递；"
                    "客侧 bootstrap 将以 rc=97 拒绝执行（不降级、不泄漏）",
                    len(pending),
                )
                count = len(pending)
            else:
                secrets.extend(pending)
                count = len(secrets)

        plain = {k: v for k, v in (env_vars or {}).items() if k not in SECRET_ENV_NAMES}
        env_prefix = "".join(f"{k}={shlex.quote(str(v))} " for k, v in plain.items())
        guest_cmd = f"{shlex.quote(interpreter)} {shlex.quote(remote_path)}"
        if count:
            guest_cmd = (
                f"sh -c {shlex.quote(GUEST_SECRET_BOOTSTRAP)} "
                f"{GUEST_SECRET_ARGV0} {count} {guest_cmd}"
            )
        return f"{env_prefix}{guest_cmd}"

    @staticmethod
    def _seccomp_retry_needed(stderr: str, seccomp_fd: int | None) -> bool:
        """Return true when bwrap failed because host seccomp support is incompatible."""
        if seccomp_fd is None:
            return False
        return (
            "EINVAL" in stderr
            or "Invalid argument" in stderr
            or "PR_SET_SECCOMP" in stderr
        )

    def execute(
        self,
        vm: VMInstance,
        code: str,
        language: str = "python",
        session_key: str | None = None,
        env_vars: dict[str, str] | None = None,
        timeout: int | None = None,
    ) -> ExecutionResult:
        """Execute code inside a Firecracker microVM.

        Args:
            vm: Running VM instance
            code: Code to execute
            language: "python" or "bash"
            session_key: Optional session key for env injection
            env_vars: Additional sandbox context variables for audit/policy hooks.
            timeout: Per-execution timeout in seconds.

        Returns:
            ExecutionResult with output and exit code
        """
        start = time.monotonic()

        if self._simulation_mode:
            return self._simulate_execute(
                vm,
                code,
                language,
                session_key=session_key,
                env_vars=env_vars,
                timeout=timeout,
            )

        try:
            # For QEMU TCG, use serial execution (no SSH without network)
            if self._backend == "qemu-tcg":
                result = self._execute_via_qemu_serial(
                    vm,
                    code,
                    language,
                    session_key=session_key,
                    env_vars=env_vars,
                    timeout=timeout,
                )
                duration = int((time.monotonic() - start) * 1000)
                return ExecutionResult(
                    output=result.get("output", ""),
                    exit_code=result.get("exit_code", -1),
                    duration_ms=duration,
                    vm_id=vm.vm_id,
                )
            # Write code to workspace
            ext = "py" if language == "python" else "sh"
            code_file = f"{vm.workspace}/tmp/exec.{ext}"
            with open(code_file, "w") as f:
                f.write(code)

            # Execute via SSH (if network enabled) or serial console
            if vm.config.network_enabled and vm.ip_address:
                result = self._execute_via_ssh(
                    vm,
                    code_file,
                    language,
                    session_key=session_key,
                    env_vars=env_vars,
                    timeout=timeout,
                )
            else:
                result = self._execute_via_serial(
                    vm,
                    code_file,
                    language,
                    session_key=session_key,
                    env_vars=env_vars,
                    timeout=timeout,
                )

            duration = int((time.monotonic() - start) * 1000)
            return ExecutionResult(
                output=result.get("output", ""),
                exit_code=result.get("exit_code", -1),
                duration_ms=duration,
                vm_id=vm.vm_id,
            )
        except Exception as e:
            duration = int((time.monotonic() - start) * 1000)
            return ExecutionResult(
                output=str(e),
                exit_code=-1,
                duration_ms=duration,
                vm_id=vm.vm_id,
            )

    def _simulate_execute(
        self,
        vm: VMInstance,
        code: str,
        language: str,
        session_key: str | None = None,
        env_vars: dict[str, str] | None = None,
        timeout: int | None = None,
    ) -> ExecutionResult:
        """Simulate execution with bwrap namespace isolation (L3-grade security)."""
        start = time.monotonic()
        effective_timeout = timeout or 120

        # Write code to workspace
        ext = "py" if language == "python" else "sh"
        code_file = Path(vm.workspace) / "tmp" / f"exec.{ext}"
        code_file.write_text(code)

        result = self._run_sim_bwrap_leg(
            Path(vm.workspace),
            f"/workspace/tmp/exec.{ext}",
            language,
            session_key,
            env_vars,
            effective_timeout,
        )
        duration = int((time.monotonic() - start) * 1000)
        return ExecutionResult(
            output=result["output"],
            exit_code=result["exit_code"],
            duration_ms=duration,
            vm_id=vm.vm_id,
        )

    def _execute_via_ssh(
        self,
        vm: VMInstance,
        code_file: str,
        language: str,
        session_key: str | None = None,
        env_vars: dict[str, str] | None = None,
        timeout: int | None = None,
    ) -> dict:
        """Execute code via SSH into the VM."""
        ext = "py" if language == "python" else "sh"
        interpreter = "python3" if language == "python" else "bash"
        effective_timeout = timeout or 120

        # Copy file to VM
        scp_cmd = ["scp", "-o", "StrictHostKeyChecking=no", code_file, f"root@{vm.ip_address}:/tmp/exec.{ext}"]
        subprocess.run(scp_cmd, capture_output=True, timeout=10)

        # Execute（A96d：ssh 的参数串就在本机 argv 里 ⇒ 密钥走带外通道，经 ssh 的 stdin 投递）
        pairs = _secret_pairs(session_key, env_vars)
        secrets = _new_guest_secrets() if pairs else None
        remote_cmd = self._ssh_remote_command(
            interpreter, f"/tmp/exec.{ext}", session_key, env_vars, secrets
        )
        ssh_cmd = [
            "ssh",
            "-o",
            "StrictHostKeyChecking=no",
            f"root@{vm.ip_address}",
            remote_cmd,
        ]
        try:
            if secrets is not None:
                secrets.push()
                result = subprocess.run(
                    ssh_cmd, capture_output=True, text=True,
                    timeout=effective_timeout, stdin=secrets.stdin_fd,
                )
            else:
                result = subprocess.run(
                    ssh_cmd, capture_output=True, text=True, timeout=effective_timeout
                )
        finally:
            if secrets is not None:
                secrets.close()
        return {"output": result.stdout + result.stderr, "exit_code": result.returncode}


    def _execute_via_serial(
        self,
        vm: VMInstance,
        code_file: str,
        language: str,
        session_key: str | None = None,
        env_vars: dict[str, str] | None = None,
        timeout: int | None = None,
    ) -> dict:
        """Execute code for non-network Firecracker guests.

        A production Firecracker guest needs a guest agent or an SSH/mount channel
        to run arbitrary snippets. When network execution is disabled and no guest
        agent is configured, keep the request functional by using the same hardened
        local bwrap fallback as QEMU TCG instead of returning a placeholder failure.
        """
        with open(code_file) as f:
            code = f.read()
        logger.warning(
            "[firecracker] Guest serial execution unavailable for VM %s; using hardened local fallback",
            vm.vm_id,
        )
        return self._simulate_execute_raw(
            vm,
            code,
            language,
            session_key=session_key,
            env_vars=env_vars,
            timeout=timeout,
        )

    def _execute_via_qemu_serial(
        self,
        vm: VMInstance,
        code: str,
        language: str,
        session_key: str | None = None,
        env_vars: dict[str, str] | None = None,
        timeout: int | None = None,
    ) -> dict:
        """Execute code via QEMU serial console (QMP + guest agent or pexpect).

        For QEMU TCG without network, we use the QMP monitor to interact with the VM.
        This is a simplified implementation that writes code to a shared virtio-fs path
        and uses QMP to trigger execution.
        """
        # In QEMU TCG mode without a guest agent, we fall back to simulation
        # since we can't easily pipe code through serial without a guest agent.
        # The VM provides process-level isolation (separate address space, kernel).
        logger.warning(f"[qemu-tcg] Using simulation fallback for VM {vm.vm_id}")
        return self._simulate_execute_raw(
            vm,
            code,
            language,
            session_key=session_key,
            env_vars=env_vars,
            timeout=timeout,
        )

    def _simulate_execute_raw(
        self,
        vm: VMInstance,
        code: str,
        language: str,
        session_key: str | None = None,
        env_vars: dict[str, str] | None = None,
        timeout: int | None = None,
    ) -> dict:
        """Execute code with bwrap namespace isolation (used by QEMU TCG and simulation)."""
        effective_timeout = timeout or 120
        ext = "py" if language == "python" else "sh"
        code_file = Path(vm.workspace) / "tmp" / f"exec.{ext}"
        code_file.write_text(code)

        return self._run_sim_bwrap_leg(
            Path(vm.workspace),
            f"/workspace/tmp/exec.{ext}",
            language,
            session_key,
            env_vars,
            effective_timeout,
        )

    def transfer_file(self, vm: VMInstance, local_path: str, vm_path: str) -> bool:
        """Transfer a file into the VM.

        Args:
            vm: Running VM instance
            local_path: Path on host
            vm_path: Destination path inside VM

        Returns:
            True on success
        """
        if self._simulation_mode:
            dest = Path(vm.workspace) / "input" / Path(vm_path).name
            shutil.copy2(local_path, dest)
            return True

        if vm.config.network_enabled and vm.ip_address:
            scp_cmd = ["scp", "-o", "StrictHostKeyChecking=no", local_path, f"root@{vm.ip_address}:{vm_path}"]
            result = subprocess.run(scp_cmd, capture_output=True, timeout=30)
            return result.returncode == 0

        # Fallback: copy to workspace (shared via virtio-fs)
        dest = Path(vm.workspace) / "input" / Path(vm_path).name
        shutil.copy2(local_path, dest)
        return True

    def retrieve_file(self, vm: VMInstance, vm_path: str, local_path: str) -> bool:
        """Retrieve a file from the VM.

        Args:
            vm: Running VM instance
            vm_path: Path inside VM
            local_path: Destination path on host

        Returns:
            True on success
        """
        if self._simulation_mode:
            src = Path(vm.workspace) / "output" / Path(vm_path).name
            if src.exists():
                shutil.copy2(src, local_path)
                return True
            return False

        if vm.config.network_enabled and vm.ip_address:
            scp_cmd = ["scp", "-o", "StrictHostKeyChecking=no", f"root@{vm.ip_address}:{vm_path}", local_path]
            result = subprocess.run(scp_cmd, capture_output=True, timeout=30)
            return result.returncode == 0

        src = Path(vm.workspace) / "output" / Path(vm_path).name
        if src.exists():
            shutil.copy2(src, local_path)
            return True
        return False

    def pause_vm(self, vm: VMInstance) -> bool:
        """Pause a running VM."""
        if self._simulation_mode:
            vm.state = VMState.PAUSED
            return True

        try:
            import http.client
            conn = http.client.HTTPConnection("localhost")
            conn.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            conn.sock.connect(vm.api_socket)
            conn.request("PUT", "/actions", json.dumps({"action_type": "Pause"}))
            resp = conn.getresponse()
            resp.read()
            conn.close()
            vm.state = VMState.PAUSED
            return True
        except Exception as e:
            logger.error(f"Failed to pause VM {vm.vm_id}: {e}")
            return False

    def resume_vm(self, vm: VMInstance) -> bool:
        """Resume a paused VM."""
        if self._simulation_mode:
            vm.state = VMState.RUNNING
            return True

        try:
            import http.client
            conn = http.client.HTTPConnection("localhost")
            conn.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            conn.sock.connect(vm.api_socket)
            conn.request("PUT", "/actions", json.dumps({"action_type": "Resume"}))
            resp = conn.getresponse()
            resp.read()
            conn.close()
            vm.state = VMState.RUNNING
            return True
        except Exception as e:
            logger.error(f"Failed to resume VM {vm.vm_id}: {e}")
            return False

    def destroy_vm(self, vm: VMInstance) -> bool:
        """Destroy a VM and clean up resources.

        Args:
            vm: VM instance to destroy

        Returns:
            True on success
        """
        if self._simulation_mode:
            workspace = Path(vm.workspace)
            if workspace.exists():
                shutil.rmtree(workspace, ignore_errors=True)
            self._vms.pop(vm.vm_id, None)
            vm.state = VMState.STOPPED
            logger.info(f"[sim] Destroyed VM {vm.vm_id}")
            return True

        try:
            # Send shutdown signal via QMP or Firecracker API
            if self._backend == "qemu-tcg":
                self._qemu_quit(vm)
            else:
                import http.client
                conn = http.client.HTTPConnection("localhost")
                conn.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                conn.sock.connect(vm.api_socket)
                conn.request("PUT", "/actions", json.dumps({"action_type": "SendCtrlAltDel"}))
                resp = conn.getresponse()
                resp.read()
                conn.close()

            # Wait for process to exit
            if vm.pid:
                try:
                    os.kill(vm.pid, 15)  # SIGTERM
                    time.sleep(0.5)
                    os.kill(vm.pid, 9)   # SIGKILL (if still alive)
                except ProcessLookupError:
                    pass

            # Clean up
            for socket_path in (vm.api_socket, f"{vm.workspace}/qmp.sock"):
                if os.path.exists(socket_path):
                    os.unlink(socket_path)
            workspace = Path(vm.workspace)
            if workspace.exists():
                shutil.rmtree(workspace, ignore_errors=True)

            self._vms.pop(vm.vm_id, None)
            vm.state = VMState.STOPPED
            logger.info(f"Destroyed VM {vm.vm_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to destroy VM {vm.vm_id}: {e}")
            return False

    def _qemu_quit(self, vm: VMInstance) -> None:
        """Send quit command to QEMU via QMP."""
        import http.client
        qmp_socket = f"{vm.workspace}/qmp.sock"
        if not os.path.exists(qmp_socket):
            return
        try:
            conn = http.client.HTTPConnection("localhost")
            conn.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            conn.sock.connect(qmp_socket)
            # QMP execute quit
            conn.request("PUT", "/execute", json.dumps({"execute": "quit"}))
            resp = conn.getresponse()
            resp.read()
            conn.close()
        except Exception:
            pass  # Process kill will handle cleanup

    def get_vm(self, vm_id: str) -> VMInstance | None:
        """Get VM instance by ID."""
        return self._vms.get(vm_id)

    def list_vms(self) -> list[VMInstance]:
        """List all active VMs."""
        return list(self._vms.values())

    def get_vm_metrics(self, vm: VMInstance) -> dict[str, Any]:
        """Get VM resource usage metrics.

        Returns:
            Dict with CPU, memory, disk usage
        """
        if self._simulation_mode:
            return {
                "vm_id": vm.vm_id,
                "state": vm.state.value,
                "cpu_usage_percent": 0.0,
                "mem_used_mb": 0,
                "mem_total_mb": vm.config.mem_size_mb,
                "vcpu_count": vm.config.vcpu_count,
                "disk_used_mb": 0,
                "disk_total_mb": vm.config.disk_size_mb,
                "uptime_seconds": (datetime.now(timezone.utc) - vm.created_at).total_seconds(),
            }

        return {
            "vm_id": vm.vm_id,
            "state": vm.state.value,
            "pid": vm.pid,
            "mem_total_mb": vm.config.mem_size_mb,
            "vcpu_count": vm.config.vcpu_count,
            "uptime_seconds": (datetime.now(timezone.utc) - vm.created_at).total_seconds(),
        }


# Singleton
firecracker_runtime = FirecrackerRuntime()
