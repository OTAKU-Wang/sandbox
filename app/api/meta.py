from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_business_user
from app.models.sandbox_node import NodeStatus, SandboxNode
from app.models.user import User
from app.services.capability_matrix import get_capability_matrix, sandbox_levels_payload
from app.services.compliance_report import disclosure_fields

router = APIRouter()


# PZ-13 (SS-11): level capability matrix — single authority source
# (spec ss-11-cds-productization.md §4.2). Read-only, JWT like the other
# /api/v1 endpoints.
@router.get("/capability-matrix")
async def capability_matrix_endpoint(user: User = Depends(get_business_user)):
    return {
        **get_capability_matrix(),
        **disclosure_fields(),
    }


# 2026-10-30 拍板②：「允许沙箱级别」此前在前端被硬编码成 l2c（disabled），
# 而 dev compose 把 CDS_DEV_SANDBOX_LEVEL 设成 L3 → 本机一开调试环必 400
# （取证见 specs/ss-19-full-platform-docker-e2e.md §10.1-4）。这个只读端点把
# 「当前这套部署实际能跑哪些档位 + 每个档位为什么能/不能」如实说清楚，
# 前端合约表单据此渲染选项与披露文案，不再硬编码。
#
# 铁律（SS-13）：**披露能力 ≠ 降级隔离，也 ≠ 放宽校验**。本端点一个判定都不做，
# 创建会话/合约的执行侧门禁（app/api/sandbox_sessions.py 的 LEGACY_LEVEL_RETIRED /
# RUNTIME_UNAVAILABLE、app/schemas/contract.py 的取值域）一个字都不改，
# 不可满足的请求照旧 400。
@router.get("/sandbox-levels")
async def sandbox_levels_endpoint(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_business_user),
):
    """当前部署真实可用的沙箱档位 + 逐项披露（只读，不改任何执行判定）。"""
    # online 节点数的判据与 app/api/flow_runtime.py::runtime_status 逐字一致，
    # l2c 的「有没有节点」两处披露不得各说各话。
    ready = int(
        await db.scalar(
            select(func.count()).select_from(SandboxNode).where(
                SandboxNode.status == NodeStatus.ONLINE.value
            )
        )
        or 0
    )
    return {
        **sandbox_levels_payload(ready_nodes=ready),
        **disclosure_fields(),
    }
