"""TEE capability detection tests (ocr A162 honest-disclosure gate).

Pins the anti-forgery invariants: a TEE hardware claim requires a kernel
character-device node; PATH-resident command names alone are never hardware
evidence; unknown TEE_MODE is disclosed as a config error, not as "no
hardware".
"""
import os
import stat
import sys
from pathlib import Path

import pytest

import app.services.tee_capability as tc
from app.services.tee_capability import TEECapabilityDetector


def _fake_which(table):
    def _which(command, path=None):
        # path= must always be pinned by the detector; the fake records it.
        _which.paths.append(path)
        return table.get(command)
    _which.paths = []
    return _which


def test_tee_capability_detector_software_mode_skips_hardware():
    capability = TEECapabilityDetector().detect("software")

    assert capability.provider == "software_confidential"
    assert capability.hardware_available is False
    assert "software confidential" in capability.reason


def test_tee_capability_detector_detects_sgx_device_and_runtime(monkeypatch):
    monkeypatch.setattr(tc, "_is_hardware_device", lambda p: str(p) == "/dev/sgx_enclave")
    monkeypatch.setattr(tc, "_is_trusted_command", lambda p: p == "/usr/bin/gramine-sgx")
    which = _fake_which({"gramine-sgx": "/usr/bin/gramine-sgx"})
    monkeypatch.setattr("app.services.tee_capability.shutil.which", which)

    capability = TEECapabilityDetector().detect("sgx")

    assert capability.provider == "sgx"
    assert capability.hardware_available is True
    assert "device:/dev/sgx_enclave" in capability.evidence
    assert "command:/usr/bin/gramine-sgx" in capability.evidence
    # A162: command lookup must never run against the ambient PATH.
    assert all(p for p in which.paths), "shutil.which must be called with an explicit trusted path="


def test_tee_capability_detector_auto_falls_back_when_no_signal(monkeypatch):
    monkeypatch.setattr(tc, "_is_hardware_device", lambda p: False)
    monkeypatch.setattr("app.services.tee_capability.shutil.which", lambda command, path=None: None)

    capability = TEECapabilityDetector().detect("auto")

    assert capability.provider == "software_confidential"
    assert capability.hardware_available is False
    assert "No hardware TEE signal" in capability.reason


def test_plain_file_is_not_device_hardware_evidence(monkeypatch, tmp_path):
    """A162: a regular file where a /dev node is expected must not count."""
    fake_device = tmp_path / "sgx_enclave"
    fake_device.write_bytes(b"")  # st_mode 0o100644 — a plain file
    assert not stat.S_ISCHR(fake_device.stat().st_mode)
    providers = {"sgx": {"devices": (str(fake_device),), "commands": ()}}
    monkeypatch.setattr(TEECapabilityDetector, "_PROVIDERS", providers)

    capability = TEECapabilityDetector().detect("sgx")

    assert capability.hardware_available is False
    assert not any(e.startswith("device:") for e in capability.evidence)


@pytest.mark.skipif(os.name != "posix", reason="PATH poisoning via exec bit is POSIX-shaped; Windows uses .bat")
def test_command_in_writable_path_is_not_evidence(monkeypatch, tmp_path):
    """A162: a same-named script in a PATH-writable directory must not forge a TEE."""
    fake = tmp_path / "sgx-detect"
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(tc, "_is_hardware_device", lambda p: False)

    capability = TEECapabilityDetector().detect("sgx")

    assert capability.hardware_available is False
    assert not any(str(tmp_path) in e for e in capability.evidence)


def test_command_found_by_which_outside_trusted_dirs_is_not_evidence(monkeypatch, tmp_path):
    fake = tmp_path / "gramine-sgx"
    fake.write_text("")
    which = _fake_which({"gramine-sgx": str(fake)})
    monkeypatch.setattr("app.services.tee_capability.shutil.which", which)
    monkeypatch.setattr(tc, "_is_hardware_device", lambda p: False)

    capability = TEECapabilityDetector().detect("sgx")

    assert capability.hardware_available is False
    assert not any(e.startswith("command:") for e in capability.evidence)


def test_runtime_tools_without_device_are_not_hardware(monkeypatch):
    """A162: trusted tooling alone is a runtime signal, never `hardware_available`."""
    monkeypatch.setattr(tc, "_is_hardware_device", lambda p: False)
    monkeypatch.setattr(tc, "_is_trusted_command", lambda p: True)
    monkeypatch.setattr("app.services.tee_capability.shutil.which",
                        _fake_which({"tdx-attest": "/usr/bin/tdx-attest"}))

    capability = TEECapabilityDetector().detect("tdx")

    assert capability.hardware_available is False
    assert "command:/usr/bin/tdx-attest" in capability.evidence
    assert "not confirmed" in capability.reason


def test_unknown_mode_discloses_config_error(monkeypatch):
    """A162: a TEE_MODE typo must surface as an unknown-mode config error."""
    monkeypatch.setattr(tc, "_is_hardware_device", lambda p: False)
    monkeypatch.setattr("app.services.tee_capability.shutil.which", lambda command, path=None: None)

    capability = TEECapabilityDetector().detect("tdx_guest")

    assert capability.provider == "unknown"
    assert capability.hardware_available is False
    assert "unknown TEE_MODE" in capability.reason
    assert "No hardware TEE signal" not in capability.reason


def test_is_trusted_command_rejects_untrusted_shapes(tmp_path):
    inside = tmp_path / "tool"
    inside.write_text("")
    inside.chmod(0o755)
    assert tc._is_trusted_command(str(inside)) is False          # not under a trusted dir
    assert tc._is_trusted_command("gramine-sgx") is False        # relative
    assert tc._is_trusted_command(str(tmp_path)) is False        # not a regular file
    assert tc._is_trusted_command("/nonexistent/dir/tool") is False


@pytest.mark.skipif(sys.platform != "linux", reason="root-ownership check is POSIX/Linux-only")
def test_is_trusted_command_accepts_root_owned_system_binary():
    for candidate in ("/usr/bin/sh", "/bin/sh"):
        if os.path.exists(candidate):
            # If the CI box owns it with group/world writability, this legitimately
            # fails — that would itself be a compromised-host finding, not a bug.
            st = os.stat(candidate)
            if st.st_uid == 0 and not st.st_mode & 0o022:
                assert tc._is_trusted_command(candidate) is True
            else:
                assert tc._is_trusted_command(candidate) is False
            return
    pytest.skip("no /bin/sh on this host")
