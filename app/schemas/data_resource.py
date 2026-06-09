"""Data Resource schemas."""
import uuid
from datetime import datetime
from pydantic import BaseModel, field_validator


VALID_RESOURCE_TYPES = ("file", "api", "database")
VALID_FORMATS = ("csv", "parquet", "json", "image", "text", "pdf", "dicom", "other")


class DataResourceCreate(BaseModel):
    name: str
    description: str | None = None
    resource_type: str = "file"
    format: str = "csv"

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Name cannot be empty")
        return v

    @field_validator("resource_type")
    @classmethod
    def valid_resource_type(cls, v: str) -> str:
        if v not in VALID_RESOURCE_TYPES:
            raise ValueError(f"Invalid resource_type: {v}")
        return v

    @field_validator("format")
    @classmethod
    def valid_format(cls, v: str) -> str:
        if v not in VALID_FORMATS:
            raise ValueError(f"Invalid format: {v}")
        return v


class DataResourceResponse(BaseModel):
    id: uuid.UUID
    provider_id: uuid.UUID
    name: str
    description: str | None
    resource_type: str
    format: str
    status: str
    schema_fields: list | None
    row_count: int | None
    file_size_bytes: int | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
