"""SS-17: deterministic Merkle leaf ordering across /audit/anchor, /audit/verify
and /audit/merkle-proof.

merkle_service builds the tree from the *positional* order of the leaf list, so the
root is reproducible only if the anchoring query and both verifying queries return
rows in one identical total order. Measured on sqlite (probe, same models): the
``id IN (...)`` shape of /anchor answers in primary-key order while the
``blockchain_tx_hash = ...`` shape of /verify answers in storage order - two
different roots for one untampered batch.

The rows below are built so created_at order, primary-key order and insertion order
are three different permutations, with a created_at tie group inside them. Remove
any ORDER BY in app/api/audit.py, or drop its id tiebreaker, and an assertion below
goes red.

The leaf formula is duplicated from app/api/audit.py on purpose: recomputing it
locally is what makes the expected root independent of the endpoint under test.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.audit import _MERKLE_ORDER_BY
from app.models.audit_log import AuditLog
from app.services.crypto_service import crypto_service
from app.services.merkle_service import merkle_service

BASE_TIME = datetime(2026, 1, 5, 3, 4, 5, tzinfo=timezone.utc)

# name -> (created_at offset in minutes, rank of its uuid among the four)
#   canonical (created_at asc, id asc) = A, C, B, D   (C beats B inside the tie group)
#   primary key (id asc)               = C, A, D, B
#   insertion (rowid)                  = D, B, A, C
SLOTS = {"A": (0, 1), "B": (10, 3), "C": (10, 0), "D": (20, 2)}
CANONICAL = ("A", "C", "B", "D")
ID_ASC = ("C", "A", "D", "B")
INSERTION = ("D", "B", "A", "C")


def _leaf(record: AuditLog) -> str:
    return crypto_service.sm3_hash(
        f"{record.id}|{record.action}|{record.resource_type}|{record.resource_id}|{record.created_at}".encode()
    )


def _root(rows: list[AuditLog]) -> str:
    return merkle_service.get_root([_leaf(r) for r in rows])


def _ordered(rows: dict[str, AuditLog], order: tuple[str, ...]) -> list[AuditLog]:
    return [rows[name] for name in order]


async def _seed(db_session: AsyncSession) -> dict[str, AuditLog]:
    ids = sorted((uuid.uuid4() for _ in SLOTS), key=str)
    rows = {
        name: AuditLog(
            id=ids[rank],
            user_id=None,
            session_id=None,
            action=f"ss17.ordering.{name}",
            resource_type="ss17_ordering_probe",
            resource_id=f"resource-{name}",
            detail={"slot": name},
            created_at=BASE_TIME + timedelta(minutes=minutes),
        )
        for name, (minutes, rank) in SLOTS.items()
    }
    for name in INSERTION:
        db_session.add(rows[name])
    await db_session.flush()
    return rows


async def _reload(db_session: AsyncSession, rows: dict[str, AuditLog]) -> list[AuditLog]:
    result = await db_session.execute(
        select(AuditLog).where(AuditLog.id.in_([r.id for r in rows.values()]))
    )
    return list(result.scalars().all())


@pytest_asyncio.fixture
async def anchored_batch(client: AsyncClient, db_session: AsyncSession, operator_headers: dict):
    """Anchor A-D through the API, then mirror the tx_hash stamping onto the rows.

    pg_append_only leaves audit rows unstamped (_stamp_records is the FISCO/Ant
    path), so the fixture writes the tx_hash the way a stamping backend leaves it -
    without it /verify and /merkle-proof would have no anchored batch to work on.
    """
    rows = await _seed(db_session)
    resp = await client.post(
        "/api/v1/audit/anchor",
        json={"record_ids": [str(r.id) for r in rows.values()]},
        headers=operator_headers,
    )
    assert resp.status_code == 200, resp.text
    anchor = resp.json()
    assert anchor["anchored"] == 4
    assert anchor["tx_hash"]

    for row in rows.values():
        row.blockchain_tx_hash = anchor["tx_hash"]
    await db_session.flush()
    return {"rows": rows, "anchor": anchor}


@pytest.mark.asyncio
async def test_anchor_root_follows_created_at_order_not_row_order(
    client: AsyncClient, db_session: AsyncSession, operator_headers: dict,
):
    rows = await _seed(db_session)
    assert len(await _reload(db_session, rows)) == 4

    canonical_root = _root(_ordered(rows, CANONICAL))
    assert canonical_root != _root(_ordered(rows, ID_ASC))
    assert canonical_root != _root(_ordered(rows, INSERTION))
    assert _root(_ordered(rows, ID_ASC)) != _root(_ordered(rows, INSERTION))

    resp = await client.post(
        "/api/v1/audit/anchor",
        json={"record_ids": [str(r.id) for r in rows.values()]},
        headers=operator_headers,
    )
    assert resp.status_code == 200
    body = resp.json()

    assert body["merkle_root"] == canonical_root
    assert body["merkle_root"] != _root(_ordered(rows, ID_ASC))
    assert body["merkle_root"] != _root(_ordered(rows, INSERTION))


@pytest.mark.asyncio
async def test_verify_rebuilds_root_in_canonical_order(
    client: AsyncClient, db_session: AsyncSession, operator_headers: dict, anchored_batch: dict,
):
    anchor = anchored_batch["anchor"]
    rows = anchored_batch["rows"]
    canonical_root = _root(_ordered(rows, CANONICAL))

    for name in CANONICAL:
        resp = await client.get(f"/api/v1/audit/verify/{rows[name].id}", headers=operator_headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["verified"] is True, (
            "verify rebuilt a different root than the anchored one - the sibling "
            f"query lost its ORDER BY: {body}"
        )
        assert body["merkle_root"] == canonical_root
        assert body["tx_hash"] == anchor["tx_hash"]
        assert body["anchor_id"] == anchor["anchor_id"]
        assert body["block_number"] is None
        assert body["backend"]["chain_anchoring"] == "pg-log"
        assert body["backend"]["is_consortium_chain"] is False
        assert body["verification_note"]


@pytest.mark.asyncio
async def test_merkle_proof_root_and_leaf_index_follow_canonical_order(
    client: AsyncClient, db_session: AsyncSession, operator_headers: dict, anchored_batch: dict,
):
    anchor = anchored_batch["anchor"]
    rows = anchored_batch["rows"]
    canonical_root = _root(_ordered(rows, CANONICAL))

    for expected_index, name in enumerate(CANONICAL):
        resp = await client.get(
            f"/api/v1/audit/merkle-proof/{rows[name].id}", headers=operator_headers
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["merkle_root"] == canonical_root, (
            "merkle-proof rebuilt the tree in a different row order - the sibling "
            f"query lost its ORDER BY: {body}"
        )
        assert body["leaf_index"] == expected_index, body
        assert body["total_leaves"] == 4
        assert body["record_hash"] == _leaf(rows[name])
        assert body["anchor_id"] == anchor["anchor_id"]
        assert body["block_number"] is None


@pytest.mark.asyncio
async def test_proof_path_recomputes_root_from_leaf(
    client: AsyncClient, db_session: AsyncSession, operator_headers: dict, anchored_batch: dict,
):
    """The published path must be independently verifiable from the leaf hash."""
    rows = anchored_batch["rows"]
    for name in CANONICAL:
        resp = await client.get(
            f"/api/v1/audit/merkle-proof/{rows[name].id}", headers=operator_headers
        )
        body = resp.json()
        current = body["record_hash"]
        for step in body["proof_path"]:
            if step["position"] == "right":
                current = merkle_service.hash_internal(current, step["hash"])
            else:
                current = merkle_service.hash_internal(step["hash"], current)
        assert current == body["merkle_root"], name


@pytest.mark.asyncio
async def test_block_number_exposed_when_backend_reports_height(
    client: AsyncClient, db_session: AsyncSession, operator_headers: dict, anchored_batch: dict,
    monkeypatch: pytest.MonkeyPatch,
):
    """Response mapping for a backend that does report a chain height.

    Only the height/confirmation/backend attributes of the cached anchor record are
    changed - tx_hash and data_hash stay untouched, so verified=True below still
    means the SM3 Merkle root was really recomputed and compared.
    """
    from app.services.blockchain_adapter import ChainBackend, blockchain_adapter

    anchor = anchored_batch["anchor"]
    record = blockchain_adapter._anchors[anchor["anchor_id"]]
    monkeypatch.setattr(record, "block_number", 18234)
    monkeypatch.setattr(record, "confirmed", True)
    monkeypatch.setattr(record, "backend", ChainBackend.FISCO_BCOS)

    row = anchored_batch["rows"][CANONICAL[0]]

    resp = await client.get(f"/api/v1/audit/verify/{row.id}", headers=operator_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["verified"] is True
    assert body["block_number"] == 18234
    assert body["confirmed"] is True
    assert body["backend"]["backend"] == "fisco_bcos"
    assert body["backend"]["is_consortium_chain"] is True

    proof = await client.get(f"/api/v1/audit/merkle-proof/{row.id}", headers=operator_headers)
    assert proof.status_code == 200
    assert proof.json()["block_number"] == 18234
    assert proof.json()["confirmed"] is True


@pytest.mark.asyncio
async def test_unanchored_record_never_reports_success(
    client: AsyncClient, db_session: AsyncSession, operator_headers: dict,
):
    row = AuditLog(
        user_id=None, action="ss17.unanchored", resource_type="ss17_ordering_probe",
        resource_id="resource-unanchored", detail={}, created_at=BASE_TIME,
    )
    db_session.add(row)
    await db_session.flush()

    resp = await client.get(f"/api/v1/audit/verify/{row.id}", headers=operator_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["verified"] is False
    assert "not anchored" in body["reason"]

    proof = await client.get(f"/api/v1/audit/merkle-proof/{row.id}", headers=operator_headers)
    assert proof.status_code == 400


def test_merkle_order_by_compiles_to_a_total_order():
    sql = " ".join(
        str(select(AuditLog.id).order_by(*_MERKLE_ORDER_BY)).split()
    )
    assert sql.endswith("ORDER BY audit_logs.created_at ASC, audit_logs.id ASC")
