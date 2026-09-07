import uuid
from datetime import datetime

from pydantic import BaseModel, field_validator


VALID_SANDBOX_LEVELS = ("L0", "L1", "L2", "L3", "k8s")
VALID_SANDBOX_MODES = (
    "structured_query",
    "structured_modeling",
    "structured_app",
    "llm_training",
    "product_dev",
    "joint_federated",
)


class SandboxSessionCreate(BaseModel):
    data_product_id: uuid.UUID
    sandbox_level: str = "L3"
    sandbox_mode: str = "structured_query"
    contract_id: str | None = None
    timeout_seconds: int = 3600
    resource_limits: dict | None = None
    # Round 40 usability: optional preinstalled workspace template
    # (see GET /sandbox-sessions/session-templates for valid names).
    template: str | None = None
    # W9: idle expiry behavior + wake-on-touch.
    idle_policy: str | None = None  # "kill" | "pause" (None = platform default)
    auto_resume: bool = False

    @field_validator("idle_policy")
    @classmethod
    def valid_idle_policy(cls, v: str | None) -> str | None:
        if v is not None and v not in ("kill", "pause"):
            raise ValueError("idle_policy must be 'kill' or 'pause'")
        return v

    @field_validator("sandbox_level")
    @classmethod
    def valid_sandbox_level(cls, v: str) -> str:
        if v not in VALID_SANDBOX_LEVELS:
            raise ValueError(f"Invalid sandbox_level: {v}. Must be one of {VALID_SANDBOX_LEVELS}")
        return v

    @field_validator("sandbox_mode")
    @classmethod
    def valid_sandbox_mode(cls, v: str) -> str:
        if v not in VALID_SANDBOX_MODES:
            raise ValueError(f"Invalid sandbox_mode: {v}. Must be one of {VALID_SANDBOX_MODES}")
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
    sandbox_mode: str
    status: str
    contract_id: str | None
    container_id: str | None
    session_key_id: str | None = None
    timeout_seconds: int
    extended_seconds: int = 0
    pre_pause_status: str | None = None
    resource_limits: dict | None
    error_message: str | None
    started_at: datetime | None
    ended_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SandboxExecuteRequest(BaseModel):
    code: str
    language: str = "python"

    @field_validator("code")
    @classmethod
    def code_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("code cannot be empty")
        return v


class SandboxExecRequest(BaseModel):
    """Round 40 usability: run a shell command inside the live sandbox.

    Unlike ``/execute`` (batch code with full output-gateway pipeline), ``exec``
    is the interactive building block: short hard-capped timeout, output still
    passes the T5 DLP review before release.
    """

    command: str
    timeout_seconds: int | None = None

    @field_validator("command")
    @classmethod
    def command_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("command cannot be empty")
        if len(v) > 8192:
            raise ValueError("command too long (max 8192 chars)")
        return v

    @field_validator("timeout_seconds")
    @classmethod
    def valid_timeout(cls, v: int | None) -> int | None:
        if v is None:
            return v
        if v < 1 or v > 600:
            raise ValueError("timeout_seconds must be between 1 and 600")
        return v
