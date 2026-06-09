"""Network Policy model — controls network access for sandbox sessions.

Implements zero-trust networking: default deny, explicit allowlist per session.
Supports IP CIDR ranges, domain names, and port restrictions.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, String, Integer, DateTime, Boolean, JSON, ForeignKey
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


class NetworkPolicy(Base):
    """Network access policy for a sandbox session.

    Each session binds to one policy. Policy defines what the sandbox CAN access.
    Everything else is blocked by default (zero-trust).
    """
    __tablename__ = "network_policies"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(String(255), nullable=False, index=True, unique=True)
    user_id = Column(String(255), nullable=False, index=True)

    # Policy mode: "deny_all" (default, no network) or "allowlist" (explicit allows)
    mode = Column(String(32), nullable=False, default="deny_all")

    # IP allowlist (CIDR notation) — stored as JSON array
    # Examples: ["10.0.0.0/8", "172.16.0.0/12", "192.168.1.100/32"]
    allowed_ips = Column(JSON, nullable=True, default=list)

    # Domain allowlist (exact match or wildcard) — stored as JSON array
    # Examples: ["api.internal.example.com", "*.example.com"]
    allowed_domains = Column(JSON, nullable=True, default=list)

    # Port allowlist (TCP) — stored as JSON array
    # Examples: [443, 80, 8080]
    allowed_ports = Column(JSON, nullable=True, default=lambda: [443, 80])

    # Whether to enable DNS proxy for domain filtering
    dns_proxy_enabled = Column(Boolean, nullable=False, default=True)

    # Rate limit: max outbound connections per second
    max_connections_per_second = Column(Integer, nullable=False, default=10)

    # Bandwidth limit: max outbound bytes per second (0 = unlimited)
    max_bandwidth_bytes_per_second = Column(Integer, nullable=False, default=0)

    # Whether the policy is currently enforced
    active = Column(Boolean, nullable=False, default=True)

    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f"<NetworkPolicy session={self.session_id} mode={self.mode}>"

    def is_ip_allowed(self, ip: str) -> bool:
        """Check if an IP address is allowed by this policy."""
        if self.mode == "deny_all":
            return False
        if not self.allowed_ips:
            return False
        from ipaddress import ip_address, ip_network
        try:
            addr = ip_address(ip)
            return any(addr in ip_network(cidr, strict=False) for cidr in self.allowed_ips)
        except ValueError:
            return False

    def is_domain_allowed(self, domain: str) -> bool:
        """Check if a domain name is allowed by this policy."""
        if self.mode == "deny_all":
            return False
        if not self.allowed_domains:
            return False
        domain = domain.lower().rstrip(".")
        for allowed in self.allowed_domains:
            allowed = allowed.lower().rstrip(".")
            if allowed.startswith("*."):
                # Wildcard: *.example.com matches sub.example.com and example.com
                suffix = allowed[1:]  # .example.com
                if domain.endswith(suffix) or domain == allowed[2:]:
                    return True
            elif domain == allowed:
                return True
        return False

    def is_port_allowed(self, port: int) -> bool:
        """Check if a port is allowed by this policy."""
        if self.mode == "deny_all":
            return False
        if not self.allowed_ports:
            return True  # If no port restriction, all ports are allowed
        return port in self.allowed_ports
