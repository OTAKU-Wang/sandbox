"""A92 — 注册/登录路径的 bcrypt 不得占用事件循环（rounds=12 ≈ 200-300ms 独占循环线程）

判据不用计时：直接测哈希计算落在哪个线程。同步执行时 `threading.get_ident()`
等于事件循环线程；`asyncio.to_thread` 卸载后必然不同。
"""
import asyncio
import inspect
import threading
import uuid

import pytest

from app.services import auth_service


@pytest.mark.asyncio
async def test_register_user_offloads_bcrypt_from_event_loop(db_session, monkeypatch):
    loop_thread = threading.get_ident()
    seen: dict[str, int] = {}
    real_hashpw = auth_service.bcrypt.hashpw

    def spy(password: bytes, salt: bytes) -> bytes:
        seen["tid"] = threading.get_ident()
        return real_hashpw(password, salt)

    monkeypatch.setattr(auth_service.bcrypt, "hashpw", spy)

    name = f"a92u{uuid.uuid4().hex[:8]}"
    user = await auth_service.register_user(
        db_session, name, f"{name}@example.com", "testpass123"
    )
    await db_session.commit()

    assert seen, "bcrypt.hashpw 根本没被调用——测试没有覆盖到哈希路径"
    assert seen["tid"] != loop_thread, (
        "bcrypt.hashpw 在事件循环线程上同步执行：注册期间该 worker 的所有请求"
        "（含 WebSocket 流）都会停 200-300ms"
    )
    assert user.hashed_password.startswith("$2")
    assert auth_service.verify_password("testpass123", user.hashed_password)


@pytest.mark.asyncio
async def test_authenticate_user_offloads_bcrypt_from_event_loop(db_session, monkeypatch):
    loop_thread = threading.get_ident()
    name = f"a92v{uuid.uuid4().hex[:8]}"
    user = await auth_service.register_user(
        db_session, name, f"{name}@example.com", "testpass123"
    )
    await db_session.commit()

    seen: dict[str, int] = {}
    real_checkpw = auth_service.bcrypt.checkpw

    def spy(password: bytes, hashed: bytes) -> bool:
        seen["tid"] = threading.get_ident()
        return real_checkpw(password, hashed)

    monkeypatch.setattr(auth_service.bcrypt, "checkpw", spy)

    ok = await auth_service.authenticate_user(db_session, name, "testpass123")
    bad = await auth_service.authenticate_user(db_session, name, "wrong-password")

    assert ok is not None and ok.id == user.id
    assert bad is None
    assert seen, "bcrypt.checkpw 根本没被调用——登录路径没走哈希"
    assert seen["tid"] != loop_thread, (
        "bcrypt.checkpw 在事件循环线程上同步执行：登录主路径阻塞整个 worker"
    )


def test_sync_helpers_are_unchanged_for_callers():
    """conftest 与二十来个测试仍以同步方式调用哈希原语，不能改成协程。"""
    assert not inspect.iscoroutinefunction(auth_service.hash_password)
    assert not inspect.iscoroutinefunction(auth_service.verify_password)
    assert inspect.iscoroutinefunction(auth_service.hash_password_async)
    assert inspect.iscoroutinefunction(auth_service.verify_password_async)
