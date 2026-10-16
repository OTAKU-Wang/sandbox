"""PZ-04 honesty-batch gates (spec ss-11 §3.1 PZ-04; mock-remediation MR-A9/A12/A13/A14).

Covers:
- sm3 SHA-256 fallback gate + one-shot warning (crypto.sm3_hash, merkle_service._hash)
- column encryption DEK from CDS_COLUMN_ENCRYPTION_KEY (MR-A9)
- GPUTeeRuntimeStub attestation carries is_simulation=True / tee_backend (MR-A12)
- firecracker seccomp-less retry gated by SECCOMP_FALLBACK_ALLOWED (MR-A13, S4 closed)
"""
import base64
import uuid
from types import SimpleNamespace

import pytest

from app.core.config import get_settings


# ────────────────────────── sm3 fallback (MR-A14) ──────────────────────────

def test_sm3_hash_uses_real_sm3_when_gmssl_present():
    import gmssl  # noqa: F401 — presence is asserted by the env contract (venv)
    from app.utils import crypto
    from app.utils.crypto import sm3_hash

    digest = sm3_hash(b"hello")
    assert digest == gmssl.sm3.sm3_hash(list(b"hello"))
    # real SM3 path must not trip the fallback warning flag
    assert crypto._sm3_fallback_warned is False


def test_sm3_hash_fallback_warns_once_when_gmssl_missing(monkeypatch, caplog):
    import logging

    import app.utils.crypto as crypto

    monkeypatch.setitem(__import__("sys").modules, "gmssl", None)
    monkeypatch.setattr(crypto, "_sm3_fallback_warned", False)
    settings = get_settings()
    monkeypatch.setattr(settings, "SM3_FALLBACK_ALLOWED", True)

    import hashlib

    with caplog.at_level(logging.WARNING, logger="app.utils.crypto"):
        first = crypto.sm3_hash(b"hello")
        second = crypto.sm3_hash(b"hello")
    assert first == second == hashlib.sha256(b"hello").hexdigest()
    fallback_warnings = [r for r in caplog.records if "SM3" in r.getMessage() and "fall back" in r.getMessage()]
    assert len(fallback_warnings) == 1  # one warning per process
    assert crypto._sm3_fallback_warned is True


def test_sm3_hash_refuses_fallback_when_disallowed(monkeypatch):
    import app.utils.crypto as crypto

    monkeypatch.setitem(__import__("sys").modules, "gmssl", None)
    settings = get_settings()
    monkeypatch.setattr(settings, "SM3_FALLBACK_ALLOWED", False)
    with pytest.raises(RuntimeError, match="SM3_FALLBACK_ALLOWED"):
        crypto.sm3_hash(b"hello")


def test_merkle_hash_refuses_fallback_when_disallowed(monkeypatch):
    import app.services.merkle_service as ms

    monkeypatch.setattr(ms, "_GMSSL_AVAILABLE", False)
    settings = get_settings()
    monkeypatch.setattr(settings, "SM3_FALLBACK_ALLOWED", False)
    with pytest.raises(RuntimeError, match="SM3_FALLBACK_ALLOWED"):
        ms._hash(b"leaf")


def test_crypto_service_sm3_refuses_fallback_when_disallowed(monkeypatch):
    """CR-2026-10-15: audit Merkle leaves are hashed by crypto_service.sm3_hash,
    which used to be the one SM3 implementation outside the gate — it would
    mint SHA-256 "SM3" roots in production with the gate set to false."""
    import sys

    import app.services.crypto_service as cs

    # Both layers must see gmssl as unavailable: the deployment image installs
    # gmssl (requirements.txt), where flipping only `cs._HAS_GMSSL` would leave
    # the delegated app.utils.crypto implementation free to hash real SM3 and
    # never consult the gate. Removing the module makes the test independent of
    # whether the interpreter running it happens to have gmssl.
    monkeypatch.setitem(sys.modules, "gmssl", None)
    monkeypatch.setattr(cs, "_HAS_GMSSL", False)
    settings = get_settings()
    monkeypatch.setattr(settings, "SM3_FALLBACK_ALLOWED", False)
    with pytest.raises(RuntimeError, match="SM3_FALLBACK_ALLOWED"):
        cs.crypto_service.sm3_hash(b"leaf")


def test_merkle_hash_fallback_warns_when_gmssl_missing(monkeypatch, caplog):
    import hashlib
    import logging

    import app.services.merkle_service as ms

    monkeypatch.setattr(ms, "_GMSSL_AVAILABLE", False)
    monkeypatch.setattr(ms, "_hash_fallback_warned", False)
    settings = get_settings()
    monkeypatch.setattr(settings, "SM3_FALLBACK_ALLOWED", True)

    with caplog.at_level(logging.WARNING, logger="app.services.merkle_service"):
        assert ms._hash(b"leaf") == hashlib.sha256(b"leaf").digest()
    assert ms._hash_fallback_warned is True
    warnings = [r for r in caplog.records if "SHA-256" in r.getMessage()]
    assert len(warnings) == 1


# ─────────────────────── column encryption DEK (MR-A9) ─────────────────────

def _build_with_key(monkeypatch, raw):
    import app.services.column_encryption as ce

    settings = get_settings()
    monkeypatch.setattr(settings, "COLUMN_ENCRYPTION_KEY", raw)
    monkeypatch.setattr(settings, "DEBUG", False)
    monkeypatch.delenv("TESTING", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    return ce.build_column_encryption()


def test_column_dek_from_settings_hex(monkeypatch):
    dek_hex = "00112233445566778899aabbccddeeff"
    eng = _build_with_key(monkeypatch, dek_hex)
    assert eng.dek == bytes.fromhex(dek_hex)
    assert eng.key_id == "cds-configured"


def test_column_dek_from_settings_base64(monkeypatch):
    raw = base64.b64encode(b"K" * 16).decode()
    eng = _build_with_key(monkeypatch, raw)
    assert eng.dek == b"K" * 16
    assert eng.key_id == "cds-configured"


def test_column_dek_invalid_raises(monkeypatch):
    for bad in ("abc", "zzzz", base64.b64encode(b"short").decode()):
        with pytest.raises(ValueError, match="CDS_COLUMN_ENCRYPTION_KEY"):
            _build_with_key(monkeypatch, bad)


def test_column_dek_empty_in_prod_raises(monkeypatch):
    import app.services.column_encryption as ce

    settings = get_settings()
    monkeypatch.setattr(settings, "COLUMN_ENCRYPTION_KEY", "")
    monkeypatch.setattr(settings, "DEBUG", False)
    monkeypatch.delenv("TESTING", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    with pytest.raises(ValueError, match="CDS_COLUMN_ENCRYPTION_KEY"):
        ce.build_column_encryption()


def test_column_dek_empty_in_dev_keeps_default(monkeypatch):
    import app.services.column_encryption as ce

    settings = get_settings()
    monkeypatch.setattr(settings, "COLUMN_ENCRYPTION_KEY", "")
    monkeypatch.setattr(settings, "DEBUG", True)
    eng = ce.build_column_encryption()
    assert eng.key_id == "cds-default"
    assert eng.dek == ce._default_dek


def test_column_roundtrip_with_configured_key(monkeypatch):
    import app.services.column_encryption as ce
    from app.services.column_encryption import EncryptionMode

    eng = _build_with_key(monkeypatch, "aabbccddeeff00112233445566778899")
    enc_det = eng.encrypt_column("13800000000", "phone", mode=EncryptionMode.DETERMINISTIC)
    assert eng.decrypt_column(enc_det) == "13800000000"
    enc_gcm = eng.encrypt_column("user-42", "id", mode=EncryptionMode.RANDOMIZED)
    assert eng.decrypt_column(enc_gcm) == "user-42"


# ──────────────── GPU stub is_simulation disclosure (MR-A12) ───────────────

@pytest.mark.asyncio
async def test_gpu_stub_attestation_discloses_simulation():
    from app.services.gpu_tee_runtime import GPUTeeRuntimeStub

    stub = GPUTeeRuntimeStub()
    report = await stub.attest()
    assert report.is_simulation is True
    assert report.tee_backend == "local_software"


def test_attestation_report_defaults_are_not_simulation():
    from app.services.gpu_tee_runtime import AttestationReport
    from datetime import datetime, timezone

    r = AttestationReport(
        report_id="x", gpu_model="m", driver_version="d",
        tee_enabled=True, measurement="00",
        timestamp=datetime.now(timezone.utc),
    )
    assert r.is_simulation is False
    assert r.tee_backend == ""


# ─────────── firecracker seccomp gate (MR-A13, S4 closed) ──────────────────

def _mk_vm(tmp_path):
    from app.services.firecracker_runtime import VMConfig, VMInstance, VMState

    ws = tmp_path / "ws"
    (ws / "tmp").mkdir(parents=True)
    return VMInstance(
        vm_id=f"vm-{uuid.uuid4().hex[:8]}",
        session_id="sess-pz04",
        socket_path="/tmp/nope.sock",
        state=VMState.CREATED,
        config=VMConfig(network_enabled=False),
        workspace=str(ws),
    )


def _patch_bwrap(monkeypatch, runtime):
    monkeypatch.setattr(
        runtime,
        "_build_sim_bwrap_args",
        lambda workspace, code_file, language, session_key=None, env_vars=None: (
            ["bwrap", "--seccomp", "7", "chdir", "/workspace"],
            7,
        ),
    )


def _runtime():
    from app.services.firecracker_runtime import firecracker_runtime
    return firecracker_runtime


def test_firecracker_seccomp_retry_allowed(monkeypatch, tmp_path):
    from app.services import firecracker_runtime as fr

    settings = get_settings()
    monkeypatch.setattr(settings, "SECCOMP_FALLBACK_ALLOWED", True)
    runtime = _runtime()
    vm = _mk_vm(tmp_path)
    _patch_bwrap(monkeypatch, runtime)
    calls: list = []
    monkeypatch.setattr(fr.subprocess, "run", lambda *a, **kw: (calls.append(list(a[0] if a else kw.get("cmd"))), SimpleNamespace(returncode=1, stdout="", stderr="bwrap: --seccomp: EINVAL"))[1])

    result = runtime._simulate_execute(vm, "print(1)", "python")
    assert len(calls) == 2  # retried once, without seccomp
    assert "--seccomp" not in calls[1]


def test_firecracker_seccomp_retry_denied_fails_honestly(monkeypatch, tmp_path):
    from app.services import firecracker_runtime as fr

    settings = get_settings()
    monkeypatch.setattr(settings, "SECCOMP_FALLBACK_ALLOWED", False)
    runtime = _runtime()
    vm = _mk_vm(tmp_path)
    _patch_bwrap(monkeypatch, runtime)
    calls: list = []
    monkeypatch.setattr(fr.subprocess, "run", lambda *a, **kw: (calls.append(list(a[0] if a else kw.get("cmd"))), SimpleNamespace(returncode=1, stdout="", stderr="bwrap: --seccomp: EINVAL"))[1])

    result = runtime._simulate_execute(vm, "print(1)", "python")
    assert len(calls) == 1  # NO seccomp-less retry
    assert result.exit_code == 1
    assert "SECURITY ERROR" in result.output
    assert "SECCOMP_FALLBACK_ALLOWED" in result.output


def test_firecracker_qemu_serial_seccomp_retry_denied(monkeypatch, tmp_path):
    from app.services import firecracker_runtime as fr

    settings = get_settings()
    monkeypatch.setattr(settings, "SECCOMP_FALLBACK_ALLOWED", False)
    runtime = _runtime()
    vm = _mk_vm(tmp_path)
    _patch_bwrap(monkeypatch, runtime)
    calls: list = []
    monkeypatch.setattr(fr.subprocess, "run", lambda *a, **kw: (calls.append(list(a[0] if a else kw.get("cmd"))), SimpleNamespace(returncode=1, stdout="", stderr="bwrap: --seccomp: EINVAL"))[1])

    result = runtime._execute_via_qemu_serial(vm, "print(1)", "python")
    assert len(calls) == 1
    assert result["exit_code"] == 1
    assert "SECURITY ERROR" in result["output"]


def test_firecracker_qemu_serial_seccomp_retry_allowed(monkeypatch, tmp_path):
    from app.services import firecracker_runtime as fr

    settings = get_settings()
    monkeypatch.setattr(settings, "SECCOMP_FALLBACK_ALLOWED", True)
    runtime = _runtime()
    vm = _mk_vm(tmp_path)
    _patch_bwrap(monkeypatch, runtime)
    calls: list = []
    monkeypatch.setattr(fr.subprocess, "run", lambda *a, **kw: (calls.append(list(a[0] if a else kw.get("cmd"))), SimpleNamespace(returncode=1, stdout="", stderr="bwrap: --seccomp: EINVAL"))[1])

    result = runtime._execute_via_qemu_serial(vm, "print(1)", "python")
    assert len(calls) == 2
    assert "--seccomp" not in calls[1]
