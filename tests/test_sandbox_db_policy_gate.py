"""A48 A49 A50 A51 A56 — sandbox-db 数据出域门的 fail-closed 契约。

台账：ocr-verified-findings.md 第十三批（app/api/sandbox_db.py × secure_duckdb.py）。
契约：docs/error-codes.md（POLICY_COMPILE_ERROR=400 策略未下发即拒 / FORBIDDEN=403 /
require_roles 的 403），错误体形状 {code,message,detail,request_id}（app/core/error_handlers.py）。

覆盖矩阵（"应有策略却挂不上 ⇒ 拒绝" vs "本就无需策略 ⇒ 放行" 是本文件的核心分野）：
| 场景 | 期望 |
| A48 会话有合约、合约禁止读取 | 拒绝执行（策略在同一条 SQL 之前就已挂接）|
| A48 会话有合约、合约允许读取 | 放行，且 engine.policy_state == attached |
| A48/A49 会话有合约但合约行取不到 | 400 POLICY_COMPILE_ERROR + WARNING + 独立审计 |
| A48 未经策略解析的引擎（进程内用法） | 放行；一旦标记 required 未挂策略 ⇒ 拒绝 |
| A49 默认 session_id | 完整 UUID；无 DB 会话行 = 无合约 = 放行 |
| A50 json_path / parquet_path 越出导入根 | 403，且不落库 |
| A50 导入根内的合法文件 | 200 |
| A51 encryption-config 明文 DEK | 非 ADMIN/OPERATOR ⇒ 403；ADMIN/OPERATOR ⇒ 200 + 审计 |
| A56 TTL 清扫 | 任何端点都扫；不留与 _engines 平行的残留登记 |
"""
import ast
import inspect
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.api import sandbox_db
from app.api.sandbox_db import CreateTableRequest, EncryptedTableRequest
from app.models.contract import Contract, ContractStatus
from app.models.sandbox_session import SandboxSession, SessionStatus
from app.services import secure_duckdb as duckdb_module
from app.services.audit_service import audit_service
from app.services.secure_duckdb import SecureDuckDBEngine

DB = "/api/v1/sandbox-db"

# 策略状态常量的期望值（test_policy_state_vocabulary_is_public 钉住它们确实被导出，
# 这里取默认值只是为了让"缺陷还在"的那一轮能逐条跑红，而不是收集期就炸）。
POLICY_NOT_REQUIRED = getattr(duckdb_module, "POLICY_NOT_REQUIRED", "not_required")
POLICY_REQUIRED = getattr(duckdb_module, "POLICY_REQUIRED", "required")
POLICY_ATTACHED = getattr(duckdb_module, "POLICY_ATTACHED", "attached")


# --------------------------------------------------------------------------- helpers

async def _mk_contract(
    db_session,
    provider_id: str,
    *,
    status: str = ContractStatus.ACTIVE.value,
    allowed_operations: str = "read,analyze",
    allowed_sandbox_levels: str = "L3",
    max_output_rows: int = 1000,
):
    contract = Contract(
        contract_no=f"A48-{uuid.uuid4().hex[:12]}",
        contract_type="data_query",
        status=status,
        provider_id=uuid.UUID(provider_id),
        buyer_id=uuid.UUID(provider_id),
        title="sandbox-db policy gate",
        terms={},
        product_ids=[str(uuid.uuid4())],
        allowed_sandbox_levels=allowed_sandbox_levels,
        allowed_sandbox_modes=["query"],
        allowed_operations=allowed_operations,
        max_duration_hours=1,
        max_output_rows=max_output_rows,
    )
    db_session.add(contract)
    await db_session.flush()
    await db_session.refresh(contract)
    return contract


async def _mk_session(db_session, user_id: str, *, contract_id: str | None = None,
                      sandbox_level: str = "L3"):
    session = SandboxSession(
        id=uuid.uuid4(),
        user_id=uuid.UUID(user_id),
        data_product_id=uuid.uuid4(),
        sandbox_level=sandbox_level,
        sandbox_mode="structured_query",
        status=SessionStatus.RUNNING.value,
        contract_id=contract_id,
        timeout_seconds=3600,
    )
    db_session.add(session)
    await db_session.flush()
    await db_session.refresh(session)
    return session


async def _create_table(client, headers, session_id: str, table_name: str = "people"):
    return await client.post(
        f"{DB}/create-table",
        json={
            "session_id": session_id,
            "table_name": table_name,
            "data": [{"name": "alice", "phone": "13800000001"},
                     {"name": "bob", "phone": "13800000002"}],
        },
        headers=headers,
    )


def _lingering_refs(module, session_id: str) -> list[str]:
    """列出模块里仍以该 session 为键的字典（A56：与 _engines 平行的残留登记）。"""
    return [
        name
        for name, value in vars(module).items()
        if isinstance(value, dict) and session_id in value
    ]


@pytest.fixture
def audit_spy(monkeypatch):
    """录下 log_standalone（拒绝类审计写在本请求事务之外，见 audit_service 文档）。"""
    calls: list[dict] = []

    async def _record(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(audit_service, "log_standalone", _record)
    return calls


# ----------------------------------------------------------------------- A48 / A49

@pytest.mark.asyncio
async def test_contract_policy_is_attached_before_first_query(client, db_session, make_user):
    """A48：策略必须在同一次请求内、第一条 SQL 之前挂上（不再有 fire-and-forget 窗口）。"""
    headers, uid = await make_user("data_provider", "a48_ok")
    contract = await _mk_contract(db_session, uid, allowed_operations="read,analyze")
    session = await _mk_session(db_session, uid, contract_id=str(contract.id))
    sid = str(session.id)

    resp = await _create_table(client, headers, sid)
    assert resp.status_code == 200, resp.text

    engine = sandbox_db.get_engine(sid)
    assert engine is not None
    assert engine.policy_state == POLICY_ATTACHED
    assert engine.policy_contract_id == str(contract.id)

    # 合约放行读取 ⇒ 合法路径照旧可用（不得被 fail-closed 误伤）
    resp = await client.post(f"{DB}/query", json={"session_id": sid, "sql": "SELECT * FROM people"},
                             headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["row_count"] == 2


@pytest.mark.asyncio
async def test_contract_policy_denial_refuses_query(client, db_session, make_user):
    """A48：策略挂上了但合约不允许读取 ⇒ 拒绝，不得返回数据。"""
    headers, uid = await make_user("data_provider", "a48_deny")
    contract = await _mk_contract(db_session, uid, allowed_operations="train")
    session = await _mk_session(db_session, uid, contract_id=str(contract.id))
    sid = str(session.id)

    assert (await _create_table(client, headers, sid)).status_code == 200
    resp = await client.post(f"{DB}/query", json={"session_id": sid, "sql": "SELECT * FROM people"},
                             headers=headers)
    assert resp.status_code == 400
    assert "Policy denied" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_missing_contract_refuses_execution_and_leaves_warning_and_audit(
    client, db_session, make_user, caplog, audit_spy
):
    """A48/A49：会话声明了合约但策略挂不上 ⇒ 400 POLICY_COMPILE_ERROR + WARNING + 审计。

    旧实现：`except Exception: logger.debug(...)` ⇒ 200 放行、日志级别看不见。
    """
    headers, uid = await make_user("data_provider", "a49_missing")
    session = await _mk_session(db_session, uid, contract_id=str(uuid.uuid4()))  # 合约行不存在
    sid = str(session.id)

    with caplog.at_level(logging.WARNING, logger="app.api.sandbox_db"):
        resp = await _create_table(client, headers, sid)

    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body["code"] == "POLICY_COMPILE_ERROR"
    assert body["detail"]["contract_id"] == session.contract_id
    assert "request_id" in body

    # A49：失败至少要 WARNING（旧代码只进 debug，生产 INFO 下不可见）
    assert any(r.levelno >= logging.WARNING and sid in r.getMessage() for r in caplog.records), \
        [r.getMessage() for r in caplog.records]

    # 审计留痕：拒绝类审计走独立会话（请求事务会被回滚）
    actions = [c.get("action") for c in audit_spy]
    assert "sandbox_db.policy_attach_denied" in actions
    entry = next(c for c in audit_spy if c.get("action") == "sandbox_db.policy_attach_denied")
    assert entry["resource_type"] == "sandbox_session"
    assert entry["detail"]["contract_id"] == session.contract_id

    # 查询链路同样拒绝（不能只在建表处把门）
    resp = await client.post(f"{DB}/query", json={"session_id": sid, "sql": "SELECT 1"}, headers=headers)
    assert resp.status_code == 400
    assert resp.json()["code"] == "POLICY_COMPILE_ERROR"


@pytest.mark.asyncio
async def test_inactive_contract_policy_refuses_query(client, db_session, make_user):
    """A48：合约已终止 ⇒ 策略判定不通过 ⇒ 拒绝（不是"策略没挂所以放行"）。"""
    headers, uid = await make_user("data_provider", "a48_draft")
    contract = await _mk_contract(db_session, uid, status=ContractStatus.DRAFT.value)
    session = await _mk_session(db_session, uid, contract_id=str(contract.id))
    sid = str(session.id)

    assert (await _create_table(client, headers, sid)).status_code == 200
    engine = sandbox_db.get_engine(sid)
    assert engine.policy_state == POLICY_ATTACHED
    resp = await client.post(f"{DB}/query", json={"session_id": sid, "sql": "SELECT * FROM people"},
                             headers=headers)
    assert resp.status_code == 400
    assert "Policy denied" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_session_without_contract_is_not_policy_gated(client, db_session, make_user):
    """A48/A49 的反面：会话存在但没有治理合约 ⇒ 显式登记"无需策略"并放行。"""
    headers, uid = await make_user("data_provider", "a48_nocontract")
    session = await _mk_session(db_session, uid, contract_id=None)
    sid = str(session.id)

    assert (await _create_table(client, headers, sid)).status_code == 200
    engine = sandbox_db.get_engine(sid)
    assert engine.policy_state == POLICY_NOT_REQUIRED

    resp = await client.post(f"{DB}/query", json={"session_id": sid, "sql": "SELECT * FROM people"},
                             headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["row_count"] == 2


@pytest.mark.asyncio
async def test_adhoc_session_id_outside_db_is_allowed(client, auth_headers):
    """A48/A49 的反面：sandbox-db 自有会话（DB 里没有会话行）= 无合约 = 放行。"""
    sid = f"a48-adhoc-{uuid.uuid4().hex[:8]}"
    assert (await _create_table(client, auth_headers, sid)).status_code == 200
    engine = sandbox_db.get_engine(sid)
    assert engine.policy_state == POLICY_NOT_REQUIRED

    resp = await client.post(f"{DB}/query", json={"session_id": sid, "sql": "SELECT * FROM people"},
                             headers=auth_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["row_count"] == 2


def test_default_session_ids_are_full_uuids():
    """A49：默认 session_id 必须能进 uuid.UUID(...)（8 位 hex 时策略解析 100% 失败）。"""
    defaults = [
        CreateTableRequest(table_name="t", data=[{"a": 1}]).session_id,
        EncryptedTableRequest(table_name="t", data=[{"a": 1}],
                              encrypted_columns={"a": "sm4-siv"}).session_id,
    ]
    for default in defaults:
        assert uuid.UUID(default)  # 旧实现这里直接 ValueError
        assert len(default) == 36


@pytest.mark.asyncio
async def test_default_session_id_path_still_executes(client, auth_headers):
    """A49：不传 session_id 的合法调用（默认 UUID、无会话行）必须照常执行。"""
    resp = await client.post(
        f"{DB}/create-table",
        json={"table_name": "adhoc", "data": [{"a": 1}, {"a": 2}]},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    sid = resp.json()["session_id"]
    assert uuid.UUID(sid)

    resp = await client.post(f"{DB}/query", json={"session_id": sid, "sql": "SELECT * FROM adhoc"},
                             headers=auth_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["row_count"] == 2


@pytest.mark.asyncio
async def test_no_fire_and_forget_policy_attach():
    """A48：策略挂接不得再用未 await 的后台任务（异常没人取回、时序不可控）。

    查 AST 而不是查源码文本：描述旧缺陷的注释里也会写 create_task。
    """
    tree = ast.parse(inspect.getsource(sandbox_db))
    spawned = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr in ("create_task", "ensure_future", "run_until_complete")
    }
    assert not spawned, f"sandbox_db must not spawn un-awaited tasks: {sorted(spawned)}"
    assert not hasattr(sandbox_db, "_attach_policy")


def test_engine_refuses_sql_when_policy_required_but_missing():
    """A48 执行门本体：标记"必须有策略"而策略缺席 ⇒ execute_query 拒绝。"""
    engine = SecureDuckDBEngine("unit-policy-gate", mode="memory")
    try:
        engine.register_table("t", [{"a": 1}])
        # 未声明合约治理的进程内引擎：保持放行（测试/离线用法）
        assert engine.policy_state != POLICY_REQUIRED
        assert engine.execute_query("SELECT * FROM t").success is True

        engine.require_contract_policy()
        assert engine.policy_state == POLICY_REQUIRED
        denied = engine.execute_query("SELECT * FROM t")
        assert denied.success is False
        assert "policy" in (denied.error or "").lower()

        # 挂上合约允许的策略后恢复放行
        from app.services.policy_compiler import PolicyBundle
        engine.set_policy(PolicyBundle(
            contract_id=str(uuid.uuid4()),
            rego_source="package cds.policy.unit",
            rules={"status": "active", "sandbox_levels": ["L3"], "operations": ["read"],
                   "max_duration": 3600, "dp_epsilon": None},
        ))
        assert engine.policy_state == POLICY_ATTACHED
        assert engine.execute_query("SELECT * FROM t").success is True
    finally:
        engine.close()


def test_policy_state_vocabulary_is_public():
    """A48：引擎必须把策略状态作为可观测面暴露（诚实降级：能查到现在处于哪一档）。"""
    for name in ("POLICY_UNSET", "POLICY_NOT_REQUIRED", "POLICY_REQUIRED", "POLICY_ATTACHED"):
        assert hasattr(duckdb_module, name), f"secure_duckdb must export {name}"


def test_policy_state_never_downgrades():
    """A48：已挂策略的引擎不得被"无需策略"回填（避免并发解析把门打开）。"""
    from app.services.policy_compiler import PolicyBundle
    engine = SecureDuckDBEngine("unit-policy-state", mode="memory")
    try:
        bundle = PolicyBundle(contract_id="c1", rego_source="p", rules={"status": "active"})
        engine.set_policy(bundle)
        engine.mark_policy_not_required("racy late resolution")
        assert engine.policy_state == POLICY_ATTACHED
        assert engine._policy is bundle
        engine.require_contract_policy()
        assert engine.policy_state == POLICY_ATTACHED
    finally:
        engine.close()


# ------------------------------------------------------------------------------ A50

@pytest.fixture
def import_root(tmp_path):
    """把平台导入根指到 pytest 临时目录的一个子目录（根外即视为越界）。"""
    inside = tmp_path / "imports"
    inside.mkdir()
    return inside, tmp_path


@pytest.mark.asyncio
async def test_import_json_outside_import_root_is_refused(
    client, auth_headers, import_root, monkeypatch
):
    """A50：json_path 由请求体控制 ⇒ 越出导入根必须 403，且内容绝不落进表里。"""
    inside, outside = import_root
    monkeypatch.setenv("CDS_SANDBOX_IMPORT_ROOTS", str(inside))
    secret = outside / "env.json"          # 根外：相当于 /app/.env
    secret.write_text(json.dumps({"DB_PASSWORD": "s3cr3t"}), encoding="utf-8")

    sid = f"a50-json-{uuid.uuid4().hex[:8]}"
    assert (await _create_table(client, auth_headers, sid)).status_code == 200

    resp = await client.post(
        f"{DB}/import/json",
        json={"session_id": sid, "table_name": "stolen", "json_path": str(secret)},
        headers=auth_headers,
    )
    assert resp.status_code == 403, resp.text
    body = resp.json()
    assert body["code"] in ("FORBIDDEN", "HTTP_403")
    assert body["detail"]

    tables = (await client.get(f"{DB}/sessions/{sid}/tables", headers=auth_headers)).json()
    assert [t["table_name"] for t in tables] == ["people"]


@pytest.mark.asyncio
async def test_import_json_inside_import_root_still_works(
    client, auth_headers, import_root, monkeypatch
):
    """A50 的反面：导入根内的合法文件必须照常导入（不得过度收紧）。"""
    inside, _ = import_root
    monkeypatch.setenv("CDS_SANDBOX_IMPORT_ROOTS", str(inside))
    (inside / "ok.json").write_text(json.dumps([{"a": 1}, {"a": 2}]), encoding="utf-8")

    sid = f"a50-ok-{uuid.uuid4().hex[:8]}"
    assert (await _create_table(client, auth_headers, sid)).status_code == 200
    resp = await client.post(
        f"{DB}/import/json",
        json={"session_id": sid, "table_name": "ok", "json_path": str(inside / "ok.json")},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["row_count"] == 2


@pytest.mark.asyncio
async def test_import_parquet_outside_import_root_is_refused(
    client, auth_headers, import_root, monkeypatch
):
    """A50：parquet_path 同形 —— 越界 ⇒ 403（校验发生在 DuckDB 读文件之前）。"""
    inside, outside = import_root
    monkeypatch.setenv("CDS_SANDBOX_IMPORT_ROOTS", str(inside))
    victim = outside / "victim.parquet"
    victim.write_bytes(b"PAR1")

    sid = f"a50-pq-{uuid.uuid4().hex[:8]}"
    assert (await _create_table(client, auth_headers, sid)).status_code == 200
    resp = await client.post(
        f"{DB}/import/parquet",
        json={"session_id": sid, "table_name": "stolen", "parquet_path": str(victim)},
        headers=auth_headers,
    )
    assert resp.status_code == 403, resp.text
    tables = (await client.get(f"{DB}/sessions/{sid}/tables", headers=auth_headers)).json()
    assert [t["table_name"] for t in tables] == ["people"]


@pytest.mark.asyncio
async def test_import_glob_pattern_is_refused(client, auth_headers, import_root, monkeypatch):
    """A50：read_parquet 的 glob 等于目录枚举 ⇒ 通配符一律拒绝。"""
    inside, _ = import_root
    monkeypatch.setenv("CDS_SANDBOX_IMPORT_ROOTS", str(inside))
    sid = f"a50-glob-{uuid.uuid4().hex[:8]}"
    assert (await _create_table(client, auth_headers, sid)).status_code == 200
    resp = await client.post(
        f"{DB}/import/parquet",
        json={"session_id": sid, "table_name": "enum",
              "parquet_path": str(inside / "*.parquet")},
        headers=auth_headers,
    )
    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_symlink_escape_is_refused(client, auth_headers, import_root, monkeypatch):
    """A50：根内的符号链接指向根外 ⇒ 解析后仍越界 ⇒ 403。"""
    inside, outside = import_root
    monkeypatch.setenv("CDS_SANDBOX_IMPORT_ROOTS", str(inside))
    victim = outside / "secret.json"
    victim.write_text(json.dumps([{"a": 1}]), encoding="utf-8")
    link = inside / "link.json"
    try:
        link.symlink_to(victim)
    except (OSError, NotImplementedError):  # Windows 无符号链接权限
        pytest.skip("symlinks unavailable on this platform")

    sid = f"a50-link-{uuid.uuid4().hex[:8]}"
    assert (await _create_table(client, auth_headers, sid)).status_code == 200
    resp = await client.post(
        f"{DB}/import/json",
        json={"session_id": sid, "table_name": "stolen", "json_path": str(link)},
        headers=auth_headers,
    )
    assert resp.status_code == 403, resp.text


def test_json_import_size_cap():
    """A50：整文件 f.read() 无上限是 DoS 面 ⇒ 有硬上限（引擎级）。"""
    engine = SecureDuckDBEngine("unit-import-cap", mode="memory")
    try:
        assert engine.max_import_bytes > 0
    finally:
        engine.close()


# ------------------------------------------------------------------------------ A51

async def _mk_encrypted_table(client, headers, sid: str):
    resp = await client.post(
        f"{DB}/create-encrypted-table",
        json={
            "session_id": sid,
            "table_name": "secret",
            "data": [{"name": "alice", "id_no": "110101199001011234"}],
            "encrypted_columns": {"id_no": "sm4-gcm"},
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    # DEK 从过进程内注入口读（A51 的立场：HTTP 不再是拿明文密钥的通道）
    cfg = sandbox_db.get_encryption_config_for_session(sid)
    assert cfg and cfg.get("dek_hex")
    return cfg["dek_hex"]


@pytest.mark.asyncio
async def test_encryption_config_dek_requires_privileged_role(
    client, db_session, make_user
):
    """A51：明文列 DEK 的 HTTP 口子不得对任意会话拥有者开放。"""
    headers, uid = await make_user("data_provider", "a51_owner")
    sid = f"a51-{uuid.uuid4().hex[:8]}"
    await _mk_encrypted_table(client, headers, sid)

    resp = await client.get(f"{DB}/sessions/{sid}/encryption-config", headers=headers)
    assert resp.status_code == 403, resp.text
    body = resp.json()
    assert body["code"] in ("FORBIDDEN", "HTTP_403")
    assert "dek_hex" not in json.dumps(body)


@pytest.mark.asyncio
async def test_encryption_config_for_operator_is_audited(client, db_session, make_user):
    """A51：ADMIN/OPERATOR 仍可取（沙箱注入路径），但必须留下密钥访问审计。"""
    from app.models.audit_log import AuditLog
    from sqlalchemy import select

    headers, uid = await make_user("operator", "a51_op")
    sid = f"a51op-{uuid.uuid4().hex[:8]}"
    dek_hex = await _mk_encrypted_table(client, headers, sid)

    resp = await client.get(f"{DB}/sessions/{sid}/encryption-config", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["encryption_configs"]["secret"]["dek_hex"] == dek_hex

    rows = (await db_session.execute(
        select(AuditLog).where(AuditLog.action == "sandbox_db.encryption_config_read")
    )).scalars().all()
    matching = [r for r in rows if (r.resource_id or str(r.session_id or "")) == sid
                or sid in json.dumps(r.detail or {})]
    assert matching, [(r.action, r.resource_id) for r in rows]


# ------------------------------------------------------------------------------ A56

@pytest.mark.asyncio
async def test_ttl_sweep_runs_on_non_query_endpoints(client, auth_headers):
    """A56：清扫过去只挂在 /query 上 ⇒ 只建表不查询的会话引擎（含明文与 DEK）永不回收。"""
    stale = f"a56-stale-{uuid.uuid4().hex[:8]}"
    fresh = f"a56-fresh-{uuid.uuid4().hex[:8]}"
    assert (await _create_table(client, auth_headers, stale)).status_code == 200
    assert (await _create_table(client, auth_headers, fresh)).status_code == 200

    sandbox_db.get_engine(stale)._created_at = datetime.now(timezone.utc) - timedelta(hours=2)

    # 一次完全无关的元数据请求就该把过期引擎带走
    resp = await client.get(f"{DB}/sessions/{fresh}/tables", headers=auth_headers)
    assert resp.status_code == 200
    assert stale not in sandbox_db._engines
    assert sandbox_db.get_engine(stale) is None


@pytest.mark.asyncio
async def test_no_parallel_registry_survives_engine_ttl(client, auth_headers):
    """A56：归属登记与 _engines 平行 ⇒ TTL 后仍留残键（无界增长）。"""
    sid = f"a56-owner-{uuid.uuid4().hex[:8]}"
    assert (await _create_table(client, auth_headers, sid)).status_code == 200
    sandbox_db.get_engine(sid)._created_at = datetime.now(timezone.utc) - timedelta(hours=2)

    resp = await client.get(f"{DB}/sessions/{sid}/tables", headers=auth_headers)
    assert resp.status_code == 404  # 引擎已回收，会话也随之消失
    assert _lingering_refs(sandbox_db, sid) == []


@pytest.mark.asyncio
async def test_ownership_check_survives_without_owner_registry(client, make_user):
    """A56：归属并到引擎上之后，非拥有者仍然拿不到别人的引擎。"""
    headers, _ = await make_user("data_provider", "a56_owner2")
    other_headers, _ = await make_user("data_provider", "a56_intruder")
    sid = f"a56-own-{uuid.uuid4().hex[:8]}"
    assert (await _create_table(client, headers, sid)).status_code == 200

    resp = await client.post(f"{DB}/query", json={"session_id": sid, "sql": "SELECT * FROM people"},
                             headers=other_headers)
    assert resp.status_code == 403

    resp = await client.delete(f"{DB}/sessions/{sid}", headers=other_headers)
    assert resp.status_code == 403
