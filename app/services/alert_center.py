"""Persistent alert center with de-duplication, notification and disposition."""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Iterable
from uuid import UUID

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.alert import AlertNotificationStatus, AlertRecord, AlertStatus
from app.services.alert_engine import Alert

logger = logging.getLogger(__name__)


class AlertCenterService:
    """Production alert workflow service.

    The rule engine stays fast and in-memory. This service is the durable layer:
    it de-duplicates fired alerts, stores them, tracks notification delivery,
    and manages operator disposition.
    """

    def build_dedup_key(self, alert: Alert) -> str:
        metadata = alert.metadata or {}
        stable_identity = {
            "alert_type": alert.alert_type.value,
            "session_id": alert.session_id or "",
            "user_id": alert.user_id or "",
            "resource_type": metadata.get("resource_type") or "",
            "resource_id": metadata.get("resource_id") or "",
            "rule_id": metadata.get("rule_id") or "",
        }
        if not any(stable_identity[k] for k in ("session_id", "user_id", "resource_id", "rule_id")):
            stable_identity["message"] = alert.message
        raw = json.dumps(stable_identity, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(raw).hexdigest()

    async def ingest_alerts(
        self,
        db: AsyncSession,
        alerts: Iterable[Alert],
        *,
        notify: bool = False,
        notification_targets: list[str] | None = None,
    ) -> list[AlertRecord]:
        records: list[AlertRecord] = []
        seen: dict[str, AlertRecord] = {}
        now = datetime.now(timezone.utc)
        for alert in alerts:
            dedup_key = self.build_dedup_key(alert)
            existing = seen.get(dedup_key)
            if existing is None:
                existing = await self._get_by_dedup_key(db, dedup_key)
            if existing is None:
                existing = self._record_from_alert(alert, dedup_key, now)
                db.add(existing)
            else:
                self._merge_occurrence(existing, alert, now)
            seen[dedup_key] = existing
            records.append(existing)

        await db.flush()
        if notify and records:
            await self.dispatch_notifications(db, records, notification_targets=notification_targets)
        return records

    async def list_alerts(
        self,
        db: AsyncSession,
        *,
        status: str | None = None,
        severity: str | None = None,
        alert_type: str | None = None,
        skip: int = 0,
        limit: int = 20,
    ) -> tuple[list[AlertRecord], int]:
        query = select(AlertRecord)
        count_query = select(func.count()).select_from(AlertRecord)
        filters = []
        if status:
            filters.append(AlertRecord.status == status)
        if severity:
            filters.append(AlertRecord.severity == severity)
        if alert_type:
            filters.append(AlertRecord.alert_type == alert_type)
        for condition in filters:
            query = query.where(condition)
            count_query = count_query.where(condition)

        total_result = await db.execute(count_query)
        total = total_result.scalar() or 0
        result = await db.execute(query.order_by(AlertRecord.last_seen_at.desc()).offset(skip).limit(limit))
        return list(result.scalars().all()), total

    async def acknowledge_alert(
        self,
        db: AsyncSession,
        alert_id: UUID,
        actor_id: str,
        note: str | None = None,
    ) -> AlertRecord | None:
        record = await db.get(AlertRecord, alert_id)
        if record is None:
            return None
        if record.status == AlertStatus.RESOLVED.value:
            return record
        now = datetime.now(timezone.utc)
        record.status = AlertStatus.ACKNOWLEDGED.value
        record.acknowledged_by = actor_id
        record.acknowledged_at = now
        record.updated_at = now
        if note:
            record.resolution_note = note
        await db.flush()
        return record

    async def resolve_alert(
        self,
        db: AsyncSession,
        alert_id: UUID,
        actor_id: str,
        note: str | None = None,
    ) -> AlertRecord | None:
        record = await db.get(AlertRecord, alert_id)
        if record is None:
            return None
        now = datetime.now(timezone.utc)
        record.status = AlertStatus.RESOLVED.value
        record.resolved_by = actor_id
        record.resolved_at = now
        record.updated_at = now
        record.resolution_note = note
        if record.acknowledged_at is None:
            record.acknowledged_by = actor_id
            record.acknowledged_at = now
        await db.flush()
        return record

    async def dispatch_notifications(
        self,
        db: AsyncSession,
        records: Iterable[AlertRecord],
        *,
        notification_targets: list[str] | None = None,
    ) -> None:
        settings = get_settings()
        targets = notification_targets if notification_targets is not None else settings.ALERT_WEBHOOK_URLS
        records = list(records)
        if not targets:
            for record in records:
                record.notification_status = AlertNotificationStatus.NOT_CONFIGURED.value
                record.notification_results = []
                record.updated_at = datetime.now(timezone.utc)
            await db.flush()
            return

        for record in records:
            record.notification_status = AlertNotificationStatus.PENDING.value
            await db.flush()
            payload = self._notification_payload(record)
            results = []
            for target in targets:
                results.append(await self._post_webhook(target, payload))
            record.notification_results = results
            record.notification_status = (
                AlertNotificationStatus.DELIVERED.value
                if results and all(r.get("ok") for r in results)
                else AlertNotificationStatus.FAILED.value
            )
            record.updated_at = datetime.now(timezone.utc)
        await db.flush()

    async def _get_by_dedup_key(self, db: AsyncSession, dedup_key: str) -> AlertRecord | None:
        result = await db.execute(select(AlertRecord).where(AlertRecord.dedup_key == dedup_key))
        return result.scalar_one_or_none()

    def _record_from_alert(self, alert: Alert, dedup_key: str, now: datetime) -> AlertRecord:
        metadata = dict(alert.metadata or {})
        return AlertRecord(
            dedup_key=dedup_key,
            alert_type=alert.alert_type.value,
            severity=alert.severity.value,
            status=AlertStatus.OPEN.value,
            message=alert.message,
            user_id=alert.user_id,
            session_id=alert.session_id,
            resource_type=metadata.get("resource_type"),
            resource_id=metadata.get("resource_id"),
            alert_metadata=metadata,
            occurrence_count=1,
            first_seen_at=alert.timestamp or now,
            last_seen_at=alert.timestamp or now,
            created_at=now,
            updated_at=now,
        )

    def _merge_occurrence(self, record: AlertRecord, alert: Alert, now: datetime) -> None:
        record.occurrence_count += 1
        record.last_seen_at = alert.timestamp or now
        record.message = alert.message
        record.severity = alert.severity.value
        record.alert_metadata = dict(alert.metadata or {})
        record.updated_at = now
        if record.status == AlertStatus.RESOLVED.value:
            record.status = AlertStatus.OPEN.value
            record.resolved_by = None
            record.resolved_at = None
            record.resolution_note = "reopened by new occurrence"
        if record.status == AlertStatus.ACKNOWLEDGED.value:
            record.status = AlertStatus.OPEN.value
            record.acknowledged_by = None
            record.acknowledged_at = None

    @staticmethod
    def _notification_payload(record: AlertRecord) -> dict:
        return {
            "id": str(record.id),
            "type": record.alert_type,
            "severity": record.severity,
            "status": record.status,
            "message": record.message,
            "user_id": record.user_id,
            "session_id": record.session_id,
            "resource_type": record.resource_type,
            "resource_id": record.resource_id,
            "metadata": record.alert_metadata,
            "occurrence_count": record.occurrence_count,
            "last_seen_at": record.last_seen_at.isoformat() if record.last_seen_at else None,
        }

    async def _post_webhook(self, target: str, payload: dict) -> dict:
        settings = get_settings()
        timeout = httpx.Timeout(settings.ALERT_NOTIFICATION_TIMEOUT_SECONDS)
        attempts = max(1, int(settings.ALERT_NOTIFICATION_RETRIES))
        last_error = ""
        for attempt in range(1, attempts + 1):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    response = await client.post(target, json=payload)
                ok = 200 <= response.status_code < 300
                return {
                    "target": target,
                    "ok": ok,
                    "status_code": response.status_code,
                    "attempt": attempt,
                    "delivered_at": datetime.now(timezone.utc).isoformat(),
                }
            except Exception as e:
                last_error = str(e)
                logger.warning("[AlertCenter] webhook delivery failed target=%s attempt=%s error=%s", target, attempt, e)
        return {
            "target": target,
            "ok": False,
            "error": last_error,
            "attempt": attempts,
            "delivered_at": datetime.now(timezone.utc).isoformat(),
        }


alert_center = AlertCenterService()
