import uuid
import pytest
from httpx import AsyncClient
from datetime import datetime, timezone, timedelta
from sqlalchemy import select

from app.models.contract import Contract, ContractStatus, ContractType
from app.models.data_product import DataProduct


class DummySandboxRuntime:
    def __init__(self, output: str = ""):
        self.output = output

    def provision(self, **kwargs):
        return {"container_id": "dummy-container", "status": "running"}

    async def execute(self, container_id: str, code: str, language: str, **kwargs):
        self.last_execute = {
            "container_id": container_id,
            "code": code,
            "language": language,
            **kwargs,
        }
        return {"output": self.output, "exit_code": 0, "duration_ms": 10}

    def terminate(self, container_id: str):
        return True


async def _create_running_session(client: AsyncClient, auth_headers: dict, monkeypatch, publish_product, output: str) -> str:
    import app.api.sandbox_sessions as sandbox_sessions_api

    runtime = DummySandboxRuntime(output=output)

    async def fake_get_sandbox_manager():
        return runtime

    monkeypatch.setattr(sandbox_sessions_api, "get_sandbox_manager", fake_get_sandbox_manager)

    product_resp = await client.post("/api/v1/data-products", json={"name": f"Exec-{uuid.uuid4().hex[:6]}"}, headers=auth_headers)
    product_id = product_resp.json()["id"]
    await publish_product(product_id)
    session_resp = await client.post("/api/v1/sandbox-sessions", json={"data_product_id": product_id}, headers=auth_headers)
    assert session_resp.status_code == 201
    assert session_resp.json()["status"] == "running"
    return session_resp.json()["id"]


async def _create_contract_bound_product(client: AsyncClient, auth_headers: dict, db_session, make_user, publish_product):
    buyer_headers, buyer_id = await make_user("buyer", "session_buyer")
    product_resp = await client.post(
        "/api/v1/data-products",
        json={"name": f"Contract Session {uuid.uuid4().hex[:6]}", "product_type": "structured"},
        headers=auth_headers,
    )
    product_id = product_resp.json()["id"]
    await publish_product(product_id)
    product = await db_session.get(DataProduct, uuid.UUID(product_id))
    contract = Contract(
        contract_no=f"SS-{uuid.uuid4().hex[:12]}",
        contract_type=ContractType.DATA_QUERY.value,
        status=ContractStatus.ACTIVE.value,
        provider_id=product.provider_id,
        buyer_id=uuid.UUID(buyer_id),
        title="Sandbox Session Contract",
        terms={},
        product_ids=[product_id],
        allowed_sandbox_levels="L3",
        allowed_sandbox_modes=["query"],
        allowed_operations="read,analyze",
        max_duration_hours=1,
        max_output_rows=200,
    )
    db_session.add(contract)
    await db_session.flush()
    return product_id, contract, buyer_headers


@pytest.mark.asyncio
async def test_create_sandbox_session(client: AsyncClient, auth_headers: dict, publish_product):
    # Create a data product first
    product_resp = await client.post("/api/v1/data-products", json={
        "name": "Session Test Product",
        "product_type": "structured",
    }, headers=auth_headers)
    product_id = product_resp.json()["id"]

    await publish_product(product_id)

    # Create sandbox session
    resp = await client.post("/api/v1/sandbox-sessions", json={
        "data_product_id": product_id,
        "sandbox_level": "L3",
    }, headers=auth_headers)
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] in ("running", "failed")
    assert data["sandbox_level"] == "L3"


@pytest.mark.asyncio
async def test_failed_provision_does_not_create_session_key_or_network_policy(
    client: AsyncClient, auth_headers: dict, publish_product, monkeypatch, db_session
):
    import app.api.sandbox_sessions as sandbox_sessions_api
    from app.models.kms import KeyMetadata
    from app.models.network_policy import NetworkPolicy

    class FailingRuntime(DummySandboxRuntime):
        def provision(self, **kwargs):
            return {"container_id": None, "status": "failed", "error": "provision failed"}

    async def fake_get_sandbox_manager():
        return FailingRuntime()

    monkeypatch.setattr(sandbox_sessions_api, "get_sandbox_manager", fake_get_sandbox_manager)

    product_resp = await client.post(
        "/api/v1/data-products",
        json={"name": f"Failed Provision {uuid.uuid4().hex[:6]}", "product_type": "structured"},
        headers=auth_headers,
    )
    product_id = product_resp.json()["id"]
    await publish_product(product_id)

    resp = await client.post(
        "/api/v1/sandbox-sessions",
        json={"data_product_id": product_id, "sandbox_level": "L3"},
        headers=auth_headers,
    )

    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "failed"
    assert data["container_id"] is None
    assert data["session_key_id"] is None

    key_result = await db_session.execute(
        select(KeyMetadata).where(KeyMetadata.session_id == uuid.UUID(data["id"]))
    )
    assert key_result.scalar_one_or_none() is None

    policy_result = await db_session.execute(
        select(NetworkPolicy).where(NetworkPolicy.session_id == data["id"])
    )
    assert policy_result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_create_sandbox_session_validates_bound_contract(client: AsyncClient, auth_headers: dict, db_session, make_user, publish_product):
    product_id, contract, buyer_headers = await _create_contract_bound_product(
        client, auth_headers, db_session, make_user, publish_product
    )

    resp = await client.post(
        "/api/v1/sandbox-sessions",
        json={
            "data_product_id": product_id,
            "contract_id": str(contract.id),
            "sandbox_level": "L3",
            "timeout_seconds": 3600,
        },
        headers=buyer_headers,
    )

    assert resp.status_code == 201
    data = resp.json()
    assert data["contract_id"] == str(contract.id)
    assert data["user_id"] == str(contract.buyer_id)


@pytest.mark.asyncio
async def test_create_sandbox_session_persists_requested_sandbox_mode(
    client: AsyncClient, auth_headers: dict, db_session, make_user, publish_product, monkeypatch
):
    import app.api.sandbox_sessions as sandbox_sessions_api

    async def fake_get_sandbox_manager():
        return DummySandboxRuntime(output="ok")

    monkeypatch.setattr(sandbox_sessions_api, "get_sandbox_manager", fake_get_sandbox_manager)
    product_id, contract, buyer_headers = await _create_contract_bound_product(
        client, auth_headers, db_session, make_user, publish_product
    )
    contract.allowed_sandbox_modes = ["product_dev"]
    contract.allowed_operations = "read,transform"
    await db_session.flush()

    resp = await client.post(
        "/api/v1/sandbox-sessions",
        json={
            "data_product_id": product_id,
            "contract_id": str(contract.id),
            "sandbox_level": "L3",
            "sandbox_mode": "product_dev",
            "timeout_seconds": 3600,
        },
        headers=buyer_headers,
    )

    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "running"
    assert data["sandbox_mode"] == "product_dev"


@pytest.mark.asyncio
async def test_create_sandbox_session_rejects_disallowed_contract_mode(
    client: AsyncClient, auth_headers: dict, db_session, make_user, publish_product, monkeypatch
):
    import app.api.sandbox_sessions as sandbox_sessions_api

    async def fake_get_sandbox_manager():
        return DummySandboxRuntime(output="ok")

    monkeypatch.setattr(sandbox_sessions_api, "get_sandbox_manager", fake_get_sandbox_manager)
    product_id, contract, buyer_headers = await _create_contract_bound_product(
        client, auth_headers, db_session, make_user, publish_product
    )

    resp = await client.post(
        "/api/v1/sandbox-sessions",
        json={
            "data_product_id": product_id,
            "contract_id": str(contract.id),
            "sandbox_level": "L3",
            "sandbox_mode": "product_dev",
        },
        headers=buyer_headers,
    )

    assert resp.status_code == 400
    assert "Sandbox mode 'product_dev' not allowed" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_create_sandbox_session_rejects_missing_contract(client: AsyncClient, auth_headers: dict, publish_product):
    product_resp = await client.post(
        "/api/v1/data-products",
        json={"name": "Missing Contract Session", "product_type": "structured"},
        headers=auth_headers,
    )
    product_id = product_resp.json()["id"]
    await publish_product(product_id)

    resp = await client.post(
        "/api/v1/sandbox-sessions",
        json={"data_product_id": product_id, "contract_id": str(uuid.uuid4())},
        headers=auth_headers,
    )

    assert resp.status_code == 404
    assert resp.json()["detail"] == "Contract not found"


@pytest.mark.asyncio
async def test_create_sandbox_session_rejects_non_buyer_contract_user(client: AsyncClient, auth_headers: dict, db_session, make_user, publish_product):
    product_id, contract, _ = await _create_contract_bound_product(
        client, auth_headers, db_session, make_user, publish_product
    )

    resp = await client.post(
        "/api/v1/sandbox-sessions",
        json={"data_product_id": product_id, "contract_id": str(contract.id)},
        headers=auth_headers,
    )

    assert resp.status_code == 403
    assert "Only the contract buyer" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_create_sandbox_session_rejects_product_outside_contract(client: AsyncClient, auth_headers: dict, db_session, make_user, publish_product):
    _, contract, buyer_headers = await _create_contract_bound_product(
        client, auth_headers, db_session, make_user, publish_product
    )
    other_resp = await client.post(
        "/api/v1/data-products",
        json={"name": "Outside Contract", "product_type": "structured"},
        headers=auth_headers,
    )
    other_id = other_resp.json()["id"]
    await publish_product(other_id)

    resp = await client.post(
        "/api/v1/sandbox-sessions",
        json={"data_product_id": other_id, "contract_id": str(contract.id)},
        headers=buyer_headers,
    )

    assert resp.status_code == 400
    assert resp.json()["detail"] == "Data product not covered by this contract"


@pytest.mark.asyncio
async def test_create_sandbox_session_rejects_disallowed_contract_level(client: AsyncClient, auth_headers: dict, db_session, make_user, publish_product):
    product_id, contract, buyer_headers = await _create_contract_bound_product(
        client, auth_headers, db_session, make_user, publish_product
    )
    contract.allowed_sandbox_levels = "L2"
    await db_session.flush()

    resp = await client.post(
        "/api/v1/sandbox-sessions",
        json={"data_product_id": product_id, "contract_id": str(contract.id), "sandbox_level": "L3"},
        headers=buyer_headers,
    )

    assert resp.status_code == 400
    assert "not allowed by contract" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_list_sandbox_sessions(client: AsyncClient, auth_headers: dict):
    resp = await client.get("/api/v1/sandbox-sessions", headers=auth_headers)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_create_session_unpublished_product(client: AsyncClient, auth_headers: dict):
    product_resp = await client.post("/api/v1/data-products", json={"name": "Draft"}, headers=auth_headers)
    product_id = product_resp.json()["id"]
    resp = await client.post("/api/v1/sandbox-sessions", json={
        "data_product_id": product_id,
    }, headers=auth_headers)
    assert resp.status_code == 400  # not published


@pytest.mark.asyncio
async def test_terminate_sandbox_session(client: AsyncClient, auth_headers: dict, publish_product):
    product_resp = await client.post("/api/v1/data-products", json={"name": "Term"}, headers=auth_headers)
    product_id = product_resp.json()["id"]
    await publish_product(product_id)

    session_resp = await client.post("/api/v1/sandbox-sessions", json={
        "data_product_id": product_id,
    }, headers=auth_headers)
    session_id = session_resp.json()["id"]

    resp = await client.post(f"/api/v1/sandbox-sessions/{session_id}/terminate", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "terminated"


@pytest.mark.asyncio
async def test_regulator_can_read_but_not_terminate_sessions(client: AsyncClient, auth_headers: dict, make_user, publish_product):
    regulator_headers, _ = await make_user("regulator", "regulator")

    product_resp = await client.post("/api/v1/data-products", json={"name": "Regulator Session"}, headers=auth_headers)
    product_id = product_resp.json()["id"]
    await publish_product(product_id)

    session_resp = await client.post("/api/v1/sandbox-sessions", json={
        "data_product_id": product_id,
    }, headers=auth_headers)
    assert session_resp.status_code == 201
    session_id = session_resp.json()["id"]

    list_resp = await client.get("/api/v1/sandbox-sessions", headers=regulator_headers)
    assert list_resp.status_code == 200
    assert any(session["id"] == session_id for session in list_resp.json()["items"])

    detail_resp = await client.get(f"/api/v1/sandbox-sessions/{session_id}", headers=regulator_headers)
    assert detail_resp.status_code == 200

    terminate_resp = await client.post(f"/api/v1/sandbox-sessions/{session_id}/terminate", headers=regulator_headers)
    assert terminate_resp.status_code == 403


@pytest.mark.asyncio
async def test_execute_sandbox_session_redacts_noncritical_output(client: AsyncClient, auth_headers: dict, monkeypatch, publish_product):
    session_id = await _create_running_session(
        client,
        auth_headers,
        monkeypatch,
        publish_product,
        output="phone=13812345678",
    )

    resp = await client.post(
        f"/api/v1/sandbox-sessions/{session_id}/execute",
        json={"code": "print('ok')", "language": "python"},
        headers=auth_headers,
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["output_blocked"] is False
    assert "13812345678" not in data["output"]
    assert "[REDACTED:phone]" in data["output"]
    assert data["security_report"]["blocked"] is False
    assert data["security_report"]["findings_count"] >= 1


@pytest.mark.asyncio
async def test_execute_sandbox_session_blocks_critical_output(client: AsyncClient, auth_headers: dict, monkeypatch, publish_product):
    session_id = await _create_running_session(
        client,
        auth_headers,
        monkeypatch,
        publish_product,
        output="id_card=110101199001011234",
    )

    resp = await client.post(
        f"/api/v1/sandbox-sessions/{session_id}/execute",
        json={"code": "print('ok')", "language": "python"},
        headers=auth_headers,
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["output_blocked"] is True
    assert data["output"] == ""
    assert data["security_report"]["blocked"] is True


@pytest.mark.asyncio
async def test_execute_runtime_with_context_passes_supported_kwargs():
    from app.api.sandbox_sessions import _execute_runtime_with_context

    runtime = DummySandboxRuntime(output="ok")

    result = await _execute_runtime_with_context(
        runtime,
        "dummy-container",
        "print('ok')",
        "python",
        session_key="feedface",
        env_vars={"CDS_SANDBOX_MODE": "structured_app"},
        timeout=30,
    )

    assert result["exit_code"] == 0
    assert runtime.last_execute["session_key"] == "feedface"
    assert runtime.last_execute["env_vars"]["CDS_SANDBOX_MODE"] == "structured_app"
    assert runtime.last_execute["timeout"] == 30


@pytest.mark.asyncio
async def test_sandbox_task_create_rejects_empty_code(client: AsyncClient, auth_headers: dict, monkeypatch, publish_product):
    session_id = await _create_running_session(client, auth_headers, monkeypatch, publish_product, output="ok")

    resp = await client.post(
        "/api/v1/sandbox-tasks",
        params={"session_id": session_id, "task_type": "query", "code": "   ", "language": "python"},
        headers=auth_headers,
    )

    assert resp.status_code == 422
    assert resp.json()["detail"] == "code cannot be empty"


@pytest.mark.asyncio
async def test_session_network_policy_can_be_viewed_and_updated_with_json_body(client: AsyncClient, auth_headers: dict, monkeypatch, publish_product):
    import app.api.sandbox_sessions as sandbox_sessions_api

    engine_calls: list[tuple[str, str]] = []

    async def fake_create_policy(session_id: str, config):
        engine_calls.append(("create", config.mode))
        return {"session_id": session_id, "mode": config.mode}

    async def fake_remove_policy(session_id: str):
        engine_calls.append(("remove", session_id))

    monkeypatch.setattr(sandbox_sessions_api.network_policy_engine, "create_policy", fake_create_policy)
    monkeypatch.setattr(sandbox_sessions_api.network_policy_engine, "remove_policy", fake_remove_policy)

    session_id = await _create_running_session(client, auth_headers, monkeypatch, publish_product, output="ok")

    get_resp = await client.get(f"/api/v1/sandbox-sessions/{session_id}/network-policy", headers=auth_headers)
    assert get_resp.status_code == 200
    assert get_resp.json()["mode"] == "deny_all"

    update_resp = await client.put(
        f"/api/v1/sandbox-sessions/{session_id}/network-policy",
        json={
            "mode": "allowlist",
            "allowed_ips": ["10.0.0.0/8"],
            "allowed_domains": ["api.example.com", "*.internal.example.com"],
            "allowed_ports": [443, 8443],
            "dns_proxy_enabled": True,
            "max_connections_per_second": 5,
            "active": True,
        },
        headers=auth_headers,
    )

    assert update_resp.status_code == 200
    policy = update_resp.json()["network_policy"]
    assert policy["mode"] == "allowlist"
    assert policy["allowed_ips"] == ["10.0.0.0/8"]
    assert policy["allowed_domains"] == ["api.example.com", "*.internal.example.com"]
    assert policy["allowed_ports"] == [443, 8443]
    assert ("remove", session_id) in engine_calls
    assert ("create", "allowlist") in engine_calls


@pytest.mark.asyncio
async def test_session_network_policy_rejects_invalid_cidr(client: AsyncClient, auth_headers: dict, monkeypatch, publish_product):
    session_id = await _create_running_session(client, auth_headers, monkeypatch, publish_product, output="ok")

    resp = await client.put(
        f"/api/v1/sandbox-sessions/{session_id}/network-policy",
        json={"mode": "allowlist", "allowed_ips": ["not-a-cidr"]},
        headers=auth_headers,
    )

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_network_policy_session_route_is_reachable(client: AsyncClient, auth_headers: dict, monkeypatch, publish_product):
    session_id = await _create_running_session(client, auth_headers, monkeypatch, publish_product, output="ok")

    resp = await client.get(f"/api/v1/network-policies/session/{session_id}", headers=auth_headers)

    assert resp.status_code == 200
    assert resp.json()["session_id"] == session_id


@pytest.mark.asyncio
async def test_cleanup_expired_sessions_removes_network_policy_enforcement(
    client: AsyncClient, auth_headers: dict, admin_headers: dict, monkeypatch, publish_product, db_session
):
    import app.api.sandbox_sessions as sandbox_sessions_api
    from app.models.network_policy import NetworkPolicy
    from app.models.sandbox_session import SandboxSession

    runtime = DummySandboxRuntime(output="ok")
    engine_calls: list[tuple[str, str]] = []

    async def fake_get_sandbox_manager():
        return runtime

    async def fake_create_policy(session_id: str, config):
        engine_calls.append(("create", session_id))
        return {"session_id": session_id}

    async def fake_remove_policy(session_id: str):
        engine_calls.append(("remove", session_id))

    monkeypatch.setattr(sandbox_sessions_api, "get_sandbox_manager", fake_get_sandbox_manager)
    monkeypatch.setattr(sandbox_sessions_api.network_policy_engine, "create_policy", fake_create_policy)
    monkeypatch.setattr(sandbox_sessions_api.network_policy_engine, "remove_policy", fake_remove_policy)

    product_resp = await client.post(
        "/api/v1/data-products",
        json={"name": f"Expired Session {uuid.uuid4().hex[:6]}", "product_type": "structured"},
        headers=auth_headers,
    )
    product_id = product_resp.json()["id"]
    await publish_product(product_id)

    session_resp = await client.post(
        "/api/v1/sandbox-sessions",
        json={"data_product_id": product_id, "sandbox_level": "L3", "timeout_seconds": 60},
        headers=auth_headers,
    )
    assert session_resp.status_code == 201
    session_id = session_resp.json()["id"]

    session = await db_session.get(SandboxSession, uuid.UUID(session_id))
    session.created_at = datetime.now(timezone.utc) - timedelta(hours=2)
    await db_session.flush()

    resp = await client.post("/api/v1/sandbox-sessions/cleanup-expired", headers=admin_headers)

    assert resp.status_code == 200
    assert resp.json()["cleaned"] == 1
    assert ("remove", session_id) in engine_calls

    policy_result = await db_session.execute(
        select(NetworkPolicy).where(NetworkPolicy.session_id == session_id)
    )
    policy = policy_result.scalar_one()
    assert policy.active is False
