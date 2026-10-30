"""A141 — 「生产必填」的度量白名单可被静默绕过（high；config 侧半边）。

启动门（app/core/config.py）只检查 `ATTESTATION_ALLOWED_MEASUREMENTS.strip()` 非空，
而消费方（app/services/remote_attestation.py::_load_operator_measurements）把它按
csv 解析并**丢掉空片段**，空集在它的语义里就是 allow-all。于是
`CDS_ATTESTATION_ALLOWED_MEASUREMENTS=" , ,, "` 这类值：门过了（strip 后非空），
实际白名单为空集 = accept-any-TEE —— 运维以为配了白名单，密钥分发面仍是全放行。

本测试的两条读数：
1. 判别读数：同一个值喂给消费方，有效白名单必须是空集（=allow-all），而旧门放行；
2. 收紧读数：这种「解析后为空」的值在 prod 必须拒绝启动，真实 csv 照旧放行。

台账的主机制半边（KMS 显式传 policy 时白名单不生效）已由 A183 在
remote_attestation.py 落地（`_operator_measurements` 在每条校验路径上兜底），
本文件只做 config 侧的门，不越界改 services。
"""
import pytest

from app.core.config import get_settings
from app.services.remote_attestation import AttestationService


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
    monkeypatch.setattr(settings, "CUBE_DATAPLANE_ENABLED", False)
    monkeypatch.delenv("TESTING", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    return settings


def test_effective_whitelist_is_empty_for_punctuation_only_value(monkeypatch):
    """判别读数：旧门放行的值，在消费方那里有效白名单为空集（=accept-any-TEE）。"""
    settings = _prod_baseline(monkeypatch)
    monkeypatch.setattr(settings, "KMS_REQUIRE_ATTESTATION", True)
    monkeypatch.setattr(settings, "ATTESTATION_ALLOWED_MEASUREMENTS", " , ,, ")

    effective = AttestationService._load_operator_measurements()
    assert effective == set(), (
        "有效白名单非空——旧门的 .strip() 判据与消费方的 csv 解析已不再分歧"
    )


def test_prod_refuses_whitelist_that_parses_to_nothing(monkeypatch):
    """收紧读数：解析后为空的白名单在 prod 必须拒绝启动，而不是过了门继续跑。"""
    settings = _prod_baseline(monkeypatch)
    monkeypatch.setattr(settings, "KMS_REQUIRE_ATTESTATION", True)
    monkeypatch.setattr(settings, "ATTESTATION_ALLOWED_MEASUREMENTS", " , ,, ")

    with pytest.raises(ValueError, match="ATTESTATION_ALLOWED_MEASUREMENTS"):
        settings.validate_security_config()


def test_prod_refuses_empty_whitelist(monkeypatch):
    """既有语义不得退化：完全留空照旧拒绝。"""
    settings = _prod_baseline(monkeypatch)
    monkeypatch.setattr(settings, "KMS_REQUIRE_ATTESTATION", True)
    monkeypatch.setattr(settings, "ATTESTATION_ALLOWED_MEASUREMENTS", "")

    with pytest.raises(ValueError, match="ATTESTATION_ALLOWED_MEASUREMENTS"):
        settings.validate_security_config()


def test_real_csv_whitelist_still_boots_and_is_consumed(monkeypatch):
    """真实 csv（带空格）必须照旧放行，并且消费方得到同样的非空集合。"""
    settings = _prod_baseline(monkeypatch)
    monkeypatch.setattr(settings, "KMS_REQUIRE_ATTESTATION", True)
    monkeypatch.setattr(settings, "ATTESTATION_ALLOWED_MEASUREMENTS", "aaaa1111, bbbb2222 ,,")

    settings.validate_security_config()
    assert AttestationService._load_operator_measurements() == {"aaaa1111", "bbbb2222"}


def test_attestation_disabled_does_not_require_whitelist(monkeypatch):
    """KMS_REQUIRE_ATTESTATION=false 时不得凭空新增要求（默认/开发口径）。"""
    settings = _prod_baseline(monkeypatch)
    monkeypatch.setattr(settings, "KMS_REQUIRE_ATTESTATION", False)
    monkeypatch.setattr(settings, "ATTESTATION_ALLOWED_MEASUREMENTS", "")

    settings.validate_security_config()
