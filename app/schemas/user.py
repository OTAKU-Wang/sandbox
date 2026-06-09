import re
import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, field_validator


ALLOWED_SELF_REGISTER_ROLES = {"buyer", "data_provider"}

class UserCreate(BaseModel):
    username: str
    email: EmailStr
    password: str
    role: str = "buyer"
    organization: str | None = None

    @field_validator("role")
    @classmethod
    def role_must_be_allowed(cls, v: str) -> str:
        if v not in ALLOWED_SELF_REGISTER_ROLES:
            raise ValueError(f"Role must be one of: {', '.join(ALLOWED_SELF_REGISTER_ROLES)}")
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
        if not re.match(r"^[a-zA-Z0-9_]+$", v):
            raise ValueError("Username must be alphanumeric (letters, digits, underscore)")
        if len(v) < 3:
            raise ValueError("Username must be at least 3 characters")
        return v


class UserLogin(BaseModel):
    username: str
    password: str


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
