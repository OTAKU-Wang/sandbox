"""W12: retention janitor — dry-run default, bounded deletes, purge audit."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import get_settings
from app.models.alert import AlertRecord
from app.models.audit_log import AuditLog
from app.services.retention_janitor import purge_cycle


@pytest.fixture(autouse=True)
def _janitor_settings(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "RETENTION_JANITOR_ENABLED", True)
    monkeypatch.setattr(settings, "RETENTION_JANITOR_BATCH", 2)
    monkeypatch.setattr(settings, "AUDIT_LOG_RETENTION_DAYS", 0)
    monkeypatch.setattr(settings, "MERKLE_LEAF_RETENTION_DAYS", 0)
    monkeypatch.setattr(settings, "ALERT_RETENTION_DAYS", 7)
    yield settings


async def _mk_alert(db_session, *, age_days: float):
    alert = AlertRecord(
        id=uuid.uuid4(),
        dedup_key=f"retention-test-{age_days}-{uuid.uuid4().hex[:8]}",
        alert_type="retention_test",
        severity="info",
        status="resolved",
        message=f"old-{age_days}",
        created_at=datetime.now(timezone.utc) - timedelta(days=age_days),
    )
    db_session.add(alert)
    await db_session.flush()
    return alert


from sqlalchemy import func as _func, select as _select


async def _count(model, db_session):
    result = await db_session.execute(_select(_func.count()).select_from(model))
    return result.scalar() or 0


def func_count():
    return _func.count()


@pytest.mark.asyncio
async def test_dry_run_counts_but_keeps_rows(db_session, _janitor_settings):
    await _mk_alert(db_session, age_days=30)
    _janitor_settings.RETENTION_JANITOR_DRY_RUN = True
    results = await purge_cycle(db_session)
    assert results["alert_records"] == 0
    assert await _count(AlertRecord, db_session) == 1


@pytest.mark.asyncio
async def test_real_purge_removes_expired_and_audits(db_session, _janitor_settings):
    await _mk_alert(db_session, age_days=30)
    await _mk_alert(db_session, age_days=1)  # inside retention window
    _janitor_settings.RETENTION_JANITOR_DRY_RUN = False

    results = await purge_cycle(db_session)
    assert results["alert_records"] == 1
    assert await _count(AlertRecord, db_session) == 1

    purge_audits = await db_session.execute(
        select_audit_with_action("retention.purge")
    )
    records = purge_audits.scalars().all()
    assert records, "purge cycle must write a retention.purge audit record"
    import json

    detail = records[-1].detail
    assert detail.get("dry_run") is False
    assert detail.get("results", {}).get("alert_records") == 1


def select_audit_with_action(action: str):
    from sqlalchemy import select

    return select(AuditLog).where(AuditLog.action == action)


@pytest.mark.asyncio
async def test_retention_zero_tables_never_purged(db_session, _janitor_settings):
    old_audit = AuditLog(
        id=uuid.uuid4(),
        action="legacy.event",
        resource_type="test",
        created_at=datetime.now(timezone.utc) - timedelta(days=3650),
    )
    db_session.add(old_audit)
    await db_session.flush()
    _janitor_settings.RETENTION_JANITOR_DRY_RUN = False

    results = await purge_cycle(db_session)
    assert results["audit_logs"] == 0
    assert results["merkle_leaves"] == 0
    from sqlalchemy import select

    remaining = await db_session.execute(
        select(func_count()).select_from(AuditLog).where(AuditLog.action == "legacy.event")
    )
    assert (remaining.scalar() or 0) == 1


@pytest.mark.asyncio
async def test_batched_delete_clears_all_rows(db_session, _janitor_settings):
    for i in range(5):
        await _mk_alert(db_session, age_days=30 + i)
    _janitor_settings.RETENTION_JANITOR_DRY_RUN = False
    _janitor_settings.RETENTION_JANITOR_BATCH = 2

    results = await purge_cycle(db_session)
    assert results["alert_records"] == 5
    assert await _count(AlertRecord, db_session) == 0
