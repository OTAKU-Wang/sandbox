"""A40 — 节点 gRPC 明文回退并未 fail-closed（high）。

`validate_security_config` 的注释写着「No silent plaintext fallback —
CUBE_NODE_GRPC_TLS=False is dev-only」，但启动门只写成
`if self.CUBE_NODE_GRPC_TLS and not self.CUBE_NODE_TLS_CA: raise`：
**唯一能真正关掉 TLS 的那个开关自己不受任何校验**。生产只要
`CDS_CUBE_NODE_GRPC_TLS=false`（且 dataplane 打开），CA/mTLS 检查整体短路，
启动成功、无 warning，L2' 控制面→cubelet 的 provision/exec/DEK 引用全部裸奔。

要求（台账修法）：prod 分支里 `if not self.CUBE_NODE_GRPC_TLS: raise`，与同文件
`FEDERATION_ENABLED` 那种「按开关本身 fail-closed」的写法对齐。
边界：dataplane 关闭（默认部署）与 dev/test 一律不得被这条断言打挂。
"""
import pytest

from app.core.config import get_settings


def _prod_baseline(monkeypatch):
    """一份「其余 prod 门全合规」的 prod Settings，使只有被测门可能 raise。"""
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
    # A141 另有归属：先关掉无关的度量白名单门，保证只有 cube 门可能触发
    monkeypatch.setattr(settings, "KMS_REQUIRE_ATTESTATION", False)
    monkeypatch.delenv("TESTING", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    return settings


def test_prod_dataplane_with_tls_disabled_refuses(monkeypatch):
    """A40: dataplane 开 + TLS 关 = 明文控制面，prod 必须拒绝启动（旧门被整体短路）。"""
    settings = _prod_baseline(monkeypatch)
    monkeypatch.setattr(settings, "CUBE_DATAPLANE_ENABLED", True)
    monkeypatch.setattr(settings, "CUBE_NODE_GRPC_TLS", False)
    # CA 齐备：证明「缺 CA」那条旧门不是本案的兜底——TLS 关掉时它根本不会被评估。
    monkeypatch.setattr(settings, "CUBE_NODE_TLS_CA", "/etc/cube/ca/cube-root-ca.crt")

    with pytest.raises(ValueError, match="CUBE_NODE_GRPC_TLS"):
        settings.validate_security_config()


def test_prod_dataplane_tls_enabled_with_ca_still_boots(monkeypatch):
    """合规口径（TLS=true + CA）必须照旧可启动，不得把正确配置一起打挂。"""
    settings = _prod_baseline(monkeypatch)
    monkeypatch.setattr(settings, "CUBE_DATAPLANE_ENABLED", True)
    monkeypatch.setattr(settings, "CUBE_NODE_GRPC_TLS", True)
    monkeypatch.setattr(settings, "CUBE_NODE_TLS_CA", "/etc/cube/ca/cube-root-ca.crt")
    monkeypatch.setattr(settings, "CUBE_TEMPLATE_SIGNING_KEY", "cb05-signing-key")

    issues = settings.validate_security_config()
    joined = "\n".join(issues)
    assert "plaintext" not in joined.lower()


def test_prod_without_dataplane_tls_disabled_still_boots(monkeypatch):
    """dataplane 关（默认部署）时新门必须不生效：否则会把默认部署打挂。"""
    settings = _prod_baseline(monkeypatch)
    monkeypatch.setattr(settings, "CUBE_DATAPLANE_ENABLED", False)
    monkeypatch.setattr(settings, "CUBE_NODE_GRPC_TLS", False)

    settings.validate_security_config()


def test_debug_tls_disabled_still_boots(monkeypatch):
    """dev（DEBUG=true）保留 grpc.dev 明文腿：这条门只在 prod 生效。"""
    settings = _prod_baseline(monkeypatch)
    monkeypatch.setattr(settings, "DEBUG", True)
    monkeypatch.setattr(settings, "CUBE_DATAPLANE_ENABLED", True)
    monkeypatch.setattr(settings, "CUBE_NODE_GRPC_TLS", False)

    settings.validate_security_config()
