"""Session file store + endpoint tests (Round 39 usability).

Service-level tests run against a plain tmp workspace (no bwrap needed);
endpoint tests monkeypatch SandboxRuntime.get_workspace to a tmp dir.

Service tests import sandbox_security transitively (POSIX ``resource``
module) and are skipped on Windows; they run on Linux/CI.
"""
import sys
import uuid

import pytest
import pytest_asyncio

# session_files imports sandbox_security (POSIX-only 'resource' module) —
# file-store tests run on Linux/CI; Windows skips them entirely.
_win_skip = pytest.mark.skipif(
    sys.platform == "win32",
    reason="sandbox_security imports POSIX-only 'resource'; covered on Linux",
)


# ─── service level ─────────────────────────────────────────────

@_win_skip
def test_write_read_roundtrip_plaintext(tmp_path):
    from app.services import session_files as sf

    ws = tmp_path / "ws"
    ws.mkdir()
    meta = sf.write_file(ws, "hello.txt", b"hello world")
    assert meta["encrypted"] is False
    content, m = sf.read_file(ws, "hello.txt")
    assert content == b"hello world"
    assert m["sha256"] == meta["sha256"]
    names = [e["filename"] for e in sf.list_files(ws)]
    assert names == ["hello.txt"]


@_win_skip
def test_write_read_roundtrip_encrypted(tmp_path):
    from app.services import session_files as sf

    ws = tmp_path / "ws"
    ws.mkdir()
    dek = bytes(range(32))
    meta = sf.write_file(ws, "secret.txt", b"top secret data", dek=dek)
    assert meta["encrypted"] is True

    # At rest the bytes are ciphertext ([nonce12][tag16][ct]) — not plaintext.
    raw = (ws / "files" / "secret.txt").read_bytes()
    assert raw != b"top secret data" and len(raw) > len(b"top secret data")

    content, _ = sf.read_file(ws, "secret.txt", dek=dek)
    assert content == b"top secret data"

    # Without the DEK an encrypted file is not readable.
    with pytest.raises(sf.SessionFileError):
        sf.read_file(ws, "secret.txt", dek=None)


@_win_skip
def test_filename_traversal_rejected(tmp_path):
    from app.services import session_files as sf

    ws = tmp_path / "ws"
    ws.mkdir()
    for bad in ["../evil.txt", "a/b.txt", "..", "", "x\\y.txt", "n\x00l"]:
        with pytest.raises(sf.SessionFileError):
            sf.write_file(ws, bad, b"x")
    with pytest.raises(sf.SessionFileError):
        sf.read_file(ws, "../../etc/passwd")
    assert sf.list_files(ws) == []


@_win_skip
def test_file_size_and_count_limits(tmp_path, monkeypatch):
    from app.core.config import get_settings
    from app.services import session_files as sf

    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setattr(get_settings(), "SESSION_FILE_MAX_BYTES", 8)
    with pytest.raises(sf.SessionFileError, match="too large"):
        sf.write_file(ws, "big.bin", b"x" * 9)

    monkeypatch.setattr(get_settings(), "SESSION_FILE_MAX_BYTES", 1024)
    monkeypatch.setattr(get_settings(), "SESSION_MAX_FILES", 2)
    sf.write_file(ws, "a.txt", b"a")
    sf.write_file(ws, "b.txt", b"b")
    with pytest.raises(sf.SessionFileError, match="count limit"):
        sf.write_file(ws, "c.txt", b"c")


@_win_skip
def test_delete_file(tmp_path):
    from app.services import session_files as sf

    ws = tmp_path / "ws"
    ws.mkdir()
    sf.write_file(ws, "gone.txt", b"bye")
    assert sf.delete_file(ws, "gone.txt") is True
    assert sf.delete_file(ws, "gone.txt") is False
    assert sf.list_files(ws) == []


# ─── endpoint level ────────────────────────────────────────────

@pytest.fixture
def sandbox_workspace(tmp_path):
    ws = tmp_path / "bwrap-it"
    ws.mkdir()
    return ws


@pytest.fixture
def fake_session_id():
    return uuid.uuid4()


@pytest_asyncio.fixture
async def session_row(db_session, fake_session_id):
    """Insert an active, provisioned session owned by a fresh user."""
    from app.models.sandbox_session import SandboxSession, SandboxLevel, SandboxMode
    from app.models.user import User
    from app.services.auth_service import hash_password

    user = User(username=f"own_{uuid.uuid4().hex[:8]}", email=f"{uuid.uuid4().hex[:8]}@e2e.local",
                hashed_password=hash_password("testpass123"), role="data_provider")
    db_session.add(user)
    await db_session.flush()
    from app.models.data_product import DataProduct
    product = DataProduct(name="FileTest", provider_id=user.id, product_type="structured")
    db_session.add(product)
    await db_session.flush()
    session = SandboxSession(
        id=fake_session_id,
        user_id=user.id,
        data_product_id=product.id,
        sandbox_level=SandboxLevel.L3.value,
        sandbox_mode=SandboxMode.STRUCTURED_QUERY.value,
        status="running",
        container_id=f"bwrap-{fake_session_id}",
        timeout_seconds=3600,
    )
    db_session.add(session)
    await db_session.flush()
    return session


@pytest_asyncio.fixture
async def owner_headers(db_session, session_row):
    """Auth headers for the session OWNER (uploads are owner-gated)."""
    from app.services.auth_service import create_access_token
    token = create_access_token(session_row.user_id, "data_provider")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def session_client(client, db_session, session_row, sandbox_workspace, monkeypatch):
    """client with get_workspace patched to the tmp workspace."""
    from app.services.sandbox_runtime import sandbox_runtime

    monkeypatch.setattr(
        sandbox_runtime, "get_workspace",
        lambda container_id, level: sandbox_workspace,
    )
    return client


@pytest.mark.asyncio
@_win_skip
async def test_upload_list_download_roundtrip(session_client, owner_headers, fake_session_id, session_row):
    resp = await session_client.post(
        f"/api/v1/sandbox-sessions/{fake_session_id}/files",
        files={"file": ("data.csv", b"id,name\n1,alice\n", "text/csv")},
        headers=owner_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["filename"] == "data.csv"

    resp = await session_client.get(
        f"/api/v1/sandbox-sessions/{fake_session_id}/files", headers=owner_headers
    )
    assert resp.status_code == 200
    files = resp.json()["files"]
    assert len(files) == 1 and files[0]["filename"] == "data.csv"

    resp = await session_client.get(
        f"/api/v1/sandbox-sessions/{fake_session_id}/files/data.csv", headers=owner_headers
    )
    assert resp.status_code == 200
    assert resp.content == b"id,name\n1,alice\n"
    assert "passed" in resp.headers.get("X-CDS-Output-Review", "")


@pytest.mark.asyncio
@_win_skip
async def test_download_blocked_on_critical_dlp(session_client, owner_headers, fake_session_id):
    pii = "身份证 11010119900307867X，手机 13800138000"
    resp = await session_client.post(
        f"/api/v1/sandbox-sessions/{fake_session_id}/files",
        files={"file": ("pii.txt", pii.encode(), "text/plain")},
        headers=owner_headers,
    )
    assert resp.status_code == 200

    resp = await session_client.get(
        f"/api/v1/sandbox-sessions/{fake_session_id}/files/pii.txt", headers=owner_headers
    )
    assert resp.status_code == 409
    body = resp.json()["detail"]
    assert body["error"].startswith("Output review blocked")
    assert body["findings_count"] >= 1


@pytest.mark.asyncio
@_win_skip
async def test_upload_requires_ownership(session_client, fake_session_id):
    unique = uuid.uuid4().hex[:8]
    resp = await session_client.post("/api/v1/auth/register", json={
        "username": f"other_{unique}", "email": f"other_{unique}@example.com",
        "password": "testpass123", "role": "buyer",
    })
    other = {"Authorization": f"Bearer {resp.json()['access_token']}"}
    resp = await session_client.post(
        f"/api/v1/sandbox-sessions/{fake_session_id}/files",
        files={"file": ("x.txt", b"x", "text/plain")}, headers=other,
    )
    assert resp.status_code == 403
