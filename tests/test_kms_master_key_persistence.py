"""P2-3 — KMS 软件后端主密钥持久化（todo-plan 2026-10-30，ss-19 §10.1-2）。

根因台账（D-e2e-14 / ss-19 P-06）：软件 HSM 的 KEK 只活在本进程，容器重启后
既有信封对象不可解密（现已诚实报错，根因未修）。修复：`CDS_KMS_MASTER_KEY_FILE`
指向挂载卷路径 → 首启 0600 生成 32 字节主密钥，KEK 由 HMAC-SHA256(master,
"cds-kms-kek-v1:"+key_id) 确定性派生，同路径重启后旧信封照常解密。

本文件钉住交付判据：
1. 重启模拟：同路径二次加载可解密旧信封（关单判据）；
2. 权限位/损坏/路径缺失全部 fail-closed（0600、拒载、拒生成、不静默 mkdir）；
3. prod 门：validate_security_config 对配了但不可用的路径拒启（仿 15 枚 raise 形）；
4. 未配置 = 旧行为逐字节不变（无文件、随机 KEK、披露仍喊内存态）；
5. 信封布局 [nonce(12)][tag(16)][ct] 不变 → 既有对象不破坏；
6. 诚实披露三态 + 首启审计挂点（源码级，仿 a94b 口径）。
"""
import logging
import os
import stat

import pytest

import app.main as main
from app.core.config import get_settings
from app.services import hsm_adapter as hsm

KEY_ID = "cds-master-kek"


def _prod_shape(monkeypatch, **over):
    """test_security_config_matrix 同款 prod 形：改缓存 Settings + delenv 两个测试标记。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "DEBUG", False)
    monkeypatch.setattr(settings, "JWT_SECRET_KEY", "k" * 40)
    monkeypatch.setattr(settings, "SECCOMP_FALLBACK_ALLOWED", False)
    monkeypatch.setattr(settings, "HSM_SOFTWARE_FALLBACK_ALLOWED", False)
    monkeypatch.setattr(settings, "FEDERATION_JWT_KEY_REQUIRED", True)
    monkeypatch.setattr(settings, "SM3_FALLBACK_ALLOWED", False)
    monkeypatch.setattr(settings, "COLUMN_ENCRYPTION_KEY", "00112233445566778899aabbccddeeff")
    monkeypatch.setattr(settings, "MFA_SECRET_ENCRYPTION_KEY", "mfa-test-master-secret")
    monkeypatch.setattr(settings, "ALLOW_ADMIN_SELF_REGISTER", False)
    monkeypatch.setattr(settings, "FEDERATION_ENABLED", False)
    monkeypatch.setattr(settings, "KMS_REQUIRE_ATTESTATION", False)
    # suite 环境把 legacy 沙箱档位 pin 成 true（conftest SS-10/13 集）——prod 形
    # 必须一并收紧，否则先撞 SS-13 单路径门，测不到本门。
    monkeypatch.setattr(settings, "SANDBOX_LEGACY_ENABLED", False)
    # 同款理由（2026-11-05）：conftest pin CUBE_DATAPLANE_ENABLED=true，而本机
    # .env 的 dev 明文数据面（CUBE_NODE_GRPC_TLS=false）会被 Settings 捡进来——
    # prod 形不收紧就先撞 Cube TLS 门，测不到 KMS 主密钥门。CI 上 Settings
    # 默认 true 所以历史绿；这里 pin 的是形态，不是本机偶然值。
    monkeypatch.setattr(settings, "CUBE_NODE_GRPC_TLS", True)
    # TLS=true 后下一道门要 mTLS 材料；conftest 本就 pin 同一测试 CA 路径
    # （:59），本机 shell 漏入的空 CDS_CUBE_NODE_TLS_CA 会盖掉 setdefault——
    # prod 形按 suite 口径补齐，与上面同族。
    monkeypatch.setattr(settings, "CUBE_NODE_TLS_CA", "/etc/cds/cube-node/test-node-ca.pem")
    # 再下一道门：DATAPLANE=true 要模板分发签名 key（conftest:60 同款 pin）。
    monkeypatch.setattr(settings, "CUBE_TEMPLATE_SIGNING_KEY", "test-signing-key")
    for k, v in over.items():
        monkeypatch.setattr(settings, k, v)
    monkeypatch.delenv("TESTING", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    return settings


# ---------------------------------------------------------------- ① 重启模拟
def test_restart_roundtrip_decrypts_old_envelope(tmp_path):
    """关单判据：适配器实例 1 包的信封，全新实例 2（同文件）照常解。"""
    path = str(tmp_path / "master.key")
    a1 = hsm.SoftwareHSMAdapter(master_key_file=path)
    assert a1.master_key_just_generated is True
    a1.generate_kek(KEY_ID)  # kms_service._ensure_kek 同款认领
    dek = os.urandom(32)
    wrapped = a1.wrap_dek(KEY_ID, dek)

    a2 = hsm.SoftwareHSMAdapter(master_key_file=path)  # 「容器重启」
    assert a2.master_key_just_generated is False
    assert a2.generate_kek(KEY_ID)  # 首启认领路径同型：重启后重新认领 KEK
    assert a2.unwrap_dek(KEY_ID, wrapped) == dek
    # 新包旧解 / 旧包新解双向都通
    old_wrapped = a2.wrap_dek(KEY_ID, dek)
    assert a1.unwrap_dek(KEY_ID, old_wrapped) == dek


def test_wrong_file_cannot_decrypt(tmp_path):
    """持久化不是万能钥匙：另一把主密钥必须解不开。"""
    p1, p2 = str(tmp_path / "k1"), str(tmp_path / "k2")
    dek = os.urandom(32)
    a1 = hsm.SoftwareHSMAdapter(master_key_file=p1)
    a1.generate_kek(KEY_ID)
    wrapped = a1.wrap_dek(KEY_ID, dek)
    other = hsm.SoftwareHSMAdapter(master_key_file=p2)
    other.generate_kek(KEY_ID)
    with pytest.raises(Exception):
        other.unwrap_dek(KEY_ID, wrapped)


# ---------------------------------------------------------------- ② 权限位/损坏
def test_master_key_file_created_0600(tmp_path):
    path = tmp_path / "master.key"
    hsm.SoftwareHSMAdapter(master_key_file=str(path))
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600, f"主密钥文件必须 0600，实际 {oct(mode)}"


def test_loose_perm_file_refuses_load(tmp_path):
    path = tmp_path / "master.key"
    hsm.SoftwareHSMAdapter(master_key_file=str(path))
    path.chmod(0o644)
    with pytest.raises(RuntimeError, match="权限过宽"):
        hsm.SoftwareHSMAdapter(master_key_file=str(path))


def test_corrupt_master_key_refuses_start(tmp_path):
    """损坏文件宁可拒启，绝不悄悄生成新的把旧信封陪葬。"""
    path = tmp_path / "master.key"
    path.write_bytes(b"too-short")
    path.chmod(0o600)  # 权限合法、内容损坏——才能测到「损坏拒载」而不是「权限拒载」
    with pytest.raises(RuntimeError, match="损坏"):
        hsm.SoftwareHSMAdapter(master_key_file=str(path))


def test_missing_parent_dir_refuses_generation(tmp_path):
    """挂载卷没挂上 = 路径不可用：fail-closed，不静默 mkdir 造一个假的「成功」。"""
    path = str(tmp_path / "unmounted-volume" / "master.key")
    with pytest.raises(RuntimeError, match="父目录缺失或不可写"):
        hsm.SoftwareHSMAdapter(master_key_file=path)
    assert not os.path.exists(tmp_path / "unmounted-volume")


# ---------------------------------------------------------------- ③ 生产门
def test_prod_gate_raises_on_unusable_master_key_path(monkeypatch, tmp_path):
    settings = _prod_shape(
        monkeypatch,
        KMS_MASTER_KEY_FILE=str(tmp_path / "no-such-volume" / "master.key"),
    )
    with pytest.raises(ValueError, match="KMS_MASTER_KEY_FILE"):
        settings.validate_security_config()


def test_prod_gate_message_precedes_hsm_verdict(monkeypatch, tmp_path):
    """路径诊断优先于口径拒：运维先看到「卷没挂上」，不是泛泛的 HSM 政策拒。"""
    settings = _prod_shape(
        monkeypatch,
        HSM_SOFTWARE_FALLBACK_ALLOWED=True,
        KMS_MASTER_KEY_FILE=str(tmp_path / "gone" / "master.key"),
    )
    with pytest.raises(ValueError) as ei:
        settings.validate_security_config()
    assert "KMS_MASTER_KEY_FILE" in str(ei.value)
    assert "HSM_SOFTWARE_FALLBACK_ALLOWED" not in str(ei.value)


def test_prod_gate_passes_with_usable_paths(tmp_path, monkeypatch):
    """prod 下可用路径（已存在的文件 / 可写父目录下的新文件）都不得触发本门。"""
    existing = tmp_path / "master.key"
    existing.write_bytes(os.urandom(32))
    for value in (str(existing), str(tmp_path / "fresh.key")):
        settings = _prod_shape(monkeypatch, KMS_MASTER_KEY_FILE=value)
        issues = settings.validate_security_config()  # 不 raise 即过门
        assert not any("KMS_MASTER_KEY_FILE" in i for i in issues)


# ---------------------------------------------------------------- ④ 旧行为不变
def test_unset_file_keeps_legacy_in_memory_behavior(tmp_path):
    """未配置 = 逐字节旧行为：不落任何文件，两次生成互不相同。"""
    adapter = hsm.SoftwareHSMAdapter()
    assert adapter.master_key_file == ""
    assert adapter.master_key_just_generated is False
    assert list(tmp_path.iterdir()) == []
    k1 = adapter.generate_kek("a")
    k2 = adapter.generate_kek("b")
    assert k1 != k2
    assert adapter.is_simulation is True  # 持久化不是硬件豁免


def test_envelope_layout_unchanged(tmp_path):
    """[nonce(12)][tag(16)][ct] 布局不变 → 既有存储对象不破坏。"""
    for adapter in (hsm.SoftwareHSMAdapter(),
                    hsm.SoftwareHSMAdapter(master_key_file=str(tmp_path / "k"))):
        dek = os.urandom(32)
        adapter.generate_kek(KEY_ID)
        wrapped = adapter.wrap_dek(KEY_ID, dek)
        assert len(wrapped) == 12 + 16 + len(dek)
        assert adapter.unwrap_dek(KEY_ID, wrapped) == dek


# ---------------------------------------------------------------- ⑤ 披露与审计
def test_boot_disclosure_three_states(monkeypatch, tmp_path, caplog):
    path = str(tmp_path / "master.key")
    persistent = hsm.SoftwareHSMAdapter(master_key_file=path)
    monkeypatch.setattr(hsm, "hsm_adapter", persistent, raising=False)
    with caplog.at_level(logging.INFO, logger="app.main"):
        desc = main.hsm_backend_disclosure()
    line = next(r for r in caplog.records if "HSM backend=" in r.getMessage())
    assert line.levelno >= logging.WARNING
    assert "软件主密钥文件（非 HSM）" in line.getMessage() and path in line.getMessage()
    assert desc["master_key_persistent"] is True and desc["master_key_file"] == path

    caplog.clear()
    monkeypatch.setattr(hsm, "hsm_adapter", hsm.SoftwareHSMAdapter(), raising=False)
    with caplog.at_level(logging.INFO, logger="app.main"):
        desc = main.hsm_backend_disclosure()
    line = next(r for r in caplog.records if "HSM backend=" in r.getMessage())
    assert "KEK 活在本进程" in line.getMessage(), "未持久化的软件后端必须继续喊内存态"
    assert desc["master_key_persistent"] is False


def test_first_boot_audit_hook_wired_in_lifespan():
    """首启生成 → kms.master_key.generated 审计挂在 lifespan（import 期无 DB）。

    源码级（仿 test_ocr_a94b 对披露挂点的钉子形）：审计事件名与
    master_key_just_generated 判定必须都在启动路径源码里，缺一即挂点被拆。
    """
    import inspect

    src = inspect.getsource(getattr(main.lifespan, "__wrapped__", main.lifespan))
    assert "kms.master_key.generated" in src
    assert "master_key_just_generated" in src


def test_just_generated_flag_only_on_first_boot(tmp_path):
    path = str(tmp_path / "master.key")
    assert hsm.SoftwareHSMAdapter(master_key_file=path).master_key_just_generated is True
    assert hsm.SoftwareHSMAdapter(master_key_file=path).master_key_just_generated is False
