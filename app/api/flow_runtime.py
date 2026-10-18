"""SS-15 流程运行时可用性只读面（产品化 UX：点击之前先如实告知）。

背景：无 /dev/kvm 或 CDS_CUBE_DATAPLANE_ENABLED=false 的部署里，MicroVM 数据面按 SS-13
铁律 fail-closed（不降级到进程隔离、不伪装可用）。此前用户只有在点了「启动测试沙箱」
之后才看到一条带内部字段的 400——本只读端点把同一个判断提前暴露成可渲染的状态，
措辞与 detail.reason 一一对应，绝不粉饰成"可用"。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.deps import get_business_user
from app.models.api_endpoint import ApiEndpoint, ApiEndpointStatus
from app.models.audit_log import AuditLog
from app.models.compute_run import ComputeRun, ComputeRunStatus
from app.models.contract import Contract
from app.models.delivery_bundle import DeliveryBundle
from app.models.notebook import Notebook, NotebookStatus
from app.models.sandbox_node import NodeStatus, SandboxNode
from app.models.sandbox_session import SandboxSession
from app.models.user import User

router = APIRouter()
logger = logging.getLogger(__name__)

REASON_MESSAGES: dict[str, str] = {
    "dataplane_disabled": (
        "当前部署未启用 MicroVM 沙箱数据面（CDS_CUBE_DATAPLANE_ENABLED=false），"
        "MicroVM 档位的在线开发、测试沙箱与生产沙箱都无法创建。"
        "请管理员按 deploy/cube-node/ 完成计算节点部署（前置：节点有 /dev/kvm）。"
        "其它隔离档位（如容器隔离的在线开发环）是否可用，以各自页面的实际探测为准。"
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


# ─────────────────────────────────────────────────────────────────────────────
# 原型图 1「首页-平台总览」的单一聚合面（SS-19 D-e2e-23）
#
# 为什么要有这个端点：首页八个区块里有四个（今日调用次数、今日操作日志+失败、
# 两类趋势、结果集调用次数）在既有路由上**拼不出来**——不是「前端再发三个请求」的事，
# 而是根本没有对应口径的读面（取证见 specs/ss-19-full-platform-docker-e2e.md）。
# 本端点只做**聚合读取**：不新增计数器、不新增表、不写审计（高频只读页）。
#
# 三条铁律（与 app/api/quota.py 同源）：
# 1. 每个数字都要有真实落库来源；给不出的返回 null 并写进 not_collected，
#    由前端渲染「未记录」——绝不把 0 当「没用过」，绝不把「已分配」说成「实测占用」。
# 2. 只查 PostgreSQL audit_logs 与业务表。ClickHouse 在当前 compose 部署里写不进去
#    （docker-compose.yml 把原生协议客户端指向了 HTTP 端口 8123，且写入异常被吞在
#    debug 级），拿它出来的 0 当数字就是伪造，所以这里一次都不碰它。
# 3. 口径写在响应里（*_caliber / visibility / source / note），前端照抄展示，不再各自解释。
# ─────────────────────────────────────────────────────────────────────────────

# 趋势两类的**真实**动作名——只写 app/ 里确实有写入点的那几个：
#   调试 = 在线调试运行（单元格执行 app/api/notebooks.py:481、整本运行 :531）
#   执行 = 正式生产运行请求（app/api/compute.py:127）
# 刻意不复用 GET /api/v1/monitoring/task-trend 的动作表：那 6 个动作里
# sandbox.start/stop、task.execute/complete/fail 在 app/ 内**从来没有写入点**，
# 用它画「执行趋势」等于把「从未埋点」画成「今天 0 次」。
DEBUG_TREND_ACTIONS: tuple[str, ...] = ("notebook.cell_executed", "notebook.run")
EXEC_TREND_ACTIONS: tuple[str, ...] = ("compute.run.requested",)
# 结果通道调用：与 app/api/audit.py 的 AUDIT_EVENT_TYPES["call"] 同一对动作名。
CALL_ACTIONS: tuple[str, ...] = ("openapi.call", "openapi.call_failed")

# 调试环会话 == sandbox_mode=product_dev（开发环「启动测试沙箱」建的就是它）；
# 其余模式的活跃会话算在执行档上。词表：app/models/sandbox_session.py:36-43。
DEBUG_SANDBOX_MODE = "product_dev"

# 能读平台级操作日志的角色（与 app/api/audit.py:186 的 require_roles 同一组）：
# 只有这些角色拿到的「今日操作日志」才是平台口径，其他人只能是本人口径。
PLATFORM_AUDIT_ROLES = ("admin", "platform_admin", "operator", "regulator")

_NOT_COLLECTED: tuple[dict[str, str], ...] = (
    {
        "key": "memory.measured",
        "label": "沙箱内存实测占用（RSS/cgroup）",
        "reason": (
            "后端没有任何实测内存来源：无 cgroup/psutil 读取，Cube 节点 RPC 只暴露 "
            "get_storage_metrics（存储字节）。这里的分子是**已分配**——活跃会话创建时按档位扣减的 "
            "resource_limits.memory_mb 之和（app/api/sandbox_sessions.py:381 调 "
            "sandbox_manager.check_tenant_quota/update_tenant_usage），它等于「配额被占了多少」，"
            "不等于「进程正在用多少」。"
        ),
    },
    {
        "key": "memory.exec.quota",
        "label": "执行内存独立配额（原型上的 16 G）",
        "reason": (
            "配置里没有 16 G 这一档：全库只有租户内存配额 "
            "sandbox_manager.TENANT_DEFAULTS.max_memory_mb=8192（8 G），调试与执行会话创建都扣同一个池子；"
            "单任务上限是 32 GiB（调试）/64 GiB（执行）（app/api/notebooks.py:48-50），是上限不是配额。"
            "所以执行档沿用同一个租户内存配额做分母。"
        ),
    },
    {
        "key": "bundle.call_count",
        "label": "结果集文件级调用次数",
        "reason": (
            "delivery_bundles 没有调用计数列（app/models/delivery_bundle.py 全列无 calls*）；"
            "响应里的 call_count 是**该合约下 OpenAPI 接口的累计调用次数**"
            "（api_endpoints.calls_used 之和，app/services/delivery_service.py:370 逐次累加），"
            "口径随响应 call_count_caliber 下发，不是单个结果文件的下载次数。"
        ),
    },
)


def _utc_day_start(moment: datetime) -> datetime:
    """UTC 自然日 0 点（带 tz）——「今日」的唯一定义，避免前端按本地时区再切一刀。"""
    return moment.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)


async def _audit_window_counts(
    db: AsyncSession,
    *,
    since: datetime,
    until: datetime | None,
    user_id: Any = None,
    actions: tuple[str, ...] | None = None,
) -> tuple[int, int]:
    """窗口内 (总条数, 失败条数)。

    失败判定复用 app/api/audit.py::audit_outcome——审计里没有独立的「结果」列，
    失败只由动作名派生，这条规则由 tests/test_audit_query_filters.py 机检，首页不得另立口径。
    按动作分组后在 Python 里判定，既避开各数据库的 LIKE/大小写差异，也保证与
    GET /api/v1/audit/records?outcome=failure 逐行一致。
    """
    from app.api.audit import audit_outcome

    stmt = select(AuditLog.action, func.count()).where(AuditLog.created_at >= since)
    if until is not None:
        stmt = stmt.where(AuditLog.created_at < until)
    if actions is not None:
        stmt = stmt.where(AuditLog.action.in_(actions))
    if user_id is not None:
        stmt = stmt.where(AuditLog.user_id == user_id)
    rows = (await db.execute(stmt.group_by(AuditLog.action))).all()
    total = sum(int(count) for _, count in rows)
    failures = sum(int(count) for action, count in rows if audit_outcome(action) == "failure")
    return total, failures


@router.get("/dashboard/home")
async def dashboard_home(
    days: int = Query(7, ge=1, le=30),
    recent_limit: int = Query(5, ge=1, le=20),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_business_user),
) -> dict[str, Any]:
    """首页（原型图 1）唯一数据源：开发任务、结果通道、今日两类计数、内存档位、趋势、最新结果集。

    范围口径与各自列表页逐字一致，这样首页与点进去的页面不会出现两个数字：
    - 开发任务：Notebook.user_id == 当前主体（同 app/api/notebooks.py:373）；
    - OpenAPI 接口 / 结果集 / 执行运行：合约当事方（buyer 或 provider，同 app/api/quota.py:228-238
      与 app/api/deliveries.py:340-344）；
    - 今日操作日志：本人口径（AuditLog.user_id），持平台审计角色才是平台口径，并如实回 visibility。
    """
    # 与列表页/配额页同源的常量，放在函数内是刻意的：app.api.notebooks / app.api.quota 在模块
    # 导入期会拉起 sandbox_manager、quota_manager 等重依赖，不把它们拽进本模块的导入环。
    from app.api.notebooks import DEBUG_MEMORY_MAX_MB, EXEC_MEMORY_MAX_MB
    from app.api.quota import CONCURRENT_STATUSES
    from app.services import sandbox_manager

    uid = current_user.id
    role = getattr(current_user, "role", None)
    now = datetime.now(timezone.utc)
    today_start = _utc_day_start(now)

    # ── 1) 开发任务：进行中 / 已结束 ──────────────────────────────────────
    # 状态词表只有 draft/archived 两个值（app/models/notebook.py:21-24），原型上的
    # 「进行中/已结束」是开发者门户的叫法：未归档 = 进行中，已归档 = 已结束。
    # 映射写进响应（status_caliber），前端不再自己猜第三种状态。
    task_rows = (
        await db.execute(
            select(Notebook.status, func.count()).where(Notebook.user_id == uid).group_by(Notebook.status)
        )
    ).all()
    by_status = {status: int(count) for status, count in task_rows}
    ended = by_status.get(NotebookStatus.ARCHIVED.value, 0)
    active = sum(count for status, count in by_status.items() if status != NotebookStatus.ARCHIVED.value)
    task_total = sum(by_status.values())

    recent_rows = (
        (
            await db.execute(
                select(Notebook)
                .where(Notebook.user_id == uid)
                # 「最近创建」按 created_at 排；列表页 GET /notebooks 按 updated_at 排
                # （app/api/notebooks.py:379），两者语义不同，这里要的是原型说的「最近创建」。
                .order_by(Notebook.created_at.desc(), Notebook.id.desc())
                .limit(recent_limit)
            )
        )
        .scalars()
        .all()
    )

    # ── 2) 结果通道接口：在线 / 离线 ────────────────────────────────────
    ep_rows = (
        await db.execute(
            select(ApiEndpoint.status, func.count(), func.max(ApiEndpoint.last_called_at))
            .join(Contract, Contract.id == ApiEndpoint.contract_id)
            .where(or_(ApiEndpoint.owner_id == uid, Contract.buyer_id == uid, Contract.provider_id == uid))
            .group_by(ApiEndpoint.status)
        )
    ).all()
    ep_by_status = {status: (int(count), last) for status, count, last in ep_rows}
    online = ep_by_status.get(ApiEndpointStatus.ENABLED.value, (0, None))[0]
    offline = ep_by_status.get(ApiEndpointStatus.DISABLED.value, (0, None))[0]
    ep_total = sum(count for count, _ in ep_by_status.values())
    last_called_candidates = [last for _, last in ep_by_status.values() if last is not None]
    last_called_at = max(last_called_candidates).isoformat() if last_called_candidates else None
    # 接口累计调用数按合约聚合一次，供结果集区块复用（避免逐包 N+1）
    calls_by_contract = {
        str(contract_id): int(calls or 0)
        for contract_id, calls in (
            await db.execute(
                select(ApiEndpoint.contract_id, func.sum(ApiEndpoint.calls_used))
                .join(Contract, Contract.id == ApiEndpoint.contract_id)
                .where(
                    or_(ApiEndpoint.owner_id == uid, Contract.buyer_id == uid, Contract.provider_id == uid)
                )
                .group_by(ApiEndpoint.contract_id)
            )
        ).all()
    }

    # ── 3) 今日调用 / 今日操作日志 ──────────────────────────────────────
    calls_count, calls_failures = await _audit_window_counts(
        db, since=today_start, until=None, actions=CALL_ACTIONS
    )
    platform_scope = role in PLATFORM_AUDIT_ROLES
    ops_count, ops_failures = await _audit_window_counts(
        db, since=today_start, until=None, user_id=None if platform_scope else uid
    )

    # ── 4) 内存档位（已分配，非实测） ───────────────────────────────────
    live_sessions = (
        (
            await db.execute(
                select(SandboxSession).where(
                    SandboxSession.user_id == uid, SandboxSession.status.in_(CONCURRENT_STATUSES)
                )
            )
        )
        .scalars()
        .all()
    )

    def _reserved_mb(sessions: list[SandboxSession]) -> int:
        return sum(int((sess.resource_limits or {}).get("memory_mb") or 0) for sess in sessions)

    debug_sessions = [s for s in live_sessions if s.sandbox_mode == DEBUG_SANDBOX_MODE]
    exec_sessions = [s for s in live_sessions if s.sandbox_mode != DEBUG_SANDBOX_MODE]

    run_rows = (
        await db.execute(
            select(ComputeRun.status, func.count())
            .join(Contract, Contract.id == ComputeRun.contract_id)
            .where(
                or_(Contract.buyer_id == uid, Contract.provider_id == uid),
                ComputeRun.status.in_((ComputeRunStatus.RUNNING.value, ComputeRunStatus.PENDING.value)),
            )
            .group_by(ComputeRun.status)
        )
    ).all()
    runs_by_status = {status: int(count) for status, count in run_rows}
    running_runs = runs_by_status.get(ComputeRunStatus.RUNNING.value, 0)
    pending_runs = runs_by_status.get(ComputeRunStatus.PENDING.value, 0)

    tenant_quota = sandbox_manager.get_tenant_quota(str(uid))
    tenant_memory_mb = int(tenant_quota.get("max_memory_mb") or 0) or None

    # ── 5) 近 N 天两类趋势 ───────────────────────────────────────────
    # 逐日半开区间计数，不用 CAST(created_at AS DATE)：那个写法在 sqlite 上会被数值亲和
    # 吐成同一个桶（GET /api/v1/monitoring/task-trend 就踩在这个上面），而本仓库门在
    # sqlite 上跑。索引 ix_audit_logs_created_action (created_at, action) 为这类区间计数服务。
    # 桶按日期升序、最后一桶恒为今天（原型「近 7 天」从旧到新画），绝不产出未来日期桶。
    trend_days: list[dict[str, Any]] = []
    for back in range(days - 1, -1, -1):
        day_start = today_start - timedelta(days=back)
        day_end = day_start + timedelta(days=1)
        debug_count, _ = await _audit_window_counts(
            db, since=day_start, until=day_end, user_id=uid, actions=DEBUG_TREND_ACTIONS
        )
        exec_count, _ = await _audit_window_counts(
            # 执行趋势同样按【谁触发的审计】口径，与本页「非平台全局汇总」一致；
            # 与下方执行内存卡的口径不同（那是合约当事方），差异写进 trend.note。
            db, since=day_start, until=day_end, user_id=uid, actions=EXEC_TREND_ACTIONS
        )
        trend_days.append({"date": day_start.date().isoformat(), "debug": debug_count, "exec": exec_count})

    # ── 6) 最新结果集 ──────────────────────────────────────────────────
    bundle_rows = (
        (
            await db.execute(
                select(DeliveryBundle, Contract.contract_no)
                .join(Contract, Contract.id == DeliveryBundle.contract_id)
                .where(or_(Contract.buyer_id == uid, Contract.provider_id == uid))
                .order_by(DeliveryBundle.created_at.desc(), DeliveryBundle.id.desc())
                .limit(recent_limit)
            )
        )
        .all()
    )
    latest_result_sets: list[dict[str, Any]] = []
    for bundle, contract_no in bundle_rows:
        # 「异常」只从打包当时落在行上的两个结论派生；两列在迁移 0021 之前是 NULL，
        # NULL 语义是「未记录」，不得回填成「正常」（app/models/delivery_bundle.py:36-38）。
        t5 = bundle.t5_review_status
        watermark = bundle.watermark_status
        if t5 is None and watermark is None:
            state, state_reason = None, "交付包未记录审查/水印状态（迁移 0021 之前的历史行）"
        elif t5 == "partially_reviewed":
            state, state_reason = "abnormal", "T5 出口审查为部分审查（存在未审查的二进制或空文件）"
        elif t5 == "passed_with_findings":
            state, state_reason = "abnormal", "T5 出口审查带发现项"
        else:
            state, state_reason = "normal", None
        latest_result_sets.append(
            {
                "bundle_id": str(bundle.id),
                "contract_id": str(bundle.contract_id),
                "contract_no": contract_no,
                # 文件名就是交付包对象名 delivery/<bundle_id>.zip（app/api/deliveries.py:283）
                "file_name": bundle.object_name,
                "file_count": bundle.file_count,
                "size_bytes": bundle.size_bytes,
                "row_count": bundle.row_count,
                "created_at": bundle.created_at.isoformat() if bundle.created_at else None,
                # 该合约下一个接口都没有时为 null：口径不适用，不是「调用 0 次」
                "call_count": calls_by_contract.get(str(bundle.contract_id)),
                "t5_review_status": t5,
                "watermark_status": watermark,
                "state": state,
                "state_reason": state_reason,
            }
        )

    return {
        "generated_at": now.isoformat(),
        "scope": {
            "user_id": str(uid),
            "username": current_user.username,
            "organization": getattr(current_user, "organization", None),
            "role": role,
            "note": "本页数字按当前主体统计（接口/结果集/执行按合约当事方），非平台全局汇总；例外：calls_today 是全平台今日对外调用审计数（外部 HMAC 调用无人的主体，无法按账号归因）。",
        },
        "dev_tasks": {
            "total": task_total,
            "active": active,
            "ended": ended,
            "status_caliber": {
                "active": "status != archived（含 draft）",
                "ended": "status == " + NotebookStatus.ARCHIVED.value,
            },
        },
        "task_filters": {"all": task_total, "active": active, "ended": ended},
        "recent_tasks": [
            {
                "id": str(nb.id),
                "task_no": nb.task_no,
                "title": nb.title,
                "status": nb.status,
                "debug_method": nb.debug_method,
                "created_at": nb.created_at.isoformat() if nb.created_at else None,
            }
            for nb in recent_rows
        ],
        "export_interfaces": {
            "total": ep_total,
            "online": online,
            "offline": offline,
            "status_caliber": {
                "online": ApiEndpointStatus.ENABLED.value,
                "offline": ApiEndpointStatus.DISABLED.value,
            },
        },
        "calls_today": {
            "count": calls_count,
            "failures": calls_failures,
            "last_called_at": last_called_at,
            "window": "utc_day",
            "actions": list(CALL_ACTIONS),
            "source": "audit_logs",
        },
        "ops_log_today": {
            "count": ops_count,
            "failures": ops_failures,
            "visibility": "platform" if platform_scope else "self",
            "window": "utc_day",
            "source": "audit_logs",
            "note": (
                "平台级今日操作日志（当前主体持平台审计角色）"
                if platform_scope
                else "本人今日操作日志：GET /api/v1/audit/records 对非运营/监管/管理角色返回 403，"
                "首页不显示平台级数字，只按 AuditLog.user_id 统计当前账号自己的记录。"
            ),
        },
        "memory": {
            "quota_mb": tenant_memory_mb,
            "quota_source": "sandbox_manager 租户内存配额（进程内计数，Redis 镜像；重启后由会话重算）",
            "debug": {
                "reserved_mb": _reserved_mb(debug_sessions),
                "active_sessions": len(debug_sessions),
                "state": "busy" if debug_sessions else "idle",
                "per_task_ceiling_mb": DEBUG_MEMORY_MAX_MB,
                "used_mb": None,
                "measured": False,
            },
            "exec": {
                "reserved_mb": _reserved_mb(exec_sessions),
                "active_sessions": len(exec_sessions),
                "running_runs": running_runs,
                "pending_runs": pending_runs,
                # 原型的三态：占用中 / 待执行 / 空闲
                "state": "busy" if (running_runs or exec_sessions) else ("queued" if pending_runs else "idle"),
                "per_task_ceiling_mb": EXEC_MEMORY_MAX_MB,
                "used_mb": None,
                "measured": False,
            },
        },
        "trend": {
            "days": trend_days,
            "window_days": days,
            "series": {"debug": list(DEBUG_TREND_ACTIONS), "exec": list(EXEC_TREND_ACTIONS)},
            "source": "audit_logs",
            "note": (
                "调试=在线调试运行（单元格与整本运行），执行=正式生产运行请求；两者都按当前主体的"
                "审计行统计（执行内存卡则按合约当事方口径）；0 表示当天确实没有这类动作。"
            ),
        },
        "latest_result_sets": latest_result_sets,
        "call_count_caliber": "该合约下 OpenAPI 接口的累计调用次数（api_endpoints.calls_used 之和），非单个结果文件的下载次数",
        "not_collected": [dict(item) for item in _NOT_COLLECTED],
    }
