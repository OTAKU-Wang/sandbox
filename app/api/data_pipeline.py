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


@router.post("/upload-and-process")
async def upload_and_process(
    file: UploadFile = File(...),
    task_type: str = Form("document"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR, UserRole.DATA_PROVIDER)),
):
    """Upload a file and process it in sandbox. Supported types: ocr, asr, video, document."""
    if task_type not in ("ocr", "asr", "video", "document"):
        raise HTTPException(status_code=400, detail=f"Invalid task_type: {task_type}. Must be one of: ocr, asr, video, document")

    # Save upload
    user_dir = UPLOAD_ROOT / str(current_user.id)
    user_dir.mkdir(parents=True, exist_ok=True)
    file_path = user_dir / file.filename
    with open(file_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    # Create DB task record
    task_id = f"pipe-{uuid.uuid4().hex[:12]}"
    db_task = PipelineTask(
        task_id=task_id,
        task_type=task_type,
        status=PipelineTaskStatus.PENDING.value,
        input_path=str(file_path),
        user_id=current_user.id,
    )
    db.add(db_task)
    await db.flush()

    # Execute in sandbox
    in_memory_task = pipeline.submit_task(task_type, str(file_path))
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
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR)),
):
    """Process a local file path in sandbox (operator/admin only)."""
    if task_type not in ("ocr", "asr", "video", "document"):
        raise HTTPException(status_code=400, detail=f"Invalid task_type: {task_type}")

    if not Path(input_path).exists():
        raise HTTPException(status_code=404, detail=f"Input path not found: {input_path}")

    task_id = f"pipe-{uuid.uuid4().hex[:12]}"
    db_task = PipelineTask(
        task_id=task_id,
        task_type=task_type,
        status=PipelineTaskStatus.PENDING.value,
        input_path=input_path,
        user_id=current_user.id,
    )
    db.add(db_task)
    await db.flush()

    in_memory_task = pipeline.submit_task(task_type, input_path)
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
