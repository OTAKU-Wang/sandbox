"""A256 — `approved_fields: []` must never mean "approve all requested fields".

`app/api/field_exposure.py` used truthiness to decide the fallback
(`approved = body.approved_fields if body.approved_fields else request.requested_fields`),
so an explicit empty list — "the approver selected nothing" — was
indistinguishable from the field being absent, and both silently granted the
FULL set of requested fields (PII included). That is a privilege escalation at
the API boundary for any client that sends `[]`.

Invariants pinned here:
* `approved_fields: []` -> 400, request stays pending, nothing approved;
* `approved_fields` absent -> approve all requested (backward compatibility
  with the shipped UI, which always sends the full set).
"""
import uuid

import pytest

from app.models.data_product import DataProduct, DataProductStatus
from app.models.field_exposure import FieldExposureRequest, ExposureRequestStatus
from app.models.user import User
from app.services.auth_service import create_access_token

FIELDS = ["city", "identity_no", "income"]


async def _headers(db_session, user_id: str) -> dict:
    user = await db_session.get(User, uuid.UUID(user_id))
    return {"Authorization": f"Bearer {create_access_token(user.id, user.role)}"}


async def _pending_request(client, db_session, make_user) -> tuple[str, str]:
    _, provider_id = await make_user("data_provider", "a256_provider")
    _, buyer_id = await make_user("buyer", "a256_buyer")
    product = DataProduct(
        provider_id=uuid.UUID(provider_id),
        name="A256 Product",
        product_type="structured",
        status=DataProductStatus.PUBLISHED.value,
    )
    db_session.add(product)
    await db_session.flush()
    await db_session.refresh(product)
    request = FieldExposureRequest(
        product_id=product.id,
        buyer_id=uuid.UUID(buyer_id),
        requested_fields=list(FIELDS),
        justification="a256 probe",
        status=ExposureRequestStatus.PENDING.value,
    )
    db_session.add(request)
    await db_session.flush()
    await db_session.refresh(request)
    return provider_id, str(request.id)


@pytest.mark.asyncio
async def test_empty_approved_fields_never_approves_all(client, db_session, make_user):
    """`approved_fields: []` is "approve nothing" — a 400, never a full grant."""
    provider_id, request_id = await _pending_request(client, db_session, make_user)
    resp = await client.post(
        f"/api/v1/field-exposure/exposure-requests/{request_id}/review",
        json={"approved_fields": []},
        headers=await _headers(db_session, provider_id),
    )
    assert resp.status_code == 400, (
        "empty approved_fields was not refused — check whether it silently "
        f"granted everything: {resp.status_code} {resp.json()}"
    )
    request = await db_session.get(FieldExposureRequest, uuid.UUID(request_id))
    assert request.status == ExposureRequestStatus.PENDING.value
    assert not request.approved_fields


@pytest.mark.asyncio
async def test_absent_approved_fields_still_approves_all(client, db_session, make_user):
    """Over-tightening guard: absent keeps the shipped 'approve all' meaning."""
    provider_id, request_id = await _pending_request(client, db_session, make_user)
    resp = await client.post(
        f"/api/v1/field-exposure/exposure-requests/{request_id}/review",
        json={},
        headers=await _headers(db_session, provider_id),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == ExposureRequestStatus.APPROVED.value
    assert sorted(body["approved_fields"]) == sorted(FIELDS)
