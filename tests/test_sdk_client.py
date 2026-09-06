"""SDK client tests (Round 40) — httpx MockTransport, no server needed."""
import json
import sys
from pathlib import Path

import httpx
import pytest

# Make the sdk package importable without installation.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sdk"))

from cds_sdk import CDSClient, CDSError  # noqa: E402


def _client(handler) -> CDSClient:
    return CDSClient(
        "http://test", token="tok", transport=httpx.MockTransport(handler)
    )


def test_error_mapping_raises_cdserror():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "Sandbox session not found"})

    with _client(handler) as c:
        with pytest.raises(CDSError) as exc:
            c.get_session("abc")
    assert exc.value.status_code == 404
    assert "not found" in str(exc.value.detail)


def test_create_session_body_includes_template():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json={"id": "s-1", "status": "provisioning"})

    with _client(handler) as c:
        s = c.create_session(
            "dp-1", contract_id="c-1", template="python-analysis", timeout_seconds=600
        )
    assert s["id"] == "s-1"
    assert seen["path"] == "/api/v1/sandbox-sessions"
    assert seen["body"]["template"] == "python-analysis"
    assert seen["body"]["contract_id"] == "c-1"
    assert seen["body"]["timeout_seconds"] == 600


def test_exec_command_sends_json_body():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"exit_code": 0, "output": "ok"})

    with _client(handler) as c:
        r = c.exec_command("s-1", "ls -la", timeout_seconds=30)
    assert r["output"] == "ok"
    assert seen["path"] == "/api/v1/sandbox-sessions/s-1/exec"
    assert seen["body"] == {"command": "ls -la", "timeout_seconds": 30}


def test_upload_download_file_roundtrip():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        if request.method == "POST":
            ct = request.headers["content-type"]
            assert ct.startswith("multipart/form-data")
            assert b"data.csv" in request.content
            return httpx.Response(200, json={"filename": "data.csv", "size": 5})
        return httpx.Response(
            200, content=b"col\n1\n",
            headers={"X-CDS-Output-Review": "passed; findings=0"},
        )

    with _client(handler) as c:
        up = c.upload_file("s-1", "data.csv", b"col\n1\n")
        assert up["filename"] == "data.csv"
        data = c.download_file("s-1", "data.csv")
    assert data == b"col\n1\n"
    assert seen["path"].endswith("/files/data.csv")


def test_download_file_blocked_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"detail": {"error": "Output review blocked the file download"}})

    with _client(handler) as c:
        with pytest.raises(CDSError) as exc:
            c.download_file("s-1", "pii.txt")
    assert exc.value.status_code == 409


def test_wait_for_status_polls_until_target():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        status = "provisioning" if calls["n"] < 3 else "running"
        return httpx.Response(200, json={"id": "s-1", "status": status})

    with _client(handler) as c:
        s = c.wait_for_status("s-1", target=("running",), timeout=5, poll_interval=0.01)
    assert s["status"] == "running"
    assert calls["n"] == 3


def test_wait_for_status_times_out():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "s-1", "status": "provisioning"})

    with _client(handler) as c:
        with pytest.raises(TimeoutError):
            c.wait_for_status("s-1", target=("running",), timeout=0.2, poll_interval=0.05)


def test_logs_and_usage_params():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, dict(request.url.params)))
        if request.url.path.endswith("/logs"):
            return httpx.Response(200, json={"logs": [], "count": 0, "latest_created_at": None})
        return httpx.Response(200, json={"workspace": {"files": 0, "bytes": 0}})

    with _client(handler) as c:
        c.logs("s-1", limit=50, since="2026-09-06T00:00:00+00:00", action="sandbox.exec")
        c.usage("s-1")
    assert seen[0][0] == "/api/v1/sandbox-sessions/s-1/logs"
    assert seen[0][1]["action"] == "sandbox.exec"
    assert seen[0][1]["limit"] == "50"
    assert seen[1][0] == "/api/v1/sandbox-sessions/s-1/usage"


def test_snapshot_and_lifecycle_paths():
    paths = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append((request.method, request.url.path))
        if request.url.path.endswith("/snapshots"):
            return httpx.Response(200, json={"snapshots": []})
        if request.url.path.endswith("/session-templates"):
            return httpx.Response(200, json={"templates": []})
        return httpx.Response(200, json={"ok": True})

    with _client(handler) as c:
        c.list_snapshots("s-1")
        c.create_snapshot("s-1")
        c.rollback_snapshot("s-1", "snap-1")
        c.delete_snapshot("s-1", "snap-1")
        c.pause("s-1")
        c.resume("s-1")
        c.refresh("s-1", 60)
        c.terminate_session("s-1")
        c.list_templates()
    assert ("POST", "/api/v1/sandbox-sessions/s-1/snapshots/snap-1/rollback") in paths
    assert ("DELETE", "/api/v1/sandbox-sessions/s-1/snapshots/snap-1") in paths
    assert ("POST", "/api/v1/sandbox-sessions/s-1/pause") in paths
    assert ("POST", "/api/v1/sandbox-sessions/s-1/refreshes") in paths
    assert ("GET", "/api/v1/sandbox-sessions/session-templates") in paths
