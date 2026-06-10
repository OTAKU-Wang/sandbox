"""TEE runtime capability detection.

This module only detects whether a host exposes a usable hardware TEE signal.
The sandbox runtime still requires an explicit execution command to run code in
that hardware environment.
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path


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

        providers = tuple(self._PROVIDERS) if mode == "auto" else (mode,)
        for provider in providers:
            spec = self._PROVIDERS.get(provider)
            if not spec:
                continue
            evidence = self._detect_provider(spec)
            if evidence:
                return TEECapability(
                    provider=provider,
                    hardware_available=True,
                    evidence=evidence,
                    reason=f"{provider} hardware/runtime signal detected",
                )

        return TEECapability(
            provider="software_confidential" if mode == "auto" else mode,
            hardware_available=False,
            reason=f"No hardware TEE signal detected for mode={mode}",
        )

    @staticmethod
    def _detect_provider(spec: dict[str, tuple[str, ...]]) -> list[str]:
        evidence: list[str] = []
        for device in spec.get("devices", ()):
            if Path(device).exists():
                evidence.append(f"device:{device}")
        for command in spec.get("commands", ()):
            path = shutil.which(command)
            if path:
                evidence.append(f"command:{path}")
        return evidence
