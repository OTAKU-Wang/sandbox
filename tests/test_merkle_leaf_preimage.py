"""AUDIT-LEAF: Merkle 叶预映像单一来源化 + 写入侧 "|" 完整性守卫。

The Merkle leaf preimage — `id|action|resource_type|resource_id|created_at`,
SM3-hashed, Merkle root anchored immutably on-chain — was rebuilt by a raw
f-string pipe-join in five places (app/api/audit.py anchor/verify/merkle-proof
x2, app/services/cube_egress_audit.py::_anchor_batch). "|" is neither escaped
nor length-prefixed in that join, so two different field tuples can produce the
SAME preimage (action="a|b", resource_type="c" vs action="a",
resource_type="b|c") and therefore the same leaf/anchor for different facts.

Compatibility iron law: the leaf STRING FORMAT IS FROZEN. /verify and
/merkle-proof recompute the leaf, so any format change would make every
already-anchored root unverifiable forever. Hence exactly two changes:

1. one helper — ``merkle_service.merkle_leaf_preimage`` — that reproduces the
   old f-string byte-for-byte (pinned to a literal below, and the endpoints are
   checked at runtime against a locally re-implemented copy of the old string);
2. a write-side guard in ``audit_service.log`` (the only ``AuditLog(...)``
   constructor in app/): "|" in ``action``/``resource_type``/``resource_id`` is
   escaped (``\\`` -> ``\\\\``, ``|`` -> ``\\|``) so the pipe-join of NEW rows is
   uniquely decodable, and the escape is DISCLOSED in
   ``detail["_leaf_escaped_fields"]`` + a warning log (honest-degradation rule —
   never mutate silently, cf. ``detail["_sm2_sign_error"]``).

Sanitise rather than raise, deliberately: ``resource_id`` legitimately carries
node-supplied free text on the cube-egress path
(``cube_egress_audit.map_egress_record`` writes ``str(policy["matched_rule"])``,
an unvalidated JSONL field), and ``db_access_proxy`` wraps ``audit_service.log``
in ``try/except: pass`` — raising there would silently destroy audit rows,
which is a worse integrity loss than the ambiguity.
"""
import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.services.audit_service import audit_service
from app.services.crypto_service import crypto_service
from app.services.merkle_service import merkle_service

REPO = Path(__file__).resolve().parents[1]

BASE_TIME = datetime(2026, 3, 4, 5, 6, 7, 800000, tzinfo=timezone.utc)


# ─── Independent re-implementation of the FROZEN legacy format ───────────────
# Deliberately a copy of the old app-side f-string, never the helper under test:
# this is what proves the refactor moved no byte. (tests/test_ss17_audit_verify_
# ordering.py:47 keeps its own copy for the same reason.)

def _legacy_preimage(rec) -> bytes:
    return (
        f"{rec.id}|{rec.action}|{rec.resource_type}|{rec.resource_id}|{rec.created_at}"
    ).encode()


def _legacy_leaf(rec) -> str:
    return crypto_service.sm3_hash(_legacy_preimage(rec))


def _legacy_root(rows: list) -> str:
    return merkle_service.get_root([_legacy_leaf(r) for r in rows])


def _fake(
    *,
    rid: uuid.UUID,
    action: str,
    resource_type: str,
    resource_id: str | None,
    created_at: datetime = BASE_TIME,
) -> SimpleNamespace:
    """Duck-typed stand-in for an AuditLog row (no DB round-trip needed)."""
    return SimpleNamespace(
        id=rid,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        created_at=created_at,
    )


# ─── 1. Byte-for-byte equality with the legacy f-string ──────────────────────

def test_preimage_equals_legacy_fstring_literal():
    from app.services.merkle_service import merkle_leaf_preimage

    rid = uuid.UUID("11111111-2222-3333-4444-555555555555")
    row = _fake(
        rid=rid,
        action="sandbox.create",
        resource_type="sandbox_session",
        resource_id="sess-42",
    )
    # Fully literal, hand-checked: uuid | action | resource_type | resource_id |
    # str(datetime) — the exact bytes already inside anchored Merkle roots.
    assert merkle_leaf_preimage(row) == (
        b"11111111-2222-3333-4444-555555555555|sandbox.create|sandbox_session"
        b"|sess-42|2026-03-04 05:06:07.800000+00:00"
    )
    assert merkle_leaf_preimage(row) == _legacy_preimage(row)


def test_preimage_is_bytes_and_matches_legacy_for_many_rows():
    from app.services.merkle_service import merkle_leaf_preimage

    rows = [
        _fake(rid=uuid.uuid4(), action="a", resource_type="b", resource_id="c"),
        _fake(rid=uuid.uuid4(), action="delivery.dataset.downloaded",
              resource_type="data_product", resource_id=None),
        _fake(rid=uuid.uuid4(), action="kms.volume_key.revoke",
              resource_type="volume_key", resource_id="vkey-" + uuid.uuid4().hex,
              created_at=BASE_TIME + timedelta(seconds=1)),
        _fake(rid=uuid.uuid4(), action="中文动作", resource_type="资源",
              resource_id="id/含中文"),
    ]
    for row in rows:
        got = merkle_leaf_preimage(row)
        assert isinstance(got, bytes)
        assert got == _legacy_preimage(row)


def test_preimage_keeps_null_resource_id_as_literal_none():
    """f"{None}" == "None": a null resource_id must still render as the text
    "None" — rendering "" instead would change every already-anchored leaf."""
    from app.services.merkle_service import merkle_leaf_preimage

    row = _fake(rid=uuid.uuid4(), action="a", resource_type="b", resource_id=None)
    assert b"|None|" in merkle_leaf_preimage(row)
    assert merkle_leaf_preimage(row).count(b"|") == 4


def test_raw_join_ambiguity_is_real_without_the_write_guard():
    """Documents the defect the guard closes: two different tuples, one
    preimage, one anchored root."""
    from app.services.merkle_service import merkle_leaf_preimage

    rid = uuid.uuid4()
    left = _fake(rid=rid, action="a|b", resource_type="c", resource_id="d")
    right = _fake(rid=rid, action="a", resource_type="b|c", resource_id="d")
    assert left.action != right.action and left.resource_type != right.resource_type
    assert merkle_leaf_preimage(left) == merkle_leaf_preimage(right)
    assert _legacy_leaf(left) == _legacy_leaf(right)


# ─── 2. Write-side guard (audit_service.log is the only AuditLog writer) ──────

@pytest.mark.asyncio
async def test_log_escapes_pipe_and_discloses_it(db_session: AsyncSession, caplog):
    with caplog.at_level(logging.WARNING, logger="app.services.audit_service"):
        entry = await audit_service.log(
            db_session,
            action="weird|action",
            resource_type="rt|x",
            resource_id="rid|1",
        )
    # "\\" -> "\\\\" first, then "|" -> "\\|" (injective: nothing is lost).
    assert entry.action == "weird\\|action"
    assert entry.resource_type == "rt\\|x"
    assert entry.resource_id == "rid\\|1"
    # Honest degradation: the mutation is recorded on the row and logged loud.
    assert entry.detail["_leaf_escaped_fields"] == ["action", "resource_type", "resource_id"]
    assert any("_leaf_escaped" in rec.getMessage() or "leaf field" in rec.getMessage()
               for rec in caplog.records), [r.getMessage() for r in caplog.records]


@pytest.mark.asyncio
async def test_escaped_rows_cannot_collide(db_session: AsyncSession):
    """The end-to-end point of the guard: the ambiguous pair from
    test_raw_join_ambiguity_is_real_without_the_write_guard must land as two
    different leaves once both went through the write funnel."""
    from app.services.merkle_service import merkle_leaf_preimage

    left = await audit_service.log(
        db_session, action="a|b", resource_type="c", resource_id="d"
    )
    right = await audit_service.log(
        db_session, action="a", resource_type="b|c", resource_id="d"
    )
    assert merkle_leaf_preimage(left) != merkle_leaf_preimage(right)
    # …and the escape loses no information: un-escaping returns the input.
    assert left.action.replace("\\|", "|") == "a|b"
    assert right.resource_type.replace("\\|", "|") == "b|c"


@pytest.mark.asyncio
async def test_clean_values_are_untouched_and_undecorated(db_session: AsyncSession):
    entry = await audit_service.log(
        db_session,
        action="sandbox.create",
        resource_type="sandbox_session",
        resource_id="sess-1",
        detail={"k": "v"},
    )
    assert entry.action == "sandbox.create"
    assert entry.resource_type == "sandbox_session"
    assert entry.resource_id == "sess-1"
    assert "_leaf_escaped_fields" not in entry.detail
    assert entry.detail["k"] == "v"


@pytest.mark.asyncio
async def test_backslash_only_value_is_left_alone(db_session: AsyncSession):
    """Escaping must mutate the fewest values: only fields that actually carry
    "|" are rewritten (and the escape stays injective either way)."""
    entry = await audit_service.log(
        db_session, action="path\\check", resource_type="rt", resource_id="a\\b"
    )
    assert entry.action == "path\\check"
    assert entry.resource_id == "a\\b"
    assert "_leaf_escaped_fields" not in entry.detail


@pytest.mark.asyncio
async def test_escape_is_injective():
    from app.services.merkle_service import escape_leaf_field

    samples = [
        "plain", "a|b", "\\|", "a\\|b", "a\\\\b", "|", "||", "\\||", "x|y|z",
        None, "", "cube_egress.access",
    ]
    encoded = {s: escape_leaf_field(s) for s in samples}
    for raw in samples:
        if raw is None:
            assert encoded[raw] is None
            continue
        # no escaped output can equal a pass-through output: pass-throughs hold
        # no "|" at all, escaped ones always hold the "\|" digraph.
        if "|" not in raw:
            assert encoded[raw] == raw
        else:
            assert "\\|" in encoded[raw]
    distinct = set(encoded.values())
    assert len(distinct) == len(samples), encoded


@pytest.mark.asyncio
async def test_signature_covers_the_stored_escaped_values(
    db_session: AsyncSession, monkeypatch
):
    """The SM2 event string is pipe-joined too (audit_service.py:93); it must be
    computed from what is actually stored, or later verification fails."""
    from app.services.crypto_service import crypto_service

    signed: list[bytes] = []
    real_sign = crypto_service.sign
    real_hsm = crypto_service.sign_with_hsm

    def _spy(data: bytes, *args, **kwargs):
        signed.append(data)
        return real_sign(data, *args, **kwargs)

    def _spy_hsm(data: bytes, *args, **kwargs):
        signed.append(data)
        return real_hsm(data, *args, **kwargs)

    monkeypatch.setattr(crypto_service, "sign", _spy)
    monkeypatch.setattr(crypto_service, "sign_with_hsm", _spy_hsm)
    entry = await audit_service.log(
        db_session, action="a|b", resource_type="c", resource_id="d"
    )
    assert signed, "no SM2 event was signed in this environment"
    # sign_with_hsm falls back to the software path internally, so the same
    # event bytes can reach both spies — every payload signed must be the
    # escaped one.
    assert set(signed) == {b"a\\|b|c|d|"}
    assert signed[-1] == (
        f"{entry.action}|{entry.resource_type}|{entry.resource_id or ''}|"
    ).encode()


# ─── 3. Runtime equality: the four API sites + the egress site ────────────────

async def _seed_rows(db_session: AsyncSession, n: int = 3) -> list[AuditLog]:
    ids = [f"leaf-probe-{uuid.uuid4().hex[:8]}" for _ in range(n)]
    for rid in ids:
        await audit_service.log(
            db_session,
            action="leafprobe.execute",
            resource_type="leaf_probe",
            resource_id=rid,
        )
    result = await db_session.execute(
        select(AuditLog).where(AuditLog.resource_id.in_(ids))
    )
    rows = list(result.scalars().all())
    assert len(rows) == n
    # Canonical anchoring order is (created_at, id) — see _MERKLE_ORDER_BY.
    return sorted(rows, key=lambda r: (r.created_at, str(r.id)))


@pytest.mark.asyncio
async def test_anchor_endpoint_root_equals_legacy_bytes(
    client: AsyncClient, db_session: AsyncSession, operator_headers: dict
):
    rows = await _seed_rows(db_session)
    expected_root = _legacy_root(rows)

    resp = await client.post(
        "/api/v1/audit/anchor",
        json={"record_ids": [str(r.id) for r in rows]},
        headers=operator_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["anchored"] == len(rows)
    assert body["merkle_root"] == expected_root
    tx_hash = body["tx_hash"]
    for r in rows:
        r.blockchain_tx_hash = tx_hash
    await db_session.flush()

    # /verify recomputes the whole sibling root.
    verify = await client.get(
        f"/api/v1/audit/verify/{rows[0].id}", headers=operator_headers
    )
    assert verify.status_code == 200, verify.text
    assert verify.json()["merkle_root"] == expected_root
    assert verify.json()["verified"] is True

    # /merkle-proof recomputes both the single leaf and the rebuilt root.
    for i, row in enumerate(rows):
        proof = await client.get(
            f"/api/v1/audit/merkle-proof/{row.id}", headers=operator_headers
        )
        assert proof.status_code == 200, proof.text
        body = proof.json()
        assert body["record_hash"] == _legacy_leaf(row)
        assert body["merkle_root"] == expected_root
        assert body["leaf_index"] == i


class _NoopDb:
    """Stands in for the session plumbing around _anchor_batch so the leaf
    expression itself runs for real on in-memory rows."""

    async def refresh(self, _entry):
        return None

    async def commit(self):
        return None


@pytest.mark.asyncio
async def test_egress_anchor_batch_leaf_equals_legacy_bytes(monkeypatch):
    from app.services import cube_egress_audit as egress
    from app.services.blockchain_adapter import blockchain_adapter as _real

    rows = [
        AuditLog(
            id=uuid.uuid4(), action="cube_egress.access", resource_type="egress",
            resource_id="rule-7", created_at=BASE_TIME,
        ),
        AuditLog(
            id=uuid.uuid4(), action="cube_egress.security_event",
            resource_type="egress", resource_id=None,
            created_at=BASE_TIME + timedelta(seconds=1),
        ),
    ]
    captured: dict = {}

    class _Anchor:
        success = True
        anchor_id = "anchor-1"
        tx_hash = "0xdeadbeef"
        block_number = None
        confirmed = True
        backend = _real.get_backend_type()

    class _Result:
        def __init__(self):
            self.success = True
            self.anchor = _Anchor()

    async def _fake_anchor(merkle_root, metadata=None):
        captured["merkle_root"] = merkle_root.decode()
        captured["metadata"] = metadata
        return _Result()

    monkeypatch.setattr(
        "app.services.blockchain_adapter.blockchain_adapter.anchor", _fake_anchor
    )
    await egress._anchor_batch(_NoopDb(), rows, "node-1")

    assert captured["merkle_root"] == _legacy_root(rows)
    assert all(r.blockchain_tx_hash == "0xdeadbeef" for r in rows)


# ─── 4. The leaf format lives in exactly one module ───────────────────────────

def test_leaf_format_is_single_sourced():
    api_src = (REPO / "app/api/audit.py").read_text(encoding="utf-8")
    egress_src = (
        REPO / "app/services/cube_egress_audit.py"
    ).read_text(encoding="utf-8")
    helper_src = (
        REPO / "app/services/merkle_service.py"
    ).read_text(encoding="utf-8")

    # no hand-rolled leaf join left in the consumers …
    assert "resource_type}|{" not in api_src
    assert "resource_type}|{" not in egress_src
    # … they call the helper (4 sites in the API: anchor, verify, and both
    # computations inside merkle-proof; 1 in the egress anchoring path) …
    assert api_src.count("merkle_leaf_preimage(") == 4
    assert egress_src.count("merkle_leaf_preimage(") == 1
    # … and the format itself appears exactly once, in the helper.
    assert helper_src.count("resource_type}|{log.resource_id}|{log.created_at}") == 1
