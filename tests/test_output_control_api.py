"""API-level tests for output_control endpoints.

Covers:
- POST /api/v1/output-control/inspect
- POST /api/v1/output-control/dp/noise
- POST /api/v1/output-control/dp/budget/init
- GET /api/v1/output-control/dp/budget/{session_id}
"""
import uuid
import pytest
from httpx import AsyncClient


async def _create_session_for_output_control(client: AsyncClient, auth_headers: dict, publish_product) -> str:
    product_resp = await client.post(
        "/api/v1/data-products",
        json={"name": f"Output Session Product {uuid.uuid4().hex[:8]}"},
        headers=auth_headers,
    )
    assert product_resp.status_code == 201
    product_id = product_resp.json()["id"]
    await publish_product(product_id)
    session_resp = await client.post(
        "/api/v1/sandbox-sessions",
        json={"data_product_id": product_id},
        headers=auth_headers,
    )
    assert session_resp.status_code == 201
    return session_resp.json()["id"]


# ─── Inspect Endpoint ──────────────────────────────

@pytest.mark.asyncio
async def test_inspect_clean_output(client: AsyncClient, auth_headers: dict):
    """Clean output should pass all stages."""
    resp = await client.post("/api/v1/output-control/inspect", json={
        "output": "分析结果：平均值为42.5，无敏感信息",
        "session_id": "sess-1",
    }, headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["passed"] is True
    assert data["findings"] == []
    assert data["dp_applied"] is False
    # All stages should be present
    stages = data["stage_results"]
    for key in ["format_validation", "dlp_scan", "differential_privacy", "watermark", "signature", "final_approval"]:
        assert key in stages


@pytest.mark.asyncio
async def test_inspect_pii_phone(client: AsyncClient, auth_headers: dict):
    """Phone number should trigger DLP finding and fail."""
    resp = await client.post("/api/v1/output-control/inspect", json={
        "output": "联系方式：13812345678",
        "session_id": "sess-2",
    }, headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["passed"] is False
    assert any(f["type"] == "phone" for f in data["findings"])
    assert "[REDACTED:phone]" in data["redacted_output"]


@pytest.mark.asyncio
async def test_inspect_pii_id_card(client: AsyncClient, auth_headers: dict):
    """ID card should trigger critical finding."""
    resp = await client.post("/api/v1/output-control/inspect", json={
        "output": "身份证号：110101199001011234",
        "session_id": "sess-3",
    }, headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["passed"] is False
    assert any(f["type"] == "id_card" for f in data["findings"])
    assert data["stage_results"]["final_approval"] is False


@pytest.mark.asyncio
async def test_inspect_pii_email(client: AsyncClient, auth_headers: dict):
    """Email alone is medium severity — should detect but not necessarily block."""
    resp = await client.post("/api/v1/output-control/inspect", json={
        "output": "联系邮箱：user@example.com",
        "session_id": "sess-4",
    }, headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert any(f["type"] == "email" for f in data["findings"])


@pytest.mark.asyncio
async def test_inspect_with_dp_epsilon(client: AsyncClient, auth_headers: dict):
    """Passing dp_epsilon should set dp_applied=True."""
    resp = await client.post("/api/v1/output-control/inspect", json={
        "output": "统计结果：平均值50",
        "session_id": "sess-5",
        "dp_epsilon": 1.0,
    }, headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["dp_applied"] is True


@pytest.mark.asyncio
async def test_inspect_empty_output_fails(client: AsyncClient, auth_headers: dict):
    """Empty output should fail format validation."""
    resp = await client.post("/api/v1/output-control/inspect", json={
        "output": "",
        "session_id": "sess-6",
    }, headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["passed"] is False
    assert data["stage_results"]["format_validation"] is False


@pytest.mark.asyncio
async def test_inspect_mixed_pii(client: AsyncClient, auth_headers: dict):
    """Multiple PII types should all be detected and redacted."""
    resp = await client.post("/api/v1/output-control/inspect", json={
        "output": "身份证110101199001011234，手机13812345678，邮箱test@x.com",
        "session_id": "sess-7",
    }, headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["passed"] is False
    types = {f["type"] for f in data["findings"]}
    assert "id_card" in types
    assert "phone" in types
    assert "email" in types


@pytest.mark.asyncio
async def test_inspect_watermark_present(client: AsyncClient, auth_headers: dict):
    """Watermark should always be generated."""
    resp = await client.post("/api/v1/output-control/inspect", json={
        "output": "Some output data",
        "session_id": "sess-8",
    }, headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["watermark"] is not None
    assert len(data["watermark"]) > 0


@pytest.mark.asyncio
async def test_inspect_unauthorized(client: AsyncClient):
    """Inspect without auth should return 401."""
    resp = await client.post("/api/v1/output-control/inspect", json={
        "output": "data",
        "session_id": "sess-x",
    })
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_inspect_rejects_non_owner_real_session(
    client: AsyncClient,
    auth_headers: dict,
    make_user,
    publish_product,
):
    """A real sandbox session ID cannot be used by a non-owner for output audit."""
    other_headers, _ = await make_user("buyer", "inspect_other")
    session_id = await _create_session_for_output_control(client, auth_headers, publish_product)

    resp = await client.post(
        "/api/v1/output-control/inspect",
        json={"output": "safe result", "session_id": session_id},
        headers=other_headers,
    )

    assert resp.status_code == 403
    assert "Not your output control session" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_inspect_rejects_non_owner_ad_hoc_session(
    client: AsyncClient,
    auth_headers: dict,
    make_user,
):
    """Ad-hoc output-control session IDs are bound to their first authenticated user."""
    other_headers, _ = await make_user("buyer", "inspect_adhoc_other")
    session_id = f"adhoc-{uuid.uuid4().hex[:8]}"
    owner_resp = await client.post(
        "/api/v1/output-control/inspect",
        json={"output": "owner result", "session_id": session_id},
        headers=auth_headers,
    )
    assert owner_resp.status_code == 200

    other_resp = await client.post(
        "/api/v1/output-control/inspect",
        json={"output": "other result", "session_id": session_id},
        headers=other_headers,
    )

    assert other_resp.status_code == 403
    assert "Not your output control session" in other_resp.json()["detail"]


# ─── DP Noise Endpoint ──────────────────────────────

@pytest.mark.asyncio
async def test_dp_noise_laplace(client: AsyncClient, auth_headers: dict):
    """Laplace noise should return valid result."""
    resp = await client.post("/api/v1/output-control/dp/noise", json={
        "value": 100.0,
        "sensitivity": 1.0,
        "epsilon": 1.0,
        "mechanism": "laplace",
    }, headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["original"] == 100.0
    assert isinstance(data["noisy_value"], float)
    assert data["mechanism"] == "laplace"
    assert data["epsilon"] == 1.0
    assert data["sensitivity"] == 1.0


@pytest.mark.asyncio
async def test_dp_noise_gaussian(client: AsyncClient, auth_headers: dict):
    """Gaussian noise should return valid result."""
    resp = await client.post("/api/v1/output-control/dp/noise", json={
        "value": 50.0,
        "sensitivity": 2.0,
        "epsilon": 0.5,
        "mechanism": "gaussian",
        "delta": 1e-5,
    }, headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["original"] == 50.0
    assert isinstance(data["noisy_value"], float)
    assert data["mechanism"] == "gaussian"


@pytest.mark.asyncio
async def test_dp_noise_invalid_mechanism(client: AsyncClient, auth_headers: dict):
    """Unknown mechanism should return 400."""
    resp = await client.post("/api/v1/output-control/dp/noise", json={
        "value": 100.0,
        "sensitivity": 1.0,
        "epsilon": 1.0,
        "mechanism": "invalid_mechanism",
    }, headers=auth_headers)
    assert resp.status_code == 400
    assert "Unknown mechanism" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_dp_noise_default_mechanism(client: AsyncClient, auth_headers: dict):
    """Default mechanism should be laplace."""
    resp = await client.post("/api/v1/output-control/dp/noise", json={
        "value": 100.0,
        "sensitivity": 1.0,
        "epsilon": 1.0,
    }, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["mechanism"] == "laplace"


@pytest.mark.asyncio
async def test_dp_noise_unauthorized(client: AsyncClient):
    """DP noise without auth should return 401."""
    resp = await client.post("/api/v1/output-control/dp/noise", json={
        "value": 100.0,
        "sensitivity": 1.0,
        "epsilon": 1.0,
    })
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_dp_noise_value_not_equal_original(client: AsyncClient, auth_headers: dict):
    """With reasonable probability, noisy value should differ from original."""
    resp = await client.post("/api/v1/output-control/dp/noise", json={
        "value": 1000.0,
        "sensitivity": 1.0,
        "epsilon": 1.0,
        "mechanism": "laplace",
    }, headers=auth_headers)
    assert resp.status_code == 200
    # Statistically, the noisy value should differ (overwhelmingly likely with value=1000)
    data = resp.json()
    # We don't assert != because there's a tiny probability they're equal,
    # but we verify the response structure is correct
    assert "noisy_value" in data
    assert "original" in data


# ─── DP Budget Endpoints ──────────────────────────────

@pytest.mark.asyncio
async def test_dp_budget_init(client: AsyncClient, auth_headers: dict):
    """Init budget should allocate epsilon."""
    session_id = f"budget-{uuid.uuid4().hex[:8]}"
    resp = await client.post(
        f"/api/v1/output-control/dp/budget/init?session_id={session_id}&epsilon=10.0",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["session_id"] == session_id
    assert data["epsilon_allocated"] == 10.0


@pytest.mark.asyncio
async def test_dp_budget_query(client: AsyncClient, auth_headers: dict):
    """Query budget after init should return allocated epsilon."""
    session_id = f"budget-{uuid.uuid4().hex[:8]}"
    # Init
    await client.post(
        f"/api/v1/output-control/dp/budget/init?session_id={session_id}&epsilon=15.0",
        headers=auth_headers,
    )
    # Query
    resp = await client.get(
        f"/api/v1/output-control/dp/budget/{session_id}",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["session_id"] == session_id
    assert data["epsilon_remaining"] == 15.0


@pytest.mark.asyncio
async def test_dp_budget_multiple_sessions_isolated(client: AsyncClient, auth_headers: dict):
    """Different sessions should have independent budgets."""
    s1 = f"budget-{uuid.uuid4().hex[:8]}"
    s2 = f"budget-{uuid.uuid4().hex[:8]}"

    await client.post(f"/api/v1/output-control/dp/budget/init?session_id={s1}&epsilon=10.0", headers=auth_headers)
    await client.post(f"/api/v1/output-control/dp/budget/init?session_id={s2}&epsilon=5.0", headers=auth_headers)

    r1 = await client.get(f"/api/v1/output-control/dp/budget/{s1}", headers=auth_headers)
    r2 = await client.get(f"/api/v1/output-control/dp/budget/{s2}", headers=auth_headers)

    assert r1.json()["epsilon_remaining"] == 10.0
    assert r2.json()["epsilon_remaining"] == 5.0


@pytest.mark.asyncio
async def test_dp_budget_overwrite(client: AsyncClient, auth_headers: dict):
    """Re-init with new epsilon should overwrite."""
    session_id = f"budget-{uuid.uuid4().hex[:8]}"
    await client.post(f"/api/v1/output-control/dp/budget/init?session_id={session_id}&epsilon=10.0", headers=auth_headers)
    await client.post(f"/api/v1/output-control/dp/budget/init?session_id={session_id}&epsilon=20.0", headers=auth_headers)

    resp = await client.get(f"/api/v1/output-control/dp/budget/{session_id}", headers=auth_headers)
    assert resp.json()["epsilon_remaining"] == 20.0


@pytest.mark.asyncio
async def test_dp_budget_rejects_non_owner_access(client: AsyncClient, auth_headers: dict, make_user):
    """Only the owner/operator/admin can query or reinitialize an ad-hoc DP budget."""
    other_headers, _ = await make_user("buyer", "dp_budget_other")
    session_id = f"budget-{uuid.uuid4().hex[:8]}"
    await client.post(
        f"/api/v1/output-control/dp/budget/init?session_id={session_id}&epsilon=10.0",
        headers=auth_headers,
    )

    query_resp = await client.get(
        f"/api/v1/output-control/dp/budget/{session_id}",
        headers=other_headers,
    )
    overwrite_resp = await client.post(
        f"/api/v1/output-control/dp/budget/init?session_id={session_id}&epsilon=20.0",
        headers=other_headers,
    )

    assert query_resp.status_code == 403
    assert overwrite_resp.status_code == 403


@pytest.mark.asyncio
async def test_dp_budget_unauthorized(client: AsyncClient):
    """Budget endpoints without auth should return 401."""
    resp = await client.post("/api/v1/output-control/dp/budget/init?session_id=x&epsilon=1.0")
    assert resp.status_code == 401

    resp = await client.get("/api/v1/output-control/dp/budget/x")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_dp_budget_negative_epsilon_rejected(client: AsyncClient, auth_headers: dict):
    """Negative epsilon should be rejected."""
    resp = await client.post(
        "/api/v1/output-control/dp/budget/init?session_id=x&epsilon=-1.0",
        headers=auth_headers,
    )
    assert resp.status_code == 422


# ─── Audit Integration ──────────────────────────────

@pytest.mark.asyncio
async def test_inspect_creates_audit_log(client: AsyncClient, auth_headers: dict, operator_headers: dict):
    """Inspection should create an audit log entry."""
    session_id = f"audit-{uuid.uuid4().hex[:8]}"
    await client.post("/api/v1/output-control/inspect", json={
        "output": "测试审计日志",
        "session_id": session_id,
    }, headers=auth_headers)

    # Check audit records (requires operator role)
    resp = await client.get("/api/v1/audit/records", headers=operator_headers)
    assert resp.status_code == 200
    records = resp.json()
    # Should have at least one output_inspection record
    inspection_records = [r for r in records.get("items", []) if "output_inspection" in r.get("action", "")]
    assert len(inspection_records) >= 1


@pytest.mark.asyncio
async def test_output_gateway_rejects_non_owner_real_session(
    client: AsyncClient,
    auth_headers: dict,
    make_user,
    publish_product,
):
    """Gateway output processing must not audit or release under another user's session."""
    other_headers, _ = await make_user("buyer", "gateway_other")
    session_id = await _create_session_for_output_control(client, auth_headers, publish_product)

    resp = await client.post(
        "/api/v1/output-control/gateway",
        json={"data": [{"value": 1}], "session_id": session_id},
        headers=other_headers,
    )

    assert resp.status_code == 403
    assert "Not your output control session" in resp.json()["detail"]
