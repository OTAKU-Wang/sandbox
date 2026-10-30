"""Cube egress audit ingestion (SS-10 CB-06).

Receives forced-point CubeEgress JSONL lines from the node audit tailer
(POST /internal/audit/egress), dedups them on the idempotency key
(node_id, file_name, offset, line_no) in the PG ``cube_egress_dedup`` table,
persists new lines through the existing audit path (``audit_service.log`` →
PG audit_logs + ClickHouse cds_audit.audit_events) and anchors each stored
batch through the synchronous Merkle path (SM3 leaf hashes → merkle_service
root → blockchain_adapter anchor → blockchain_tx_hash stamp), the same call
chain /api/v1/audit/anchor uses.

Upstream JSONL schema (CubeSandbox v0.7, CubeEgress/lua/audit.lua, spec
SS-10 §2.7): {ts, request_id, event, sandbox{}, conn{}, tls{}, http{},
policy{}, credentials{}, security{}, redacted_*}.  The "access" record is
emitted with event="http_request" by the upstream code.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.models.cube_egress_dedup import CubeEgressDedup
from app.services.audit_service import audit_service

logger = logging.getLogger(__name__)

ZERO_UUID = "00000000-0000-0000-0000-000000000000"

# canonical source label (spec SS-10 CB-06 step 2: source='cube_egress').
# The CH audit_events table has no `source` column; the source value is
# carried in `action_category` (LowCardinality, documented bucket column)
# and in detail["source"].
EGRESS_SOURCE = "cube_egress"
# canonical event types (spec CB-06 step 2); upstream emits "http_request"
# for the access record (CubeEgress/lua/audit.lua build_record)
KNOWN_EVENT_TYPES = ("access", "security_event", "tls_handshake")
_EVENT_ALIASES = {"http_request": "access"}


class MalformedEgressRecord(ValueError):
    """One JSONL line whose shape cannot be mapped to an audit row (A81).

    Raised only by the mapping's own shape guards — never by infrastructure — so
    ``ingest_egress_batch`` can isolate a single unusable line while a genuine
    failure (database, dedup constraint) keeps propagating to the tailer.
    """


def normalize_event_type(raw: object) -> str:
    """Map the JSONL event field to the canonical set.

    Unknown values map to 'other' (never to 'access' — spec: unknown is a
    distinct, counted value, not an access record).
    """
    value = _EVENT_ALIASES.get(raw) if isinstance(raw, str) else None
    if value is None and isinstance(raw, str):
        value = raw
    return value if value in KNOWN_EVENT_TYPES else "other"


def _parse_ts(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _clean(mapping: dict) -> dict:
    """Drop None values so audit rows don't carry empty noise keys."""
    return {k: v for k, v in mapping.items() if v is not None}


def _section(record: dict, key: str) -> dict:
    """One documented sub-object of the line: absent is fine, wrong type is not.

    ``EgressEntry.record`` is an unconstrained ``dict``, so a sub-object that
    arrived as a string/number means this line is not the schema this mapper
    reads. Guessing past it would file a *plausible-looking* audit row for data
    nobody verified — the funnel would rather drop the line and say so (A81).
    """
    value = record.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise MalformedEgressRecord(
            f"{key} must be a JSON object, got {type(value).__name__}"
        )
    return value


def _str_list(value: object, field: str) -> list[str]:
    """A string list destined for ``",".join``; any other shape is rejected, not coerced."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise MalformedEgressRecord(
            f"{field} must be a list of strings, got {type(value).__name__}"
        )
    bad = next((item for item in value if not isinstance(item, str)), None)
    if bad is not None:
        raise MalformedEgressRecord(
            f"{field} must contain only strings, got a {type(bad).__name__} element"
        )
    return value


def _str(value: object, field: str) -> str | None:
    """A value destined for a typed ``audit_logs`` column (ip_address, user_agent)."""
    if value is None or isinstance(value, str):
        return value
    raise MalformedEgressRecord(f"{field} must be a string, got {type(value).__name__}")


def _shape_hint(payload: object) -> str:
    """Field names of a rejected line — never its values.

    Provenance (node/file/offset/line_no) plus these names is enough to find the
    original bytes in the node's own JSONL; copying egress payloads into the
    control-plane log would be a second, unredacted store of the same data.
    """
    return str(sorted(payload)) if isinstance(payload, dict) else type(payload).__name__


def map_egress_record(
    record: dict,
    node_id: str,
    file_name: str,
    offset: int,
    line_no: int,
) -> dict:
    """Map one JSONL line to the existing audit row fields.

    Column targets (CH cds_audit.audit_events via audit_service's writer):
    host→resource_name, path→request_path, method→request_method,
    verdict (policy.decision)→policy_decision, rule_id (policy.matched_rule)
    →resource_id, credential_id (credentials.secret_ids joined)→
    encryption_key_id; cube-specific fields (sni, tls result, upstream
    failure, provenance, redacted bodies) live in the detail JSON.
    """
    if not isinstance(record, dict):
        raise MalformedEgressRecord(
            f"record must be a JSON object, got {type(record).__name__}"
        )
    sandbox = _section(record, "sandbox")
    conn = _section(record, "conn")
    tls = _section(record, "tls")
    http = _section(record, "http")
    policy = _section(record, "policy")
    creds = _section(record, "credentials")
    security = _section(record, "security")

    event_type = normalize_event_type(record.get("event"))
    rule_id = policy.get("matched_rule")
    secret_ids = _str_list(creds.get("secret_ids"), "credentials.secret_ids")
    upstream_failed = bool(security.get("upstream_status") is not None)

    detail = _clean(
        {
            # audit_service CH writer reads these detail keys:
            "source": EGRESS_SOURCE,
            "action_category": EGRESS_SOURCE,
            "resource_name": http.get("host"),
            "request_method": http.get("method"),
            "request_path": http.get("path"),
            "request_id": str(uuid.uuid4()),  # CH UUID column
            "policy_decision": policy.get("decision"),
            "policy_reason": security.get("reason"),
            "encryption_key_id": ",".join(secret_ids) if secret_ids else None,
            "bytes_read": http.get("req_bytes") or 0,
            "bytes_written": http.get("resp_bytes") or 0,
            "compute_duration_ms": (policy.get("duration_us") or 0) // 1000,
            "error_message": (
                f"upstream failure: status={security.get('upstream_status')} "
                f"addr={security.get('upstream_addr')}"
                if upstream_failed
                else None
            ),
            "tags": [EGRESS_SOURCE, event_type],
            # provenance + idempotency:
            "event_type": event_type,
            "node_id": node_id,
            "file_name": file_name,
            "offset": offset,
            "line_no": line_no,
            "egress_request_id": record.get("request_id"),
            "policy_id": sandbox.get("policy_id"),
            # cube-specific fields (spec CB-06 step 2):
            "sni": tls.get("sni"),
            "tls_cipher": tls.get("cipher"),
            "tls_version": tls.get("version"),
            "tls_client_alpn": tls.get("client_alpn"),
            "tls_upstream_verified": tls.get("upstream_verified"),
            "upstream_status": security.get("upstream_status"),
            "upstream_addr": security.get("upstream_addr"),
            "original_dst": (
                f"{conn.get('original_dst_ip')}:{conn.get('original_dst_port')}"
                if conn.get("original_dst_ip")
                else None
            ),
            "http_status": http.get("status"),
            "injected_headers": creds.get("injected"),
            "redacted_request_headers": record.get("redacted_request_headers"),
            "redacted_request_body": record.get("redacted_request_body"),
            "redacted_response_body": record.get("redacted_response_body"),
        }
    )

    return {
        "event_type": event_type,
        "action": f"{EGRESS_SOURCE}.{event_type}",
        "resource_type": "egress",
        "resource_id": str(rule_id) if rule_id not in (None, "") else None,
        "ip_address": _str(sandbox.get("src_ip"), "sandbox.src_ip"),
        "user_agent": _str(http.get("user_agent"), "http.user_agent"),
        "detail": detail,
        "timestamp": _parse_ts(record.get("ts")),
    }


def build_audit_row(mapped: dict, event_id: str | None = None) -> dict:
    """Full cds_audit.audit_events row (all 43 INSERT columns) as a dict.

    Pure — used by tests for field-level mapping assertions and documents
    exactly what audit_service._write_to_clickhouse emits for an egress row.
    """
    d = mapped["detail"]
    return {
        "event_id": event_id or str(uuid.uuid4()),
        "timestamp": mapped.get("timestamp") or datetime.now(timezone.utc),
        "user_id": ZERO_UUID,
        "session_id": ZERO_UUID,
        "user_role": d.get("user_role", ""),
        "user_region": d.get("user_region", ""),
        "action": mapped["action"],
        "action_category": d.get("action_category", ""),
        "resource_type": mapped["resource_type"],
        "resource_id": mapped["resource_id"] or "",
        "resource_name": d.get("resource_name", ""),
        "ip_address": mapped.get("ip_address") or "0.0.0.0",
        "user_agent": mapped.get("user_agent") or "",
        "request_method": d.get("request_method", ""),
        "request_path": d.get("request_path", ""),
        "request_id": d.get("request_id", str(uuid.uuid4())),
        "sm2_signature": d.get("_sm2_signature", ""),
        "integrity_hash": d.get("integrity_hash", ""),
        "merkle_root": d.get("merkle_root", ""),
        "blockchain_tx_hash": d.get("blockchain_tx_hash", ""),
        "data_product_id": d.get("data_product_id", ZERO_UUID),
        "data_product_version": d.get("data_product_version", 0),
        "data_product_type": d.get("data_product_type", ""),
        "sandbox_id": d.get("sandbox_id", ZERO_UUID),
        "sandbox_type": d.get("sandbox_type", ""),
        "compute_duration_ms": d.get("compute_duration_ms", 0),
        "rows_affected": d.get("rows_affected", 0),
        "bytes_read": d.get("bytes_read", 0),
        "bytes_written": d.get("bytes_written", 0),
        "contract_id": d.get("contract_id", ZERO_UUID),
        "policy_decision": d.get("policy_decision", ""),
        "policy_reason": d.get("policy_reason", ""),
        "security_level": d.get("security_level", ""),
        "encryption_key_id": d.get("encryption_key_id", ""),
        "column_encrypted_fields": d.get("column_encrypted_fields", []),
        "training_job_id": d.get("training_job_id", ZERO_UUID),
        "model_id": d.get("model_id", ""),
        "dp_epsilon": d.get("dp_epsilon", 0.0),
        "dp_delta": d.get("dp_delta", 0.0),
        "error_code": d.get("error_code", ""),
        "error_message": d.get("error_message", ""),
        "detail": d,
        "tags": d.get("tags", []),
    }


async def _anchor_batch(db: AsyncSession, entries: list[AuditLog], node_id: str) -> None:
    """Synchronous Merkle anchoring — the same call chain as /api/v1/audit/anchor:
    SM3 leaf hash per record (id|action|resource_type|resource_id|created_at)
    → merkle_service.get_root → blockchain_adapter.anchor → tx_hash stamp."""
    from app.services.blockchain_adapter import blockchain_adapter
    from app.services.crypto_service import crypto_service
    from app.services.merkle_service import merkle_leaf_preimage, merkle_service

    for entry in entries:
        await db.refresh(entry)
    record_hashes = [
        crypto_service.sm3_hash(merkle_leaf_preimage(e))
        for e in entries
    ]
    merkle_root = merkle_service.get_root(record_hashes)
    try:
        result = await blockchain_adapter.anchor(
            merkle_root.encode(),
            {
                "source": EGRESS_SOURCE,
                "node_id": node_id,
                "record_count": len(entries),
                "record_ids": [str(e.id) for e in entries],
            },
        )
    except Exception as e:
        # anchoring failure is non-fatal for storage (same posture as
        # audit_service.log_to_blockchain) but must be loud — these rows
        # stay unanchored and /api/v1/audit/anchor can anchor them later
        logger.error("cube egress Merkle anchoring failed: %s", e)
        return
    if result.success and result.anchor and result.anchor.tx_hash:
        tx_hash = result.anchor.tx_hash
        for entry in entries:
            entry.blockchain_tx_hash = tx_hash
        await db.commit()


async def _claim_dedup_key(
    db: AsyncSession,
    node_id: str,
    file_name: str,
    offset: int,
    line_no: int,
) -> bool:
    """Insert one idempotency key; False when a racing writer already holds it.

    ``ingest_egress_batch``'s pre-check is a separate SELECT from this INSERT while
    ``uq_cube_egress_dedup_key`` is UNIQUE, and the tailer delivers at-least-once, so two
    replicas (or two batches) can pass the pre-check for the same line (A120). Without a
    guard the loser's IntegrityError escapes through the batch-end commit and takes every
    other line of the batch — plus its audit row — down with it. The INSERT gets its own
    SAVEPOINT because ``db.rollback()`` would expire everything the request session still
    holds, and only a *visible* winner row is treated as a duplicate: any other integrity
    failure keeps propagating instead of silently dropping the line.
    """
    try:
        async with db.begin_nested():
            db.add(
                CubeEgressDedup(
                    node_id=node_id, file_name=file_name, offset=offset, line_no=line_no
                )
            )
            await db.flush()
    except IntegrityError:
        winner = await db.execute(
            select(CubeEgressDedup.id).where(
                CubeEgressDedup.node_id == node_id,
                CubeEgressDedup.file_name == file_name,
                CubeEgressDedup.offset == offset,
                CubeEgressDedup.line_no == line_no,
            )
        )
        if winner.first() is None:
            raise
        logger.debug(
            "[cube_egress] dedup race lost for %s/%s:%s:%s; counting as duplicate",
            node_id, file_name, offset, line_no,
        )
        return False
    return True


async def ingest_egress_batch(
    db: AsyncSession,
    node_id: str,
    file_name: str,
    entries: list[dict],
) -> dict:
    """Dedup + store + anchor one tailer batch.

    entries: [{"offset": int, "line_no": int, "record": dict}, ...]
    Returns {"received": n, "duplicates": m, "stored": k}.  Lines that cannot be
    mapped are dropped one by one and disclosed through the ``malformed`` counter
    plus a WARNING carrying their provenance (A81); the response body keeps its
    three keys because the tailer contract pins them.
    """
    from app.core.metrics import record_cube_egress_lines

    received = len(entries)
    duplicates = 0
    malformed = 0
    stored: list[AuditLog] = []
    for entry in entries:
        try:
            offset = int(entry["offset"])
            line_no = int(entry["line_no"])
            record = entry["record"] or {}
        except (KeyError, TypeError, ValueError) as exc:
            # Envelope itself unusable — no provenance beyond the batch, so say that too.
            malformed += 1
            record_cube_egress_lines("malformed")
            logger.warning(
                "[cube_egress] dropped unusable entry envelope node=%s file=%s "
                "reason=%s: %s; entry_keys=%s",
                node_id, file_name, type(exc).__name__, exc, _shape_hint(entry),
            )
            continue
        existing = await db.execute(
            select(CubeEgressDedup.id).where(
                CubeEgressDedup.node_id == node_id,
                CubeEgressDedup.file_name == file_name,
                CubeEgressDedup.offset == offset,
                CubeEgressDedup.line_no == line_no,
            )
        )
        if existing.first() is not None:
            duplicates += 1
            record_cube_egress_lines("duplicate")
            continue
        if not await _claim_dedup_key(db, node_id, file_name, offset, line_no):
            duplicates += 1
            record_cube_egress_lines("duplicate")
            continue
        try:
            mapped = map_egress_record(record, node_id, file_name, offset, line_no)
        except (MalformedEgressRecord, AttributeError, TypeError, ValueError) as exc:
            # A81: one unmappable line used to escape through the batch-end commit and
            # 500 the whole POST, losing every other line of the batch — and because the
            # node tailer only advances its watermark on 2xx
            # (deploy/cube-node/audit_tailer.py), that one line blocked the file's whole
            # audit stream. Dropping it is only acceptable if it stays *visible*: counted,
            # warned, with the coordinates needed to recover the line from the node.
            #
            # Trade-off, chosen deliberately: _claim_dedup_key already consumed this
            # line's idempotency key, so a redelivery counts it as a duplicate instead of
            # re-mapping it. map_egress_record is a pure function of the line, so a retry
            # can only fail identically; leaving the key unclaimed would turn one known-bad
            # line into an unbounded warning loop with no chance of recovery. Recovery is
            # an explicit operator action: fix the mapping (or the producer), delete that
            # one cube_egress_dedup row, replay the file.
            malformed += 1
            record_cube_egress_lines("malformed")
            logger.warning(
                "[cube_egress] dropped unmappable egress line node=%s file=%s "
                "offset=%s line_no=%s reason=%s: %s; record_keys=%s",
                node_id, file_name, offset, line_no,
                type(exc).__name__, exc, _shape_hint(record),
            )
            continue
        audit_entry = await audit_service.log(
            db,
            action=mapped["action"],
            resource_type=mapped["resource_type"],
            resource_id=mapped["resource_id"],
            detail=mapped["detail"],
            ip_address=mapped["ip_address"],
            user_agent=mapped["user_agent"],
        )
        if mapped["timestamp"] is not None:
            audit_entry.created_at = mapped["timestamp"]
        stored.append(audit_entry)
        record_cube_egress_lines("stored")

    await db.commit()

    if stored:
        await _anchor_batch(db, stored, node_id)

    return {"received": received, "duplicates": duplicates, "stored": len(stored)}
