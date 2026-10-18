"""TEE runtime capability detection.

This module only detects whether a host exposes a usable hardware TEE signal.
The sandbox runtime still requires an explicit execution command to run code in
that hardware environment.

Honesty contract (ocr A162 — 禁伪造 red line):
- Hardware evidence is a kernel character-device node (``stat.S_ISCHR``) for
  the provider's driver interface. A regular file, directory or dangling path
  at the device location is NOT evidence.
- Runtime tooling is resolved only against operator-trusted absolute
  directories (never the ambient PATH, which any writable PATH entry poisons)
  and the resolved binary must be a root-owned, non group/world-writable
  regular file directly under a trusted directory. Tooling alone is a
  *runtime signal*: ``hardware_available`` stays False until a device node is
  present, so consumers (monitoring ``tee_status``, the L1 provision path)
  can never publish "verified" from a PATH-resident name.
- An unknown ``TEE_MODE`` is disclosed as a configuration error
  (``provider="unknown"``), never misattributed as "no hardware signal".
"""
from __future__ import annotations

import os
import shutil
import stat
from dataclasses import dataclass, field


# Absolute directories command evidence may resolve from. Built for the
# current platform's path separator; on Windows these POSIX dirs resolve to
# nothing (fail-closed: no command evidence off Linux).
TRUSTED_COMMAND_DIRS: tuple[str, ...] = ("/usr/sbin", "/usr/bin", "/sbin", "/bin")
TRUSTED_COMMAND_PATH = os.pathsep.join(TRUSTED_COMMAND_DIRS)


def _is_hardware_device(path: str) -> bool:
    """True only for a kernel character device (the real driver interface).

    ``Path.exists()`` accepted plain files — an unprivileged writable path or a
    stray file is NOT hardware evidence (A162)."""
    try:
        st = os.stat(path)
    except OSError:
        return False
    return stat.S_ISCHR(st.st_mode)


def _is_trusted_command(path: str) -> bool:
    """True only for a regular executable directly under a trusted directory.

    POSIX additionally requires root ownership and no group/world write bit:
    a binary the deploy user can rewrite is attacker-controlled content."""
    if not os.path.isabs(path):
        return False
    norm = os.path.normpath(path)
    if os.path.dirname(norm) not in TRUSTED_COMMAND_DIRS:
        return False
    try:
        st = os.stat(norm)
    except OSError:
        return False
    if not stat.S_ISREG(st.st_mode) or not st.st_mode & 0o111:
        return False
    if os.name == "posix" and (st.st_uid != 0 or st.st_mode & 0o022):
        return False
    return True


@dataclass(frozen=True)
class TEECapability:
    provider: str
    hardware_available: bool
    evidence: list[str] = field(default_factory=list)
    reason: str = ""


class TEECapabilityDetector:
    """Detect available CPU TEE hardware/runtime signals."""

    _PROVIDERS: dict[str, dict[str, tuple[str, ...]]] = {
        "sgx": {
            "devices": ("/dev/sgx_enclave", "/dev/sgx/enclave", "/dev/isgx"),
            "commands": ("gramine-sgx", "occlum", "sgx-detect"),
        },
        "sev_snp": {
            "devices": ("/dev/sev-guest", "/dev/sev"),
            "commands": ("snpguest",),
        },
        "tdx": {
            "devices": ("/dev/tdx_guest", "/dev/tdx-guest"),
            "commands": ("tdx-attest", "tdx_guest_test", "trustauthority-cli"),
        },
        "itrustee": {
            "devices": ("/dev/teec", "/dev/tzdriver"),
            "commands": ("teecd", "itrustee_client"),
        },
    }

    def detect(self, requested: str = "auto") -> TEECapability:
        mode = (requested or "auto").strip().lower()
        if mode in {"software", "software_confidential", "none", "off"}:
            return TEECapability(
                provider="software_confidential",
                hardware_available=False,
                reason="TEE mode requests ordinary software confidential sandbox",
            )
        if mode not in {"auto", "hardware"} and mode not in self._PROVIDERS:
            # A162: a TEE_MODE typo is a configuration error — disclose it as
            # such instead of echoing the bad name as a provider and blaming
            # absent hardware.
            return TEECapability(
                provider="unknown",
                hardware_available=False,
                reason=(
                    f"unknown TEE_MODE {requested!r}: expected auto, hardware, software, "
                    f"or one of {', '.join(self._PROVIDERS)}"
                ),
            )

        providers = tuple(self._PROVIDERS) if mode in {"auto", "hardware"} else (mode,)
        runtime_evidence: list[str] = []
        runtime_provider = ""
        for provider in providers:
            spec = self._PROVIDERS[provider]
            devices, commands = self._detect_provider(spec)
            if devices:
                return TEECapability(
                    provider=provider,
                    hardware_available=True,
                    evidence=devices + commands,
                    reason=f"{provider} kernel TEE device detected",
                )
            if commands and not runtime_evidence:
                runtime_evidence, runtime_provider = commands, provider

        if runtime_evidence:
            # Runtime tooling without the driver node: honest runtime signal,
            # never the hardware verdict that would publish tee_status=verified.
            return TEECapability(
                provider=runtime_provider,
                hardware_available=False,
                evidence=runtime_evidence,
                reason=(
                    f"{runtime_provider} runtime tooling present but no kernel TEE device "
                    "node — hardware not confirmed"
                ),
            )

        return TEECapability(
            provider="software_confidential" if mode == "auto" else mode,
            hardware_available=False,
            reason=f"No hardware TEE signal detected for mode={mode}",
        )

    @staticmethod
    def _detect_provider(spec: dict[str, tuple[str, ...]]) -> tuple[list[str], list[str]]:
        devices: list[str] = []
        commands: list[str] = []
        for device in spec.get("devices", ()):
            if _is_hardware_device(device):
                devices.append(f"device:{device}")
        for command in spec.get("commands", ()):
            # path= pins the lookup: the ambient PATH (writable dirs, vendored
            # packages, /tmp) can never forge provider tooling (A162).
            path = shutil.which(command, path=TRUSTED_COMMAND_PATH)
            if path and _is_trusted_command(path):
                commands.append(f"command:{path}")
        return devices, commands
