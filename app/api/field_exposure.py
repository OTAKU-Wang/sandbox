"""Field Exposure Approval API — controls sensitive field access.

Providers configure field sensitivity rules per data product.
Buyers request access to specific fields with justification.
Providers approve/reject; approved fields are enforced at query time.
"""
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.user import User
from app.models.data_product import DataProduct
from app.models.field_exposure import (
    FieldExposureRequest, ExposureRequestStatus,
    FieldVisibilityConfig, FieldSensitivity,
)
from app.schemas.field_exposure import (
    FieldVisibilityConfigCreate, FieldVisibilityConfigResponse,
    ExposureRequestCreate, ExposureRequestReview, ExposureRequestResponse,
)
from app.services.audit_service import audit_service

router = APIRouter()

# Auto-approve fields with these sensitivities
AUTO_APPROVE_SENSITIVITIES = {FieldSensitivity.PUBLIC.value, FieldSensitivity.INTERNAL.value}
# Require justification
REQUIRE_JUSTIFICATION = {FieldSensitivity.PII.value}


@router.put("/products/{product_id}/field-visibility", response_model=FieldVisibilityConfigResponse)
async def set_field_visibility(
    product_id: uuid.UUID,
    body: FieldVisibilityConfigCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Set field visibility rules for a data product (provider only)."""
    result = await db.execute(select(DataProduct).where(DataProduct.id == product_id))
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="Data product not found")
    if product.provider_id != current_user.id:
        raise HTTPException(status_code=403, detail="Only the provider can set field visibility")

    # Upsert field visibility config
    result = await db.execute(select(FieldVisibilityConfig).where(FieldVisibilityConfig.product_id == product_id))
    config = result.scalar_one_or_none()

    rules_dict = {}
    for field_name, rule in body.field_rules.items():
        rules_dict[field_name] = {
            "sensitivity": rule.sensitivity,
            "mask_pattern": rule.mask_pattern,
            "auto_approve": rule.auto_approve,
            "description": rule.description,
        }

    if config:
        config.field_rules = rules_dict
        config.default_sensitivity = body.default_sensitivity
    else:
        config = FieldVisibilityConfig(
            product_id=product_id,
            field_rules=rules_dict,
            default_sensitivity=body.default_sensitivity,
        )
        db.add(config)

    await db.flush()
    await db.refresh(config)

    await audit_service.log(
        db, action="field_visibility.update", resource_type="data_product",
        user_id=current_user.id, resource_id=str(product_id),
        detail={"fields_configured": len(rules_dict), "default_sensitivity": body.default_sensitivity},
    )

    return FieldVisibilityConfigResponse.model_validate(config)


@router.get("/products/{product_id}/field-visibility", response_model=FieldVisibilityConfigResponse)
async def get_field_visibility(
    product_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get field visibility rules for a data product."""
    result = await db.execute(select(FieldVisibilityConfig).where(FieldVisibilityConfig.product_id == product_id))
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="No field visibility config found for this product")
    return FieldVisibilityConfigResponse.model_validate(config)


@router.post("/exposure-requests", response_model=ExposureRequestResponse, status_code=201)
async def create_exposure_request(
    body: ExposureRequestCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Request access to specific fields of a data product (buyer)."""
    # Verify product exists and is published
    result = await db.execute(select(DataProduct).where(DataProduct.id == body.product_id))
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="Data product not found")
    if product.status != "published":
        raise HTTPException(status_code=400, detail="Can only request fields from published products")
    if product.provider_id == current_user.id:
        raise HTTPException(status_code=400, detail="Provider already has full access to own product")

    # Load field visibility rules
    result = await db.execute(select(FieldVisibilityConfig).where(FieldVisibilityConfig.product_id == body.product_id))
    config = result.scalar_one_or_none()
    field_rules = config.field_rules if config else {}
    default_sensitivity = config.default_sensitivity if config else FieldSensitivity.INTERNAL.value

    # Validate requested fields exist in schema
    schema_fields = set()
    if product.data_schema and "fields" in product.data_schema:
        schema_fields = {f["name"] for f in product.data_schema["fields"]}
    unknown_fields = [f for f in body.requested_fields if schema_fields and f not in schema_fields]
    if unknown_fields:
        raise HTTPException(status_code=400, detail=f"Unknown fields: {unknown_fields}")

    # Check which fields require justification
    for field_name in body.requested_fields:
        rule = field_rules.get(field_name, {})
        sensitivity = rule.get("sensitivity", default_sensitivity)
        if sensitivity in REQUIRE_JUSTIFICATION and not body.justification:
            raise HTTPException(
                status_code=400,
                detail=f"Field '{field_name}' has PII sensitivity — justification is required",
            )

    # Auto-approve public/internal fields
    auto_approved = []
    needs_review = []
    for field_name in body.requested_fields:
        rule = field_rules.get(field_name, {})
        sensitivity = rule.get("sensitivity", default_sensitivity)
        auto_approve = rule.get("auto_approve", False)
        if sensitivity in AUTO_APPROVE_SENSITIVITIES or auto_approve:
            auto_approved.append(field_name)
        else:
            needs_review.append(field_name)

    # Determine initial status
    if not needs_review:
        status = ExposureRequestStatus.APPROVED.value
        approved_fields = auto_approved
    else:
        status = ExposureRequestStatus.PENDING.value
        approved_fields = None

    request = FieldExposureRequest(
        product_id=body.product_id,
        buyer_id=current_user.id,
        requested_fields=body.requested_fields,
        justification=body.justification,
        approved_fields=approved_fields,
        status=status,
        expires_at=datetime.now(timezone.utc) + timedelta(days=30) if status == ExposureRequestStatus.APPROVED.value else None,
    )
    db.add(request)
    await db.flush()
    await db.refresh(request)

    await audit_service.log(
        db, action="field_exposure.request", resource_type="field_exposure",
        user_id=current_user.id, resource_id=str(request.id),
        detail={
            "product_id": str(body.product_id),
            "requested_fields": body.requested_fields,
            "auto_approved": auto_approved,
            "needs_review": needs_review,
        },
    )

    return ExposureRequestResponse.model_validate(request)


@router.get("/exposure-requests", response_model=list[ExposureRequestResponse])
async def list_exposure_requests(
    product_id: uuid.UUID | None = Query(None),
    status_filter: str | None = Query(None, alias="status"),
    as_provider: bool = Query(False),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List field exposure requests. Provider sees requests for their products; buyer sees own requests."""
    if as_provider:
        # Provider: get requests for products they own
        query = (
            select(FieldExposureRequest)
            .join(DataProduct, FieldExposureRequest.product_id == DataProduct.id)
            .where(DataProduct.provider_id == current_user.id)
        )
    else:
        query = select(FieldExposureRequest).where(FieldExposureRequest.buyer_id == current_user.id)

    if product_id:
        query = query.where(FieldExposureRequest.product_id == product_id)
    if status_filter:
        query = query.where(FieldExposureRequest.status == status_filter)

    query = query.order_by(FieldExposureRequest.created_at.desc())
    result = await db.execute(query)
    return [ExposureRequestResponse.model_validate(r) for r in result.scalars().all()]


@router.post("/exposure-requests/{request_id}/review", response_model=ExposureRequestResponse)
async def review_exposure_request(
    request_id: uuid.UUID,
    body: ExposureRequestReview,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Review (approve/reject) a field exposure request (provider only)."""
    result = await db.execute(select(FieldExposureRequest).where(FieldExposureRequest.id == request_id))
    request = result.scalar_one_or_none()
    if not request:
        raise HTTPException(status_code=404, detail="Exposure request not found")
    if request.status != ExposureRequestStatus.PENDING.value:
        raise HTTPException(status_code=400, detail=f"Request is already {request.status}")

    # Verify the reviewer is the product provider
    result = await db.execute(select(DataProduct).where(DataProduct.id == request.product_id))
    product = result.scalar_one_or_none()
    if not product or product.provider_id != current_user.id:
        raise HTTPException(status_code=403, detail="Only the product provider can review exposure requests")

    if body.rejection_reason and body.approved_fields:
        raise HTTPException(status_code=400, detail="Cannot both approve fields and reject — choose one")

    if body.rejection_reason:
        request.status = ExposureRequestStatus.REJECTED.value
        request.rejection_reason = body.rejection_reason
        request.approved_fields = []
    else:
        # Approve — either all requested or a subset
        approved = body.approved_fields if body.approved_fields else request.requested_fields
        # Validate that approved fields are a subset of requested
        invalid = [f for f in approved if f not in request.requested_fields]
        if invalid:
            raise HTTPException(status_code=400, detail=f"Cannot approve fields not requested: {invalid}")
        request.status = ExposureRequestStatus.APPROVED.value
        request.approved_fields = approved
        request.expires_at = datetime.now(timezone.utc) + timedelta(days=30)

    request.reviewed_by = current_user.id
    request.reviewed_at = datetime.now(timezone.utc)

    await db.flush()
    await db.refresh(request)

    await audit_service.log(
        db, action="field_exposure.review", resource_type="field_exposure",
        user_id=current_user.id, resource_id=str(request.id),
        detail={
            "product_id": str(request.product_id),
            "buyer_id": str(request.buyer_id),
            "decision": request.status,
            "approved_fields": request.approved_fields,
        },
    )

    return ExposureRequestResponse.model_validate(request)


@router.get("/exposure-requests/{request_id}", response_model=ExposureRequestResponse)
async def get_exposure_request(
    request_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get details of a specific exposure request."""
    result = await db.execute(select(FieldExposureRequest).where(FieldExposureRequest.id == request_id))
    request = result.scalar_one_or_none()
    if not request:
        raise HTTPException(status_code=404, detail="Exposure request not found")

    # Only buyer, provider, or admin can view
    result_prod = await db.execute(select(DataProduct).where(DataProduct.id == request.product_id))
    product = result_prod.scalar_one_or_none()
    if request.buyer_id != current_user.id and (not product or product.provider_id != current_user.id):
        raise HTTPException(status_code=403, detail="Access denied")

    return ExposureRequestResponse.model_validate(request)


@router.post("/exposure-requests/{request_id}/revoke", response_model=ExposureRequestResponse)
async def revoke_exposure_request(
    request_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Revoke a previously approved field exposure (provider only)."""
    result = await db.execute(select(FieldExposureRequest).where(FieldExposureRequest.id == request_id))
    request = result.scalar_one_or_none()
    if not request:
        raise HTTPException(status_code=404, detail="Exposure request not found")
    if request.status != ExposureRequestStatus.APPROVED.value:
        raise HTTPException(status_code=400, detail="Can only revoke approved requests")

    result = await db.execute(select(DataProduct).where(DataProduct.id == request.product_id))
    product = result.scalar_one_or_none()
    if not product or product.provider_id != current_user.id:
        raise HTTPException(status_code=403, detail="Only the provider can revoke field access")

    request.status = ExposureRequestStatus.REVOKED.value
    request.reviewed_by = current_user.id
    request.reviewed_at = datetime.now(timezone.utc)

    await db.flush()
    await db.refresh(request)

    await audit_service.log(
        db, action="field_exposure.revoke", resource_type="field_exposure",
        user_id=current_user.id, resource_id=str(request.id),
        detail={"product_id": str(request.product_id), "buyer_id": str(request.buyer_id)},
    )

    return ExposureRequestResponse.model_validate(request)


@router.get("/products/{product_id}/approved-fields")
async def get_approved_fields_for_buyer(
    product_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get the list of fields the current user is approved to access for a product.

    This is used by the sandbox runtime to filter query results.
    """
    result = await db.execute(
        select(FieldExposureRequest).where(
            FieldExposureRequest.product_id == product_id,
            FieldExposureRequest.buyer_id == current_user.id,
            FieldExposureRequest.status == ExposureRequestStatus.APPROVED.value,
        )
    )
    requests = result.scalars().all()

    # Merge all approved fields across requests
    approved = set()
    for req in requests:
        # Check expiry
        if req.expires_at and req.expires_at < datetime.now(timezone.utc):
            continue
        if req.approved_fields:
            approved.update(req.approved_fields)

    # Also include public fields (no approval needed)
    result_config = await db.execute(select(FieldVisibilityConfig).where(FieldVisibilityConfig.product_id == product_id))
    config = result_config.scalar_one_or_none()
    if config:
        for field_name, rule in config.field_rules.items():
            if rule.get("sensitivity") == FieldSensitivity.PUBLIC.value:
                approved.add(field_name)

    return {
        "product_id": str(product_id),
        "buyer_id": str(current_user.id),
        "approved_fields": sorted(approved),
        "total_approved": len(approved),
    }
