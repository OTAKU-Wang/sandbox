"""合约维度的自动计算调度器（SS-15）。

一个 tick 只做三件事：到点取合约、按幂等键抢占一次运行、执行已发布的**生产**负载。
幂等键 = 合约 + 计划时点，靠数据库唯一约束抢占，多副本部署时同一时点只会跑一次；
同一合约上一轮没跑完时本轮记 skipped_overlap（不排队堆叠，避免雪崩式并发沙箱）。
"""
import asyncio
import base64
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.core.database import async_session
from app.core.errors import CDSError
from app.models.compute_run import ComputeRun, ComputeRunStatus, ComputeTrigger
from app.models.contract import Contract, ComputeFrequency, WorkloadEnv
from app.models.user import User
from app.models.workload import Workload, WorkloadStatus
from app.services.audit_service import audit_service

logger = logging.getLogger(__name__)

_ACTIVE_RUN_STATUSES = (ComputeRunStatus.PENDING.value, ComputeRunStatus.RUNNING.value)


def _tz(name: str | None):
    if not name:
        return timezone.utc
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        logger.warning("unknown compute_tz=%s, falling back to UTC", name)
        return timezone.utc


def compute_next_run(
    *,
    auto_compute: bool,
    frequency: str,
    minute: int,
    hour: int,
    tz_name: str | None,
    after: datetime | None = None,
) -> datetime | None:
    if not auto_compute or frequency == ComputeFrequency.MANUAL.value:
        return None
    now = after or datetime.now(timezone.utc)
    tz = _tz(tz_name)
    local = now.astimezone(tz)
    if frequency == ComputeFrequency.HOURLY.value:
        candidate = local.replace(minute=max(0, min(59, minute)), second=0, microsecond=0)
        if candidate <= local:
            candidate = candidate + timedelta(hours=1)
    elif frequency == ComputeFrequency.DAILY.value:
        candidate = local.replace(hour=max(0, min(23, hour)), minute=max(0, min(59, minute)), second=0, microsecond=0)
        if candidate <= local:
            candidate = candidate + timedelta(days=1)
    else:
        return None
    return candidate.astimezone(timezone.utc)


def idempotency_key(contract_id: uuid.UUID, scheduled_for: datetime) -> str:
    return f"{contract_id}:{scheduled_for.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"


def _entrypoint_with_params(entrypoint: str, params: dict | None) -> str:
    """把运行参数以 CDS_PARAMS 注入入口代码开头（base64 避开任何引号/编码歧义）。

    不伪造环境：参数作为被审代码的一部分进入执行与审计记录，沙箱内可见可取。
    """
    if not params:
        return entrypoint
    payload = base64.b64encode(json.dumps(params, ensure_ascii=False).encode("utf-8")).decode("ascii")
    return (
        "import base64 as _cds_b64, json as _cds_json\n"
        f'CDS_PARAMS = _cds_json.loads(_cds_b64.b64decode("{payload}").decode("utf-8"))\n'
        + entrypoint
    )


async def _production_workload(db, contract_id: uuid.UUID) -> Workload | None:
    res = await db.execute(
        select(Workload)
        .where(
            Workload.contract_id == contract_id,
            Workload.env == WorkloadEnv.PRODUCTION.value,
            Workload.status == WorkloadStatus.PUBLISHED.value,
            Workload.is_latest.is_(True),
        )
        .order_by(Workload.version.desc())
    )
    return res.scalars().first()


async def _collect_output_manifest(db, session) -> tuple[list[dict[str, Any]] | None, str | None]:
    try:
        if session.sandbox_level == "l2c":
            from app.api.sandbox_sessions import _l2c_file_op

            out = await _l2c_file_op(session, "list")
            files = out.get("files") if isinstance(out, dict) else None
            return (files or []), None
        from app.api.sandbox_sessions import _session_workspace_or_400
        from app.services import session_files as sf

        return sf.list_files(_session_workspace_or_400(session)), None
    except Exception as exc:
        logger.warning("output manifest collection failed (session=%s): %s", session.id, exc)
        return None, str(exc)[:500]


async def execute_run(db, run: ComputeRun, contract: Contract) -> ComputeRun:
    """执行一次计算运行：生产沙箱 + 已发布负载 + 真实输出清单。"""
    from app.api.sandbox_sessions import create_sandbox_session, execute_in_sandbox
    from app.schemas.sandbox_session import SandboxExecuteRequest, SandboxSessionCreate
    from app.models.sandbox_session import SandboxSession
    from app.services.session_lifecycle import terminate_session
    from app.services.session_state_machine import session_state_machine
    from app.services.workflow_service import resolve_sandbox_mode

    run.status = ComputeRunStatus.RUNNING.value
    run.started_at = datetime.now(timezone.utc)
    await db.flush()

    user_res = await db.execute(select(User).where(User.id == contract.buyer_id))
    actor = user_res.scalar_one_or_none()
    if actor is None:
        run.status = ComputeRunStatus.FAILED.value
        run.error_message = "合约买方用户不存在，无法以业务身份运行"
        run.finished_at = datetime.now(timezone.utc)
        await db.flush()
        return run

    workload = None
    if run.workload_id is not None:
        wl_res = await db.execute(select(Workload).where(Workload.id == run.workload_id))
        workload = wl_res.scalar_one_or_none()
        if (
            workload is None
            or workload.contract_id != contract.id
            or workload.env != WorkloadEnv.PRODUCTION.value
            or workload.status != WorkloadStatus.PUBLISHED.value
        ):
            run.status = ComputeRunStatus.FAILED.value
            run.error_message = "指定的生产版本不存在或已不再是本合约的 published 生产版本"
            run.finished_at = datetime.now(timezone.utc)
            await db.flush()
            return run
    if workload is None:
        workload = await _production_workload(db, contract.id)
    if workload is None or not (workload.entrypoint or "").strip():
        run.status = ComputeRunStatus.FAILED.value
        run.error_message = "该合约没有处于 published 状态的生产工作负载，请先在测试沙箱验证后发布"
        run.finished_at = datetime.now(timezone.utc)
        await db.flush()
        return run

    run.workload_id = workload.id
    product_ids = workload.data_product_ids or []
    if not product_ids:
        run.status = ComputeRunStatus.FAILED.value
        run.error_message = "生产工作负载未绑定数据产品"
        run.finished_at = datetime.now(timezone.utc)
        await db.flush()
        return run

    spec = {**(contract.sandbox_resource_spec or {}), **(workload.resource_spec or {}), **(run.resource_override or {})}
    session_id: uuid.UUID | None = None
    try:
        body = SandboxSessionCreate(
            data_product_id=uuid.UUID(str(product_ids[0])),
            sandbox_level="l2c",
            sandbox_mode=resolve_sandbox_mode(contract, "product_dev"),
            contract_id=str(contract.id),
            timeout_seconds=int(spec.get("timeout_seconds") or 1800),
            resource_limits=spec or None,
        )
        created = await create_sandbox_session(body=body, db=db, current_user=actor)
        session_id = created.id
        sess_res = await db.execute(select(SandboxSession).where(SandboxSession.id == session_id))
        sess = sess_res.scalar_one_or_none()
        if sess is not None:
            sess.env = WorkloadEnv.PRODUCTION.value
            sess.workload_id = workload.id
        run.session_id = session_id
        await db.flush()

        exec_res = await execute_in_sandbox(
            session_id=session_id,
            body=SandboxExecuteRequest(code=_entrypoint_with_params(workload.entrypoint, run.params), language=workload.language or "python"),
            db=db,
            current_user=actor,
        )
        exit_code = int(exec_res.get("exit_code", -1) if isinstance(exec_res, dict) else -1)
        stdout = str((exec_res or {}).get("output") or "")
        files, manifest_error = await _collect_output_manifest(db, sess) if sess is not None else (None, "session missing")
        run.output_files = files
        run.output_rows = int((exec_res or {}).get("output_rows") or 0)
        run.resource_usage = {
            "exit_code": exit_code,
            "output_bytes": len(stdout.encode("utf-8")),
            "output_blocked": bool((exec_res or {}).get("output_blocked")),
            "manifest_error": manifest_error,
        }
        if exit_code == 0 and not (exec_res or {}).get("output_blocked"):
            run.status = ComputeRunStatus.SUCCEEDED.value
        else:
            run.status = ComputeRunStatus.FAILED.value
            run.error_message = ((exec_res or {}).get("blocked_reason") or f"entrypoint 退出码 {exit_code}")[:2000]
    except Exception as exc:
        logger.warning("compute run failed contract=%s: %s", contract.id, exc)
        run.status = ComputeRunStatus.FAILED.value
        run.error_message = str(exc)[:2000]
    finally:
        run.finished_at = datetime.now(timezone.utc)
        if session_id is not None:
            try:
                sess_res = await db.execute(select(SandboxSession).where(SandboxSession.id == session_id))
                sess = sess_res.scalar_one_or_none()
                if sess is not None and session_state_machine.is_active(sess.status):
                    await terminate_session(sess, db, reason="SS-15 计划计算运行结束")
            except Exception as exc:
                logger.warning("compute run session cleanup failed: %s", exc)
        await db.flush()

    await _dispatch_run_events(db, run, contract)
    return run


async def _dispatch_run_events(db, run: ComputeRun, contract: Contract) -> None:
    from app.services.delivery_service import dispatch_event

    event = "compute_run.succeeded" if run.status == ComputeRunStatus.SUCCEEDED.value else "compute_run.failed"
    payload = {
        "contract_id": str(contract.id),
        "contract_no": contract.contract_no,
        "run_id": str(run.id),
        "status": run.status,
        "trigger": run.trigger,
        "scheduled_for": run.scheduled_for.isoformat() if run.scheduled_for else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "output_rows": run.output_rows,
        "output_files": run.output_files,
        "error_message": run.error_message,
    }
    try:
        await dispatch_event(db, event, payload, contract_id=contract.id, user_id=contract.buyer_id)
    except Exception as exc:
        logger.warning("delivery dispatch failed run=%s: %s", run.id, exc)


async def claim_run(
    db,
    contract: Contract,
    *,
    trigger: str,
    scheduled_for: datetime | None,
    requested_by: uuid.UUID | None = None,
    workload_id: uuid.UUID | None = None,
    params: dict | None = None,
    resource_override: dict | None = None,
) -> ComputeRun | None:
    if workload_id is not None:
        res = await db.execute(select(Workload).where(Workload.id == workload_id))
        pinned = res.scalar_one_or_none()
        if (
            pinned is None
            or pinned.contract_id != contract.id
            or pinned.env != WorkloadEnv.PRODUCTION.value
            or pinned.status != WorkloadStatus.PUBLISHED.value
        ):
            raise CDSError(
                "指定的工作负载不是本合约已发布(published)的生产版本",
                code="WORKLOAD_NOT_PUBLISHED",
                http_status=400,
            )
    key = idempotency_key(contract.id, scheduled_for) if scheduled_for else None
    if key:
        existing = await db.execute(select(ComputeRun).where(ComputeRun.idempotency_key == key))
        found = existing.scalar_one_or_none()
        if found is not None:
            return None
    overlap = await db.execute(
        select(ComputeRun).where(ComputeRun.contract_id == contract.id, ComputeRun.status.in_(_ACTIVE_RUN_STATUSES)).order_by(ComputeRun.created_at.desc()).limit(1)
    )
    running = overlap.scalar_one_or_none()
    if running is not None:
        skipped = ComputeRun(
            contract_id=contract.id,
            trigger=trigger,
            scheduled_for=scheduled_for,
            idempotency_key=key,
            status=ComputeRunStatus.SKIPPED_OVERLAP.value,
            requested_by=requested_by,
            error_message=f"上一轮运行 {running.id} 尚未结束，本轮跳过",
            finished_at=datetime.now(timezone.utc),
        )
        db.add(skipped)
        try:
            await db.flush()
        except IntegrityError:
            await db.rollback()
            return None
        return None
    run = ComputeRun(
        contract_id=contract.id,
        trigger=trigger,
        scheduled_for=scheduled_for,
        idempotency_key=key,
        status=ComputeRunStatus.PENDING.value,
        requested_by=requested_by,
        workload_id=workload_id,
        params=params,
        resource_override=resource_override,
    )
    db.add(run)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        return None
    return run


async def run_due_contracts(now: datetime | None = None) -> dict[str, int]:
    counters = {"scanned": 0, "started": 0, "succeeded": 0, "failed": 0, "skipped": 0}
    moment = now or datetime.now(timezone.utc)
    async with async_session() as db:
        res = await db.execute(
            select(Contract).where(
                Contract.auto_compute.is_(True),
                Contract.status == "active",
                Contract.next_compute_at.is_not(None),
                Contract.next_compute_at <= moment,
            )
        )
        due = list(res.scalars().all())
        for contract in due:
            counters["scanned"] += 1
            scheduled_for = contract.next_compute_at
            run = await claim_run(db, contract, trigger=ComputeTrigger.SCHEDULE.value, scheduled_for=scheduled_for)
            if run is None:
                counters["skipped"] += 1
                contract.next_compute_at = compute_next_run(
                    auto_compute=contract.auto_compute,
                    frequency=contract.compute_frequency,
                    minute=contract.compute_minute,
                    hour=contract.compute_hour,
                    tz_name=contract.compute_tz,
                    after=moment,
                )
                await db.commit()
                continue
            counters["started"] += 1
            await db.commit()
            await execute_run(db, run, contract)
            contract.last_computed_at = run.finished_at
            contract.next_compute_at = compute_next_run(
                auto_compute=contract.auto_compute,
                frequency=contract.compute_frequency,
                minute=contract.compute_minute,
                hour=contract.compute_hour,
                tz_name=contract.compute_tz,
                after=moment,
            )
            await audit_service.log(
                db,
                action="compute.run.finished",
                resource_type="contract",
                user_id=contract.buyer_id,
                resource_id=str(contract.id),
                detail={"run_id": str(run.id), "status": run.status, "trigger": run.trigger},
            )
            await db.commit()
            counters["succeeded" if run.status == ComputeRunStatus.SUCCEEDED.value else "failed"] += 1
    return counters


async def compute_scheduler_loop(interval_seconds: int | None = None) -> None:
    settings = get_settings()
    interval = interval_seconds or getattr(settings, "COMPUTE_SCHEDULER_INTERVAL_SECONDS", 60)
    while True:
        try:
            counters = await run_due_contracts()
            if counters["scanned"]:
                logger.info("compute scheduler: %s", counters)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("compute scheduler tick failed: %s", exc)
        await asyncio.sleep(interval)
