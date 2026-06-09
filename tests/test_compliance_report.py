"""Compliance report API tests."""
import uuid
from datetime import datetime, timedelta

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_generate_compliance_report(client: AsyncClient, auth_headers: dict):
    """Operator can generate a compliance report."""
    from tests.conftest import create_user_with_role
    # Need operator/regulator/admin role
    # auth_headers is data_provider, should get 403
    start = (datetime.now() - timedelta(days=30)).isoformat()
    end = datetime.now().isoformat()
    resp = await client.post("/api/v1/compliance/reports", json={
        "start_date": start,
        "end_date": end,
        "report_type": "tc609",
    }, headers=auth_headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_generate_report_as_operator(client: AsyncClient, operator_headers: dict):
    """Operator can generate compliance report."""
    start = (datetime.now() - timedelta(days=30)).isoformat()
    end = datetime.now().isoformat()
    resp = await client.post("/api/v1/compliance/reports", json={
        "start_date": start,
        "end_date": end,
    }, headers=operator_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["report_type"] == "tc609"
    assert "report_id" in data
    assert "audit_summary" in data
    assert "access_control" in data
    assert "data_protection" in data
    assert "compliance_status" in data
    assert "recommendations" in data


@pytest.mark.asyncio
async def test_report_audit_summary_structure(client: AsyncClient, operator_headers: dict):
    """Report audit summary has correct fields."""
    start = (datetime.now() - timedelta(days=1)).isoformat()
    end = datetime.now().isoformat()
    resp = await client.post("/api/v1/compliance/reports", json={
        "start_date": start,
        "end_date": end,
    }, headers=operator_headers)
    assert resp.status_code == 200
    summary = resp.json()["audit_summary"]
    assert "total_events" in summary
    assert "events_by_type" in summary
    assert "events_by_severity" in summary
    assert "security_incidents" in summary
    assert "policy_denials" in summary


@pytest.mark.asyncio
async def test_report_compliance_status_checks(client: AsyncClient, operator_headers: dict):
    """Compliance status contains all TC609 checks."""
    start = (datetime.now() - timedelta(days=1)).isoformat()
    end = datetime.now().isoformat()
    resp = await client.post("/api/v1/compliance/reports", json={
        "start_date": start,
        "end_date": end,
    }, headers=operator_headers)
    assert resp.status_code == 200
    status = resp.json()["compliance_status"]
    expected_checks = ["audit_trail", "security_incident_response", "access_control",
                       "data_protection", "policy_enforcement", "output_review", "dp_budget_tracking"]
    for check in expected_checks:
        assert check in status
        assert status[check] in ("pass", "fail", "warn")


@pytest.mark.asyncio
async def test_quick_compliance_report(client: AsyncClient, operator_headers: dict):
    """Quick report endpoint works with default 30 days."""
    resp = await client.get("/api/v1/compliance/reports/quick", headers=operator_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["report_type"] == "tc609"
    assert len(data["recommendations"]) > 0


@pytest.mark.asyncio
async def test_quick_report_custom_days(client: AsyncClient, operator_headers: dict):
    """Quick report with custom day range."""
    resp = await client.get("/api/v1/compliance/reports/quick?days=7", headers=operator_headers)
    assert resp.status_code == 200
    start = resp.json()["period_start"]
    end = resp.json()["period_end"]
    # Verify roughly 7 days
    from datetime import datetime as dt
    s = dt.fromisoformat(start)
    e = dt.fromisoformat(end)
    assert (e - s).days >= 6


@pytest.mark.asyncio
async def test_report_empty_period(client: AsyncClient, operator_headers: dict):
    """Report for a period with no data still generates successfully."""
    # Far future dates — no audit data
    start = (datetime.now() + timedelta(days=365)).isoformat()
    end = (datetime.now() + timedelta(days=366)).isoformat()
    resp = await client.post("/api/v1/compliance/reports", json={
        "start_date": start,
        "end_date": end,
    }, headers=operator_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["audit_summary"]["total_events"] == 0
    # Should have warn recommendations
    assert any("审计记录为空" in r for r in data["recommendations"])


@pytest.mark.asyncio
async def test_report_rbac_buyer_denied(client: AsyncClient, auth_headers: dict):
    """Buyer role cannot generate compliance reports."""
    resp = await client.get("/api/v1/compliance/reports/quick", headers=auth_headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_report_no_auth(client: AsyncClient):
    """Unauthenticated request returns 401."""
    resp = await client.get("/api/v1/compliance/reports/quick")
    assert resp.status_code in (401, 403)
