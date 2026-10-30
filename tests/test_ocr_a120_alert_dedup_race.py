"""A120 — the alert de-duplication pre-check must not lose the batch on a race.

`alert_records.dedup_key` is UNIQUE, while `AlertCenterService.ingest_alerts()`
did SELECT-then-INSERT and flushed the whole batch at the end, so a row
committed by a concurrent collector turned into `IntegrityError` at flush time:
every alert in that batch — and, because it shares the request session, the
audit row written by the same request — was lost. The race is driven here the
only way SQLite in one connection can: a committed row plus a pre-check forced
to miss it.
"""
import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.alert import AlertRecord, AlertStatus
from app.services.alert_center import alert_center
from app.services.alert_engine import Alert, AlertSeverity, AlertType


def _alert(user_id: str) -> Alert:
    return Alert(
        alert_type=AlertType.ANOMALY_BURST,
        severity=AlertSeverity.HIGH,
        message=f"burst {user_id}",
        user_id=user_id,
        session_id="sess-1",
        metadata={"resource_type": "session", "resource_id": "sess-1"},
    )


async def _committed_record(db: AsyncSession, dedup_key: str, *, message: str) -> AlertRecord:
    record = AlertRecord(
        dedup_key=dedup_key,
        alert_type=AlertType.ANOMALY_BURST.value,
        severity=AlertSeverity.CRITICAL.value,
        status=AlertStatus.OPEN.value,
        message=message,
        user_id="whoever-won",
        alert_metadata={},
        occurrence_count=3,
    )
    db.add(record)
    await db.commit()
    return record


def _race_the_precheck(monkeypatch):
    real = type(alert_center)._get_by_dedup_key
    missed: set[str] = set()

    async def _miss_once(self, db, key):
        if key not in missed:
            missed.add(key)
            return None
        return await real(self, db, key)

    monkeypatch.setattr(type(alert_center), "_get_by_dedup_key", _miss_once)


async def test_racing_duplicate_key_merges_instead_of_raising(db_session: AsyncSession, monkeypatch):
    alert = _alert("user-race")
    key = alert_center.build_dedup_key(alert)
    await _committed_record(db_session, key, message="stale message")
    _race_the_precheck(monkeypatch)

    records = await alert_center.ingest_alerts(db_session, [alert])

    assert len(records) == 1
    assert records[0].dedup_key == key
    assert records[0].occurrence_count == 4, "the winner's count must keep counting, not reset"
    total = await db_session.scalar(select(func.count()).select_from(AlertRecord))
    assert total == 1, "the loser must not add a second row for the same key"


async def test_the_rest_of_the_batch_survives_the_race(db_session: AsyncSession, monkeypatch):
    loser = _alert("user-loser")
    key = alert_center.build_dedup_key(loser)
    await _committed_record(db_session, key, message="stale message")

    untouched = _alert("user-fresh")
    _race_the_precheck(monkeypatch)

    records = await alert_center.ingest_alerts(db_session, [loser, untouched])

    assert len(records) == 2
    keys = {r.dedup_key for r in records}
    assert keys == {key, alert_center.build_dedup_key(untouched)}
    rows = (await db_session.execute(select(AlertRecord))).scalars().all()
    assert len(rows) == 2, "one racing key must not take the whole batch down"


async def test_the_request_session_is_not_rolled_back(db_session: AsyncSession, monkeypatch):
    """Recovering from the race may not discard the caller's earlier work."""
    alert = _alert("user-session-state")
    key = alert_center.build_dedup_key(alert)
    await _committed_record(db_session, key, message="stale message")

    sibling = AlertRecord(
        dedup_key=f"unrelated-{uuid.uuid4().hex}",
        alert_type=AlertType.DP_BUDGET_EXHAUSTION.value,
        severity=AlertSeverity.MEDIUM.value,
        status=AlertStatus.OPEN.value,
        message="written before ingest in the same request",
        alert_metadata={},
    )
    db_session.add(sibling)
    _race_the_precheck(monkeypatch)

    await alert_center.ingest_alerts(db_session, [alert])
    await db_session.flush()

    found = await db_session.get(AlertRecord, sibling.id)
    assert found is not None and found.message.startswith("written before")


async def test_plain_first_ingest_is_unchanged(db_session: AsyncSession):
    records = await alert_center.ingest_alerts(db_session, [_alert("user-plain")])
    assert len(records) == 1
    assert records[0].occurrence_count == 1


async def test_in_batch_duplicates_still_merge_without_touching_the_db(db_session: AsyncSession):
    first, second = _alert("user-twice"), _alert("user-twice")
    records = await alert_center.ingest_alerts(db_session, [first, second])
    assert len(records) == 2
    assert {id(r) for r in records} == {id(records[0])}
    assert records[0].occurrence_count == 2
    total = await db_session.scalar(select(func.count()).select_from(AlertRecord))
    assert total == 1
