"""Tests for output gateway: format conversion, row limiting, policy enforcement."""
import json
import pytest
from httpx import AsyncClient

from app.services.output_gateway import OutputGateway, OutputPolicy, OutputFormat, VALID_FORMATS


@pytest.fixture
def gateway():
    return OutputGateway()


@pytest.fixture
def sample_data():
    return [
        {"id": 1, "name": "Alice", "score": 95.5},
        {"id": 2, "name": "Bob", "score": 87.3},
        {"id": 3, "name": "Charlie", "score": 92.1},
    ]


def test_valid_formats():
    """All expected formats are valid."""
    assert "csv" in VALID_FORMATS
    assert "json" in VALID_FORMATS
    assert "parquet" in VALID_FORMATS


def test_csv_conversion(gateway, sample_data):
    """CSV conversion produces valid CSV."""
    policy = OutputPolicy(allowed_output_formats=["csv"])
    result = gateway.process(sample_data, "csv", policy)
    assert result.success is True
    assert result.output_format == "csv"
    assert result.row_count == 3
    assert "id,name,score" in result.output_data
    assert "Alice" in result.output_data


def test_json_conversion(gateway, sample_data):
    """JSON conversion produces valid JSON."""
    policy = OutputPolicy(allowed_output_formats=["json"])
    result = gateway.process(sample_data, "json", policy)
    assert result.success is True
    assert result.output_format == "json"
    parsed = json.loads(result.output_data)
    assert len(parsed) == 3


def test_parquet_conversion(gateway, sample_data):
    """Parquet conversion produces bytes (or JSON fallback)."""
    policy = OutputPolicy(allowed_output_formats=["parquet"])
    result = gateway.process(sample_data, "parquet", policy)
    assert result.success is True
    assert result.output_format == "parquet"
    assert result.output_data is not None


def test_row_limiting(gateway, sample_data):
    """Output is truncated when exceeding max_output_rows."""
    policy = OutputPolicy(max_output_rows=2, allowed_output_formats=["json"])
    result = gateway.process(sample_data, "json", policy)
    assert result.success is True
    assert result.row_count == 2
    assert result.truncated is True
    parsed = json.loads(result.output_data)
    assert len(parsed) == 2


def test_row_limit_not_exceeded(gateway, sample_data):
    """No truncation when within limit."""
    policy = OutputPolicy(max_output_rows=100, allowed_output_formats=["json"])
    result = gateway.process(sample_data, "json", policy)
    assert result.truncated is False


def test_invalid_format(gateway, sample_data):
    """Invalid format returns error."""
    policy = OutputPolicy()
    result = gateway.process(sample_data, "xml", policy)
    assert result.success is False
    assert "Invalid output format" in result.error


def test_format_not_allowed_by_policy(gateway, sample_data):
    """Format not in policy returns error."""
    policy = OutputPolicy(allowed_output_formats=["csv"])
    result = gateway.process(sample_data, "json", policy)
    assert result.success is False
    assert "not allowed by contract" in result.error


def test_empty_data(gateway):
    """Empty data produces empty output."""
    policy = OutputPolicy(allowed_output_formats=["csv"])
    result = gateway.process([], "csv", policy)
    assert result.success is True
    assert result.row_count == 0
    assert result.output_data == ""


def test_gateway_redacts_noncritical_findings(gateway):
    """Non-critical DLP findings are redacted before release."""
    policy = OutputPolicy(allowed_output_formats=["json"])
    result = gateway.process(
        [{"name": "Alice", "phone": "13812345678"}],
        "json",
        policy,
        user_id="user-1",
        session_id="session-1",
    )
    assert result.success is True
    assert result.findings
    assert "13812345678" not in result.output_data
    assert "[REDACTED:phone]" in result.output_data
    assert result.security_report["findings_count"] == 1


def test_gateway_blocks_critical_findings(gateway):
    """Critical DLP findings block output release."""
    policy = OutputPolicy(allowed_output_formats=["json"])
    result = gateway.process(
        [{"id_card": "110101199001011234"}],
        "json",
        policy,
        user_id="user-1",
        session_id="session-1",
    )
    assert result.success is False
    assert result.output_data is None
    assert result.security_report["blocked"] is True


def test_default_policy():
    """Default policy has reasonable defaults."""
    policy = OutputPolicy()
    assert policy.max_output_rows == 10000
    assert "csv" in policy.allowed_output_formats
    assert "json" in policy.allowed_output_formats


@pytest.mark.asyncio
async def test_gateway_api_endpoint(client: AsyncClient, auth_headers: dict):
    """Gateway API endpoint processes output."""
    resp = await client.post("/api/v1/output-control/gateway", json={
        "data": [{"x": 1}, {"x": 2}],
        "output_format": "json",
        "max_output_rows": 100,
        "allowed_output_formats": ["json", "csv"],
    }, headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["row_count"] == 2


@pytest.mark.asyncio
async def test_gateway_api_format_not_allowed(client: AsyncClient, auth_headers: dict):
    """Gateway API rejects disallowed format."""
    resp = await client.post("/api/v1/output-control/gateway", json={
        "data": [{"x": 1}],
        "output_format": "parquet",
        "allowed_output_formats": ["csv"],
    }, headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is False


@pytest.mark.asyncio
async def test_formats_endpoint(client: AsyncClient):
    """Formats endpoint lists supported formats."""
    resp = await client.get("/api/v1/output-control/formats")
    assert resp.status_code == 200
    assert "csv" in resp.json()["formats"]
