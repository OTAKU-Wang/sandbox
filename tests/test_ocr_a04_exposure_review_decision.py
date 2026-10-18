"""A4 — a blank rejection reason must not become a full approval.

``POST /api/v1/field-exposure/exposure-requests/{id}/review`` decided between
approve and reject with *truthiness*:

* ``ExposureRequestReview.validate_reason`` only ``strip()``s, so
  ``rejection_reason="   "`` arrives as ``""`` — falsy — and the handler fell
  through to the approval branch;
* ``approved = body.approved_fields if body.approved_fields else request.requested_fields``
  treated ``[]`` the same as ``None`` ("approve everything requested").

Combined, one whitespace-only rejection granted the buyer every field it had
asked for — PII included — and recorded that as ``approved``. The decision is
now taken from *presence* (``is not None``), and an empty ``approved_fields``
list means "approve nothing", never "approve all".
"""
import uuid

import pytest

from app.models.data_product import DataProduct, DataProductStatus
from app.models.field_exposure import FieldExposureRequest, ExposureRequestStatus
from app.models.user import User
from app.services.auth_service import create_access_token

FIELDS = ["city", "identity_no"]


async def _headers(db_session, user_id: str) -> dict:
    user = await db_session.get(User, uuid.UUID(user_id))
    return {"Authorization": f"Bearer {create_access_token(user.id, user.role)}"}


async def _setup(client, db_session, make_user):
    """Published product with a PII field, plus a buyer that may request fields."""
    _, provider_id = await make_user("data_provider", "a4_provider")
    _, buyer_id = await make_user("buyer", "a4_buyer")
    product = DataProduct(
        provider_id=uuid.UUID(provider_id),
        name="A4 Product",
        product_type="structured",
        status=DataProductStatus.PUBLISHED.value,
    )
    db_session.add(product)
    await db_session.flush()
    await db_session.refresh(product)

    config = await client.put(
        f"/api/v1/field-exposure/products/{product.id}/field-visibility",
        json={"field_rules": {"city": {"sensitivity": "internal"},
                              "identity_no": {"sensitivity": "pii"}},
              "default_sensitivity": "internal"},
        headers=await _headers(db_session, provider_id),
    )
    assert config.status_code == 200, config.text
    return provider_id, buyer_id, str(product.id)


async def _request(client, db_session, buyer_id, product_id) -> str:
    """A pending exposure request for both fields (``identity_no`` needs review)."""
    created = await client.post(
        "/api/v1/field-exposure/exposure-requests",
        json={"product_id": product_id, "requested_fields": list(FIELDS),
              "justification": "risk model"},
        headers=await _headers(db_session, buyer_id),
    )
    assert created.status_code == 201, created.text
    assert created.json()["status"] == ExposureRequestStatus.PENDING.value
    return created.json()["id"]


async def _review(client, db_session, reviewer_id, request_id, payload):
    return await client.post(
        f"/api/v1/field-exposure/exposure-requests/{request_id}/review",
        json=payload,
        headers=await _headers(db_session, reviewer_id),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("blank_reason", ["   ", "", "\n\t "], ids=["spaces", "empty", "newline"])
async def test_blank_rejection_reason_never_approves(
    client, db_session, make_user, blank_reason
):
    """The whole point of A4: a blank rejection must not hand out the fields."""
    provider_id, buyer_id, product_id = await _setup(client, db_session, make_user)
    request_id = await _request(client, db_session, buyer_id, product_id)

    resp = await _review(client, db_session, provider_id, request_id,
                         {"rejection_reason": blank_reason})

    assert resp.status_code == 400, (
        f"a blank rejection_reason must be refused, not served: "
        f"got {resp.status_code} -> {resp.json()}"
    )
    assert resp.json()["code"] == "HTTP_400"

    request = await db_session.get(FieldExposureRequest, uuid.UUID(request_id))
    assert request.status == ExposureRequestStatus.PENDING.value
    assert not request.approved_fields, "the reversal stored every requested field as approved"
    assert request.reviewed_by is None


@pytest.mark.asyncio
async def test_empty_approved_fields_list_does_not_approve_everything(
    client, db_session, make_user
):
    """``[]`` is "approve nothing"; only ``None``/absent means "approve all"."""
    provider_id, buyer_id, product_id = await _setup(client, db_session, make_user)
    request_id = await _request(client, db_session, buyer_id, product_id)

    resp = await _review(client, db_session, provider_id, request_id, {"approved_fields": []})

    assert resp.status_code == 400, f"empty approved_fields must not grant all: {resp.json()}"
    assert resp.json()["code"] == "HTTP_400"
    request = await db_session.get(FieldExposureRequest, uuid.UUID(request_id))
    assert request.status == ExposureRequestStatus.PENDING.value
    assert not request.approved_fields


@pytest.mark.asyncio
async def test_reject_and_approve_together_is_still_refused(client, db_session, make_user):
    provider_id, buyer_id, product_id = await _setup(client, db_session, make_user)
    request_id = await _request(client, db_session, buyer_id, product_id)

    resp = await _review(client, db_session, provider_id, request_id,
                         {"rejection_reason": "not comfortable", "approved_fields": ["city"]})

    assert resp.status_code == 400
    request = await db_session.get(FieldExposureRequest, uuid.UUID(request_id))
    assert request.status == ExposureRequestStatus.PENDING.value


@pytest.mark.asyncio
async def test_legitimate_review_paths_still_work(client, db_session, make_user):
    """Tightening must leave the provider's real decisions usable."""
    provider_id, buyer_id, product_id = await _setup(client, db_session, make_user)
    subset_id = await _request(client, db_session, buyer_id, product_id)
    all_id = await _request(client, db_session, buyer_id, product_id)
    reject_id = await _request(client, db_session, buyer_id, product_id)

    subset = await _review(client, db_session, provider_id, subset_id, {"approved_fields": ["city"]})
    assert subset.status_code == 200, subset.text
    assert subset.json()["status"] == "approved"
    assert subset.json()["approved_fields"] == ["city"]

    approve_all = await _review(client, db_session, provider_id, all_id, {"approved_fields": None})
    assert approve_all.status_code == 200, approve_all.text
    assert approve_all.json()["status"] == "approved"
    assert set(approve_all.json()["approved_fields"]) == set(FIELDS)

    rejected = await _review(client, db_session, provider_id, reject_id,
                             {"rejection_reason": "purpose too broad"})
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "rejected"
    assert rejected.json()["rejection_reason"] == "purpose too broad"
    assert rejected.json()["approved_fields"] == []


@pytest.mark.asyncio
async def test_reviewer_role_boundary_unchanged(client, db_session, make_user):
    """A stranger (not the provider, not admin) still gets 403 on every shape."""
    _provider_id, buyer_id, product_id = await _setup(client, db_session, make_user)
    request_id = await _request(client, db_session, buyer_id, product_id)
    _other, stranger = await make_user("data_provider", "a4_other_provider")

    for payload in ({"rejection_reason": "not my product"}, {"approved_fields": FIELDS},
                    {"rejection_reason": "   "}):
        resp = await _review(client, db_session, stranger, request_id, payload)
        assert resp.status_code == 403, payload
        assert resp.json()["code"] == "HTTP_403", payload

    request = await db_session.get(FieldExposureRequest, uuid.UUID(request_id))
    assert request.status == ExposureRequestStatus.PENDING.value
    assert not request.approved_fields
