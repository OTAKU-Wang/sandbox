"""GPU-TEE Runtime — NVIDIA Confidential Computing mode interface.

Provides:
1. GPU-TEE attestation (remote attestation for NVIDIA CC mode)
2. Secure memory allocation (encrypted GPU memory)
3. Secure compute execution (run workloads in TEE)
4. Resource monitoring (GPU utilization within TEE)

Architecture:
- Interface definition for GPU-TEE operations
- Local software implementation for development/testing
- Production implementation would use NVIDIA H100/A100 CC APIs

NVIDIA CC Mode:
- Hardware-level memory encryption on GPU
- Attestation report proves TEE integrity
- Data encrypted in GPU memory, decrypted only inside TEE
"""
from app.services.crypto_service import crypto_service
import logging
import os
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

logger = logging.getLogger(__name__)

_LOCAL_GPU_ATTESTATION_ROOT = b"cds-local-gpu-tee-root-v1"


def _aead_encrypt(key: bytes, plaintext: bytes, aad: bytes = b"") -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce = os.urandom(12)
    return nonce + AESGCM(key).encrypt(nonce, plaintext, aad)


def _aead_decrypt(key: bytes, envelope: bytes, aad: bytes = b"") -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce, ciphertext = envelope[:12], envelope[12:]
    return AESGCM(key).decrypt(nonce, ciphertext, aad)


class GPUTeeStatus(str, Enum):
    """GPU-TEE availability status."""
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    ATTESTING = "attesting"
    ERROR = "error"


class ComputeMode(str, Enum):
    """GPU compute modes."""
    INFERENCE = "inference"
    TRAINING = "training"
    ANALYSIS = "analysis"


@dataclass
class AttestationReport:
    """GPU-TEE attestation report."""
    report_id: str
    gpu_model: str
    driver_version: str
    tee_enabled: bool
    measurement: str  # SM3 hash of TEE environment
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    valid: bool = True
    signature: str = ""


@dataclass
class SecureAllocation:
    """Secure GPU memory allocation."""
    allocation_id: str
    size_bytes: int
    encrypted: bool = True
    gpu_device: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class ComputeJob:
    """A secure compute job running in GPU-TEE."""
    job_id: str
    mode: ComputeMode
    status: str = "pending"  # pending, running, completed, failed
    gpu_device: int = 0
    input_allocation: str | None = None
    output_allocation: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None


@dataclass
class GPUMetrics:
    """GPU resource metrics."""
    gpu_device: int
    gpu_model: str
    memory_total_mb: int
    memory_used_mb: int
    utilization_pct: float
    temperature_c: float
    tee_active: bool


@dataclass
class ChannelKey:
    """CPU-TEE ↔ GPU-TEE encrypted channel key."""
    key_id: str
    key_material: bytes  # Derived key for AES-GCM
    algorithm: str = "AES-256-GCM"
    gpu_measurement: str = ""  # GPU firmware hash used in derivation
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class GPUCCConfig:
    """NVIDIA Confidential Computing mode configuration."""
    device_id: int = 0
    cc_mode_enabled: bool = True
    attestation_required: bool = True
    firmware_version: str = ""
    driver_version: str = ""


@dataclass
class GPUHandle:
    """Handle for an active GPU-TEE session with encrypted channel."""
    device_id: int
    channel_key: ChannelKey
    session_id: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    _bytes_sent: int = 0
    _bytes_received: int = 0
    _payload_store: dict[str, bytes] = field(default_factory=dict, repr=False)

    def send_encrypted(self, data: bytes) -> str:
        """Encrypt data and send to GPU-TEE. Returns allocation reference."""
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("GPU channel payload must be bytes")
        ref = f"gpu-alloc-{uuid.uuid4().hex[:8]}"
        self._payload_store[ref] = _aead_encrypt(
            self.channel_key.key_material,
            bytes(data),
            aad=ref.encode(),
        )
        self._bytes_sent += len(data)
        return ref

    def receive_encrypted(self, ref: str) -> bytes:
        """Receive decrypted data from GPU-TEE."""
        envelope = self._payload_store.get(ref)
        if envelope is None:
            return b""
        data = _aead_decrypt(self.channel_key.key_material, envelope, aad=ref.encode())
        self._bytes_received += len(data)
        return data

    @property
    def stats(self) -> dict:
        return {"bytes_sent": self._bytes_sent, "bytes_received": self._bytes_received}


@dataclass
class EpochMetrics:
    """Metrics from a training epoch."""
    epoch: int = 0
    total_batches: int = 0
    total_loss: float = 0.0
    avg_loss: float = 0.0
    duration_seconds: float = 0.0
    gpu_memory_peak_mb: int = 0

    def update(self, loss: float) -> None:
        self.total_batches += 1
        self.total_loss += loss
        self.avg_loss = self.total_loss / self.total_batches


class GPUTeeRuntime(ABC):
    """Abstract interface for GPU-TEE runtime operations."""

    @abstractmethod
    async def check_status(self) -> GPUTeeStatus:
        """Check GPU-TEE availability."""
        ...

    @abstractmethod
    async def attest(self) -> AttestationReport:
        """Perform remote attestation of GPU-TEE environment."""
        ...

    @abstractmethod
    async def allocate_secure_memory(self, size_bytes: int, gpu_device: int = 0) -> SecureAllocation:
        """Allocate encrypted GPU memory within TEE."""
        ...

    @abstractmethod
    async def free_secure_memory(self, allocation_id: str) -> bool:
        """Free secure GPU memory allocation."""
        ...

    @abstractmethod
    async def submit_compute(self, mode: ComputeMode, input_data: bytes, gpu_device: int = 0) -> ComputeJob:
        """Submit a compute job to run inside GPU-TEE."""
        ...

    @abstractmethod
    async def get_job_status(self, job_id: str) -> ComputeJob | None:
        """Get status of a compute job."""
        ...

    @abstractmethod
    async def get_metrics(self, gpu_device: int = 0) -> GPUMetrics:
        """Get GPU resource metrics."""
        ...

    @abstractmethod
    async def initialize_gpu_cc(self, session_id: str, config: GPUCCConfig | None = None) -> GPUHandle:
        """Initialize GPU in Confidential Computing mode.

        1. Verify GPU CC mode is enabled
        2. Perform remote attestation
        3. Establish CPU-TEE ↔ GPU-TEE encrypted channel

        Returns:
            GPUHandle with encrypted channel ready for data transfer
        """
        ...

    @abstractmethod
    async def run_training_epoch(
        self,
        handle: GPUHandle,
        batches: list[bytes],
        model_ref: str = "",
    ) -> EpochMetrics:
        """Run one training epoch inside GPU-TEE.

        Args:
            handle: Active GPU-TEE handle with encrypted channel
            batches: Encrypted batch data to transfer to GPU
            model_ref: Reference to model weights in GPU memory

        Returns:
            EpochMetrics with loss and timing
        """
        ...


class GPUTeeRuntimeStub(GPUTeeRuntime):
    """Local software GPU-TEE runtime for development/testing.

    Provides the same interface as NVIDIA CC mode without requiring GPU
    hardware. Memory transfers are encrypted with AES-GCM, attestation reports
    are signed with a local root, and compute jobs record encrypted input and
    output allocations.
    """

    def __init__(self, gpu_model: str = "NVIDIA H100 (local)", gpu_memory_mb: int = 81920):
        self._gpu_model = gpu_model
        self._gpu_memory_mb = gpu_memory_mb
        self._allocations: dict[str, SecureAllocation] = {}
        self._jobs: dict[str, ComputeJob] = {}
        self._handles: dict[int, GPUHandle] = {}
        self._secure_payloads: dict[str, bytes] = {}
        self._channel_payloads: dict[str, bytes] = {}
        self._runtime_key = os.urandom(32)
        self._memory_used = 0

    async def check_status(self) -> GPUTeeStatus:
        return GPUTeeStatus.AVAILABLE

    async def attest(self) -> AttestationReport:
        report_id = str(uuid.uuid4())
        from app.utils.crypto import sm3_hash
        measurement = sm3_hash(f"{report_id}:local:{self._gpu_model}:{self._gpu_memory_mb}".encode())
        signature = sm3_hash(
            _LOCAL_GPU_ATTESTATION_ROOT
            + f"{report_id}:{self._gpu_model}:{measurement}".encode()
        )
        return AttestationReport(
            report_id=report_id,
            gpu_model=self._gpu_model,
            driver_version="local-1.0.0",
            tee_enabled=True,
            measurement=measurement,
            valid=True,
            signature=signature,
        )

    async def allocate_secure_memory(self, size_bytes: int, gpu_device: int = 0) -> SecureAllocation:
        if size_bytes <= 0:
            raise ValueError("size_bytes must be positive")
        if self._memory_used + size_bytes > self._gpu_memory_mb * 1024 * 1024:
            raise MemoryError("GPU secure memory capacity exceeded")
        alloc_id = str(uuid.uuid4())
        allocation = SecureAllocation(
            allocation_id=alloc_id,
            size_bytes=size_bytes,
            encrypted=True,
            gpu_device=gpu_device,
        )
        self._allocations[alloc_id] = allocation
        self._memory_used += size_bytes
        self._secure_payloads[alloc_id] = _aead_encrypt(self._runtime_key, b"\x00" * min(size_bytes, 4096), aad=alloc_id.encode())
        logger.info(f"GPU-TEE local: allocated {size_bytes} bytes on GPU {gpu_device}")
        return allocation

    async def free_secure_memory(self, allocation_id: str) -> bool:
        allocation = self._allocations.pop(allocation_id, None)
        if allocation:
            self._memory_used -= allocation.size_bytes
            self._secure_payloads.pop(allocation_id, None)
            return True
        return False

    async def submit_compute(self, mode: ComputeMode, input_data: bytes, gpu_device: int = 0) -> ComputeJob:
        job_id = str(uuid.uuid4())
        input_alloc = await self.allocate_secure_memory(max(len(input_data), 1), gpu_device)
        self._secure_payloads[input_alloc.allocation_id] = _aead_encrypt(
            self._runtime_key,
            input_data,
            aad=input_alloc.allocation_id.encode(),
        )
        output_data = self._execute_local_compute(mode, input_data)
        output_alloc = await self.allocate_secure_memory(max(len(output_data), 1), gpu_device)
        self._secure_payloads[output_alloc.allocation_id] = _aead_encrypt(
            self._runtime_key,
            output_data,
            aad=output_alloc.allocation_id.encode(),
        )
        job = ComputeJob(
            job_id=job_id,
            mode=mode,
            status="completed",
            gpu_device=gpu_device,
            input_allocation=input_alloc.allocation_id,
            output_allocation=output_alloc.allocation_id,
            started_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
        )
        self._jobs[job_id] = job
        logger.info(f"GPU-TEE local: compute job {job_id} completed (mode={mode.value})")
        return job

    async def get_job_status(self, job_id: str) -> ComputeJob | None:
        return self._jobs.get(job_id)

    async def get_metrics(self, gpu_device: int = 0) -> GPUMetrics:
        mem_used_pct = self._memory_used / (self._gpu_memory_mb * 1024 * 1024) if self._gpu_memory_mb > 0 else 0
        return GPUMetrics(
            gpu_device=gpu_device,
            gpu_model=self._gpu_model,
            memory_total_mb=self._gpu_memory_mb,
            memory_used_mb=int(self._memory_used / (1024 * 1024)),
            utilization_pct=mem_used_pct * 100,
            temperature_c=35.0 + min(mem_used_pct * 25.0, 30.0),
            tee_active=True,
        )

    async def initialize_gpu_cc(self, session_id: str, config: GPUCCConfig | None = None) -> GPUHandle:
        config = config or GPUCCConfig()

        # Step 1: Verify CC mode
        status = await self.check_status()
        if status != GPUTeeStatus.AVAILABLE:
            raise RuntimeError(f"GPU-TEE not available: {status.value}")

        # Step 2: Remote attestation
        report = await self.attest()
        if not report.valid:
            raise RuntimeError("GPU attestation failed")

        # Step 3: Derive channel key
        key_id = f"chkey-{uuid.uuid4().hex[:12]}"
        from app.utils.crypto import sm3_hash
        key_material = bytes.fromhex(sm3_hash(
            f"{key_id}:{report.measurement}:{session_id}".encode()
        ))
        channel_key = ChannelKey(
            key_id=key_id,
            key_material=key_material,
            gpu_measurement=report.measurement,
        )

        handle = GPUHandle(
            device_id=config.device_id,
            channel_key=channel_key,
            session_id=session_id,
            _payload_store=self._channel_payloads,
        )
        self._handles[handle.device_id] = handle
        logger.info(f"GPU-TEE CC initialized: device={config.device_id}, session={session_id}")
        return handle

    async def run_training_epoch(
        self,
        handle: GPUHandle,
        batches: list[bytes],
        model_ref: str = "",
    ) -> EpochMetrics:
        import time as _time
        start = _time.monotonic()
        metrics = EpochMetrics()

        for batch in batches:
            # Encrypt and send batch to GPU-TEE
            alloc_ref = handle.send_encrypted(batch)
            if handle.receive_encrypted(alloc_ref) != batch:
                raise RuntimeError("GPU channel round-trip integrity failed")
            # Local training proxy loss.
            simulated_loss = 0.5 / (metrics.total_batches + 1)
            metrics.update(simulated_loss)

        metrics.duration_seconds = _time.monotonic() - start
        metrics.gpu_memory_peak_mb = int(self._memory_used / (1024 * 1024)) + 256
        logger.info(f"GPU-TEE epoch: {metrics.total_batches} batches, avg_loss={metrics.avg_loss:.4f}")
        return metrics

    @property
    def allocations(self) -> dict[str, SecureAllocation]:
        return dict(self._allocations)

    @property
    def jobs(self) -> dict[str, ComputeJob]:
        return dict(self._jobs)

    def read_secure_payload(self, allocation_id: str) -> bytes:
        """Decrypt a local secure allocation for tests and internal consumers."""
        envelope = self._secure_payloads[allocation_id]
        return _aead_decrypt(self._runtime_key, envelope, aad=allocation_id.encode())

    def _execute_local_compute(self, mode: ComputeMode, input_data: bytes) -> bytes:
        from app.utils.crypto import sm3_hash

        digest = sm3_hash(mode.value.encode() + b":" + input_data)
        return f"{mode.value}:{digest}".encode()


class GPUTeeRuntimeFactory:
    """Factory for creating GPU-TEE runtime instances."""

    _implementations: dict[str, type] = {
        "stub": GPUTeeRuntimeStub,
        "local": GPUTeeRuntimeStub,
    }

    @classmethod
    def create(cls, implementation: str = "stub", **kwargs) -> GPUTeeRuntime:
        """Create a GPU-TEE runtime instance.

        When implementation is 'auto', checks CDS_GPU_TEE_SIMULATION config:
        - True  -> uses 'simulator' (CPU-based fallback)
        - False -> uses local software runtime
        """
        if implementation == "auto":
            from app.core.config import get_settings
            settings = get_settings()
            implementation = "simulator" if settings.GPU_TEE_SIMULATION else "local"

        impl_cls = cls._implementations.get(implementation)
        if not impl_cls:
            raise ValueError(f"Unknown GPU-TEE implementation: {implementation}")
        return impl_cls(**kwargs)

    @classmethod
    def register(cls, name: str, impl_cls: type) -> None:
        """Register a custom implementation."""
        cls._implementations[name] = impl_cls


# Default singleton -- use simulator when available, fall back to local runtime
try:
    from app.services.gpu_tee_simulator import GPUTeeSimulator
    gpu_tee_runtime: GPUTeeRuntime = GPUTeeSimulator()
    GPUTeeRuntimeFactory.register("simulator", GPUTeeSimulator)
except ImportError:
    gpu_tee_runtime: GPUTeeRuntime = GPUTeeRuntimeStub()
