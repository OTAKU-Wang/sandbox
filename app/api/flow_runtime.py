"""SS-15 流程运行时可用性只读面（产品化 UX：点击之前先如实告知）。

背景：无 /dev/kvm 或 CDS_CUBE_DATAPLANE_ENABLED=false 的部署里，MicroVM 数据面按 SS-13
铁律 fail-closed（不降级到进程隔离、不伪装可用）。此前用户只有在点了「启动测试沙箱」
之后才看到一条带内部字段的 400——本只读端点把同一个判断提前暴露成可渲染的状态，
措辞与 detail.reason 一一对应，绝不粉饰成"可用"。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.deps import get_business_user
from app.models.sandbox_node import NodeStatus, SandboxNode
from app.models.user import User

router = APIRouter()
logger = logging.getLogger(__name__)

REASON_MESSAGES: dict[str, str] = {
    "dataplane_disabled": (
        "当前部署未启用 MicroVM 沙箱数据面（CDS_CUBE_DATAPLANE_ENABLED=false），"
        "在线开发、测试沙箱与生产沙箱都无法真实执行代码。"
        "请管理员按 deploy/cube-node/ 完成计算节点部署（前置：节点有 /dev/kvm）。"
    ),
    "no_ready_node": (
        "MicroVM 数据面已开启，但当前没有 online 的计算节点，沙箱创建会被拒绝。"
        "请检查节点心跳与 deploy/cube-node/preflight.sh 的 PASS/FAIL 矩阵。"
    ),
}


@router.get("/runtime-status")
async def runtime_status(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_business_user),
) -> dict[str, Any]:
    """沙箱运行时可用性：available=false 时 message 必须可直接展示给用户。"""
    settings = get_settings()
    total = int(await db.scalar(select(func.count()).select_from(SandboxNode)) or 0)
    ready = int(
        await db.scalar(
            select(func.count()).select_from(SandboxNode).where(
                SandboxNode.status == NodeStatus.ONLINE.value
            )
        )
        or 0
    )
    reason: str | None = None
    if not settings.CUBE_DATAPLANE_ENABLED:
        reason = "dataplane_disabled"
    elif ready == 0:
        reason = "no_ready_node"
    return {
        "available": reason is None,
        "reason": reason,
        "message": None if reason is None else REASON_MESSAGES[reason],
        "ready_nodes": ready,
        "total_nodes": total,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
