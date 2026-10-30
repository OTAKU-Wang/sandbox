"""A90b — 知道 session id 的调用方必须显式传 session_id，而不是靠兜底强转。

台账: docs/ocr-review-2026-10-18/ocr-verified-findings.md §A90 的调用方半段
      (`.gate-scratch/evidence/backend-20.md` A90 NOTE 2 — backend-20 明写
       "callers that know the session id … are outside this track's file list")

落点: `app/services/sandbox_runtime.py`
  - `SandboxRuntime.secure_destroy` — 它手里就是 `session_id: uuid.UUID`，却只把
    `str(session_id)` 放进 `resource_id`，审计行的 `session_id` 列留 NULL；
  - `SandboxRuntime.terminate` / `secure_destroy` / `terminate_sessions_by_key` —
    只传 `container_id`，把"这属于哪个会话"留给运行面反查
    (`cube_runtime_adapter.terminate` 的 `_session_id_for_container` DB 兜底)。

`audit_service._coerce_session_id`（A68 刚修，`tests/test_ocr_a68_audit_session_uuid.py`
钉住）是给**不知道** id / 拿到的是 `String(255)` 脏值的调用方留的兜底：它会 warn、把
列写成 NULL、并在 `detail._session_id_unparsed` 里披露。知道 id 的调用方走它 = 每条
审计都白吃一次 warning 并且永远拿不到回链。所以本文件断言的是"显式路径"：
`AuditLog.session_id` 是一个真正的 `uuid.UUID` 且**没有**走强转披露。
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.models.kms import KeyMetadata
from app.models.sandbox_session import SandboxLevel, SandboxSession, SessionStatus
from app.services.sandbox_runtime import RuntimeAdapter, SandboxRuntime


async def _seed_session(db: AsyncSession, *, container_id: str | None,
                        session_key_id: str | None = None) -> SandboxSession:
    session = SandboxSession(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        data_product_id=uuid.uuid4(),
        sandbox_level="L3",
        sandbox_mode="structured_query",
        status=SessionStatus.RUNNING.value,
        container_id=container_id,
        session_key_id=session_key_id,
    )
    db.add(session)
    await db.flush()
    return session


@pytest.mark.asyncio
async def test_secure_destroy_audit_row_links_session_uuid(db_session: AsyncSession):
    """显式路径：sandbox.secure_destroy 的审计行必须以 UUID 回链到会话。"""
    session = await _seed_session(db_session, container_id=None)
    runtime = SandboxRuntime()

    report = await runtime.secure_destroy(session.id, db_session, reason="a90b")
    await db_session.flush()

    assert report.session_id == session.id, report.errors

    row = (
        await db_session.execute(
            select(AuditLog).where(AuditLog.action == "sandbox.secure_destroy")
        )
    ).scalar_one()
    assert row.session_id is not None, (
        "secure_destroy 知道 session_id，却把它留在 AuditLog.session_id 之外 —— "
        "审计事件无法回链到会话（只能靠 resource_id 文本匹配）"
    )
    assert isinstance(row.session_id, uuid.UUID), (
        f"session_id 必须以 UUID 落列，实际 {type(row.session_id).__name__}"
    )
    assert str(row.session_id) == str(session.id)
    detail = row.detail or {}
    assert "_session_id_unparsed" not in detail, (
        "显式路径不该落到 _coerce_session_id 的强转兜底上"
    )


@pytest.mark.asyncio
async def test_secure_destroy_passes_session_id_to_terminate(db_session: AsyncSession, monkeypatch):
    """销毁容器时必须把已知的 session_id 交出去，别让运行面回库反查。"""
    container_id = f"bwrap-{uuid.uuid4()}"
    session = await _seed_session(db_session, container_id=container_id)
    runtime = SandboxRuntime()

    seen: list[tuple[str, object]] = []

    def _fake_terminate(container_id: str, session_id=None) -> bool:
        seen.append((container_id, session_id))
        return True

    monkeypatch.setattr(runtime, "terminate", _fake_terminate)

    await runtime.secure_destroy(session.id, db_session, reason="a90b")

    assert seen and seen[0][0] == container_id
    assert seen[0][1] is not None, "调用方知道 session id，必须显式传参"
    assert str(seen[0][1]) == str(session.id)


@pytest.mark.asyncio
async def test_facade_forwards_session_id_to_adapters_that_accept_it():
    """门面把 session_id 透给支持它的运行面；老签名适配器不受影响。"""
    runtime = SandboxRuntime()

    class _TwoArgAdapter(RuntimeAdapter):
        def __init__(self) -> None:
            self.calls: list[tuple[str, object]] = []

        def provision(self, session_id: str, data_path: str, timeout: int = 3600,
                      user_id: str = "") -> dict[str, object]:
            raise NotImplementedError

        async def execute(self, container_id: str, code: str,
                          language: str = "python") -> dict[str, object]:
            raise NotImplementedError

        def get_status(self, container_id: str) -> str:
            return "stub"

        def terminate(self, container_id: str, session_id: str | None = None) -> bool:
            self.calls.append((container_id, session_id))
            return True

    class _OneArgAdapter(RuntimeAdapter):
        def __init__(self) -> None:
            self.calls: list[str] = []

        def provision(self, session_id: str, data_path: str, timeout: int = 3600,
                      user_id: str = "") -> dict[str, object]:
            raise NotImplementedError

        async def execute(self, container_id: str, code: str,
                          language: str = "python") -> dict[str, object]:
            raise NotImplementedError

        def get_status(self, container_id: str) -> str:
            return "stub"

        def terminate(self, container_id: str) -> bool:
            self.calls.append(container_id)
            return True

    two = _TwoArgAdapter()
    one = _OneArgAdapter()
    runtime._adapters[SandboxLevel.L3.value] = two
    runtime._adapters[SandboxLevel.L0.value] = one

    sid = uuid.uuid4()
    assert runtime.terminate(f"bwrap-{sid}", session_id=str(sid)) is True
    assert two.calls == [(f"bwrap-{sid}", str(sid))], (
        "门面必须把已知的 session_id 透给支持它的运行面"
    )

    assert runtime.terminate("l0-legacy", session_id=str(sid)) is True, (
        "只实现 ABC 单参签名的适配器（含测试桩与旧运行面）必须照旧工作，"
        "门面不得把关键字参数塞给不声明它的适配器"
    )
    assert one.calls == ["l0-legacy"]

    assert runtime.terminate(f"bwrap-{sid}") is True, "不传 session_id 的既有调用点行为必须不变"
    assert len(two.calls) == 2 and two.calls[1] == (f"bwrap-{sid}", None)


@pytest.mark.asyncio
async def test_terminate_sessions_by_key_passes_each_session_id(db_session: AsyncSession,
                                                                monkeypatch):
    """按密钥批量关闭会话时，逐个会话的 id 同样是已知的。"""
    from app.services import kms_service as kms_module

    session = await _seed_session(db_session, container_id=f"bwrap-{uuid.uuid4()}",
                                  session_key_id="a90b-key-1")
    db_session.add(KeyMetadata(
        key_id="a90b-key-1", key_type="data_key", status="active",
    ))
    await db_session.flush()

    monkeypatch.setattr(kms_module.kms_service, "destroy_key", lambda *_a, **_k: True)

    runtime = SandboxRuntime()
    seen: list[tuple[str, object]] = []

    def _fake_terminate(container_id: str, session_id=None) -> bool:
        seen.append((container_id, session_id))
        return True

    monkeypatch.setattr(runtime, "terminate", _fake_terminate)

    closed = await runtime.terminate_sessions_by_key("a90b-key-1", db_session)
    assert closed == 1
    assert seen and seen[0][0] == session.container_id
    assert str(seen[0][1]) == str(session.id), (
        "批量关闭路径每个 session 行都在手上，仍传 NULL 就是让运行面回库猜"
    )
