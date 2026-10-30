"""Unstructured data pipeline API — OCR/ASR/video processing in sandbox."""
import os
import uuid
import shutil
from pathlib import Path
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from app.core.database import get_db
from app.core.deps import get_current_user, require_roles
from app.core.path_security import PathTraversalError, safe_resolve_within, sanitize_filename
from app.models.user import User, UserRole
from app.models.pipeline_task import PipelineTask, PipelineTaskStatus, PipelineTaskType
from app.services.unstructured_pipeline import pipeline, PipelineResult
from app.services.audit_service import audit_service

router = APIRouter()

UPLOAD_ROOT = Path("/tmp/cds-uploads")
UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)


class PipelineTaskResponse(BaseModel):
    task_id: str
    task_type: str
    status: str
    input_path: str
    output_path: str | None = None
    result: dict | None = None
    error: str | None = None


class PipelineResultResponse(BaseModel):
    task_id: str
    task_type: str
    success: bool
    output: dict
    duration_ms: int
    error: str | None = None


class PipelineUploadResponse(BaseModel):
    file_path: str
    filename: str
    size_bytes: int


def _stage_upload(user_id, upload: UploadFile) -> tuple[Path, str]:
    """A3①: 落盘上传文件 —— 文件名必须净化，落点必须在上传根内，同名不得互相覆盖。

    旧实现是 ``UPLOAD_ROOT / uid / file.filename`` 后直接 ``open(..., "wb")``：
    ``Path`` 的 ``/`` 语义下 ``../../x`` 会逃逸出上传根、绝对文件名会丢掉整个前缀，
    带路径分隔的文件名则把 ``open()`` 打进 500；同名即同文件 ⇒ 先上传方的数据蒸发。
    现在：``sanitize_filename`` 取最后一段并去掉前导点，再用 ``safe_resolve_within``
    做拒绝式的包含校验（refuse-by-default），文件名前加唯一前缀保证同名不覆盖。
    """
    try:
        name = sanitize_filename(upload.filename or "")
    except PathTraversalError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid filename: {exc}")

    user_dir = UPLOAD_ROOT / str(user_id)
    user_dir.mkdir(parents=True, exist_ok=True)
    # 唯一前缀：两次同名上传必须是两个文件（A3 同名静默覆盖）
    staged_name = f"{uuid.uuid4().hex[:12]}-{name}"
    try:
        file_path = safe_resolve_within(UPLOAD_ROOT, f"{user_id}/{staged_name}")
    except PathTraversalError as exc:  # 净化后仍越界 ⇒ 拒绝，不落盘
        raise HTTPException(status_code=400, detail=f"Upload path escapes the upload root: {exc}")

    with open(file_path, "wb") as f:
        shutil.copyfileobj(upload.file, f)
    return file_path, name


def _resolve_pipeline_input(input_path: str) -> Path:
    """A3②/A165①: ``process-path`` 的 input_path 必须解析在上传根内。

    拒绝式（refuse-by-default）包含校验 —— ADMIN/OPERATOR 也不能让流水线读取任意宿主
    路径（旧实现只有一个 ``Path(input_path).exists()``，等于零约束，还能把别人目录整体
    copytree 进任务）：
    - 显式 ``..`` 遍历段＝逃逸尝试，无论目标存在与否一律 400；
    - 解析结果落在上传根外：目标存在 ⇒ 400 明确拒绝；目标不存在 ⇒ 沿用 404
      （既不读取，也不额外泄露根外是否存在文件）；
    - 上传根内的相对路径是合法输入，解析成绝对路径后交给流水线。
    """
    if not input_path.strip():
        raise HTTPException(status_code=400, detail="input_path is empty")
    if "\x00" in input_path:
        raise HTTPException(status_code=400, detail="Invalid input_path")
    if ".." in Path(input_path).parts:
        raise HTTPException(
            status_code=400,
            detail=f"input_path contains traversal segments and is refused: {input_path}",
        )
    try:
        resolved = safe_resolve_within(UPLOAD_ROOT, input_path)
    except PathTraversalError:
        if (UPLOAD_ROOT / input_path).exists():
            raise HTTPException(
                status_code=400,
                detail=f"input_path must resolve inside the upload root {UPLOAD_ROOT}: {input_path}",
            )
        raise HTTPException(status_code=404, detail=f"Input path not found: {input_path}")
    if not resolved.exists():
        raise HTTPException(status_code=404, detail=f"Input path not found: {input_path}")
    if resolved == UPLOAD_ROOT.resolve():
        # 上传根本身＝包内所有用户目录，递上去就是整库 copytree（A165① 的跨租户读）
        raise HTTPException(status_code=400, detail="input_path must be a file or directory inside the upload root, not the root itself")
    return resolved


@router.post("/upload-and-process")
async def upload_and_process(
    file: UploadFile = File(...),
    task_type: str = Form("document"),
    max_retries: int = Form(0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR, UserRole.DATA_PROVIDER)),
):
    """Upload a file and process it in sandbox. Supported types: ocr, asr, video, document, dicom."""
    if task_type not in ("ocr", "asr", "video", "document", "dicom"):
        raise HTTPException(status_code=400, detail=f"Invalid task_type: {task_type}. Must be one of: ocr, asr, video, document, dicom")

    # Save upload (A3①: sanitised filename, containment-checked, never overwriting)
    file_path, _safe_name = _stage_upload(current_user.id, file)

    # Create DB task record
    task_id = f"pipe-{uuid.uuid4().hex[:12]}"
    db_task = PipelineTask(
        task_id=task_id,
        task_type=task_type,
        status=PipelineTaskStatus.PENDING.value,
        input_path=str(file_path),
        options={"max_retries": max_retries},
        user_id=current_user.id,
    )
    db.add(db_task)
    await db.flush()

    # Execute in sandbox
    in_memory_task = pipeline.submit_task(task_type, str(file_path), options={"max_retries": max_retries, "user_id": str(current_user.id)})
    result = await pipeline.execute_task(in_memory_task.task_id)

    # Update DB record
    db_task.status = PipelineTaskStatus.COMPLETED.value if result.success else PipelineTaskStatus.FAILED.value
    db_task.output_path = result.output.get("output_path") if result.output else None
    db_task.result = result.output
    db_task.error_message = result.error
    db_task.duration_ms = result.duration_ms
    db_task.completed_at = datetime.now(timezone.utc)

    await db.flush()

    await audit_service.log(
        db, action="pipeline.process", resource_type="pipeline_task",
        user_id=current_user.id, resource_id=task_id,
        detail={"task_type": task_type, "success": result.success},
    )

    return PipelineResultResponse(
        task_id=task_id,
        task_type=result.task_type,
        success=result.success,
        output=result.output,
        duration_ms=result.duration_ms,
        error=result.error,
    )


@router.post("/process-path")
async def process_local_path(
    task_type: str = Query(...),
    input_path: str = Query(...),
    max_retries: int = Query(0, ge=0, le=5),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR)),
):
    """Process a local file path in sandbox (operator/admin only)."""
    if task_type not in ("ocr", "asr", "video", "document", "dicom"):
        raise HTTPException(status_code=400, detail=f"Invalid task_type: {task_type}")

    # A3②/A165①: 先做上传根包含校验，再谈存在性 —— 越界一律拒绝，不进入流水线
    source = _resolve_pipeline_input(input_path)

    task_id = f"pipe-{uuid.uuid4().hex[:12]}"
    db_task = PipelineTask(
        task_id=task_id,
        task_type=task_type,
        status=PipelineTaskStatus.PENDING.value,
        input_path=str(source),
        options={"max_retries": max_retries},
        user_id=current_user.id,
    )
    db.add(db_task)
    await db.flush()

    in_memory_task = pipeline.submit_task(task_type, str(source), options={"max_retries": max_retries, "user_id": str(current_user.id)})
    result = await pipeline.execute_task(in_memory_task.task_id)

    db_task.status = PipelineTaskStatus.COMPLETED.value if result.success else PipelineTaskStatus.FAILED.value
    db_task.result = result.output
    db_task.error_message = result.error
    db_task.duration_ms = result.duration_ms
    db_task.completed_at = datetime.now(timezone.utc)

    await db.flush()

    return PipelineResultResponse(
        task_id=task_id,
        task_type=result.task_type,
        success=result.success,
        output=result.output,
        duration_ms=result.duration_ms,
        error=result.error,
    )


@router.post("/upload", response_model=PipelineUploadResponse)
async def upload_file(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR, UserRole.DATA_PROVIDER)),
):
    """Stage one upload inside the upload root (A3①) — returns its stored path.

    只落盘、不执行；返回的 ``file_path`` 可以交给 ``/process-path`` 处理（该端点
    同样只接受上传根内的路径）。
    """
    file_path, safe_name = _stage_upload(current_user.id, file)
    size = file_path.stat().st_size

    await audit_service.log(
        db, action="pipeline.upload", resource_type="pipeline_task",
        user_id=current_user.id, resource_id=safe_name,
        detail={"filename": safe_name, "size_bytes": size},
    )

    return PipelineUploadResponse(file_path=str(file_path), filename=safe_name, size_bytes=size)


@router.get("/tasks")
async def list_tasks(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    status_filter: str | None = Query(None, alias="status"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List pipeline tasks for the current user."""
    query = select(PipelineTask).where(PipelineTask.user_id == current_user.id)
    count_query = select(func.count()).select_from(PipelineTask).where(PipelineTask.user_id == current_user.id)
    if status_filter:
        query = query.where(PipelineTask.status == status_filter)
        count_query = count_query.where(PipelineTask.status == status_filter)

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    query = query.order_by(PipelineTask.created_at.desc()).offset(skip).limit(limit)
    result = await db.execute(query)
    tasks = result.scalars().all()
    return {
        "items": [
            {
                "task_id": t.task_id, "task_type": t.task_type, "status": t.status,
                "input_path": t.input_path, "output_path": t.output_path,
                "duration_ms": t.duration_ms, "created_at": t.created_at.isoformat(),
            }
            for t in tasks
        ],
        "total": total,
    }


@router.get("/tasks/{task_id}")
async def get_task(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get task details by ID."""
    result = await db.execute(select(PipelineTask).where(PipelineTask.task_id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not your task")
    return {
        "task_id": task.task_id, "task_type": task.task_type, "status": task.status,
        "input_path": task.input_path, "output_path": task.output_path,
        "result": task.result, "error": task.error_message,
        "duration_ms": task.duration_ms,
        "created_at": task.created_at.isoformat(),
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
    }
