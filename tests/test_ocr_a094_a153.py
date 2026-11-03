"""OCR 复核轮 A94 —— HSM 回退闸门的回归测试。

- A94 `hsm_adapter.create_hsm_adapter`：`fallback_allowed = True` 是默认值，读取
  `HSM_SOFTWARE_FALLBACK_ALLOWED` 的那一段整个包在 `except Exception: pass` 里 ⇒
  配置读不出来时闸门自动打开，`HSM_SOFTWARE_FALLBACK_ALLOWED=false` 的
  fail-closed 承诺（gap B5/T12）失效。
"""
from types import SimpleNamespace

import pytest

from app.services import hsm_adapter as hsm


def _settings(**kw):
    base = dict(VAULT_ADDR="", VAULT_TOKEN="", HSM_SOFTWARE_FALLBACK_ALLOWED=True)
    base.update(kw)
    return SimpleNamespace(**base)


def test_a094_unreadable_settings_must_not_open_the_fallback_gate(monkeypatch):
    def boom():
        raise RuntimeError("settings unavailable")

    monkeypatch.setattr(hsm, "get_settings", boom, raising=False)
    monkeypatch.setattr("app.core.config.get_settings", boom)
    with pytest.raises(RuntimeError, match="HSM_SOFTWARE_FALLBACK_ALLOWED"):
        hsm.create_hsm_adapter()


def test_a094_software_fallback_still_allowed_when_config_says_yes(monkeypatch):
    monkeypatch.setattr("app.core.config.get_settings", lambda: _settings())
    assert isinstance(hsm.create_hsm_adapter(), hsm.SoftwareHSMAdapter)


def test_a094_explicit_refusal_still_refuses(monkeypatch):
    monkeypatch.setattr(
        "app.core.config.get_settings",
        lambda: _settings(HSM_SOFTWARE_FALLBACK_ALLOWED=False),
    )
    with pytest.raises(RuntimeError, match="refusing to fall back"):
        hsm.create_hsm_adapter()


