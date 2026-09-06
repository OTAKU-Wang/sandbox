"""Output gateway enforcement tests (gap E1).

Contract-derived OutputPolicy resolution, request-clamping on the gateway,
row-limit enforcement on text outputs, and the get_task_result verdict gate.
"""
import uuid

import pytest

from app.models.contract import Contract, ContractStatus
from app.models.data_product import DataProduct
from app.models.sandbox_session import SandboxSession, SessionStatus
from app.models.sandbox_task import SandboxTask, TaskStatus


async def _mk_contract(db_session, provider_id, buyer_id, *, max_output_rows=10, formats="csv,json", epsilon=None):
    contract = Contract(
        contract_no=f"C-{uuid.uuid4().hex[:8]}",
        contract_type="data_query",
        provider_id=uuid.UUID(provider_id),
        buyer_id=uuid.UUID(buyer_id),
        title="policy-test",
        product_ids=[str(uuid.uuid4())],
        status=ContractStatus.ACTIVE.value,
        max_output_rows=max_output_rows,
        allowed_output_formats=formats,
        dp_epsilon_budget=epsilon,
    )
    db_session.add(contract)
    await db_session.flush()
    await db_session.refresh(contract)
    return contract


@pytest.mark.asyncio
async def test_build_output_policy_from_contract(db_session, make_user):
    from app.services.output_policy import build_output_policy

    _, provider = await make_user("data_provider", "prov")
    _, buyer = await make_user("buyer", "buy")
    contract = await _mk_contract(db_session, provider, buyer, max_output_rows=7, formats="csv,json", epsilon=1.5)

    policy = await build_output_policy(db_session, contract.id)
    assert policy.max_output_rows == 7
    assert policy.allowed_output_formats == ["csv", "json"]
    assert policy.dp_epsilon_budget == 1.5


@pytest.mark.asyncio
async def test_build_output_policy_defaults_without_contract(db_session):
    from app.services.output_policy import build_output_policy

    policy = await build_output_policy(db_session, None)
    assert policy.max_output_rows == 10000
    assert policy.allowed_output_formats == ["csv", "json"]
    # unresolvable contract id also yields conservative defaults
    policy2 = await build_output_policy(db_session, uuid.uuid4())
    assert policy2.max_output_rows == 10000


def test_clamp_gateway_request_never_widens():
    from app.services.output_policy import clamp_gateway_request
    from app.services.output_gateway import OutputPolicy

    policy = OutputPolicy(max_output_rows=10, allowed_output_formats=["csv"])
    rows, fmts = clamp_gateway_request(100, ["csv", "json", "parquet"], policy)
    assert rows == 10
    assert fmts == ["csv"]

    # narrower request stays narrower
    rows, fmts = clamp_gateway_request(3, ["csv"], policy)
    assert rows == 3

    # nothing requested → contract allowance
    rows, fmts = clamp_gateway_request(None, None, policy)
    assert rows == 10
    assert fmts == ["csv"]


@pytest.mark.asyncio
async def test_enforce_text_output_policy_truncates(db_session, make_user):
    from app.services.output_policy import enforce_text_output_policy

    _, provider = await make_user("data_provider", "prov")
    _, buyer = await make_user("buyer", "buy")
    product = DataProduct(provider_id=uuid.UUID(provider), name="p", status="published")
    db_session.add(product)
    await db_session.flush()
    await db_session.refresh(product)
    contract = await _mk_contract(db_session, provider, buyer, max_output_rows=2)
    session = SandboxSession(
        user_id=uuid.UUID(buyer),
        data_product_id=product.id,
        status=SessionStatus.RUNNING.value,
        contract_id=str(contract.id),
        timeout_seconds=3600,
    )
    db_session.add(session)
    await db_session.flush()
    await db_session.refresh(session)

    report = {"passed": True}
    redacted, report2 = await enforce_text_output_policy(
        db_session, session.id, "a\nb\nc\nd\n", report
    )
    assert redacted == "a\nb"
    assert report2["policy_row_limit_truncated"] is True
    assert report2["policy"]["source"] == "contract"
    assert report2["policy"]["max_output_rows"] == 2


@pytest.mark.asyncio
async def test_get_task_result_gated_on_inspection_verdict(client, db_session, make_user):
    headers, user_id = await make_user("data_provider", "prov")
    product = DataProduct(provider_id=uuid.UUID(user_id), name="p", status="published")
    db_session.add(product)
    await db_session.flush()
    await db_session.refresh(product)
    session = SandboxSession(
        user_id=uuid.UUID(user_id),
        data_product_id=product.id,
        status=SessionStatus.COMPLETED.value,
        timeout_seconds=3600,
    )
    db_session.add(session)
    await db_session.flush()
    await db_session.refresh(session)

    # COMPLETED but no inspection verdict → withheld (409)
    no_verdict = SandboxTask(
        task_id=f"t-{uuid.uuid4().hex[:8]}",
        session_id=session.id,
        user_id=uuid.UUID(user_id),
        task_type="query",
        status=TaskStatus.COMPLETED.value,
        resource_usage={},
    )
    db_session.add(no_verdict)
    await db_session.flush()

    resp = await client.get(
        f"/api/v1/sandbox-tasks/{no_verdict.task_id}/result",
        params={"session_id": str(session.id)},
        headers=headers,
    )
    assert resp.status_code == 409

    # COMPLETED with a passing verdict → payload released with inspection info
    passed = SandboxTask(
        task_id=f"t-{uuid.uuid4().hex[:8]}",
        session_id=session.id,
        user_id=uuid.UUID(user_id),
        task_type="query",
        status=TaskStatus.COMPLETED.value,
        resource_usage={
            "output_security": {
                "inspection_report": {"passed": True, "findings_count": 0, "watermark": "wm", "signature": "sig"},
                "redacted_output": "safe rows",
            },
        },
    )
    db_session.add(passed)
    await db_session.flush()

    resp = await client.get(
        f"/api/v1/sandbox-tasks/{passed.task_id}/result",
        params={"session_id": str(session.id)},
        headers=headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["output_inspection"]["passed"] is True
    assert body["output_inspection"]["watermark"] == "wm"
    assert body["output_inspection"]["redacted_output"] == "safe rows"
