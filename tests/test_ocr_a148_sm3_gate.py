"""OCR A148 — `merkle_service._sm3_fallback_allowed()` 曾在任何异常时返回 True。

台账（docs/ocr-review-2026-10-18/ocr-verified-findings.md ### A148）：该 helper 的职责
就是尊重 `CDS_SM3_FALLBACK_ALLOWED`（PZ-04/MR-A14 生产硬约束）。`except Exception:
return True` 让配置一读不出来就把 SHA-256 冒充 SM3 放行，审计 Merkle 叶子的哈希静默
降级，而调用方 `if not _sm3_fallback_allowed(): raise` 本应拒绝服务。修法是 fail closed。
"""
import pytest

import app.core.config as config
from app.services import merkle_service


def test_a148_unreadable_settings_fails_closed(monkeypatch):
    def boom():
        raise RuntimeError("settings unavailable")

    monkeypatch.setattr(config, "get_settings", boom)
    assert merkle_service._sm3_fallback_allowed() is False


def test_a148_settings_exception_subclass_fails_closed(monkeypatch):
    class _Broken:
        @property
        def SM3_FALLBACK_ALLOWED(self):
            raise ValueError("malformed config")

    monkeypatch.setattr(config, "get_settings", lambda: _Broken())
    assert merkle_service._sm3_fallback_allowed() is False


def test_a148_gate_follows_settings(monkeypatch):
    class _S:
        SM3_FALLBACK_ALLOWED: bool = False

    monkeypatch.setattr(config, "get_settings", lambda: _S())
    assert merkle_service._sm3_fallback_allowed() is False
    _S.SM3_FALLBACK_ALLOWED = True
    assert merkle_service._sm3_fallback_allowed() is True


def test_a148_hash_refuses_sha256_when_gate_closed(monkeypatch):
    """gmssl 缺失 + 门关闭 = 拒绝哈希，绝不静默用 SHA-256 假装 SM3。"""
    monkeypatch.setattr(merkle_service, "_GMSSL_AVAILABLE", False)

    class _S:
        SM3_FALLBACK_ALLOWED = False

    monkeypatch.setattr(config, "get_settings", lambda: _S())
    with pytest.raises(RuntimeError, match="SM3_FALLBACK_ALLOWED"):
        merkle_service._hash(b"audit leaf")


def test_a148_hash_refuses_sha256_when_settings_unreadable(monkeypatch):
    """本条的核心：配置读不出来时同样必须拒绝对审计叶子哈希。"""
    monkeypatch.setattr(merkle_service, "_GMSSL_AVAILABLE", False)

    def boom():
        raise RuntimeError("settings unavailable")

    monkeypatch.setattr(config, "get_settings", boom)
    with pytest.raises(RuntimeError, match="SM3_FALLBACK_ALLOWED"):
        merkle_service._hash(b"audit leaf")


def test_a148_disclosed_fallback_still_works(monkeypatch):
    """诚实降级路径保留：显式 opt-in 时仍可用 SHA-256（不得删，PZ-04/MR-A14）。"""
    monkeypatch.setattr(merkle_service, "_GMSSL_AVAILABLE", False)

    class _S:
        SM3_FALLBACK_ALLOWED = True

    monkeypatch.setattr(config, "get_settings", lambda: _S())
    assert len(merkle_service._hash(b"audit leaf")) == 32


def test_a148_real_sm3_when_gmssl_available():
    assert merkle_service._GMSSL_AVAILABLE is True
    assert len(merkle_service._hash(b"audit leaf")) == 32
