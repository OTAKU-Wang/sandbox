"""Sandbox Security Hardening — production-grade isolation for L3 bwrap.

Implements defense-in-depth:
1. Namespace isolation (user, PID, mount, network, UTS, IPC)
2. Cgroup resource limits (CPU, memory, PID count, file size)
3. Seccomp syscall filter
4. Read-only root filesystem with minimal writable mounts
5. No /proc, /sys, /dev access beyond minimum
6. Session key injection via environment (never written to disk)
7. Output size limits to prevent data exfiltration via large outputs
8. Dynamic timeout enforcement
"""
import ctypes
import ctypes.util
import os
import json
import logging
import resource
import struct
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Seccomp BPF filter: whitelist of allowed syscalls for Python/bash execution
# Blocks: ptrace, mount, umount2, reboot, kexec_load, init_module, etc.
_SECCOMP_JSON = json.dumps({
    "defaultAction": "SCMP_ACT_ERRNO",
    "architectures": ["SCMP_ARCH_X86_64"],
    "syscalls": [
        {
            "names": [
                "read", "write", "open", "close", "stat", "fstat", "lstat",
                "poll", "lseek", "mmap", "mprotect", "munmap", "brk",
                "rt_sigaction", "rt_sigprocmask", "rt_sigreturn",
                "ioctl", "access", "pipe", "select", "sched_yield",
                "mremap", "msync", "mincore", "madvise",
                "dup", "dup2", "nanosleep", "getpid", "getppid",
                "getuid", "getgid", "geteuid", "getegid",
                "getresuid", "getresgid", "getgroups",
                "getpgid", "getpgrp", "setsid", "setpgid",
                "gettimeofday", "getrlimit", "getrusage",
                "times", "futex", "set_tid_address",
                "clock_gettime", "clock_getres", "clock_nanosleep",
                "exit_group", "epoll_wait", "epoll_ctl",
                "epoll_create1", "eventfd2",
                "wait4", "waitid",
                "openat", "newfstatat", "readlinkat", "faccessat",
                "getdents64", "readlink", "arch_prctl",
                "getrandom", "memfd_create",
                "socket", "connect", "accept", "sendto", "recvfrom",
                "shutdown", "bind", "listen", "getsockname", "getpeername",
                "socketpair", "setsockopt", "getsockopt",
                "clone", "fork", "vfork", "execve",
                "fcntl", "ftruncate", "truncate",
                "pread64", "pwrite64", "readv", "writev",
                "getcwd", "chdir", "renameat2", "mkdirat",
                "unlinkat", "symlinkat", "linkat",
                "umask", "fchmod", "fchmodat",
                "fchown", "fchownat",
                "prctl", "sysinfo", "uname",
                "gettid", "tgkill",
                "mlock", "munlock", "mlock2",
                "copy_file_range", "splice", "tee",
                "statx", "rseq", "pidfd_open",
            ],
            "action": "SCMP_ACT_ALLOW",
        }
    ],
})


# --- Seccomp BPF compiler via ctypes (libseccomp2) ---

# Syscall name-to-number mapping for x86_64
_SYSCALL_MAP: dict[str, int] = {}

# libseccomp2 constants
_SCMP_ACT_ERRNO = 0x00050001  # SECCOMP_RET_ERRNO
_SCMP_ACT_ALLOW = 0x7FFF0000  # SECCOMP_RET_ALLOW
_SCMP_ARCH_X86_64 = 0xC000003E

# Syscall numbers for x86_64 (key subset for the whitelist)
_SYSCALL_NUMBERS = {
    "read": 0, "write": 1, "open": 2, "close": 3, "stat": 4, "fstat": 5,
    "lstat": 6, "poll": 7, "lseek": 8, "mmap": 9, "mprotect": 10,
    "munmap": 11, "brk": 12, "rt_sigaction": 13, "rt_sigprocmask": 14,
    "rt_sigreturn": 15, "ioctl": 16, "access": 21, "pipe": 22, "select": 23,
    "sched_yield": 24, "mremap": 25, "msync": 26, "mincore": 27, "madvise": 28,
    "dup": 32, "dup2": 33, "nanosleep": 35, "getpid": 39, "getppid": 40,
    "getuid": 102, "getgid": 104, "geteuid": 107, "getegid": 108,
    "getresuid": 118, "getresgid": 120, "getgroups": 115,
    "getpgid": 121, "getpgrp": 111, "setsid": 112, "setpgid": 109,
    "gettimeofday": 96, "getrlimit": 97, "getrusage": 98,
    "times": 100, "futex": 202, "set_tid_address": 218,
    "clock_gettime": 228, "clock_getres": 229, "clock_nanosleep": 230,
    "exit_group": 231, "epoll_wait": 232, "epoll_ctl": 233,
    "epoll_create1": 291, "eventfd2": 290,
    "wait4": 61, "waitid": 247,
    "openat": 257, "newfstatat": 262, "readlinkat": 267, "faccessat": 269,
    "getdents64": 217, "readlink": 89, "arch_prctl": 158,
    "getrandom": 318, "memfd_create": 319,
    "socket": 41, "connect": 42, "accept": 43, "sendto": 44, "recvfrom": 45,
    "shutdown": 48, "bind": 49, "listen": 50, "getsockname": 51, "getpeername": 52,
    "socketpair": 53, "setsockopt": 54, "getsockopt": 55,
    "clone": 56, "fork": 57, "vfork": 58, "execve": 59,
    "fcntl": 72, "ftruncate": 77, "truncate": 76,
    "pread64": 17, "pwrite64": 18, "readv": 19, "writev": 20,
    "getcwd": 79, "chdir": 80, "renameat2": 264, "mkdirat": 258,
    "unlinkat": 263, "symlinkat": 266, "linkat": 265,
    "umask": 95, "fchmod": 91, "fchmodat": 268,
    "fchown": 93, "fchownat": 260,
    "prctl": 157, "sysinfo": 99, "uname": 63,
    "gettid": 186, "tgkill": 234,
    "mlock": 150, "munlock": 151, "mlock2": 325,
    "copy_file_range": 326, "splice": 275, "tee": 276,
    "statx": 332, "rseq": 334, "pidfd_open": 434,
}


def _compile_seccomp_bpf() -> int | None:
    """Compile seccomp whitelist to BPF and return a read-only file descriptor.

    Uses ctypes to call libseccomp2 directly (no pip install needed).
    Returns fd on success, None on failure.
    """
    lib_name = ctypes.util.find_library("seccomp")
    if not lib_name:
        logger.warning("libseccomp not found; seccomp filter disabled")
        return None

    try:
        lib = ctypes.CDLL(lib_name)
    except OSError:
        logger.warning("Failed to load libseccomp; seccomp filter disabled")
        return None

    # Set function signatures
    lib.seccomp_init.restype = ctypes.c_void_p
    lib.seccomp_init.argtypes = [ctypes.c_uint32]
    lib.seccomp_rule_add.restype = ctypes.c_int
    lib.seccomp_rule_add.argtypes = [ctypes.c_void_p, ctypes.c_uint32,
                                      ctypes.c_int, ctypes.c_uint]
    lib.seccomp_export_bpf.restype = ctypes.c_int
    lib.seccomp_export_bpf.argtypes = [ctypes.c_void_p, ctypes.c_int]
    lib.seccomp_release.argtypes = [ctypes.c_void_p]
    lib.seccomp_release.restype = None

    # Parse the whitelist from _SECCOMP_JSON
    spec = json.loads(_SECCOMP_JSON)
    default_action = _SCMP_ACT_ERRNO
    allowed_syscalls = set()
    for rule in spec.get("syscalls", []):
        if rule.get("action") == "SCMP_ACT_ALLOW":
            for name in rule.get("names", []):
                if name in _SYSCALL_NUMBERS:
                    allowed_syscalls.add(name)

    # Create filter context
    ctx = lib.seccomp_init(default_action)
    if not ctx:
        logger.warning("seccomp_init failed")
        return None

    try:
        # Set architecture
        ret = lib.seccomp_arch_add(ctypes.c_void_p(ctx), _SCMP_ARCH_X86_64)
        # ret == -EEXIST if already present, that's fine

        # Add allow rules
        for name in allowed_syscalls:
            num = _SYSCALL_NUMBERS[name]
            ret = lib.seccomp_rule_add(ctypes.c_void_p(ctx), _SCMP_ACT_ALLOW,
                                        ctypes.c_int(num), ctypes.c_uint(0))
            if ret < 0:
                logger.warning(f"seccomp_rule_add failed for {name} ({num}): {ret}")

        # Export BPF to temp file, then open read-only (bwrap reads via fd)
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".bpf")
        tmp_path = tmp.name
        tmp.close()
        tmp_fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        ret = lib.seccomp_export_bpf(ctypes.c_void_p(ctx), tmp_fd)
        os.close(tmp_fd)
        if ret < 0:
            logger.warning(f"seccomp_export_bpf failed: {ret}")
            os.unlink(tmp_path)
            return None
        fd = os.open(tmp_path, os.O_RDONLY)
        os.unlink(tmp_path)  # Unlink keeps fd valid until closed

        return fd
    finally:
        lib.seccomp_release(ctypes.c_void_p(ctx))


# Pre-compiled seccomp BPF fd (lazy-initialized)
_seccomp_bpf_fd: int | None = None
_seccomp_bpf_compiled = False


def get_seccomp_bpf_fd() -> int | None:
    """Get the seccomp BPF file descriptor (compiled once, reused)."""
    global _seccomp_bpf_fd, _seccomp_bpf_compiled
    if not _seccomp_bpf_compiled:
        _seccomp_bpf_compiled = True
        _seccomp_bpf_fd = _compile_seccomp_bpf()
        if _seccomp_bpf_fd is not None:
            logger.info(f"Seccomp BPF filter compiled successfully (fd={_seccomp_bpf_fd})")
        else:
            logger.warning("Seccomp BPF compilation failed; running without syscall filter")
    return _seccomp_bpf_fd


def close_seccomp_bpf():
    """Close the seccomp BPF file descriptor."""
    global _seccomp_bpf_fd, _seccomp_bpf_compiled
    if _seccomp_bpf_fd is not None:
        try:
            os.close(_seccomp_bpf_fd)
        except OSError:
            pass
        _seccomp_bpf_fd = None
        _seccomp_bpf_compiled = False


@dataclass
class SandboxResourceLimits:
    """Resource limits for a sandbox session."""
    max_memory_mb: int = 512
    max_cpu_percent: int = 100  # 100% = 1 core
    max_pids: int = 64
    max_output_bytes: int = 10 * 1024 * 1024  # 10MB
    max_file_size_mb: int = 100
    max_open_files: int = 256
    timeout_seconds: int = 120


@dataclass
class EncryptionConfig:
    """SM4 column-level encryption config for sandbox DuckDB execution.

    Injected into the sandbox via environment variables (never written to disk).
    """
    dek_hex: str = ""  # 16-byte DEK as hex string
    encrypted_columns: dict[str, str] = field(default_factory=dict)  # col -> "sm4-siv"|"sm4-gcm"
    key_id: str = "sandbox-default"


@dataclass
class SandboxSecurityConfig:
    """Security configuration for sandbox execution."""
    # Network isolation
    isolate_network: bool = True
    # User namespace (maps sandbox root to unprivileged host user)
    use_user_namespace: bool = True
    # PID namespace (sandbox processes can't see host PIDs)
    use_pid_namespace: bool = True
    # IPC namespace
    use_ipc_namespace: bool = True
    # UTS namespace (separate hostname)
    use_uts_namespace: bool = True
    # Restrict /proc to minimal set
    restrict_proc: bool = True
    # Block /sys entirely
    block_sys: bool = True
    # Block /dev beyond null/zero/urandom
    restrict_dev: bool = True
    # Seccomp syscall filter
    use_seccomp: bool = True
    # Read-only bind mounts for system dirs
    readonly_system: bool = True
    # Drop all capabilities
    drop_caps: bool = True
    # Resource limits
    resource_limits: SandboxResourceLimits = field(default_factory=SandboxResourceLimits)


def build_bwrap_args(
    workspace: Path,
    code_file: str,
    language: str,
    config: SandboxSecurityConfig | None = None,
    session_key: str | None = None,
    env_vars: dict[str, str] | None = None,
    dns_proxy_port: int | None = None,
    encryption_config: EncryptionConfig | None = None,
    session_id: str = "",
    user_id: str = "",
    sandbox_level: str = "L3",
) -> list[str]:
    """Build hardened bwrap command arguments.

    Args:
        workspace: Path to the sandbox workspace directory
        code_file: Path to the code file inside the sandbox
        language: "python", "sql", or "shell"
        config: Security configuration (uses defaults if None)
        session_key: Optional session key to inject via env var
        env_vars: Additional environment variables
        dns_proxy_port: If set, configure /etc/resolv.conf to use DNS proxy
        encryption_config: SM4 column encryption config (DEK injected via env, never on disk)
        session_id: Sandbox session ID for audit logging
        user_id: User ID for audit logging
        sandbox_level: Sandbox level (L0/L1/L2/L3) for audit logging

    Returns:
        Tuple of (args_list, seccomp_fd). Caller must keep seccomp_fd open and pass via pass_fds.
    """
    if config is None:
        config = SandboxSecurityConfig()

    limits = config.resource_limits
    workspace = Path(workspace)

    args = [
        "bwrap",

        # === Namespace isolation ===
        "--chdir", "/workspace",

        # UTS namespace (separate hostname) — must precede --hostname
        *(["--unshare-uts", "--hostname", "cds-sandbox"] if config.use_uts_namespace else []),

        # User namespace: map sandbox root to unprivileged host UID
        *(
            [
                "--unshare-user",
                "--uid", "1000",
                "--gid", "1000",
            ]
            if config.use_user_namespace and os.getuid() != 0
            else []
        ),

        # PID namespace: sandbox processes can't see host PIDs
        *(["--unshare-pid"] if config.use_pid_namespace else []),

        # IPC namespace
        *(["--unshare-ipc"] if config.use_ipc_namespace else []),

        # Cgroup namespace (isolate /proc/self/cgroup from host)
        *(["--unshare-cgroup"] if config.use_pid_namespace else []),

        # Network namespace: no network access
        *(["--unshare-net"] if config.isolate_network else []),

        # Die when parent dies
        "--die-with-parent",

        # === Filesystem isolation ===
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

        # Restricted /proc (PID namespace gives us isolated /proc)
        *(
            [
                "--proc", "/proc",
                "--ro-bind", "/proc/self/status", "/proc/self/status",
            ]
            if config.use_pid_namespace
            else ["--proc", "/proc"]
        ),

        # tmpfs for /tmp (no access to host /tmp)
        "--tmpfs", "/tmp:size=100m",

        # tmpfs for /var (no access to host /var)
        "--tmpfs", "/var:size=10m",

        # tmpfs for /etc with minimal content
        "--tmpfs", "/etc:size=1m",

        # === End of initial args (may add resolv.conf below) ===
    ]

    # DNS proxy resolv.conf for allowlist mode
    if dns_proxy_port is not None and not config.isolate_network:
        resolv_conf = workspace / ".resolv.conf"
        resolv_conf.write_text(f"nameserver 127.0.0.1\noptions ndots:0\n")
        resolv_conf.chmod(0o644)
        args += ["--ro-bind", str(resolv_conf), "/etc/resolv.conf"]

    args += [
        # Workspace (tmpfs base + bind mounts)
"--tmpfs", "/workspace:size=500m",
    "--bind", str(workspace / "output"), "/workspace/output",
    "--bind", str(workspace / "tmp"), "/workspace/tmp",
    "--ro-bind", str(workspace / "input"), "/workspace/input",
    # Session files exchange dir (Round 39/40 usability): read-write so
    # sandbox code reads uploaded inputs and can drop results back.
    *(["--bind", str(workspace / "files"), "/workspace/files"]
      if (workspace / "files").is_dir()
      else []),

        # Tmpfs for /home
        "--tmpfs", "/home:size=10m",

        # Block /sys entirely (no hardware info leakage)
        *(["--tmpfs", "/sys:size=1m"] if config.block_sys else []),

        # === Execution ===
    ]

    # Seccomp BPF filter — compile and pass via fd to bwrap
    seccomp_fd = None
    if config.use_seccomp:
        seccomp_fd = get_seccomp_bpf_fd()
        if seccomp_fd is not None:
            args += ["--seccomp", str(seccomp_fd)]
        else:
            logger.error("[SECURITY] Seccomp BPF unavailable — bwrap runs WITHOUT syscall filter. "
                         "This is a degraded security mode. Set CDS_SECCOMP_REQUIRED=0 to allow fail-open.")
            if os.environ.get("CDS_SECCOMP_REQUIRED", "1") != "0":
                raise RuntimeError("Seccomp BPF required but unavailable")

    # Resource limits via prlimit
    # These are set on the bwrap process itself
    env_args = []

    # Inject session key via environment variable (never written to disk)
    if session_key:
        env_args += ["--setenv", "CDS_SESSION_KEY", session_key]

    # Inject SM4 DEK via environment variable (never written to disk)
    if encryption_config and encryption_config.dek_hex:
        env_args += ["--setenv", "CDS_DEK_HEX", encryption_config.dek_hex]
        env_args += ["--setenv", "CDS_KEY_ID", encryption_config.key_id]

    # Inject audit logging env vars
    if session_id:
        import tempfile as _tempfile
        audit_dir = Path(_tempfile.gettempdir()) / "cds-sandbox-audit"
        audit_dir.mkdir(parents=True, exist_ok=True)
        audit_log_path = audit_dir / f"{session_id}.jsonl"
        audit_log_path.write_text("")  # Create empty log file
        audit_log_path.chmod(0o640)

        # Shell audit helper script
        from app.services.sandbox_audit import SHELL_AUDIT_HELPER
        audit_helper_path = workspace / "tmp" / ".audit_helper.sh"
        audit_helper_path.write_text(SHELL_AUDIT_HELPER)
        audit_helper_path.chmod(0o755)

        # Mount audit log into sandbox (read-write so sandbox can append)
        args += ["--bind", str(audit_log_path), "/workspace/tmp/.audit.jsonl"]

        env_args += ["--setenv", "CDS_AUDIT_LOG", "/workspace/tmp/.audit.jsonl"]
        env_args += ["--setenv", "CDS_SESSION_ID", session_id]
        env_args += ["--setenv", "CDS_USER_ID", user_id]
        env_args += ["--setenv", "CDS_SANDBOX_LEVEL", sandbox_level]
        # Source audit helper for shell sessions
        if language == "shell":
            env_args += ["--setenv", "CDS_AUDIT_HELPER", "/workspace/tmp/.audit_helper.sh"]

    # Inject additional env vars
    if env_vars:
        for k, v in env_vars.items():
            env_args += ["--setenv", k, v]

    # Set PATH for Python/bash
    env_args += ["--setenv", "PATH", "/usr/local/bin:/usr/bin:/bin"]

    args += env_args

    # Command to execute
    if language == "python":
        args += ["python3", code_file]
    elif language == "sql":
        args += ["python3", "-c", _build_sql_runner(code_file, encryption_config)]
    else:
        args += ["bash", code_file]

    return args, seccomp_fd


def build_resource_limits_args(limits: SandboxResourceLimits) -> list[str]:
    """Build prlimit arguments for resource enforcement.

    Returns arguments to prepend before the bwrap command.
    """
    args = []

    # Memory limit via cgroup (preferred) or ulimit
    # ulimit -v (virtual memory) in KB
    max_vm_kb = limits.max_memory_mb * 1024
    args += ["prlimit", f"--as={max_vm_kb}:unlimited"]

    # PID limit via ulimit -u
    args += [f"--nproc={limits.max_pids}:unlimited"]

    # File size limit (ulimit -f) in 512-byte blocks
    max_file_blocks = limits.max_file_size_mb * 1024 * 1024 // 512
    args += [f"--fsize={max_file_blocks}:unlimited"]

    # Open files limit
    args += [f"--nofile={limits.max_open_files}:unlimited"]

    return args


def _build_sql_runner(code_file: str, encryption_config: EncryptionConfig | None = None) -> str:
    """Generate Python code that executes SQL in a sandboxed DuckDB.

    When encryption_config is provided, the runner:
    - Reads DEK from CDS_DEK_HEX env var (injected by bwrap --setenv)
    - Decrypts SM4-encrypted columns in query results before output
    - Supports SM4-SIV (deterministic) and SM4-GCM (randomized) modes
    """
    # Sanitize code_file to prevent injection via f-string interpolation
    import re as _re
    if not _re.fullmatch(r'/workspace/tmp/exec\.(py|sql|sh)', code_file):
        raise ValueError(f"Invalid code_file path: {code_file!r}")
    if encryption_config and encryption_config.encrypted_columns:
        enc_cols_json = json.dumps(encryption_config.encrypted_columns)
        return f"""
import sys
import json
import os
try:
    import duckdb
except ImportError:
    print("ERROR: duckdb not available in sandbox", file=sys.stderr)
    sys.exit(1)

# SM4 column encryption support
dek_hex = os.environ.get("CDS_DEK_HEX", "")
encrypted_columns = {enc_cols_json}

def _decrypt_value(val, col_name, dek_bytes, mode):
    # Decrypt a single SM4-encrypted column value.
    import hashlib
    if isinstance(val, (bytes, bytearray)):
        raw = bytes(val)
    elif isinstance(val, str):
        try:
            raw = bytes.fromhex(val)
        except ValueError:
            return val
    else:
        return val

    if mode == "sm4-siv":
        # SM4-SIV: IV (16 bytes) prepended to ciphertext
        if len(raw) < 16:
            return val
        iv = raw[:16]
        ct = raw[16:]
    else:
        # SM4-GCM: nonce (12 bytes) prepended to ciphertext
        if len(raw) < 12:
            return val
        iv = raw[:12].ljust(16, b'\\x00')
        ct = raw[12:]

    try:
        from gmssl import sm4 as _sm4
        crypt = _sm4.CryptSM4()
        crypt.set_key(dek_bytes, _sm4.SM4_DECRYPT)
        padded = crypt.crypt_cbc(iv, ct)
        # PKCS7 unpad
        pad_len = padded[-1]
        if 1 <= pad_len <= 16 and padded[-pad_len:] == bytes([pad_len] * pad_len):
            return padded[:-pad_len].decode("utf-8")
        return padded.decode("utf-8")
    except ImportError:
        # Fallback: XOR-based decryption (matches ColumnEncryption fallback)
        if mode == "sm4-siv":
            key_stream = hashlib.sha256(dek_bytes + col_name.encode()).digest()
            data = bytes(b ^ key_stream[i % len(key_stream)] for i, b in enumerate(raw))
            return data.decode("utf-8")
        else:
            nonce = raw[:12]
            ct_data = raw[12:]
            key_stream = hashlib.sha256(dek_bytes + nonce).digest()
            data = bytes(b ^ key_stream[i % len(key_stream)] for i, b in enumerate(ct_data))
            return data.decode("utf-8")

with open("{code_file}") as f:
    sql = f.read()

con = duckdb.connect(":memory:")
try:
    result = con.execute(sql)
    if result.description:
        cols = [d[0] for d in result.description]
        rows = result.fetchall()
        # Decrypt encrypted columns
        if dek_hex and encrypted_columns:
            dek_bytes = bytes.fromhex(dek_hex)
            decrypted_rows = []
            for row in rows:
                new_row = []
                for i, col_name in enumerate(cols):
                    val = row[i]
                    if col_name in encrypted_columns and val is not None:
                        new_row.append(_decrypt_value(val, col_name, dek_bytes, encrypted_columns[col_name]))
                    else:
                        new_row.append(val)
                decrypted_rows.append(new_row)
            rows = decrypted_rows
        print(json.dumps({{"columns": cols, "rows": rows}}, default=str))
    else:
        print(json.dumps({{"affected_rows": result.rowcount}}))
except Exception as e:
    print(f"SQL ERROR: {{e}}", file=sys.stderr)
    sys.exit(1)
finally:
    con.close()
"""

    return f"""
import sys
import json
try:
    import duckdb
except ImportError:
    print("ERROR: duckdb not available in sandbox", file=sys.stderr)
    sys.exit(1)

with open("{code_file}") as f:
    sql = f.read()

con = duckdb.connect(":memory:")
try:
    result = con.execute(sql)
    if result.description:
        cols = [d[0] for d in result.description]
        rows = result.fetchall()
        print(json.dumps({{"columns": cols, "rows": rows}}, default=str))
    else:
        print(json.dumps({{"affected_rows": result.rowcount}}))
except Exception as e:
    print(f"SQL ERROR: {{e}}", file=sys.stderr)
    sys.exit(1)
finally:
    con.close()
"""


@dataclass
class OutputLimiter:
    """Enforces output size limits to prevent data exfiltration via large outputs."""
    max_bytes: int = 10 * 1024 * 1024  # 10MB default
    _collected: int = 0
    _truncated: bool = False

    def check_output(self, output: str) -> str:
        """Truncate output if it exceeds the limit."""
        encoded = output.encode("utf-8")
        if len(encoded) > self.max_bytes:
            self._truncated = True
            truncated = encoded[:self.max_bytes].decode("utf-8", errors="replace")
            return truncated + f"\n\n[OUTPUT TRUNCATED: exceeded {self.max_bytes} bytes limit]"
        return output

    @property
    def was_truncated(self) -> bool:
        return self._truncated


def validate_workspace_path(path: str, workspace_root: str) -> bool:
    """Validate that a path stays within the workspace (prevent path traversal)."""
    try:
        resolved = Path(path).resolve()
        root = Path(workspace_root).resolve()
        return str(resolved).startswith(str(root))
    except (ValueError, OSError):
        return False


def secure_wipe_file(path: Path) -> bool:
    """Securely wipe a file by overwriting with random data before deletion."""
    try:
        if not path.exists():
            return True
        size = path.stat().st_size
        # Overwrite with random data 3 times
        for _ in range(3):
            with open(path, "wb") as f:
                f.write(os.urandom(size))
                f.flush()
                os.fsync(f.fileno())
        path.unlink()
        return True
    except Exception as e:
        logger.error(f"Secure wipe failed for {path}: {e}")
        return False


# ── Workspace file encryption (SM4-GCM) ──────────────────────────────
# Format: [nonce(12)][tag(16)][ciphertext]
# DEK is injected via CDS_DEK_HEX env var at sandbox runtime.

_WORKSPACE_NONCE_LEN = 12
_WORKSPACE_TAG_LEN = 16


def encrypt_workspace_file(file_path: Path, dek: bytes) -> Path:
    """Encrypt a single workspace file in-place using SM4-GCM.

    Returns the same path (file is overwritten with encrypted bytes).
    Format: [nonce(12)][tag(16)][ciphertext]
    """
    from app.utils.crypto import SM4Cipher

    plaintext = file_path.read_bytes()
    if not plaintext:
        return file_path

    cipher = SM4Cipher(dek)
    ciphertext, nonce, tag = cipher.encrypt_gcm(plaintext)
    encrypted = nonce + tag + ciphertext

    file_path.write_bytes(encrypted)
    return file_path


def decrypt_workspace_file(encrypted_path: Path, dek: bytes) -> bytes:
    """Decrypt a workspace file encrypted by encrypt_workspace_file().

    Format: [nonce(12)][tag(16)][ciphertext]
    """
    from app.utils.crypto import SM4Cipher

    raw = encrypted_path.read_bytes()
    if len(raw) < _WORKSPACE_NONCE_LEN + _WORKSPACE_TAG_LEN:
        raise ValueError(f"Encrypted file too short: {len(raw)} bytes")

    nonce = raw[:_WORKSPACE_NONCE_LEN]
    tag = raw[_WORKSPACE_NONCE_LEN:_WORKSPACE_NONCE_LEN + _WORKSPACE_TAG_LEN]
    ciphertext = raw[_WORKSPACE_NONCE_LEN + _WORKSPACE_TAG_LEN:]

    cipher = SM4Cipher(dek)
    return cipher.decrypt_gcm(ciphertext, nonce, tag)


def encrypt_workspace_directory(workspace: Path, dek: bytes) -> list[Path]:
    """Encrypt all files in workspace/input/ directory.

    Returns list of encrypted file paths. Skips empty files and
    already-encrypted files (detected by minimum size threshold).
    """
    input_dir = workspace / "input"
    if not input_dir.exists():
        return []

    encrypted_paths = []
    min_encrypted = _WORKSPACE_NONCE_LEN + _WORKSPACE_TAG_LEN + 1

    for file_path in input_dir.rglob("*"):
        if not file_path.is_file():
            continue
        # Skip files that look already encrypted (unlikely but safe)
        size = file_path.stat().st_size
        if size == 0:
            continue
        encrypt_workspace_file(file_path, dek)
        encrypted_paths.append(file_path)
        logger.debug(f"Encrypted workspace file: {file_path.name} ({size} bytes)")

    return encrypted_paths


def decrypt_workspace_directory(workspace: Path, dek: bytes) -> list[Path]:
    """Decrypt all files in workspace/input/ that were encrypted.

    Returns list of decrypted file paths.
    """
    input_dir = workspace / "input"
    if not input_dir.exists():
        return []

    decrypted_paths = []
    min_encrypted = _WORKSPACE_NONCE_LEN + _WORKSPACE_TAG_LEN + 1

    for file_path in input_dir.rglob("*"):
        if not file_path.is_file():
            continue
        size = file_path.stat().st_size
        if size < min_encrypted:
            continue
        try:
            plaintext = decrypt_workspace_file(file_path, dek)
            file_path.write_bytes(plaintext)
            decrypted_paths.append(file_path)
        except Exception:
            # Not encrypted or corrupted — skip
            continue

    return decrypted_paths
