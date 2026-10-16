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
    sandbox = record.get("sandbox") or {}
    conn = record.get("conn") or {}
    tls = record.get("tls") or {}
    http = record.get("http") or {}
    policy = record.get("policy") or {}
    creds = record.get("credentials") or {}
    security = record.get("security") or {}

    event_type = normalize_event_type(record.get("event"))
    rule_id = policy.get("matched_rule")
    secret_ids = creds.get("secret_ids") or []
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
        "ip_address": sandbox.get("src_ip"),
        "user_agent": http.get("user_agent"),
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
    from app.services.merkle_service import merkle_service

    for entry in entries:
        await db.refresh(entry)
    record_hashes = [
        crypto_service.sm3_hash(
            f"{e.id}|{e.action}|{e.resource_type}|{e.resource_id}|{e.created_at}".encode()
        )
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


async def ingest_egress_batch(
    db: AsyncSession,
    node_id: str,
    file_name: str,
    entries: list[dict],
) -> dict:
    """Dedup + store + anchor one tailer batch.

    entries: [{"offset": int, "line_no": int, "record": dict}, ...]
    Returns {"received": n, "duplicates": m, "stored": k}.
    """
    from app.core.metrics import record_cube_egress_lines

    received = len(entries)
    duplicates = 0
    stored: list[AuditLog] = []
    for entry in entries:
        offset = int(entry["offset"])
        line_no = int(entry["line_no"])
        record = entry["record"] or {}
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
        mapped = map_egress_record(record, node_id, file_name, offset, line_no)
        db.add(
            CubeEgressDedup(
                node_id=node_id, file_name=file_name, offset=offset, line_no=line_no
            )
        )
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
