"""TEE Simulator -- software-only fallback for SGX/Occlum.

When no SGX hardware is available, this module provides:
- Memory isolation via process-level sandboxing (bubblewrap)
- Simulated attestation quote (SM3 hash of enclave measurement)
- Encrypted memory region tracking (simulated)
"""
import hashlib
import logging
import os
import shutil
import subprocess
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from app.utils.crypto import sm3_hash

logger = logging.getLogger(__name__)


@dataclass
class EnclaveHandle:
    """Handle for a simulated TEE enclave (isolated bwrap process)."""
    pid: int
    mrenclave: str  # SM3 hash of binary + config (simulated enclave measurement)
    mrsigner: str   # SM3 hash of signer identity (simulated)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    memory_limit_mb: int = 256
    workspace: str = ""
    process: subprocess.Popen | None = field(default=None, repr=False)


@dataclass
class AttestationReport:
    """Simulated TEE attestation report."""
    quote: str       # SM3 hash chain (simulated SGX quote)
    mrenclave: str
    mrsigner: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    is_simulation: bool = True


class TEESimulator:
    """Software simulation of TEE (SGX/Occlum) using bubblewrap process isolation.

    Provides the same conceptual guarantees as a real TEE but implemented
    entirely in software:
    - Process-level isolation via bwrap namespaces (PID, mount, network, IPC)
    - Simulated MRENCLAVE measurement via SM3 hash
    - Simulated attestation quotes with SM3 hash chains

    This is intended for development, testing, and environments where
    SGX hardware is not available.  In production, a real TEE adapter
    should be used instead.
    """

    def __init__(self, workspace_root: str = "/tmp/cds-tee-sim"):
        self.workspace_root = Path(workspace_root)
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self._enclaves: dict[str, EnclaveHandle] = {}
        self._bwrap_available = shutil.which("bwrap") is not None

    def create_enclave(self, memory_mb: int = 256) -> EnclaveHandle:
        """Create an isolated process using bubblewrap, generating a simulated
        MRENCLAVE measurement (SM3 hash of the binary + configuration).

        Args:
            memory_mb: Memory limit for the simulated enclave in megabytes.

        Returns:
            EnclaveHandle with pid, measurements, and workspace path.
        """
        enclave_id = str(uuid.uuid4())
        workspace = self.workspace_root / enclave_id
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "input").mkdir(exist_ok=True)
        (workspace / "output").mkdir(exist_ok=True)
        (workspace / "tmp").mkdir(exist_ok=True)
        (workspace / "tmp").chmod(0o700)

        # Generate simulated MRENCLAVE: SM3 hash of (binary path + config + enclave_id)
        binary_info = f"bwrap:{shutil.which('bwrap') or 'unavailable'}"
        config_str = f"{binary_info}:memory={memory_mb}mb:id={enclave_id}"
        mrenclave = sm3_hash(config_str.encode())

        # Generate simulated MRSIGNER: SM3 hash of signer identity
        mrsigner = sm3_hash(f"cds-tee-simulator:v1:{os.getpid()}".encode())

        # Start an isolated sleep process via bwrap (long-running placeholder)
        process = None
        pid = 0

        if self._bwrap_available:
            try:
                cmd = [
                    "bwrap",
                    "--chdir", "/workspace",
                    "--unshare-pid",
                    "--unshare-net",
                    "--unshare-ipc",
                    "--die-with-parent",
                    "--ro-bind", "/usr", "/usr",
                    "--ro-bind", "/lib", "/lib",
                    *(["--ro-bind", "/lib64", "/lib64"] if Path("/lib64").exists() else []),
                    "--dev", "/dev",
                    "--proc", "/proc",
                    "--tmpfs", "/tmp:size=50m",
                    "--tmpfs", "/workspace:size=500m",
                    "--bind", str(workspace / "output"), "/workspace/output",
                    "--bind", str(workspace / "tmp"), "/workspace/tmp",
                    "--ro-bind", str(workspace / "input"), "/workspace/input",
                    "--tmpfs", "/sys:size=1m",
                    "sleep", "86400",  # Keep enclave alive for up to 24h
                ]
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                pid = process.pid
                logger.info(
                    "[TEESim] Created enclave %s (pid=%d, mem=%dMB, mrenclave=%s...)",
                    enclave_id[:8], pid, memory_mb, mrenclave[:16],
                )
            except Exception as e:
                logger.warning("[TEESim] bwrap enclave creation failed (%s), using fallback", e)
                pid = os.getpid()  # Fallback: reference parent pid
        else:
            # No bwrap available -- simulated-only mode (no real isolation)
            pid = os.getpid()
            logger.warning(
                "[TEESim] bwrap not available -- enclave %s has NO real process isolation",
                enclave_id[:8],
            )

        handle = EnclaveHandle(
            pid=pid,
            mrenclave=mrenclave,
            mrsigner=mrsigner,
            memory_limit_mb=memory_mb,
            workspace=str(workspace),
            process=process,
        )
        self._enclaves[enclave_id] = handle
        return handle

    def attest(self, enclave: EnclaveHandle) -> AttestationReport:
        """Generate a simulated attestation report with SM3 hash chain.

        The quote is an SM3 hash chain: SM3(mrenclave || mrsigner || timestamp || nonce)
        This simulates the SGX quote structure for software-only environments.

        Args:
            enclave: The enclave handle to attest.

        Returns:
            AttestationReport with is_simulation=True.
        """
        nonce = uuid.uuid4().hex
        timestamp = datetime.now(timezone.utc).isoformat()
        quote_input = f"{enclave.mrenclave}:{enclave.mrsigner}:{timestamp}:{nonce}"
        quote = sm3_hash(quote_input.encode())

        logger.info(
            "[TEESim] Attestation for enclave (mrenclave=%s...): quote=%s..., simulation=True",
            enclave.mrenclave[:16], quote[:16],
        )
        return AttestationReport(
            quote=quote,
            mrenclave=enclave.mrenclave,
            mrsigner=enclave.mrsigner,
            timestamp=datetime.now(timezone.utc),
            is_simulation=True,
        )

    def destroy_enclave(self, enclave: EnclaveHandle) -> None:
        """Kill the isolated process and clean up the enclave workspace.

        Args:
            enclave: The enclave handle to destroy.
        """
        # Terminate the bwrap process if it is still running
        if enclave.process is not None:
            try:
                enclave.process.terminate()
                try:
                    enclave.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    enclave.process.kill()
                    enclave.process.wait(timeout=2)
                logger.info("[TEESim] Enclave process (pid=%d) terminated", enclave.pid)
            except (ProcessLookupError, OSError):
                pass  # Already dead

        # Remove workspace
        if enclave.workspace:
            workspace = Path(enclave.workspace)
            if workspace.exists():
                shutil.rmtree(workspace, ignore_errors=True)

        # Remove from tracking
        to_remove = None
        for eid, handle in self._enclaves.items():
            if handle is enclave:
                to_remove = eid
                break
        if to_remove:
            del self._enclaves[to_remove]

        logger.info("[TEESim] Enclave destroyed (mrenclave=%s...)", enclave.mrenclave[:16])

    @property
    def active_enclaves(self) -> dict[str, EnclaveHandle]:
        """Return a copy of the currently active enclave handles."""
        return dict(self._enclaves)
