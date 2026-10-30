"""A90 — terminate() deleted the session volume by the cubelet sandbox id.

The data volume is created as ``cds-data-<session_id>`` (see
``prepare_session_volume`` / ``provision``), while ``terminate()`` received the
container id ``l2c-<node_id>-<sandbox_id>`` and deleted
``cds-data-<sandbox_id>``. Those are different namespaces, so every session
termination left a permanent encrypted volume (and its KMS DEK row) behind on
cube-master with nothing — no sweeper, no audit trail — pointing at it.

``terminate(container_id, session_id=None)`` now deletes by session id: the
caller's value when given, otherwise the id resolved from the DB by container_id.
If neither is available it deletes nothing and raises a critical alert naming the
orphan, instead of "succeeding" against a name that was never created.
"""
import asyncio
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core import database as db_module
from app.core.config import get_settings
from app.core.errors import CDSError
from app.models.sandbox_session import SandboxSession
from app.models.user import User
from app.services.cube_runtime_adapter import CubeRuntimeAdapter

NODE_ID = "node-local-1"
# cubelet sandbox id: the trailing 32 lowercase hex the container-id regex right-cuts
SANDBOX_ID = "a1b2c3d4" + "0" * 24
OTHER_CONTAINER = "l2c-other-" + "f" * 32


def _container_id() -> str:
    return f"l2c-{NODE_ID}-{SANDBOX_ID}"


class _DestroyClient:
    def __init__(self):
        self.destroyed: list[str] = []

    def destroy(self, request):
        self.destroyed.append(request.sandboxID)

        class _Resp:
            ret_code = 0

        return _Resp()


def _adapter(monkeypatch, deleted: list[str], alerts: list[tuple[str, str]], client=None):
    client = client or _DestroyClient()
    adapter = CubeRuntimeAdapter(client_factory=lambda node: client)
    monkeypatch.setattr(
        adapter, "_resolve_node_sync", lambda node_id: {"node_id": node_id, "endpoint": "x"}
    )
    monkeypatch.setattr(adapter, "_client_for", lambda node: client)
    monkeypatch.setattr(adapter, "_master_delete_volume", lambda name: deleted.append(name))
    monkeypatch.setattr(
        adapter,
        "_fire_critical_alert",
        lambda alert_type, message: alerts.append((alert_type, message)),
    )
    monkeypatch.setattr(get_settings(), "CUBE_MASTER_ADDR", "http://127.0.0.1:18089")
    return adapter, client


def _seed_sessions(monkeypatch, tmp_path, container_ids: list[str]) -> dict[str, uuid.UUID]:
    """Point app.core.database at a file-backed SQLite holding those sessions."""
    from app.core.database import Base

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/a90.db", poolclass=StaticPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    ids = {cid: uuid.uuid4() for cid in container_ids}

    async def _create():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as db:
            user = User(
                id=uuid.uuid4(),
                username="a90",
                email="a90@example.test",
                hashed_password="x",
                role="buyer",
            )
            db.add(user)
            for cid, sid in ids.items():
                db.add(
                    SandboxSession(
                        id=sid,
                        user_id=user.id,
                        data_product_id=uuid.uuid4(),
                        sandbox_level="L2C",
                        container_id=cid,
                    )
                )
            await db.commit()
        async with factory() as db:
            stored = (await db.execute(select(SandboxSession.container_id))).scalars().all()
        return sorted(stored)

    assert asyncio.run(_create()) == sorted(container_ids)
    monkeypatch.setattr(db_module, "sync_bridge_session", factory)
    monkeypatch.setattr(db_module.sync_db_bridge, "run", lambda coro, *a, **k: asyncio.run(coro))
    return ids


def test_terminate_deletes_the_volume_of_the_session_that_owns_the_container(
    monkeypatch, tmp_path
):
    deleted: list[str] = []
    alerts: list[tuple[str, str]] = []
    container_id = _container_id()
    session_id = _seed_sessions(monkeypatch, tmp_path, [container_id, OTHER_CONTAINER])[container_id]
    adapter, client = _adapter(monkeypatch, deleted, alerts)

    assert adapter.terminate(container_id) is True

    assert deleted == [f"cds-data-{session_id}"], (
        "session volume must be reclaimed by its session id"
    )
    assert f"cds-data-{SANDBOX_ID}" not in deleted, (
        "cds-data-<cubelet sandbox id> was never created — deleting it orphans the real volume"
    )
    assert client.destroyed == [SANDBOX_ID], "cubelet destroy must still use the sandbox id"
    assert alerts == []


def test_terminate_uses_explicit_session_id_argument(monkeypatch):
    deleted: list[str] = []
    alerts: list[tuple[str, str]] = []
    adapter, client = _adapter(monkeypatch, deleted, alerts)

    def _no_lookup():
        raise AssertionError("an explicit session_id must not need a DB lookup")

    monkeypatch.setattr(adapter, "_session_id_for_container", _no_lookup)

    session_id = str(uuid.uuid4())
    assert adapter.terminate(_container_id(), session_id=session_id) is True

    assert deleted == [f"cds-data-{session_id}"]
    assert client.destroyed == [SANDBOX_ID]


def test_unresolvable_session_id_orphan_is_disclosed_not_guessed(monkeypatch, tmp_path):
    deleted: list[str] = []
    alerts: list[tuple[str, str]] = []
    _seed_sessions(monkeypatch, tmp_path, [OTHER_CONTAINER])
    adapter, _client = _adapter(monkeypatch, deleted, alerts)

    assert adapter.terminate(_container_id()) is True

    assert deleted == [], (
        "deleting cds-data-<sandbox_id> reclaims nothing; it must not be presented as cleanup"
    )
    assert len(alerts) == 1, "an unreclaimed session volume must raise a critical alert"
    assert _container_id() in alerts[0][1]


def test_destroy_failure_does_not_touch_volumes(monkeypatch):
    deleted: list[str] = []
    alerts: list[tuple[str, str]] = []
    client = _DestroyClient()

    def _conflict(request):
        raise CDSError("sandbox in use", code="SESSION_STATE_CONFLICT", http_status=409)

    monkeypatch.setattr(client, "destroy", _conflict)
    adapter, _client = _adapter(monkeypatch, deleted, alerts, client=client)

    assert adapter.terminate(_container_id(), session_id=str(uuid.uuid4())) is False
    assert deleted == []


def test_session_lookup_resolves_only_the_matching_container(monkeypatch, tmp_path):
    container_id = _container_id()
    ids = _seed_sessions(monkeypatch, tmp_path, [container_id, OTHER_CONTAINER])
    adapter = CubeRuntimeAdapter(client_factory=lambda node: _DestroyClient())

    assert adapter._session_id_for_container(container_id) == str(ids[container_id])
    assert adapter._session_id_for_container(OTHER_CONTAINER) == str(ids[OTHER_CONTAINER])
    assert adapter._session_id_for_container("l2c-none-" + "e" * 32) is None
