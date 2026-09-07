import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status, Query
from sqlalchemy import select, func, or_, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.user import User, UserRole
from app.models.data_product import DataProduct, DataProductStatus
from app.models.data_resource import DataResource, ResourceStatus
from app.models.contract import Contract, ContractStatus
from app.models.sandbox_session import SandboxSession, SessionStatus
from app.schemas.data_product import DataProductCreate, DataProductUpdate, DataProductResponse
from app.services.audit_service import audit_service
from app.services.test_data_generator import test_data_generator
from app.services.storage_service import storage_service

router = APIRouter()


def _can_view_all_products(user: User) -> bool:
    return user.role in (UserRole.OPERATOR, UserRole.REGULATOR, UserRole.ADMIN)


def _visible_product_filter(user: User):
    if _can_view_all_products(user):
        return None
    if user.role == UserRole.DATA_PROVIDER:
        return DataProduct.provider_id == user.id
    return DataProduct.status == DataProductStatus.PUBLISHED.value


def _can_view_product(product: DataProduct, user: User) -> bool:
    return (
        product.provider_id == user.id
        or _can_view_all_products(user)
        or product.status == DataProductStatus.PUBLISHED.value
    )


def _update_payload_without_lifecycle_fields(body: DataProductUpdate) -> dict:
    payload = body.model_dump(exclude_unset=True)
    if "status" in payload:
        raise HTTPException(
            status_code=400,
            detail="Use data product lifecycle endpoints to change status",
        )
    return payload


def _contract_references_product(contract: Contract, product_id: uuid.UUID) -> bool:
    return str(product_id) in {str(pid) for pid in (contract.product_ids or [])}


async def _contracts_referencing_product(
    db: AsyncSession,
    product_id: uuid.UUID,
    statuses: set[str] | None = None,
) -> list[Contract]:
    query = select(Contract)
    if statuses:
        query = query.where(Contract.status.in_(statuses))
    result = await db.execute(query)
    return [
        contract for contract in result.scalars().all()
        if _contract_references_product(contract, product_id)
    ]


async def _active_session_count(db: AsyncSession, product_id: uuid.UUID) -> int:
    active_statuses = (
        SessionStatus.PROVISIONING.value,
        SessionStatus.READY.value,
        SessionStatus.RUNNING.value,
    )
    result = await db.execute(
        select(func.count())
        .select_from(SandboxSession)
        .where(
            SandboxSession.data_product_id == product_id,
            SandboxSession.status.in_(active_statuses),
        )
    )
    return result.scalar() or 0


@router.post("", response_model=DataProductResponse, status_code=status.HTTP_201_CREATED)
async def create_data_product(
    body: DataProductCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Validate resource if provided
    resource = None
    if body.resource_id:
        result = await db.execute(select(DataResource).where(DataResource.id == body.resource_id))
        resource = result.scalar_one_or_none()
        if not resource:
            raise HTTPException(status_code=404, detail="Data resource not found")
        if resource.provider_id != current_user.id:
            raise HTTPException(status_code=403, detail="Not the owner of this resource")
        if resource.status != ResourceStatus.READY.value:
            raise HTTPException(status_code=400, detail="Resource is not ready (still processing or failed)")

    product = DataProduct(
        provider_id=current_user.id,
        resource_id=body.resource_id,
        name=body.name,
        description=body.description,
        product_type=body.product_type,
        industry=body.industry,
        # Inherit schema and row_count from resource if not provided
        data_schema=body.data_schema or ({"fields": resource.schema_fields} if resource and resource.schema_fields else None),
        row_count=body.row_count or resource.row_count if resource else None,
        security_level=body.security_level,
        allowed_operations=body.allowed_operations,
        output_constraints=body.output_constraints,
    )

    # Copy encryption references from resource
    if resource:
        product.encrypted_storage_path = resource.storage_path
        product.encryption_key_id = resource.encryption_key_id
        product.sm4_checksum = resource.sm3_checksum

    db.add(product)
    await db.flush()
    await db.refresh(product)

    await audit_service.log(
        db, action="data_product.create", resource_type="data_product",
        user_id=current_user.id, resource_id=str(product.id),
        detail={"product_type": body.product_type, "name": body.name},
    )

    return DataProductResponse.model_validate(product)


@router.get("")
async def list_data_products(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=500),
    q: str | None = Query(None),
    status_filter: str | None = Query(None, alias="status"),
    product_type: str | None = Query(None),
    cursor: str | None = Query(None),
    response: Response = None,  # noqa: B008 — FastAPI header-injection parameter
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    filters = []
    visibility_filter = _visible_product_filter(current_user)
    if visibility_filter is not None:
        filters.append(visibility_filter)
    if status_filter:
        filters.append(DataProduct.status == status_filter)
    search_filter = None
    if q:
        search_filter = or_(
            DataProduct.name.ilike(f"%{q}%"),
            DataProduct.description.ilike(f"%{q}%"),
        )
        filters.append(search_filter)
    if product_type:
        filters.append(DataProduct.product_type == product_type)

    # W8: opt-in keyset mode — envelope unchanged, paging info in header.
    if cursor is not None:
        from app.core.pagination import paginate_keyset

        rows, next_cursor = await paginate_keyset(
            db, DataProduct, cursor=cursor, limit=limit,
            where=and_(*filters) if filters else None,
        )
        if next_cursor:
            response.headers["X-Next-Cursor"] = next_cursor
        return {
            "items": [DataProductResponse.model_validate(p) for p in rows],
            "total": None,
            "page": None,
            "page_size": limit,
        }

    count_query = select(func.count()).select_from(DataProduct).where(*filters)
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    query = select(DataProduct).where(*filters).offset(skip).limit(limit)
    result = await db.execute(query)
    items = [DataProductResponse.model_validate(p) for p in result.scalars().all()]
    return {"items": items, "total": total, "page": skip // limit + 1, "page_size": limit}


@router.get("/{product_id}", response_model=DataProductResponse)
async def get_data_product(
    product_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(select(DataProduct).where(DataProduct.id == product_id))
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="Data product not found")
    # Operators, regulators and admins need detail visibility for review and oversight.
    if not _can_view_product(product, current_user):
        raise HTTPException(status_code=404, detail="Data product not found")
    return DataProductResponse.model_validate(product)


@router.patch("/{product_id}", response_model=DataProductResponse)
async def update_data_product(
    product_id: uuid.UUID,
    body: DataProductUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(select(DataProduct).where(DataProduct.id == product_id))
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="Data product not found")
    if product.provider_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not the provider of this product")

    for field, value in _update_payload_without_lifecycle_fields(body).items():
        setattr(product, field, value)

    await db.flush()
    await db.refresh(product)
    return DataProductResponse.model_validate(product)


@router.put("/{product_id}", response_model=DataProductResponse)
async def update_data_product_put(
    product_id: uuid.UUID,
    body: DataProductUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(select(DataProduct).where(DataProduct.id == product_id))
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="Data product not found")
    if product.provider_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not the provider of this product")

    for field, value in _update_payload_without_lifecycle_fields(body).items():
        setattr(product, field, value)

    await db.flush()
    await db.refresh(product)
    return DataProductResponse.model_validate(product)


@router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_data_product(
    product_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(select(DataProduct).where(DataProduct.id == product_id))
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="Data product not found")
    if product.provider_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not the provider of this product")
    if product.status != DataProductStatus.DRAFT.value:
        raise HTTPException(status_code=409, detail="Only draft products can be deleted; archive non-draft products instead")
    contracts = await _contracts_referencing_product(db, product_id)
    if contracts:
        raise HTTPException(status_code=409, detail="Cannot delete product referenced by contracts")
    if await _active_session_count(db, product_id):
        raise HTTPException(status_code=409, detail="Cannot delete product with active sandbox sessions")
    await db.delete(product)


# --- Approval Workflow ---

@router.post("/{product_id}/submit", response_model=DataProductResponse)
async def submit_for_review(
    product_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Submit a draft product for review."""
    result = await db.execute(select(DataProduct).where(DataProduct.id == product_id))
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="Data product not found")
    if product.provider_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not the provider of this product")
    if product.status != DataProductStatus.DRAFT.value:
        raise HTTPException(status_code=400, detail=f"Can only submit draft products, current status: {product.status}")
    product.status = DataProductStatus.REVIEWING.value
    await db.flush()
    await db.refresh(product)
    await audit_service.log(
        db, action="data_product.submit", resource_type="data_product",
        user_id=current_user.id, resource_id=str(product.id),
        detail={"from": "draft", "to": "reviewing"},
    )
    return DataProductResponse.model_validate(product)


@router.post("/{product_id}/approve", response_model=DataProductResponse)
async def approve_product(
    product_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Approve a reviewing product (admin only)."""
    result = await db.execute(select(DataProduct).where(DataProduct.id == product_id))
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="Data product not found")
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin approval required")
    if product.status != DataProductStatus.REVIEWING.value:
        raise HTTPException(status_code=400, detail=f"Can only approve reviewing products, current status: {product.status}")
    product.status = DataProductStatus.APPROVED.value
    await db.flush()
    await db.refresh(product)
    await audit_service.log(
        db, action="data_product.approve", resource_type="data_product",
        user_id=current_user.id, resource_id=str(product.id),
        detail={"approved_by": str(current_user.id)},
    )
    return DataProductResponse.model_validate(product)


@router.post("/{product_id}/publish", response_model=DataProductResponse)
async def publish_product(
    product_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Publish an approved product."""
    result = await db.execute(select(DataProduct).where(DataProduct.id == product_id))
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="Data product not found")
    if product.provider_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not the provider of this product")
    if product.status != DataProductStatus.APPROVED.value:
        raise HTTPException(status_code=400, detail=f"Can only publish approved products, current status: {product.status}")
    if not product.resource_id:
        raise HTTPException(status_code=400, detail="Product must be linked to a data resource before publishing")
    product.status = DataProductStatus.PUBLISHED.value
    await db.flush()
    await db.refresh(product)
    await audit_service.log(
        db, action="data_product.publish", resource_type="data_product",
        user_id=current_user.id, resource_id=str(product.id),
        detail={"product_type": product.product_type, "resource_id": str(product.resource_id)},
    )
    return DataProductResponse.model_validate(product)


@router.post("/{product_id}/reject", response_model=DataProductResponse)
async def reject_product(
    product_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Reject a reviewing product back to draft (admin only)."""
    result = await db.execute(select(DataProduct).where(DataProduct.id == product_id))
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="Data product not found")
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin approval required")
    if product.status != DataProductStatus.REVIEWING.value:
        raise HTTPException(status_code=400, detail=f"Can only reject reviewing products, current status: {product.status}")
    product.status = DataProductStatus.DRAFT.value
    await db.flush()
    await db.refresh(product)
    await audit_service.log(
        db, action="data_product.reject", resource_type="data_product",
        user_id=current_user.id, resource_id=str(product.id),
        detail={"rejected_by": str(current_user.id)},
    )
    return DataProductResponse.model_validate(product)


@router.post("/{product_id}/archive", response_model=DataProductResponse)
async def archive_product(
    product_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Archive a non-draft product after active usage has stopped."""
    result = await db.execute(select(DataProduct).where(DataProduct.id == product_id))
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="Data product not found")
    if product.provider_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not the provider of this product")
    if product.status == DataProductStatus.DRAFT.value:
        raise HTTPException(status_code=400, detail="Draft products can be deleted directly")
    if product.status == DataProductStatus.ARCHIVED.value:
        return DataProductResponse.model_validate(product)

    blocking_statuses = {
        ContractStatus.NEGOTIATING.value,
        ContractStatus.SIGNED.value,
        ContractStatus.ACTIVE.value,
        ContractStatus.SUSPENDED.value,
    }
    contracts = await _contracts_referencing_product(db, product_id, blocking_statuses)
    if contracts:
        raise HTTPException(status_code=409, detail="Cannot archive product referenced by active contracts")
    if await _active_session_count(db, product_id):
        raise HTTPException(status_code=409, detail="Cannot archive product with active sandbox sessions")

    previous_status = product.status
    product.status = DataProductStatus.ARCHIVED.value
    await db.flush()
    await db.refresh(product)

    await audit_service.log(
        db, action="data_product.archive", resource_type="data_product",
        user_id=current_user.id, resource_id=str(product.id),
        detail={"from": previous_status, "to": DataProductStatus.ARCHIVED.value},
    )
    return DataProductResponse.model_validate(product)


async def _generate_synthetic_test_data_for_product(
    product_id: uuid.UUID,
    row_count: int,
    db: AsyncSession,
    current_user: User,
    response_type: str,
):
    result = await db.execute(select(DataProduct).where(DataProduct.id == product_id))
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="Data product not found")
    if product.provider_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not the provider of this product")
    if not product.data_schema or "fields" not in product.data_schema:
        raise HTTPException(status_code=400, detail="Product has no schema defined — set data_schema.fields first")

    synthetic_data = test_data_generator.generate_mock(product.data_schema["fields"], row_count)

    await audit_service.log(
        db, action="data_product.test_data.synthetic", resource_type="data_product",
        user_id=current_user.id, resource_id=str(product.id),
        detail={"row_count": row_count, "fields": len(product.data_schema["fields"]), "response_type": response_type},
    )

    return {
        "product_id": str(product_id),
        "type": response_type,
        "synthetic": True,
        "row_count": row_count,
        "format": "csv",
        "data": synthetic_data,
        "schema_fields": [f["name"] for f in product.data_schema["fields"]],
    }


# --- Test Data Generation for Development ---

@router.post("/{product_id}/test-data/synthetic")
async def generate_synthetic_test_data(
    product_id: uuid.UUID,
    row_count: int = Query(100, ge=1, le=10000),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Generate synthetic test data from the product's schema definition.

    Uses the product's data_schema to produce structurally valid, non-real data
    for pipeline development and testing.
    """
    return await _generate_synthetic_test_data_for_product(product_id, row_count, db, current_user, "synthetic")


@router.post("/{product_id}/test-data/mock", deprecated=True)
async def generate_mock_test_data(
    product_id: uuid.UUID,
    row_count: int = Query(100, ge=1, le=10000),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Deprecated compatibility alias for synthetic test data generation."""
    return await _generate_synthetic_test_data_for_product(product_id, row_count, db, current_user, "mock")


@router.post("/{product_id}/test-data/sample")
async def sample_real_test_data(
    product_id: uuid.UUID,
    sample_size: int = Query(100, ge=1, le=10000),
    method: str = Query("random", pattern="^(random|systematic|first_n)$"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Sample real data from the linked resource for testing.

    Extracts a subset of actual data (without PII masking) for
    pipeline development. Only the product provider can do this.
    """
    result = await db.execute(select(DataProduct).where(DataProduct.id == product_id))
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="Data product not found")
    if product.provider_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not the provider of this product")
    if not product.resource_id:
        raise HTTPException(status_code=400, detail="Product has no linked data resource")

    from app.models.data_resource import DataResource
    result = await db.execute(select(DataResource).where(DataResource.id == product.resource_id))
    resource = result.scalar_one_or_none()
    if not resource or not resource.storage_path:
        raise HTTPException(status_code=400, detail="Resource has no stored data")

    try:
        data = storage_service.download(resource.storage_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read data: {e}")

    sampled = test_data_generator.sample_from_resource(data, resource.format, sample_size, method)

    await audit_service.log(
        db, action="data_product.test_data.sample", resource_type="data_product",
        user_id=current_user.id, resource_id=str(product.id),
        detail={"sample_size": sample_size, "method": method, "resource_id": str(product.resource_id)},
    )

    return {
        "product_id": str(product_id),
        "type": "real_sample",
        "sample_size": sample_size,
        "method": method,
        "format": "csv",
        "data": sampled,
        "row_count": max(0, sampled.count("\n") - 1) if sampled else 0,
    }


@router.post("/{product_id}/test-data/desensitize")
async def desensitize_real_test_data(
    product_id: uuid.UUID,
    sample_size: int = Query(100, ge=1, le=10000),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Generate desensitized test data from the linked resource.

    Takes a real data sample and applies PII masking (phone, email,
    ID card, name, address, bank card) while preserving structural
    validity for pipeline testing.
    """
    result = await db.execute(select(DataProduct).where(DataProduct.id == product_id))
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="Data product not found")
    if product.provider_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not the provider of this product")
    if not product.resource_id:
        raise HTTPException(status_code=400, detail="Product has no linked data resource")

    from app.models.data_resource import DataResource
    result = await db.execute(select(DataResource).where(DataResource.id == product.resource_id))
    resource = result.scalar_one_or_none()
    if not resource or not resource.storage_path:
        raise HTTPException(status_code=400, detail="Resource has no stored data")

    try:
        data = storage_service.download(resource.storage_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read data: {e}")

    # First sample, then desensitize
    sampled_bytes = test_data_generator.sample_from_resource(data, resource.format, sample_size, "random").encode("utf-8")
    desensitized = test_data_generator.desensitize(sampled_bytes, "csv", resource.schema_fields)

    await audit_service.log(
        db, action="data_product.test_data.desensitize", resource_type="data_product",
        user_id=current_user.id, resource_id=str(product.id),
        detail={"sample_size": sample_size, "resource_id": str(product.resource_id)},
    )

    return {
        "product_id": str(product_id),
        "type": "desensitized",
        "sample_size": sample_size,
        "format": "csv",
        "data": desensitized,
        "row_count": max(0, desensitized.count("\n") - 1) if desensitized else 0,
        "masking_applied": True,
    }


@router.post("/{product_id}/new-version", status_code=status.HTTP_201_CREATED)
async def create_new_version(
    product_id: uuid.UUID,
    change_summary: str = "",
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a new version of a data product. Copies metadata and increments version."""
    result = await db.execute(select(DataProduct).where(DataProduct.id == product_id))
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="Data product not found")
    if product.provider_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not the owner of this product")

    # Mark current version as not latest
    product.is_latest = False

    # Create new version
    new_product = DataProduct(
        provider_id=current_user.id,
        resource_id=product.resource_id,
        name=product.name,
        description=product.description,
        product_type=product.product_type,
        industry=product.industry,
        data_schema=product.data_schema,
        row_count=product.row_count,
        encrypted_storage_path=product.encrypted_storage_path,
        encryption_key_id=product.encryption_key_id,
        sm4_checksum=product.sm4_checksum,
        security_level=product.security_level,
        allowed_operations=product.allowed_operations,
        output_constraints=product.output_constraints,
        version=product.version + 1,
        parent_id=product.id,
        change_summary=change_summary,
        is_latest=True,
        status=DataProductStatus.DRAFT.value,
    )
    db.add(new_product)
    await db.flush()
    await db.refresh(new_product)

    await audit_service.log(
        db, action="data_product.version.create", resource_type="data_product",
        user_id=current_user.id, resource_id=str(new_product.id),
        detail={"parent_id": str(product.id), "version": new_product.version},
    )

    return {
        "id": str(new_product.id),
        "version": new_product.version,
        "parent_id": str(product.id),
        "status": new_product.status,
        "change_summary": change_summary,
    }


@router.get("/{product_id}/versions")
async def list_versions(
    product_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List all versions of a data product."""
    result = await db.execute(select(DataProduct).where(DataProduct.id == product_id))
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="Data product not found")
    if not _can_view_product(product, current_user):
        raise HTTPException(status_code=404, detail="Data product not found")

    # Find the root product (earliest version)
    root_id = product_id
    current = product
    while current.parent_id:
        prev_result = await db.execute(select(DataProduct).where(DataProduct.id == current.parent_id))
        prev = prev_result.scalar_one_or_none()
        if not prev:
            break
        root_id = prev.id
        current = prev

    # Get all versions from root
    versions = []
    to_visit = [root_id]
    visited = set()
    while to_visit:
        vid = to_visit.pop(0)
        if vid in visited:
            continue
        visited.add(vid)
        v_result = await db.execute(select(DataProduct).where(DataProduct.id == vid))
        v = v_result.scalar_one_or_none()
        if v and _can_view_product(v, current_user):
            versions.append({
                "id": str(v.id),
                "version": v.version,
                "status": v.status,
                "is_latest": v.is_latest,
                "change_summary": v.change_summary,
                "created_at": v.created_at.isoformat(),
            })
        # Find children
        child_result = await db.execute(select(DataProduct).where(DataProduct.parent_id == vid))
        for child in child_result.scalars().all():
            to_visit.append(child.id)

    versions.sort(key=lambda x: x["version"])
    return {"product_id": str(product_id), "versions": versions}
