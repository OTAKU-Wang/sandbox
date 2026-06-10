from pathlib import Path

from app.services.tee_capability import TEECapabilityDetector


def test_tee_capability_detector_software_mode_skips_hardware():
    capability = TEECapabilityDetector().detect("software")

    assert capability.provider == "software_confidential"
    assert capability.hardware_available is False
    assert "software confidential" in capability.reason


def test_tee_capability_detector_detects_sgx_device_and_runtime(monkeypatch):
    def fake_exists(path):
        return str(path) == "/dev/sgx_enclave"

    def fake_which(command):
        return "/usr/bin/gramine-sgx" if command == "gramine-sgx" else None

    monkeypatch.setattr(Path, "exists", fake_exists)
    monkeypatch.setattr("app.services.tee_capability.shutil.which", fake_which)

    capability = TEECapabilityDetector().detect("sgx")

    assert capability.provider == "sgx"
    assert capability.hardware_available is True
    assert "device:/dev/sgx_enclave" in capability.evidence
    assert "command:/usr/bin/gramine-sgx" in capability.evidence


def test_tee_capability_detector_auto_falls_back_when_no_signal(monkeypatch):
    monkeypatch.setattr(Path, "exists", lambda path: False)
    monkeypatch.setattr("app.services.tee_capability.shutil.which", lambda command: None)

    capability = TEECapabilityDetector().detect("auto")

    assert capability.provider == "software_confidential"
    assert capability.hardware_available is False
    assert "No hardware TEE signal" in capability.reason
