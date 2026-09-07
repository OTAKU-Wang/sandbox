"""W13: egress audit — JSONL trail, secret masking, drop semantics, query API."""
import asyncio
import uuid

import pytest
import pytest_asyncio
import fakeredis.aioredis

from app.core.config import get_settings
from app.services import egress_audit as ea


@pytest.fixture(autouse=True)
def _egress_settings(monkeypatch, tmp_path):
    settings = get_settings()
    monkeypatch.setattr(settings, "EGRESS_AUDIT_ENABLED", True)
    monkeypatch.setattr(settings, "EGRESS_AUDIT_LOG_PATH", str(tmp_path / "egress" / "access.jsonl"))
    # fresh writer per test
    ea._queue = None
    ea._writer_task = None
    ea._dropped_count = 0
    yield settings


async def _flush_writer():
    if ea._writer_task is None:
        return
    await ea.shutdown_egress_audit()
    ea._queue = None
    ea._writer_task = None


@pytest.mark.asyncio
async def test_allow_and_deny_events_written_as_jsonl(_egress_settings):
    ea.log_egress_event(
        session_id="sess-1", event_type="security_event",
        host="evil.example.com", path="dns-query", verdict="deny",
        rule_id="domain_allowlist",
    )
    ea.log_egress_event(
        session_id="sess-1", event_type="access",
        host="api.example.com", path="dns-query", verdict="allow",
        rule_id="domain_allowlist",
    )
    await _flush_writer()

    from pathlib import Path

    lines = Path(_egress_settings.EGRESS_AUDIT_LOG_PATH).read_text().strip().splitlines()
    assert len(lines) == 2
    deny = __import__("json").loads(lines[0])
    allow = __import__("json").loads(lines[1])
    assert deny["event_type"] == "security_event" and deny["verdict"] == "deny"
    assert deny["rule_id"] == "domain_allowlist" and deny["host"] == "evil.example.com"
    assert allow["verdict"] == "allow" and allow["ts"]


@pytest.mark.asyncio
async def test_secret_query_params_masked(_egress_settings):
    ea.log_egress_event(
        session_id="sess-2", event_type="access",
        host="api.example.com", path="/v1/data?token=abc123&x=1", verdict="allow",
        credential_used="cred-id-42",
    )
    await _flush_writer()

    from pathlib import Path

    raw = Path(_egress_settings.EGRESS_AUDIT_LOG_PATH).read_text()
    assert "abc123" not in raw
    assert "token=***" in raw
    assert "cred-id-42" in raw  # credential ID is recorded, never its value


@pytest.mark.asyncio
async def test_queue_full_drops_without_error(_egress_settings, monkeypatch):
    ea._queue = None
    ea._writer_task = None
    # freeze the writer so the queue fills
    async def _stuck_writer(queue, path):
        await asyncio.Event().wait()

    monkeypatch.setattr(ea, "_writer_loop", _stuck_writer)
    queued = 0
    for _ in range(ea._QUEUE_MAX + 50):
        if ea.log_egress_event(session_id="s", event_type="access", verdict="allow"):
            queued += 1
    assert queued == ea._QUEUE_MAX
    assert ea.dropped_count() == 50
    await ea.shutdown_egress_audit()
    ea._queue = None
    ea._writer_task = None


@pytest.mark.asyncio
async def test_disabled_audit_writes_nothing(_egress_settings, monkeypatch, tmp_path):
    monkeypatch.setattr(_egress_settings, "EGRESS_AUDIT_ENABLED", False)
    ok = ea.log_egress_event(session_id="s", event_type="access", verdict="allow")
    assert ok is False
    await _flush_writer()


@pytest.mark.asyncio
async def test_query_endpoint_filters_and_orders(client, db_session, _egress_settings, admin_headers):
    from app.models.sandbox_session import SandboxSession, SessionStatus
    from app.models.user import User

    unique = uuid.uuid4().hex[:8]
    admin_user = User(
        username=f"egadmin_{unique}",
        email=f"egadmin_{unique}@example.com",
        hashed_password="x",
        role="admin",
    )
    db_session.add(admin_user)
    await db_session.flush()
    session = SandboxSession(
        id=uuid.uuid4(),
        user_id=admin_user.id,
        data_product_id=uuid.uuid4(),
        sandbox_level="L3",
        sandbox_mode="structured_query",
        status=SessionStatus.RUNNING.value,
    )
    db_session.add(session)
    await db_session.flush()
    sid = str(session.id)

    ea.log_egress_event(session_id=sid, event_type="access", verdict="allow", host="a.example.com")
    await asyncio.sleep(0.01)
    ea.log_egress_event(session_id=sid, event_type="security_event", verdict="deny", host="b.example.com")
    ea.log_egress_event(session_id="other-session", event_type="access", verdict="allow", host="c.example.com")
    await _flush_writer()

    resp = await client.get(f"/api/v1/network-policies/session/{sid}/egress-audit", headers=admin_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 2
    # newest first
    assert body["records"][0]["host"] == "b.example.com"
    assert body["records"][1]["host"] == "a.example.com"

    since = body["records"][1]["ts"]  # timestamp of the oldest visible record
    tail = await client.get(
        f"/api/v1/network-policies/session/{sid}/egress-audit",
        params={"since": since}, headers=admin_headers,
    )
    assert tail.json()["count"] == 1
    assert tail.json()["records"][0]["host"] == "b.example.com"


def test_redact_query_string_preserves_keys():
    assert ea.redact_query_string("/x?api_key=SECRET&ok=1") == "/x?api_key=***&ok=1"
    assert ea.redact_query_string("/x?a=1") == "/x?a=1"
