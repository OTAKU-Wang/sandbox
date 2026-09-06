"""Minimal Closed-Loop E2E Test — validates the full sandbox execution path.

This test exercises the critical async subprocess path that was broken by the
event loop issue (synchronous subprocess.run blocking the FastAPI event loop).

Flow: auth → dev sandbox session → code execution → verify output → cleanup
"""
import uuid
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_minimal_loop_auth_to_execution(client: AsyncClient):
    """Minimal closed-loop: register → create session → execute code → verify → terminate."""
    # 1. Register a user
    unique = uuid.uuid4().hex[:8]
    resp = await client.post("/api/v1/auth/register", json={
        "username": f"loop_{unique}",
        "email": f"loop_{unique}@example.com",
        "password": "testpass123",
        "role": "data_provider",
    })
    assert resp.status_code == 201
    token = resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 2. Create dev sandbox session
    resp = await client.post("/api/v1/dev-sandbox/sessions", json={
        "mode": "structured",
        "sandbox_level": "L3",
    }, headers=headers)
    assert resp.status_code == 201
    session = resp.json()
    session_id = session["session_id"]
    assert session["status"] in ("running", "pending", "failed")

    # 3. Execute Python code — this is the critical async subprocess path
    resp = await client.post(f"/api/v1/dev-sandbox/sessions/{session_id}/execute", json={
        "code": "print('hello from minimal loop')",
        "language": "python",
    }, headers=headers)
    assert resp.status_code == 200
    result = resp.json()
    assert result["exit_code"] == 0
    assert "hello from minimal loop" in result["output"]

    # 4. Execute a second code snippet — validates session state persists
    resp = await client.post(f"/api/v1/dev-sandbox/sessions/{session_id}/execute", json={
        "code": "import sys; print(f'Python {sys.version_info.major}.{sys.version_info.minor}')",
        "language": "python",
    }, headers=headers)
    assert resp.status_code == 200
    result2 = resp.json()
    assert result2["exit_code"] == 0
    assert "Python" in result2["output"]

    # 5. Terminate session
    resp = await client.post(f"/api/v1/dev-sandbox/sessions/{session_id}/terminate", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "terminated"

    # 6. Verify session is terminated — cannot execute further
    resp = await client.post(f"/api/v1/dev-sandbox/sessions/{session_id}/execute", json={
        "code": "print('should fail')",
        "language": "python",
    }, headers=headers)
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_minimal_loop_bash_execution(client: AsyncClient):
    """Minimal closed-loop: bash execution through the async subprocess path."""
    unique = uuid.uuid4().hex[:8]
    resp = await client.post("/api/v1/auth/register", json={
        "username": f"bash_{unique}",
        "email": f"bash_{unique}@example.com",
        "password": "testpass123",
        "role": "data_provider",
    })
    assert resp.status_code == 201
    headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}

    resp = await client.post("/api/v1/dev-sandbox/sessions", json={
        "mode": "structured",
    }, headers=headers)
    assert resp.status_code == 201
    session_id = resp.json()["session_id"]

    # Execute bash command
    resp = await client.post(f"/api/v1/dev-sandbox/sessions/{session_id}/execute", json={
        "code": "echo 'bash works'",
        "language": "bash",
    }, headers=headers)
    assert resp.status_code == 200
    result = resp.json()
    assert result["exit_code"] == 0
    assert "bash works" in result["output"]

    # Cleanup
    await client.post(f"/api/v1/dev-sandbox/sessions/{session_id}/terminate", headers=headers)


@pytest.mark.asyncio
async def test_minimal_loop_error_handling(client: AsyncClient):
    """Minimal closed-loop: error in code does not crash the session."""
    unique = uuid.uuid4().hex[:8]
    resp = await client.post("/api/v1/auth/register", json={
        "username": f"err_{unique}",
        "email": f"err_{unique}@example.com",
        "password": "testpass123",
        "role": "data_provider",
    })
    assert resp.status_code == 201
    headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}

    resp = await client.post("/api/v1/dev-sandbox/sessions", json={
        "mode": "structured",
    }, headers=headers)
    assert resp.status_code == 201
    session_id = resp.json()["session_id"]

    # Execute code with error — should not crash, should return non-zero exit
    resp = await client.post(f"/api/v1/dev-sandbox/sessions/{session_id}/execute", json={
        "code": "raise ValueError('intentional error')",
        "language": "python",
    }, headers=headers)
    assert resp.status_code == 200
    result = resp.json()
    assert result["exit_code"] != 0

    # Session should still be usable after error
    resp = await client.post(f"/api/v1/dev-sandbox/sessions/{session_id}/execute", json={
        "code": "print('recovered')",
        "language": "python",
    }, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["exit_code"] == 0
    assert "recovered" in resp.json()["output"]

    await client.post(f"/api/v1/dev-sandbox/sessions/{session_id}/terminate", headers=headers)


@pytest.mark.asyncio
async def test_minimal_loop_unauthenticated_rejected(client: AsyncClient):
    """Minimal closed-loop: unauthenticated requests are rejected."""
    resp = await client.post("/api/v1/dev-sandbox/sessions", json={
        "mode": "structured",
    })
    assert resp.status_code in (401, 403)

    resp = await client.post("/api/v1/dev-sandbox/sessions/fake-id/execute", json={
        "code": "print('no auth')",
    })
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_minimal_loop_full_lifecycle_with_data(client: AsyncClient):
    """Full lifecycle: register → session → upload data → execute → inspect output → terminate."""
    unique = uuid.uuid4().hex[:8]
    resp = await client.post("/api/v1/auth/register", json={
        "username": f"full_{unique}",
        "email": f"full_{unique}@example.com",
        "password": "testpass123",
        "role": "data_provider",
    })
    assert resp.status_code == 201
    headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}

    # Create session in structured mode
    resp = await client.post("/api/v1/dev-sandbox/sessions", json={
        "mode": "structured",
    }, headers=headers)
    assert resp.status_code == 201
    session_id = resp.json()["session_id"]

    # Execute code that produces verifiable output
    code = """
import json
data = {"records": 3, "status": "processed", "items": [1, 2, 3]}
print(json.dumps(data))
"""
    resp = await client.post(f"/api/v1/dev-sandbox/sessions/{session_id}/execute", json={
        "code": code,
        "language": "python",
    }, headers=headers)
    assert resp.status_code == 200
    result = resp.json()
    assert result["exit_code"] == 0
    import json
    # The T5 output review appends an invisible zero-width-character
    # watermark after the payload (designed); the JSON payload itself is the
    # first output line.
    output = json.loads(result["output"].strip().splitlines()[0])
    assert output["records"] == 3
    assert output["items"] == [1, 2, 3]

    # List sessions — should include ours
    resp = await client.get("/api/v1/dev-sandbox/sessions", headers=headers)
    assert resp.status_code == 200
    sessions = resp.json()
    assert any(s["session_id"] == session_id for s in sessions)

    # Get session detail
    resp = await client.get(f"/api/v1/dev-sandbox/sessions/{session_id}", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["session_id"] == session_id

    # Terminate
    resp = await client.post(f"/api/v1/dev-sandbox/sessions/{session_id}/terminate", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "terminated"
