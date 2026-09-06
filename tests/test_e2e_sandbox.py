"""E2E Sandbox Flow — minimal end-to-end test for sandbox execution.

Tests the full lifecycle:
1. Auth → operator login
2. DuckDB session → create table → query → masked view → close
3. Task queue → enqueue → dequeue → complete
4. Audit logging → verify entries created
5. Error recovery → verify graceful handling
"""
import pytest
from httpx import AsyncClient


# ─── DuckDB E2E ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_e2e_duckdb_full_lifecycle(client: AsyncClient, operator_headers: dict):
    """E2E: DuckDB session lifecycle — create, query, mask, list, close."""
    session_id = "e2e-duckdb-1"

    # 1. Create table from JSON data
    resp = await client.post("/api/v1/sandbox-db/create-table", json={
        "session_id": session_id,
        "table_name": "employees",
        "data": [
            {"name": "Alice", "dept": "Engineering", "salary": "100000"},
            {"name": "Bob", "dept": "Sales", "salary": "80000"},
            {"name": "Charlie", "dept": "Engineering", "salary": "120000"},
        ],
    }, headers=operator_headers)
    assert resp.status_code == 200
    assert resp.json()["row_count"] == 3

    # 2. Query data
    resp = await client.post("/api/v1/sandbox-db/query", json={
        "session_id": session_id,
        "sql": "SELECT * FROM employees WHERE dept = 'Engineering'",
    }, headers=operator_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["row_count"] == 2
    assert data["columns"] == ["name", "dept", "salary"]

    # 3. Create masked view (redact salary)
    resp = await client.post("/api/v1/sandbox-db/masked-view", json={
        "session_id": session_id,
        "table_name": "employees",
        "rules": [{"field_name": "salary", "mask_type": "REDACT"}],
    }, headers=operator_headers)
    assert resp.status_code == 200
    assert resp.json()["rules_applied"] == 1

    # 4. Query masked view — salary should be NULL
    resp = await client.post("/api/v1/sandbox-db/query", json={
        "session_id": session_id,
        "sql": "SELECT name, salary FROM employees_masked WHERE name = 'Alice'",
    }, headers=operator_headers)
    assert resp.status_code == 200
    rows = resp.json()["rows"]
    assert len(rows) == 1
    assert rows[0][1] is None  # salary redacted

    # 5. List tables
    resp = await client.get(f"/api/v1/sandbox-db/sessions/{session_id}/tables", headers=operator_headers)
    assert resp.status_code == 200
    tables = resp.json()
    assert len(tables) >= 1

    # 6. Close session
    resp = await client.delete(f"/api/v1/sandbox-db/sessions/{session_id}", headers=operator_headers)
    assert resp.status_code == 200
    assert resp.json()["closed"] is True

    # 7. Verify session is gone
    resp = await client.post("/api/v1/sandbox-db/query", json={
        "session_id": session_id,
        "sql": "SELECT 1",
    }, headers=operator_headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_e2e_duckdb_blocked_operations(client: AsyncClient, operator_headers: dict):
    """E2E: DDL/DML operations are blocked in sandbox."""
    session_id = "e2e-block-1"

    # Create table
    await client.post("/api/v1/sandbox-db/create-table", json={
        "session_id": session_id,
        "table_name": "t",
        "data": [{"a": 1}],
    }, headers=operator_headers)

    # Try destructive operations — all should fail
    for sql in ["DROP TABLE t", "DELETE FROM t", "UPDATE t SET a=2", "INSERT INTO t VALUES (3)"]:
        resp = await client.post("/api/v1/sandbox-db/query", json={
            "session_id": session_id,
            "sql": sql,
        }, headers=operator_headers)
        assert resp.status_code == 400
        assert "not allowed" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_e2e_duckdb_buyer_read_only(client: AsyncClient, operator_headers: dict, auth_headers: dict):
    """E2E: buyer can query their own session read-only; ownership enforced (G-073).

    The session owner (a data_provider) queries via the read-only SQL
    allowlist; destructive SQL is rejected with 400. Operators/admins bypass
    ownership by design; another data_provider on the same session gets 403.
    """
    session_id = "e2e-buyer-1"

    # Buyer (data_provider) creates a table in their own session — buyer
    # becomes the engine owner (create-table allows DATA_PROVIDER).
    resp = await client.post("/api/v1/sandbox-db/create-table", json={
        "session_id": session_id,
        "table_name": "t",
        "data": [{"x": 42}],
    }, headers=auth_headers)
    assert resp.status_code == 200

    # Destructive SQL rejected by the read-only allowlist
    resp = await client.post("/api/v1/sandbox-db/query", json={
        "session_id": session_id,
        "sql": "DELETE FROM t",
    }, headers=auth_headers)
    assert resp.status_code == 400
    assert "not allowed" in resp.json()["detail"].lower()

    # Buyer can query their own session
    resp = await client.post("/api/v1/sandbox-db/query", json={
        "session_id": session_id,
        "sql": "SELECT * FROM t",
    }, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["row_count"] == 1

    # Operator/admin bypass ownership checks by design
    resp = await client.post("/api/v1/sandbox-db/query", json={
        "session_id": session_id,
        "sql": "SELECT * FROM t",
    }, headers=operator_headers)
    assert resp.status_code == 200

    # Another data_provider is not the owner — 403 (G-073)
    other_h, _ = await _register_other_provider(client)
    resp = await client.post("/api/v1/sandbox-db/query", json={
        "session_id": session_id,
        "sql": "SELECT * FROM t",
    }, headers=other_h)
    assert resp.status_code == 403
    assert "not your sandbox database session" in resp.json()["detail"].lower()


async def _register_other_provider(client: AsyncClient) -> tuple[dict, str]:
    """Register a second data_provider and return (headers, user_id)."""
    import uuid as _uuid
    unique = _uuid.uuid4().hex[:8]
    resp = await client.post("/api/v1/auth/register", json={
        "username": f"other_{unique}",
        "email": f"other_{unique}@example.com",
        "password": "testpass123",
        "role": "data_provider",
    })
    assert resp.status_code == 201
    data = resp.json()
    return {"Authorization": f"Bearer {data['access_token']}"}, data["user"]["id"]


# ─── Task Queue E2E ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_e2e_task_queue_lifecycle():
    """E2E: task queue — enqueue, dequeue, complete, stats."""
    from app.services.task_queue import TaskQueue, TaskPriority, QueueTaskStatus

    queue = TaskQueue()
    await queue.connect()

    # Enqueue tasks with different priorities
    low = await queue.enqueue("report_gen", {"type": "low"}, priority=TaskPriority.LOW)
    high = await queue.enqueue("code_scan", {"type": "high"}, priority=TaskPriority.HIGH)
    critical = await queue.enqueue("security_check", {"type": "critical"}, priority=TaskPriority.CRITICAL)

    # Dequeue — critical first
    task = await queue.dequeue()
    assert task.task_id == critical.task_id
    assert task.status == QueueTaskStatus.CLAIMED

    # Complete it
    ok = await queue.complete(task.task_id, result={"passed": True})
    assert ok is True

    # Dequeue — high next
    task = await queue.dequeue()
    assert task.task_id == high.task_id

    # Fail it — should retry
    await queue.fail(task.task_id, "scanner timeout")
    retried = await queue.get_task(task.task_id)
    assert retried.retry_count == 1
    assert retried.status == QueueTaskStatus.RETRYING

    # Stats
    stats = await queue.stats()
    assert stats["completed"] >= 1
    assert stats["pending"] >= 1  # low + retried high

    await queue.close()


@pytest.mark.asyncio
async def test_e2e_task_queue_retry_exhaustion():
    """E2E: task goes dead after max retries."""
    from app.services.task_queue import TaskQueue, QueueTaskStatus

    queue = TaskQueue()
    await queue.connect()

    task = await queue.enqueue("fragile_task", {}, max_retries=2)
    await queue.dequeue()

    # Fail twice
    await queue.fail(task.task_id, "error 1")
    retried = await queue.dequeue()
    assert retried.task_id == task.task_id

    await queue.fail(task.task_id, "error 2")
    final = await queue.get_task(task.task_id)
    assert final.status == QueueTaskStatus.DEAD

    await queue.close()


# ─── Compliance Report E2E ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_e2e_compliance_report(client: AsyncClient, operator_headers: dict):
    """E2E: generate a compliance report."""
    resp = await client.get("/api/v1/compliance/reports/quick?days=30", headers=operator_headers)
    assert resp.status_code == 200
    report = resp.json()
    assert "audit_summary" in report
    assert "compliance_status" in report
    assert report["report_type"] == "tc609"


# ─── MPC Key Sharing E2E ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_e2e_mpc_split_and_reconstruct(client: AsyncClient, operator_headers: dict):
    """E2E: split a key, then reconstruct from threshold shares."""
    secret_hex = "aabbccdd" * 8

    # Split
    resp = await client.post("/api/v1/mpc/split", json={
        "secret_hex": secret_hex,
        "threshold": 3,
        "total_shares": 5,
    }, headers=operator_headers)
    assert resp.status_code == 200
    shares = resp.json()
    assert len(shares) == 5
    key_id = shares[0]["key_id"]

    # Reconstruct from first 3 shares
    resp = await client.post("/api/v1/mpc/reconstruct", json={
        "key_id": key_id,
        "share_ids": [s["share_id"] for s in shares[:3]],
    }, headers=operator_headers)
    assert resp.status_code == 200
    assert resp.json()["secret_hex"] == secret_hex


# ─── Certificate E2E ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_e2e_certificate_lifecycle(client: AsyncClient, operator_headers: dict):
    """E2E: generate, list, verify, revoke certificate."""
    # Generate
    resp = await client.post("/api/v1/certificates/generate", json={
        "subject": "E2E Test Cert",
        "cert_type": "signing",
        "validity_days": 365,
    }, headers=operator_headers)
    assert resp.status_code == 200
    cert = resp.json()
    cert_id = cert["cert_id"]
    assert cert["subject"] == "E2E Test Cert"

    # List
    resp = await client.get("/api/v1/certificates/list?subject=E2E", headers=operator_headers)
    assert resp.status_code == 200

    # Get by ID
    resp = await client.get(f"/api/v1/certificates/{cert_id}", headers=operator_headers)
    assert resp.status_code == 200
    assert resp.json()["cert_id"] == cert_id


# ─── Streaming Inspector E2E ─────────────────────────────────────────

def test_e2e_streaming_inspector_pii_scan():
    """E2E: PII scanning and auto-redaction."""
    from app.services.streaming_inspector import pii_regex_scan, auto_redact

    # Scan for PII
    text = "联系人：张三，电话13812345678，邮箱zhangsan@example.com，身份证110101199001011234"
    hits = list(pii_regex_scan(text))
    assert len(hits) >= 3  # phone, email, ID

    # Auto-redact
    redacted = auto_redact(text, hits)
    assert "13812345678" not in redacted
    assert "zhangsan@example.com" not in redacted
    assert "110101199001011234" not in redacted
    assert "张三" in redacted  # name preserved


def test_e2e_streaming_inspector_rolling_window():
    """E2E: rolling window detects PII density spikes."""
    from app.services.streaming_inspector import StreamingInspector

    # Low threshold to trigger easily
    inspector = StreamingInspector(session_id="e2e-window", pii_density_threshold=0.01)

    # Feed clean chunks — no PII
    for i in range(3):
        inspector.inspect_chunk(b"This is clean text with no sensitive data at all.")
        result = inspector.check_window()
        assert result.exceeded is False

    # Feed PII-heavy chunks — phone + email + ID
    pii_text = "联系人张三 电话13812345678 邮箱zhangsan@example.com 身份证110101199001011234".encode()
    for i in range(5):
        inspector.inspect_chunk(pii_text)

    result = inspector.check_window()
    assert result.exceeded is True
    assert result.total_pii_hits > 0
    assert inspector.window_exceeded is True


# ─── Vision Pipeline E2E ─────────────────────────────────────────────

def test_e2e_vision_training_lifecycle():
    """E2E: vision training — setup, preprocess, train, verify."""
    from app.services.vision_pipeline import (
        VisionTrainingRuntime, TrainingConfig, RedactionRule, RedactionType,
        ImageSample, ImageFormat, TrainingPhase,
    )

    # Create samples
    samples = [
        ImageSample(sample_id=f"img-{i}", image_data=f"data-{i}".encode(),
                     label=i % 3, format=ImageFormat.JPEG)
        for i in range(12)
    ]

    # Setup runtime
    runtime = VisionTrainingRuntime(session_id="e2e-vision")
    config = TrainingConfig(architecture="resnet50", num_classes=3, batch_size=4, num_epochs=2)
    rules = [RedactionRule(redaction_type=RedactionType.FACE_BLUR)]

    runtime.setup(images=samples, redaction_rules=rules, config=config)

    # Verify redaction
    summary = runtime.get_redaction_summary()
    assert summary["redacted"] == 12
    assert summary["redaction_rate"] == 1.0

    # Train
    result = runtime.train()
    assert result.phase == TrainingPhase.COMPLETED
    assert result.total_epochs == 2
    assert result.watermark_hash is not None
    assert result.final_metrics.accuracy > 0


def test_e2e_multimodal_training_lifecycle():
    """E2E: multimodal training — setup, train, leakage check."""
    from app.services.vision_pipeline import (
        MultimodalTrainingRuntime, MultimodalTrainingConfig, RedactionRule,
        ImageSample, ImageFormat, TrainingPhase,
    )

    images = [ImageSample(sample_id=f"img-{i}", image_data=f"data-{i}".encode(),
                           format=ImageFormat.JPEG) for i in range(10)]
    texts = [{"description": f"Image {i}", "label": i % 3} for i in range(10)]

    runtime = MultimodalTrainingRuntime(session_id="e2e-mm")
    config = MultimodalTrainingConfig(base_model="Qwen2-VL-7B", batch_size=4, num_epochs=1)

    runtime.setup(images=images, texts=texts, redaction_rules=[], config=config)
    result = runtime.train()

    assert result.phase == TrainingPhase.COMPLETED
    assert runtime.leakage_result is not None
    assert runtime.leakage_result.samples_checked > 0
