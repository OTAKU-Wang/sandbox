import re
import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.schemas.high_risk_operation import HighRiskOperationRequest


# PZ-10: role self-selection tightened — non-privileged roles can no longer
# be claimed at registration. Only `admin` (bootstrap account) is
# self-selectable; omitting the role yields the default (buyer), and every
# other role is assigned by an admin via PATCH /auth/users/{id}/role.
ALLOWED_SELF_REGISTER_ROLES = {"admin"}

class UserRoleChangeRequest(HighRiskOperationRequest):
    """PZ-10: admin-only role change — high-risk discipline (P-GAP-014).

    reason is mandatory (>=8 chars) and ticket_id optional; both are persisted
    into the audit trail (user.role_changed). `role` itself is validated
    against the UserRole enum in the endpoint (free-text stays 400, not 422).
    """

    role: str = Field(..., max_length=32)


class UserCreate(BaseModel):
    username: str
    email: EmailStr
    password: str
    role: str | None = None
    organization: str | None = None

    @field_validator("role")
    @classmethod
    def role_must_be_allowed(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if v not in ALLOWED_SELF_REGISTER_ROLES:
            raise ValueError(
                f"Role self-selection is limited to: {', '.join(sorted(ALLOWED_SELF_REGISTER_ROLES))} "
                "(or omitted); other roles are assigned by an admin"
            )
        return v

    @field_validator("password")
    @classmethod
    def password_min_length(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v

    @field_validator("username")
    @classmethod
    def username_alphanumeric(cls, v: str) -> str:
        if not re.match(r"^[a-zA-Z0-9_]+\Z", v):
            raise ValueError("Username must be alphanumeric (letters, digits, underscore)")
        if len(v) < 3:
            raise ValueError("Username must be at least 3 characters")
        return v


class UserLogin(BaseModel):
    username: str
    password: str
    # PZ-10: required at login for users with a confirmed MFA enrollment —
    # a TOTP code or a (one-shot) recovery code.
    mfa_code: str | None = None


class UserResponse(BaseModel):
    id: uuid.UUID
    username: str
    email: str
    role: str
    organization: str | None
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class UserOptionResponse(BaseModel):
    id: uuid.UUID
    username: str
    role: str
    organization: str | None

    model_config = {"from_attributes": True}


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UserResponse
