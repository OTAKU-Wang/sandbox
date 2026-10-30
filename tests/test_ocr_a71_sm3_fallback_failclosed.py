"""OCR A71 — `app/utils/crypto._sm3_fallback_allowed()` 异常方向 fail-open。

台账（docs/ocr-review-2026-10-18/ocr-verified-findings.md ### A71）：该 helper 的
docstring 自称 fail-closed，但 `except Exception: return True` 让配置一读不出来
（pydantic 校验报错 / .env 缺失 / 构造抛错）就默认放行 SHA-256 冒充 SM3，与本仓库
其余诚实门（PZ-04/MR-A14、`validate_security_config`）方向相反。同族的 Merkle 侧
双胞胎 A148 已按 fail closed 修好（tests/test_ocr_a148_sm3_gate.py）；本文件把同一条
判据钉在 `app/utils/crypto.py` 上。诚实降级路径本身（显式 opt-in 时用 SHA-256 并告警）
保留，不得删。
"""
import hashlib
import sys

import pytest

import app.core.config as config
from app.utils import crypto


def _without_gmssl(monkeypatch) -> None:
    """Force the `from gmssl import sm3` ImportError branch.

    本机 venv 装了 gmssl（见 test_ocr_a148 的 `_GMSSL_AVAILABLE is True`），
    不剥掉依赖就永远进不了降级分支，测试会通过但什么都没测。
    """
    monkeypatch.setitem(sys.modules, "gmssl", None)


def _unreadable_settings():
    def boom():
        raise RuntimeError("settings unavailable")

    return boom


def test_a71_unreadable_settings_fails_closed(monkeypatch):
    """核心：配置读不出来时必须返回 False，而不是 True。"""
    monkeypatch.setattr(config, "get_settings", _unreadable_settings())
    assert crypto._sm3_fallback_allowed() is False


def test_a71_settings_exception_subclass_fails_closed(monkeypatch):
    class _Broken:
        @property
        def SM3_FALLBACK_ALLOWED(self):
            raise ValueError("malformed config")

    monkeypatch.setattr(config, "get_settings", lambda: _Broken())
    assert crypto._sm3_fallback_allowed() is False


def test_a71_hash_refuses_sha256_when_settings_unreadable(monkeypatch):
    """配置不可读 + gmssl 缺失 = 拒绝哈希，绝不静默用 SHA-256 冒充 SM3。"""
    _without_gmssl(monkeypatch)
    monkeypatch.setattr(config, "get_settings", _unreadable_settings())
    with pytest.raises(RuntimeError, match="SM3_FALLBACK_ALLOWED"):
        crypto.sm3_hash(b"audit leaf")


def test_a71_gate_still_follows_settings(monkeypatch):
    """门本身仍如实跟随 CDS_SM3_FALLBACK_ALLOWED（两个方向都要如实）。"""

    class _S:
        SM3_FALLBACK_ALLOWED: bool = False

    monkeypatch.setattr(config, "get_settings", lambda: _S())
    assert crypto._sm3_fallback_allowed() is False
    _S.SM3_FALLBACK_ALLOWED = True
    assert crypto._sm3_fallback_allowed() is True


def test_a71_disclosed_fallback_still_works(monkeypatch):
    """诚实降级路径保留：显式 opt-in 时仍可用 SHA-256（PZ-04/MR-A14 语义不变）。"""
    _without_gmssl(monkeypatch)
    # 进程级一次性告警标志必须在本用例 teardown 后归还，否则同一次 pytest 里
    # 后面断言「真 SM3 路径不得触发告警」的用例（tests/test_pz04_honesty_gates.py）
    # 会被本用例污染成红。
    monkeypatch.setattr(crypto, "_sm3_fallback_warned", False)

    class _S:
        SM3_FALLBACK_ALLOWED = True

    monkeypatch.setattr(config, "get_settings", lambda: _S())
    assert crypto.sm3_hash(b"audit leaf") == hashlib.sha256(b"audit leaf").hexdigest()
    assert crypto._sm3_fallback_warned is True


def test_a71_real_sm3_when_gmssl_available():
    """装了 gmssl 的默认环境仍走真 SM3，与本条修复无关的行为不得漂移。"""
    digest = crypto.sm3_hash(b"audit leaf")
    assert digest != hashlib.sha256(b"audit leaf").hexdigest()
    assert len(digest) == 64
