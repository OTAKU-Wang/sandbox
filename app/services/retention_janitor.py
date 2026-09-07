"""W12: retention janitor — bounded, audited GC for growth tables.

Targets audit_logs / alert_records / merkle_leaves. Compliance-conservative
defaults: audit logs and merkle leaves are NEVER purged (retention=0 means
"keep forever"), alerts default to 180 days, and dry-run is on — an actual
delete requires an explicit operator decision. Every cycle writes an audit
record (``retention.purge``) whether or not rows were removed.
"""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

_ADVISORY_LOCK_KEY = "cds_retention_janitor"


async def _purge_table(db: AsyncSession, model, batch: int, cutoff: datetime) -> int:
    """Delete rows older than the cutoff in id batches (SQLite has no
    DELETE ... LIMIT, so the id set is selected first)."""
    total = 0
    while True:
        id_rows = await db.execute(select(model.id).where(model.created_at < cutoff).limit(batch))
        ids = [row[0] for row in id_rows.all()]
        if not ids:
            break
        await db.execute(delete(model).where(model.id.in_(ids)))
        total += len(ids)
        if len(ids) < batch:
            break
    return total


async def purge_cycle(db: AsyncSession) -> dict[str, int]:
    """Run one retention pass over all configured tables.

    Returns {table_name: removed_count} (dry-run: removed stays 0 and the
    candidate count is logged). Single-table failures are logged as
    warnings (value -1) and do not abort the remaining tables.
    """
    from app.core.config import get_settings

    settings = get_settings()
    now = datetime.now(timezone.utc)
    plan = [
        ("audit_logs", "app.models.audit_log", "AuditLog", settings.AUDIT_LOG_RETENTION_DAYS),
        ("alert_records", "app.models.alert", "AlertRecord", settings.ALERT_RETENTION_DAYS),
        ("merkle_leaves", "app.models.merkle_leaf", "MerkleLeaf", settings.MERKLE_LEAF_RETENTION_DAYS),
    ]

    results: dict[str, int] = {}
    lock_acquired = False
    if db.bind.dialect.name == "postgresql":
        lock_result = await db.execute(
            text("SELECT pg_try_advisory_lock(hashtext(:key))"), {"key": _ADVISORY_LOCK_KEY}
        )
        lock_acquired = bool(lock_result.scalar())
        if not lock_acquired:
            logger.info("[RetentionJanitor] Another replica holds the janitor lock — skipping cycle")
            return {"lock": 0}

    try:
        for table_name, module_name, class_name, retention_days in plan:
            try:
                if retention_days <= 0:
                    results[table_name] = 0
                    continue
                module = __import__(module_name, fromlist=[class_name])
                model = getattr(module, class_name)
                cutoff = now - timedelta(days=retention_days)
                if settings.RETENTION_JANITOR_DRY_RUN:
                    count_result = await db.execute(
                        select(func.count()).select_from(model).where(model.created_at < cutoff)
                    )
                    candidates = count_result.scalar() or 0
                    results[table_name] = 0
                    logger.info(
                        "[RetentionJanitor] DRY-RUN %s: %d rows older than %dd would be purged",
                        table_name, candidates, retention_days,
                    )
                    continue
                removed = await _purge_table(db, model, settings.RETENTION_JANITOR_BATCH, cutoff)
                results[table_name] = removed
                if removed:
                    logger.info(
                        "[RetentionJanitor] Purged %d rows from %s (retention=%dd)",
                        removed, table_name, retention_days,
                    )
            except Exception as e:
                logger.warning("[RetentionJanitor] Table %s purge failed: %s", table_name, e)
                results[table_name] = -1
    finally:
        if lock_acquired:
            await db.execute(text("SELECT pg_advisory_unlock(hashtext(:key))"), {"key": _ADVISORY_LOCK_KEY})

    try:
        from app.services.audit_service import audit_service

        await audit_service.log(
            db,
            action="retention.purge",
            resource_type="retention",
            detail={
                "dry_run": settings.RETENTION_JANITOR_DRY_RUN,
                "results": results,
                "ran_at": now.isoformat(),
            },
        )
    except Exception as e:
        logger.warning("[RetentionJanitor] Purge audit write failed: %s", e)
    return results
