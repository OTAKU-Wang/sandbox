"""OCR A94b（T12 接线腿）—— 启动必须自报 HSM 后端，模拟后端不得只在模块里自称。

`tests/test_ocr_a094_hsm_disclosure.py` 钉住了 `is_simulation`/`backend` 标记与
`describe_hsm_backend()` 这个口径本身，但口径没人读就等于没披露：`SoftwareHSMAdapter`
的 KEK 是进程内随机数，开机日志里它与 Vault 一模一样安静（诚实降级铁律要求可辨别）。

本文件钉住消费端：
1. `main.hsm_backend_disclosure()` 输出机器可读的 backend/is_simulation；
2. 模拟后端走 WARNING（生产日志级别下必须留痕），真硬件/Vault 走 INFO（不得假警报）；
3. 取不到后端时报 `unknown`，不得静默跳过这条披露；
4. 披露确实挂在 lifespan 启动路径上。
"""
import inspect
import logging

import app.main as main
from app.services import hsm_adapter as hsm


def _singleton(monkeypatch, adapter):
    monkeypatch.setattr(hsm, "hsm_adapter", adapter, raising=False)


def test_software_backend_is_disclosed_at_warning(monkeypatch, caplog):
    _singleton(monkeypatch, hsm.SoftwareHSMAdapter())
    with caplog.at_level(logging.INFO, logger="app.main"):
        desc = main.hsm_backend_disclosure()
    assert desc["backend"] == "software-fallback"
    assert desc["is_simulation"] is True
    hits = [r for r in caplog.records if "HSM backend=" in r.getMessage()]
    assert hits, [r.getMessage() for r in caplog.records]
    line = hits[-1]
    assert line.levelno >= logging.WARNING, "模拟 HSM 只记 INFO 等于在生产日志级别下沉默"
    assert "backend=software-fallback" in line.getMessage()
    assert "is_simulation=True" in line.getMessage()


def test_vault_backend_is_not_flagged_as_simulation(monkeypatch, caplog):
    """反向护栏：真后端不得被染成降级警报，否则警报本身失去意义。"""
    _singleton(monkeypatch, hsm.VaultTransitAdapter("http://vault.invalid:8200", "t"))
    with caplog.at_level(logging.INFO, logger="app.main"):
        desc = main.hsm_backend_disclosure()
    assert desc["backend"] == "vault-transit"
    assert desc["is_simulation"] is False
    line = next(r for r in caplog.records if "HSM backend=" in r.getMessage())
    assert line.levelno == logging.INFO
    assert "is_simulation=False" in line.getMessage()


def test_unknown_backend_is_still_disclosed(monkeypatch, caplog):
    def _boom(*_a, **_kw):
        raise RuntimeError("adapter construction refused")

    monkeypatch.setattr(hsm, "describe_hsm_backend", _boom, raising=False)
    with caplog.at_level(logging.INFO, logger="app.main"):
        desc = main.hsm_backend_disclosure()
    assert desc["backend"] == "unknown"
    assert desc["is_simulation"] is True, "分辨不出是否模拟时，按模拟对待（fail-visible）"
    assert [r for r in caplog.records if "HSM backend=unknown" in r.getMessage()], (
        "取不到后端也必须留痕，不能静默跳过这条启动披露"
    )


def test_lifespan_calls_the_disclosure():
    src = inspect.getsource(getattr(main.lifespan, "__wrapped__", main.lifespan))
    assert "hsm_backend_disclosure()" in src, (
        "A94b: describe_hsm_backend 的口径必须在启动路径上被读出来"
    )
