import uuid
from datetime import datetime

from pydantic import BaseModel, field_validator


VALID_MODES = ("deny_all", "allowlist")


class NetworkPolicyCreate(BaseModel):
    session_id: str
    mode: str = "deny_all"
    allowed_ips: list[str] = []
    allowed_domains: list[str] = []
    allowed_ports: list[int] = [443, 80]
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
        from ipaddress import ip_network
        for cidr in v:
            try:
                ip_network(cidr, strict=False)
            except ValueError:
                raise ValueError(f"Invalid CIDR: {cidr}")
        return v


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
