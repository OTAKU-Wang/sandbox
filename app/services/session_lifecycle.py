"""Session lifecycle service — expiration cleanup and cascade termination.

Gap A2/D1 (ai-sandbox-gap-review-20260906): expired sandbox sessions were only
cleaned via a manual admin endpoint. This module provides the single
termination primitive (``terminate_session``) plus a background cleanup loop
registered in the app lifespan.

The termination primitive releases ALL resources attached to a session:
state machine transition, container, session key (including the persisted
wrapped payload — crypto-erase), non-terminal tasks, network policy, and
tenant quota. It is reused by the contract-termination cascade (gap A1).
"""
import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.sandbox_session import SandboxSession, SessionStatus
from app.models.kms import KeyMetadata, KeyStatus
from app.models.network_policy import NetworkPolicy
from app.services.sandbox_manager import (
    is_session_expired,
    get_resource_limits,
    release_tenant_usage,
    get_sandbox_manager,
)
from app.services.session_state_machine import session_state_machine

logger = logging.getLogger(__name__)

# All non-terminal session statuses eligible for expiry cleanup.
# Terminal statuses (completed/failed/terminated/revoked) are skipped by
# is_session_expired anyway; SUSPENDED sessions still consume resources and
# must be reclaimed once past their timeout.
ACTIVE_SESSION_STATUSES = [
    SessionStatus.PENDING.value,
    SessionStatus.KEY_DISTRIBUTING.value,
    SessionStatus.PROVISIONING.value,
    SessionStatus.READY.value,
    SessionStatus.RUNNING.value,
    SessionStatus.SUSPENDED.value,
]

# Non-terminal ORM task statuses cancelled when a session is terminated.
_ACTIVE_TASK_STATUSES = ["pending", "code_scanning", "running", "output_review"]


async def terminate_session(session: SandboxSession, db: AsyncSession, reason: str) -> bool:
    """Terminate a sandbox session and release all associated resources.

    Single termination primitive used by expiry cleanup, the contract
    termination cascade (gap A1) and the admin cleanup endpoint.

    Returns:
        True when the state machine accepted the transition and the session
        was terminated; False when the transition was rejected (e.g. the
        session is already in a terminal state).
    """
    current = SessionStatus(session.status)
    transition = session_state_machine.validate_transition(current, SessionStatus.TERMINATED)
    if not transition.success:
        logger.warning("[SessionLifecycle] Cannot terminate session %s: %s", session.id, transition.error)
        return False

    from app.core.metrics import record_session_transition

    session.status = SessionStatus.TERMINATED.value
    session.ended_at = datetime.now(timezone.utc)
    session.error_message = session.error_message or reason
    record_session_transition(current.value, SessionStatus.TERMINATED.value)

    # Destroy container
    if session.container_id:
        try:
            runtime = await get_sandbox_manager()
            runtime.terminate(session.container_id)
        except Exception as e:
            logger.warning("[SessionLifecycle] Container termination failed for %s: %s", session.id, e)

        # Round 40: snapshot GC — remove the workspace snapshot archives so
        # terminated sessions stop holding disk. Operators who want
        # "restore after terminate" durability can set
        # CDS_SESSION_SNAPSHOT_GC_ON_TERMINATE=false.
        try:
            from app.core.config import get_settings

            if get_settings().SESSION_SNAPSHOT_GC_ON_TERMINATE:
                import shutil

                from app.services.sandbox_runtime import sandbox_runtime
                from app.services.session_snapshots import snapshots_dir

                workspace = sandbox_runtime.get_workspace(session.container_id, session.sandbox_level)
                if workspace:
                    sdir = snapshots_dir(Path(workspace))
                    if sdir.exists():
                        shutil.rmtree(sdir, ignore_errors=True)
                        logger.info("[SessionLifecycle] Snapshot GC removed %s", sdir)
        except Exception as e:
            logger.warning("[SessionLifecycle] Snapshot GC failed for %s: %s", session.id, e)

    # Destroy session key + crypto-erase persisted wrapped payload (gap B3)
    if session.session_key_id:
        from app.services.kms_service import kms_service
        kms_service.destroy_key(session.session_key_id)
        key_result = await db.execute(
            select(KeyMetadata).where(KeyMetadata.key_id == session.session_key_id)
        )
        key_meta = key_result.scalar_one_or_none()
        if key_meta:
            key_meta.status = KeyStatus.DESTROYED.value
            key_meta.destroyed_at = datetime.now(timezone.utc)
            key_meta.destroy_reason = reason
            key_meta.wrapped_payload = None
            key_meta.sm2_encrypted_payload = None

    # Cancel non-terminal tasks in the session
    try:
        from app.models.sandbox_task import SandboxTask, TaskStatus as ORMTaskStatus
        tasks_result = await db.execute(
            select(SandboxTask).where(
                SandboxTask.session_id == session.id,
                SandboxTask.status.in_(_ACTIVE_TASK_STATUSES),
            )
        )
        for task in tasks_result.scalars().all():
            task.status = ORMTaskStatus.CANCELLED.value
            task.error_message = task.error_message or f"Session terminated: {reason}"
            task.completed_at = datetime.now(timezone.utc)
    except Exception as e:
        logger.warning("[SessionLifecycle] Task cancellation failed for %s: %s", session.id, e)

    # Remove network policy enforcement and mark policy inactive
    try:
        from app.services.network_policy import network_policy_engine
        await network_policy_engine.remove_policy(str(session.id))
    except Exception as e:
        logger.warning("[SessionLifecycle] Network policy removal failed for %s: %s", session.id, e)
    net_result = await db.execute(
        select(NetworkPolicy).where(NetworkPolicy.session_id == str(session.id))
    )
    net_policy = net_result.scalar_one_or_none()
    if net_policy:
        net_policy.active = False

    # Release tenant quota
    limits = get_resource_limits(session.sandbox_level)
    release_tenant_usage(
        str(session.user_id),
        cpu_cores=limits["cpu_cores"],
        memory_mb=limits["memory_mb"],
        disk_mb=limits["disk_mb"],
    )
    return True


async def pause_session(session: SandboxSession, db: AsyncSession, reason: str) -> bool:
    """Pause a live session (state preserved, workspace/keys kept).

    W9 idle auto-pause primitive — mirrors the POST /{id}/pause route
    semantics without its authorization layer.

    Returns:
        True when the state machine accepted RUNNING/READY → SUSPENDED.
    """
    current = SessionStatus(session.status)
    if current not in (SessionStatus.RUNNING, SessionStatus.READY):
        return False
    transition = session_state_machine.validate_transition(current, SessionStatus.SUSPENDED)
    if not transition.success:
        logger.warning("[SessionLifecycle] Cannot pause session %s: %s", session.id, transition.error)
        return False

    from app.core.metrics import record_session_transition

    session.pre_pause_status = session.status
    session.status = SessionStatus.SUSPENDED.value
    record_session_transition(current.value, SessionStatus.SUSPENDED.value)
    await db.flush()
    logger.info("[SessionLifecycle] Session %s auto-paused (%s)", session.id, reason)
    return True


async def cleanup_expired_sessions(db: AsyncSession) -> int:
    """Reclaim expired sandbox sessions (W9 policy aware).

    Sessions with ``idle_policy="pause"`` (or the global default when set)
    are SUSPENDED instead of terminated — workspace, files and keys are
    preserved for auto_resume. Sessions already SUSPENDED past expiry are
    terminated: pause has preserved state once, but resources must not be
    held indefinitely.
    """
    from app.core.config import get_settings

    settings = get_settings()
    result = await db.execute(
        select(SandboxSession).where(SandboxSession.status.in_(ACTIVE_SESSION_STATUSES))
    )
    cleaned_ids: list[str] = []
    paused_ids: list[str] = []
    for session in result.scalars().all():
        if not is_session_expired(session):
            continue
        policy = session.idle_policy or settings.SESSION_DEFAULT_IDLE_POLICY
        if policy == "pause" and session.status != SessionStatus.SUSPENDED.value:
            if await pause_session(session, db, reason="session_idle_autopause"):
                paused_ids.append(str(session.id))
                continue
        if await terminate_session(session, db, reason="session_expired"):
            cleaned_ids.append(str(session.id))

    if cleaned_ids or paused_ids:
        from app.services.audit_service import audit_service
        await audit_service.log(
            db,
            action="session.expired_cleanup",
            resource_type="sandbox_session",
            detail={
                "cleaned": len(cleaned_ids),
                "auto_paused": len(paused_ids),
                "session_ids": cleaned_ids,
                "paused_session_ids": paused_ids,
                "cleanup_time": datetime.now(timezone.utc).isoformat(),
            },
        )
    return len(cleaned_ids)


async def cleanup_expired_dev_sessions() -> int:
    """Expire dev sandbox sessions past their max_duration_seconds.

    Dev sessions live in Redis (or in-memory fallback) and previously had no
    TTL sweeper — the Redis key TTL only removes the record, not the runtime
    resources.
    """
    from app.services.data_product_sandbox import dev_sandbox

    try:
        sessions = await dev_sandbox.list_sessions()
    except Exception as e:
        logger.warning("[SessionLifecycle] Dev session listing failed: %s", e)
        return 0

    now = datetime.now(timezone.utc)
    cleaned = 0
    for s in sessions:
        if s.status not in ACTIVE_SESSION_STATUSES:
            continue
        try:
            created = s.created_at
            if created.tzinfo is None:
                # Redis round-trip may drop tzinfo — normalize to UTC
                created = created.replace(tzinfo=timezone.utc)
            elapsed = (now - created).total_seconds()
        except TypeError:
            continue
        if elapsed <= s.config.max_duration_seconds:
            continue
        try:
            await dev_sandbox.terminate_session(s.session_id)
            cleaned += 1
        except Exception as e:
            logger.warning("[SessionLifecycle] Dev session termination failed for %s: %s", s.session_id, e)
    return cleaned


async def _terminate_expired_contracts(db: AsyncSession) -> int:
    """Auto-terminate ACTIVE contracts past their valid_until (gap A1)."""
    # Lazy import: contract_service imports this module for the cascade.
    from app.services.contract_service import contract_service
    try:
        return await contract_service.terminate_expired_contracts(db)
    except Exception as e:
        logger.error("[SessionLifecycle] Contract expiry sweep failed: %s", e)
        return 0


async def session_cleanup_loop(interval_seconds: float = 300.0) -> None:
    """Background loop: periodically reclaim expired sessions and contracts.

    Mirrors ``kms_service._ttl_cleanup_loop``: survives single-round errors,
    exits cleanly on cancellation.
    """
    from app.core.database import async_session

    while True:
        try:
            await asyncio.sleep(interval_seconds)
            async with async_session() as db:
                cleaned = await cleanup_expired_sessions(db)
                dev_cleaned = await cleanup_expired_dev_sessions()
                contracts_expired = await _terminate_expired_contracts(db)
                # W14: heartbeat-overdue nodes get health_state=stale + alert
                try:
                    from app.api.sandbox_nodes import detect_stale_nodes, recover_stale_nodes
                    await detect_stale_nodes(db)
                    # W18: hopeless heartbeats take the node offline
                    await recover_stale_nodes(db)
                except Exception as e:
                    logger.error("[SessionLifecycle] Node stale detection failed: %s", e)
                # W12: retention janitor rides the same sweep (same cadence,
                # fewer background tasks); it self-audits.
                janitor_results: dict[str, int] = {}
                try:
                    from app.core.config import get_settings
                    from app.services.retention_janitor import purge_cycle

                    if get_settings().RETENTION_JANITOR_ENABLED:
                        janitor_results = await purge_cycle(db)
                except Exception as e:
                    logger.error("[SessionLifecycle] Retention janitor failed: %s", e)
                await db.commit()
            if cleaned or dev_cleaned or contracts_expired or any(v for v in janitor_results.values()):
                logger.info(
                    "[SessionLifecycle] Sweep: %d sessions, %d dev sessions, %d contracts expired, janitor=%s",
                    cleaned, dev_cleaned, contracts_expired, janitor_results,
                )
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("[SessionLifecycle] Cleanup error: %s", e)
            await asyncio.sleep(60)  # Back off on error
