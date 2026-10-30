"""A121 半边（父会话路由 poolA2）— CUBE_DEK_LENGTH 与节点卷插件编译期 dekLen 缺启动期相等断言。

`app/core/config.py:356 CUBE_DEK_LENGTH`（运行时可配，注入沙箱 env 的 CDS_DEK 长度）与
`cds-volume-plugin/cipher.go:26 dekLen = 16`（Go 侧编译期常量，加密卷实际用的 DEK 宽度）
之间没有任何启动期一致性检查：运维把 `CDS_CUBE_DEK_LENGTH` 配成 32，控制面会签发 32 字节
DEK、按 SM4 语义放行，而节点插件的 SM4-GCM 腿在 `len(key) != dekLen` 处静默失配。
本文件锁两侧口径：Go 常量按源码解析（漂移时先红本测试），dataplane 打开且宽度不等时
`validate_security_config` 必须 REJECT；dataplane 关闭（默认部署/dev）不得受影响。
"""
import re
from pathlib import Path

import pytest

from app.core.config import get_settings

REPO = Path(__file__).resolve().parents[1]
CIPHER_GO = REPO / "cds-volume-plugin" / "cipher.go"


def _go_dek_len() -> int:
    src = CIPHER_GO.read_text(encoding="utf-8")
    m = re.search(r"^\s*dekLen\s*=\s*(\d+)\s*$", src, re.M)
    assert m, "cds-volume-plugin/cipher.go 里的 dekLen 常量不见了——跨语言口径需重新对齐"
    return int(m.group(1))


def _prod_baseline(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "DEBUG", False)
    monkeypatch.setattr(settings, "JWT_SECRET_KEY", "k" * 40)
    monkeypatch.setattr(settings, "SECCOMP_FALLBACK_ALLOWED", False)
    monkeypatch.setattr(settings, "HSM_SOFTWARE_FALLBACK_ALLOWED", False)
    monkeypatch.setattr(settings, "ALLOW_ADMIN_SELF_REGISTER", False)
    monkeypatch.setattr(settings, "FEDERATION_JWT_KEY_REQUIRED", True)
    monkeypatch.setattr(settings, "SM3_FALLBACK_ALLOWED", False)
    monkeypatch.setattr(settings, "COLUMN_ENCRYPTION_KEY", "00112233445566778899aabbccddeeff")
    monkeypatch.setattr(settings, "MFA_SECRET_ENCRYPTION_KEY", "mfa-test-master-secret")
    monkeypatch.setattr(settings, "FEDERATION_ENABLED", False)
    monkeypatch.setattr(settings, "SANDBOX_LEGACY_ENABLED", False)
    monkeypatch.setattr(settings, "ADMIN_PAGES_ENABLED", False)
    monkeypatch.setattr(settings, "KMS_REQUIRE_ATTESTATION", False)
    monkeypatch.delenv("TESTING", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    return settings


def _prod_cube_baseline(monkeypatch):
    settings = _prod_baseline(monkeypatch)
    monkeypatch.setattr(settings, "CUBE_DATAPLANE_ENABLED", True)
    monkeypatch.setattr(settings, "CUBE_NODE_GRPC_TLS", True)
    monkeypatch.setattr(settings, "CUBE_NODE_TLS_CA", "/etc/cube/ca/cube-root-ca.crt")
    monkeypatch.setattr(settings, "CUBE_TEMPLATE_SIGNING_KEY", "cb05-signing-key")
    return settings


def test_go_plugin_dek_len_is_the_config_default():
    assert _go_dek_len() == 16
    assert get_settings().CUBE_DEK_LENGTH == _go_dek_len()


def test_prod_dataplane_dek_length_mismatch_refuses(monkeypatch):
    settings = _prod_cube_baseline(monkeypatch)
    monkeypatch.setattr(settings, "CUBE_DEK_LENGTH", 32)

    with pytest.raises(ValueError, match="CUBE_DEK_LENGTH"):
        settings.validate_security_config()


def test_prod_dataplane_dek_length_match_boots(monkeypatch):
    settings = _prod_cube_baseline(monkeypatch)
    monkeypatch.setattr(settings, "CUBE_DEK_LENGTH", _go_dek_len())

    settings.validate_security_config()


def test_dataplane_off_dek_length_mismatch_is_not_gated(monkeypatch):
    settings = _prod_baseline(monkeypatch)
    monkeypatch.setattr(settings, "CUBE_DATAPLANE_ENABLED", False)
    monkeypatch.setattr(settings, "CUBE_DEK_LENGTH", 32)

    settings.validate_security_config()
