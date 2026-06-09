"""Tests for enhanced audit: statistics, export, compliance report."""
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_audit_statistics(client: AsyncClient, operator_headers: dict):
    """Audit statistics returns expected structure."""
    resp = await client.get("/api/v1/audit/statistics", headers=operator_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "total_records" in data
    assert "by_action" in data
    assert "by_resource_type" in data
    assert "anchored_count" in data
    assert "unanchored_count" in data


@pytest.mark.asyncio
async def test_audit_statistics_custom_days(client: AsyncClient, operator_headers: dict):
    """Audit statistics accepts custom days parameter."""
    resp = await client.get("/api/v1/audit/statistics?days=7", headers=operator_headers)
    assert resp.status_code == 200
    assert resp.json()["period_days"] == 7


@pytest.mark.asyncio
async def test_audit_export_csv(client: AsyncClient, operator_headers: dict):
    """Audit export returns CSV."""
    resp = await client.get("/api/v1/audit/export?format=csv&days=1", headers=operator_headers)
    assert resp.status_code == 200
    assert "text/csv" in resp.headers["content-type"]


@pytest.mark.asyncio
async def test_audit_export_json(client: AsyncClient, operator_headers: dict):
    """Audit export returns JSON."""
    resp = await client.get("/api/v1/audit/export?format=json&days=1", headers=operator_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "records" in data
    assert "total" in data


@pytest.mark.asyncio
async def test_compliance_report(client: AsyncClient, operator_headers: dict):
    """Compliance report returns expected structure."""
    resp = await client.get("/api/v1/audit/compliance-report", headers=operator_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["report_type"] == "compliance"
    assert "security_events" in data
    assert "sandbox_operations" in data
    assert "audit_coverage" in data
    assert "coverage_pct" in data["audit_coverage"]


@pytest.mark.asyncio
async def test_compliance_report_custom_days(client: AsyncClient, operator_headers: dict):
    """Compliance report accepts custom days."""
    resp = await client.get("/api/v1/audit/compliance-report?days=7", headers=operator_headers)
    assert resp.status_code == 200
    assert resp.json()["period_days"] == 7
