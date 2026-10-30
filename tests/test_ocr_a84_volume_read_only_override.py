"""A84 (ocr CR 2026-10-18): 卷级 `read_only` 可被客户端在挂载时推翻。

修复前 `shared_volumes.attach` 里 `effective_ro = volume.read_only if read_only is None
else read_only` —— 调用方（`POST /api/v1/shared-volumes/{id}/attach` 的 `body.read_only`）
传 `false` 就推翻建卷时声明的 `read_only=True`，且两个渲染面同时变可写：
`resolve_binds` 交给 docker/bwrap 的 bind 三元组第三位、`resolve_mounts` 给 k8s pod 的
`readOnly`。服务层没有任何「只读卷不得读写挂载」的收敛点。

修法只允许「更严」方向：卷声明只读 ⇒ 永远只读；可写卷仍可挂成只读（也仍可挂成可写）。
全部断言是服务层/路由层的盘上事实，不依赖硬件或运行面。
"""
import uuid
from pathlib import Path

import pytest
import pytest_asyncio

from app.core.config import get_settings
from app.models.sandbox_session import SandboxSession, SessionStatus
from app.models.shared_volume import SharedVolume
from app.models.user import User
from app.services import shared_volumes

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _volume_root(monkeypatch, tmp_path):
    settings = get_settings()
    monkeypatch.setattr(settings, "SHARED_VOLUME_ROOT", str(tmp_path / "vols"))
    return settings


@pytest_asyncio.fixture
async def owner(db_session):
    unique = uuid.uuid4().hex[:8]
    user = User(
        username=f"a84owner_{unique}",
        email=f"a84owner_{unique}@example.com",
        hashed_password="x",
        role="data_provider",
    )
    db_session.add(user)
    await db_session.flush()
    from app.services.auth_service import create_access_token

    return user, {"Authorization": f"Bearer {create_access_token(user.id, user.role)}"}


@pytest_asyncio.fixture
async def session(db_session, owner):
    user, _ = owner
    row = SandboxSession(
        id=uuid.uuid4(),
        user_id=user.id,
        data_product_id=uuid.uuid4(),
        sandbox_level="L0",
        sandbox_mode="structured_query",
        status=SessionStatus.RUNNING.value,
        container_id=f"l0-{uuid.uuid4().hex[:12]}",
    )
    db_session.add(row)
    await db_session.flush()
    return row


async def _declared_ro_volume(db_session, user, name: str) -> SharedVolume:
    volume = await shared_volumes.create_volume(
        db_session, owner_id=user.id, name=name, read_only=True,
    )
    assert volume.read_only is True, "前提：建卷时声明只读"
    return volume


async def test_declared_read_only_survives_attach_read_only_false(db_session, owner, session):
    user, _ = owner
    volume = await _declared_ro_volume(db_session, user, "a84-svc")

    attachment = await shared_volumes.attach(
        db_session, volume_id=volume.id, session_id=session.id,
        owner_id=user.id, read_only=False,
    )
    assert attachment.read_only is True, (
        "A84: 显式 read_only=False 推翻了建卷时声明的 read_only=True"
    )


async def test_declared_read_only_survives_in_both_render_surfaces(db_session, owner, session):
    user, _ = owner
    volume = await _declared_ro_volume(db_session, user, "a84-render")
    await shared_volumes.attach(
        db_session, volume_id=volume.id, session_id=session.id,
        owner_id=user.id, read_only=False,
    )

    binds = await shared_volumes.resolve_binds(db_session, session.id)
    assert len(binds) == 1
    host_dir, guest_path, bind_ro = binds[0]
    assert guest_path == "/workspace/shared/a84-render"
    assert host_dir == Path(get_settings().SHARED_VOLUME_ROOT) / str(volume.id)
    assert bind_ro is True, f"A84: docker/bwrap bind 变成可写 → {binds[0]!r}"

    mounts = await shared_volumes.resolve_mounts(db_session, session.id)
    assert len(mounts) == 1
    assert mounts[0]["name"] == f"cds-shared-{volume.id}"
    assert mounts[0]["mountPath"] == "/workspace/shared/a84-render"
    assert mounts[0]["readOnly"] is True, f"A84: k8s mount 变成可写 → {mounts[0]!r}"


async def test_api_attach_cannot_downgrade_declared_read_only(client, db_session, owner, session):
    _, headers = owner
    user = owner[0]
    created = await client.post(
        "/api/v1/shared-volumes",
        json={"name": "a84-api", "size_limit_mb": 64, "read_only": True},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    volume_id = created.json()["id"]
    assert created.json()["read_only"] is True

    resp = await client.post(
        f"/api/v1/shared-volumes/{volume_id}/attach",
        json={"session_id": str(session.id), "read_only": False},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["read_only"] is True, (
        "A84: attach 响应把声明只读的卷报成可写挂载"
    )

    binds = await shared_volumes.resolve_binds(db_session, session.id)
    assert [b[2] for b in binds] == [True]
    mounts = await shared_volumes.resolve_mounts(db_session, session.id)
    assert [m["readOnly"] for m in mounts] == [True]


async def test_only_the_stricter_direction_is_allowed(db_session, owner, session):
    user, _ = owner
    writable = await shared_volumes.create_volume(
        db_session, owner_id=user.id, name="a84-writable", read_only=False,
    )

    tightened = await shared_volumes.attach(
        db_session, volume_id=writable.id, session_id=session.id,
        owner_id=user.id, read_only=True,
    )
    assert tightened.read_only is True, "客户端可以更严（挂成只读）"

    plain = await shared_volumes.create_volume(
        db_session, owner_id=user.id, name="a84-default", read_only=False,
    )
    defaulted = await shared_volumes.attach(
        db_session, volume_id=plain.id, session_id=session.id, owner_id=user.id,
    )
    assert defaulted.read_only is False, "可写卷 + 不传参数：仍是可写（不无端收紧）"
