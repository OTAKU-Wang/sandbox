"""PZ-04 honesty-batch gates (spec ss-11 §3.1 PZ-04; mock-remediation MR-A9/A12/A13/A14).

Covers:
- sm3 SHA-256 fallback gate + one-shot warning (crypto.sm3_hash, merkle_service._hash)
- column encryption DEK from CDS_COLUMN_ENCRYPTION_KEY (MR-A9)
- GPUTeeRuntimeStub attestation carries is_simulation=True / tee_backend (MR-A12)
- firecracker seccomp-less retry gated by SECCOMP_FALLBACK_ALLOWED (MR-A13, S4 closed)
"""
import base64
import os
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
    # 契约（sandbox_security.get_seccomp_bpf_fd）：交出去的是**本次调用独占**的 fd，
    # 调用方用完由 close_seccomp_fd 归还。这里以前写死 7 —— 那是 pytest 自己在用的
    # fd，产品一归还就把输出捕获的 fd 关掉，本文件 4 个测试连带同一次 pytest 里
    # 上百个文件的 teardown 全部 OSError: EBADF。给一个真正的 pipe fd。
    read_fd, write_fd = os.pipe()
    os.close(write_fd)
    monkeypatch.setattr(
        runtime,
        "_build_sim_bwrap_args",
        lambda workspace, code_file, language, session_key=None, env_vars=None: (
            ["bwrap", "--seccomp", str(read_fd), "chdir", "/workspace"],
            read_fd,
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


# ─────────── A132 GPU-TEE 双胞胎：模拟器披露 + auto 拒绝（2026-10-30）────────

@pytest.mark.asyncio
async def test_gpu_simulator_attestation_discloses_simulation():
    """MR-A12 只修了 stub；双胞胎 simulator 的 attest() 同族缺陷（走 dataclass
    默认值 is_simulation=False = 冒充真 GPU-TEE）A132 起必须与 stub 同口径。"""
    from app.services.gpu_tee_simulator import GPUTeeSimulator

    report = await GPUTeeSimulator().attest()
    assert report.is_simulation is True
    assert report.tee_backend == "cpu_simulation"


def test_gpu_simulator_source_pins_is_simulation_on_every_artifact():
    """结构针（源码级，与 inspect.getsource 系列同风格）：模拟器文件里所有共享
    产物 dataclass（默认 False=真硬件语义）的构造必须显式带 is_simulation=True。
    少一处，CPU 哈希就重新冒充真证明/真训练。"""
    import ast
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent / "app" / "services" / "gpu_tee_simulator.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    risky = {"GPUAttestationReport", "SecureAllocation", "ComputeJob", "GPUMetrics", "EpochMetrics"}
    constructions = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in risky
    ]
    assert len(constructions) >= 5
    for node in constructions:
        kw = {k.arg: k.value for k in node.keywords}
        assert "is_simulation" in kw, f"line {node.lineno}: {node.func.id} 缺 is_simulation"
        assert isinstance(kw["is_simulation"], ast.Constant) and kw["is_simulation"].value is True


def test_gpu_tee_auto_refuses_silent_fallback(monkeypatch):
    """SS-13 铁律的 GPU-TEE 分支：真实 CC 运行时不存在 + 未显式开模拟 → auto 必须
    400 RUNTIME_UNAVAILABLE 响亮拒绝，绝不静默降级到模拟运行时。"""
    from app.core.errors import CDSError
    from app.services.gpu_tee_runtime import GPUTeeRuntimeFactory

    settings = get_settings()
    monkeypatch.setattr(settings, "GPU_TEE_SIMULATION", False)
    with pytest.raises(CDSError) as ei:
        GPUTeeRuntimeFactory.create("auto")
    assert ei.value.code == "RUNTIME_UNAVAILABLE"
    assert ei.value.http_status == 400


def test_gpu_tee_channel_key_label_honest_about_sm4():
    """③标签改实：SM4Cipher 是 AES-256-GCM 替身且密钥 32 字节——SM4 真密钥
    128-bit，「SM4-GCM + 32B」双重不实标签不许复活（同 A121 DEK 长度门族）。"""
    from app.services.gpu_tee_simulator import GPUTeeSimulator

    ch = GPUTeeSimulator().create_secure_channel()
    assert ch.algorithm != "SM4-GCM"
    assert ch.algorithm.startswith("AES-256-GCM")
    assert len(ch.key) == 32
