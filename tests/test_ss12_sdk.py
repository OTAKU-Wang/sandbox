"""SS-12 SDK: platform API-key auth (spec §4.6) — httpx.MockTransport, no server."""
import sys
from pathlib import Path

import httpx
import pytest

# Make the sdk package importable without installation.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sdk"))

from cds_sdk import CDSApiError, CDSClient, CDSError  # noqa: E402

BASE = "http://test"
KEY = "cds_ak_" + "a" * 43  # 50 chars: cds_ak_ + 43 base64url


# ── construction exclusivity (three-way mutual exclusion) ─────────────
def test_token_and_api_key_conflict():
    with pytest.raises(CDSError) as exc:
        CDSClient(BASE, token="tok", api_key=KEY)
    assert exc.value.status_code == 400


def test_token_and_app_credentials_conflict():
    with pytest.raises(CDSError) as exc:
        CDSClient(BASE, token="tok", app_id="app", app_secret="sec")
    assert exc.value.status_code == 400


def test_api_key_and_app_credentials_conflict():
    with pytest.raises(CDSError) as exc:
        CDSClient(BASE, api_key=KEY, app_id="app", app_secret="sec")
    assert exc.value.status_code == 400


def test_half_formed_app_pair_rejected():
    with pytest.raises(CDSError) as exc:
        CDSClient(BASE, app_id="app")
    assert exc.value.status_code == 400


def test_no_credential_rejected():
    with pytest.raises(CDSError) as exc:
        CDSClient(BASE)
    assert exc.value.status_code == 400


# ── prefix validation (fast fail, no request sent) ───────────────────
def test_bad_prefix_rejected_at_construction():
    # No transport is supplied, so any request would raise — construction
    # must fail first, before a request is even possible.
    with pytest.raises(CDSError) as exc:
        CDSClient(BASE, api_key="sk_live_wrong")
    assert exc.value.status_code == 400
    assert "cds_ak_" in str(exc.value)


def test_valid_prefix_sets_bearer_header():
    with CDSClient(BASE, api_key=KEY) as c:
        assert c._client.headers["Authorization"] == f"Bearer {KEY}"


# ── Bearer header set on the wire ────────────────────────────────────
def test_bearer_header_sent_on_request():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization")
        seen["x_app_id"] = request.headers.get("X-App-Id")
        return httpx.Response(200, json={"id": "s-1", "status": "running"})

    with CDSClient(BASE, api_key=KEY, transport=httpx.MockTransport(handler)) as c:
        c.get_session("s-1")
    assert seen["auth"] == f"Bearer {KEY}"
    assert seen["x_app_id"] is None  # api_key path sends no gateway headers


# ── 401 surfaces CDSApiError.code and is NOT retried ─────────────────
def test_401_api_key_invalid_not_retried():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(
            401,
            json={"code": "API_KEY_INVALID", "message": "invalid key",
                  "detail": "expired", "request_id": "r-1"},
        )

    with CDSClient(BASE, api_key=KEY, transport=httpx.MockTransport(handler)) as c:
        with pytest.raises(CDSApiError) as exc:
            c.get_session("s-1")
    assert calls["n"] == 1  # single attempt — 401 is not retried
    assert exc.value.status_code == 401
    assert exc.value.code == "API_KEY_INVALID"


def test_401_api_key_inactive_not_retried():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(
            401,
            json={"code": "API_KEY_INACTIVE", "message": "key disabled",
                  "detail": "disabled", "request_id": "r-2"},
        )

    with CDSClient(BASE, api_key=KEY, transport=httpx.MockTransport(handler)) as c:
        with pytest.raises(CDSApiError) as exc:
            c.get_session("s-1")
    assert calls["n"] == 1
    assert exc.value.code == "API_KEY_INACTIVE"


def test_api_key_path_still_retries_429():
    """The api_key path keeps the existing 429/503 retry set unchanged."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, json={"code": "RATE_LIMITED"},
                                  headers={"Retry-After": "0"})
        return httpx.Response(200, json={"ok": True})

    with CDSClient(BASE, api_key=KEY, transport=httpx.MockTransport(handler)) as c:
        r = c.get_session("s-1")
    assert calls["n"] == 2
    assert r == {"ok": True}
