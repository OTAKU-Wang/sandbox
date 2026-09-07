"""W15: shared volumes — lifecycle, attach guardrails, sandbox bind mounting."""
import uuid

import pytest
import pytest_asyncio

from app.core.config import get_settings
from app.models.sandbox_session import SandboxSession, SessionStatus
from app.models.shared_volume import SharedVolume, SharedVolumeAttachment
from app.services import shared_volumes
from app.services.auth_service import create_access_token
from app.models.user import User
from sqlalchemy import select


@pytest.fixture(autouse=True)
def _volume_root(monkeypatch, tmp_path):
    settings = get_settings()
    monkeypatch.setattr(settings, "SHARED_VOLUME_ROOT", str(tmp_path / "vols"))
    return settings


@pytest_asyncio.fixture
async def owner_headers(db_session):
    unique = uuid.uuid4().hex[:8]
    user = User(
        username=f"volowner_{unique}",
        email=f"volowner_{unique}@example.com",
        hashed_password="x",
        role="data_provider",
    )
    db_session.add(user)
    await db_session.flush()
    return {"Authorization": f"Bearer {create_access_token(user.id, user.role)}"}, user


@pytest_asyncio.fixture
async def running_session(db_session, owner_headers):
    _, user = owner_headers
    session = SandboxSession(
        id=uuid.uuid4(),
        user_id=user.id,
        data_product_id=uuid.uuid4(),
        sandbox_level="L0",
        sandbox_mode="structured_query",
        status=SessionStatus.RUNNING.value,
        container_id=f"l0-{uuid.uuid4().hex[:12]}",
    )
    db_session.add(session)
    await db_session.flush()
    return session


@pytest.mark.asyncio
async def test_volume_crud_and_dir_backing(client, db_session, owner_headers):
    headers, _ = owner_headers
    resp = await client.post(
        "/api/v1/shared-volumes",
        json={"name": "datasets", "size_limit_mb": 512, "read_only": False},
        headers=headers,
    )
    assert resp.status_code == 201
    volume_id = resp.json()["id"]

    from pathlib import Path

    assert (Path(get_settings().SHARED_VOLUME_ROOT) / volume_id).is_dir()

    listing = await client.get("/api/v1/shared-volumes", headers=headers)
    assert listing.json()["total"] == 1
    assert listing.json()["volumes"][0]["name"] == "datasets"

    deleted = await client.delete(f"/api/v1/shared-volumes/{volume_id}", headers=headers)
    assert deleted.json()["deleted"] is True
    assert not (Path(get_settings().SHARED_VOLUME_ROOT) / volume_id).exists()


@pytest.mark.asyncio
async def test_duplicate_name_and_bad_name_rejected(client, db_session, owner_headers):
    headers, user = owner_headers
    first = await client.post("/api/v1/shared-volumes", json={"name": "dup"}, headers=headers)
    assert first.status_code == 201

    dup = await client.post("/api/v1/shared-volumes", json={"name": "dup"}, headers=headers)
    assert dup.status_code == 409

    bad = await client.post("/api/v1/shared-volumes", json={"name": "../escape"}, headers=headers)
    assert bad.status_code in (400, 422)


@pytest.mark.asyncio
async def test_attach_requires_same_owner_and_session(client, db_session, owner_headers, running_session):
    headers, user = owner_headers
    volume = await shared_volumes.create_volume(db_session, owner_id=user.id, name="attach-vol")
    resp = await client.post(
        f"/api/v1/shared-volumes/{volume.id}/attach",
        json={"session_id": str(running_session.id)},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["read_only"] is False

    # another user's session cannot attach
    other_user = User(
        username=f"other_{uuid.uuid4().hex[:8]}",
        email=f"other_{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="x",
        role="data_provider",
    )
    db_session.add(other_user)
    await db_session.flush()
    other_session = SandboxSession(
        id=uuid.uuid4(), user_id=other_user.id, data_product_id=uuid.uuid4(),
        sandbox_level="L0", sandbox_mode="structured_query",
        status=SessionStatus.RUNNING.value, container_id=f"l0-{uuid.uuid4().hex[:12]}",
    )
    db_session.add(other_session)
    await db_session.flush()

    forbidden = await client.post(
        f"/api/v1/shared-volumes/{volume.id}/attach",
        json={"session_id": str(other_session.id)},
        headers=headers,
    )
    assert forbidden.status_code == 403


@pytest.mark.asyncio
async def test_resolve_binds_maps_guest_paths(db_session, owner_headers, running_session):
    _, user = owner_headers
    volume = await shared_volumes.create_volume(db_session, owner_id=user.id, name="bindme")
    await shared_volumes.attach(
        db_session, volume_id=volume.id, session_id=running_session.id,
        owner_id=user.id, read_only=True,
    )
    binds = await shared_volumes.resolve_binds(db_session, running_session.id)
    assert len(binds) == 1
    host_path, guest_path, read_only = binds[0]
    assert guest_path == "/workspace/shared/bindme"
    assert read_only is True
    assert host_path.is_dir()


@pytest.mark.asyncio
async def test_bwrap_command_includes_volume_bind(db_session, owner_headers, running_session, monkeypatch, tmp_path):
    from app.services.sandbox_runtime import SandboxRuntime, _build_l0_bwrap_args

    ws_dir = tmp_path / "l0-ws"
    for sub in ("tmp", "input", "output", "files"):
        (ws_dir / sub).mkdir(parents=True)
    monkeypatch.setattr(
        "app.services.sandbox_runtime.ProcessAdapter._resolve_workspace",
        lambda self, container_id: ws_dir,
    )

    _, user = owner_headers
    volume = await shared_volumes.create_volume(db_session, owner_id=user.id, name="mounted")
    await shared_volumes.attach(
        db_session, volume_id=volume.id, session_id=running_session.id, owner_id=user.id,
    )
    binds = await shared_volumes.resolve_binds(db_session, running_session.id)

    cmd, _ = _build_l0_bwrap_args(
        workspace=ws_dir, code_file="/workspace/tmp/exec.sh", language="shell",
        extra_binds=binds,
    )
    assert "--ro-bind" in cmd or "--bind" in cmd
    assert "/workspace/shared/mounted" in cmd

    runtime = SandboxRuntime()
    result = await runtime.execute(
        running_session.container_id, "echo hi", "shell", extra_binds=binds,
    )
    assert result.get("exit_code") == 0
