"""Sandbox Database API — DuckDB in-memory SQL execution with masked views + SM4 column encryption."""
import logging
import re
import uuid
from pathlib import Path
from typing import NoReturn

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user, require_roles
from app.core.errors import CDSError
from app.models.contract import Contract
from app.models.user import User, UserRole
from app.models.sandbox_session import SandboxSession
from app.services.audit_service import audit_service
# A95d: 复用发放门（output_policy/A95c）的同一个异常类型与语义 —— 查询期与发放期
# 必须把「取不到分级」当成同一件事来处理，另造一个异常类就会再一次各说各话。
from app.services.output_policy import ClassificationUnavailableError
from app.services.secure_duckdb import (
    MaskRule,
    QueryResult,
    SecureDuckDBEngine,
    allowed_import_roots,
    cleanup_expired_engines,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# In-memory engine registry (per-session)。会话归属直接记在引擎上
# （engine.owner_user_id）：A56 之前另开一个 _engine_owners 平行字典，TTL 清扫只动
# _engines，归属登记永远留着（增长速率由请求量而非会话数决定）。
_engines: dict[str, SecureDuckDBEngine] = {}

ENGINE_TTL_SECONDS = 3600


def sweep_expired_duckdb_engines(max_age_seconds: int = ENGINE_TTL_SECONDS) -> int:
    """TTL 清扫的公开入口（A56）。

    引擎里躺着明文表与列 DEK，回收不能只靠「下一个 sandbox-db 请求」—— 空闲进程
    需要能被启动接线（app/main.py）按周期叫起来。
    """
    return cleanup_expired_engines(_engines, max_age_seconds)


def _as_uuid(value: object) -> uuid.UUID | None:
    """只有能解析成 UUID 的会话 ID 才能进审计表的 session_id 列。"""
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def _can_manage_sandbox_db(user: User) -> bool:
    return user.role in (UserRole.ADMIN, UserRole.OPERATOR)


_IMPORT_GLOB_CHARS = "*?["


def _import_path_violation(raw_path: str) -> str | None:
    """请求体里的导入路径是否越界；越界 ⇒ 原因字符串，合法 ⇒ None（A50）。

    判定与引擎层同源（allowed_import_roots）：拒绝 glob（read_parquet 的 glob = 目录
    枚举）、resolve() 后再比根（符号链接指向根外也算越界）。
    """
    raw = (raw_path or "").strip()
    if not raw:
        return "empty path"
    if any(ch in raw for ch in _IMPORT_GLOB_CHARS):
        return "glob wildcard"
    try:
        resolved = Path(raw).resolve()
    except OSError as e:
        return f"unresolvable path: {e}"
    roots = allowed_import_roots()
    if any(resolved == root or resolved.is_relative_to(root) for root in roots):
        return None
    return "outside import root"


async def _confine_import_path(raw_path: str, *, kind: str, session_id: str, current_user: User) -> None:
    """A50：导入路径在本 API 边界就要被关进平台导入根，不能只指望引擎层兜。

    响应只回语义码与原因，不回显导入根清单 —— 那是平台内部目录拓扑。
    """
    reason = _import_path_violation(raw_path)
    if reason is None:
        return
    logger.warning(
        "[sandbox-db] session=%s user=%s refused %s (%s)", session_id, current_user.id, kind, reason
    )
    await audit_service.log_standalone(
        action="sandbox_db.import_denied",
        resource_type="sandbox_session",
        user_id=current_user.id,
        session_id=_as_uuid(session_id),
        resource_id=session_id,
        detail={"reason": reason, "kind": kind, "path": (raw_path or "")[:500]},
    )
    raise CDSError(
        f"{kind} 必须位于平台导入根内",
        code="FORBIDDEN",
        http_status=403,
        detail={"session_id": session_id, "kind": kind, "reason": reason},
    )


_RELATION_REF_RE = re.compile(r"\b(?:from|join)\s+([A-Za-z_][A-Za-z0-9_$.]*)", re.IGNORECASE)


def _masked_base_tables(engine: SecureDuckDBEngine) -> set[str]:
    return {name.lower() for name in getattr(engine, "_mask_rules", {})}


async def _refuse_masked_view_bypass(
    engine: SecureDuckDBEngine, sql: str, current_user: User, session_id: str
) -> None:
    """A57：基表一旦配了脱敏规则，对非脱敏管理员就不可见 —— 只能查它的 <table>_masked 视图。

    /masked-view 是 ADMIN/OPERATOR 的管理动作，而 /query 只要会话归属；旧实现里同一个
    会话的普通用户 `SELECT * FROM 基表` 就能绕过脱敏。判定按 FROM/JOIN 后的关系名粗筛，
    宁可误拒（例如 CTE 与基表同名）也不放过基表。
    """
    if _can_manage_sandbox_db(current_user):
        return
    guarded = _masked_base_tables(engine)
    if not guarded:
        return
    referenced = {name.lower().rsplit(".", 1)[-1] for name in _RELATION_REF_RE.findall(sql)}
    blocked = sorted(referenced & guarded)
    if not blocked:
        return
    logger.warning(
        "[sandbox-db] session=%s user=%s tried to query masked base table(s) %s",
        session_id, current_user.id, blocked,
    )
    await audit_service.log_standalone(
        action="sandbox_db.masked_view_bypass_denied",
        resource_type="sandbox_session",
        user_id=current_user.id,
        session_id=_as_uuid(session_id),
        resource_id=session_id,
        detail={"tables": blocked, "sql": sql[:500]},
    )
    raise CDSError(
        "该表已配置脱敏规则，请查询其脱敏视图",
        code="FORBIDDEN",
        http_status=403,
        detail={"session_id": session_id, "tables": blocked, "use_view": f"{blocked[0]}_masked"},
    )


async def _managed_sandbox_session(db: AsyncSession, session_id: str) -> SandboxSession | None:
    try:
        session_uuid = uuid.UUID(str(session_id))
    except (TypeError, ValueError):
        return None
    return await db.get(SandboxSession, session_uuid)


async def _authorize_session_engine(
    db: AsyncSession,
    session_id: str,
    current_user: User,
    *,
    create_if_missing: bool = False,
) -> None:
    if _can_manage_sandbox_db(current_user):
        return

    session = await _managed_sandbox_session(db, session_id)
    if session:
        if session.user_id != current_user.id:
            raise HTTPException(status_code=403, detail="Not your sandbox database session")
        return

    engine = _engines.get(session_id)
    owner_id = engine.owner_user_id if engine is not None else None
    if owner_id:
        if owner_id != str(current_user.id):
            raise HTTPException(status_code=403, detail="Not your sandbox database session")
        return

    if create_if_missing:
        return
    raise HTTPException(status_code=404, detail=f"Session {session_id} not found")


async def _get_authorized_engine(
    db: AsyncSession,
    session_id: str,
    current_user: User,
    *,
    create: bool = False,
) -> SecureDuckDBEngine:
    """全部 sandbox-db 端点的唯一入口：TTL 清扫 + 归属校验 + 合约策略挂接。

    A48：策略解析从「fire-and-forget 后台任务、异常无人取回、缺失即放行」改成在
    这里同步完成 —— 本请求的第一条 SQL 之前策略就已到位，挂不上就直接 400 拒绝。
    A56：TTL 清扫也从只挂在 /query 上移到此处，只建表/只导入的会话同样会到期回收。
    """
    sweep_expired_duckdb_engines()

    await _authorize_session_engine(db, session_id, current_user, create_if_missing=create)
    engine = _engines.get(session_id)
    if engine is None:
        if not create:
            raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
        engine = _get_engine(session_id, owner_user_id=str(current_user.id))
    await _resolve_contract_policy(db, session_id, engine, current_user)
    return engine


class CreateTableRequest(BaseModel):
    # A49：默认值必须是完整 UUID —— 8 位 hex 过不了 uuid.UUID()，策略解析会 100% 失败
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    table_name: str = Field(..., min_length=1, max_length=64, pattern="^[a-zA-Z_][a-zA-Z0-9_]*$")
    data: list[dict] = Field(..., min_length=1)


class QueryRequest(BaseModel):
    session_id: str
    sql: str = Field(..., min_length=1, max_length=10000)
    limit: int = Field(default=1000, ge=1, le=10000)


class MaskRuleRequest(BaseModel):
    field_name: str
    mask_type: str = Field(..., pattern="^(HASH|REDACT|GENERALIZE|PASSTHROUGH)$")
    top_k: int = Field(default=10, ge=1, le=1000)


class MaskedViewRequest(BaseModel):
    session_id: str
    table_name: str
    view_name: str | None = None
    rules: list[MaskRuleRequest]


@router.post("/create-table")
async def create_table(
    body: CreateTableRequest,
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR, UserRole.DATA_PROVIDER)),
    db: AsyncSession = Depends(get_db),
):
    """Create an in-memory DuckDB table from JSON data."""
    session_id = body.session_id

    engine = await _get_authorized_engine(db, session_id, current_user, create=True)
    try:
        info = engine.register_table(body.table_name, body.data)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "session_id": session_id,
        "table_name": info.table_name,
        "row_count": info.row_count,
        "column_count": info.column_count,
    }


@router.post("/query")
async def execute_query(
    body: QueryRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Execute a SQL query on a sandbox DuckDB instance."""
    engine = await _get_authorized_engine(db, body.session_id, current_user)
    await _refuse_masked_view_bypass(engine, body.sql, current_user, body.session_id)

    result = engine.execute_query(body.sql, limit=body.limit)
    if not result.success:
        raise HTTPException(status_code=400, detail=result.error)

    return {
        "columns": result.columns,
        "rows": result.rows,
        "row_count": result.row_count,
        "duration_ms": result.duration_ms,
        "truncated": result.truncated,
    }


@router.post("/masked-view")
async def create_masked_view(
    body: MaskedViewRequest,
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR)),
    db: AsyncSession = Depends(get_db),
):
    """Create a masked view with field-level data masking."""
    engine = await _get_authorized_engine(db, body.session_id, current_user)

    rules = [MaskRule(field_name=r.field_name, mask_type=r.mask_type, top_k=r.top_k) for r in body.rules]
    engine.set_mask_rules(body.table_name, rules)

    view_name = engine.create_masked_view(body.table_name, body.view_name)
    return {
        "session_id": body.session_id,
        "view_name": view_name,
        "rules_applied": len(rules),
    }


@router.get("/sessions/{session_id}/tables")
async def list_tables(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List tables in a sandbox session."""
    engine = await _get_authorized_engine(db, session_id, current_user)

    tables = engine.list_tables()
    return [
        {
            "table_name": t.table_name,
            "source": t.source,
            "row_count": t.row_count,
            "column_count": t.column_count,
        }
        for t in tables
    ]


@router.delete("/sessions/{session_id}")
async def close_session(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Close and cleanup a sandbox DuckDB session."""
    await _authorize_session_engine(db, session_id, current_user)
    engine = _engines.pop(session_id, None)
    if engine:
        engine.close()
    return {"session_id": session_id, "closed": True}


# === SM4 Column Encryption + Multi-Source Import ===

class EncryptedTableRequest(BaseModel):
    # A49：同 CreateTableRequest —— 默认会话号必须可解析为 UUID
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    table_name: str = Field(..., min_length=1, max_length=64, pattern="^[a-zA-Z_][a-zA-Z0-9_]*$")
    data: list[dict] = Field(..., min_length=1)
    encrypted_columns: dict[str, str] = Field(
        default_factory=dict,
        description="Map of column_name -> 'sm4-siv'|'sm4-gcm'"
    )
    dek_hex: str | None = Field(None, description="16-byte DEK as hex string. Auto-generated if empty.")


class ImportJsonRequest(BaseModel):
    session_id: str
    table_name: str = Field(..., min_length=1, max_length=64, pattern="^[a-zA-Z_][a-zA-Z0-9_]*$")
    json_path: str
    encrypted_columns: dict[str, str] = Field(default_factory=dict)
    dek_hex: str | None = None


class ImportParquetRequest(BaseModel):
    session_id: str
    table_name: str = Field(..., min_length=1, max_length=64, pattern="^[a-zA-Z_][a-zA-Z0-9_]*$")
    parquet_path: str
    encrypted_columns: dict[str, str] = Field(default_factory=dict)
    dek_hex: str | None = None


class ImportPostgresRequest(BaseModel):
    session_id: str
    table_name: str = Field(..., min_length=1, max_length=64, pattern="^[a-zA-Z_][a-zA-Z0-9_]*$")
    pg_connstr: str
    pg_table: str
    encrypted_columns: dict[str, str] = Field(default_factory=dict)
    dek_hex: str | None = None


class ImportS3Request(BaseModel):
    session_id: str
    table_name: str = Field(..., min_length=1, max_length=64, pattern="^[a-zA-Z_][a-zA-Z0-9_]*$")
    s3_url: str
    s3_key_id: str = ""
    s3_secret: str = ""
    file_format: str = Field("parquet", pattern="^(parquet|csv|json)$")
    encrypted_columns: dict[str, str] = Field(default_factory=dict)
    dek_hex: str | None = None


class EncryptedQueryRequest(BaseModel):
    session_id: str
    sql: str = Field(..., min_length=1, max_length=10000)
    limit: int = Field(default=1000, ge=1, le=10000)


def _get_engine(session_id: str, *, owner_user_id: str | None = None) -> SecureDuckDBEngine:
    """取/建会话引擎 —— 纯对象生命周期，不再夹带策略装配。

    A48：策略装配以前在这里靠 `loop.create_task(_attach_policy(...))` 开干：不
    await、无 done_callback，同一条请求紧接着就 register_table / execute_query，策略
    在不在全看运气；现在由 _get_authorized_engine 同步解析。
    A56：会话归属记在引擎上，与 TTL 回收同一生命周期。
    """
    if session_id not in _engines:
        _engines[session_id] = SecureDuckDBEngine(session_id, mode="memory")
    engine = _engines[session_id]
    if owner_user_id and not engine.owner_user_id:
        engine.owner_user_id = owner_user_id
    return engine


async def _refuse_without_policy(
    current_user: User,
    session_id: str,
    reason: str,
    *,
    contract_id: str | None = None,
) -> NoReturn:
    """会话声明了治理合约却挂不上策略 ⇒ 拒绝数据操作，并留下 WARNING + 审计。

    宁可拒绝服务也不降级执行：合约策略是本产品对外的核心承诺，「查不到策略」绝不
    能当成「没有策略、放行」。错码沿用 docs/error-codes.md 已有的 POLICY_COMPILE_ERROR
    （策略未下发 ⇒ 不下发执行，fail-closed）。
    """
    logger.warning(
        "[sandbox-db] policy gate refused session=%s user=%s: %s (contract_id=%s)",
        session_id, current_user.id, reason, contract_id,
    )
    # 拒绝类审计必须写在本请求事务之外：抛异常后 get_db 会回滚请求会话。
    await audit_service.log_standalone(
        action="sandbox_db.policy_attach_denied",
        resource_type="sandbox_session",
        user_id=current_user.id,
        session_id=_as_uuid(session_id),
        resource_id=session_id,
        detail={"reason": reason, "contract_id": contract_id},
    )
    raise CDSError(
        "该会话的治理合约策略未能挂接，已拒绝执行数据操作",
        code="POLICY_COMPILE_ERROR",
        http_status=400,
        detail={"session_id": session_id, "contract_id": contract_id, "reason": reason},
    )


async def _field_classifications_for_contract(db: AsyncSession, contract) -> dict[str, int]:
    """汇总合约所关联资源的字段分级 raw {field: level}（A95b/A95d）。

    取数路径与出沙箱发放门（output_policy._field_rules_for_contract）一致：
    contract.product_ids → DataProduct.resource_id → DataResource.field_classifications。
    多产品同字段取**更高分级**（fail-closed）；编译留给引擎内的
    policy_compiler.field_rules_from_classifications（单一事实源）。

    A95d（最后一处 fail-open）：返回 {} 有两种完全不同的含义 —— 「这份数据确实没有
    分级」（合法，原样放行）与「分级不可知」（不可放行）。旧实现把后者也返回 {}：
    product_ids 里不可解析的条目被 `continue` 跳过、任何取数异常被宽 except 吞成
    `{}` ⇒ 查询期字段门以为「该合约没有字段分级」，deny/mask 列原文返回。现在两种
    含义分开：**只有**正面确认「没有分级」才返回 {}；不可解析 id / 悬空资源引用 /
    取数异常一律抛 ClassificationUnavailableError，由调用方拒绝本次数据操作。
    与发放门同一判定边界：产品行本身不存在（ghost id）仍是「没有可分级资源」，
    不是「不可知」—— 这是 output_policy 已落地并被 tests/test_ocr_a95c_* 钉住的语义。
    """
    merged: dict[str, int] = {}
    product_ids = contract.product_ids or []
    if not product_ids:
        return {}

    # uuid.UUID，不是 str：SQLite 的 UUID 绑定读 value.hex，喂 str 会在绑定期抛
    # AttributeError（A95c 的红），PostgreSQL 只是靠隐式转换侥幸匹配上。
    wanted: list[uuid.UUID] = []
    for pid in product_ids:
        try:
            wanted.append(pid if isinstance(pid, uuid.UUID) else uuid.UUID(str(pid)))
        except (TypeError, ValueError) as e:
            raise ClassificationUnavailableError(
                f"contract product_ids contains a non-UUID entry: {pid!r}"
            ) from e

    try:
        from app.models.data_product import DataProduct
        from app.models.data_resource import DataResource

        result = await db.execute(select(DataProduct).where(DataProduct.id.in_(wanted)))
        for product in result.scalars().all():
            if not product.resource_id:
                continue
            resource = await db.get(DataResource, product.resource_id)
            if resource is None:
                raise ClassificationUnavailableError(
                    f"product {product.id} references missing resource {product.resource_id}"
                )
            for field, raw in (resource.field_classifications or {}).items():
                try:
                    level = int(raw)
                except (TypeError, ValueError):
                    continue
                name = str(field)
                if level > merged.get(name, -1):
                    merged[name] = level
    except ClassificationUnavailableError:
        raise
    except Exception as e:
        raise ClassificationUnavailableError(f"{type(e).__name__}: {e}") from e
    return merged


async def _resolve_contract_policy(
    db: AsyncSession,
    session_id: str,
    engine: SecureDuckDBEngine,
    current_user: User,
) -> None:
    """在本请求的第一条 SQL 之前把合约策略挂上（A48/A49）。

    三态，全部同步解析，不再有「无人 await 的后台任务」：
      · 会话行不存在 / 无 contract_id → 无治理合约，登记 not_required（合法无策略路径）
      · 有 contract_id                 → 必须编译并挂上策略，否则 400 拒绝
      · 解析本身出错（会话行读失败、contract_id 不是平台 UUID、合约行缺失、编译
        抛异常）→ 一律拒绝：不能把「查不到」当成「没有合约」而放行
    """
    try:
        session = await _managed_sandbox_session(db, session_id)
    except Exception as e:
        # DB 读失败 ≠ 没有会话：到底有没有合约无法判定 ⇒ fail-closed
        await _refuse_without_policy(
            current_user, session_id, f"session lookup failed: {e}",
        )

    if session is None:
        # sandbox-db 自管的分析会话：没有会话行就没有合约可治理
        engine.mark_policy_not_required("no sandbox_session row for this id")
        return

    engine.set_sandbox_level(session.sandbox_level)
    if not session.contract_id:
        engine.mark_policy_not_required("session has no governing contract")
        return

    contract_id = str(session.contract_id)
    engine.require_contract_policy()  # 先关闸，再尝试挂策略

    try:
        contract_uuid = uuid.UUID(contract_id)
    except (TypeError, ValueError):
        await _refuse_without_policy(
            current_user, session_id, "contract_id is not a platform contract UUID",
            contract_id=contract_id,
        )

    try:
        contract = await db.get(Contract, contract_uuid)
    except Exception as e:
        await _refuse_without_policy(
            current_user, session_id, f"contract lookup failed: {e}", contract_id=contract_id,
        )
    if contract is None:
        await _refuse_without_policy(
            current_user, session_id, "governing contract row not found", contract_id=contract_id,
        )

    try:
        # 条款映射复用 contract_service.compile_policy（单一事实源），再按会话模式
        # 编译成引擎能逐条判定的 PolicyBundle。旧代码调的 policy_compiler.compile_contract()
        # 全仓根本不存在 ⇒ 每次 AttributeError 落进 debug 日志，策略从来没挂上过。
        from app.services.contract_service import contract_service
        from app.services.policy_compiler import policy_compiler
        bundle = policy_compiler.compile_for_mode(
            contract_service.compile_policy(contract),
            getattr(session, "sandbox_mode", None) or "structured_query",
        )
    except Exception as e:
        await _refuse_without_policy(
            current_user, session_id, f"policy compile failed: {e}", contract_id=contract_id,
        )

    engine.set_policy(bundle)

    # A95b（A95 接线转交）：compile_for_mode 的 5 项 PDP 不读字段，字段级控制
    # 原本只在出沙箱网关生效；这里把资源分级喂给引擎，查询期才能同样拒 deny_out
    # 列 / 掩码 mask 列。不传 ⇒ 引擎修好了也没有分类可用，等于没修。
    #
    # A95d：分级**取不到**不能当成「没有分级」继续执行 —— 那等于 deny/mask 列在查询期
    # 原文出密。与同族的其它拒绝分支一致：响亮 ERROR + 请求事务外的审计 + 400 拒绝，
    # 绝不「捕获后打 warning 继续放行」。
    try:
        field_classifications = await _field_classifications_for_contract(db, contract)
    except ClassificationUnavailableError as e:
        logger.error(
            "[sandbox-db] SECURITY: field classification lookup failed for contract %s (%s) — "
            "query-time field ACL is UNKNOWN, so the data operation is REFUSED instead of "
            "running as if this data carried no classifications",
            contract_id, e,
        )
        await _refuse_without_policy(
            current_user, session_id,
            f"field classification unavailable: {e}", contract_id=contract_id,
        )

    engine.set_field_classifications(field_classifications)


def _parse_dek(dek_hex: str | None) -> bytes | None:
    """Parse DEK from hex string, or return None for auto-generation."""
    if not dek_hex:
        return None
    return bytes.fromhex(dek_hex)


def get_engine(session_id: str) -> SecureDuckDBEngine | None:
    """Get existing engine for a session (no auto-create). Used by sandbox_sessions."""
    return _engines.get(session_id)


def get_encryption_config_for_session(session_id: str) -> dict | None:
    """Get encryption config dict for sandbox injection.

    Returns the first encryption config found (DEK + encrypted columns).
    Used by sandbox_sessions.py to inject SM4 DEK into bwrap sandbox.
    """
    engine = _engines.get(session_id)
    if not engine:
        return None
    configs = engine.get_all_encryption_configs()
    if not configs:
        return None
    # Return the first config (most sessions have one encrypted table)
    first_table = next(iter(configs))
    return configs[first_table]


@router.post("/create-encrypted-table")
async def create_encrypted_table(
    body: EncryptedTableRequest,
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR, UserRole.DATA_PROVIDER)),
    db: AsyncSession = Depends(get_db),
):
    """Create a DuckDB table with SM4 column-level encryption.

    Sensitive columns are encrypted with SM4-SIV (deterministic, supports equality queries)
    or SM4-GCM (randomized, maximum security). Encrypted columns are stored as BLOB.
    """
    engine = await _get_authorized_engine(db, body.session_id, current_user, create=True)
    dek = _parse_dek(body.dek_hex)

    try:
        info = engine.register_table_encrypted(
            body.table_name, body.data,
            encrypted_columns=body.encrypted_columns,
            dek=dek,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    meta = info.columns_meta if hasattr(info, 'columns_meta') else {}
    return {
        "session_id": body.session_id,
        "table_name": info.table_name,
        "row_count": info.row_count,
        "column_count": info.column_count,
        "encrypted_columns": meta.get("encrypted_columns", {}),
        "key_id": meta.get("key_id", ""),
    }


@router.post("/import/json")
async def import_json(
    body: ImportJsonRequest,
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR, UserRole.DATA_PROVIDER)),
    db: AsyncSession = Depends(get_db),
):
    """Import a JSON file into DuckDB with optional SM4 column encryption."""
    engine = await _get_authorized_engine(db, body.session_id, current_user, create=True)
    await _confine_import_path(body.json_path, kind="json_path", session_id=body.session_id, current_user=current_user)
    dek = _parse_dek(body.dek_hex)

    try:
        info = engine.register_json(
            body.table_name, body.json_path,
            encrypted_columns=body.encrypted_columns,
            dek=dek,
        )
    except PermissionError as e:
        # A50：引擎层白名单兜住了 API 边界没见过的形状 —— 同样只回语义码，不回显导入根
        logger.warning("[sandbox-db] session=%s engine refused json_path: %s", body.session_id, e)
        raise CDSError(
            "json_path 必须位于平台导入根内",
            code="FORBIDDEN",
            http_status=403,
            detail={"session_id": body.session_id, "kind": "json_path", "reason": "engine_import_root_check"},
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "session_id": body.session_id,
        "table_name": info.table_name,
        "row_count": info.row_count,
        "column_count": info.column_count,
    }


@router.post("/import/parquet")
async def import_parquet(
    body: ImportParquetRequest,
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR, UserRole.DATA_PROVIDER)),
    db: AsyncSession = Depends(get_db),
):
    """Import a Parquet file into DuckDB with optional SM4 column encryption."""
    engine = await _get_authorized_engine(db, body.session_id, current_user, create=True)
    await _confine_import_path(body.parquet_path, kind="parquet_path", session_id=body.session_id, current_user=current_user)
    dek = _parse_dek(body.dek_hex)

    try:
        info = engine.register_parquet(
            body.table_name, body.parquet_path,
            encrypted_columns=body.encrypted_columns,
            dek=dek,
        )
    except PermissionError as e:
        # A50：引擎层白名单兜住了 API 边界没见过的形状 —— 同样只回语义码，不回显导入根
        logger.warning("[sandbox-db] session=%s engine refused parquet_path: %s", body.session_id, e)
        raise CDSError(
            "parquet_path 必须位于平台导入根内",
            code="FORBIDDEN",
            http_status=403,
            detail={"session_id": body.session_id, "kind": "parquet_path", "reason": "engine_import_root_check"},
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "session_id": body.session_id,
        "table_name": info.table_name,
        "row_count": info.row_count,
        "column_count": info.column_count,
    }


@router.post("/import/postgres")
async def import_postgres(
    body: ImportPostgresRequest,
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR)),
    db: AsyncSession = Depends(get_db),
):
    """Import a PostgreSQL table via postgres_scanner with optional SM4 encryption."""
    engine = await _get_authorized_engine(db, body.session_id, current_user, create=True)
    dek = _parse_dek(body.dek_hex)

    try:
        info = engine.register_from_postgres(
            body.table_name, body.pg_connstr, body.pg_table,
            encrypted_columns=body.encrypted_columns,
            dek=dek,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "session_id": body.session_id,
        "table_name": info.table_name,
        "row_count": info.row_count,
        "column_count": info.column_count,
    }


@router.post("/import/s3")
async def import_s3(
    body: ImportS3Request,
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR)),
    db: AsyncSession = Depends(get_db),
):
    """Import data from S3/MinIO via httpfs with optional SM4 encryption."""
    engine = await _get_authorized_engine(db, body.session_id, current_user, create=True)
    dek = _parse_dek(body.dek_hex)

    try:
        info = engine.register_from_s3(
            body.table_name, body.s3_url,
            s3_key_id=body.s3_key_id, s3_secret=body.s3_secret,
            file_format=body.file_format,
            encrypted_columns=body.encrypted_columns,
            dek=dek,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "session_id": body.session_id,
        "table_name": info.table_name,
        "row_count": info.row_count,
        "column_count": info.column_count,
    }


@router.post("/query-decrypted")
async def query_decrypted(
    body: EncryptedQueryRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Execute a query with automatic decryption of SM4-encrypted columns.

    Transparently decrypts SM4-SIV and SM4-GCM encrypted columns in query results.

    A51：这是列级加密的**明文出口**，与发放 DEK 同级别的敏感面 —— 旧实现只要
    get_current_user + 会话归属，等于会话拥有者（角色不限，含 DATA_CONSUMER）可以经
    HTTP 把整表明文拉走，列级加密对其失效。收到 ADMIN/OPERATOR（与 /encryption-config
    同一角色门）并留审计；被拒的尝试写在本请求事务之外。
    """
    engine = await _get_authorized_engine(db, body.session_id, current_user)

    if not _can_manage_sandbox_db(current_user):
        logger.warning(
            "[sandbox-db] session=%s user=%s (%s) denied decrypted query",
            body.session_id, current_user.id, current_user.role,
        )
        await audit_service.log_standalone(
            action="sandbox_db.decrypted_query_denied",
            resource_type="sandbox_session",
            user_id=current_user.id,
            session_id=_as_uuid(body.session_id),
            resource_id=body.session_id,
            detail={"role": str(current_user.role), "sql": body.sql[:500]},
        )
        raise CDSError(
            "解密查询仅限 ADMIN/OPERATOR",
            code="FORBIDDEN",
            http_status=403,
            detail={"session_id": body.session_id, "reason": "plaintext_export_requires_admin"},
        )

    result = engine.execute_query_decrypted(body.sql, limit=body.limit)
    if not result.success:
        raise HTTPException(status_code=400, detail=result.error)

    return {
        "columns": result.columns,
        "rows": result.rows,
        "row_count": result.row_count,
        "duration_ms": result.duration_ms,
        "truncated": result.truncated,
    }


@router.get("/sessions/{session_id}/encryption-metadata")
async def get_encryption_metadata(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get encryption metadata for all tables in a session."""
    engine = await _get_authorized_engine(db, session_id, current_user)

    metadata = {}
    for table in engine.list_tables():
        meta = engine.get_encryption_metadata(table.table_name)
        if meta:
            metadata[table.table_name] = meta

    return {"session_id": session_id, "encrypted_tables": metadata}


@router.get("/sessions/{session_id}/encryption-config")
async def get_encryption_config(
    session_id: str,
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR)),
    db: AsyncSession = Depends(get_db),
):
    """Get encryption config for sandbox injection (DEK hex + encrypted column map).

    A51：这个响应里带**明文列 DEK**。执行层（sandbox_sessions.py / session_stream.py）
    走的是进程内 get_encryption_config_for_session()，不经这个 HTTP 口子；旧实现只要
    get_current_user，等于任何会话拥有者（含 DATA_CONSUMER）都能把列级 SM4 密钥捐
    出去离线解密全部落盘密文。现在收到 ADMIN/OPERATOR，且每次读取都留审计。
    """
    engine = await _get_authorized_engine(db, session_id, current_user)

    configs = engine.get_all_encryption_configs()
    await audit_service.log(
        db,
        action="sandbox_db.encryption_config_read",
        resource_type="sandbox_session",
        user_id=current_user.id,
        session_id=_as_uuid(session_id),
        resource_id=session_id,
        detail={"tables": sorted(configs), "table_count": len(configs)},
    )
    return {"session_id": session_id, "encryption_configs": configs}


class ImportPostgresSandboxRequest(BaseModel):
    session_id: str
    table_name: str = Field(..., min_length=1, max_length=64, pattern="^[a-zA-Z_][a-zA-Z0-9_]*$")
    pg_connstr: str
    pg_table: str
    encrypted_columns: dict[str, str] = Field(default_factory=dict)
    dek_hex: str | None = None
    network_allowed: bool = Field(default=False, description="Must be true; sandbox network policy must allow the target host.")


class ImportS3SandboxRequest(BaseModel):
    session_id: str
    table_name: str = Field(..., min_length=1, max_length=64, pattern="^[a-zA-Z_][a-zA-Z0-9_]*$")
    s3_url: str
    s3_key_id: str = ""
    s3_secret: str = ""
    file_format: str = Field("parquet", pattern="^(parquet|csv|json)$")
    encrypted_columns: dict[str, str] = Field(default_factory=dict)
    dek_hex: str | None = None
    network_allowed: bool = Field(default=False, description="Must be true; sandbox network policy must allow the target endpoint.")


@router.post("/import/postgres-sandbox")
async def import_postgres_sandbox(
    body: ImportPostgresSandboxRequest,
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR)),
    db: AsyncSession = Depends(get_db),
):
    """Import PostgreSQL data with sandbox network policy enforcement.

    Requires network_allowed=true — the caller must verify that the sandbox
    network policy allows outbound connections to the PostgreSQL host.
    """
    engine = await _get_authorized_engine(db, body.session_id, current_user, create=True)
    dek = _parse_dek(body.dek_hex)

    try:
        info = engine.register_from_postgres_sandbox(
            body.table_name, body.pg_connstr, body.pg_table,
            encrypted_columns=body.encrypted_columns,
            dek=dek,
            network_allowed=body.network_allowed,
        )
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "session_id": body.session_id,
        "table_name": info.table_name,
        "row_count": info.row_count,
        "column_count": info.column_count,
    }


@router.post("/import/s3-sandbox")
async def import_s3_sandbox(
    body: ImportS3SandboxRequest,
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR)),
    db: AsyncSession = Depends(get_db),
):
    """Import S3/MinIO data with sandbox network policy enforcement.

    Requires network_allowed=true — the caller must verify that the sandbox
    network policy allows outbound connections to the S3 endpoint.
    """
    engine = await _get_authorized_engine(db, body.session_id, current_user, create=True)
    dek = _parse_dek(body.dek_hex)

    try:
        info = engine.register_from_s3_sandbox(
            body.table_name, body.s3_url,
            s3_key_id=body.s3_key_id, s3_secret=body.s3_secret,
            file_format=body.file_format,
            encrypted_columns=body.encrypted_columns,
            dek=dek,
            network_allowed=body.network_allowed,
        )
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "session_id": body.session_id,
        "table_name": info.table_name,
        "row_count": info.row_count,
        "column_count": info.column_count,
    }
