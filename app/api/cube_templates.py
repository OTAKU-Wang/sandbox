"""Cube template endpoints (SS-10 CB-05): template create/build + delete/GC.

W11 operation model (reuses ``session_operations`` exactly): ``?async=true``
→ 202 ``{operation_id}`` + poll ``GET /operations/{op_id}``; sync is the
default and executes inline (an operation record is still written).
Template operations reuse the W11 ``SessionOperation`` table with a stable
derived session id (``uuid5(cds-cube-template/<id>)`` — no session exists for
a template build) and ``op_type="template_build"``.
"""
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user, require_roles
from app.core.errors import CDSError
from app.models.cube_template import CubeTemplate, CubeTemplateNode
from app.models.user import User, UserRole
from app.services import session_operations as ops
from app.services.audit_service import audit_service
from app.services.cube_template_service import (
    CubeTemplateService,
    TemplateError,
    cube_template_service,
)

router = APIRouter()

_MANAGE = require_roles(UserRole.ADMIN, UserRole.OPERATOR)


class CubeTemplateCreate(BaseModel):
    name: str
    version: str = "1.0.0"
    image_ref: str | None = None
    probe_config: dict[str, Any] | None = None


def _payload(tpl: CubeTemplate, nodes: list[CubeTemplateNode] | None = None) -> dict:
    return {
        "id": str(tpl.id),
        "name": tpl.name,
        "version": tpl.version,
        "artifact_id": tpl.artifact_id,
        "sha256": tpl.sha256,
        "size_bytes": tpl.size_bytes,
        "image_ref": tpl.image_ref,
        "status": tpl.status,
        "probe_config": tpl.probe_config,
        "last_error": tpl.last_error,
        "created_by": tpl.created_by,
        "created_at": tpl.created_at.isoformat() if tpl.created_at else None,
        "nodes": (
            [
                {
                    "node_id": r.node_id,
                    "status": r.status,
                    "artifact_sha256": r.artifact_sha256,
                    "last_error": r.last_error,
                }
                for r in (nodes or [])
            ]
            if nodes is not None
            else None
        ),
    }


async def _load(db: AsyncSession, template_id: uuid.UUID) -> CubeTemplate | None:
    try:
        return await db.get(CubeTemplate, template_id)
    except (ValueError, TypeError):
        return None


@router.post("", response_model=None, status_code=status.HTTP_201_CREATED)
async def create_cube_template(
    body: CubeTemplateCreate,
    async_mode: bool = Query(False, alias="async"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(_MANAGE),
):
    """Register a cube template and build it (convert → MinIO → AppSnapshot →
    distribute). ``?async=true`` → 202 + operation_id (W11)."""
    name = str(body.name or "").strip()
    if not name:
        raise CDSError("name is required", code="VALIDATION_ERROR", http_status=400)
    tpl = await cube_template_service.create_template(
        db,
        name=name,
        version=str(body.version or "1.0.0"),
        image_ref=body.image_ref,
        created_by=str(current_user.id),
        probe_config=body.probe_config,
    )

    async def _run(op):
        op.progress = 0.1
        return await cube_template_service.build_and_distribute(tpl.id)

    op = await ops.submit_operation(
        db,
        CubeTemplateService.op_session_id(tpl.id),
        "template_build",
        run_async=async_mode,
        executor=_run,
    )
    await audit_service.log(
        db, action="cube.template_build", resource_type="cube_template",
        resource_id=str(tpl.id), user_id=current_user.id,
        detail={"name": tpl.name, "artifact_id": tpl.artifact_id,
                "async": async_mode, "operation_id": str(op.op_id)},
    )
    await db.commit()

    if async_mode:
        return JSONResponse(status_code=202, content=ops.operation_payload(op))
    if op.status == "failed":
        await db.commit()
        raise CDSError(op.error or "template build failed", code="INTERNAL_ERROR", http_status=500)
    await db.commit()
    return JSONResponse(
        status_code=201,
        content={
            **_payload(tpl),
            "operation": ops.operation_payload(op),
        },
    )


@router.get("")
async def list_cube_templates(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(_MANAGE),
):
    result = await db.execute(select(CubeTemplate).order_by(CubeTemplate.created_at))
    return {"items": [_payload(t) for t in result.scalars().all()]}


@router.get("/operations/{op_id}")
async def get_template_operation(
    op_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(_MANAGE),
):
    """Poll a template build operation (W11 async model)."""
    op = await ops.get_operation(db, op_id)
    if op is None or op.op_type != "template_build":
        raise CDSError("operation not found", code="SESSION_NOT_FOUND", http_status=404)
    return ops.operation_payload(op)


@router.get("/{template_id}")
async def get_cube_template(
    template_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(_MANAGE),
):
    tpl = await _load(db, template_id)
    if tpl is None:
        raise CDSError("cube template not found", code="SESSION_NOT_FOUND", http_status=404)
    rows = list(
        (await db.execute(
            select(CubeTemplateNode).where(CubeTemplateNode.template_id == tpl.id)
        )).scalars().all()
    )
    return _payload(tpl, rows)


@router.post("/{template_id}/redistribute")
async def redistribute_cube_template(
    template_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(_MANAGE),
):
    """Re-run distribution: FAILED nodes go FAILED → DISTRIBUTING again (new
    presigned URL + token per attempt); READY nodes are skipped (idempotent)."""
    tpl = await _load(db, template_id)
    if tpl is None:
        raise CDSError("cube template not found", code="SESSION_NOT_FOUND", http_status=404)
    try:
        result = await cube_template_service.distribute(tpl.id)
    except TemplateError as exc:
        raise CDSError(exc.message, code=exc.code, http_status=exc.http_status, detail=exc.detail) from exc
    await audit_service.log(
        db, action="cube.template_redistribute", resource_type="cube_template",
        resource_id=str(tpl.id), user_id=current_user.id, detail=result,
    )
    await db.commit()
    return result


@router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_cube_template(
    template_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(_MANAGE),
):
    """GC round: per-node DestroyImage; in-use (Conflict) rows are deferred to
    the next round (409 + detail; idempotent — re-call to retry)."""
    tpl = await _load(db, template_id)
    if tpl is None:
        raise CDSError("cube template not found", code="SESSION_NOT_FOUND", http_status=404)
    try:
        result = await cube_template_service.delete_template(tpl.id)
    except TemplateError as exc:
        raise CDSError(exc.message, code=exc.code, http_status=exc.http_status, detail=exc.detail) from exc
    await audit_service.log(
        db, action="cube.template_delete", resource_type="cube_template",
        resource_id=str(tpl.id), user_id=current_user.id, detail=result,
    )
    await db.commit()
    if not result["deleted"]:
        return JSONResponse(
            status_code=409,
            content={
                "code": "SESSION_STATE_CONFLICT",
                "message": "template artifact still in use on some nodes; deferred to next GC round",
                "detail": result,
            },
        )
    return JSONResponse(status_code=204, content=None)
