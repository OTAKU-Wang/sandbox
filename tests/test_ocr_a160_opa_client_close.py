"""A160 — `OPAClient.close()` must be terminal, not a mark-the-socket-closed no-op.

`app/main.py` shutdown calls `await opa_client.close()`, but `close()` only ran
`aclose()` and left the object on `self._client` while `_get_client()` rebuilt a
client whenever `is_closed` was true. Every late request (background task,
in-flight call) after lifespan shutdown therefore minted a fresh `AsyncClient`
that nothing would ever close again — a connection-pool leak that accumulates
once per rebuild.
"""
import httpx
import pytest

from app.services.opa_client import OPAClient


def _client() -> OPAClient:
    return OPAClient(base_url="http://127.0.0.1:1")


@pytest.mark.asyncio
async def test_close_drops_the_client_reference():
    c = _client()
    built = await c._get_client()
    assert built is not None
    await c.close()
    assert c._client is None, (
        "close() left the closed AsyncClient on the instance — _get_client() "
        "will silently rebuild it after shutdown"
    )


@pytest.mark.asyncio
async def test_close_is_idempotent():
    c = _client()
    await c._get_client()
    await c.close()
    await c.close()
    assert c._client is None


@pytest.mark.asyncio
async def test_no_client_is_minted_after_close(monkeypatch):
    built: list[httpx.AsyncClient] = []
    real = httpx.AsyncClient

    def _counting(*args, **kwargs):
        client = real(*args, **kwargs)
        built.append(client)
        return client

    monkeypatch.setattr(httpx, "AsyncClient", _counting)
    c = _client()
    await c._get_client()
    await c.close()
    assert len(built) == 1

    with pytest.raises(RuntimeError):
        await c._get_client()
    assert len(built) == 1, f"rebuild after shutdown minted {len(built) - 1} leaked client(s)"


@pytest.mark.asyncio
async def test_requests_after_close_fail_closed():
    c = _client()
    await c._get_client()
    await c.close()
    assert await c.health() is False
    assert await c.push_policy("cds/policies/x", "package cds") is False
    assert await c.evaluate("cds/policies/x", {"a": 1}) is None
    assert await c.delete_policy("cds/policies/x") is False
    assert c._client is None


@pytest.mark.asyncio
async def test_a_fresh_instance_works_after_the_old_one_was_closed():
    c1 = _client()
    await c1._get_client()
    await c1.close()
    c2 = _client()
    live = await c2._get_client()
    assert isinstance(live, httpx.AsyncClient)
    assert live.is_closed is False
    await c2.close()
