"""Notifications API — 聚合通知中心（2026-11-03 realignment Task 12）。

挂载：main.py 以 prefix=/api/v1/monitoring/notifications + require_group("audit")。
聚合口径（计划草案与真实代码的差集见 tests/test_notifications_api.py 的 docstring）：
1. ``UserNotification`` 持久化通知：本人行 + user_id IS NULL 广播行；
2. 实时待办：我未签署的 draft/negotiating 合约（当事方各自可见）；
   出域待审 = ``SandboxTask.status == output_review``，仅对 operator/admin 出现。
合成待办项 id 带 ``contract:`` / ``task:`` 前缀，标记已读一律 404——它们随
真实状态流转自然消失，不落表；伪造 id 永远命中不了任何真实资源。
"""
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_business_user
from app.models.contract import Contract, ContractStatus
from app.models.sandbox_task import SandboxTask, TaskStatus
from app.models.user import User
from app.models.user_notification import NotificationCategory, UserNotification
from app.services.audit_service import audit_service

router = APIRouter()

TODO_CONTRACT_LIMIT = 20
TODO_OUTPUT_LIMIT = 20


class NotificationCreateRequest(BaseModel):
    category: NotificationCategory = NotificationCategory.BUSINESS
    title: str = Field(min_length=1, max_length=256)
    content: str | None = None
    target_path: str | None = Field(default=None, max_length=256)


def _notification_out(row: UserNotification) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "category": row.category,
        "title": row.title,
        "content": row.content,
        "target_path": row.target_path,
        "is_read": bool(row.is_read),
        "extra_meta": row.extra_meta or {},
        "source": "stored",
        "created_at": row.created_at,
    }


async def _contract_todos(db: AsyncSession, user: User) -> list[dict[str, Any]]:
    res = await db.execute(
        select(Contract)
        .where(
            Contract.status.in_([ContractStatus.DRAFT.value, ContractStatus.NEGOTIATING.value]),
            (Contract.provider_id == user.id) | (Contract.buyer_id == user.id),
        )
        .order_by(Contract.created_at.desc())
        .limit(TODO_CONTRACT_LIMIT)
    )
    todos = []
    for c in res.scalars().all():
        signed = c.provider_signed_at if c.provider_id == user.id else c.buyer_signed_at
        if signed is not None:
            continue
        todos.append(
            {
                "id": f"contract:{c.id}",
                "category": NotificationCategory.TODO.value,
                "title": f"合约待签署：{c.title or c.contract_no}",
                "content": f"合约号 {c.contract_no}（{c.status}）等待你方签署",
                "target_path": "/contracts",
                "is_read": False,
                "extra_meta": {"contract_no": c.contract_no, "status": c.status},
                "source": "live",
                "created_at": c.created_at,
            }
        )
    return todos


async def _output_review_todos(db: AsyncSession) -> list[dict[str, Any]]:
    res = await db.execute(
        select(SandboxTask)
        .where(SandboxTask.status == TaskStatus.OUTPUT_REVIEW.value)
        .order_by(SandboxTask.created_at.desc())
        .limit(TODO_OUTPUT_LIMIT)
    )
    return [
        {
            "id": f"task:{t.id}",
            "category": NotificationCategory.TODO.value,
            "title": f"出域待审：任务 {t.task_id}",
            "content": t.purpose or "任务产出已进入出域审批队列",
            "target_path": None,
            "is_read": False,
            "extra_meta": {"task_id": t.task_id, "session_id": str(t.session_id)},
            "source": "live",
            "created_at": t.created_at,
        }
        for t in res.scalars().all()
    ]


@router.get("")
async def list_notifications(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_business_user),
):
    res = await db.execute(
        select(UserNotification)
        .where(
            (UserNotification.user_id == current_user.id) | (UserNotification.user_id.is_(None))
        )
        .order_by(UserNotification.created_at.desc())
        .limit(100)
    )
    stored = [_notification_out(row) for row in res.scalars().all()]

    todos = await _contract_todos(db, current_user)
    if getattr(current_user, "role", None) in ("operator", "admin"):
        todos = todos + await _output_review_todos(db)

    items = sorted(
        todos + stored,
        key=lambda item: item["created_at"] or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    unread_count = sum(1 for item in stored if not item["is_read"])
    return {
        "items": items,
        "total": len(items),
        "unread_count": unread_count,
        "todo_count": len(todos),
    }


@router.post("", status_code=201)
async def create_notification(
    body: NotificationCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_business_user),
):
    row = UserNotification(
        user_id=current_user.id,
        category=body.category.value if isinstance(body.category, NotificationCategory) else str(body.category),
        title=body.title,
        content=body.content,
        target_path=body.target_path,
        is_read=False,
        created_at=datetime.now(timezone.utc),
    )
    db.add(row)
    await db.flush()
    await audit_service.log(
        db, action="notification.create", resource_type="user_notification",
        user_id=current_user.id, resource_id=str(row.id),
        detail={"category": row.category, "title": row.title},
    )
    await db.refresh(row)
    return _notification_out(row)


@router.post("/{notification_id}/read")
async def mark_read(
    notification_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_business_user),
):
    res = await db.execute(
        select(UserNotification).where(
            UserNotification.id == notification_id,
            UserNotification.user_id == current_user.id,
        )
    )
    row = res.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="notification not found")
    setattr(row, "is_read", True)
    await db.flush()
    return _notification_out(row)
