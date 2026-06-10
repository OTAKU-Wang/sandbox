import pytest

from app.models.alert import AlertNotificationStatus, AlertStatus
from app.services.alert_center import AlertCenterService, alert_center
from app.services.alert_engine import Alert, AlertSeverity, AlertType


def make_alert(session_id: str = "sess-1", user_id: str = "user-1") -> Alert:
    return Alert(
        alert_type=AlertType.CONSECUTIVE_REJECTION,
        severity=AlertSeverity.HIGH,
        message=f"Session {session_id} had repeated rejections",
        user_id=user_id,
        session_id=session_id,
        metadata={"resource_type": "sandbox_session", "resource_id": session_id},
    )


@pytest.mark.asyncio
async def test_alert_center_persists_and_deduplicates_alerts(db_session):
    service = AlertCenterService()

    records = await service.ingest_alerts(db_session, [make_alert(), make_alert()])
    listed, total = await service.list_alerts(db_session)

    assert len(records) == 2
    assert total == 1
    assert listed[0].occurrence_count == 2
    assert listed[0].status == AlertStatus.OPEN.value
    assert listed[0].dedup_key == records[0].dedup_key


@pytest.mark.asyncio
async def test_alert_center_acknowledge_resolve_and_reopen(db_session):
    service = AlertCenterService()
    records = await service.ingest_alerts(db_session, [make_alert()])
    record = records[0]

    acknowledged = await service.acknowledge_alert(db_session, record.id, "operator-1", note="triaged")
    assert acknowledged.status == AlertStatus.ACKNOWLEDGED.value
    assert acknowledged.acknowledged_by == "operator-1"

    resolved = await service.resolve_alert(db_session, record.id, "operator-2", note="false positive")
    assert resolved.status == AlertStatus.RESOLVED.value
    assert resolved.resolved_by == "operator-2"
    assert resolved.resolution_note == "false positive"

    await service.ingest_alerts(db_session, [make_alert()])
    reopened, total = await service.list_alerts(db_session)
    assert total == 1
    assert reopened[0].status == AlertStatus.OPEN.value
    assert reopened[0].occurrence_count == 2


@pytest.mark.asyncio
async def test_alert_center_records_webhook_notification_results(db_session, monkeypatch):
    service = AlertCenterService()

    async def fake_post(target, payload):
        return {"target": target, "ok": True, "status_code": 204, "attempt": 1}

    monkeypatch.setattr(service, "_post_webhook", fake_post)
    records = await service.ingest_alerts(
        db_session,
        [make_alert()],
        notify=True,
        notification_targets=["https://alerts.example.test/hook"],
    )

    assert records[0].notification_status == AlertNotificationStatus.DELIVERED.value
    assert records[0].notification_results[0]["target"] == "https://alerts.example.test/hook"


@pytest.mark.asyncio
async def test_monitoring_alerts_api_lists_and_updates_persistent_alerts(
    client,
    db_session,
    operator_headers,
):
    records = await alert_center.ingest_alerts(db_session, [make_alert(session_id="sess-api")])
    alert_id = str(records[0].id)

    response = await client.get("/api/v1/monitoring/alerts?include_legacy=false", headers=operator_headers)
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["id"] == alert_id
    assert payload["items"][0]["status"] == AlertStatus.OPEN.value

    ack = await client.post(
        f"/api/v1/monitoring/alerts/{alert_id}/acknowledge",
        headers=operator_headers,
        json={"note": "owner assigned"},
    )
    assert ack.status_code == 200
    assert ack.json()["status"] == AlertStatus.ACKNOWLEDGED.value

    resolved = await client.post(
        f"/api/v1/monitoring/alerts/{alert_id}/resolve",
        headers=operator_headers,
        json={"note": "contained"},
    )
    assert resolved.status_code == 200
    assert resolved.json()["status"] == AlertStatus.RESOLVED.value
    assert resolved.json()["resolution_note"] == "contained"
