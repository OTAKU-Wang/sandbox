import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.errors import CDSError
from app.core.deps import get_business_user
from app.models.contract import Contract, WorkloadEnv
from app.models.notebook import Notebook, NotebookStatus
from app.models.sandbox_session import SandboxSession
from app.models.user import User
from app.schemas.flow import CellExecuteRequest, NotebookCreate, NotebookOut, NotebookUpdate
from app.services import notebook_service as nb
from app.services.audit_service import audit_service

router = APIRouter()
logger = logging.getLogger(__name__)


def _clean(result: dict[str, Any]) -> dict[str, Any]:
    keys = ("ok", "backend", "stateful", "stdout", "stderr", "repr", "exit_code", "output_blocked", "blocked_reason", "degraded_reason", "kernel_error", "skipped_empty")
    out = {}
    for key in keys:
        if key in result and result[key] is not None:
            out[key] = result[key]
    out.setdefault("ok", False)
    out.setdefault("exit_code", -1)
    return out


def _code_digest(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()[:32]


# ── 开发任务字段（SS-18 §8 P0-①）────────────────────────────────────────────
# UI 说 GB、库里存 MB：调试 2-32 GiB、执行 2-64 GiB（产品档位），且都不得超过合约
# 已批准的 sandbox_resource_spec.memory_mb —— 与 compute 的规格覆盖同一口径：只向下
# 收窄、不提供比配额更高的档位——与 compute 的规格覆盖同源（app/api/compute.py:100）。
MEMORY_MIN_MB = 2 * 1024
DEBUG_MEMORY_MAX_MB = 32 * 1024
EXEC_MEMORY_MAX_MB = 64 * 1024
_TASK_NO_ATTEMPTS = 3
_TASK_NO_SCAN_LIMIT = 999  # 当日任务编号上限（NNN 三位）


def _validation_error(field: str, msg: str) -> CDSError:
    # 与 FastAPI 自身校验失败同一契约：code=VALIDATION_ERROR / 422 / detail.errors 明细。
    return CDSError(
        msg,
        code="VALIDATION_ERROR",
        http_status=422,
        detail={"errors": [{"loc": ["body", field], "msg": msg, "type": "value_error"}]},
    )


async def _authorized_resource_ids(db: AsyncSession, contract: Any) -> set[str]:
    """合约已授权的数据资源 id 集（与 jupyter_gateway.inject_contract_data 同一推导链：
    contract.product_ids → DataProduct.resource_id）。"""
    from app.models.data_product import DataProduct

    pids = [uuid.UUID(str(p)) for p in (getattr(contract, "product_ids", None) or [])]
    if not pids:
        return set()
    rows = await db.execute(select(DataProduct.resource_id).where(DataProduct.id.in_(pids)))
    return {str(row[0]) for row in rows if row[0]}


async def _resolve_data_resource_ids(
    db: AsyncSession, contract: Any, requested: list[uuid.UUID] | None,
) -> list[str] | None:
    """把图 2-2「已选择数据」收敛到合约授权集：越权直接 422，绝不静默裁剪或默认扩权。"""
    if requested is None:
        return None
    picked = sorted({str(x) for x in requested})
    if not picked:
        return []
    if contract is None:
        raise _validation_error("data_resource_ids", "未关联合约时不能指定数据资源子集")
    allowed = await _authorized_resource_ids(db, contract)
    outside = [x for x in picked if x not in allowed]
    if outside:
        raise _validation_error("data_resource_ids", f"以下数据资源未在合约授权范围内：{', '.join(outside)}")
    return picked


def _memory_ceiling(contract: Any, kind_max_mb: int) -> tuple[int, int | None]:
    """返回 (生效上限 MB, 合约规格值或 None)。合约未写规格时只有产品档位上限。"""
    spec = (getattr(contract, "sandbox_resource_spec", None) or {})
    approved = spec.get("memory_mb")
    if isinstance(approved, int) and not isinstance(approved, bool) and approved > 0:
        return min(kind_max_mb, approved), approved
    return kind_max_mb, None


def _resolve_task_memory(contract: Any, debug_mb: int | None, exec_mb: int | None) -> tuple[int | None, int | None]:
    """校验任务内存并回填执行内存默认值（默认继承调试内存，可更高）。

    两项都不传 = 未指定：运行时沿用合约规格（SS-15 既有行为，不改旧客户端语义）。
    """
    if debug_mb is None and exec_mb is None:
        return None, None
    if debug_mb is None:
        raise _validation_error("exec_memory_mb", "执行内存必须与调试内存一起提交")
    if debug_mb < MEMORY_MIN_MB:
        raise _validation_error("debug_memory_mb", f"调试内存不得低于 {MEMORY_MIN_MB // 1024} GiB")
    debug_ceiling, debug_approved = _memory_ceiling(contract, DEBUG_MEMORY_MAX_MB)
    if debug_mb > DEBUG_MEMORY_MAX_MB:
        raise _validation_error("debug_memory_mb", f"调试内存不得超过产品档位上限 {DEBUG_MEMORY_MAX_MB // 1024} GiB")
    if debug_mb > debug_ceiling:
        raise CDSError(
            f"debug_memory_mb={debug_mb} 超出合约已批准的规格上限 {debug_ceiling}",
            code="RESOURCE_OVERRIDE_EXCEEDED",
            http_status=400,
            detail={"contract_memory_mb": debug_approved, "effective_ceiling_mb": debug_ceiling},
        )
    if exec_mb is None:
        return debug_mb, debug_mb  # 执行内存默认继承调试内存
    if exec_mb < debug_mb:
        raise _validation_error("exec_memory_mb", "执行内存不得低于调试内存")
    if exec_mb > EXEC_MEMORY_MAX_MB:
        raise _validation_error("exec_memory_mb", f"执行内存不得超过产品档位上限 {EXEC_MEMORY_MAX_MB // 1024} GiB")
    exec_ceiling, exec_approved = _memory_ceiling(contract, EXEC_MEMORY_MAX_MB)
    if exec_mb > exec_ceiling:
        raise CDSError(
            f"exec_memory_mb={exec_mb} 超出合约已批准的规格上限 {exec_ceiling}",
            code="RESOURCE_OVERRIDE_EXCEEDED",
            http_status=400,
            detail={"contract_memory_mb": exec_approved, "effective_ceiling_mb": exec_ceiling},
        )
    return debug_mb, exec_mb


async def _next_task_no(db: AsyncSession) -> str:
    """TASK-YYYYMMDD-NNN（UTC 日期 + 当日序号）。同瞬并发由唯一索引兜底。"""
    prefix = "TASK-" + datetime.now(timezone.utc).strftime("%Y%m%d") + "-"
    res = await db.execute(select(func.count()).select_from(Notebook).where(Notebook.task_no.like(prefix + "%")))
    seq = int(res.scalar_one() or 0) + 1
    while seq <= _TASK_NO_SCAN_LIMIT:
        candidate = prefix + format(seq, "03d")
        taken = (await db.execute(select(Notebook.id).where(Notebook.task_no == candidate))).first()
        if taken is None:
            return candidate
        seq += 1
    raise _validation_error("task_no", "当日任务编号已用尽，请联系管理员")


async def _insert_with_task_no(db: AsyncSession, notebook: Notebook) -> None:
    """写入并保证 task_no 唯一：先探测空位，再用保存点重试吸收同瞬碰撞（不落 500）。

    保存点而非事务回滚：失败只吞掉这一次 INSERT，请求事务（含鉴权用户与后续审计）继续可用。
    """
    for attempt in range(_TASK_NO_ATTEMPTS):
        notebook.task_no = await _next_task_no(db)
        savepoint = await db.begin_nested()
        try:
            db.add(notebook)
            await db.flush()
            await savepoint.commit()
            return
        except IntegrityError:
            await savepoint.rollback()
            if attempt == _TASK_NO_ATTEMPTS - 1:
                raise


def _as_document(content: dict[str, Any] | str) -> dict[str, Any]:
    """文档本体或它的 JSON 字符串都接受（编辑器存的是字符串），入库前统一归一化为 nbformat 4 文档。"""
    raw: Any = content
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"notebook content 不是合法 JSON：{str(exc)[:120]}") from exc
    try:
        return nb.normalize_notebook(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"notebook content 不合规范：{exc}") from exc


async def _audit_notebook(db: AsyncSession, user: User, notebook: Notebook, action: str, detail: dict[str, Any]) -> None:
    await audit_service.log(
        db,
        action=action,
        resource_type="notebook",
        user_id=user.id,
        resource_id=str(notebook.id),
        detail=detail,
    )


async def _load_notebook(db: AsyncSession, notebook_id: uuid.UUID, user: User) -> Notebook:
    res = await db.execute(select(Notebook).where(Notebook.id == notebook_id))
    notebook = res.scalar_one_or_none()
    if notebook is None:
        raise HTTPException(status_code=404, detail="notebook not found")
    if notebook.user_id != user.id and getattr(user, "role", None) not in ("admin", "platform_admin"):
        raise HTTPException(status_code=403, detail="not your notebook")
    return notebook


async def _session_state(db: AsyncSession, session_id: uuid.UUID | None) -> SandboxSession | None:
    if session_id is None:
        return None
    from app.services.session_state_machine import session_state_machine

    res = await db.execute(select(SandboxSession).where(SandboxSession.id == session_id))
    session = res.scalar_one_or_none()
    if session is None:
        return None
    if not session_state_machine.is_active(session.status):
        return None
    return session


async def _ensure_dev_session(db: AsyncSession, notebook: Notebook, user: User):
    from sqlalchemy.orm.exc import ObjectDeletedError

    from app.api.sandbox_sessions import create_sandbox_session
    from app.core.errors import CDSError
    from app.models.contract import Contract
    from app.schemas.sandbox_session import SandboxSessionCreate

    existing = await _session_state(db, notebook.session_id)
    if existing is not None:
        return existing, False

    contract = None
    if notebook.contract_id is not None:
        cres = await db.execute(select(Contract).where(Contract.id == notebook.contract_id))
        contract = cres.scalar_one_or_none()
    if contract is None or not contract.product_ids:
        raise HTTPException(status_code=400, detail="Notebook 未关联有效合约或该合约没有数据产品，无法创建在线开发沙箱")

    spec = contract.sandbox_resource_spec or {}
    from app.services.workflow_service import resolve_sandbox_mode

    # SS-19: 开发环等级原本写死 l2c（SS-13 立场：在线开发只在 Cube MicroVM 里发生）。
    # 没 Cube 节点的部署里这会让开发环整条路直接断掉，所以等级改成可配：
    # 默认仍是 l2c（产品立场不变），dev compose 显式降到 L0（进程隔离）并在前端标徒。
    # 降级只能到合约 allowed_sandbox_levels 允许的范围（create_sandbox_session 里把门）。
    created = await create_sandbox_session(
        body=SandboxSessionCreate(
            data_product_id=uuid.UUID(str(contract.product_ids[0])),
            sandbox_level=get_settings().DEV_SANDBOX_LEVEL,
            sandbox_mode=resolve_sandbox_mode(contract, "product_dev"),
            contract_id=str(contract.id),
            timeout_seconds=int(spec.get("timeout_seconds") or 3600),
            resource_limits=spec or None,
        ),
        db=db,
        current_user=user,
    )
    created_id = uuid.UUID(str(created.id))
    sres = await db.execute(select(SandboxSession).where(SandboxSession.id == created_id))
    session = sres.scalar_one_or_none()
    if session is None:
        rows = (await db.execute(
            select(SandboxSession.id, SandboxSession.status).order_by(SandboxSession.created_at.desc()).limit(5)
        )).all()
        logger.warning(
            "[flow] dev session lookup miss after create: created_id=%s type=%s recent=%s",
            created_id, type(created).__name__, rows,
        )
        raise CDSError(
            "在线开发沙箱创建后无法定位会话记录，请重试或联系管理员",
            code="SANDBOX_SESSION_MISSING",
            http_status=503,
            detail={
                "created_id": str(created_id),
                "created_type": type(created).__name__,
                "recent": [[str(rid), stt] for rid, stt in rows],
            },
        )
    session.env = WorkloadEnv.TEST.value
    try:
        await db.refresh(notebook)
    except ObjectDeletedError as exc:
        raise CDSError(
            "开发沙箱会话已创建，但工作台记录当前不可写，请重试",
            code="NOTEBOOK_UNAVAILABLE",
            http_status=503,
            detail={"notebook_id": str(notebook.id), "session_id": str(created_id)},
        ) from exc
    notebook.session_id = created_id
    await db.flush()
    await audit_service.log(
        db, action="notebook.dev_session_created", resource_type="notebook",
        user_id=user.id, resource_id=str(notebook.id),
        detail={"session_id": str(created_id), "env": WorkloadEnv.TEST.value},
    )
    return session, True


def _executor(db: AsyncSession, session_id: uuid.UUID, user: User, language: str):
    from app.api.sandbox_sessions import execute_in_sandbox
    from app.schemas.sandbox_session import SandboxExecuteRequest

    async def _exec(code: str) -> dict[str, Any]:
        return await execute_in_sandbox(
            session_id=session_id,
            body=SandboxExecuteRequest(code=code, language=language, trusted_runner=True),
            db=db,
            current_user=user,
        )

    return _exec


@router.post("", response_model=NotebookOut, status_code=201)
async def create_notebook(
    body: NotebookCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_business_user),
):
    doc = _as_document(body.content) if body.content else nb.make_notebook(body.title, language=body.language)
    contract = None
    if body.contract_id is not None:
        cres = await db.execute(select(Contract).where(Contract.id == body.contract_id))
        contract = cres.scalar_one_or_none()
        if contract is None:
            raise HTTPException(status_code=404, detail="contract not found")
    debug_memory_mb, exec_memory_mb = _resolve_task_memory(contract, body.debug_memory_mb, body.exec_memory_mb)
    data_resource_ids = await _resolve_data_resource_ids(db, contract, body.data_resource_ids)
    notebook = Notebook(
        user_id=current_user.id,
        contract_id=body.contract_id,
        session_id=body.session_id,
        title=body.title,
        description=body.description,
        path=body.path,
        language=body.language,
        content=doc,
        content_hash=nb.notebook_hash(doc),
        debug_method=body.debug_method.value if body.debug_method else None,
        export_mode=body.export_mode.value if body.export_mode else None,
        debug_memory_mb=debug_memory_mb,
        exec_memory_mb=exec_memory_mb,
        data_resource_ids=data_resource_ids,
    )
    await _insert_with_task_no(db, notebook)
    await db.refresh(notebook)
    await audit_service.log(db, action="notebook.created", resource_type="notebook", user_id=current_user.id, resource_id=str(notebook.id), detail={
        "title": body.title,
        "path": body.path,
        "task_no": notebook.task_no,
        "debug_method": notebook.debug_method,
        "export_mode": notebook.export_mode,
        "debug_memory_mb": notebook.debug_memory_mb,
        "exec_memory_mb": notebook.exec_memory_mb,
        "data_resource_ids": notebook.data_resource_ids,
    })
    return notebook


@router.get("")
async def list_notebooks(
    contract_id: uuid.UUID | None = None,
    status_filter: str | None = Query(None, alias="status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_business_user),
):
    stmt = select(Notebook).where(Notebook.user_id == current_user.id)
    if contract_id is not None:
        stmt = stmt.where(Notebook.contract_id == contract_id)
    if status_filter:
        stmt = stmt.where(Notebook.status == status_filter)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    res = await db.execute(stmt.order_by(Notebook.updated_at.desc()).offset((page - 1) * page_size).limit(page_size))
    return {"items": [NotebookOut.model_validate(n) for n in res.scalars().all()], "total": total, "page": page, "page_size": page_size}


@router.get("/{notebook_id}", response_model=NotebookOut)
async def get_notebook(notebook_id: uuid.UUID, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_business_user)):
    return await _load_notebook(db, notebook_id, current_user)


@router.put("/{notebook_id}", response_model=NotebookOut)
async def update_notebook(notebook_id: uuid.UUID, body: NotebookUpdate, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_business_user)):
    notebook = await _load_notebook(db, notebook_id, current_user)
    if body.title is not None:
        notebook.title = body.title
    if body.session_id is not None:
        notebook.session_id = body.session_id
    content_changed = False
    if body.content is not None:
        doc = _as_document(body.content)
        new_hash = nb.notebook_hash(doc)
        if new_hash != notebook.content_hash:
            notebook.version += 1
            notebook.content_hash = new_hash
            notebook.content = doc
            content_changed = True
    await db.flush()
    await db.refresh(notebook)
    if body.title is not None or body.session_id is not None or content_changed:
        await _audit_notebook(
            db,
            current_user,
            notebook,
            "notebook.updated",
            {
                "version": notebook.version,
                "title_changed": body.title is not None,
                "session_rebound": body.session_id is not None,
                "content_changed": content_changed,
                "content_hash": notebook.content_hash,
            },
        )
    return notebook


@router.delete("/{notebook_id}", status_code=204)
async def archive_notebook(notebook_id: uuid.UUID, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_business_user)):
    notebook = await _load_notebook(db, notebook_id, current_user)
    notebook.status = NotebookStatus.ARCHIVED.value
    await db.flush()
    await _audit_notebook(
        db,
        current_user,
        notebook,
        "notebook.archived",
        {"version": notebook.version, "content_hash": notebook.content_hash},
    )
    return Response(status_code=204)


@router.post("/{notebook_id}/dev-session")
async def create_dev_session(notebook_id: uuid.UUID, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_business_user)):
    notebook = await _load_notebook(db, notebook_id, current_user)
    session, created = await _ensure_dev_session(db, notebook, current_user)
    return {
        "session_id": str(session.id),
        "created": created,
        "sandbox_level": session.sandbox_level,
        "sandbox_mode": session.sandbox_mode,
        "env": session.env or WorkloadEnv.TEST.value,
    }


@router.post("/{notebook_id}/cells/execute")
async def execute_cell(
    notebook_id: uuid.UUID,
    body: CellExecuteRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_business_user),
):
    # 空代码要在校验阶段就拒：否则会为一段不存在的代码起沙箱会话、写执行时间、自增版本。
    if not body.code or not body.code.strip():
        raise HTTPException(status_code=422, detail="code cannot be empty")
    notebook = await _load_notebook(db, notebook_id, current_user)
    session, _ = await _ensure_dev_session(db, notebook, current_user)
    result = await nb.run_cell(_executor(db, session.id, current_user, notebook.language), body.code, cell_id=str(body.cell_index) if body.cell_index is not None else None)
    clean = _clean(result)
    notebook.execution_backend = clean.get("backend")
    notebook.last_executed_at = datetime.now(timezone.utc)
    if body.persist and body.cell_index is not None:
        doc = notebook.content or nb.make_notebook(notebook.title)
        try:
            doc = nb.apply_cell_result(doc, body.cell_index, clean, source=body.code)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        notebook.content = doc
        notebook.content_hash = nb.notebook_hash(doc)
        notebook.version += 1
    await db.flush()
    await _audit_notebook(
        db,
        current_user,
        notebook,
        "notebook.cell_executed",
        {
            "session_id": str(session.id),
            "cell_index": body.cell_index,
            "backend": clean.get("backend"),
            "ok": bool(clean.get("ok")),
            "output_blocked": bool(clean.get("output_blocked")),
            "persisted": bool(body.persist and body.cell_index is not None),
            "code_sha256": _code_digest(body.code),
            "code_bytes": len(body.code.encode("utf-8")),
            "version": notebook.version,
        },
    )
    return clean


@router.post("/{notebook_id}/run")
async def run_notebook(notebook_id: uuid.UUID, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_business_user)):
    notebook = await _load_notebook(db, notebook_id, current_user)
    doc = notebook.content or nb.make_notebook(notebook.title)
    # 空笔记本一跑就是“0 个单元格全部通过”：它会白起一个开发沙箱、把版本+1、进程
    # 返 200。空细就是空细，当成失败报给调用方（浏览器“新建”落的就是空 doc）。
    if not any(nb.cell_source(cell).strip() for _index, cell in nb.code_cells(doc)):
        raise CDSError(
            "笔记本没有可执行的单元格：请先输入代码并保存",
            code="NOTEBOOK_NO_CELLS",
            http_status=409,
            detail={"notebook_id": str(notebook_id)},
        )
    session, _ = await _ensure_dev_session(db, notebook, current_user)
    executor = _executor(db, session.id, current_user, notebook.language)
    results: list[dict[str, Any]] = []
    passed = True
    for index, cell in nb.code_cells(doc):
        clean = _clean(await nb.run_cell(executor, nb.cell_source(cell), cell_id=str(index)))
        clean["cell_index"] = index
        results.append(clean)
        passed = passed and bool(clean.get("ok")) and not clean.get("output_blocked")
        if notebook.content:
            notebook.content = nb.apply_cell_result(notebook.content, index, clean)
    notebook.execution_backend = results[-1].get("backend") if results else None
    notebook.last_executed_at = datetime.now(timezone.utc)
    if notebook.content:
        notebook.content_hash = nb.notebook_hash(notebook.content)
        notebook.version += 1
    await db.flush()
    await _audit_notebook(
        db,
        current_user,
        notebook,
        "notebook.run",
        {
            "session_id": str(session.id),
            "executed_cells": len(results),
            "passed": passed,
            "backend": notebook.execution_backend,
            "version": notebook.version,
        },
    )
    return {"results": results, "passed": passed, "executed_cells": len(results)}


@router.get("/{notebook_id}/jupyter")
async def jupyter_status(notebook_id: uuid.UUID, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_business_user)):
    from app.core.config import get_settings
    from app.services import jupyter_gateway as jg

    notebook = await _load_notebook(db, notebook_id, current_user)
    session, _ = await _ensure_dev_session(db, notebook, current_user)
    # 探测只是自述：不能把 KMS/执行链的故障变成整页 503，
    # 失败就如实报 jupyter_available=false + probe_error，让页面把原因显示出来。
    try:
        probe = await nb.probe_jupyter(_executor(db, session.id, current_user, notebook.language))
        probe_error = None
    except Exception as e:
        logger.warning("[notebooks] jupyter probe failed for %s: %s", notebook_id, e)
        probe = {"jupyter_available": False, "jupyter_path": None}
        probe_error = str(getattr(e, "detail", None) or e)
    settings = get_settings()
    base = settings.JUPYTER_PROXY_BASE_URL
    supported = session.sandbox_level in ("L0", "L3")
    return {
        "jupyter_available": probe["jupyter_available"],
        "jupyter_path": probe["jupyter_path"],
        "probe_error": probe_error,
        "proxy_enabled": bool(base),
        # 直连入口需要一张一次性票据（iframe 带不了 Authorization 头），
        # 真入口在 POST /api/jupyter/tickets；这里只报自述信息，不下发可用裸链。
        "url": f"{jg.PUBLIC_PATH}/" if base else None,
        "dev_env": {
            "available": bool(base) and supported,
            "sandbox_level": session.sandbox_level,
            "microvm": session.sandbox_level == "l2c",
            "reason": None if (base and supported) else (
                "未配置 CDS_JUPYTER_PROXY_BASE_URL" if not base
                else f"沙箱等级 {session.sandbox_level} 暂未接开发环（现支持 L0 进程隔离 / L3 bwrap）"
            ),
        },
        "notebook_execution_backend": notebook.execution_backend,
        "session_id": str(session.id),
    }
