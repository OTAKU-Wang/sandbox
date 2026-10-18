"""Gap A52/A53/A54/A55 — dev-sandbox 供数链路的四条（数据装载路径）。

A52: 「对象存储读 + 解密 + 落盘」是阻塞 I/O，必须离开事件循环（asyncio.to_thread），
     否则一个建会话请求会把整个 worker 的心跳/健检一起停住。
A53: 资源不可用时不得静默 201（honest-degradation 铁律）——产品**带**资源但加载不到
     ⇒ 409 拒绝（宁可拒绝，不可假装）；产品本就**无**绑定资源 ⇒ 201 但显式披露
     ``data_loaded=false`` + 原因，调用方能区分「数据已挂载」与「数据根本没加载」。
A54: 解密明文只是 provision 期间的 staging 副本（所有适配器都把 data_path 复制进
     工作区），provision 之后必须回收；进程崩溃残留由 mtime 清扫兜底。
A55: 合约 ``allowed_sandbox_levels`` 必须参与 dev 会话的档位选择（此前客户端自选
     sandbox_level 直通 provision，合约条款在这条入口上完全不参与）。

沙箱 runtime 的 provision 一律以 spy 替身观测（本文件测的是供数链路，不是隔离机制）。
"""
import os
import threading
import time
import uuid
from pathlib import Path

import pytest

from app.core.config import get_settings
from app.models.contract import Contract, ContractStatus
from app.models.data_product import DataProduct
from app.models.data_resource import DataResource, ResourceStatus
from app.services.storage_service import storage_service

CSV = b"city,amount\nShanghai,100\nBeijing,200\n"


async def _mk_product(db_session, provider_id, *, resource_id=None) -> DataProduct:
    product = DataProduct(
        provider_id=uuid.UUID(str(provider_id)),
        name=f"a5x-{uuid.uuid4().hex[:8]}",
        status="published",
        resource_id=resource_id,
    )
    db_session.add(product)
    await db_session.flush()
    await db_session.refresh(product)
    return product


async def _mk_resource(db_session, provider_id, *, status=ResourceStatus.READY.value,
                       data: bytes = CSV, with_storage: bool = True) -> DataResource:
    """Real upload through storage_service (本地 dev 落盘 + 软件 KMS 信封加密)。"""
    meta = storage_service.upload(data, f"test-resources/{uuid.uuid4().hex}.csv", "text/csv")
    resource = DataResource(
        provider_id=uuid.UUID(str(provider_id)),
        name=f"a5x-res-{uuid.uuid4().hex[:8]}",
        storage_path=meta["path"] if with_storage else None,
        encryption_key_id=meta.get("key_id"),
        status=status,
        file_size_bytes=meta.get("size"),
        sm3_checksum=meta.get("checksum"),
        format="csv",
    )
    db_session.add(resource)
    await db_session.flush()
    await db_session.refresh(resource)
    return resource


async def _mk_contract(db_session, provider_id, product_ids, *, allowed_levels=None):
    contract = Contract(
        contract_no=f"A55-{uuid.uuid4().hex[:8]}",
        contract_type="data_query",
        provider_id=uuid.UUID(str(provider_id)),
        buyer_id=uuid.UUID(str(provider_id)),
        title="a55-level-gate",
        product_ids=[str(pid) for pid in product_ids],
        status=ContractStatus.ACTIVE.value,
        allowed_sandbox_levels=allowed_levels,
    )
    db_session.add(contract)
    await db_session.flush()
    await db_session.refresh(contract)
    return contract


def _spy_provision(monkeypatch, captured: dict):
    """Replace runtime.provision with an observer of what the sandbox actually got."""
    from app.services.data_product_sandbox import dev_sandbox

    def _fake(session_id, level, data_path, timeout, user_id=""):
        staged = Path(data_path) if data_path else None
        captured["calls"] = captured.get("calls", 0) + 1
        captured["level"] = level
        captured["data_path"] = data_path
        captured["exists_at_provision"] = bool(staged and staged.exists())
        captured["bytes_at_provision"] = staged.read_bytes() if (staged and staged.exists()) else None
        return {"container_id": f"{str(level).lower()}-fake", "status": "running",
                "attestation_quote": None}

    monkeypatch.setattr(dev_sandbox.runtime, "provision", _fake)


# ── A52 + A54: 装载离开事件循环，且明文副本不跨 provision 存活 ──────────────────
@pytest.mark.asyncio
async def test_data_load_runs_off_the_loop_and_is_reclaimed_afterwards(
    client, db_session, make_user, monkeypatch
):  
    headers, provider_id = await make_user("data_provider", "a52prov")
    resource = await _mk_resource(db_session, provider_id)
    product = await _mk_product(db_session, provider_id, resource_id=resource.id)

    captured: dict = {}
    main_ident = threading.main_thread().ident

    def _fake_download(object_name):
        captured["download_thread"] = threading.get_ident()
        return CSV

    monkeypatch.setattr(storage_service, "download_decrypted", _fake_download)
    _spy_provision(monkeypatch, captured)

    resp = await client.post("/api/v1/dev-sandbox/sessions", json={
        "mode": "structured",
        "data_product_id": str(product.id),
    }, headers=headers)
    assert resp.status_code == 201, resp.text
    body = resp.json()

    # A52 — the blocking download must NOT have run on the event-loop thread.
    assert captured["download_thread"] is not None
    assert captured["download_thread"] != main_ident, (
        "A52: storage read + decrypt still executes on the event loop thread"
    )

    # The data really reached the sandbox (no "fix" by never loading it).
    assert captured["exists_at_provision"] is True
    assert captured["bytes_at_provision"] == CSV
    assert body["data_loaded"] is True

    # A54 — the decrypted staging copy must not outlive provisioning.
    staged = Path(captured["data_path"])
    assert not staged.exists(), f"A54: plaintext copy left behind at {staged}"
    assert not staged.parent.exists(), f"A54: staging dir left behind at {staged.parent}"

    # terminate must stay idempotent-safe once nothing is left to clean.
    term = await client.post(f"/api/v1/dev-sandbox/sessions/{body['session_id']}/terminate",
                             headers=headers)
    assert term.status_code == 200, term.text


@pytest.mark.asyncio
async def test_failed_provision_still_reclaims_the_plaintext_copy(
    client, db_session, make_user, monkeypatch
):
    """A54: provision 抛异常也不能留下明文残渣。"""
    from app.services.data_product_sandbox import dev_sandbox

    headers, provider_id = await make_user("data_provider", "a54prov")
    resource = await _mk_resource(db_session, provider_id)
    product = await _mk_product(db_session, provider_id, resource_id=resource.id)

    monkeypatch.setattr(storage_service, "download_decrypted", lambda object_name: CSV)
    staged_paths: list[Path] = []

    def _boom(session_id, level, data_path, timeout, user_id=""):
        if data_path:
            staged_paths.append(Path(data_path))
        raise RuntimeError("provision blew up")

    monkeypatch.setattr(dev_sandbox.runtime, "provision", _boom)

    # ASGITransport re-raises server exceptions (raise_app_exceptions=True);
    # what is under test is that the finally-branch still reclaimed the file.
    with pytest.raises(RuntimeError, match="provision blew up"):
        await client.post("/api/v1/dev-sandbox/sessions", json={
            "mode": "structured",
            "data_product_id": str(product.id),
        }, headers=headers)
    assert staged_paths, "test precondition: provision never saw a staged copy"
    for path in staged_paths:
        assert not path.exists(), f"A54: plaintext copy survived a failed provision at {path}"


def test_stale_staging_dirs_are_swept():
    """A54: 进程被强杀的中途残留由 mtime 清扫兜底（新目录不动）。"""
    from app.api.dev_sandbox import _data_load_root, _sweep_stale_data_load_dirs

    root = _data_load_root()
    stale = root / "sweep-user" / f"stale-{uuid.uuid4().hex[:8]}"
    fresh = root / "sweep-user" / f"fresh-{uuid.uuid4().hex[:8]}"
    stale.mkdir(parents=True, exist_ok=True)
    fresh.mkdir(parents=True, exist_ok=True)
    (stale / "x.csv").write_bytes(b"secret")
    (fresh / "y.csv").write_bytes(b"secret")
    old = time.time() - 6 * 3600
    for path in (stale / "x.csv", stale):
        os.utime(path, (old, old))

    _sweep_stale_data_load_dirs(max_age_seconds=3600)

    assert not stale.exists(), "A54: stale plaintext dir survived the sweep"
    assert fresh.exists(), "A54: sweep must not touch a fresh (possibly in-flight) dir"


# ── A53: 资源加载不到 ⇒ 拒绝；产品无资源 ⇒ 显式披露 ────────────────────────────
@pytest.mark.asyncio
@pytest.mark.parametrize("status,with_storage,missing_row", [
    (ResourceStatus.ENCRYPTING.value, True, False),   # 资源还没 READY
    (ResourceStatus.FAILED.value, True, False),       # 资源失败
    (ResourceStatus.READY.value, False, False),       # READY 但没有 storage_path
    (ResourceStatus.READY.value, True, True),         # 资源行根本不存在
])
async def test_dev_session_refuses_when_product_data_cannot_load(
    client, db_session, make_user, monkeypatch, status, with_storage, missing_row
):
    """A53: 密态沙箱不得用 201 假装「数据已挂载」。"""
    headers, provider_id = await make_user("data_provider", "a53prov")
    resource = await _mk_resource(db_session, provider_id, status=status, with_storage=with_storage)
    product = await _mk_product(
        db_session, provider_id,
        resource_id=uuid.uuid4() if missing_row else resource.id,
    )

    captured: dict = {}
    _spy_provision(monkeypatch, captured)

    resp = await client.post("/api/v1/dev-sandbox/sessions", json={
        "mode": "structured",
        "data_product_id": str(product.id),
    }, headers=headers)

    assert resp.status_code == 409, resp.text
    body = resp.json()
    assert body["code"] == "SESSION_STATE_CONFLICT", body
    assert body["detail"]["data_loaded"] is False, body
    # 空沙箱绝不建出来：provision 一次都不该被调用
    assert captured.get("calls", 0) == 0, captured


@pytest.mark.asyncio
async def test_dev_session_discloses_product_without_attached_data(
    client, db_session, make_user, monkeypatch
):
    """A53: 无绑定资源是合法形态，但必须显式披露，不能与「已加载」同形。"""
    headers, provider_id = await make_user("data_provider", "a53nores")
    product = await _mk_product(db_session, provider_id, resource_id=None)

    captured: dict = {}
    _spy_provision(monkeypatch, captured)

    resp = await client.post("/api/v1/dev-sandbox/sessions", json={
        "mode": "structured",
        "data_product_id": str(product.id),
    }, headers=headers)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["data_loaded"] is False
    assert body["data_load_reason"]
    assert captured["data_path"] in (None, "")

    # 同一条披露必须能在 GET 上读到（不是只在创建响应里闪一下）
    got = await client.get(f"/api/v1/dev-sandbox/sessions/{body['session_id']}", headers=headers)
    assert got.status_code == 200, got.text
    assert got.json()["data_loaded"] is False
    assert got.json()["data_load_reason"]

    listed = await client.get("/api/v1/dev-sandbox/sessions", headers=headers)
    assert listed.status_code == 200
    match = [s for s in listed.json() if s["session_id"] == body["session_id"]]
    assert match and match[0]["data_loaded"] is False


@pytest.mark.asyncio
async def test_plain_dev_session_also_discloses_data_loaded(
    client, make_user, monkeypatch
):
    """不挂数据产品的纯开发会话：data_loaded=false（而不是缺字段）。"""
    headers, _ = await make_user("data_provider", "a53plain")
    captured: dict = {}
    _spy_provision(monkeypatch, captured)

    resp = await client.post("/api/v1/dev-sandbox/sessions", json={"mode": "structured"},
                             headers=headers)
    assert resp.status_code == 201, resp.text
    assert resp.json()["data_loaded"] is False
    assert resp.json()["data_load_reason"]


# ── A55: 合约档位门 ───────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_contract_sandbox_level_gate(client, db_session, make_user, monkeypatch):
    """A55: 客户端自选档位高于合约允许档位 ⇒ 400，允许档位仍可用（防过度修正）。"""
    monkeypatch.setattr(get_settings(), "DEV_SANDBOX_REQUIRE_CONTRACT", True)

    headers, provider_id = await make_user("data_provider", "a55prov")
    resource = await _mk_resource(db_session, provider_id)
    product = await _mk_product(db_session, provider_id, resource_id=resource.id)
    contract = await _mk_contract(db_session, provider_id, [product.id], allowed_levels="L2")

    monkeypatch.setattr(storage_service, "download_decrypted", lambda object_name: CSV)
    captured: dict = {}
    _spy_provision(monkeypatch, captured)

    resp = await client.post("/api/v1/dev-sandbox/sessions", json={
        "mode": "structured",
        "data_product_id": str(product.id),
        "contract_id": str(contract.id),
        "sandbox_level": "L3",
    }, headers=headers)
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body["code"] == "VALIDATION_ERROR", body
    assert "not allowed by contract" in body["message"], body
    assert body["detail"]["allowed_sandbox_levels"] == ["L2"], body
    assert captured.get("calls", 0) == 0

    # 合约允许的档位照旧可用（收紧而不是把入口焊死）
    contract.allowed_sandbox_levels = "L2,L3"
    await db_session.flush()

    ok = await client.post("/api/v1/dev-sandbox/sessions", json={
        "mode": "structured",
        "data_product_id": str(product.id),
        "contract_id": str(contract.id),
        "sandbox_level": "L3",
    }, headers=headers)
    assert ok.status_code == 201, ok.text
    assert captured["level"] == "L3"


@pytest.mark.asyncio
async def test_contract_without_level_clause_does_not_gate(
    client, db_session, make_user, monkeypatch
):
    """合约未写档位条款（NULL）⇒ 不设限，与 sandbox_sessions 同一判据。"""
    monkeypatch.setattr(get_settings(), "DEV_SANDBOX_REQUIRE_CONTRACT", True)

    headers, provider_id = await make_user("data_provider", "a55null")
    resource = await _mk_resource(db_session, provider_id)
    product = await _mk_product(db_session, provider_id, resource_id=resource.id)
    contract = await _mk_contract(db_session, provider_id, [product.id], allowed_levels=None)

    monkeypatch.setattr(storage_service, "download_decrypted", lambda object_name: CSV)
    captured: dict = {}
    _spy_provision(monkeypatch, captured)

    resp = await client.post("/api/v1/dev-sandbox/sessions", json={
        "mode": "structured",
        "data_product_id": str(product.id),
        "contract_id": str(contract.id),
        "sandbox_level": "L3",
    }, headers=headers)
    assert resp.status_code == 201, resp.text
    assert captured["level"] == "L3"
