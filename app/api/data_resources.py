"""Data Resource API — upload, encrypt, and manage data sources."""
import io
import uuid

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.core.database import get_db
from app.core.deps import get_business_user, require_roles
from app.models.user import User, UserRole
from app.models.data_product import DataProduct
from app.models.data_resource import DataResource, ResourceStatus, ResourceType, ResourceFormat
from app.schemas.data_resource import DataResourceResponse
from app.services.audit_service import audit_service
from app.services.storage_service import storage_service

router = APIRouter()

MAX_UPLOAD_SIZE = 500 * 1024 * 1024  # 500MB

# A310: opt-in list envelope (repo convention {items,total,page,page_size}).
MAX_PAGE_SIZE = 200


class DataResourcePage(BaseModel):
    """Paginated view of the resource list (only returned when paginated=true)."""
    items: list[DataResourceResponse]
    total: int
    page: int
    page_size: int


FORMAT_MAP = {
    "text/csv": ResourceFormat.CSV,
    "application/csv": ResourceFormat.CSV,
    "application/octet-stream": ResourceFormat.OTHER,
    "application/json": ResourceFormat.JSON,
    "application/parquet": ResourceFormat.PARQUET,
    "image/jpeg": ResourceFormat.IMAGE,
    "image/png": ResourceFormat.IMAGE,
    "application/pdf": ResourceFormat.PDF,
    "text/plain": ResourceFormat.TEXT,
}


def _detect_format(content_type: str, filename: str) -> ResourceFormat:
    if content_type in FORMAT_MAP:
        return FORMAT_MAP[content_type]
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    ext_map = {"csv": ResourceFormat.CSV, "parquet": ResourceFormat.PARQUET,
               "json": ResourceFormat.JSON, "jpg": ResourceFormat.IMAGE,
               "jpeg": ResourceFormat.IMAGE, "png": ResourceFormat.IMAGE,
               "pdf": ResourceFormat.PDF, "txt": ResourceFormat.TEXT,
               "dicom": ResourceFormat.DICOM}
    return ext_map.get(ext, ResourceFormat.OTHER)


def _infer_schema_from_csv(data: bytes) -> list[dict]:
    """Infer schema from CSV data (first few rows)."""
    import csv
    try:
        text = data.decode("utf-8", errors="ignore")
        reader = csv.DictReader(io.StringIO(text))
        fields = []
        for name in (reader.fieldnames or []):
            fields.append({"name": name, "type": "string", "sensitivity": "unknown", "mask_pattern": "none"})
        # Sample a few rows to detect types
        for i, row in enumerate(reader):
            if i >= 10:
                break
            for f in fields:
                val = row.get(f["name"], "")
                if val.isdigit():
                    f["type"] = "integer"
                elif val.replace(".", "", 1).isdigit():
                    f["type"] = "float"
                elif val.lower() in ("true", "false"):
                    f["type"] = "boolean"
                elif _is_date_like(val):
                    f["type"] = "date"
                elif _is_email_like(val):
                    f["type"] = "email"
                    f["sensitivity"] = "pii"
                elif _is_phone_like(val):
                    f["type"] = "phone"
                    f["sensitivity"] = "pii"
        return fields
    except Exception:
        return []


def _infer_schema_from_json(data: bytes) -> list[dict]:
    """Infer schema from JSON data."""
    import json as _json
    try:
        parsed = _json.loads(data)
        items = parsed if isinstance(parsed, list) else parsed.get("data", [parsed])
        if not items:
            return []
        # Use first item to detect schema
        first = items[0]
        if not isinstance(first, dict):
            return []
        fields = []
        for key, val in first.items():
            field_type = "string"
            sensitivity = "unknown"
            if isinstance(val, int):
                field_type = "integer"
            elif isinstance(val, float):
                field_type = "float"
            elif isinstance(val, bool):
                field_type = "boolean"
            elif isinstance(val, str):
                if _is_email_like(val):
                    field_type = "email"
                    sensitivity = "pii"
                elif _is_phone_like(val):
                    field_type = "phone"
                    sensitivity = "pii"
                elif _is_date_like(val):
                    field_type = "date"
            fields.append({"name": key, "type": field_type, "sensitivity": sensitivity, "mask_pattern": "none"})
        return fields
    except Exception:
        return []


def _is_date_like(val: str) -> bool:
    """Check if a string looks like a date."""
    if not val or len(val) < 8:
        return False
    import re
    return bool(re.match(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}", val))


def _is_email_like(val: str) -> bool:
    return "@" in val and "." in val.split("@")[-1] if val else False


def _is_phone_like(val: str) -> bool:
    if not val:
        return False
    digits = "".join(c for c in val if c.isdigit())
    return len(digits) == 11 and digits.startswith("1")


@router.post("/upload", response_model=DataResourceResponse)
async def upload_data_resource(
    file: UploadFile = File(...),
    name: str = Form(None),
    description: str = Form(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.DATA_PROVIDER, UserRole.ADMIN)),
):
    """Upload a data file, encrypt it, and register as a data resource."""
    if not name:
        name = file.filename or "unnamed"

    # Read file content
    content = await file.read()
    if len(content) > MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=413, detail=f"File too large (max {MAX_UPLOAD_SIZE // 1024 // 1024}MB)")

    # Detect format
    detected_format = _detect_format(file.content_type or "", file.filename or "")

    # Infer schema for structured data
    schema_fields = None
    row_count = None
    if detected_format == ResourceFormat.CSV:
        schema_fields = _infer_schema_from_csv(content)
        row_count = max(0, content.count(b"\n") - 1)
    elif detected_format == ResourceFormat.JSON:
        schema_fields = _infer_schema_from_json(content)
        try:
            import json as _json
            parsed = _json.loads(content)
            items = parsed if isinstance(parsed, list) else parsed.get("data", [])
            row_count = len(items)
        except Exception:
            pass

    # Create resource record first (need ID for storage path)
    resource = DataResource(
        provider_id=current_user.id,
        name=name,
        description=description,
        resource_type=ResourceType.FILE.value,
        format=detected_format.value,
        status=ResourceStatus.ENCRYPTING.value,
        file_size_bytes=len(content),
        schema_fields=schema_fields,
        row_count=row_count,
    )
    db.add(resource)
    await db.flush()

    # Encrypt and store via StorageService (envelope encryption with KMS DEK)
    object_name = f"data-resources/{resource.id}/{file.filename or 'data'}"
    try:
        result = storage_service.upload(content, object_name)
    except Exception as e:
        resource.status = ResourceStatus.FAILED.value
        resource.error_message = str(e)
        await db.flush()
        raise HTTPException(status_code=500, detail=f"Encryption/storage failed: {e}")

    resource.storage_path = result["path"]
    resource.encryption_key_id = result.get("key_id", "")
    resource.sm3_checksum = result["checksum"]
    resource.chunk_refs = [{
        "path": result["path"],
        "size": result["size"],
        "sm3_hash": result.get("sm3_hash", result["checksum"]),
    }]
    resource.status = ResourceStatus.READY.value

    await db.flush()
    await db.refresh(resource)

    await audit_service.log(
        db, action="data_resource.upload", resource_type="data_resource",
        user_id=current_user.id, resource_id=str(resource.id),
        detail={"format": detected_format.value, "size": len(content), "rows": row_count},
    )
    # SS-14 D-e2e-14 同族 (2026-10-14): 显式 commit 后再响应 —— upload 200 先于
    # commit 可见时, 紧接的 create-product 按 resource_id 读不到行 (404)。
    await db.commit()
    return DataResourceResponse.model_validate(resource)


@router.get("/", response_model=list[DataResourceResponse] | DataResourcePage)
async def list_data_resources(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=MAX_PAGE_SIZE),
    paginated: bool = Query(False),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_business_user),
):
    """List data resources owned by the current user.

    A310: 不带分页参数时返回的仍是**裸数组**，与旧行为逐字节一致 ——
    `sdk/cds_sdk/client.py::list_data_resources` 与既有测试都按数组消费。
    显式传 `paginated=true` 的调用方拿到仓库惯例的 `{items,total,page,page_size}`
    信封，分页在 SQL 侧真做 LIMIT/OFFSET（旧形态只能让前端全量下载再 slice）。
    """
    ownership = DataResource.provider_id == current_user.id
    if not paginated:
        result = await db.execute(
            select(DataResource).where(ownership).order_by(DataResource.created_at.desc())
        )
        return [DataResourceResponse.model_validate(r) for r in result.scalars().all()]

    total = (await db.execute(
        select(func.count()).select_from(DataResource).where(ownership)
    )).scalar() or 0
    result = await db.execute(
        select(DataResource).where(ownership)
        .order_by(DataResource.created_at.desc())
        .offset((page - 1) * page_size).limit(page_size)
    )
    return {
        "items": [DataResourceResponse.model_validate(r) for r in result.scalars().all()],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/{resource_id}", response_model=DataResourceResponse)
async def get_data_resource(
    resource_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_business_user),
):
    """Get a data resource by ID."""
    result = await db.execute(select(DataResource).where(DataResource.id == resource_id))
    resource = result.scalar_one_or_none()
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")
    if resource.provider_id != current_user.id and current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Access denied")
    return DataResourceResponse.model_validate(resource)


@router.delete("/{resource_id}")
async def delete_data_resource(
    resource_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_business_user),
):
    """Delete a data resource."""
    result = await db.execute(select(DataResource).where(DataResource.id == resource_id))
    resource = result.scalar_one_or_none()
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")
    if resource.provider_id != current_user.id and current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Access denied")
    product_count_result = await db.execute(
        select(func.count())
        .select_from(DataProduct)
        .where(DataProduct.resource_id == resource_id)
    )
    product_count = product_count_result.scalar() or 0
    if product_count:
        raise HTTPException(
            status_code=409,
            detail="Cannot delete resource referenced by data products",
        )

    storage_deleted = False
    if resource.storage_path:
        try:
            storage_deleted = storage_service.delete(resource.storage_path)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to delete stored resource: {e}")
    # A312: DEK 销毁失败过去被 `except: pass` 吞掉，调用方看到的永远是成功。
    # key_destroyed 如实记录：本就没有密钥可销毁 = True（没有留下可解密的材料）；
    # destroy_key 抛错或没找到这把 key = False，由调用方（UI）如实转达。
    key_destroyed = True
    if resource.encryption_key_id:
        try:
            from app.services.kms_service import kms_service
            key_destroyed = bool(kms_service.destroy_key(resource.encryption_key_id))
        except Exception:
            key_destroyed = False

    await db.delete(resource)
    await audit_service.log(
        db, action="data_resource.delete", resource_type="data_resource",
        user_id=current_user.id, resource_id=str(resource_id),
        detail={"storage_deleted": storage_deleted, "key_destroyed": key_destroyed},
    )
    return {
        "deleted": True,
        "resource_id": str(resource_id),
        "storage_deleted": storage_deleted,
        "key_destroyed": key_destroyed,
    }


@router.post("/{resource_id}/sample")
async def sample_data_resource(
    resource_id: uuid.UUID,
    sample_size: int = Query(100, ge=1, le=10000),
    method: str = Query("random", pattern="^(random|systematic|first_n)$"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_business_user),
):
    """Sample rows from a data resource for testing.

    Methods:
    - random: Random selection of rows
    - systematic: Every Nth row
    - first_n: First N rows
    """
    result = await db.execute(select(DataResource).where(DataResource.id == resource_id))
    resource = result.scalar_one_or_none()
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")
    if resource.provider_id != current_user.id and current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Access denied")
    if not resource.storage_path:
        raise HTTPException(status_code=400, detail="Resource has no stored data")

    # Download and decrypt data
    try:
        data = storage_service.download(resource.storage_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read data: {e}")

    from app.services.test_data_generator import test_data_generator
    sampled = test_data_generator.sample_from_resource(data, resource.format, sample_size, method)

    return {
        "resource_id": str(resource_id),
        "sample_size": sample_size,
        "method": method,
        "format": "csv",
        "data": sampled,
        "row_count": max(0, sampled.count("\n") - 1) if sampled else 0,
    }


@router.post("/{resource_id}/desensitize")
async def desensitize_data_resource(
    resource_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_business_user),
):
    """Apply PII masking to a data resource for safe testing.

    Returns desensitized data that preserves structural validity
    while replacing PII fields with masked values.
    """
    result = await db.execute(select(DataResource).where(DataResource.id == resource_id))
    resource = result.scalar_one_or_none()
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")
    if resource.provider_id != current_user.id and current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Access denied")
    if not resource.storage_path:
        raise HTTPException(status_code=400, detail="Resource has no stored data")

    try:
        data = storage_service.download(resource.storage_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read data: {e}")

    from app.services.test_data_generator import test_data_generator
    # A309: masking_applied 必须是**算出来的**，不是写死的。_infer_schema_from_csv
    # 对上传文件的每一列都填 sensitivity="unknown"，普通 CSV 一个字段都不会被掩码；
    # 此时返回 true 就是在对数商谎称"PII 已按策略掩码"（禁伪造铁律）。
    desensitized, masked_columns = test_data_generator.desensitize_with_report(
        data, resource.format, resource.schema_fields
    )

    return {
        "resource_id": str(resource_id),
        "format": "csv",
        "data": desensitized,
        "row_count": max(0, desensitized.count("\n") - 1) if desensitized else 0,
        "masking_applied": bool(masked_columns),
        "masked_columns": masked_columns,
    }


def _generate_synthetic_data_from_schema(schema: list[dict], row_count: int, response_type: str):
    from app.services.test_data_generator import test_data_generator
    synthetic_data = test_data_generator.generate_mock(schema, row_count)

    return {
        "type": response_type,
        "synthetic": True,
        "format": "csv",
        "data": synthetic_data,
        "row_count": row_count,
        "schema": schema,
    }


@router.post("/generate-synthetic")
async def generate_synthetic_data(
    schema: list[dict],
    row_count: int = Query(100, ge=1, le=10000),
    current_user: User = Depends(get_business_user),
):
    """Generate synthetic test data from a schema definition.

    Schema format: [{name: str, type: str, sensitivity: str}]
    Types: string, integer, float, boolean, date, datetime, email, phone, address, name
    """
    return _generate_synthetic_data_from_schema(schema, row_count, "synthetic")


@router.post("/generate-mock", deprecated=True)
async def generate_mock_data(
    schema: list[dict],
    row_count: int = Query(100, ge=1, le=10000),
    current_user: User = Depends(get_business_user),
):
    """Deprecated compatibility alias for synthetic test data generation."""
    return _generate_synthetic_data_from_schema(schema, row_count, "mock")
