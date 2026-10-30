"""A89 (deployment leg) — the boot log must disclose the JWT key SOURCE.

backend-17a made auth_service fall back to an ephemeral random key under DEBUG;
what remained undiagnosable was which source THIS boot actually resolved —
explicit / test / published-opt-in / ephemeral / published-default. Tokens
only reveal it after they expire. Pins:

* `main.jwt_key_source()` classifies in the exact branch order of
  `auth_service._effective_jwt_secret_key`;
* neither the published default nor the source-local test key (or any prefix)
  ever appears in the category/advice text;
* lifespan calls the disclosure AND the A82 boot reclaim.
"""
import inspect

import pytest

import app.main as main
from app.core.config import _JWT_DEFAULT_KEY
from app.services import auth_service as auth


def _clean_env(monkeypatch):
    monkeypatch.delenv("TESTING", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("CDS_ALLOW_LOCAL_DEFAULT_JWT_KEY", raising=False)
    monkeypatch.delenv("_JWT_KEY_DISCLOSED", raising=False)


def _settings_stub(monkeypatch, **attrs):
    for name, value in attrs.items():
        monkeypatch.setattr(main.settings, name, value, raising=False)


def test_explicit_key_wins_first(monkeypatch):
    _clean_env(monkeypatch)
    _settings_stub(monkeypatch, JWT_SECRET_KEY="a-real-secret-not-the-default", DEBUG=True)
    assert main.jwt_key_source()[0] == "explicit"


def test_test_env_is_its_own_category(monkeypatch):
    _clean_env(monkeypatch)
    _settings_stub(monkeypatch, JWT_SECRET_KEY=_JWT_DEFAULT_KEY, DEBUG=True)
    monkeypatch.setenv("TESTING", "1")
    assert main.jwt_key_source()[0] == "test-local"


def test_published_opt_in_beats_debug(monkeypatch):
    _clean_env(monkeypatch)
    _settings_stub(monkeypatch, JWT_SECRET_KEY=_JWT_DEFAULT_KEY, DEBUG=True)
    monkeypatch.setenv("CDS_ALLOW_LOCAL_DEFAULT_JWT_KEY", "true")
    assert main.jwt_key_source()[0] == "published-opt-in"


def test_debug_is_ephemeral_random(monkeypatch):
    _clean_env(monkeypatch)
    _settings_stub(monkeypatch, JWT_SECRET_KEY=_JWT_DEFAULT_KEY, DEBUG=True)
    assert main.jwt_key_source()[0] == "ephemeral-random"


def test_non_debug_default_is_flagged(monkeypatch):
    _clean_env(monkeypatch)
    _settings_stub(monkeypatch, JWT_SECRET_KEY=_JWT_DEFAULT_KEY, DEBUG=False)
    assert main.jwt_key_source()[0] == "published-default"


def test_disclosure_never_leaks_key_material(monkeypatch):
    _clean_env(monkeypatch)
    _settings_stub(monkeypatch, JWT_SECRET_KEY=_JWT_DEFAULT_KEY, DEBUG=True)
    secret_texts = [_JWT_DEFAULT_KEY, auth._LOCAL_DEFAULT_JWT_KEY]
    for env in ({}, {"TESTING": "1"}, {"CDS_ALLOW_LOCAL_DEFAULT_JWT_KEY": "true"}):
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        source, advice = main.jwt_key_source()
        blob = f"{source} {advice}"
        for secret in secret_texts:
            assert secret not in blob
            assert secret[:8] not in blob, "even a key prefix must not reach the log"


def test_lifespan_wires_disclosure_and_a82_reclaim():
    body = inspect.getsource(main.lifespan)
    assert "jwt_key_source()" in body, "A89: boot must disclose the key source"
    assert "reclaim_stale_operations" in body, "A82: boot must sweep orphaned running ops"
