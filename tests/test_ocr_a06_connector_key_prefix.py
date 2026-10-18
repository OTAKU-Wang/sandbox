"""A6 — connector API-key prefix must fit the declared column width.

``Connector.api_key_prefix`` is ``String(8)`` (and the alembic 0000 baseline
creates ``varchar(8)``), while both write sites in ``app/api/connectors.py``
stored ``api_key[:15]``. SQLite silently accepts the over-long value, PostgreSQL
raises ``value too long for type character varying(8)`` — so
``POST /api/v1/connectors/register`` and ``.../rotate-key`` were guaranteed 500s
on the production database, invisible on the SQLite harness.

The width is read from the model here (never a literal) so the write site and
the column cannot drift apart again.
"""
import uuid

import pytest

from app.models.connector import Connector, generate_api_key

COLUMN_WIDTH = Connector.__table__.c.api_key_prefix.type.length


async def _register(client, headers, space_name="A06 Space"):
    resp = await client.post(
        "/api/v1/connectors/register",
        params={
            "space_id": f"space-{uuid.uuid4().hex[:8]}",
            "space_name": space_name,
            "space_url": "https://a06.example.test",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest.mark.asyncio
async def test_registered_connector_prefix_fits_the_column(client, db_session, make_user):
    """The value the register handler writes must fit ``varchar(8)`` on PG."""
    headers, _ = await make_user("admin", "a06_admin")
    body = await _register(client, headers)

    api_key = body["api_key"]
    assert len(api_key) > COLUMN_WIDTH, "the generated key is longer than the column"

    stored = await db_session.get(Connector, uuid.UUID(body["id"]))
    assert 0 < len(stored.api_key_prefix) <= COLUMN_WIDTH, (
        f"api_key_prefix {stored.api_key_prefix!r} is {len(stored.api_key_prefix)} chars, "
        f"column width is {COLUMN_WIDTH} — PostgreSQL rejects this insert (A6)"
    )
    assert body["api_key_prefix"] == stored.api_key_prefix


@pytest.mark.asyncio
async def test_rotated_connector_prefix_fits_the_column(client, db_session, make_user):
    headers, _ = await make_user("admin", "a06_rot_admin")
    body = await _register(client, headers, space_name="A06 Rotate Space")

    resp = await client.post(
        f"/api/v1/connectors/{body['id']}/rotate-key",
        json={"reason": "A06 key rotation drill", "ticket_id": "T-A06"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    rotated = resp.json()

    stored = await db_session.get(Connector, uuid.UUID(body["id"]))
    assert 0 < len(stored.api_key_prefix) <= COLUMN_WIDTH, (
        f"rotated api_key_prefix {stored.api_key_prefix!r} overflows varchar({COLUMN_WIDTH})"
    )
    assert rotated["api_key_prefix"] == stored.api_key_prefix


@pytest.mark.asyncio
async def test_prefix_still_identifies_its_own_key(client, make_user):
    """The stored prefix is shown to operators to tell connectors apart.

    A constant truncation would fit the column but be worthless (every key
    starts with the same ``cds-conn-`` scheme), so the prefix must be a
    substring of *that* connector's key and differ between connectors.
    """
    headers, _ = await make_user("admin", "a06_ident_admin")
    first = await _register(client, headers, space_name="A06 First")
    second = await _register(client, headers, space_name="A06 Second")

    for body in (first, second):
        assert body["api_key_prefix"] and body["api_key"].find(body["api_key_prefix"]) > 0
    assert first["api_key_prefix"] != second["api_key_prefix"]


def test_generated_key_is_wider_than_the_column():
    """Guard for the guard: if the key format ever shrinks, the tests above
    stop proving anything."""
    api_key, _ = generate_api_key()
    assert len(api_key) > COLUMN_WIDTH
