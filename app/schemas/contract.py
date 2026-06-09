import uuid
from datetime import datetime
from typing import ClassVar

from pydantic import BaseModel, field_validator


VALID_CONTRACT_TYPES = (
    "data_query", "model_training", "data_application",
    "api_service", "joint_compute", "product_dev", "data_modeling",
)
VALID_SANDBOX_LEVELS = ("L1", "L2", "L3", "k8s")


class ContractCreate(BaseModel):
    contract_type: str
    buyer_id: uuid.UUID
    product_ids: list[uuid.UUID]  # 1:N: at least one data product required
    title: str
    terms: dict | None = None
    allowed_sandbox_levels: str = "L3"
    allowed_sandbox_modes: list[str] | None = None  # e.g. ["query","train","develop"]
    allowed_operations: str = "read,analyze"
    max_duration_hours: int = 24
    dp_epsilon_budget: float | None = None
    max_output_rows: int = 10000
    allowed_output_formats: str = "csv,json"
    inspection_rule_set: dict | None = None

    @field_validator("product_ids")
    @classmethod
    def product_ids_not_empty(cls, v: list[uuid.UUID]) -> list[uuid.UUID]:
        if not v:
            raise ValueError("At least one data product is required")
        return v

    @field_validator("contract_type")
    @classmethod
    def valid_contract_type(cls, v: str) -> str:
        if v not in VALID_CONTRACT_TYPES:
            raise ValueError(f"Invalid contract_type: {v}. Must be one of {VALID_CONTRACT_TYPES}")
        return v

    @field_validator("title")
    @classmethod
    def title_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Title cannot be empty")
        if len(v) > 500:
            raise ValueError("Title too long (max 500 chars)")
        return v

    @field_validator("allowed_sandbox_levels")
    @classmethod
    def valid_sandbox_level(cls, v: str) -> str:
        levels = [l.strip() for l in v.split(",")]
        for level in levels:
            if level not in VALID_SANDBOX_LEVELS:
                raise ValueError(f"Invalid sandbox level: {level}. Must be one of {VALID_SANDBOX_LEVELS}")
        return v

    @field_validator("max_duration_hours")
    @classmethod
    def valid_duration(cls, v: int) -> int:
        if v < 1 or v > 720:  # Max 30 days
            raise ValueError("max_duration_hours must be between 1 and 720")
        return v

    @field_validator("dp_epsilon_budget")
    @classmethod
    def valid_dp_budget(cls, v: float | None) -> float | None:
        if v is not None and v <= 0:
            raise ValueError("dp_epsilon_budget must be positive")
        return v

    @field_validator("max_output_rows")
    @classmethod
    def valid_max_output_rows(cls, v: int) -> int:
        if v < 1 or v > 1_000_000:
            raise ValueError("max_output_rows must be between 1 and 1,000,000")
        return v

    VALID_OUTPUT_FORMATS: ClassVar[set[str]] = {"csv", "json", "parquet", "xlsx", "arrow"}

    @field_validator("allowed_output_formats")
    @classmethod
    def valid_output_formats(cls, v: str) -> str:
        formats = [f.strip().lower() for f in v.split(",")]
        for fmt in formats:
            if fmt not in cls.VALID_OUTPUT_FORMATS:
                raise ValueError(f"Invalid output format: {fmt}. Must be one of {cls.VALID_OUTPUT_FORMATS}")
        return v


class ContractSign(BaseModel):
    signature: str  # SM2 signature hex

    @field_validator("signature")
    @classmethod
    def signature_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Signature cannot be empty")
        return v


class ContractResponse(BaseModel):
    id: uuid.UUID
    contract_no: str
    contract_type: str
    status: str
    provider_id: uuid.UUID
    buyer_id: uuid.UUID
    product_ids: list
    title: str
    terms: dict | None
    allowed_sandbox_levels: str | None
    allowed_sandbox_modes: list | None = None
    allowed_operations: str | None
    max_duration_hours: int
    dp_epsilon_budget: float | None
    max_output_rows: int
    allowed_output_formats: str | None
    inspection_rule_set: dict | None
    provider_signed_at: datetime | None
    buyer_signed_at: datetime | None
    provider_signature: str | None
    buyer_signature: str | None
    platform_signature: str | None = None
    platform_signed_at: datetime | None = None
    blockchain_tx_hash: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
