import uuid

import pytest
from sqlalchemy import select

from app.models.connector import Connector, ConnectorSession, ConnectorStatus, generate_api_key
from app.models.contract import Contract, ContractStatus, ContractType
from app.models.data_product import DataProduct, DataProductStatus
from app.models.kms import KeyMetadata
from app.models.network_policy import NetworkPolicy
from app.models.sandbox_session import SandboxSession


class DummyConnectorRuntime:
    def __init__(self, output: str = "ok"):
        self.output = output
        self.provision_result = {"container_id": "connector-dummy-container", "status": "running"}
        self.distributed_keys = []
        self.terminated = []

    def provision(self, **kwargs):
        return self.provision_result

    async def execute(self, container_id: str, code: str, language: str):
        return {"output": self.output, "exit_code": 0, "duration_ms": 5}

    def terminate(self, container_id: str):
        self.terminated.append(container_id)
        return True


class FailedConnectorRuntime(DummyConnectorRuntime):
    def __init__(self, error: str = "sandbox provision failed"):
        super().__init__()
        self.provision_result = {"container_id": None, "status": "failed", "error": error}


class FailingKeyKMS:
    def __init__(self):
        self.destroyed = []

    def generate_session_key(self, session_id: str):
        return {"key_id": f"session-{session_id}-fail", "key_bytes": b"0" * 32}

    def distribute_key(self, key_id: str, session_id: str, **kwargs):
        return None

    def destroy_key(self, key_id: str):
        self.destroyed.append(key_id)
        return True


async def _make_connector_session(
    client,
    db_session,
    make_user,
    monkeypatch,
    output: str,
    contract_terms: dict | None = None,
    allowed_operations: str = "query",
    runtime: DummyConnectorRuntime | None = None,
):
    import app.api.connectors as connectors_api

    _, admin_id = await make_user("admin", "connector_admin")
    _, provider_id = await make_user("data_provider", "connector_provider")
    _, buyer_id = await make_user("buyer", "connector_buyer")

    api_key, key_hash = generate_api_key()
    connector = Connector(
        space_id=f"space-{uuid.uuid4().hex[:8]}",
        space_name="Remote Space",
        space_url="https://remote.example.test",
        api_key_hash=key_hash,
        api_key_prefix=api_key[:8],
        status=ConnectorStatus.ACTIVE.value,
        registered_by=uuid.UUID(admin_id),
    )
    db_session.add(connector)
    await db_session.flush()

    product = DataProduct(
        provider_id=uuid.UUID(provider_id),
        name="Connector Product",
        product_type="structured",
        status=DataProductStatus.PUBLISHED.value,
    )
    db_session.add(product)
    await db_session.flush()

    terms = contract_terms
    if terms is None:
        terms = {"connector_id": str(connector.id), "space_id": connector.space_id}
    contract = Contract(
        contract_no=f"CONN-{uuid.uuid4().hex[:12]}",
        contract_type=ContractType.DATA_QUERY.value,
        status=ContractStatus.ACTIVE.value,
        provider_id=uuid.UUID(provider_id),
        buyer_id=uuid.UUID(buyer_id),
        title="Connector Contract",
        terms=terms,
        product_ids=[str(product.id)],
        allowed_operations=allowed_operations,
        allowed_sandbox_modes=["structured_query"],
    )
    db_session.add(contract)
    await db_session.flush()

    runtime = runtime or DummyConnectorRuntime(output=output)

    async def fake_get_sandbox_manager():
        return runtime

    monkeypatch.setattr(connectors_api, "get_sandbox_manager", fake_get_sandbox_manager)

    create_resp = await client.post(
        "/api/v1/connectors/remote/sessions",
        params={
            "product_id": str(product.id),
            "contract_id": str(contract.id),
            "remote_user_id": "remote-user-1",
            "sandbox_level": "L3",
        },
        headers={"Authorization": f"Bearer {api_key}"},
    )
    return create_resp, api_key, connector, product, contract


@pytest.mark.asyncio
async def test_connector_session_gets_session_key_and_default_network_policy(client, db_session, make_user, monkeypatch):
    create_resp, *_ = await _make_connector_session(
        client,
        db_session,
        make_user,
        monkeypatch,
        output="ok",
    )
    assert create_resp.status_code == 201

    session_id = uuid.UUID(create_resp.json()["sandbox_session_id"])
    session = await db_session.get(SandboxSession, session_id)
    assert session.session_key_id

    key_result = await db_session.execute(select(KeyMetadata).where(KeyMetadata.key_id == session.session_key_id))
    assert key_result.scalar_one_or_none() is not None

    policy_result = await db_session.execute(select(NetworkPolicy).where(NetworkPolicy.session_id == str(session_id)))
    policy = policy_result.scalar_one_or_none()
    assert policy is not None
    assert policy.mode == "deny_all"
    assert policy.active is True


@pytest.mark.asyncio
async def test_connector_session_fails_closed_when_runtime_provision_fails(client, db_session, make_user, monkeypatch):
    create_resp, *_ = await _make_connector_session(
        client,
        db_session,
        make_user,
        monkeypatch,
        output="ok",
        runtime=FailedConnectorRuntime("k8s pod did not become ready"),
    )

    assert create_resp.status_code == 503
    assert create_resp.json()["detail"] == "k8s pod did not become ready"

    sessions = (await db_session.execute(select(SandboxSession))).scalars().all()
    assert len(sessions) == 1
    assert sessions[0].status == "failed"
    assert sessions[0].session_key_id is None

    key_result = await db_session.execute(select(KeyMetadata))
    assert key_result.scalar_one_or_none() is None

    policy_result = await db_session.execute(select(NetworkPolicy))
    assert policy_result.scalar_one_or_none() is None

    connector_session_result = await db_session.execute(select(ConnectorSession))
    assert connector_session_result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_connector_session_fails_closed_when_session_key_distribution_fails(client, db_session, make_user, monkeypatch):
    import app.api.connectors as connectors_api

    runtime = DummyConnectorRuntime(output="ok")
    kms = FailingKeyKMS()
    monkeypatch.setattr(connectors_api, "kms_service", kms)

    create_resp, *_ = await _make_connector_session(
        client,
        db_session,
        make_user,
        monkeypatch,
        output="ok",
        runtime=runtime,
    )

    assert create_resp.status_code == 503
    assert create_resp.json()["detail"] == "Session key distribution failed"
    assert runtime.terminated == ["connector-dummy-container"]
    assert len(kms.destroyed) == 1

    sessions = (await db_session.execute(select(SandboxSession))).scalars().all()
    assert len(sessions) == 1
    assert sessions[0].status == "failed"
    assert sessions[0].session_key_id is None
    assert sessions[0].container_id == "connector-dummy-container"
    assert (await db_session.execute(select(KeyMetadata))).scalar_one_or_none() is None
    assert (await db_session.execute(select(NetworkPolicy))).scalar_one_or_none() is None
    assert (await db_session.execute(select(ConnectorSession))).scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_connector_execute_applies_output_inspection(client, db_session, make_user, monkeypatch):
    create_resp, api_key, *_ = await _make_connector_session(
        client,
        db_session,
        make_user,
        monkeypatch,
        output="phone=13812345678",
    )
    assert create_resp.status_code == 201
    connector_session_id = create_resp.json()["connector_session_id"]

    execute_resp = await client.post(
        f"/api/v1/connectors/remote/sessions/{connector_session_id}/execute",
        params={"code": "print('ok')", "language": "python"},
        headers={"Authorization": f"Bearer {api_key}"},
    )

    assert execute_resp.status_code == 200
    data = execute_resp.json()
    assert data["output_blocked"] is False
    assert "13812345678" not in data["output"]
    assert "[REDACTED:phone]" in data["output"]
    assert data["security_report"]["findings_count"] >= 1


@pytest.mark.asyncio
async def test_connector_execute_blocks_critical_output(client, db_session, make_user, monkeypatch):
    create_resp, api_key, *_ = await _make_connector_session(
        client,
        db_session,
        make_user,
        monkeypatch,
        output="id_card=110101199001011234",
    )
    assert create_resp.status_code == 201

    execute_resp = await client.post(
        f"/api/v1/connectors/remote/sessions/{create_resp.json()['connector_session_id']}/execute",
        params={"code": "print('ok')", "language": "python"},
        headers={"Authorization": f"Bearer {api_key}"},
    )

    assert execute_resp.status_code == 200
    data = execute_resp.json()
    assert data["output_blocked"] is True
    assert data["output"] == ""
    assert data["security_report"]["blocked"] is True


@pytest.mark.asyncio
async def test_connector_execute_rejects_failed_code_scan(client, db_session, make_user, monkeypatch):
    create_resp, api_key, *_ = await _make_connector_session(
        client,
        db_session,
        make_user,
        monkeypatch,
        output="ok",
    )
    assert create_resp.status_code == 201

    execute_resp = await client.post(
        f"/api/v1/connectors/remote/sessions/{create_resp.json()['connector_session_id']}/execute",
        params={"code": "open('/tmp/leak', 'w')", "language": "python"},
        headers={"Authorization": f"Bearer {api_key}"},
    )

    assert execute_resp.status_code == 422
    assert execute_resp.json()["detail"]["error"] == "Code scan failed"


@pytest.mark.asyncio
async def test_connector_session_requires_matching_contract_binding(client, db_session, make_user, monkeypatch):
    create_resp, *_ = await _make_connector_session(
        client,
        db_session,
        make_user,
        monkeypatch,
        output="ok",
        contract_terms={"connector_id": str(uuid.uuid4()), "space_id": "other-space"},
    )

    assert create_resp.status_code == 403
    assert create_resp.json()["detail"] == "Contract is not bound to this connector"
