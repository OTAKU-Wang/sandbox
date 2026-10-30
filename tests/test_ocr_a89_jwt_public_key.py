"""A89 — 公开硬编码 JWT 密钥不得在 DEBUG 形态下签发令牌（源码里的密钥＝任何人都能自签 admin）

DEBUG 栈（默认 compose / deploy 脚本都是 CDS_DEBUG=true）过去直接走源码里公开的
`_LOCAL_DEFAULT_JWT_KEY`：拿到源码的人就能自签 `role=admin` 的 access token。
修后：回退密钥只在测试环境或显式开关（CDS_ALLOW_LOCAL_DEFAULT_JWT_KEY）下使用，
且使用时必须 WARNING 披露；生产（非 DEBUG）依旧 fail-closed。
"""
import logging
import uuid

import jwt
import pytest

from app.core.config import _JWT_DEFAULT_KEY, get_settings
from app.services import auth_service

settings = get_settings()


def _leave_test_env(monkeypatch) -> None:
    """按仓库惯例模拟非测试进程：剥掉 TESTING / PYTEST_CURRENT_TEST。"""
    assert auth_service.settings is settings
    monkeypatch.delenv("TESTING", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("CDS_ALLOW_LOCAL_DEFAULT_JWT_KEY", raising=False)
    monkeypatch.setattr(settings, "JWT_SECRET_KEY", _JWT_DEFAULT_KEY)
    monkeypatch.setattr(auth_service, "_JWT_KEY_WARNINGS", set())
    monkeypatch.setattr(auth_service, "_ephemeral_jwt_key", None)


def test_debug_stack_does_not_sign_with_the_published_key(monkeypatch):
    """DEBUG=true + 默认 JWT_SECRET_KEY：不得用源码公开密钥签名，否则源码即万能钥匙。"""
    monkeypatch.setattr(settings, "DEBUG", True)
    _leave_test_env(monkeypatch)

    uid = uuid.uuid4()
    token = auth_service.create_access_token(uid, "admin")

    with pytest.raises(jwt.InvalidTokenError):
        jwt.decode(token, auth_service._LOCAL_DEFAULT_JWT_KEY, algorithms=["HS256"])

    assert auth_service.decode_access_token(token)["sub"] == str(uid)


def test_debug_stack_warning_discloses_the_fallback(monkeypatch, caplog):
    """honest degradation：不用公开密钥也要说清楚当前用的是哪种密钥。"""
    monkeypatch.setattr(settings, "DEBUG", True)
    _leave_test_env(monkeypatch)

    with caplog.at_level(logging.WARNING, logger="app.services.auth_service"):
        auth_service.create_access_token(uuid.uuid4(), "admin")

    assert any(
        r.levelno >= logging.WARNING and "JWT" in r.getMessage() for r in caplog.records
    ), "DEBUG 回退密钥未做任何 WARNING 披露"


def test_opt_in_local_key_still_works_but_is_disclosed(monkeypatch, caplog):
    """DEBUG 栈要固定密钥得显式打开开关，并且打开时必须有 WARNING。"""
    monkeypatch.setattr(settings, "DEBUG", True)
    _leave_test_env(monkeypatch)
    monkeypatch.setenv("CDS_ALLOW_LOCAL_DEFAULT_JWT_KEY", "1")

    with caplog.at_level(logging.WARNING, logger="app.services.auth_service"):
        token = auth_service.create_access_token(uuid.uuid4(), "admin")

    claims = jwt.decode(token, auth_service._LOCAL_DEFAULT_JWT_KEY, algorithms=["HS256"])
    assert claims["role"] == "admin"
    assert any(
        r.levelno >= logging.WARNING and "JWT" in r.getMessage() for r in caplog.records
    ), "使用公开本地密钥却未披露"


def test_production_still_fails_closed(monkeypatch):
    """回归钉：非 DEBUG + 默认密钥必须照旧抛错（不放宽成 fail-open）。"""
    monkeypatch.setattr(settings, "DEBUG", False)
    _leave_test_env(monkeypatch)

    with pytest.raises(ValueError, match="JWT_SECRET_KEY"):
        auth_service.create_access_token(uuid.uuid4(), "admin")


def test_real_secret_is_used_verbatim(monkeypatch):
    """配了真密钥时行为不变：用该密钥可验，用公开密钥不可验。"""
    import secrets as _secrets

    key = _secrets.token_urlsafe(48)
    monkeypatch.setattr(settings, "DEBUG", True)
    _leave_test_env(monkeypatch)
    monkeypatch.setattr(settings, "JWT_SECRET_KEY", key)

    token = auth_service.create_access_token(uuid.uuid4(), "buyer")
    assert jwt.decode(token, key, algorithms=["HS256"])["role"] == "buyer"
    with pytest.raises(jwt.InvalidTokenError):
        jwt.decode(token, auth_service._LOCAL_DEFAULT_JWT_KEY, algorithms=["HS256"])
