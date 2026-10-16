import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.errors import CDSError
from app.core.deps import get_business_user
from app.models.contract import WorkloadEnv
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

    created = await create_sandbox_session(
        body=SandboxSessionCreate(
            data_product_id=uuid.UUID(str(contract.product_ids[0])),
            sandbox_level="l2c",
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
    notebook = Notebook(
        user_id=current_user.id,
        contract_id=body.contract_id,
        session_id=body.session_id,
        title=body.title,
        path=body.path,
        language=body.language,
        content=doc,
        content_hash=nb.notebook_hash(doc),
    )
    db.add(notebook)
    await db.flush()
    await db.refresh(notebook)
    await audit_service.log(db, action="notebook.created", resource_type="notebook", user_id=current_user.id, resource_id=str(notebook.id), detail={"title": body.title, "path": body.path})
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

    notebook = await _load_notebook(db, notebook_id, current_user)
    session, _ = await _ensure_dev_session(db, notebook, current_user)
    probe = await nb.probe_jupyter(_executor(db, session.id, current_user, notebook.language))
    settings = get_settings()
    base = settings.JUPYTER_PROXY_BASE_URL
    return {
        "jupyter_available": probe["jupyter_available"],
        "jupyter_path": probe["jupyter_path"],
        "proxy_enabled": bool(base),
        "url": f"{base.rstrip('/')}/?session={session.id}" if base else None,
        "notebook_execution_backend": notebook.execution_backend,
        "session_id": str(session.id),
    }
