"""P1a-6: conftest 卷桩不再是 import 期永久覆盖——真 prepare_session_volume 可达针。

cr §4/tests MAJOR-1：conftest 曾在 import 期永久覆盖
``CubeRuntimeAdapter.prepare_session_volume``，真卷预建路径（SS-14 RT-07：
master EnsureVolume → 插件 KMS 回写 VolumeKey → DEK 回查 → PutVolumeData）
在全测试会话不可达且无恢复路径。现在打桩是 per-test monkeypatch（autouse，
默认行为不变），本文件经 ``real_session_volume`` opt-in 走**真实方法本体**：
只把两个硬件边界 RPC（cube master HTTP、节点插件 unix socket）以显式假件
替身——这是 honest degradation，编排逻辑（fail-closed 门、DEK 回查、返回
插件 key ref）全部真实执行。
"""
import uuid

import pytest

from app.core import database as db_module
from app.core.config import get_settings
from app.core.errors import CDSError
from app.models.volume_key import VolumeKey
from app.services import cube_runtime_adapter as cra
from app.services.cube_runtime_adapter import CubeRuntimeAdapter

# ── 收集期结构针（fixture 尚未安装）：P1a-6 的 import 期永久覆盖若复活，
# 本文件在 collection 阶段即红——这比任何运行期断言都早。
assert CubeRuntimeAdapter.prepare_session_volume.__qualname__ == "CubeRuntimeAdapter.prepare_session_volume", (
    "P1a-6 回归：CubeRuntimeAdapter.prepare_session_volume 在 import 期又被永久覆盖了"
)


@pytest.fixture
def real_volume_flow(real_session_volume, db_session, monkeypatch):
    """真 prepare_session_volume + 两个硬件边界的显式假件 + DB 记录。

    adapter 模块顶层 ``from app.core.database import async_session`` 是
    import 早绑定（conftest 打补丁发生在 app.main 之后），生产语义如此，
    不动它；测试里把模块全局指到 conftest 的测试工厂，DEK 回查才落在
    套件内存库上（与 db_session 同库同连接，flush 可见）。
    """
    adapter = CubeRuntimeAdapter()
    monkeypatch.setattr(adapter._settings, "CUBE_MASTER_ADDR", "http://cube-master.invalid")
    calls: dict[str, list] = {"ensure": [], "put": []}
    # 边界 1：cube master EnsureVolume（真实现=同步 httpx；假件如实记录）
    monkeypatch.setattr(adapter, "_master_ensure_volume", lambda name: calls["ensure"].append(name))
    # 边界 2：节点插件 PutVolumeData（真实现=unix socket gRPC；假件如实记录）
    monkeypatch.setattr(
        adapter, "_put_volume_data", lambda name, data: calls["put"].append((name, len(data))) or len(data)
    )
    monkeypatch.setattr(cra, "async_session", db_module.async_session)
    return adapter, calls


async def _seed_volume_key(db_session, volume_name: str, key_id: str):
    db_session.add(VolumeKey(key_id=key_id, volume_id=volume_name))
    await db_session.flush()


class TestRealPrepareSessionVolume:
    @pytest.mark.asyncio
    async def test_happy_path_returns_plugin_dek_ref(self, real_volume_flow, db_session):
        adapter, calls = real_volume_flow
        sid = f"p1a6-{uuid.uuid4().hex[:12]}"
        volume = f"cds-data-{sid}"
        await _seed_volume_key(db_session, volume, "dek-ref-0001")

        dek_ref = await adapter.prepare_session_volume(sid, b"city,amount\n")

        assert dek_ref == "dek-ref-0001"  # 返回的是 KMS 落库后的插件 ref，不是自造串
        assert calls["ensure"] == [volume]
        assert calls["put"] == [(volume, len(b"city,amount\n"))]

    @pytest.mark.asyncio
    async def test_fail_closed_without_master_addr(self, real_volume_flow, monkeypatch):
        adapter, calls = real_volume_flow
        monkeypatch.setattr(adapter._settings, "CUBE_MASTER_ADDR", "")
        with pytest.raises(CDSError, match="fail-closed"):
            await adapter.prepare_session_volume("p1a6-nomaster", b"x")
        assert calls["ensure"] == [] and calls["put"] == []

    @pytest.mark.asyncio
    async def test_fail_closed_when_kms_never_registered_dek(self, real_volume_flow):
        """ensure 成功但插件 KMS 回查超时（无 VolumeKey 行）⇒ 必须拒走，
        不得带着空 DEK ref 继续 provision（10 次×0.2s 重试 ≈2s，真实路径）。"""
        adapter, calls = real_volume_flow
        with pytest.raises(CDSError, match="no VolumeKey row"):
            await adapter.prepare_session_volume(f"p1a6-missing-{uuid.uuid4().hex[:8]}", b"x")
        assert len(calls["ensure"]) == 1
        assert calls["put"] == []  # PutVolumeData 不得在 DEK 缺失时发生


class TestStubIsPerTestNotPermanent:
    @pytest.mark.asyncio
    async def test_default_stub_active_without_opt_in(self):
        """默认（不 opt-in）时 per-test 桩必须仍生效——211 文件既有行为不变。"""
        result = await CubeRuntimeAdapter.prepare_session_volume(
            object(), "sess-default", b"data"
        )
        assert result == "vault:v1:test-sess-default"

    @pytest.mark.asyncio
    async def test_opt_in_restores_real_then_unwinds(self, real_session_volume):
        """opt-in 拿到的是产品方法；monkeypatch 在用例结束自动解除，
        类属性回到 conftest 捕获的真实默认（下一个用例又是桩）。"""
        assert CubeRuntimeAdapter.prepare_session_volume is real_session_volume
        assert "to_thread(self._master_ensure_volume" in _src(real_session_volume)


def _src(fn) -> str:
    import inspect

    return inspect.getsource(fn)
