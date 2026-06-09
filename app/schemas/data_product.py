import uuid
from datetime import datetime

from pydantic import BaseModel, field_validator


VALID_PRODUCT_TYPES = ("structured", "unstructured", "semi-structured", "api")
VALID_STATUSES = ("draft", "reviewing", "approved", "published", "suspended", "archived")
VALID_SECURITY_LEVELS = ("public", "internal", "confidential", "secret")
VALID_OPERATIONS = ("read", "query", "export", "train", "aggregate")


class DataProductCreate(BaseModel):
    name: str
    description: str | None = None
    product_type: str = "structured"
    resource_id: uuid.UUID | None = None  # Link to uploaded DataResource (recommended)
    industry: str | None = None
    data_schema: dict | None = None
    row_count: int | None = None
    security_level: str | None = None
    allowed_operations: list[str] | None = None
    output_constraints: dict | None = None

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Name cannot be empty")
        if len(v) > 200:
            raise ValueError("Name too long (max 200 chars)")
        return v

    @field_validator("product_type")
    @classmethod
    def valid_product_type(cls, v: str) -> str:
        if v not in VALID_PRODUCT_TYPES:
            raise ValueError(f"Invalid product_type: {v}. Must be one of {VALID_PRODUCT_TYPES}")
        return v

    @field_validator("row_count")
    @classmethod
    def non_negative_row_count(cls, v: int | None) -> int | None:
        if v is not None and v < 0:
            raise ValueError("row_count must be non-negative")
        return v

    @field_validator("security_level")
    @classmethod
    def valid_security_level(cls, v: str | None) -> str | None:
        if v is not None and v not in VALID_SECURITY_LEVELS:
            raise ValueError(f"Invalid security_level: {v}. Must be one of {VALID_SECURITY_LEVELS}")
        return v

    @field_validator("allowed_operations")
    @classmethod
    def valid_operations(cls, v: list[str] | None) -> list[str] | None:
        if v is not None:
            for op in v:
                if op not in VALID_OPERATIONS:
                    raise ValueError(f"Invalid operation: {op}. Must be one of {VALID_OPERATIONS}")
        return v


class DataProductUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    status: str | None = None
    industry: str | None = None
    data_schema: dict | None = None
    security_level: str | None = None
    allowed_operations: list[str] | None = None
    output_constraints: dict | None = None

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str | None) -> str | None:
        if v is not None:
            v = v.strip()
            if not v:
                raise ValueError("Name cannot be empty")
            if len(v) > 200:
                raise ValueError("Name too long (max 200 chars)")
        return v

    @field_validator("status")
    @classmethod
    def valid_status(cls, v: str | None) -> str | None:
        if v is not None and v not in VALID_STATUSES:
            raise ValueError(f"Invalid status: {v}. Must be one of {VALID_STATUSES}")
        return v

    @field_validator("security_level")
    @classmethod
    def valid_security_level(cls, v: str | None) -> str | None:
        if v is not None and v not in VALID_SECURITY_LEVELS:
            raise ValueError(f"Invalid security_level: {v}. Must be one of {VALID_SECURITY_LEVELS}")
        return v

    @field_validator("allowed_operations")
    @classmethod
    def valid_operations(cls, v: list[str] | None) -> list[str] | None:
        if v is not None:
            for op in v:
                if op not in VALID_OPERATIONS:
                    raise ValueError(f"Invalid operation: {op}. Must be one of {VALID_OPERATIONS}")
        return v


class DataProductResponse(BaseModel):
    id: uuid.UUID
    provider_id: uuid.UUID
    resource_id: uuid.UUID | None
    name: str
    description: str | None
    product_type: str
    status: str
    industry: str | None
    data_schema: dict | None
    row_count: int | None
    # NOTE: encrypted_storage_path and encryption_key_id are NEVER exposed to clients
    security_level: str | None
    allowed_operations: list[str] | None
    output_constraints: dict | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
