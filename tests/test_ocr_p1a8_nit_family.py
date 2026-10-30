"""P1a-8 NIT 族逐条验（cr §4-9「Cube 其余 MINOR/NIT」尾巴）— ②③④。

逐条按「验 → 修或证伪 → 钉」：
- ② `contract_fulfillment` 空取值域兜底 L3：**修** — 兜底档保留（legacy NULL
  是现实），但来源必须诚实披露：会话信息与履约审计能分辨 `contract_declared`
  与 `platform_default_fallback`（禁伪造：兜底不伪装成合约授权）；选档语义一字
  未动，A55c 供数下限门对兜底档同样生效。本文件钉新披露字段。
- ③ `cube_runtime_adapter.provision` 节点报成功却不带 sandboxID 时 `uuid4()`
  兜底：**修** — 臆造 ID = 凭空发明节点从未登记的地址，session 行与 destroy/
  exec/metrics 全拿假 ID 寻址（`_parse_container_id` 四处消费），真沙箱连同
  注入的数据通道脱离账本泄漏。改为响亮失败（session 记 FAILED），绝不臆造。
- ④ dedup 唯一约束竞态以 500 收场：**证伪（已由 A120 修复）** — 幂等而非 409；
  `_claim_dedup_key` 用 SAVEPOINT + 赢家可见性校验把落败者计为重复（`return
  False`），非 500。功能面已由 `test_ocr_a120_egress_dedup_race.py` 钉住；此处
  补一条直接幂等 + 结构守卫（禁「未捕获再退回 500」的回归）。

（① gencode/protobuf 兼容声明在 test_ocr_p1a8_gencode_compat.py。）
"""
from __future__ import annotations

import ast
import inspect
import uuid

import pytest

from app._stubs.cube.cubebox.v1 import cubebox_pb2
from app._stubs.cube.errorcode.v1 import errorcode_pb2
from app.core.config import get_settings
from app.models.contract import Contract, ContractStatus
from app.models.cube_egress_dedup import CubeEgressDedup
from app.models.data_product import DataProduct, DataProductStatus
from app.services import cube_egress_audit
from app.services.contract_fulfillment import contract_fulfillment


# ── ② 履约兜底档来源披露 ──────────────────────────────────────────────────
# helper 本地复制是 tests/ 惯例（无共享 util 模块），照 test_ocr_a55c_* 的形状。
class _RecordingRuntime:
    def __init__(self):
        self.provision_calls: list[str] = []

    def provision(self, **kwargs):
        self.provision_calls.append(str(kwargs.get("level")))
        return {"container_id": "p1a8-container", "status": "running"}

    def terminate(self, container_id: str):
        return True


class _OkKMS:
    def generate_session_key(self, session_id: str):
        return {"key_id": f"p1a8-key-{session_id}", "key_bytes": b"0" * 32}

    def distribute_key(self, key_id: str, session_id: str, **kwargs):
        return {"key_bytes": b"0" * 32}

    def export_wrapped(self, key_id: str):
        return b"wrapped-blob"

    def export_sm2_ciphertext(self, key_id: str):
        return b"sm2-blob"

    def destroy_key(self, key_id: str):
        return True


async def _use_fake_runtime(monkeypatch):
    import app.services.contract_fulfillment as fm

    runtime = _RecordingRuntime()

    async def _fake_manager():
        return runtime

    monkeypatch.setattr(fm, "get_sandbox_manager", _fake_manager)
    monkeypatch.setattr(fm, "kms_service", _OkKMS())
    return runtime


async def _mk_product(db_session, make_user):
    _, provider_id = await make_user("data_provider", f"p1a8prov{uuid.uuid4().hex[:8]}")
    product = DataProduct(
        provider_id=uuid.UUID(provider_id),
        name=f"P1a8 Product {uuid.uuid4().hex[:8]}",
        product_type="structured",
        status=DataProductStatus.PUBLISHED.value,
    )
    db_session.add(product)
    await db_session.flush()
    return product, provider_id


async def _mk_contract(db_session, make_user, provider_id, *, product_ids, allowed_levels):
    _, buyer_id = await make_user("buyer", f"p1a8buyer{uuid.uuid4().hex[:8]}")
    contract = Contract(
        contract_no=f"P1A8-{uuid.uuid4().hex[:8]}",
        contract_type="data_query",
        status=ContractStatus.ACTIVE.value,
        provider_id=uuid.UUID(provider_id),
        buyer_id=uuid.UUID(buyer_id),
        title="p1a8-nit",
        terms={},
        product_ids=[str(pid) for pid in product_ids],
        allowed_operations="query",
        allowed_sandbox_levels=allowed_levels,
        allowed_sandbox_modes=["structured_query"],
    )
    db_session.add(contract)
    await db_session.flush()
    await db_session.refresh(contract)
    return contract


@pytest.mark.asyncio
async def test_empty_level_discloses_platform_fallback(db_session, make_user, monkeypatch):
    """②核心：空取值域仍默认达标档 L3，但必须披露来源是平台兜底，不是合约授权。"""
    await _use_fake_runtime(monkeypatch)
    product, provider_id = await _mk_product(db_session, make_user)
    contract = await _mk_contract(
        db_session, make_user, provider_id, product_ids=[product.id], allowed_levels=None
    )

    result = await contract_fulfillment.fulfill_contract(db_session, contract.id)
    assert result["status"] == "fulfilled"
    entry = result["provisioned_sessions"][0]
    assert entry["sandbox_level"] == "L3", "兜底档仍是达标档 L3（不得升/降档）"
    assert entry["sandbox_level_source"] == "platform_default_fallback", (
        "兜底档必须以来源披露与合约声明档区分（禁伪造：兜底不伪装成授权）"
    )


@pytest.mark.asyncio
async def test_declared_level_discloses_contract_source(db_session, make_user, monkeypatch):
    """②边界：合约明确声明的档位来源标 contract_declared，且选档一字不变。"""
    await _use_fake_runtime(monkeypatch)
    product, provider_id = await _mk_product(db_session, make_user)
    contract = await _mk_contract(
        db_session, make_user, provider_id, product_ids=[product.id], allowed_levels="l2c,L3"
    )

    result = await contract_fulfillment.fulfill_contract(db_session, contract.id)
    entry = result["provisioned_sessions"][0]
    assert entry["sandbox_level"] == "l2c", "声明档取第一档的语义不得因披露而变"
    assert entry["sandbox_level_source"] == "contract_declared"


# ── ③ 空 sandboxID 不臆造 uuid ────────────────────────────────────────────
class _EmptyIdClient:
    def __init__(self):
        self.calls = 0

    def create(self, request):
        self.calls += 1
        resp = cubebox_pb2.RunCubeSandboxResponse()
        resp.requestID = request.requestID
        resp.sandboxID = ""  # 节点报成功却不带 ID：协议违规
        resp.ret.ret_code = errorcode_pb2.Success
        return resp


class _GoodIdClient:
    def create(self, request):
        resp = cubebox_pb2.RunCubeSandboxResponse()
        resp.requestID = request.requestID
        resp.sandboxID = "b" * 32
        resp.ret.ret_code = errorcode_pb2.Success
        return resp


def _provision(monkeypatch, tmp_path, client):
    from app.services.cube_runtime_adapter import CubeRuntimeAdapter

    monkeypatch.setattr(get_settings(), "CUBE_DATAPLANE_ENABLED", True)
    monkeypatch.setattr(get_settings(), "CUBE_RESULTS_DIR", str(tmp_path))
    adapter = CubeRuntimeAdapter(client_factory=lambda node: client)
    return adapter.provision(
        "sess-p1a8", "", 3600,
        cube_node={"node_id": "n1", "endpoint": "10.0.0.1:9443", "runtime_version": "v1"},
        volume_dek_ref="vault:v1:precreated-dek",
    )


def test_empty_sandbox_id_is_refused_not_fabricated(monkeypatch, tmp_path):
    """③核心：空 sandboxID ⇒ 响亮失败，container_id 为 None，绝不 uuid4() 兜底。"""
    result = _provision(monkeypatch, tmp_path, _EmptyIdClient())
    assert result["status"] == "failed"
    assert result["container_id"] is None, "不得返回臆造出来的 container_id"
    assert "sandbox_id" not in result, "错误分支不得带出一个假 sandbox_id"
    assert "empty sandboxID" in result["error"]


def test_real_sandbox_id_still_provisions_unchanged(monkeypatch, tmp_path):
    """③边界（防越界收紧）：节点给了真 ID 时行为逐字不变。"""
    result = _provision(monkeypatch, tmp_path, _GoodIdClient())
    assert result["status"] == "running"
    assert result["container_id"] == "l2c-n1-" + "b" * 32


# ── ④ dedup 唯一约束竞态：证伪（A120 已修，幂等非 500）+ 回归守卫 ──────────
@pytest.mark.asyncio
async def test_dedup_race_loser_is_idempotent_not_500(db_session):
    """④直接幂等钉：同一 (node,file,offset,line) 二次 claim 返回 False（计为重复），
    不抛 IntegrityError ⇒ 端到端不会再冒 500 把整批审计带下（A120 语义）。"""
    claim = cube_egress_audit._claim_dedup_key
    first = await claim(db_session, "node-1", "egress.jsonl", 0, 42)
    assert first is True, "首次 claim 应持有该幂等键"
    await db_session.flush()
    second = await claim(db_session, "node-1", "egress.jsonl", 0, 42)
    assert second is False, "落败者必须计为重复返回 False，而非抛 IntegrityError→500"


def test_dedup_guard_does_not_degrade_back_to_500():
    """④结构守卫：钉住修复的三要件（SAVEPOINT + IntegrityError 捕获 + 仅可见赢家
    才判重、其余 IntegrityError 继续抛），防止有人把它改回「未捕获 ⇒ 整批 500」。"""
    src = inspect.getsource(cube_egress_audit._claim_dedup_key)
    assert "begin_nested" in src, "dedup INSERT 必须走 SAVEPOINT，不能整批回滚"
    assert "IntegrityError" in src and "return False" in src, "竞态落败必须返回 False（幂等）"
    tree = ast.parse(src)
    # 只把「赢家可见」的冲突判重；其它完整性错误必须原样 raise（不许静默丢行）
    assert "raise" in src, "非重复的 IntegrityError 必须继续传播，不得静默丢审计行"
    # 全仓 dedup 写入方唯一，且必须经 _claim_dedup_key（防第二处裸 INSERT 复活 500）
    callers = [n for n in ast.walk(ast.parse(inspect.getsource(cube_egress_audit)))
               if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "_claim_dedup_key"]
    assert callers, "ingest 必须通过 _claim_dedup_key 写入幂等键"
