"""OCR A191: 输出报告的 SM2 签名密钥必须来自部署配置，不能是进程内随机密钥。

进程内随机密钥 ⇒ 一次重启/一次多副本部署就换钥：旧报告验不过、任何人都能重签，
不可抵赖性不成立；且无锁 check-then-set 在同步审查路径上还能同进程出现两把公钥。
判据：配置了 `SM2_PRIVATE_KEY_PATH` 时签名必须能用配置公钥验通、且跨「重启」稳定；
未配置时必须在结果里诚实披露密钥来源（不谎称可抵赖）；惰初始化必须在锁内。
"""
import pytest

from app.core.config import get_settings
from app.services.crypto_service import crypto_service
from app.services.output_inspection import OutputInspector, SandboxMode

TEXT = "本季度统计结果：合计 42 条"


@pytest.fixture
def fresh_key(monkeypatch):
    monkeypatch.setattr(OutputInspector, "_signing_keypair", None, raising=False)
    monkeypatch.setattr(OutputInspector, "_signing_key_source", None, raising=False)
    return monkeypatch


def _configure_key(monkeypatch, tmp_path, keypair, write_public=True):
    priv_file = tmp_path / "sm2_private.key"
    priv_file.write_text(f"{keypair.private_key}\n", encoding="utf-8")
    settings = get_settings()
    monkeypatch.setattr(settings, "SM2_PRIVATE_KEY_PATH", str(priv_file), raising=False)
    if write_public:
        pub_file = tmp_path / "sm2_public.key"
        pub_file.write_text(f"04{keypair.public_key}", encoding="utf-8")
        monkeypatch.setattr(settings, "SM2_PUBLIC_KEY_PATH", str(pub_file), raising=False)
    else:
        monkeypatch.setattr(settings, "SM2_PUBLIC_KEY_PATH", "", raising=False)
    return priv_file


def _unset_key(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "SM2_PRIVATE_KEY_PATH", "", raising=False)
    monkeypatch.setattr(settings, "SM2_PUBLIC_KEY_PATH", "", raising=False)


def _inspect():
    return OutputInspector().inspect(
        TEXT, "u-1", "s-a191", sandbox_mode=SandboxMode.DEVELOP.value,
    )


def test_signature_verifies_under_configured_key(fresh_key, tmp_path):
    keypair = crypto_service.generate_keypair()
    _configure_key(fresh_key, tmp_path, keypair)

    result = _inspect()

    assert result.signature, "签名阶段必须真的产出签名"
    assert result.signature_key_source == "configured", result.signature_key_source
    assert result.signature_public_key == keypair.public_key
    assert crypto_service.verify(
        result.redacted_output.encode(), result.signature, keypair.public_key
    ) is True, "报告签名必须能用部署配置里的公钥验通（否则不可抵赖性不成立）"


def test_public_key_derived_when_only_private_configured(fresh_key, tmp_path):
    keypair = crypto_service.generate_keypair()
    _configure_key(fresh_key, tmp_path, keypair, write_public=False)

    result = _inspect()

    assert result.signature_key_source == "configured"
    assert result.signature_public_key == keypair.public_key, "只给私钥时应推出对应公钥"
    assert crypto_service.verify(
        result.redacted_output.encode(), result.signature, keypair.public_key
    ) is True


def test_key_survives_process_restart(fresh_key, tmp_path):
    """「重启」后仍是同一把钥：两次签名都能用同一配置公钥验通。"""
    keypair = crypto_service.generate_keypair()
    _configure_key(fresh_key, tmp_path, keypair)

    first = _inspect()
    fresh_key.setattr(OutputInspector, "_signing_keypair", None, raising=False)
    second = _inspect()

    assert first.signature_public_key == second.signature_public_key == keypair.public_key
    for sig in (first.signature, second.signature):
        assert crypto_service.verify(first.redacted_output.encode(), sig, keypair.public_key)


def test_ephemeral_key_is_disclosed(fresh_key):
    _unset_key(fresh_key)
    result = _inspect()
    assert result.signature_key_source == "ephemeral", (
        "没配部署密钥时结果必须披露密钥来源，别让临时密钥冒充可抵赖签名"
    )


def test_lazy_key_init_is_lock_guarded(fresh_key, monkeypatch):
    _unset_key(fresh_key)
    held: list[bool] = []
    real_generate = crypto_service.generate_keypair

    def spy_generate():
        lock = getattr(OutputInspector, "_signing_key_lock", None)
        held.append(bool(lock is not None and lock.locked()))
        return real_generate()

    monkeypatch.setattr(crypto_service, "generate_keypair", spy_generate)
    OutputInspector().inspect(TEXT, "u-1", "s-a191b", sandbox_mode=SandboxMode.DEVELOP.value)
    assert held == [True], "密钥惰初始化必须在锁内完成（无锁 check-then-set 会并发造两把公钥）"
