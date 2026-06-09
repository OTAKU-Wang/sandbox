"""Data Resource API — upload, encrypt, and manage data sources."""
import io
import uuid

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.database import get_db
from app.core.deps import get_current_user, require_roles
from app.models.user import User, UserRole
from app.models.data_resource import DataResource, ResourceStatus, ResourceType, ResourceFormat
from app.schemas.data_resource import DataResourceResponse
from app.services.audit_service import audit_service
from app.services.storage_service import storage_service

router = APIRouter()

MAX_UPLOAD_SIZE = 500 * 1024 * 1024  # 500MB

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
    resource.chunk_refs = [{"path": result["path"], "size": result["size"], "sm3_hash": result["sm3_hash"]}]
    resource.status = ResourceStatus.READY.value

    await db.flush()
    await db.refresh(resource)

    await audit_service.log(
        db, action="data_resource.upload", resource_type="data_resource",
        user_id=current_user.id, resource_id=str(resource.id),
        detail={"format": detected_format.value, "size": len(content), "rows": row_count},
    )

    return DataResourceResponse.model_validate(resource)


@router.get("/", response_model=list[DataResourceResponse])
async def list_data_resources(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List data resources owned by the current user."""
    result = await db.execute(
        select(DataResource).where(DataResource.provider_id == current_user.id).order_by(DataResource.created_at.desc())
    )
    return [DataResourceResponse.model_validate(r) for r in result.scalars().all()]


@router.get("/{resource_id}", response_model=DataResourceResponse)
async def get_data_resource(
    resource_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
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
    current_user: User = Depends(get_current_user),
):
    """Delete a data resource."""
    result = await db.execute(select(DataResource).where(DataResource.id == resource_id))
    resource = result.scalar_one_or_none()
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")
    if resource.provider_id != current_user.id and current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Access denied")
    await db.delete(resource)
    return {"deleted": True, "resource_id": str(resource_id)}


@router.post("/{resource_id}/sample")
async def sample_data_resource(
    resource_id: uuid.UUID,
    sample_size: int = Query(100, ge=1, le=10000),
    method: str = Query("random", regex="^(random|systematic|first_n)$"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
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
    current_user: User = Depends(get_current_user),
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
    desensitized = test_data_generator.desensitize(data, resource.format, resource.schema_fields)

    return {
        "resource_id": str(resource_id),
        "format": "csv",
        "data": desensitized,
        "row_count": max(0, desensitized.count("\n") - 1) if desensitized else 0,
        "masking_applied": True,
    }


@router.post("/generate-mock")
async def generate_mock_data(
    schema: list[dict],
    row_count: int = Query(100, ge=1, le=10000),
    current_user: User = Depends(get_current_user),
):
    """Generate mock test data from a schema definition.

    Schema format: [{name: str, type: str, sensitivity: str}]
    Types: string, integer, float, boolean, date, datetime, email, phone, address, name
    """
    from app.services.test_data_generator import test_data_generator
    mock_data = test_data_generator.generate_mock(schema, row_count)

    return {
        "format": "csv",
        "data": mock_data,
        "row_count": row_count,
        "schema": schema,
    }
