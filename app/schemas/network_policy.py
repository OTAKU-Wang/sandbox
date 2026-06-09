import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator


VALID_MODES = ("deny_all", "allowlist")


def _validate_cidrs(values: list[str]) -> list[str]:
    from ipaddress import ip_network
    for cidr in values:
        try:
            ip_network(cidr, strict=False)
        except ValueError:
            raise ValueError(f"Invalid CIDR: {cidr}")
    return values


def _validate_ports(values: list[int]) -> list[int]:
    for port in values:
        if port < 1 or port > 65535:
            raise ValueError(f"Invalid port: {port}. Must be between 1 and 65535")
    return values


class NetworkPolicyCreate(BaseModel):
    session_id: str
    mode: str = "deny_all"
    allowed_ips: list[str] = Field(default_factory=list)
    allowed_domains: list[str] = Field(default_factory=list)
    allowed_ports: list[int] = Field(default_factory=lambda: [443, 80])
    dns_proxy_enabled: bool = True
    max_connections_per_second: int = 10
    max_bandwidth_bytes_per_second: int = 0

    @field_validator("mode")
    @classmethod
    def valid_mode(cls, v: str) -> str:
        if v not in VALID_MODES:
            raise ValueError(f"Invalid mode: {v}. Must be one of {VALID_MODES}")
        return v

    @field_validator("allowed_ips")
    @classmethod
    def validate_ips(cls, v: list[str]) -> list[str]:
        return _validate_cidrs(v)

    @field_validator("allowed_ports")
    @classmethod
    def validate_ports(cls, v: list[int]) -> list[int]:
        return _validate_ports(v)


class NetworkPolicyUpdate(BaseModel):
    mode: str | None = None
    allowed_ips: list[str] | None = None
    allowed_domains: list[str] | None = None
    allowed_ports: list[int] | None = None
    dns_proxy_enabled: bool | None = None
    max_connections_per_second: int | None = None
    max_bandwidth_bytes_per_second: int | None = None
    active: bool | None = None

    @field_validator("mode")
    @classmethod
    def valid_mode(cls, v: str | None) -> str | None:
        if v is not None and v not in VALID_MODES:
            raise ValueError(f"Invalid mode: {v}. Must be one of {VALID_MODES}")
        return v

    @field_validator("allowed_ips")
    @classmethod
    def validate_ips(cls, v: list[str] | None) -> list[str] | None:
        return _validate_cidrs(v) if v is not None else v

    @field_validator("allowed_ports")
    @classmethod
    def validate_ports(cls, v: list[int] | None) -> list[int] | None:
        return _validate_ports(v) if v is not None else v


class NetworkPolicyResponse(BaseModel):
    id: uuid.UUID
    session_id: str
    user_id: str
    mode: str
    allowed_ips: list[str] | None
    allowed_domains: list[str] | None
    allowed_ports: list[int] | None
    dns_proxy_enabled: bool
    max_connections_per_second: int
    max_bandwidth_bytes_per_second: int
    active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
