import uuid
from datetime import datetime

from pydantic import BaseModel, field_validator


VALID_SANDBOX_LEVELS = ("L0", "L1", "L2", "L3")


class SandboxSessionCreate(BaseModel):
    data_product_id: uuid.UUID
    sandbox_level: str = "L3"
    contract_id: str | None = None
    timeout_seconds: int = 3600
    resource_limits: dict | None = None

    @field_validator("sandbox_level")
    @classmethod
    def valid_sandbox_level(cls, v: str) -> str:
        if v not in VALID_SANDBOX_LEVELS:
            raise ValueError(f"Invalid sandbox_level: {v}. Must be one of {VALID_SANDBOX_LEVELS}")
        return v

    @field_validator("timeout_seconds")
    @classmethod
    def valid_timeout(cls, v: int) -> int:
        if v < 60 or v > 86400:  # 1 min to 24 hours
            raise ValueError("timeout_seconds must be between 60 and 86400")
        return v


class SandboxSessionResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    data_product_id: uuid.UUID
    sandbox_level: str
    status: str
    contract_id: str | None
    container_id: str | None
    session_key_id: str | None = None
    timeout_seconds: int
    resource_limits: dict | None
    error_message: str | None
    started_at: datetime | None
    ended_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
