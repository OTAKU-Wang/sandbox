"""OCR A94（续片）—— 软件 HSM 回退的「可辨别」半边：is_simulation/backend 标记与告警级别。

A94 的 fail-closed 半边（`fallback_allowed = True` 默认值 + `except Exception: pass`）已由
前一片在 5aa84a8 改掉，`tests/test_ocr_a094_a153.py` 钉住。台账 A94 还点名缺的另一半：
「回退只记 `logger.info`，且 `SoftwareHSMAdapter` 不向后端输出任何
`is_simulation`/backend 标记（honest-degradation 要求可辨别）」。

本文件钉住可辨别这半边：
1. 每个 HSMAdapter 都声明 `is_simulation` / `backend`（硬件真相）；
2. 软件回退路径以 WARNING 级别披露（`logger.info` 在生产日志级别下等于沉默）；
3. `describe_hsm_backend()` 给启动披露（T12）一个机器可读的口径。
"""
import logging

import pytest

from app.services import hsm_adapter as hsm


def _settings(**kw):
    base = dict(VAULT_ADDR="", VAULT_TOKEN="", HSM_SOFTWARE_FALLBACK_ALLOWED=True)
    base.update(kw)
    from types import SimpleNamespace
    return SimpleNamespace(**base)


def test_a094_adapters_declare_hardware_truth():
    """软件回退必须自称 simulation，Vault 路径必须自称非 simulation。"""
    assert hsm.SoftwareHSMAdapter.is_simulation is True
    assert hsm.VaultTransitAdapter.is_simulation is False
    assert "software" in hsm.SoftwareHSMAdapter.backend
    assert hsm.SoftwareHSMAdapter.backend != hsm.VaultTransitAdapter.backend


def test_a094_created_software_adapter_is_discernible(monkeypatch):
    """调用方（kms_service）拿到实例后能一眼分辨它不是硬件 KEK。"""
    monkeypatch.setattr("app.core.config.get_settings", lambda: _settings())
    adapter = hsm.create_hsm_adapter()
    assert isinstance(adapter, hsm.SoftwareHSMAdapter)
    assert adapter.is_simulation is True
    assert adapter.backend == hsm.SoftwareHSMAdapter.backend


def test_a094_fallback_is_logged_at_warning(monkeypatch, caplog):
    """回退不能只在 INFO 里说悄悄话：生产日志级别下必须留痕。"""
    monkeypatch.setattr("app.core.config.get_settings", lambda: _settings())
    with caplog.at_level(logging.WARNING, logger="app.services.hsm_adapter"):
        hsm.create_hsm_adapter()
    hits = [
        r for r in caplog.records
        if "fallback" in r.getMessage().lower() and r.levelno >= logging.WARNING
    ]
    assert hits, [r.getMessage() for r in caplog.records]
    assert any("software" in r.getMessage().lower() for r in hits)


def test_a094_describe_backend_is_machine_readable(monkeypatch):
    """T12 启动披露需要的口径：backend + is_simulation，不含任何密钥材料。"""
    monkeypatch.setattr("app.core.config.get_settings", lambda: _settings())
    adapter = hsm.create_hsm_adapter()
    desc = hsm.describe_hsm_backend(adapter)
    assert desc == {"backend": "software-fallback", "is_simulation": True}
    assert set(desc) == {"backend", "is_simulation"}
