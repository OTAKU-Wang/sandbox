"""W4 (parity plan): legacy admin pages are gated behind ADMIN_PAGES_ENABLED.

The /admin/* Jinja pages (FE-1~FE-5 leftovers) carry no authentication — the
default must be OFF, and enabling them in production must fail startup.
"""
import secrets

import pytest


@pytest.mark.asyncio
async def test_admin_pages_disabled_by_default(client):
    resp = await client.get("/admin/identities")
    assert resp.status_code == 404, "admin pages must be off by default"


def test_admin_pages_enabled_when_configured(monkeypatch):
    """The gate is decided at app-build time — exercise it on a fresh app."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.core.config import get_settings
    from app.main import _register_admin_pages

    monkeypatch.setattr(get_settings(), "ADMIN_PAGES_ENABLED", True)
    fresh = FastAPI()
    _register_admin_pages(fresh)
    with TestClient(fresh, raise_server_exceptions=False) as c:
        resp = c.get("/admin/identities")
    assert resp.status_code == 200


def test_register_admin_pages_noop_when_disabled():
    from fastapi import FastAPI
    from app.main import _register_admin_pages

    fresh = FastAPI()
    _register_admin_pages(fresh)  # default flag False -> no routes added
    routes = [getattr(r, "path", "") for r in fresh.routes]
    assert "/admin/identities" not in routes


def test_validate_security_config_rejects_admin_pages_in_production(monkeypatch):
    import os

    from app.core.config import get_settings

    settings = get_settings()
    # Emulate production: DEBUG off, TESTING env lookups empty, fail-closed
    # gates satisfied, unique JWT — so the raised ValueError is the
    # ADMIN_PAGES_ENABLED one we are testing.
    monkeypatch.setattr(settings, "DEBUG", False)
    monkeypatch.setattr(settings, "JWT_SECRET_KEY", secrets.token_urlsafe(48))
    monkeypatch.setattr(settings, "SECCOMP_FALLBACK_ALLOWED", False)
    monkeypatch.setattr(settings, "HSM_SOFTWARE_FALLBACK_ALLOWED", False)
    monkeypatch.setattr(settings, "ADMIN_PAGES_ENABLED", True)

    # validate_security_config treats "not TESTING and not PYTEST_CURRENT_TEST"
    # as production — emulate that environment for the duration of the call.
    real_get = os.environ.get

    def _prod_env_get(key, default=None):
        if key in ("TESTING", "PYTEST_CURRENT_TEST"):
            return ""
        return real_get(key, default)

    monkeypatch.setattr("os.environ.get", _prod_env_get)
    with pytest.raises(ValueError, match="ADMIN_PAGES_ENABLED"):
        settings.validate_security_config()
