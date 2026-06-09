"""Schemas for field exposure approval workflow."""
import uuid
from datetime import datetime
from pydantic import BaseModel, field_validator


class FieldRule(BaseModel):
    """Visibility rule for a single field."""
    sensitivity: str = "internal"  # public/internal/sensitive/pii/restricted
    mask_pattern: str | None = None  # e.g. "keep_first_3_last_4", "hash", "redact"
    auto_approve: bool = False
    description: str | None = None

    @field_validator("sensitivity")
    @classmethod
    def valid_sensitivity(cls, v: str) -> str:
        valid = ("public", "internal", "sensitive", "pii", "restricted")
        if v not in valid:
            raise ValueError(f"Invalid sensitivity: {v}. Must be one of {valid}")
        return v


class FieldVisibilityConfigCreate(BaseModel):
    """Set field visibility rules for a data product."""
    field_rules: dict[str, FieldRule]
    default_sensitivity: str = "internal"

    @field_validator("default_sensitivity")
    @classmethod
    def valid_default(cls, v: str) -> str:
        valid = ("public", "internal", "sensitive", "pii", "restricted")
        if v not in valid:
            raise ValueError(f"Invalid sensitivity: {v}. Must be one of {valid}")
        return v


class FieldVisibilityConfigResponse(BaseModel):
    id: uuid.UUID
    product_id: uuid.UUID
    field_rules: dict
    default_sensitivity: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ExposureRequestCreate(BaseModel):
    """Buyer requests access to specific fields."""
    product_id: uuid.UUID
    requested_fields: list[str]
    justification: str | None = None

    @field_validator("requested_fields")
    @classmethod
    def non_empty_fields(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("requested_fields cannot be empty")
        # Deduplicate while preserving order
        seen = set()
        result = []
        for f in v:
            if f not in seen:
                seen.add(f)
                result.append(f)
        return result

    @field_validator("justification")
    @classmethod
    def validate_justification(cls, v: str | None) -> str | None:
        if v is not None:
            v = v.strip()
            if len(v) > 2000:
                raise ValueError("Justification too long (max 2000 chars)")
        return v


class ExposureRequestReview(BaseModel):
    """Provider reviews an exposure request."""
    approved_fields: list[str] | None = None  # None = approve all requested
    rejection_reason: str | None = None  # If rejecting entirely

    @field_validator("rejection_reason")
    @classmethod
    def validate_reason(cls, v: str | None) -> str | None:
        if v is not None:
            v = v.strip()
            if len(v) > 1000:
                raise ValueError("Rejection reason too long (max 1000 chars)")
        return v


class ExposureRequestResponse(BaseModel):
    id: uuid.UUID
    product_id: uuid.UUID
    buyer_id: uuid.UUID
    contract_id: uuid.UUID | None
    requested_fields: list[str]
    justification: str | None
    approved_fields: list[str] | None
    rejection_reason: str | None
    status: str
    reviewed_by: uuid.UUID | None
    reviewed_at: datetime | None
    expires_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
