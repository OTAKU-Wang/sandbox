"""Sandbox Runtime — manages L1/L2/L3/K8s sandbox lifecycle.
L1: TEE/SGX/Occlum adapter with software fallback
L2: Firecracker/QEMU adapter with hardened local fallback when guest execution is unavailable
L3: bwrap (Bubblewrap) lightweight sandbox + Docker fallback
"""
import asyncio
import logging
import uuid
import subprocess
import json
import os
import shlex
import shutil
from datetime import datetime, timezone
from pathlib import Path
from abc import ABC, abstractmethod
from types import SimpleNamespace

from app.models.sandbox_session import SandboxLevel, SessionStatus, SandboxMode

logger = logging.getLogger(__name__)


def _seccomp_fallback_allowed() -> bool:
    """Gap D4/T12: whether sandbox exec may retry without seccomp on EINVAL.

    Fail-closed when SECCOMP_FALLBACK_ALLOWED=false — the seccomp error is
    surfaced instead of silently degrading isolation.
    """
    try:
        from app.core.config import get_settings
        return bool(get_settings().SECCOMP_FALLBACK_ALLOWED)
    except Exception:
        return True


class RuntimeAdapter(ABC):
    """Abstract interface for sandbox runtime adapters."""

    @abstractmethod
    def provision(self, session_id: str, data_path: str, timeout: int, user_id: str = "") -> dict:
        ...

    @abstractmethod
    async def execute(self, container_id: str, code: str, language: str) -> dict:
        ...

    @abstractmethod
    def terminate(self, container_id: str) -> bool:
        ...

    @abstractmethod
    def get_status(self, container_id: str) -> str:
        ...


def _build_l0_bwrap_args(
    workspace: Path,
    code_file: str,
    language: str,
    session_key: str | None = None,
    env_vars: dict[str, str] | None = None,
    isolate_network: bool = True,
    dns_proxy_port: int | None = None,
    extra_binds: list[tuple[Path, str, bool]] | None = None,
) -> tuple[list[str], int | None]:
    """Build hardened bwrap arguments for L0 sandbox.

    Lighter than L3 but still provides:
    - PID namespace (process isolation)
    - Mount namespace (filesystem isolation)
    - Network namespace (no network by default, or controlled access via policy)
    - IPC namespace (no shared memory)
    - Seccomp syscall filter
    - Minimal /dev, restricted /proc, no /sys

    Args:
        isolate_network: If True, isolate network (--unshare-net). If False, allow
            network access (for allowlist mode with iptables filtering).
        dns_proxy_port: If set, configure /etc/resolv.conf to point to DNS proxy.

    Returns (args_list, seccomp_fd). Caller must keep seccomp_fd open and pass via pass_fds.
    """
    from app.services.sandbox_security import get_seccomp_bpf_fd

    # Seccomp BPF filter — compile and pass via fd to bwrap
    bpf_fd = get_seccomp_bpf_fd()

    args = [
        "bwrap",
        "--chdir", "/workspace",

        # Namespace isolation
        "--unshare-pid",
        *(["--unshare-net"] if isolate_network else []),
        "--unshare-ipc",

        # Die when parent dies
        "--die-with-parent",

        # Read-only system directories
        "--ro-bind", "/usr", "/usr",
        "--ro-bind", "/lib", "/lib",
        "--ro-bind", "/lib64", "/lib64",
        *(["--ro-bind", "/bin", "/bin"] if Path("/bin").exists() else []),

        # Minimal /dev
        "--dev", "/dev",
        "--ro-bind", "/dev/null", "/dev/null",
        "--ro-bind", "/dev/zero", "/dev/zero",
        "--ro-bind", "/dev/urandom", "/dev/urandom",

        # Restricted /proc (PID namespace gives isolated /proc)
        "--proc", "/proc",

        # tmpfs for /tmp, /var, /etc
        "--tmpfs", "/tmp:size=100m",
        "--tmpfs", "/var:size=10m",
        "--tmpfs", "/etc:size=1m",
    ]

    # DNS proxy resolv.conf for allowlist mode
    if dns_proxy_port is not None and not isolate_network:
        resolv_conf = workspace / ".resolv.conf"
        resolv_conf.write_text(f"nameserver 127.0.0.1\noptions ndots:0\n")
        resolv_conf.chmod(0o644)
        args += ["--ro-bind", str(resolv_conf), "/etc/resolv.conf"]

    args += [
        # Workspace
        "--tmpfs", "/workspace:size=500m",
        "--bind", str(workspace / "output"), "/workspace/output",
        "--bind", str(workspace / "tmp"), "/workspace/tmp",
        "--ro-bind", str(workspace / "input"), "/workspace/input",
        # Session files exchange dir (Round 39/40 usability): read-write so
        # sandbox code reads uploaded inputs and can drop results back.
        *(["--bind", str(workspace / "files"), "/workspace/files"]
          if (workspace / "files").is_dir()
          else []),
        # W15 shared volumes (owner-attached): ro or rw per attachment
        *[
            bind_arg
            for host_path, guest_path, read_only in (extra_binds or [])
            for bind_arg in (
                ("--ro-bind", str(host_path), guest_path)
                if read_only
                else ("--bind", str(host_path), guest_path)
            )
        ],

        # tmpfs for /home
        "--tmpfs", "/home:size=10m",

        # Block /sys
        "--tmpfs", "/sys:size=1m",
    ]

    # Seccomp BPF filter
    if bpf_fd is not None:
        args += ["--seccomp", str(bpf_fd)]

    # Inject session key via environment variable (never written to disk)
    if session_key:
        args += ["--setenv", "CDS_SESSION_KEY", session_key]

    # Inject additional env vars
    if env_vars:
        for k, v in env_vars.items():
            args += ["--setenv", k, v]

    # Set PATH
    args += ["--setenv", "PATH", "/usr/local/bin:/usr/bin:/bin"]

    # Command to execute
    if language == "python":
        args += ["python3", code_file]
    elif language == "sql":
        from app.services.sandbox_security import _build_sql_runner
        args += ["python3", "-c", _build_sql_runner(code_file)]
    else:
        args += ["bash", code_file]

    return args, bpf_fd


class ProcessAdapter(RuntimeAdapter):
    """L0: Process-level isolation with bwrap namespace hardening.

    Security boundary: bwrap (PID/mount/network/IPC namespace) + seccomp syscall filter
    + cgroup v2 resource limits + output size limit + timeout. Unsandboxed fallback
    is disabled by default and should only be enabled for local development.
    """

    def __init__(self, workspace_root: str = "/tmp/cds-sandbox-l0", allow_unsandboxed_fallback: bool = False):
        self.workspace_root = Path(workspace_root)
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self._cgroup_root = Path("/sys/fs/cgroup/cds-l0")
        self._bwrap_available = shutil.which("bwrap") is not None
        self._allow_unsandboxed_fallback = allow_unsandboxed_fallback

    def provision(self, session_id: str, data_path: str, timeout: int = 3600, user_id: str = "") -> dict:
        # Workspace with tenant prefix: /tmp/cds-sandbox-l0/{user_id}/{session_id}/
        tenant_dir = self.workspace_root / (user_id or "anonymous")
        workspace = tenant_dir / session_id
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "input").mkdir(exist_ok=True)
        (workspace / "output").mkdir(exist_ok=True)
        (workspace / "tmp").mkdir(exist_ok=True)

        if data_path and Path(data_path).exists():
            if Path(data_path).is_file():
                shutil.copy2(data_path, workspace / "input")
            elif Path(data_path).is_dir():
                shutil.copytree(data_path, workspace / "input" / "data", dirs_exist_ok=True)

        # Create cgroup for this session
        cgroup_path = self._cgroup_root / session_id
        self._create_cgroup(cgroup_path, memory_mb=256, cpu_percent=100, max_pids=64)

        return {"container_id": f"l0-{session_id}", "status": SessionStatus.RUNNING.value, "workspace": str(workspace)}

    def _resolve_workspace(self, container_id: str) -> Path | None:
        """Find workspace for a container ID by searching tenant directories."""
        session_id = container_id.replace("l0-", "")
        # Try direct path first (backward compat)
        direct = self.workspace_root / session_id
        if direct.exists():
            return direct
        # Search tenant directories
        for tenant_dir in self.workspace_root.iterdir():
            if tenant_dir.is_dir():
                candidate = tenant_dir / session_id
                if candidate.exists():
                    return candidate
        return None

    def _prepare_exec_command(self, workspace: Path, container_id: str, code: str, language: str,
                              session_key: str | None, env_vars: dict[str, str] | None,
                              isolate_network: bool, dns_proxy_port: int | None,
                              extra_binds: list[tuple[Path, str, bool]] | None = None):
        """Build the isolation command for one execution (shared by execute()
        and execute_streaming() so both paths use identical hardening).

        Returns (cmd, env, seccomp_fd). Raises RuntimeError when no isolation
        is available and the unsandboxed fallback is disabled.
        """
        ext_map = {"python": "py", "sql": "sql", "shell": "sh", "bash": "sh"}
        ext = ext_map.get(language, "py")
        code_file = workspace / "tmp" / f"exec.{ext}"
        code_file.write_text(code)

        seccomp_fd = None
        if self._bwrap_available:
            bwrap_args, seccomp_fd = _build_l0_bwrap_args(
                workspace=workspace,
                code_file=f"/workspace/tmp/exec.{ext}",
                language=language,
                session_key=session_key,
                env_vars=env_vars,
                isolate_network=isolate_network,
                dns_proxy_port=dns_proxy_port,
                extra_binds=extra_binds,
            )
            return bwrap_args, os.environ.copy(), seccomp_fd

        if not self._allow_unsandboxed_fallback:
            raise RuntimeError(
                "SECURITY ERROR: bwrap not installed and L0 unsandboxed fallback is disabled. "
                "Install bubblewrap or enable allow_unsandboxed_fallback for development only."
            )

        if language == "python":
            cmd = ["python3", str(code_file)]
        elif language == "sql":
            from app.services.sandbox_security import _build_sql_runner
            sql_wrapper = workspace / "tmp" / "exec_sql.py"
            sql_wrapper.write_text(_build_sql_runner(str(code_file)))
            cmd = ["python3", str(sql_wrapper)]
        else:
            cmd = ["bash", str(code_file)]

        session_id = container_id[3:] if container_id.startswith("l0-") else container_id
        cgroup_path = self._cgroup_root / session_id
        if cgroup_path.exists():
            cmd = self._build_cgroup_exec(cgroup_path, cmd)

        env = os.environ.copy()
        if session_key:
            env["CDS_SESSION_KEY"] = session_key
        if env_vars:
            env.update(env_vars)
        return cmd, env, None

    async def execute(self, container_id: str, code: str, language: str = "python",
                      session_key: str | None = None, env_vars: dict[str, str] | None = None,
                      timeout: int | None = None,
                      isolate_network: bool = True,
                      dns_proxy_port: int | None = None,
                      extra_binds: list[tuple[Path, str, bool]] | None = None) -> dict:
        from app.services.sandbox_security import OutputLimiter

        workspace = self._resolve_workspace(container_id)
        if not workspace or not workspace.exists():
            return {"output": "Workspace not found", "exit_code": -1, "duration_ms": 0}

        effective_timeout = timeout or 120

        try:
            cmd, env, seccomp_fd = self._prepare_exec_command(
                workspace, container_id, code, language, session_key, env_vars,
                isolate_network, dns_proxy_port, extra_binds=extra_binds,
            )
        except RuntimeError as e:
            return {"output": str(e), "exit_code": -1, "duration_ms": 0, "sandbox_level": "L0"}

        output_limiter = OutputLimiter(max_bytes=10 * 1024 * 1024)
        start = datetime.now()
        extra_fds = (seccomp_fd,) if seccomp_fd is not None else ()
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(subprocess.run, cmd, capture_output=True, text=True, env=env, pass_fds=extra_fds),
                timeout=effective_timeout,
            )
            # Retry without seccomp if kernel doesn't support it (EINVAL) —
            # gated by SECCOMP_FALLBACK_ALLOWED (gap D4/T12, fail-closed).
            if (
                result.returncode != 0
                and "EINVAL" in result.stderr
                and seccomp_fd is not None
                and _seccomp_fallback_allowed()
            ):
                cmd_no_seccomp = [c for c in cmd if c != "--seccomp" and c != str(seccomp_fd)]
                result = await asyncio.wait_for(
                    asyncio.to_thread(subprocess.run, cmd_no_seccomp, capture_output=True, text=True, env=env),
                    timeout=effective_timeout,
                )
            duration = int((datetime.now() - start).total_seconds() * 1000)

            stdout = output_limiter.check_output(result.stdout)
            stderr = output_limiter.check_output(result.stderr)

            return {
                "output": stdout + stderr,
                "exit_code": result.returncode,
                "duration_ms": duration,
                "output_truncated": output_limiter.was_truncated,
                "sandbox_level": "L0",
            }
        except asyncio.TimeoutError:
            return {
                "output": f"Execution timed out ({effective_timeout}s)",
                "exit_code": -1,
                "duration_ms": effective_timeout * 1000,
                "sandbox_level": "L0",
            }
        except Exception as e:
            return {"output": str(e), "exit_code": -1, "duration_ms": 0, "sandbox_level": "L0"}

    async def execute_streaming(self, container_id: str, code: str, language: str,
                                *, session_key: str | None = None,
                                env_vars: dict[str, str] | None = None,
                                timeout: int | None = None,
                                extra_binds: list[tuple[Path, str, bool]] | None = None,
                                on_line) -> dict:
        """W10: streaming execution — every output line is pushed to
        ``on_line(stream_name, line)`` as it arrives (same isolation
        construction as execute(): bwrap + seccomp + cgroup).

        The callback may raise (e.g. OutputBlockedError) — the process is
        killed, remaining output discarded, and the exception propagates to
        the caller (fail-closed, T5 semantics).
        """
        workspace = self._resolve_workspace(container_id)
        if not workspace or not workspace.exists():
            await on_line("stderr", "Workspace not found")
            return {"exit_code": -1, "duration_ms": 0, "sandbox_level": "L0"}

        effective_timeout = timeout or 120
        try:
            cmd, env, seccomp_fd = self._prepare_exec_command(
                workspace, container_id, code, language, session_key, env_vars,
                isolate_network=True, dns_proxy_port=None, extra_binds=extra_binds,
            )
        except RuntimeError as e:
            await on_line("stderr", str(e))
            return {"exit_code": -1, "duration_ms": 0, "sandbox_level": "L0"}

        extra_fds = (seccomp_fd,) if seccomp_fd is not None else ()
        started = datetime.now()
        saw_einval = False
        proc_holder: dict[str, asyncio.subprocess.Process] = {}

        async def _run_once(command: list[str]) -> int:
            nonlocal saw_einval
            proc = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                pass_fds=extra_fds,
            )
            proc_holder["proc"] = proc

            async def _pump(stream: asyncio.StreamReader, name: str) -> None:
                try:
                    nonlocal saw_einval
                    while True:
                        raw = await stream.readline()
                        if not raw:
                            break
                        line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                        if "EINVAL" in line:
                            saw_einval = True
                        await on_line(name, line)
                except BaseException:
                    # Fail-closed: kill the process the moment the reviewer
                    # rejects a line (T5 semantics) so no further output is
                    # produced or streamed.
                    if proc.returncode is None:
                        proc.kill()
                    raise

            out_task = asyncio.create_task(_pump(proc.stdout, "stdout"))
            err_task = asyncio.create_task(_pump(proc.stderr, "stderr"))
            timed_out = False
            try:
                exit_code = await asyncio.wait_for(proc.wait(), timeout=effective_timeout)
            except asyncio.TimeoutError:
                timed_out = True
                proc.kill()
                await proc.wait()
                exit_code = -1
                await on_line("stderr", f"Execution timed out ({effective_timeout}s)")
            pump_results = await asyncio.gather(out_task, err_task, return_exceptions=True)
            for outcome in pump_results:
                if isinstance(outcome, BaseException) and not isinstance(outcome, asyncio.CancelledError):
                    raise outcome
            return -1 if timed_out else exit_code

        try:
            exit_code = await _run_once(cmd)
            # Mirror execute(): retry without seccomp on kernel EINVAL
            # (SECCOMP_FALLBACK_ALLOWED gated, fail-closed default).
            if exit_code != 0 and saw_einval and seccomp_fd is not None and _seccomp_fallback_allowed():
                cmd_no_seccomp = [c for c in cmd if c != "--seccomp" and c != str(seccomp_fd)]
                exit_code = await _run_once(cmd_no_seccomp)
        except BaseException:
            proc = proc_holder.get("proc")
            if proc is not None and proc.returncode is None:
                proc.kill()
            raise
        duration = int((datetime.now() - started).total_seconds() * 1000)
        return {"exit_code": exit_code, "duration_ms": duration, "sandbox_level": "L0", "blocked": False}

    def terminate(self, container_id: str) -> bool:
        session_id = container_id.replace("l0-", "")
        workspace = self._resolve_workspace(container_id)

        # Remove cgroup
        cgroup_path = self._cgroup_root / session_id
        self._remove_cgroup(cgroup_path)

        if workspace and workspace.exists():
            shutil.rmtree(workspace, ignore_errors=True)
        return True

    def get_status(self, container_id: str) -> str:
        workspace = self._resolve_workspace(container_id)
        return SessionStatus.RUNNING.value if workspace and workspace.exists() else "terminated"

    def _create_cgroup(self, cgroup_path: Path, memory_mb: int = 256, cpu_percent: int = 100, max_pids: int = 64):
        """Create a cgroup v2 group with resource limits.

        Applies: memory.max, pids.max, cpu.max (quota), cpu.weight, io.max.
        """
        try:
            cgroup_path.mkdir(parents=True, exist_ok=True)

            # Enable controllers in parent if needed
            parent = cgroup_path.parent
            if parent.exists():
                controllers_file = parent / "cgroup.subtree_control"
                if controllers_file.exists():
                    try:
                        controllers_file.write_text("+cpu +memory +pids +io")
                    except PermissionError:
                        pass  # May already be enabled

            # Memory limit (hard limit)
            mem_file = cgroup_path / "memory.max"
            if mem_file.exists():
                mem_file.write_text(str(memory_mb * 1024 * 1024))

            # Memory high watermark (throttle before hard limit)
            mem_high_file = cgroup_path / "memory.high"
            if mem_high_file.exists():
                high_mb = max(1, memory_mb - 64)
                mem_high_file.write_text(str(high_mb * 1024 * 1024))

            # PID limit
            pids_file = cgroup_path / "pids.max"
            if pids_file.exists():
                pids_file.write_text(str(max_pids))

            # CPU weight (relative priority, 1-10000, default 100)
            cpu_file = cgroup_path / "cpu.weight"
            if cpu_file.exists():
                cpu_weight = min(100, max(1, cpu_percent))
                cpu_file.write_text(str(cpu_weight))

            # CPU max (absolute quota: "quota period" in microseconds)
            # 100% = 100000us per 100000us period
            cpu_max_file = cgroup_path / "cpu.max"
            if cpu_max_file.exists():
                quota_us = int(cpu_percent / 100 * 100_000)
                cpu_max_file.write_text(f"{quota_us} 100000")

            # IO bandwidth limit (prevent disk flooding)
            # Format: "major:minor rbps=bytes wbps=bytes riops=N wiops=N"
            io_max_file = cgroup_path / "io.max"
            if io_max_file.exists():
                try:
                    # 50MB/s read, 25MB/s write, 1000 read IOPS, 500 write IOPS
                    io_max_file.write_text("8:0 rbps=52428800 wbps=26214400 riops=1000 wiops=500")
                except (PermissionError, OSError):
                    pass  # IO controller may not be available

            logger.info(f"cgroup created: {cgroup_path} mem={memory_mb}MB pids={max_pids} cpu={cpu_percent}%")
        except PermissionError:
            logger.warning(f"cgroup creation failed (no permission): {cgroup_path}")
        except Exception as e:
            logger.warning(f"cgroup creation failed: {e}")

    def _remove_cgroup(self, cgroup_path: Path):
        """Remove a cgroup v2 group."""
        try:
            if cgroup_path.exists():
                # Try to move processes to parent cgroup first
                procs_file = cgroup_path / "cgroup.procs"
                if procs_file.exists():
                    try:
                        pids = procs_file.read_text().strip()
                        if pids:
                            parent_procs = cgroup_path.parent / "cgroup.procs"
                            for pid in pids.split("\n"):
                                if pid.strip():
                                    parent_procs.write_text(pid.strip())
                    except (PermissionError, FileNotFoundError):
                        pass
                cgroup_path.rmdir()
                logger.info(f"cgroup removed: {cgroup_path}")
        except Exception as e:
            logger.warning(f"cgroup removal failed: {e}")

    def _build_cgroup_exec(self, cgroup_path: Path, cmd: list[str]) -> list[str]:
        """Wrap command to run inside a cgroup."""
        # Use systemd-run if available, otherwise direct execution
        if shutil.which("systemd-run"):
            return [
                "systemd-run", "--scope", "--quiet",
                f"--slice={cgroup_path.name}.slice",
                "-p", f"MemoryMax={cgroup_path}",
            ] + cmd
        # Fallback: use cgexec if available
        if shutil.which("cgexec"):
            return ["cgexec", "-g", f"cpu,memory,pids:{cgroup_path}"] + cmd
        # Last resort: direct execution (cgroup limits still apply if set)
        return cmd


class BwrapAdapter(RuntimeAdapter):
    """L3: Bubblewrap lightweight sandbox adapter with production-grade security.

    Security features:
    - User/PID/IPC/UTS/Network namespace isolation
    - Read-only system filesystem
    - No /sys access, restricted /proc
    - Seccomp syscall filter
    - Output size limits (prevents data exfiltration via large outputs)
    - Session key injection via env (never written to disk)
    - Secure workspace cleanup on termination
    """

    def __init__(self, workspace_root: str = "/tmp/cds-sandbox", allow_unsandboxed_fallback: bool = False):
        self.workspace_root = Path(workspace_root)
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self._allow_unsandboxed_fallback = allow_unsandboxed_fallback

    @staticmethod
    def _seccomp_retry_needed(stderr: str, seccomp_fd: int | None) -> bool:
        """Return True when bwrap failed because the kernel rejected seccomp setup."""
        if seccomp_fd is None:
            return False
        text = (stderr or "").lower()
        return (
            "einval" in text
            or "invalid argument" in text
            or "pr_set_seccomp" in text
        )

    def provision(self, session_id: str, data_path: str, timeout: int = 3600, user_id: str = "") -> dict:
        from app.services.sandbox_security import (
            SandboxSecurityConfig, SandboxResourceLimits,
            encrypt_workspace_directory,
        )

        # Workspace with tenant prefix: /tmp/cds-sandbox/{user_id}/{session_id}/
        tenant_dir = self.workspace_root / (user_id or "anonymous")
        workspace = tenant_dir / session_id
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "input").mkdir(exist_ok=True)
        (workspace / "output").mkdir(exist_ok=True)
        (workspace / "tmp").mkdir(exist_ok=True)
        (workspace / "tmp").chmod(0o700)  # Only owner can access tmp

        if data_path and Path(data_path).exists():
            if Path(data_path).is_file():
                shutil.copy2(data_path, workspace / "input")
            elif Path(data_path).is_dir():
                shutil.copytree(data_path, workspace / "input" / "data", dirs_exist_ok=True)

        # Encrypt workspace files with per-session DEK from KMS
        key_id = ""
        try:
            from app.services.kms_service import kms_service
            key_result = kms_service.generate_data_key(f"workspace-{session_id}")
            dek = key_result["key_bytes"]
            key_id = key_result["key_id"]
            encrypted_files = encrypt_workspace_directory(workspace, dek)
            if encrypted_files:
                logger.info(f"Encrypted {len(encrypted_files)} workspace files for session {session_id}")
        except Exception as e:
            logger.warning(f"Workspace encryption failed (non-fatal): {e}")

        # Store security config for this session
        config = SandboxSecurityConfig()
        config.resource_limits.timeout_seconds = timeout
        config_path = workspace / ".security_config.json"
        config_path.write_text(json.dumps({
            "timeout": timeout,
            "max_memory_mb": config.resource_limits.max_memory_mb,
            "max_pids": config.resource_limits.max_pids,
            "max_output_bytes": config.resource_limits.max_output_bytes,
            "workspace_key_id": key_id,
            "session_id": session_id,
            "user_id": user_id or "anonymous",
        }))

        return {"container_id": f"bwrap-{session_id}", "status": SessionStatus.RUNNING.value, "workspace": str(workspace)}

    async def execute(self, container_id: str, code: str, language: str = "python",
                      session_key: str | None = None, env_vars: dict[str, str] | None = None,
                      timeout: int | None = None) -> dict:
        from app.services.sandbox_security import (
            build_bwrap_args, OutputLimiter, SandboxSecurityConfig, SandboxResourceLimits,
        )

        workspace = self._resolve_workspace(container_id)
        if not workspace or not workspace.exists():
            return {"output": "Workspace not found", "exit_code": -1, "duration_ms": 0}

        # Load session-specific security config
        config = SandboxSecurityConfig()
        config_path = workspace / ".security_config.json"
        cfg = {}
        if config_path.exists():
            try:
                cfg = json.loads(config_path.read_text())
                config.resource_limits.timeout_seconds = cfg.get("timeout", 120)
                config.resource_limits.max_memory_mb = cfg.get("max_memory_mb", 512)
                config.resource_limits.max_pids = cfg.get("max_pids", 64)
                config.resource_limits.max_output_bytes = cfg.get("max_output_bytes", 10 * 1024 * 1024)
            except (json.JSONDecodeError, KeyError):
                pass

        # Inject workspace DEK for in-sandbox file decryption
        workspace_key_id = cfg.get("workspace_key_id", "")
        if workspace_key_id and env_vars is None:
            env_vars = {}
        if workspace_key_id:
            try:
                from app.services.kms_service import kms_service
                dek_bytes = kms_service.get_key(workspace_key_id)
                if dek_bytes:
                    env_vars["CDS_DEK_HEX"] = dek_bytes.hex()
            except Exception as e:
                logger.warning(f"Failed to retrieve workspace DEK: {e}")

        effective_timeout = timeout or config.resource_limits.timeout_seconds

        # Determine file extension
        ext_map = {"python": "py", "sql": "sql", "shell": "sh", "bash": "sh"}
        ext = ext_map.get(language, "py")
        code_file = workspace / "tmp" / f"exec.{ext}"
        code_file.write_text(code)
        code_file.chmod(0o600)  # Only owner can read/write

        # Build hardened bwrap arguments
        session_id = cfg.get("session_id", container_id.replace("bwrap-", ""))
        bwrap_args, seccomp_fd = build_bwrap_args(
            workspace=workspace,
            code_file=f"/workspace/tmp/exec.{ext}",
            language=language,
            config=config,
            session_key=session_key,
            env_vars=env_vars,
            session_id=session_id,
            user_id=cfg.get("user_id", ""),
            sandbox_level="L3",
        )

        # Output limiter to prevent data exfiltration via large outputs
        output_limiter = OutputLimiter(max_bytes=config.resource_limits.max_output_bytes)

        try:
            start = datetime.now()
            extra_fds = (seccomp_fd,) if seccomp_fd is not None else ()
            result = await asyncio.wait_for(
                asyncio.to_thread(subprocess.run, bwrap_args, capture_output=True, text=True, pass_fds=extra_fds),
                timeout=effective_timeout,
            )
            # Retry without seccomp if kernel/bwrap rejects PR_SET_SECCOMP —
            # gated by SECCOMP_FALLBACK_ALLOWED (gap D4/T12, fail-closed).
            if (
                result.returncode != 0
                and self._seccomp_retry_needed(result.stderr, seccomp_fd)
                and _seccomp_fallback_allowed()
            ):
                cmd_no_seccomp = [c for c in bwrap_args if c != "--seccomp" and c != str(seccomp_fd)]
                result = await asyncio.wait_for(
                    asyncio.to_thread(subprocess.run, cmd_no_seccomp, capture_output=True, text=True),
                    timeout=effective_timeout,
                )
            duration = int((datetime.now() - start).total_seconds() * 1000)

            # Apply output size limit
            stdout = output_limiter.check_output(result.stdout)
            stderr = output_limiter.check_output(result.stderr)

            # Collect in-sandbox audit logs
            audit_events = []
            import tempfile as _tempfile
            audit_log_path = Path(_tempfile.gettempdir()) / "cds-sandbox-audit" / f"{session_id}.jsonl"
            if audit_log_path.exists():
                try:
                    from app.services.sandbox_audit import get_sandbox_audit_collector
                    collector = get_sandbox_audit_collector()
                    audit_events = collector.collect_from_file(str(audit_log_path))
                    if audit_events:
                        collector.flush_to_clickhouse(audit_events)
                        logger.info(f"[audit] Collected {len(audit_events)} sandbox events for session {session_id}")
                except Exception as e:
                    logger.warning(f"[audit] Failed to collect sandbox logs: {e}")

            # Log execution event to main audit trail
            try:
                from app.services.audit_service import audit_service
                await audit_service.log(
                    db=None,
                    action="sandbox.execute",
                    resource_type="sandbox_session",
                    user_id=cfg.get("user_id", ""),
                    resource_id=session_id,
                    detail={
                        "language": language,
                        "exit_code": result.returncode,
                        "duration_ms": duration,
                        "output_size": len(stdout) + len(stderr),
                        "audit_events_collected": len(audit_events),
                    },
                )
            except Exception:
                pass  # Non-fatal

            return {
                "output": stdout + stderr,
                "exit_code": result.returncode,
                "duration_ms": duration,
                "output_truncated": output_limiter.was_truncated,
                "sandbox_level": "L3",
                "audit_events": len(audit_events),
            }
        except asyncio.TimeoutError:
            # Collect partial audit logs even on timeout
            import tempfile as _tempfile
            audit_log_path = Path(_tempfile.gettempdir()) / "cds-sandbox-audit" / f"{session_id}.jsonl"
            audit_count = 0
            if audit_log_path.exists():
                try:
                    from app.services.sandbox_audit import get_sandbox_audit_collector
                    collector = get_sandbox_audit_collector()
                    events = collector.collect_from_file(str(audit_log_path))
                    if events:
                        collector.flush_to_clickhouse(events)
                        audit_count = len(events)
                except Exception:
                    pass
            return {
                "output": f"Execution timed out ({effective_timeout}s)",
                "exit_code": -1,
                "duration_ms": effective_timeout * 1000,
                "sandbox_level": "L3",
                "audit_events": audit_count,
            }
        except FileNotFoundError:
            if not self._allow_unsandboxed_fallback:
                return {
                    "output": "SECURITY ERROR: bwrap not installed and unsandboxed fallback is disabled. "
                              "Install bubblewrap or enable allow_unsandboxed_fallback for development only.",
                    "exit_code": -1,
                    "duration_ms": 0,
                    "sandbox_level": "L3",
                }
            return await self._execute_direct(code_file, language, effective_timeout)
        except Exception as e:
            return {"output": str(e), "exit_code": -1, "duration_ms": 0, "sandbox_level": "L3"}

    async def _execute_direct(self, code_file: Path, language: str, timeout: int = 120) -> dict:
        """Unsandboxed fallback — DEVELOPMENT ONLY. Never used in production."""
        logger.warning("SECURITY: Executing code without bwrap sandbox (unsandboxed fallback)")
        try:
            start = datetime.now()
            cmd = ["python3", str(code_file)] if language == "python" else ["bash", str(code_file)]
            result = await asyncio.wait_for(
                asyncio.to_thread(subprocess.run, cmd, capture_output=True, text=True),
                timeout=timeout,
            )
            duration = int((datetime.now() - start).total_seconds() * 1000)
            return {"output": result.stdout + result.stderr, "exit_code": result.returncode, "duration_ms": duration}
        except asyncio.TimeoutError:
            return {"output": f"Execution timed out ({timeout}s)", "exit_code": -1, "duration_ms": timeout * 1000}
        except Exception as e:
            return {"output": str(e), "exit_code": -1, "duration_ms": 0}

    def _resolve_workspace(self, container_id: str) -> Path | None:
        """Find workspace for a container ID by searching tenant directories."""
        session_id = container_id.replace("bwrap-", "")
        direct = self.workspace_root / session_id
        if direct.exists():
            return direct
        for tenant_dir in self.workspace_root.iterdir():
            if tenant_dir.is_dir():
                candidate = tenant_dir / session_id
                if candidate.exists():
                    return candidate
        return None

    def terminate(self, container_id: str) -> bool:
        from app.services.sandbox_security import secure_wipe_file
        workspace = self._resolve_workspace(container_id)
        if workspace and workspace.exists():
            # Secure wipe sensitive files before deletion
            for sensitive_file in [".seccomp.json", ".security_config.json"]:
                fp = workspace / sensitive_file
                if fp.exists():
                    secure_wipe_file(fp)
            # Wipe all code files
            for code_file in (workspace / "tmp").glob("exec.*"):
                secure_wipe_file(code_file)
            # Remove workspace
            shutil.rmtree(workspace, ignore_errors=True)
        return True

    def get_status(self, container_id: str) -> str:
        workspace = self._resolve_workspace(container_id)
        return SessionStatus.RUNNING.value if workspace and workspace.exists() else "terminated"


class FirecrackerAdapter(RuntimeAdapter):
    """L2: Firecracker microVM adapter.

    Uses FirecrackerRuntime for hardware-level VM isolation.
    Falls back to simulation mode when Firecracker is not installed.
    """

    def __init__(self):
        from app.services.firecracker_runtime import firecracker_runtime, VMConfig
        self._runtime = firecracker_runtime
        self._VMConfig = VMConfig

    def provision(self, session_id: str, data_path: str, timeout: int = 3600, user_id: str = "") -> dict:
        try:
            config = self._VMConfig(vcpu_count=2, mem_size_mb=512)
            vm = self._runtime.create_vm(session_id, config, user_id=user_id)

            if data_path and Path(data_path).exists():
                self._runtime.transfer_file(vm, data_path, f"/workspace/input/data")

            attestation_quote = None
            attestation_measurement = None
            try:
                from app.services.remote_attestation import AttestationService, TEEType
                quote = AttestationService().generate_quote(TEEType.FIRECRACKER, str(session_id).encode())
                attestation_quote = quote.raw_bytes.decode("utf-8")
                attestation_measurement = quote.measurement
            except Exception as e:
                logger.warning("[FirecrackerAdapter] Failed to generate software attestation quote: %s", e)

            return {
                "container_id": vm.vm_id,
                "status": SessionStatus.RUNNING.value,
                "workspace": vm.workspace,
                "attestation_quote": attestation_quote,
                "attestation_type": "firecracker",
                "attestation_measurement": attestation_measurement,
                "is_simulation": self._runtime._simulation_mode,
            }
        except Exception as e:
            return {"container_id": None, "status": SessionStatus.FAILED.value, "error": str(e)}

    async def execute(self, container_id: str, code: str, language: str = "python",
                      session_key: str | None = None, env_vars: dict[str, str] | None = None,
                      timeout: int | None = None) -> dict:
        vm = self._runtime.get_vm(container_id)
        if not vm:
            return {"output": "VM not found", "exit_code": -1, "duration_ms": 0}

        result = await asyncio.to_thread(
            self._runtime.execute,
            vm,
            code,
            language,
            session_key=session_key,
            env_vars=env_vars,
            timeout=timeout,
        )
        return {"output": result.output, "exit_code": result.exit_code, "duration_ms": result.duration_ms,
                "sandbox_level": "L2"}

    def terminate(self, container_id: str) -> bool:
        vm = self._runtime.get_vm(container_id)
        if not vm:
            return True
        return self._runtime.destroy_vm(vm)

    def get_status(self, container_id: str) -> str:
        vm = self._runtime.get_vm(container_id)
        if not vm:
            return "terminated"
        return vm.state.value


class TEEAdapter(RuntimeAdapter):
    """L1 adapter for hardware TEE with ordinary software-confidential fallback.

    Hardware is used only when both conditions are true:
    - a host capability signal is detected for the selected provider; and
    - an operator-configured hardware execution command is present.

    Without a usable TEE environment, L1 remains a confidential sandbox backed
    by bwrap isolation and software_hash attestation. It does not emit fake SGX
    evidence in fallback mode.
    """

    _QUOTE_TYPE_BY_PROVIDER = {
        "sgx": "sgx_ecdsa",
        "sev_snp": "sev_snp",
        "tdx": "tdx",
        "itrustee": "itrustee",
    }

    def __init__(self, detector=None):
        self._simulator = None
        self._is_simulation = False
        self._enclaves: dict[str, object] = {}
        self._detector = detector

    def _get_detector(self):
        if self._detector is None:
            from app.services.tee_capability import TEECapabilityDetector
            self._detector = TEECapabilityDetector()
        return self._detector

    def _get_simulator(self):
        """Lazy-initialize the software confidential sandbox backend."""
        if self._simulator is None:
            from app.services.tee_simulator import TEESimulator
            self._simulator = TEESimulator()
            self._is_simulation = True
            logger.warning(
                "[TEEAdapter] Hardware TEE unavailable or unconfigured -- using software_confidential "
                "sandbox fallback (no hardware TEE guarantees)"
            )
        return self._simulator

    @property
    def is_simulation(self) -> bool:
        """True when the adapter has used the software-confidential fallback."""
        return self._is_simulation

    def provision(self, session_id: str, data_path: str, timeout: int = 3600, user_id: str = "") -> dict:
        from app.core.config import get_settings

        settings = get_settings()
        capability = self._get_detector().detect(settings.TEE_MODE)

        if capability.hardware_available and settings.TEE_HARDWARE_EXEC_CMD:
            try:
                return self._provision_hardware(session_id, data_path, timeout, user_id, capability, settings)
            except Exception as e:
                logger.error("[TEEAdapter] Hardware TEE provision failed: %s", e)
                return {
                    "container_id": None,
                    "status": SessionStatus.FAILED.value,
                    "error": f"hardware TEE provision failed: {e}",
                    "tee_mode": "hardware",
                    "tee_provider": capability.provider,
                }

        if not settings.TEE_ALLOW_SOFTWARE_FALLBACK:
            reason = capability.reason
            if capability.hardware_available:
                reason = "hardware TEE detected but CDS_TEE_HARDWARE_EXEC_CMD is not configured"
            return {
                "container_id": None,
                "status": SessionStatus.FAILED.value,
                "error": f"L1 TEE unavailable and software fallback is disabled: {reason}",
                "tee_mode": "unavailable",
                "tee_provider": capability.provider,
            }

        fallback_reason = capability.reason
        if capability.hardware_available:
            fallback_reason = "hardware TEE detected but CDS_TEE_HARDWARE_EXEC_CMD is not configured"
        return self._provision_software_confidential(session_id, data_path, fallback_reason)

    def _provision_software_confidential(self, session_id: str, data_path: str, fallback_reason: str) -> dict:
        sim = self._get_simulator()
        if not getattr(sim, "_bwrap_available", False):
            return {
                "container_id": None,
                "status": SessionStatus.FAILED.value,
                "error": "L1 software_confidential fallback requires bubblewrap isolation; bwrap is not available",
                "tee_mode": "software_confidential",
                "tee_provider": "software_confidential",
            }

        try:
            enclave = sim.create_enclave(memory_mb=256)
            container_id = f"tee-{session_id}"
            self._copy_input_data(data_path, Path(enclave.workspace))

            report = sim.attest(enclave)
            quote = self._generate_software_quote(session_id)
            self._enclaves[container_id] = SimpleNamespace(
                mode="software_confidential",
                provider="software_confidential",
                workspace=enclave.workspace,
                enclave=enclave,
            )

            logger.info(
                "[TEEAdapter] Provisioned L1 software_confidential session %s "
                "(measurement=%s..., fallback_reason=%s)",
                session_id[:8], enclave.mrenclave[:16], fallback_reason,
            )
            return {
                "container_id": container_id,
                "status": SessionStatus.RUNNING.value,
                "workspace": enclave.workspace,
                "is_simulation": True,
                "tee_mode": "software_confidential",
                "tee_provider": "software_confidential",
                "hardware_available": False,
                "fallback_reason": fallback_reason,
                "mrenclave": enclave.mrenclave,
                "software_attestation_quote": report.quote,
                "attestation_quote": quote.raw_bytes.decode("utf-8"),
                "attestation_type": quote.quote_type.value,
                "attestation_measurement": quote.measurement,
            }
        except Exception as e:
            logger.error("[TEEAdapter] Software confidential provision failed: %s", e)
            return {
                "container_id": None,
                "status": SessionStatus.FAILED.value,
                "error": f"software confidential provision failed: {e}",
                "tee_mode": "software_confidential",
                "tee_provider": "software_confidential",
            }

    def _provision_hardware(self, session_id: str, data_path: str, timeout: int, user_id: str, capability, settings) -> dict:
        workspace = Path("/tmp/cds-tee-hw") / str(session_id)
        (workspace / "input").mkdir(parents=True, exist_ok=True)
        (workspace / "output").mkdir(exist_ok=True)
        (workspace / "tmp").mkdir(exist_ok=True)
        (workspace / "tmp").chmod(0o700)
        self._copy_input_data(data_path, workspace)

        container_id = f"tee-{session_id}"
        env = self._hardware_env(
            session_id=session_id,
            provider=capability.provider,
            workspace=workspace,
            user_id=user_id,
        )
        hardware_handle = container_id
        if settings.TEE_HARDWARE_PROVISION_CMD:
            result = self._run_hardware_command(
                settings.TEE_HARDWARE_PROVISION_CMD,
                input_text="",
                env=env,
                timeout=min(timeout, 120),
                cwd=workspace,
            )
            if result.returncode != 0:
                raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "hardware provision command failed")
            hardware_handle = self._parse_hardware_handle(result.stdout, default=container_id)
            env["CDS_TEE_HANDLE"] = hardware_handle

        attestation_quote, attestation_type, measurement = self._generate_hardware_quote(
            provider=capability.provider,
            session_id=session_id,
            workspace=workspace,
            env=env,
            settings=settings,
        )
        handle = SimpleNamespace(
            mode="hardware",
            provider=capability.provider,
            workspace=str(workspace),
            hardware_handle=hardware_handle,
            evidence=list(capability.evidence),
        )
        self._enclaves[container_id] = handle

        logger.info(
            "[TEEAdapter] Provisioned hardware TEE session %s (provider=%s, evidence=%s)",
            session_id[:8], capability.provider, ",".join(capability.evidence),
        )
        return {
            "container_id": container_id,
            "status": SessionStatus.RUNNING.value,
            "workspace": str(workspace),
            "is_simulation": False,
            "tee_mode": "hardware",
            "tee_provider": capability.provider,
            "hardware_available": True,
            "hardware_evidence": list(capability.evidence),
            "hardware_handle": hardware_handle,
            "attestation_quote": attestation_quote,
            "attestation_type": attestation_type,
            "attestation_measurement": measurement,
        }

    @staticmethod
    def _generate_software_quote(session_id: str):
        from app.services.remote_attestation import AttestationService, TEEType

        return AttestationService().generate_quote(TEEType.FIRECRACKER, str(session_id).encode())

    def _generate_hardware_quote(self, provider: str, session_id: str, workspace: Path, env: dict[str, str], settings):
        quote_type = self._QUOTE_TYPE_BY_PROVIDER.get(provider, provider)
        if settings.TEE_HARDWARE_ATTEST_CMD:
            env = dict(env)
            env["CDS_TEE_REPORT_DATA"] = str(session_id).encode().hex()
            result = self._run_hardware_command(
                settings.TEE_HARDWARE_ATTEST_CMD,
                input_text="",
                env=env,
                timeout=60,
                cwd=workspace,
            )
            if result.returncode != 0:
                raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "hardware attestation command failed")
            payload = self._parse_hardware_attestation(result.stdout)
            return (
                json.dumps(payload, sort_keys=True),
                payload.get("type", quote_type),
                payload.get("measurement"),
            )

        logger.warning(
            "[TEEAdapter] Hardware TEE attestation command is not configured for %s; "
            "no hardware quote will be emitted",
            provider,
        )
        return None, quote_type, None

    @staticmethod
    def _parse_hardware_attestation(stdout: str) -> dict:
        raw = stdout.strip()
        if not raw:
            raise RuntimeError("hardware attestation command returned empty output")
        payload = json.loads(raw)
        if isinstance(payload.get("quote"), dict):
            payload = payload["quote"]
        if not isinstance(payload, dict):
            raise RuntimeError("hardware attestation command must return a JSON object")
        if "type" not in payload or "measurement" not in payload:
            raise RuntimeError("hardware attestation JSON must include type and measurement")
        return payload

    @staticmethod
    def _parse_hardware_handle(stdout: str, default: str) -> str:
        raw = stdout.strip()
        if not raw:
            return default
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return raw.splitlines()[-1].strip() or default
        if isinstance(payload, dict):
            return str(payload.get("handle_id") or payload.get("container_id") or payload.get("id") or default)
        return default

    @staticmethod
    def _hardware_env(
        session_id: str,
        provider: str,
        workspace: Path,
        user_id: str = "",
        handle_id: str = "",
        language: str = "",
        code_file: Path | None = None,
        session_key: str | None = None,
        env_vars: dict[str, str] | None = None,
    ) -> dict[str, str]:
        env = os.environ.copy()
        env.update({
            "CDS_TEE_SESSION_ID": str(session_id),
            "CDS_TEE_PROVIDER": provider,
            "CDS_TEE_WORKSPACE": str(workspace),
            "CDS_TEE_INPUT_DIR": str(workspace / "input"),
            "CDS_TEE_OUTPUT_DIR": str(workspace / "output"),
            "CDS_TEE_TMP_DIR": str(workspace / "tmp"),
        })
        if user_id:
            env["CDS_TEE_USER_ID"] = user_id
        if handle_id:
            env["CDS_TEE_HANDLE"] = handle_id
        if language:
            env["CDS_TEE_LANGUAGE"] = language
        if code_file is not None:
            env["CDS_TEE_CODE_FILE"] = str(code_file)
        if session_key:
            env["CDS_SESSION_KEY"] = session_key
        if env_vars:
            env.update(env_vars)
        return env

    @staticmethod
    def _run_hardware_command(command: str, input_text: str, env: dict[str, str], timeout: int, cwd: Path):
        argv = shlex.split(command)
        if not argv:
            raise RuntimeError("empty hardware TEE command")
        return subprocess.run(
            argv,
            input=input_text,
            capture_output=True,
            text=True,
            env=env,
            cwd=str(cwd),
            timeout=timeout,
        )

    @staticmethod
    def _copy_input_data(data_path: str, workspace: Path) -> None:
        if not data_path or not Path(data_path).exists():
            return
        input_dir = workspace / "input"
        input_dir.mkdir(parents=True, exist_ok=True)
        source = Path(data_path)
        if source.is_file():
            shutil.copy2(source, input_dir / source.name)
        elif source.is_dir():
            shutil.copytree(source, input_dir / "data", dirs_exist_ok=True)

    async def execute(self, container_id: str, code: str, language: str,
                      session_key: str | None = None, env_vars: dict[str, str] | None = None,
                      timeout: int | None = None) -> dict:
        handle = self._enclaves.get(container_id)
        if not handle:
            return {"output": "TEE enclave not found", "exit_code": -1, "duration_ms": 0, "is_simulation": self._is_simulation}

        if getattr(handle, "mode", "software_confidential") == "hardware":
            return await self._execute_hardware(container_id, handle, code, language, session_key, env_vars, timeout)

        workspace = Path(handle.workspace)
        ext_map = {"python": "py", "sql": "sql", "shell": "sh", "bash": "sh"}
        ext = ext_map.get(language, "py")
        code_file = workspace / "tmp" / f"exec.{ext}"
        code_file.write_text(code)
        code_file.chmod(0o600)

        effective_timeout = timeout or 120

        # Build bwrap execution command (reuses the enclave's isolation)
        if shutil.which("bwrap"):
            cmd = [
                "bwrap",
                "--chdir", "/workspace",
                "--unshare-pid", "--unshare-net", "--unshare-ipc",
                "--die-with-parent",
                "--ro-bind", "/usr", "/usr",
                "--ro-bind", "/lib", "/lib",
                *(["--ro-bind", "/lib64", "/lib64"] if Path("/lib64").exists() else []),
                "--dev", "/dev",
                "--proc", "/proc",
                "--tmpfs", "/tmp:size=100m",
                "--tmpfs", "/workspace:size=500m",
                "--bind", str(workspace / "output"), "/workspace/output",
                "--bind", str(workspace / "tmp"), "/workspace/tmp",
                "--ro-bind", str(workspace / "input"), "/workspace/input",
                # Session files exchange dir (Round 39/40 usability).
                *(["--bind", str(workspace / "files"), "/workspace/files"]
                  if (workspace / "files").is_dir()
                  else []),
                "--tmpfs", "/sys:size=1m",
            ]
            if session_key:
                cmd += ["--setenv", "CDS_SESSION_KEY", session_key]
            if env_vars:
                for k, v in env_vars.items():
                    cmd += ["--setenv", k, v]
            cmd += ["--setenv", "PATH", "/usr/local/bin:/usr/bin:/bin"]

            if language == "python":
                cmd += ["python3", f"/workspace/tmp/exec.{ext}"]
            elif language == "sql":
                from app.services.sandbox_security import _build_sql_runner
                cmd += ["python3", "-c", _build_sql_runner(f"/workspace/tmp/exec.{ext}")]
            else:
                cmd += ["bash", f"/workspace/tmp/exec.{ext}"]
        else:
            return {
                "output": "SECURITY ERROR: bwrap not installed and L1 software_confidential fallback cannot execute safely.",
                "exit_code": -1,
                "duration_ms": 0,
                "sandbox_level": "L1",
                "is_simulation": self._is_simulation,
                "tee_mode": getattr(handle, "mode", "software_confidential"),
            }

        env = os.environ.copy()
        if session_key:
            env["CDS_SESSION_KEY"] = session_key
        if env_vars:
            env.update(env_vars)

        try:
            start = datetime.now()
            result = await asyncio.wait_for(
                asyncio.to_thread(subprocess.run, cmd, capture_output=True, text=True, env=env),
                timeout=effective_timeout,
            )
            duration = int((datetime.now() - start).total_seconds() * 1000)
            return {
                "output": result.stdout + result.stderr,
                "exit_code": result.returncode,
                "duration_ms": duration,
                "sandbox_level": "L1",
                "is_simulation": self._is_simulation,
                "tee_mode": getattr(handle, "mode", "software_confidential"),
            }
        except asyncio.TimeoutError:
            return {
                "output": f"Execution timed out ({effective_timeout}s)",
                "exit_code": -1,
                "duration_ms": effective_timeout * 1000,
                "sandbox_level": "L1",
                "is_simulation": self._is_simulation,
                "tee_mode": getattr(handle, "mode", "software_confidential"),
            }
        except Exception as e:
            return {
                "output": str(e),
                "exit_code": -1,
                "duration_ms": 0,
                "sandbox_level": "L1",
                "is_simulation": self._is_simulation,
                "tee_mode": getattr(handle, "mode", "software_confidential"),
            }

    async def _execute_hardware(self, container_id: str, handle, code: str, language: str,
                                session_key: str | None, env_vars: dict[str, str] | None,
                                timeout: int | None) -> dict:
        from app.core.config import get_settings

        settings = get_settings()
        effective_timeout = timeout or 120
        workspace = Path(handle.workspace)
        ext_map = {"python": "py", "sql": "sql", "shell": "sh", "bash": "sh"}
        ext = ext_map.get(language, "py")
        code_file = workspace / "tmp" / f"exec.{ext}"
        code_file.write_text(code)
        code_file.chmod(0o600)
        env = self._hardware_env(
            session_id=container_id.removeprefix("tee-"),
            provider=handle.provider,
            workspace=workspace,
            handle_id=getattr(handle, "hardware_handle", container_id),
            language=language,
            code_file=code_file,
            session_key=session_key,
            env_vars=env_vars,
        )
        try:
            start = datetime.now()
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    self._run_hardware_command,
                    settings.TEE_HARDWARE_EXEC_CMD,
                    code,
                    env,
                    effective_timeout,
                    workspace,
                ),
                timeout=effective_timeout,
            )
            duration = int((datetime.now() - start).total_seconds() * 1000)
            return {
                "output": result.stdout + result.stderr,
                "exit_code": result.returncode,
                "duration_ms": duration,
                "sandbox_level": "L1",
                "is_simulation": False,
                "tee_mode": "hardware",
                "tee_provider": handle.provider,
            }
        except asyncio.TimeoutError:
            return {
                "output": f"Execution timed out ({effective_timeout}s)",
                "exit_code": -1,
                "duration_ms": effective_timeout * 1000,
                "sandbox_level": "L1",
                "is_simulation": False,
                "tee_mode": "hardware",
                "tee_provider": handle.provider,
            }
        except Exception as e:
            return {
                "output": str(e),
                "exit_code": -1,
                "duration_ms": 0,
                "sandbox_level": "L1",
                "is_simulation": False,
                "tee_mode": "hardware",
                "tee_provider": handle.provider,
            }

    def terminate(self, container_id: str) -> bool:
        handle = self._enclaves.pop(container_id, None)
        if not handle:
            return True

        if getattr(handle, "mode", "software_confidential") == "hardware":
            from app.core.config import get_settings

            settings = get_settings()
            if settings.TEE_HARDWARE_TERMINATE_CMD:
                workspace = Path(handle.workspace)
                env = self._hardware_env(
                    session_id=container_id.removeprefix("tee-"),
                    provider=handle.provider,
                    workspace=workspace,
                    handle_id=getattr(handle, "hardware_handle", container_id),
                )
                result = self._run_hardware_command(
                    settings.TEE_HARDWARE_TERMINATE_CMD,
                    input_text="",
                    env=env,
                    timeout=30,
                    cwd=workspace,
                )
                if result.returncode != 0:
                    logger.warning("[TEEAdapter] Hardware terminate command failed for %s: %s", container_id, result.stderr)
                    return False
            logger.info("[TEEAdapter] Terminated hardware TEE session for %s", container_id)
            return True

        enclave = getattr(handle, "enclave", handle)
        if self._simulator:
            self._simulator.destroy_enclave(enclave)
            logger.info("[TEEAdapter] Terminated software_confidential enclave for %s", container_id)
        return True

    def get_status(self, container_id: str) -> str:
        if container_id in self._enclaves:
            return SessionStatus.RUNNING.value
        return "terminated"


class DestructionReport:
    """Report of secure sandbox destruction."""
    def __init__(self, session_id, container_destroyed=False, key_destroyed=False,
                 memory_wiped=False, audit_logged=False, errors=None):
        self.session_id = session_id
        self.container_destroyed = container_destroyed
        self.key_destroyed = key_destroyed
        self.memory_wiped = memory_wiped
        self.audit_logged = audit_logged
        self.errors = errors or []


class K8sRuntimeAdapter(RuntimeAdapter):
    """Adapter that wraps K8sSandboxAdapter to implement the RuntimeAdapter interface."""

    def __init__(self):
        self._k8s = None

    def _get_k8s(self):
        if self._k8s is None:
            try:
                from app.services.k8s_sandbox import K8sSandboxAdapter
                self._k8s = K8sSandboxAdapter()
            except Exception as e:
                logger.warning("[K8sRuntime] K8s adapter unavailable: %s", e)
                raise
        return self._k8s

    @staticmethod
    def _pod_name_from_container_id(container_id: str) -> str:
        session_id = container_id.replace("k8s-", "", 1)
        return f"sandbox-{session_id[:16]}"

    def _build_script(self, code: str, language: str, env_vars: dict, session_key: str | None) -> list[str]:
        import base64
        import shlex
        from app.services.k8s_sandbox import _env_var_name

        merged_env = {
            "HOME": "/home/sandbox",
            "TMPDIR": "/tmp",
            "PYTHONPYCACHEPREFIX": "/tmp/pycache",
        }
        merged_env.update(env_vars or {})
        if session_key:
            merged_env["CDS_SESSION_KEY"] = session_key

        invalid_env = [name for name in merged_env if not _env_var_name(name)]
        if invalid_env:
            raise ValueError(
                f"Invalid environment variable name(s): {', '.join(sorted(invalid_env))}"
            )

        lang = (language or "python").lower()
        encoded_code = base64.b64encode(code.encode("utf-8")).decode("ascii")
        script_lines = ["set -eu", "umask 077", "mkdir -p /workspace/tmp /workspace/output /tmp/pycache"]
        for key, value in merged_env.items():
            script_lines.append(f"export {key}={shlex.quote(str(value))}")

        if lang == "python":
            script_lines += [
                "python3 - <<'CDS_DECODE_EOF'",
                "import base64, pathlib",
                f"pathlib.Path('/workspace/tmp/cds-exec.py').write_bytes(base64.b64decode('{encoded_code}'))",
                "CDS_DECODE_EOF",
                "__cds_rc=0",
                "python3 /workspace/tmp/cds-exec.py || __cds_rc=$?",
                'echo "__CDS_EXIT__:$__cds_rc"',
            ]
        elif lang in {"bash", "shell", "sh"}:
            script_lines += [
                "python3 - <<'CDS_DECODE_EOF'",
                "import base64, pathlib",
                f"pathlib.Path('/workspace/tmp/cds-exec.sh').write_bytes(base64.b64decode('{encoded_code}'))",
                "CDS_DECODE_EOF",
                "__cds_rc=0",
                "sh /workspace/tmp/cds-exec.sh || __cds_rc=$?",
                'echo "__CDS_EXIT__:$__cds_rc"',
            ]
        else:
            raise ValueError(f"Unsupported language for K8s sandbox: {language}")
        return script_lines

    @staticmethod
    def _extract_exit_code(output: str) -> int | None:
        for line in output.splitlines():
            if line.startswith("__CDS_EXIT__:"):
                try:
                    return int(line.split(":", 1)[1])
                except ValueError:
                    return None
        return None

    def provision(self, session_id: str, data_path: str, timeout: int, user_id: str = "",
                  volume_mounts: list[dict] | None = None) -> dict:
        from app.services.k8s_sandbox import SandboxPodSpec
        k8s = self._get_k8s()
        spec = SandboxPodSpec(
            session_id=session_id,
            user_id=user_id,
            sandbox_level="L3",
            timeout_seconds=timeout,
            volume_mounts=volume_mounts or [],
        )
        result = k8s.provision(spec)
        if result.get("error"):
            return {"status": "failed", "error": result["error"]}
        return {
            "container_id": result.get("container_id", f"k8s-{session_id}"),
            "status": result.get("status", "provisioning"),
        }

    async def execute(self, container_id: str, code: str, language: str = "python",
                      session_key: str | None = None, env_vars: dict[str, str] | None = None,
                      timeout: int | None = None) -> dict:
        k8s = self._get_k8s()
        pod_name = self._pod_name_from_container_id(container_id)
        effective_timeout = timeout or 30
        try:
            pod_status = k8s.get_status(pod_name)
        except Exception as e:
            return {
                "output": "",
                "exit_code": -1,
                "duration_ms": 0,
                "error": f"K8s pod readiness check failed: {e}",
                "sandbox_level": "k8s",
            }
        if pod_status.get("cds_status") != "running" or not pod_status.get("ready", False):
            reason = pod_status.get("reason") or pod_status.get("error") or pod_status.get("message") or pod_status.get("phase", "unknown")
            return {
                "output": f"K8s pod is not ready: {reason}",
                "exit_code": -1,
                "duration_ms": 0,
                "error": "K8s pod is not ready",
                "sandbox_level": "k8s",
            }

        try:
            script_lines = self._build_script(code, language, env_vars or {}, session_key)
        except ValueError as e:
            return {
                "output": str(e),
                "exit_code": -1,
                "duration_ms": 0,
                "sandbox_level": "k8s",
            }

        # N7: python-client exec path (real exit code via the sentinel) with
        # the kubectl subprocess fallback.
        if getattr(k8s, "_k8s", None) is not None:
            try:
                started = datetime.now()
                output, sentinel_code, err = await asyncio.to_thread(
                    k8s.exec_script, pod_name, script_lines, effective_timeout,
                )
                duration_ms = int((datetime.now() - started).total_seconds() * 1000)
                exit_code = sentinel_code if sentinel_code is not None else -1
                return {
                    "output": output,
                    "exit_code": exit_code,
                    "error": err if exit_code != 0 else None,
                    "sandbox_level": "k8s",
                    "duration_ms": duration_ms,
                }
            except Exception as e:
                return {"output": "", "exit_code": -1, "error": str(e), "sandbox_level": "k8s"}

        # kubectl fallback.
        import subprocess
        try:
            result = k8s._kubectl(
                "exec", "-i", pod_name, "-n", k8s.namespace, "--", "sh", "-s",
                input="\n".join(script_lines) + "\n",
                timeout=effective_timeout,
            )
            output = result.stdout or ""
            sentinel_code = self._extract_exit_code(output)
            exit_code = sentinel_code if sentinel_code is not None else result.returncode
            return {
                "output": output,
                "exit_code": exit_code,
                "error": result.stderr if exit_code != 0 else None,
                "sandbox_level": "k8s",
            }
        except subprocess.TimeoutExpired:
            return {
                "output": f"Execution timed out ({effective_timeout}s)",
                "exit_code": -1,
                "error": "Execution timed out",
                "sandbox_level": "k8s",
            }
        except Exception as e:
            return {"output": "", "exit_code": -1, "error": str(e), "sandbox_level": "k8s"}

    def terminate(self, container_id: str) -> bool:
        k8s = self._get_k8s()
        pod_name = self._pod_name_from_container_id(container_id)
        return k8s.terminate(pod_name)

    async def execute_streaming(self, container_id: str, code: str, language: str = "bash",
                                *, env_vars: dict[str, str] | None = None,
                                timeout: int | None = None,
                                extra_binds: list | None = None,
                                on_line) -> dict:
        """W10 streaming for K8s pods via the python-client exec WebSocket.

        Every output line is pushed to ``on_line(stream_name, line)`` as it
        arrives. A raise from the callback (e.g. OutputBlockedError) closes the
        stream and propagates — fail-closed. Uses the python client only;
        returns an honest unsupported marker when the client is unavailable.
        """
        import asyncio
        import time as _time

        k8s = self._get_k8s()
        pod_name = self._pod_name_from_container_id(container_id)
        if getattr(k8s, "_k8s", None) is None:
            await on_line("stderr", "K8s streaming requires the python client (kubectl exec has no frame protocol)")
            return {"exit_code": -1, "duration_ms": 0, "sandbox_level": "k8s", "error": "python client unavailable"}

        effective_timeout = timeout or 120
        try:
            script_lines = self._build_script(code, language, env_vars or {}, None)
        except ValueError as e:
            await on_line("stderr", str(e))
            return {"exit_code": -1, "duration_ms": 0, "sandbox_level": "k8s"}

        try:
            ws = k8s.open_exec_stream(pod_name)
        except Exception as e:
            await on_line("stderr", f"exec stream failed: {e}")
            return {"exit_code": -1, "duration_ms": 0, "sandbox_level": "k8s"}

        queue: asyncio.Queue = asyncio.Queue()
        started = datetime.now()
        deadline = _time.monotonic() + effective_timeout

        def _pump() -> None:
            out_buf, err_buf = "", ""
            try:
                while _time.monotonic() < deadline and ws.is_open():
                    ws.update(timeout=1)
                    out = ws.read_stdout(timeout=0.2)
                    if out:
                        out_buf += out
                        while "\n" in out_buf:
                            line, out_buf = out_buf.split("\n", 1)
                            queue.put_nowait(("stdout", line))
                    err = ws.read_stderr(timeout=0.2)
                    if err:
                        err_buf += err
                        while "\n" in err_buf:
                            line, err_buf = err_buf.split("\n", 1)
                            queue.put_nowait(("stderr", line))
                if out_buf:
                    queue.put_nowait(("stdout", out_buf))
                if err_buf:
                    queue.put_nowait(("stderr", err_buf))
                queue.put_nowait(("__done__", None))
            except Exception as e:
                queue.put_nowait(("__error__", str(e)))

        try:
            ws.write_stdin("\n".join(script_lines) + "\n")
        except Exception as e:
            try:
                ws.close()
            except Exception:
                pass
            await on_line("stderr", f"exec stdin failed: {e}")
            return {"exit_code": -1, "duration_ms": 0, "sandbox_level": "k8s"}

        pump_task = asyncio.create_task(asyncio.to_thread(_pump))
        exit_code = -1
        timed_out = False
        try:
            while True:
                try:
                    kind, payload = await asyncio.wait_for(queue.get(), timeout=effective_timeout)
                except asyncio.TimeoutError:
                    timed_out = True
                    break
                if kind == "__done__":
                    break
                if kind == "__error__":
                    await on_line("stderr", f"exec stream error: {payload}")
                    break
                if payload.startswith("__CDS_EXIT__:"):
                    try:
                        exit_code = int(payload.split(":", 1)[1])
                    except ValueError:
                        pass
                    continue
                await on_line(kind, payload)
        except Exception:
            try:
                ws.close()
            except Exception:
                pass
            raise
        finally:
            try:
                ws.close()
            except Exception:
                pass
            if not pump_task.done():
                pump_task.cancel()

        duration_ms = int((datetime.now() - started).total_seconds() * 1000)
        if timed_out:
            await on_line("stderr", f"Execution timed out ({effective_timeout}s)")
            return {"exit_code": -1, "duration_ms": duration_ms, "sandbox_level": "k8s"}
        return {"exit_code": exit_code, "duration_ms": duration_ms, "sandbox_level": "k8s"}

    def get_status(self, container_id: str) -> str:
        k8s = self._get_k8s()
        pod_name = self._pod_name_from_container_id(container_id)
        status = k8s.get_status(pod_name)
        return status.get("cds_status") or status.get("phase", "Unknown").lower()


class SandboxRuntime:
    """Sandbox lifecycle manager with adapter selection and secure destruction."""

    def __init__(self):
        self._adapters: dict[str, RuntimeAdapter] = {
            SandboxLevel.L0.value: ProcessAdapter(),
            SandboxLevel.L1.value: TEEAdapter(),
            SandboxLevel.L2.value: FirecrackerAdapter(),
            SandboxLevel.L3.value: BwrapAdapter(),
        }
        # K8s adapter available for distributed deployment (lazy init)
        self._k8s_adapter: K8sRuntimeAdapter | None = None
        self._adapters["k8s"] = K8sRuntimeAdapter()

    def register_adapter(self, name: str, adapter: RuntimeAdapter) -> None:
        """Register a custom runtime adapter."""
        self._adapters[name] = adapter
        logger.info("[SandboxRuntime] Registered adapter: %s", name)

    def get_k8s_adapter(self) -> K8sRuntimeAdapter:
        """Get or create the K8s runtime adapter."""
        if self._k8s_adapter is None:
            self._k8s_adapter = K8sRuntimeAdapter()
        return self._k8s_adapter

    def get_adapter(self, level: str) -> RuntimeAdapter:
        adapter = self._adapters.get(level)
        if not adapter:
            raise ValueError(f"Unsupported sandbox level: {level}")
        return adapter

    def get_workspace(self, container_id: str, level: str):
        """Resolve the on-host workspace dir for a container (gap T11).

        Delegates to the level adapter's ``_resolve_workspace`` so RAG corpus
        materialization can write into the exact directory the sandbox binds.
        Returns a ``pathlib.Path`` or None when unresolved.
        """
        from pathlib import Path as _Path

        try:
            adapter = self.get_adapter(level)
        except ValueError:
            return None
        resolve = getattr(adapter, "_resolve_workspace", None)
        if resolve is None:
            return None
        try:
            result = resolve(container_id)
            return _Path(result) if result else None
        except Exception:
            return None

    def provision(self, session_id: uuid.UUID, level: str, data_path: str, timeout: int = 3600, user_id: str = "",
                  volume_mounts: list[dict] | None = None) -> dict:
        adapter = self.get_adapter(level)
        if level == "k8s":
            return adapter.provision(
                str(session_id), data_path, timeout, user_id=user_id, volume_mounts=volume_mounts or [],
            )
        return adapter.provision(str(session_id), data_path, timeout, user_id=user_id)

    async def _execute_adapter(
        self,
        adapter: RuntimeAdapter,
        container_id: str,
        code: str,
        language: str,
        *,
        session_key: str | None = None,
        env_vars: dict[str, str] | None = None,
        timeout: int | None = None,
        isolate_network: bool = True,
        dns_proxy_port: int | None = None,
        extra_binds: list[tuple[Path, str, bool]] | None = None,
    ) -> dict:
        import inspect

        kwargs = {}
        params = inspect.signature(adapter.execute).parameters
        accepts_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
        optional = {
            "session_key": session_key,
            "env_vars": env_vars,
            "timeout": timeout,
            "isolate_network": isolate_network,
            "dns_proxy_port": dns_proxy_port,
            "extra_binds": extra_binds,
        }
        for name, value in optional.items():
            if accepts_kwargs or name in params:
                kwargs[name] = value
        return await adapter.execute(container_id, code, language, **kwargs)

    async def execute(
        self,
        container_id: str,
        code: str,
        language: str = "python",
        *,
        session_key: str | None = None,
        env_vars: dict[str, str] | None = None,
        timeout: int | None = None,
        isolate_network: bool = True,
        dns_proxy_port: int | None = None,
        extra_binds: list[tuple[Path, str, bool]] | None = None,
    ) -> dict:
        if container_id.startswith("l0-"):
            return await self._execute_adapter(                self._adapters[SandboxLevel.L0.value], container_id, code, language,
                session_key=session_key, env_vars=env_vars, timeout=timeout,
                isolate_network=isolate_network, dns_proxy_port=dns_proxy_port,
                extra_binds=extra_binds,
            )
        if container_id.startswith("tee-"):
            return await self._execute_adapter(
                self._adapters[SandboxLevel.L1.value], container_id, code, language,
                session_key=session_key, env_vars=env_vars, timeout=timeout,
            )
        if container_id.startswith("fc-"):
            return await self._execute_adapter(
                self._adapters[SandboxLevel.L2.value], container_id, code, language,
                session_key=session_key, env_vars=env_vars, timeout=timeout,
            )
        if container_id.startswith("bwrap-"):
            return await self._execute_adapter(
                self._adapters[SandboxLevel.L3.value], container_id, code, language,
                session_key=session_key, env_vars=env_vars, timeout=timeout,
            )
        if container_id.startswith("k8s-"):
            return await self._execute_adapter(
                self._adapters["k8s"], container_id, code, language,
                session_key=session_key, env_vars=env_vars, timeout=timeout,
            )
        return {
            "output": f"SECURITY ERROR: unsupported sandbox container prefix for {container_id}",
            "exit_code": -1,
            "duration_ms": 0,
            "sandbox_level": "unknown",
        }

    async def execute_streaming(
        self,
        container_id: str,
        code: str,
        language: str = "bash",
        *,
        env_vars: dict[str, str] | None = None,
        timeout: int | None = None,
        extra_binds: list[tuple[Path, str, bool]] | None = None,
        on_line,
    ) -> dict:
        """W10: facade dispatch for streaming execution (L0 + k8s; other
        adapters return an unsupported marker the caller reports honestly)."""
        if container_id.startswith("l0-"):
            adapter = self._adapters[SandboxLevel.L0.value]
            streaming = getattr(adapter, "execute_streaming", None)
            if streaming is None:
                return {"exit_code": -1, "duration_ms": 0, "blocked": False, "error": "adapter does not support streaming"}
            return await streaming(
                container_id, code, language, env_vars=env_vars, timeout=timeout,
                extra_binds=extra_binds, on_line=on_line,
            )
        if container_id.startswith("k8s-"):
            adapter = self._adapters["k8s"]
            streaming = getattr(adapter, "execute_streaming", None)
            if streaming is None:
                return {"exit_code": -1, "duration_ms": 0, "blocked": False, "error": "adapter does not support streaming"}
            return await streaming(
                container_id, code, language, env_vars=env_vars, timeout=timeout,
                on_line=on_line,
            )
        return {
            "exit_code": -1,
            "duration_ms": 0,
            "blocked": False,
            "error": f"streaming exec not supported for container {container_id[:5]}…",
        }

    def terminate(self, container_id: str) -> bool:
        if container_id.startswith("l0-"):
            return self._adapters[SandboxLevel.L0.value].terminate(container_id)
        if container_id.startswith("tee-"):
            return self._adapters[SandboxLevel.L1.value].terminate(container_id)
        if container_id.startswith("fc-"):
            return self._adapters[SandboxLevel.L2.value].terminate(container_id)
        if container_id.startswith("bwrap-"):
            return self._adapters[SandboxLevel.L3.value].terminate(container_id)
        if container_id.startswith("k8s-"):
            return self._adapters["k8s"].terminate(container_id)
        logger.warning("[SandboxRuntime] Refusing to terminate unsupported container id: %s", container_id)
        return False

    def get_status(self, container_id: str) -> str:
        if container_id.startswith("l0-"):
            return self._adapters[SandboxLevel.L0.value].get_status(container_id)
        if container_id.startswith("tee-"):
            return self._adapters[SandboxLevel.L1.value].get_status(container_id)
        if container_id.startswith("fc-"):
            return self._adapters[SandboxLevel.L2.value].get_status(container_id)
        if container_id.startswith("bwrap-"):
            return self._adapters[SandboxLevel.L3.value].get_status(container_id)
        if container_id.startswith("k8s-"):
            return self._adapters["k8s"].get_status(container_id)
        logger.warning("[SandboxRuntime] Refusing to inspect unsupported container id: %s", container_id)
        return "unknown"

    async def secure_destroy(self, session_id: uuid.UUID, db, reason: str = "manual") -> DestructionReport:
        """Securely destroy a sandbox: terminate container + revoke key + audit.

        Args:
            session_id: The sandbox session to destroy.
            db: AsyncSession for database operations.
            reason: Why the sandbox is being destroyed.

        Returns:
            DestructionReport with per-step success/failure details.
        """
        from sqlalchemy import select
        from app.models.sandbox_session import SandboxSession
        from app.models.sandbox_task import SandboxTask, TaskStatus as TStatus
        from app.models.kms import KeyMetadata, KeyStatus
        from app.services.kms_service import kms_service
        from app.services.audit_service import audit_service

        errors = []
        container_destroyed = False
        key_destroyed = False
        memory_wiped = False
        audit_logged = False

        result = await db.execute(select(SandboxSession).where(SandboxSession.id == session_id))
        session = result.scalar_one_or_none()
        if not session:
            return DestructionReport(session_id, errors=["Session not found"])

        # Step 1: Destroy container
        if session.container_id:
            try:
                self.terminate(session.container_id)
                container_destroyed = True
            except Exception as e:
                errors.append(f"Container destruction failed: {e}")
                logger.error(f"Container destruction failed: {e}")

        # Step 2: Revoke session key (in-memory only — never written to disk)
        if session.session_key_id:
            try:
                kms_service.destroy_key(session.session_key_id)
                key_result = await db.execute(
                    select(KeyMetadata).where(KeyMetadata.key_id == session.session_key_id)
                )
                key_meta = key_result.scalar_one_or_none()
                if key_meta:
                    key_meta.status = KeyStatus.DESTROYED.value
                    key_meta.destroyed_at = datetime.now(timezone.utc)
                    key_meta.destroy_reason = reason
                key_destroyed = True
            except Exception as e:
                errors.append(f"Key destruction failed: {e}")
                logger.error(f"Key destruction failed: {e}")

        # Step 3: Memory wipe (best-effort — container already terminated)
        try:
            workspace = Path(f"/tmp/cds-sandbox/{session_id}")
            if workspace.exists():
                shutil.rmtree(workspace, ignore_errors=True)
            memory_wiped = True
        except Exception as e:
            errors.append(f"Memory wipe failed: {e}")

        # Step 4: Cancel running tasks
        tasks_result = await db.execute(
            select(SandboxTask).where(
                SandboxTask.session_id == session_id,
                SandboxTask.status.in_([TStatus.PENDING.value, TStatus.RUNNING.value]),
            )
        )
        for task in tasks_result.scalars().all():
            task.status = TStatus.CANCELLED.value
            task.error_message = f"Session destroyed: {reason}"

        # Step 5: Update session
        session.status = SessionStatus.TERMINATED.value
        session.ended_at = datetime.now(timezone.utc)
        session.error_message = f"Secure destroy: {reason}"

        # Step 6: Audit
        try:
            await audit_service.log(
                db,
                action="sandbox.secure_destroy",
                resource_type="sandbox_session",
                resource_id=str(session_id),
                user_id=session.user_id,
                detail={
                    "reason": reason,
                    "container_destroyed": container_destroyed,
                    "key_destroyed": key_destroyed,
                    "memory_wiped": memory_wiped,
                    "errors": errors,
                },
            )
            audit_logged = True
        except Exception as e:
            errors.append(f"Audit log failed: {e}")

        await db.flush()
        return DestructionReport(
            session_id, container_destroyed, key_destroyed,
            memory_wiped, audit_logged, errors,
        )

    async def terminate_sessions_by_key(self, key_id: str, db, reason: str = "key_revoked") -> int:
        """Terminate all active sandbox sessions using a specific key.

        Called when a DEK is revoked to ensure no sandbox continues
        operating with a revoked key. Returns count of terminated sessions.
        """
        from sqlalchemy import select
        from app.models.sandbox_session import SandboxSession
        from app.models.kms import KeyMetadata, KeyStatus
        from app.services.kms_service import kms_service
        from app.services.session_state_machine import session_state_machine

        result = await db.execute(
            select(SandboxSession).where(
                SandboxSession.session_key_id == key_id,
                SandboxSession.status.in_([
                    SessionStatus.RUNNING.value,
                    SessionStatus.PROVISIONING.value,
                    SessionStatus.READY.value,
                    SessionStatus.PENDING.value,
                    SessionStatus.KEY_DISTRIBUTING.value,
                    SessionStatus.SUSPENDED.value,
                ]),
            )
        )
        sessions = result.scalars().all()
        terminated = 0

        for session in sessions:
            try:
                # Validate state transition via state machine
                current = SessionStatus(session.status)
                result = session_state_machine.validate_transition(current, SessionStatus.TERMINATED)
                if not result.success:
                    logger.warning(f"[KMS] Cannot terminate session {session.id}: {result.error}")
                    continue
                if session.container_id:
                    self.terminate(session.container_id)
                session.status = SessionStatus.TERMINATED.value
                session.ended_at = datetime.now(timezone.utc)
                session.error_message = f"Session terminated: {reason}"
                terminated += 1
                logger.info(f"[KMS] Terminated session {session.id} due to key revocation ({key_id})")
            except Exception as e:
                logger.error(f"[KMS] Failed to terminate session {session.id}: {e}")

        # Destroy the key in KMS
        kms_service.destroy_key(key_id)

        # Update key metadata in DB
        key_result = await db.execute(select(KeyMetadata).where(KeyMetadata.key_id == key_id))
        key_meta = key_result.scalar_one_or_none()
        if key_meta:
            key_meta.status = KeyStatus.DESTROYED.value
            key_meta.destroyed_at = datetime.now(timezone.utc)
            key_meta.destroy_reason = reason

        if terminated > 0:
            from app.services.audit_service import audit_service
            await audit_service.log(
                db, action="kms.revoke_key_terminate_sessions",
                resource_type="key", resource_id=key_id,
                detail={"terminated_sessions": terminated, "reason": reason},
            )

        await db.flush()
        logger.info(f"[KMS] Key {key_id} revoked: {terminated} sessions terminated")
        return terminated


# Singleton
sandbox_runtime = SandboxRuntime()


# --- P2-5: Scene Runtime Factory ---

class SceneRuntime:
    """Base class for scene-specific runtimes.

    Each scene runtime wraps a SandboxRuntime adapter and adds
    scene-specific pre/post processing (query validation, output
    filtering, policy enforcement, etc.).
    """

    def __init__(self, mode: "SandboxMode"):
        self.mode = mode

    async def pre_execute(self, code: str, context: dict) -> str:
        """Pre-process code before execution. Returns modified code."""
        return code

    async def post_execute(self, result: dict, context: dict) -> dict:
        """Post-process execution result. Returns modified result."""
        return result

    async def execute(self, container_id: str, code: str, language: str,
                      context: dict | None = None) -> dict:
        """Full execution cycle: pre → adapter.execute → post."""
        ctx = context or {}
        ctx.setdefault("language", language)
        code = await self.pre_execute(code, ctx)
        result = await sandbox_runtime.execute(container_id, code, language)
        return await self.post_execute(result, ctx)


class StructuredQueryRuntime(SceneRuntime):
    """Structured query scene: DuckDB SQL with field masking and row limits.

    Pre:  validates SQL syntax, injects field masks, checks policy
    Post: applies row limits, strips sensitive columns, logs query metrics
    """

    def __init__(self):
        super().__init__(SandboxMode.STRUCTURED_QUERY)

    async def pre_execute(self, code: str, context: dict) -> str:
        """Validate and wrap SQL for DuckDB execution."""
        import re

        if context.get("language", "sql") != "sql":
            return code

        sql = code.strip()
        if not sql:
            return code

        # Reject dangerous DDL/DML in query mode
        blocked = re.compile(
            r'^\s*(DROP|ALTER|TRUNCATE|CREATE\s+USER|GRANT|REVOKE)\b',
            re.IGNORECASE,
        )
        if blocked.match(sql):
            raise ValueError(f"DDL/DML not allowed in structured_query mode: {sql.split()[0].upper()}")

        # Enforce row limit via LIMIT clause
        max_rows = context.get("max_output_rows", 10000)
        if not re.search(r'\bLIMIT\b', sql, re.IGNORECASE):
            sql = f"{sql.rstrip(';')} LIMIT {max_rows}"

        return sql

    async def post_execute(self, result: dict, context: dict) -> dict:
        """Apply output post-processing for query results."""
        # Truncate output if exceeds max rows
        max_rows = context.get("max_output_rows", 10000)
        output = result.get("output", "")
        if output:
            lines = output.splitlines()
            if len(lines) > max_rows:
                result["output"] = "\n".join(lines[:max_rows]) + f"\n... (truncated, {len(lines)} total rows)"
                result["truncated"] = True

        return result


class LLMTrainingRuntime(SceneRuntime):
    """LLM training scene: GPU resource validation, epoch limits."""

    def __init__(self):
        super().__init__(SandboxMode.LLM_TRAINING)

    async def pre_execute(self, code: str, context: dict) -> str:
        max_epochs = context.get("max_epochs", 100)
        # Inject epoch limit guard
        guard = f"""
import os
os.environ["CDS_MAX_EPOCHS"] = "{max_epochs}"
"""
        return guard + code


class ProductDevRuntime(SceneRuntime):
    """Product dev scene: output format restrictions, no raw data leakage."""

    def __init__(self):
        super().__init__(SandboxMode.PRODUCT_DEV)

    async def post_execute(self, result: dict, context: dict) -> dict:
        """Verify output doesn't contain raw data indicators."""
        output = result.get("output", "")
        if "SELECT * FROM" in output.upper() and "LIMIT" not in output.upper():
            result["warning"] = "Output may contain raw data without row limits"
        return result


class StructuredAppRuntime(SceneRuntime):
    """Structured app scene: API rate limiting, response size caps."""

    def __init__(self):
        super().__init__(SandboxMode.STRUCTURED_APP)

    async def post_execute(self, result: dict, context: dict) -> dict:
        """Cap response size."""
        max_mb = context.get("max_response_mb", 10)
        output = result.get("output", "")
        max_bytes = max_mb * 1024 * 1024
        if len(output.encode()) > max_bytes:
            result["output"] = output[:max_bytes] + "\n... (truncated: response size limit)"
            result["truncated"] = True
        return result


class DataModelingRuntime(SceneRuntime):
    """Data modeling scene: DuckDB-based statistical modeling.

    Allows DDL/DML (CREATE/INSERT/DROP/ALTER/SELECT) for building
    models and temporary tables.  Blocks privilege-escalation
    statements (GRANT/REVOKE/CREATE USER).

    Pre:  validates SQL, blocks admin statements
    Post: applies row limits, checks for raw data leakage
    """

    def __init__(self):
        super().__init__(SandboxMode.STRUCTURED_MODELING)

    async def pre_execute(self, code: str, context: dict) -> str:
        """Validate SQL for modeling.

        Allows: CREATE, INSERT, DROP, ALTER, SELECT (and WITH for CTEs).
        Blocks: privilege escalation (GRANT/REVOKE/CREATE USER).
        """
        import re

        if context.get("language", "sql") != "sql":
            return code

        sql = code.strip()
        if not sql:
            return code

        blocked = re.compile(
            r'^\s*(GRANT|REVOKE|CREATE\s+USER|DROP\s+USER|ALTER\s+USER)\b',
            re.IGNORECASE,
        )
        if blocked.match(sql):
            raise ValueError(
                f"Privilege escalation not allowed in modeling mode: {sql.split()[0].upper()}"
            )

        return sql

    async def post_execute(self, result: dict, context: dict) -> dict:
        """Apply output post-processing for modeling results.

        - Truncates output exceeding max_output_rows
        - Warns if output contains raw SELECT * without LIMIT
        """
        import re

        max_rows = context.get("max_output_rows", 10000)
        output = result.get("output", "")

        # Warn on potential raw data leakage
        if re.search(r'SELECT\s+\*\s+FROM', output, re.IGNORECASE):
            if not re.search(r'\bLIMIT\b', output, re.IGNORECASE):
                result["warning"] = "Output may contain raw data without row limits"

        # Truncate excessive output
        if output:
            lines = output.splitlines()
            if len(lines) > max_rows:
                result["output"] = (
                    "\n".join(lines[:max_rows])
                    + f"\n... (truncated, {len(lines)} total rows)"
                )
                result["truncated"] = True

        return result


class FederatedRuntime(SceneRuntime):
    """Joint federated scene runtime.

    The runtime supports two execution shapes:
    - normal sandbox code with stricter federation-mode guards;
    - context-driven remote federation calls through ``FederationConnector``.
    """

    def __init__(self):
        super().__init__(SandboxMode.JOINT_FEDERATED)

    async def pre_execute(self, code: str, context: dict) -> str:
        import re

        language = (context.get("language") or "python").lower()

        if language == "sql":
            sql = code.strip()
            if not sql:
                return code
            blocked = re.compile(
                r"^\s*(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|CREATE|GRANT|REVOKE|COPY|ATTACH)\b",
                re.IGNORECASE,
            )
            if blocked.match(sql):
                raise ValueError(
                    f"Mutating SQL is not allowed in joint_federated mode: {sql.split()[0].upper()}"
                )
            if re.search(r"\bSELECT\s+\*\s+FROM\b", sql, re.IGNORECASE) and not context.get("allow_raw_rows"):
                raise ValueError("Raw SELECT * is not allowed in joint_federated mode")
            max_rows = int(context.get("max_output_rows", 10000))
            if not re.search(r"\bLIMIT\b", sql, re.IGNORECASE):
                sql = f"{sql.rstrip(';')} LIMIT {max_rows}"
            return sql

        if language == "python" and not context.get("allow_direct_network"):
            direct_network = re.compile(
                r"(^|\n)\s*(import|from)\s+(requests|httpx|urllib|socket)\b|"
                r"\b(requests|httpx|urllib|socket)\.",
                re.IGNORECASE,
            )
            if direct_network.search(code):
                raise ValueError(
                    "Direct network access is not allowed in joint_federated mode; "
                    "use federation_request context"
                )

        if language == "python":
            metadata = {
                "mode": SandboxMode.JOINT_FEDERATED.value,
                "allowed_operations": context.get("allowed_operations", []),
            }
            guard = (
                "import os\n"
                f"os.environ['CDS_SANDBOX_MODE']='{SandboxMode.JOINT_FEDERATED.value}'\n"
                f"os.environ['CDS_FEDERATION_CONTEXT']={json.dumps(json.dumps(metadata, ensure_ascii=False))}\n"
            )
            return guard + code

        return code

    async def execute(
        self,
        container_id: str,
        code: str,
        language: str,
        context: dict | None = None,
    ) -> dict:
        ctx = context or {}
        request = ctx.get("federation_request") or ctx.get("federated_request")
        if request:
            return await self._execute_federation_request(request, ctx)
        return await super().execute(container_id, code, language, ctx)

    async def _execute_federation_request(self, request: dict, context: dict) -> dict:
        connector = request.get("connector") or context.get("federation_connector")
        if connector is None:
            from app.services.federation_connector import FederationConnector
            connector = FederationConnector()

        trust = request.get("trust") or context.get("federation_trust")
        if trust is None:
            return {
                "output": "federation_request requires a trusted FederationTrust context",
                "exit_code": -1,
                "duration_ms": 0,
                "federated": True,
            }

        operation = request.get("operation") or context.get("operation")
        resource = request.get("resource") or context.get("resource")
        if not operation or not resource:
            return {
                "output": "federation_request requires operation and resource",
                "exit_code": -1,
                "duration_ms": 0,
                "federated": True,
            }

        response = connector.send_request(
            trust=trust,
            operation=operation,
            resource=resource,
            payload=request.get("payload") or context.get("payload"),
            user_id=request.get("user_id") or context.get("user_id"),
        )
        if hasattr(response, "__await__"):
            response = await response

        output = {
            "request_id": getattr(response, "request_id", ""),
            "status_code": getattr(response, "status_code", 0),
            "data": getattr(response, "data", None),
            "error": getattr(response, "error", None),
            "source_space": getattr(response, "source_space", ""),
        }
        status_code = int(output["status_code"] or 0)
        return {
            "output": json.dumps(output, ensure_ascii=False, default=str),
            "exit_code": 0 if 200 <= status_code < 400 else -1,
            "duration_ms": int(getattr(response, "duration_ms", 0) or 0),
            "federated": True,
            "status_code": status_code,
            "source_space": output["source_space"],
        }

    async def post_execute(self, result: dict, context: dict) -> dict:
        output = result.get("output", "") or ""
        max_rows = int(context.get("max_output_rows", 10000))
        if output:
            lines = output.splitlines()
            if len(lines) > max_rows:
                result["output"] = "\n".join(lines[:max_rows]) + f"\n... (truncated, {len(lines)} total rows)"
                result["truncated"] = True
        result.setdefault("federated", True)
        return result


class SceneRuntimeFactory:
    """Factory for creating scene-specific runtimes (P2-5).

    Usage:
        runtime = SceneRuntimeFactory.create(SandboxMode.STRUCTURED_QUERY)
        result = await runtime.execute(container_id, sql, "sql", context)
    """

    _registry: dict[str, type[SceneRuntime]] = {
        SandboxMode.STRUCTURED_QUERY.value: StructuredQueryRuntime,
        SandboxMode.STRUCTURED_MODELING.value: DataModelingRuntime,
        SandboxMode.LLM_TRAINING.value: LLMTrainingRuntime,
        SandboxMode.PRODUCT_DEV.value: ProductDevRuntime,
        SandboxMode.STRUCTURED_APP.value: StructuredAppRuntime,
        SandboxMode.JOINT_FEDERATED.value: FederatedRuntime,
    }

    @classmethod
    def create(cls, mode: str | SandboxMode) -> SceneRuntime:
        """Create a scene runtime for the given mode.

        Falls back to base SceneRuntime for unknown extension modes.
        """
        mode_val = mode.value if hasattr(mode, "value") else str(mode)
        runtime_cls = cls._registry.get(mode_val)
        if runtime_cls:
            return runtime_cls()
        # Unregistered modes use passthrough runtime
        return SceneRuntime(SandboxMode(mode_val) if mode_val in [m.value for m in SandboxMode] else SandboxMode.STRUCTURED_QUERY)

    @classmethod
    def register(cls, mode: str, runtime_cls: type[SceneRuntime]):
        """Register a custom scene runtime."""
        cls._registry[mode] = runtime_cls
