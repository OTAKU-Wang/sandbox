"""P2-5（D-e2e-18，ss-19 §10.1-5）：MinIO 一致性巡检.

对账 `data_resources.status=ready` 与存储对象实际存在性：库里 ready 只证明
「当年入库成功」，不证明对象今天还在（早期 fixture 注入时 `S3Error NoSuchKey`
的教训——票据诚实记 `injected: []`，但 ready 行照常留着，没人对账）。

诚实三态（写 `data_resources.integrity_status`，DDL=0034）：
  ok           head 确认对象在场
  missing      存储可达而对象不在（或 ready 行没有存储引用）
  uncheckable  探测不出结论（MinIO 不可达/陌生桶/FS 权限）——**禁冒认 ok**；
               从未巡检的存量行也默认 uncheckable（integrity_checked_at=NULL
               可区分「从未查」与「查了查不出」）。

纪律：
- 只读探测 + 只写 integrity_* 两列，不碰上传/下载热路径、不碰 status 机。
- 不 commit：事务归调用方（会话清理骨架的 sweep / get_db 请求事务）——
  app/core 铁律，handler 中途 commit 会制造 stale-row/MissingGreenlet。
- 审计走唯一漏斗 audit_service.log（action=integrity.patrol），巡检结果
  对操作者可见靠 API，对取证可见靠审计，不新开面。
- 调度双门默认关：INTEGRITY_PATROL_ENABLED（主闸，默认 false）×
  interval>0；手动触发端点是显式人工意图，不受调度双门约束（见 api/integrity.py）。
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.data_resource import DataResource, ResourceStatus
from app.services.audit_service import audit_service
from app.services.storage_service import storage_service

logger = logging.getLogger(__name__)

INTEGRITY_OK = "ok"
INTEGRITY_MISSING = "missing"
INTEGRITY_UNCHECKABLE = "uncheckable"
INTEGRITY_STATES = (INTEGRITY_OK, INTEGRITY_MISSING, INTEGRITY_UNCHECKABLE)

# 详情列表封顶：审计 detail 与响应都不该被十万条 missing 撑爆
_DETAIL_ID_CAP = 50

# 进程内调度记时（monotonic）。诚实披露：进程重启=计时清零，重启后的首个
# sweep（若调度开着）就补一轮巡检——多巡检无害（幂等只读），漏巡检才有害。
_last_patrol_monotonic: float | None = None


async def patrol_cycle(
    db: AsyncSession,
    *,
    triggered_by: str = "scheduler",
    user_id: uuid.UUID | str | None = None,
) -> dict:
    """批量 head 对账一轮。返回汇总 dict（不 commit，见模块纪律）。"""
    settings = get_settings()
    limit = max(1, settings.INTEGRITY_PATROL_BATCH_SIZE)
    rows = (
        await db.execute(
            select(DataResource)
            .where(DataResource.status == ResourceStatus.READY.value)
            .order_by(DataResource.integrity_checked_at.asc().nulls_first())
            .limit(limit)
        )
    ).scalars().all()

    now = datetime.now(timezone.utc)
    counts = {"checked": 0, INTEGRITY_OK: 0, INTEGRITY_MISSING: 0, INTEGRITY_UNCHECKABLE: 0}
    missing_ids: list[str] = []
    uncheckable_ids: list[str] = []
    for res in rows:
        # minio SDK 是同步阻塞的：整段探测丢给线程，事件循环不卡（批量 head
        # 在网络型不可达下每张都可能吃满连接超时）
        state, reason = await asyncio.to_thread(storage_service.stat_presence, res.storage_path)
        res.integrity_status = state
        res.integrity_checked_at = now
        counts["checked"] += 1
        counts[state] += 1
        if state == INTEGRITY_MISSING and len(missing_ids) < _DETAIL_ID_CAP:
            missing_ids.append(str(res.id))
        if state == INTEGRITY_UNCHECKABLE and len(uncheckable_ids) < _DETAIL_ID_CAP:
            uncheckable_ids.append(str(res.id))

    await audit_service.log(
        db,
        action="integrity.patrol",
        resource_type="data_resource",
        user_id=user_id,
        detail={
            **counts,
            "triggered_by": triggered_by,
            "batch_limit": limit,
            "missing_ids": missing_ids,
            "uncheckable_ids": uncheckable_ids,
            "uncheckable_semantics": "探测不出结论（MinIO 不可达等），非对象丢失判定；禁冒认 ok 亦禁误判 missing",
        },
    )
    if counts[INTEGRITY_MISSING] or counts[INTEGRITY_UNCHECKABLE]:
        logger.warning(
            "[IntegrityPatrol] checked=%d ok=%d missing=%d uncheckable=%d (by %s)",
            counts["checked"], counts[INTEGRITY_OK], counts[INTEGRITY_MISSING],
            counts[INTEGRITY_UNCHECKABLE], triggered_by,
        )
    return {**counts, "missing_ids": missing_ids, "uncheckable_ids": uncheckable_ids}


async def maybe_run_patrol_cycle(db: AsyncSession) -> dict:
    """会话清理骨架的搭载面（janitor 同型双门，默认全关）：
    主闸 INTEGRITY_PATROL_ENABLED=false → 直接空转；interval<=0 = 只留手动面。
    返回 {} 表示本轮未到期/未启用。"""
    global _last_patrol_monotonic
    settings = get_settings()
    if not settings.INTEGRITY_PATROL_ENABLED or settings.INTEGRITY_PATROL_INTERVAL_SECONDS <= 0:
        return {}
    now = time.monotonic()
    if (
        _last_patrol_monotonic is not None
        and now - _last_patrol_monotonic < settings.INTEGRITY_PATROL_INTERVAL_SECONDS
    ):
        return {}
    # 先占位再执行：两轮 sweep 交错时宁可少跑一轮（幂等只读，下轮再补）
    _last_patrol_monotonic = now
    return await patrol_cycle(db, triggered_by="scheduler")


async def integrity_summary(db: AsyncSession) -> dict:
    """ready 资源的巡检面汇总（只读查询端点用）。never_checked 单独计：
    默认 uncheckable ≠ 查过查不出，两档不许混（诚实）。"""
    rows = (
        await db.execute(
            select(
                DataResource.integrity_status,
                func.count(DataResource.id),
                func.count(DataResource.integrity_checked_at),
            )
            .where(DataResource.status == ResourceStatus.READY.value)
            .group_by(DataResource.integrity_status)
        )
    ).all()
    summary = {s: 0 for s in INTEGRITY_STATES}
    never_checked = 0
    ready_total = 0
    for state, cnt, checked_cnt in rows:
        if state in summary:
            summary[state] += cnt
        ready_total += cnt
        never_checked += cnt - checked_cnt
    return {"ready_total": ready_total, "never_checked": never_checked, **summary}
