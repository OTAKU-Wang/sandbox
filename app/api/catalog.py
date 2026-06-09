"""Data Catalog API — buyer-facing search and browse for published data products."""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.user import User
from app.models.data_product import DataProduct, DataProductStatus

router = APIRouter()


@router.get("")
async def search_catalog(
    q: str | None = Query(None, description="Search query (name/description)"),
    product_type: str | None = Query(None),
    industry: str | None = Query(None),
    security_level: str | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Search published data products. Only returns products with status='published'."""
    query = select(DataProduct).where(DataProduct.status == DataProductStatus.PUBLISHED.value)
    count_query = select(func.count()).select_from(DataProduct).where(DataProduct.status == DataProductStatus.PUBLISHED.value)

    if q:
        search_filter = or_(
            DataProduct.name.ilike(f"%{q}%"),
            DataProduct.description.ilike(f"%{q}%"),
        )
        query = query.where(search_filter)
        count_query = count_query.where(search_filter)

    if product_type:
        query = query.where(DataProduct.product_type == product_type)
        count_query = count_query.where(DataProduct.product_type == product_type)

    if industry:
        query = query.where(DataProduct.industry == industry)
        count_query = count_query.where(DataProduct.industry == industry)

    if security_level:
        query = query.where(DataProduct.security_level == security_level)
        count_query = count_query.where(DataProduct.security_level == security_level)

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    query = query.order_by(DataProduct.created_at.desc()).offset(skip).limit(limit)
    result = await db.execute(query)

    items = []
    for p in result.scalars().all():
        items.append({
            "id": str(p.id),
            "name": p.name,
            "description": p.description,
            "product_type": p.product_type,
            "industry": p.industry,
            "security_level": p.security_level,
            "allowed_operations": p.allowed_operations,
            "row_count": p.row_count,
            "provider_id": str(p.provider_id),
            "created_at": p.created_at.isoformat() if p.created_at else None,
        })

    return {"items": items, "total": total, "page": skip // limit + 1, "page_size": limit}


@router.get("/{product_id}")
async def get_catalog_product(
    product_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get details of a published data product from the catalog."""
    result = await db.execute(
        select(DataProduct).where(
            DataProduct.id == product_id,
            DataProduct.status == DataProductStatus.PUBLISHED.value,
        )
    )
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found in catalog")

    return {
        "id": str(product.id),
        "name": product.name,
        "description": product.description,
        "product_type": product.product_type,
        "industry": product.industry,
        "security_level": product.security_level,
        "allowed_operations": product.allowed_operations,
        "output_constraints": product.output_constraints,
        "row_count": product.row_count,
        "data_schema": product.data_schema,
        "provider_id": str(product.provider_id),
        "created_at": product.created_at.isoformat() if product.created_at else None,
        "updated_at": product.updated_at.isoformat() if product.updated_at else None,
    }
