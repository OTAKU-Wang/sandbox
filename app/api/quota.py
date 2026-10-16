"""SS-17 配额与用量只读面（开发门户「配额用量」页的唯一数据源）。

规划出处：specs/developer-portal-review-plan.md P4（配额页无真实数据源）。本模块只做**聚合读取**，
不新增任何计数器、不改任何限额——限额的唯一写入方仍是会话创建路径
（app/api/sandbox_sessions.py 的 quota_manager.reset/set_limits）与合约限额预填。

三条铁律（项目 honest degradation）：
1. 每个数字都必须有真实落库/落 Redis 的来源。后端没采的东西返回 ``null``，并在
   ``not_collected`` 里点名原因，由前端渲染「未采集」——绝不把 0 当成「没用过」。
2. 计数器的真实后端（Redis 原子计数 / 进程内兜底）如实披露：进程内兜底不跨副本、
   重启即失，用户看不到这一点就会误读数字。判据取 ``quota_manager._redis``，
   与 app/main.py 启动时注入的是同一个句柄（读私有属性是刻意的：main.py 亦以此注入）。
3. 只读 ⇒ 无副作用 ⇒ 可安全轮询；因此不写审计（审计写路径本身有成本，用量页是高频读）。

挂载：prefix ``/api/v1/deliveries``、依赖 ``require_group("gateway")``（见 app/main.py）——
沿用已映射的功能组（specs/developer-portal-review-plan.md P4 定的就是本路径）。
app/core/scopes.py 的 GROUP_PREFIXES 未收录的新前缀会被 ``resolve_group`` 判为未映射，
对机器主体（SS-12 API Key）默认拒绝；而结果通道的调用方本就持 ``gateway:*``。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_business_user
from app.models.api_endpoint import ApiEndpoint, ApiEndpointStatus
from app.models.contract import Contract
from app.models.sandbox_session import SandboxSession, SessionStatus
from app.models.user import User
from app.services.quota_manager import QuotaType, quota_manager
from app.services.sandbox_manager import get_tenant_quota

router = APIRouter()
logger = logging.getLogger(__name__)

# 并发会话上限的**同源副本**：真实判据在 app/api/sandbox_sessions.py:326
# （``MAX_ACTIVE_SESSIONS = 5``，handler 函数内的局部常量，未导出，本模块无法 import）。
# 改那边必须同时改这里——漂移的后果是页面少报/多报可用额度，而不是放行越限会话
# （放行与否只由那个 handler 决定），所以刻意选择"复制 + 显式标注"而不是另造一份配置。
MAX_ACTIVE_SESSIONS = 5

# 同上同源副本：计入并发的状态集与创建路径的 ``SandboxSession.status.in_([...])`` 一致。
CONCURRENT_STATUSES = (
    SessionStatus.PROVISIONING.value,
    SessionStatus.RUNNING.value,
    SessionStatus.READY.value,
)

# QuotaType → 展示元数据。unit 必须写清楚：dp_epsilon 的计数单位是 ε×1e-4
# （见 quota_manager.reset：``epsilon * 10000`` 取整），按「ε」直接展示会夸大 1e4 倍。
_QUOTA_META: dict[QuotaType, tuple[str, str]] = {
    QuotaType.ROWS: ("输出行数", "rows"),
    QuotaType.BYTES: ("输出字节", "bytes"),
    QuotaType.API_CALLS: ("API 调用数", "calls"),
    QuotaType.GPU_SECONDS: ("GPU 秒", "gpu_seconds"),
    QuotaType.DP_EPSILON: ("差分隐私预算", "epsilon×1e-4"),
    QuotaType.DURATION_SECONDS: ("运行时长", "seconds"),
}

# 明确点名「后端没采」的缺口：前端据此显示未采集 + 原因，而不是画一个 0。
_NOT_COLLECTED: list[dict[str, str]] = [
    {
        "key": "gateway_daily_rows_bytes",
        "label": "网关按 app_id 的当日行/字节配额",
        "reason": (
            "quota_manager 的 gwquota:* 计数器按 app_id（网关凭证）分片，"
            "聚合到当前用户需要先枚举其全部合约凭证；本端点未做该聚合。"
            "单合约真实值见 GET /api/v1/gateway/{contract_id}/usage。"
        ),
    },
    {
        "key": "session_resource_spec",
        "label": "单会话资源规格（cpu/内存/磁盘）",
        "reason": (
            "会话规格逐合约由 contracts.sandbox_resource_spec 指定，账号级无统一限额；"
            "账号级只有租户总量（tenant_resources）。"
        ),
    },
    {
        "key": "session_wall_clock",
        "label": "会话已消耗墙钟时长",
        "reason": (
            "duration_seconds 计数器仅在会话内执行路径递增；未走该路径的会话无该计数，"
            "由逐会话 quotas 如实披露（无记录即 0，见 sources.sandbox_counters.note）。"
        ),
    },
]


def _counter_source() -> dict[str, Any]:
    """计数后端的真实形态（honest degradation 的披露位）。"""
    if getattr(quota_manager, "_redis", None) is not None:
        return {
            "backend": "redis",
            "label": "Redis 原子计数",
            "cross_replica": True,
            "survives_restart": True,
            "note": "计数存于 Redis（Lua 原子递增），多副本共享、重启不丢。",
        }
    return {
        "backend": "in_memory",
        "label": "进程内兜底计数（Redis 未连接）",
        "cross_replica": False,
        "survives_restart": False,
        "note": (
            "Redis 未连接，quota_manager 退化为本进程内存计数：进程重启即归零，"
            "多副本部署下各副本各算各的。此处数字仅代表当前进程视角，非平台权威用量。"
        ),
    }


def _tenant_source(redis_connected: bool) -> dict[str, Any]:
    """租户资源用量的真实形态：本地 dict 为读路径，Redis 为写穿透镜像。"""
    if redis_connected:
        return {
            "backend": "in_memory+redis_write_through",
            "label": "进程内 dict（Redis 写穿透镜像）",
            "cross_replica": True,
            "survives_restart": True,
            "note": (
                "读路径是进程内 dict（W3：同步读以保持既有调用方语义），"
                "每次变更写穿透 Redis 哈希 cds:tenant_quota:{user_id}，重启后由 "
                "restore_tenant_quotas 回填。刚重启且尚无新变更时，展示值以回填为准。"
            ),
        }
    return {
        "backend": "in_memory",
        "label": "进程内 dict（未配置 Redis）",
        "cross_replica": False,
        "survives_restart": False,
        "note": "未连接 Redis：租户用量仅存于本进程内存，重启即失、多副本不共享。",
    }


@router.get("/quota-usage")
async def quota_usage(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_business_user),
) -> dict[str, Any]:
    """当前主体（用户 / API Key 的 issuer）的配额与用量快照，只读。

    返回分三块：沙箱会话（并发数 + 逐会话 quota_manager 计数）、租户资源
    （sandbox_manager 的 cpu/mem/disk 限额与占用）、OpenAPI 结果通道
    （api_endpoints 的 calls_used/quota_total）。缺数一律 null + not_collected 说明。
    """
    from app.core import redis as redis_module

    uid = current_user.id
    # 只看已连上的客户端（get_redis 仅在 ping 成功后赋值），不能拿「配了 URL」当「有镜像」
    redis_connected = getattr(redis_module, "_redis_client", None) is not None

    status_rows = (
        await db.execute(
            select(SandboxSession.status, func.count())
            .where(SandboxSession.user_id == uid)
            .group_by(SandboxSession.status)
        )
    ).all()
    by_status = {status: int(count) for status, count in status_rows}
    total_sessions = sum(by_status.values())
    active_count = sum(count for status, count in by_status.items() if status in CONCURRENT_STATUSES)

    active_rows = (
        (
            await db.execute(
                select(SandboxSession)
                .where(SandboxSession.user_id == uid, SandboxSession.status.in_(CONCURRENT_STATUSES))
                .order_by(SandboxSession.created_at.desc())
            )
        )
        .scalars()
        .all()
    )

    # 逐会话 quota_manager 计数（活跃会话数量受 MAX_ACTIVE_SESSIONS 约束，读取成本有界）
    per_session: list[dict[str, Any]] = []
    for sess in active_rows:
        quotas: list[dict[str, Any]] = []
        for qt in QuotaType:
            label, unit = _QUOTA_META[qt]
            try:
                usage = await quota_manager.get_usage(sess.id, qt)
                limit = await quota_manager.get_limit(sess.id, qt)
            except Exception as exc:  # 单个计数器读失败不拖垮整页：如实标 null
                logger.warning("[quota] counter read failed session=%s type=%s: %s", sess.id, qt.value, exc)
                usage, limit = None, None
            quotas.append(
                {
                    "type": qt.value,
                    "label": label,
                    "unit": unit,
                    "usage": usage,
                    # quota_manager 语义：limit 0 == 未设限（check_and_increment 里
                    # ``limit <= 0`` 直接放行）。0 不是「额度为 0」，故返回 null 交前端
                    # 渲染「未设限」，避免把不限额误读成一用就超。
                    "limit": limit if limit else None,
                }
            )
        per_session.append(
            {
                "session_id": str(sess.id),
                "status": sess.status,
                # 历史级别原样透出（SS-13 前的 L0/L1/L2/L3/k8s 会话仍需在列表里可读）
                "sandbox_level": sess.sandbox_level,
                "contract_id": sess.contract_id,
                "created_at": sess.created_at.isoformat() if sess.created_at else None,
                "quotas": quotas,
            }
        )

    tenant_quota = get_tenant_quota(str(uid))
    # used_* 仅在有会话占用过资源后才写入 dict（update_tenant_usage）；
    # 缺键 = 后端确实没有该数字 ⇒ null（未采集），不能显示 0。
    used = {
        field: tenant_quota[field]
        for field in ("used_cpu_cores", "used_memory_mb", "used_disk_mb")
        if field in tenant_quota
    }

    ep_rows = (
        (
            await db.execute(
                select(ApiEndpoint, Contract.contract_no, Contract.buyer_id, Contract.provider_id)
                .join(Contract, Contract.id == ApiEndpoint.contract_id)
                .where(
                    or_(
                        ApiEndpoint.owner_id == uid,
                        Contract.buyer_id == uid,
                        Contract.provider_id == uid,
                    )
                )
                .order_by(ApiEndpoint.created_at.desc())
                .limit(200)
            )
        )
        .all()
    )
    endpoints: list[dict[str, Any]] = []
    calls_used_total = 0
    quota_total_sum: int | None = None
    for ep, contract_no, buyer_id, provider_id in ep_rows:
        calls = int(ep.calls_used or 0)
        calls_used_total += calls
        if ep.quota_total is not None:
            quota_total_sum = (quota_total_sum or 0) + int(ep.quota_total)
        endpoints.append(
            {
                "id": str(ep.id),
                "name": ep.name,
                "slug": ep.slug,
                "status": ep.status,
                "contract_id": str(ep.contract_id),
                "contract_no": contract_no,
                "role": "provider" if provider_id == uid else "consumer",
                "calls_used": calls,
                # quota_total 可空 = 创建时未设总额度（不限额），非「额度 0」
                "quota_total": ep.quota_total,
                "calls_remaining": (
                    max(0, int(ep.quota_total) - calls) if ep.quota_total is not None else None
                ),
                "rate_limit_per_minute": ep.rate_limit_per_minute,
                "last_called_at": ep.last_called_at.isoformat() if ep.last_called_at else None,
                "created_at": ep.created_at.isoformat() if ep.created_at else None,
            }
        )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": {
            "user_id": str(uid),
            "username": current_user.username,
            "role": getattr(current_user, "role", None),
            "note": "本页数字按当前主体（API Key 为其签发人身份）统计，非平台全局汇总。",
        },
        "sources": {
            "sandbox_counters": _counter_source(),
            "tenant_resources": _tenant_source(redis_connected),
            "openapi_calls": {
                "backend": "database",
                "label": "api_endpoints 表（OpenAPI 通道调用落库）",
                "cross_replica": True,
                "survives_restart": True,
                "note": "calls_used 由结果通道每次调用落库递增（app/services/delivery_service.py），与网关同库同事务。",
            },
        },
        "sandbox_sessions": {
            "active": active_count,
            "active_limit": MAX_ACTIVE_SESSIONS,
            "active_remaining": max(0, MAX_ACTIVE_SESSIONS - active_count),
            "total": total_sessions,
            "by_status": by_status,
            "per_session": per_session,
            "note": (
                f"并发上限 {MAX_ACTIVE_SESSIONS} 与创建路径同源"
                "（app/api/sandbox_sessions.py 的 MAX_ACTIVE_SESSIONS）。"
            ),
        },
        "tenant_resources": {
            "limits": {
                "cpu_cores": tenant_quota["max_cpu_cores"],
                "memory_mb": tenant_quota["max_memory_mb"],
                "disk_mb": tenant_quota["max_disk_mb"],
                "max_sessions": tenant_quota["max_sessions"],
            },
            # 空 dict = 该账号从未有会话占用过资源：前端显示「未采集」而不是 0/8 核
            "used": used or None,
        },
        "openapi_endpoints": {
            "count": len(endpoints),
            "items": endpoints,
            "totals": {
                "calls_used": calls_used_total,
                # 无任何端点设置总额度时返回 null：「总配额」这个数后端确实没有
                "quota_total": quota_total_sum,
                "calls_remaining": (
                    max(0, quota_total_sum - calls_used_total) if quota_total_sum is not None else None
                ),
            },
            "status_enabled": sum(1 for ep in endpoints if ep["status"] == ApiEndpointStatus.ENABLED.value),
        },
        "not_collected": _NOT_COLLECTED,
    }
