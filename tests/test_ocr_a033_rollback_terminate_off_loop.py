"""A33（high）— 回滚路径在事件循环上直接调用同步 `runtime.terminate`。

台账: docs/ocr-review-2026-10-18/ocr-verified-findings.md §A33

创建会话的三条回滚路径（attestation 缺失 / 会话密钥分发失败 / l2c 数据注入失败）
里，`runtime.terminate(...)` 是同步调用（docker/节点 RPC，可阻塞数十秒），直接跑在
事件循环线程上；同文件其它终止调用（:1044 与 session_lifecycle.py:79）都用
`asyncio.to_thread`。后果：一个失败请求的清理阶段会把整个 worker 的事件循环钉住。

断言只用**线程归属**判定（`threading.get_ident()`），不含任何计时/轮询。
"""

from __future__ import annotations

import threading
import uuid

import pytest
from httpx import AsyncClient


class _ThreadRecordingRuntime:
    """Records the OS thread every terminate() call ran on."""

    def __init__(self) -> None:
        self.terminations: list[tuple[str, int]] = []

    def get_adapter(self, level: str):
        return self

    def provision(self, **kwargs):
        return {"container_id": "dummy-a33", "status": "running"}

    async def execute(self, container_id: str, code: str, language: str, **kwargs):
        return {"output": "", "exit_code": 0}

    def terminate(self, container_id: str) -> bool:
        self.terminations.append((container_id, threading.get_ident()))
        return True


async def test_key_distribution_rollback_terminates_off_the_event_loop(
    client: AsyncClient,
    auth_headers: dict,
    monkeypatch: pytest.MonkeyPatch,
    publish_product,
) -> None:
    """A33: 回滚清理必须经 asyncio.to_thread，不得占用事件循环线程。"""
    import app.api.sandbox_sessions as api
    from app.services.kms_service import kms_service

    runtime = _ThreadRecordingRuntime()

    async def fake_get_sandbox_manager():
        return runtime

    monkeypatch.setattr(api, "get_sandbox_manager", fake_get_sandbox_manager)
    monkeypatch.setattr(kms_service, "distribute_key", lambda *a, **k: None)

    product_resp = await client.post(
        "/api/v1/data-products", json={"name": f"A33-{uuid.uuid4().hex[:6]}"}, headers=auth_headers
    )
    product_id = product_resp.json()["id"]
    await publish_product(product_id)

    loop_thread = threading.get_ident()
    resp = await client.post(
        "/api/v1/sandbox-sessions", json={"data_product_id": product_id}, headers=auth_headers
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "failed", body
    assert body["container_id"] is None, body

    assert [c for c, _ in runtime.terminations] == ["dummy-a33"], runtime.terminations
    assert runtime.terminations[0][1] != loop_thread, (
        "A33: synchronous runtime.terminate ran on the event-loop thread "
        "(it must run via asyncio.to_thread like every other terminate call)"
    )


async def test_attestation_denial_rollback_terminates_off_the_event_loop(
    client: AsyncClient,
    auth_headers: dict,
    monkeypatch: pytest.MonkeyPatch,
    publish_product,
) -> None:
    """A33 第二处回滚点（:560）：attestation 缺失同样不得在事件循环上 terminate。"""
    import app.api.sandbox_sessions as api

    runtime = _ThreadRecordingRuntime()

    async def fake_get_sandbox_manager():
        return runtime

    monkeypatch.setattr(api, "get_sandbox_manager", fake_get_sandbox_manager)
    monkeypatch.setattr(api, "_attestation_required_for_level", lambda level: True)

    product_resp = await client.post(
        "/api/v1/data-products", json={"name": f"A33B-{uuid.uuid4().hex[:6]}"}, headers=auth_headers
    )
    product_id = product_resp.json()["id"]
    await publish_product(product_id)

    loop_thread = threading.get_ident()
    resp = await client.post(
        "/api/v1/sandbox-sessions", json={"data_product_id": product_id}, headers=auth_headers
    )

    assert resp.status_code == 201, resp.text
    assert resp.json()["status"] == "failed", resp.json()

    assert [c for c, _ in runtime.terminations] == ["dummy-a33"], runtime.terminations
    assert runtime.terminations[0][1] != loop_thread, (
        "A33: attestation-denial rollback ran terminate() on the event-loop thread"
    )
