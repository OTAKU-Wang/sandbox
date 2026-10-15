"""SS-10 CB-06 — CDS egress audit ingestion endpoint + schema mapping.

Covers: service-token auth (401 on missing/wrong/unconfigured), batch store,
idempotency-key dedup (replay → 0 new), Merkle anchoring of stored rows,
the full cds_audit.audit_events column mapping (field-level), and the
`source` filter on the existing audit list route.
"""
import uuid

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.models.audit_log import AuditLog
from app.models.cube_egress_dedup import CubeEgressDedup
from app.services.cube_egress_audit import (
    KNOWN_EVENT_TYPES,
    build_audit_row,
    map_egress_record,
    normalize_event_type,
)

TOKEN = "cb06-test-token"

NODE = "node-cb06"
FILE = "access.jsonl#ino-123"


def _rec(i, event):
    return {
        "ts": f"2026-10-13T10:00:{i:02d}.000Z",
        "request_id": f"1-{i}",
        "event": event,
        "sandbox": {"src_ip": "10.0.0.5", "policy_id": "pol-9"},
        "conn": {"original_dst_ip": "93.184.216.34", "original_dst_port": 443},
        "tls": {
            "sni": "api.example.com",
            "cipher": "ECDHE-RSA-AES128-GCM-SHA256",
            "version": "TLSv1.3",
        },
        "http": {
            "method": "POST",
            "host": "api.example.com",
            "path": "/v1/data",
            "status": 200,
            "req_bytes": 42,
            "resp_bytes": 1337,
            "user_agent": "cube-sandbox/0.7",
        },
        "policy": {"matched_rule": "rule-7", "decision": "allow", "duration_us": 2500},
        "credentials": {"injected": ["Authorization"], "secret_ids": ["cred-1", "cred-2"]},
        "security": None,
    }


def _batch(events=("http_request", "security_event", "tls_handshake", "weird_event")):
    return {
        "node_id": NODE,
        "file_name": FILE,
        "entries": [
            {
                "offset": i * 100,
                "line_no": i + 1,
                "record": _rec(i + 1, event),
            }
            for i, event in enumerate(events)
        ],
    }


@pytest.fixture
def token(monkeypatch):
    monkeypatch.setattr(get_settings(), "CUBE_AUDIT_SERVICE_TOKEN", TOKEN)
    return {"Authorization": f"Bearer {TOKEN}"}


async def test_missing_or_wrong_token_is_401(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "CUBE_AUDIT_SERVICE_TOKEN", TOKEN)
    r = await client.post("/internal/audit/egress", json=_batch())
    assert r.status_code == 401
    r = await client.post(
        "/internal/audit/egress", json=_batch(), headers={"Authorization": "Bearer wrong"}
    )
    assert r.status_code == 401
    monkeypatch.setattr(get_settings(), "CUBE_AUDIT_SERVICE_TOKEN", None)
    r = await client.post(
        "/internal/audit/egress", json=_batch(), headers={"Authorization": f"Bearer {TOKEN}"}
    )
    assert r.status_code == 401


async def test_batch_store_replay_idempotent(client, db_session, token):
    r = await client.post("/internal/audit/egress", json=_batch(), headers=token)
    assert r.status_code == 200
    assert r.json() == {"received": 4, "duplicates": 0, "stored": 4}

    # stored through the existing audit path (PG audit_logs)
    rows = (await db_session.execute(select(AuditLog))).scalars().all()
    assert len(rows) == 4
    assert sorted(x.action for x in rows) == sorted(
        ["cube_egress.access", "cube_egress.security_event", "cube_egress.tls_handshake", "cube_egress.other"]
    )
    # dedup rows: one per idempotency key
    dedup = (await db_session.execute(select(CubeEgressDedup))).scalars().all()
    assert len(dedup) == 4
    assert {(d.node_id, d.file_name, d.offset, d.line_no) for d in dedup} == {
        (NODE, FILE, i * 100, i + 1) for i in range(4)
    }
    # synchronous Merkle anchoring stamped the batch on every stored row
    tx = {x.blockchain_tx_hash for x in rows}
    assert len(tx) == 1 and None not in tx

    # replay of the same batch → 0 new rows (idempotency key dedup)
    r = await client.post("/internal/audit/egress", json=_batch(), headers=token)
    assert r.status_code == 200
    assert r.json() == {"received": 4, "duplicates": 4, "stored": 0}
    rows = (await db_session.execute(select(AuditLog))).scalars().all()
    assert len(rows) == 4

    # provenance + source land in the stored detail
    d = next(x for x in rows if x.action == "cube_egress.access")
    assert d.detail["source"] == "cube_egress"
    assert d.detail["node_id"] == NODE
    assert d.detail["file_name"] == FILE
    assert d.detail["offset"] == 0
    assert d.ip_address == "10.0.0.5"


def test_column_mapping_field_level():
    record = _rec(1, "http_request")
    record["security"] = {
        "reason": "upstream_unreachable_or_unverified",
        "upstream_status": "502",
        "upstream_addr": "10.1.1.1:443",
    }
    mapped = map_egress_record(record, NODE, FILE, offset=0, line_no=1)
    row = build_audit_row(mapped, event_id=str(uuid.uuid4()))

    # the 43 INSERT columns of cds_audit.audit_events are all present
    assert len(row) == 43
    assert row["event_id"] == row["event_id"]
    # source (spec: source='cube_egress' — carried in action_category;
    # the CH table has no dedicated source column)
    assert row["action"] == "cube_egress.access"
    assert row["action_category"] == "cube_egress"
    assert row["resource_type"] == "egress"
    # host/path/method → existing columns
    assert row["resource_name"] == "api.example.com"
    assert row["request_path"] == "/v1/data"
    assert row["request_method"] == "POST"
    # verdict/rule_id/credential_id → existing columns
    assert row["policy_decision"] == "allow"
    assert row["resource_id"] == "rule-7"
    assert row["encryption_key_id"] == "cred-1,cred-2"
    # client context
    assert row["ip_address"] == "10.0.0.5"
    assert row["user_agent"] == "cube-sandbox/0.7"
    assert row["bytes_read"] == 42
    assert row["bytes_written"] == 1337
    assert row["compute_duration_ms"] == 2  # 2500us → 2ms
    # cube-specific → detail JSON
    detail = row["detail"]
    assert detail["sni"] == "api.example.com"
    assert detail["tls_cipher"] == "ECDHE-RSA-AES128-GCM-SHA256"
    assert detail["tls_version"] == "TLSv1.3"
    assert detail["upstream_status"] == "502"
    assert detail["upstream_addr"] == "10.1.1.1:443"
    assert "upstream failure" in detail["error_message"]
    assert detail["event_type"] == "access"
    assert detail["policy_id"] == "pol-9"
    assert detail["egress_request_id"] == "1-1"
    assert row["tags"] == ["cube_egress", "access"]
    # defaults on unused columns
    assert row["user_id"] == "00000000-0000-0000-0000-000000000000"
    assert row["sandbox_id"] == "00000000-0000-0000-0000-000000000000"
    assert row["sm2_signature"] == ""
    assert row["data_product_version"] == 0


def test_event_type_normalization():
    for t in KNOWN_EVENT_TYPES:
        assert normalize_event_type(t) == t
    # upstream emits "http_request" for the access record
    assert normalize_event_type("http_request") == "access"
    # unknown → distinct counted value, never 'access'
    assert normalize_event_type("weird") == "other"
    assert normalize_event_type(None) == "other"
    assert normalize_event_type(42) == "other"


async def test_source_filter_on_audit_list(client, db_session, token, admin_headers):
    await client.post("/internal/audit/egress", json=_batch(), headers=token)
    r = await client.get("/api/v1/audit/records", headers=admin_headers)
    assert r.status_code == 200
    all_total = r.json()["total"]
    assert all_total >= 4

    r = await client.get("/api/v1/audit/records?source=cube_egress", headers=admin_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 4
    assert all(item["action"].startswith("cube_egress.") for item in body["items"])

    r = await client.get(
        "/api/v1/audit/records?source=cube_egress&action=cube_egress.security_event",
        headers=admin_headers,
    )
    assert r.json()["total"] == 1
