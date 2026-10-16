import uuid
from datetime import datetime

from pydantic import BaseModel, field_validator, model_validator


VALID_SANDBOX_LEVELS = ("L0", "L1", "L2", "l2c", "L3", "k8s")
VALID_SANDBOX_MODES = (
    "structured_query",
    "structured_modeling",
    "structured_app",
    "llm_training",
    "product_dev",
    "joint_federated",
)


# 合约侧 allowed_sandbox_modes 用的是业务词表（query/train/develop…），会话侧
# sandbox_mode 用的是执行档词表（VALID_SANDBOX_MODES）。两表在这里一次性对齐，
# 任何一侧都不再自己维护别名，避免把业务词直接塞进会话校验导致 500。
SANDBOX_MODE_ALIASES: dict[str, frozenset[str]] = {
    "structured_query": frozenset({"structured_query", "query", "read"}),
    "structured_modeling": frozenset({"structured_modeling", "modeling", "analyze", "analysis"}),
    "structured_app": frozenset({"structured_app", "app", "application", "invoke"}),
    "llm_training": frozenset({"llm_training", "train", "training", "model_training"}),
    "product_dev": frozenset({"product_dev", "develop", "development", "dev", "product_develop"}),
    "joint_federated": frozenset({"joint_federated", "federated", "compute"}),
}


def canonical_sandbox_mode(mode: str | None) -> str | None:
    """合约业务词 -> 会话执行档；无法归类时返回 None（调用方决定 fail-closed）。"""
    token = str(mode or "").strip().lower()
    for canonical, aliases in SANDBOX_MODE_ALIASES.items():
        if token in aliases:
            return canonical
    return None


VALID_TEE_POLICIES = ("tee_if_available", "tee_required", "none")


class SandboxSessionCreate(BaseModel):
    data_product_id: uuid.UUID
    # SS-13 single-path cutover: default is the Cube MicroVM level (l2c);
    # legacy levels (L0/L1/L2/L3/k8s) require SANDBOX_LEGACY_ENABLED.
    sandbox_level: str = "l2c"
    # SS-13: TEE selection policy for the l2c data plane. tee_required fails
    # closed (400 TEE_NODE_UNAVAILABLE) when no TEE-capable node exists.
    tee_policy: str = "tee_if_available"
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

    @field_validator("tee_policy")
    @classmethod
    def valid_tee_policy(cls, v: str) -> str:
        if v not in VALID_TEE_POLICIES:
            raise ValueError(f"Invalid tee_policy: {v}. Must be one of {VALID_TEE_POLICIES}")
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
    # SS-13: TEE selection policy + per-session trust root (the selected cube
    # node's capabilities.tee; "none" = no per-VM TEE — honest default).
    tee_policy: str = "tee_if_available"
    trust_root: str = "none"
    # SS-13 disclosure: the session engine (derived — see _derive_sandbox_engine).
    sandbox_engine: str | None = None
    error_message: str | None
    started_at: datetime | None
    ended_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

    @model_validator(mode="after")
    def _derive_sandbox_engine(self):
        # SS-13: only l2c sessions run on the Cube MicroVM data plane; legacy
        # levels must not claim the cube engine (honest degradation).
        self.sandbox_engine = "cube_microvm" if self.sandbox_level == "l2c" else None
        return self


class SandboxExecuteRequest(BaseModel):
    code: str
    language: str = "python"
    trusted_runner: bool = False

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
