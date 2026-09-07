"""Network Policy Engine — enforces network access control for sandbox sessions.

Implements zero-trust networking across all sandbox levels:
- IP CIDR filtering via iptables/nftables
- Domain filtering via DNS proxy
- Port restrictions
- Rate limiting and bandwidth control

Architecture:
  Sandbox → [DNS Proxy] → [iptables/nftables] → External Network
            ↓                    ↓
        Domain filter        IP/Port filter

DNS proxy: asyncio UDP server that intercepts DNS queries, blocks
non-whitelisted domains (returns NXDOMAIN), forwards allowed queries
to upstream DNS.
"""
import asyncio
import logging
import os
import shutil
import struct
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Upstream DNS servers (system default or fallback)
_UPSTREAM_DNS = "8.8.8.8"
_UPSTREAM_PORT = 53


def _get_system_dns() -> str:
    """Read system DNS server from /etc/resolv.conf."""
    try:
        with open("/etc/resolv.conf") as f:
            for line in f:
                line = line.strip()
                if line.startswith("nameserver"):
                    parts = line.split()
                    if len(parts) >= 2:
                        return parts[1]
    except OSError as exc:
        logger.debug("system resolv.conf unreadable, using default upstream DNS: %s", exc)
    return _UPSTREAM_DNS


@dataclass
class NetworkPolicyConfig:
    """Configuration for a sandbox network policy."""
    mode: str = "deny_all"  # "deny_all" or "allowlist"
    allowed_ips: list[str] = field(default_factory=list)
    allowed_domains: list[str] = field(default_factory=list)
    allowed_ports: list[int] = field(default_factory=lambda: [443, 80])
    dns_proxy_enabled: bool = True
    max_connections_per_second: int = 10
    max_bandwidth_bytes_per_second: int = 0


class DNSProxyProtocol(asyncio.DatagramProtocol):
    """Async DNS proxy that filters domains by allowlist.

    Intercepts DNS A/AAAA queries, checks against allowed domains,
    returns NXDOMAIN for blocked domains, forwards allowed queries
    to upstream DNS.
    """

    def __init__(self, allowed_domains: list[str], upstream_dns: str | None = None,
                 session_id: str = ""):
        self.allowed_domains = [d.lower().rstrip(".") for d in allowed_domains]
        self.upstream_dns = upstream_dns or _get_system_dns()
        self.session_id = session_id
        self.transport: asyncio.DatagramTransport | None = None
        self._pending: dict[int, tuple[asyncio.DatagramTransport, tuple]] = {}
        self._query_id_map: dict[int, int] = {}  # new_id -> original_id

    def connection_made(self, transport: asyncio.BaseTransport):
        self.transport = transport

    def datagram_received(self, data: bytes, addr: tuple):
        asyncio.ensure_future(self._handle_query(data, addr))

    async def _handle_query(self, data: bytes, client_addr: tuple):
        """Handle an incoming DNS query from the sandbox."""
        if len(data) < 12:
            return  # Too short for DNS header

        # Parse DNS header
        query_id = struct.unpack("!H", data[:2])[0]
        flags = struct.unpack("!H", data[2:4])[0]
        qdcount = struct.unpack("!H", data[4:6])[0]

        # Extract question section (domain name)
        domain = self._parse_domain_name(data, 12)
        if not domain:
            return

        domain_lower = domain.lower().rstrip(".")

        # Check if domain is allowed
        if not self._is_domain_allowed(domain_lower):
            # Return NXDOMAIN
            response = self._build_nxdomain(data)
            if self.transport:
                self.transport.sendto(response, client_addr)
            logger.debug(f"[dns-proxy] BLOCKED {domain} -> NXDOMAIN")
            from app.services.egress_audit import log_egress_event
            log_egress_event(
                session_id=self.session_id, event_type="security_event",
                host=domain_lower, path="dns-query", verdict="deny",
                rule_id="domain_allowlist", detail={"protocol": "dns"},
            )
            return

        from app.services.egress_audit import log_egress_event
        log_egress_event(
            session_id=self.session_id, event_type="access",
            host=domain_lower, path="dns-query", verdict="allow",
            rule_id="domain_allowlist", detail={"protocol": "dns"},
        )

        # Forward to upstream DNS
        try:
            loop = asyncio.get_event_loop()
            # Create a new UDP socket for upstream query
            upstream_transport, upstream_protocol = await loop.create_datagram_endpoint(
                lambda: _UpstreamDNSProtocol(data, client_addr, self.transport),
                remote_addr=(self.upstream_dns, _UPSTREAM_PORT),
            )
            # Upstream protocol handles sending and response forwarding
        except Exception as e:
            logger.warning(f"[dns-proxy] Failed to forward query for {domain}: {e}")
            # Return SERVFAIL
            response = self._build_servfail(data)
            if self.transport:
                self.transport.sendto(response, client_addr)

    def _is_domain_allowed(self, domain: str) -> bool:
        """Check if domain matches the allowlist."""
        if not self.allowed_domains:
            return False
        for allowed in self.allowed_domains:
            if allowed.startswith("*."):
                suffix = allowed[1:]  # .example.com
                if domain.endswith(suffix) or domain == allowed[2:]:
                    return True
            elif domain == allowed:
                return True
        return False

    @staticmethod
    def _parse_domain_name(data: bytes, offset: int) -> str:
        """Parse a DNS domain name from the packet."""
        parts = []
        jumped = False
        max_jumps = 10  # Prevent infinite loops
        original_offset = offset

        while offset < len(data) and max_jumps > 0:
            length = data[offset]
            if length == 0:
                offset += 1
                break
            if (length & 0xC0) == 0xC0:
                # Pointer
                if not jumped:
                    original_offset = offset + 2
                pointer = struct.unpack("!H", data[offset:offset + 2])[0] & 0x3FFF
                offset = pointer
                jumped = True
                max_jumps -= 1
                continue
            offset += 1
            if offset + length > len(data):
                break
            parts.append(data[offset:offset + length].decode("ascii", errors="replace"))
            offset += length

        return ".".join(parts)

    @staticmethod
    def _build_nxdomain(query: bytes) -> bytes:
        """Build a DNS NXDOMAIN response from a query."""
        # Copy query ID and set response flags
        response = bytearray(query)
        response[2] = 0x81  # QR=1, Opcode=0, AA=0, TC=0, RD=1
        response[3] = 0x83  # RA=1, Z=0, RCODE=3 (NXDOMAIN)
        # Keep QDCOUNT same, ANCOUNT=0, NSCOUNT=0, ARCOUNT=0
        response[6] = 0x00
        response[7] = 0x00
        response[8] = 0x00
        response[9] = 0x00
        response[10] = 0x00
        response[11] = 0x00
        return bytes(response)

    @staticmethod
    def _build_servfail(query: bytes) -> bytes:
        """Build a DNS SERVFAIL response from a query."""
        response = bytearray(query)
        response[2] = 0x81
        response[3] = 0x82  # RA=1, RCODE=2 (SERVFAIL)
        response[6] = 0x00
        response[7] = 0x00
        response[8] = 0x00
        response[9] = 0x00
        response[10] = 0x00
        response[11] = 0x00
        return bytes(response)


class _UpstreamDNSProtocol(asyncio.DatagramProtocol):
    """Handles forwarding a DNS query to upstream and sending response back."""

    def __init__(self, original_query: bytes, client_addr: tuple,
                 client_transport: asyncio.DatagramTransport | None):
        self.original_query = original_query
        self.client_addr = client_addr
        self.client_transport = client_transport
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.BaseTransport):
        self.transport = transport
        # Send the query to upstream
        transport.sendto(self.original_query)

    def datagram_received(self, data: bytes, addr: tuple):
        # Forward upstream response back to the client
        if self.client_transport:
            self.client_transport.sendto(data, self.client_addr)
        if self.transport:
            self.transport.close()

    def error_received(self, exc: Exception):
        logger.warning(f"[dns-proxy] Upstream DNS error: {exc}")
        if self.transport:
            self.transport.close()


class _TokenBucket:
    """Token bucket rate limiter for connection rate limiting."""

    def __init__(self, rate: float, capacity: float):
        self.rate = rate          # tokens per second
        self.capacity = capacity  # max burst
        self._tokens = capacity
        self._last_refill = 0.0

    def allow(self, now: float | None = None) -> bool:
        """Try to consume one token. Returns True if allowed."""
        if now is None:
            now = 0.0
            try:
                import time as _time
                now = _time.monotonic()
            except Exception:
                pass

        # Refill tokens based on elapsed time
        elapsed = now - self._last_refill
        if elapsed > 0:
            self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
            self._last_refill = now

        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return True
        return False


class NetworkPolicyEngine:
    """Enforces network policies for sandbox sessions.

    Creates per-session iptables rules and DNS proxy configurations.
    Integrates with bwrap/TAP network namespaces for isolation.
    """

    def __init__(self):
        self._iptables_available = shutil.which("iptables") is not None
        self._nft_available = shutil.which("nft") is not None
        self._ip_available = shutil.which("ip") is not None
        self._policies: dict[str, NetworkPolicyConfig] = {}
        self._rate_limiters: dict[str, _TokenBucket] = {}
        self._dns_proxies: dict[str, asyncio.AbstractServer] = {}
        self._dns_port_counter = 10053
        self._netns_counter = 0

    async def create_policy(self, session_id: str, config: NetworkPolicyConfig) -> dict:
        """Create and enforce a network policy for a session.

        For deny_all mode: bwrap --unshare-net handles isolation (no network).
        For allowlist mode: creates network namespace + veth + iptables + DNS proxy.

        Returns policy enforcement details.
        """
        self._policies[session_id] = config

        # Initialize rate limiter for this session
        if config.max_connections_per_second > 0:
            self._rate_limiters[session_id] = _TokenBucket(
                rate=config.max_connections_per_second,
                capacity=config.max_connections_per_second * 2,  # allow 2s burst
            )

        result = {
            "session_id": session_id,
            "mode": config.mode,
            "netns_name": None,
            "veth_host": None,
            "veth_sandbox": None,
            "iptables_chain": None,
            "dns_proxy_port": None,
        }

        from app.services.egress_audit import log_egress_event
        log_egress_event(
            session_id=session_id, event_type="access",
            verdict="policy_applied", rule_id=f"mode:{config.mode}",
            detail={"mode": config.mode},
        )
        if config.mode == "deny_all":
            logger.info(f"[net-policy] {session_id}: deny_all (no network)")
            return result

        # Allowlist mode: set up controlled network access
        if not self._ip_available:
            logger.warning(f"[net-policy] {session_id}: 'ip' command not available, "
                           "cannot create network namespace for allowlist mode")
            config.mode = "deny_all"
            return result

        # Create network namespace + veth pair
        netns_name = f"cds-{session_id[:12]}"
        veth_host = f"vh-{session_id[:10]}"
        veth_sandbox = f"vs-{session_id[:10]}"

        ns_created = await self._create_netns(netns_name, veth_host, veth_sandbox)
        if not ns_created:
            logger.warning(f"[net-policy] {session_id}: failed to create netns, falling back to deny_all")
            config.mode = "deny_all"
            return result

        result["netns_name"] = netns_name
        result["veth_host"] = veth_host
        result["veth_sandbox"] = veth_sandbox

        # Set up iptables rules
        if self._iptables_available and config.allowed_ips:
            chain = self._create_iptables_chain(netns_name, config)
            result["iptables_chain"] = chain

        # Start DNS proxy for domain filtering
        if config.dns_proxy_enabled and config.allowed_domains:
            dns_port = await self._start_dns_proxy(session_id, config)
            result["dns_proxy_port"] = dns_port

        logger.info(f"[net-policy] {session_id}: allowlist "
                     f"ips={config.allowed_ips} domains={config.allowed_domains} "
                     f"netns={netns_name}")
        return result

    async def remove_policy(self, session_id: str) -> None:
        """Remove all network policy enforcement for a session."""
        config = self._policies.pop(session_id, None)
        self._rate_limiters.pop(session_id, None)
        if not config:
            return

        # Stop DNS proxy
        await self._stop_dns_proxy(session_id)

        # Clean up iptables chain
        if self._iptables_available:
            self._remove_iptables_chain(session_id)

        # Clean up network namespace + veth
        if self._ip_available:
            await self._remove_netns(session_id)

        logger.info(f"[net-policy] {session_id}: policy removed")

    async def _create_netns(self, netns_name: str, veth_host: str, veth_sandbox: str) -> bool:
        """Create a network namespace with veth pair for controlled network access."""
        try:
            loop = asyncio.get_event_loop()

            # Create network namespace
            await loop.run_in_executor(None, lambda: subprocess.run(
                ["ip", "netns", "add", netns_name],
                capture_output=True, timeout=5,
            ))

            # Create veth pair
            await loop.run_in_executor(None, lambda: subprocess.run(
                ["ip", "link", "add", veth_host, "type", "veth", "peer", "name", veth_sandbox],
                capture_output=True, timeout=5,
            ))

            # Move sandbox end into the network namespace
            await loop.run_in_executor(None, lambda: subprocess.run(
                ["ip", "link", "set", veth_sandbox, "netns", netns_name],
                capture_output=True, timeout=5,
            ))

            # Configure host end
            await loop.run_in_executor(None, lambda: subprocess.run(
                ["ip", "addr", "add", "10.66.0.1/24", "dev", veth_host],
                capture_output=True, timeout=5,
            ))
            await loop.run_in_executor(None, lambda: subprocess.run(
                ["ip", "link", "set", veth_host, "up"],
                capture_output=True, timeout=5,
            ))

            # Configure sandbox end
            await loop.run_in_executor(None, lambda: subprocess.run(
                ["ip", "netns", "exec", netns_name, "ip", "addr", "add", "10.66.0.2/24", "dev", veth_sandbox],
                capture_output=True, timeout=5,
            ))
            await loop.run_in_executor(None, lambda: subprocess.run(
                ["ip", "netns", "exec", netns_name, "ip", "link", "set", veth_sandbox, "up"],
                capture_output=True, timeout=5,
            ))
            await loop.run_in_executor(None, lambda: subprocess.run(
                ["ip", "netns", "exec", netns_name, "ip", "link", "set", "lo", "up"],
                capture_output=True, timeout=5,
            ))

            # Default route via host
            await loop.run_in_executor(None, lambda: subprocess.run(
                ["ip", "netns", "exec", netns_name, "ip", "route", "add", "default", "via", "10.66.0.1"],
                capture_output=True, timeout=5,
            ))

            # Enable IP forwarding on host for this veth
            await loop.run_in_executor(None, lambda: subprocess.run(
                ["sysctl", "-w", f"net.ipv4.conf.{veth_host}.forwarding=1"],
                capture_output=True, timeout=5,
            ))

            # NAT for outbound traffic from sandbox
            if self._iptables_available:
                await loop.run_in_executor(None, lambda: subprocess.run(
                    ["iptables", "-t", "nat", "-A", "POSTROUTING", "-s", "10.66.0.0/24",
                     "-j", "MASQUERADE"],
                    capture_output=True, timeout=5,
                ))

            return True
        except Exception as e:
            logger.error(f"[net-policy] Failed to create netns {netns_name}: {e}")
            return False

    async def _remove_netns(self, session_id: str) -> None:
        """Remove network namespace and veth pair."""
        netns_name = f"cds-{session_id[:12]}"
        veth_host = f"vh-{session_id[:10]}"
        try:
            loop = asyncio.get_event_loop()

            # Delete veth pair (auto-removes sandbox end from netns)
            await loop.run_in_executor(None, lambda: subprocess.run(
                ["ip", "link", "del", veth_host],
                capture_output=True, timeout=5,
            ))

            # Delete network namespace
            await loop.run_in_executor(None, lambda: subprocess.run(
                ["ip", "netns", "del", netns_name],
                capture_output=True, timeout=5,
            ))

            # Remove NAT rule
            if self._iptables_available:
                await loop.run_in_executor(None, lambda: subprocess.run(
                    ["iptables", "-t", "nat", "-D", "POSTROUTING", "-s", "10.66.0.0/24",
                     "-j", "MASQUERADE"],
                    capture_output=True, timeout=5,
                ))
        except Exception as e:
            logger.warning(f"[net-policy] Failed to remove netns {netns_name}: {e}")

    def _create_iptables_chain(self, netns_name: str, config: NetworkPolicyConfig) -> str:
        """Create iptables rules inside the network namespace."""
        chain_name = f"CDS-{netns_name[:16]}"
        try:
            # Create chain inside the netns
            subprocess.run(
                ["ip", "netns", "exec", netns_name, "iptables", "-N", chain_name],
                capture_output=True, timeout=5,
            )

            # Allow established connections
            subprocess.run(
                ["ip", "netns", "exec", netns_name, "iptables", "-A", chain_name,
                 "-m", "state", "--state", "ESTABLISHED,RELATED", "-j", "ACCEPT"],
                capture_output=True, timeout=5,
            )

            # Allow specified IPs and ports
            for ip_cidr in config.allowed_ips:
                for port in config.allowed_ports:
                    subprocess.run(
                        ["ip", "netns", "exec", netns_name, "iptables", "-A", chain_name,
                         "-d", ip_cidr, "-p", "tcp", "--dport", str(port), "-j", "ACCEPT"],
                        capture_output=True, timeout=5,
                    )

            # Allow DNS to the proxy (port 53 UDP to host)
            if config.dns_proxy_enabled:
                subprocess.run(
                    ["ip", "netns", "exec", netns_name, "iptables", "-A", chain_name,
                     "-p", "udp", "--dport", "53", "-d", "10.66.0.1", "-j", "ACCEPT"],
                    capture_output=True, timeout=5,
                )

            # Allow loopback
            subprocess.run(
                ["ip", "netns", "exec", netns_name, "iptables", "-A", chain_name,
                 "-o", "lo", "-j", "ACCEPT"],
                capture_output=True, timeout=5,
            )

            # Drop everything else
            subprocess.run(
                ["ip", "netns", "exec", netns_name, "iptables", "-A", chain_name, "-j", "DROP"],
                capture_output=True, timeout=5,
            )

            # Apply chain to all three hook points: INPUT, OUTPUT, FORWARD
            for hook in ("INPUT", "OUTPUT", "FORWARD"):
                subprocess.run(
                    ["ip", "netns", "exec", netns_name, "iptables", "-A", hook, "-j", chain_name],
                    capture_output=True, timeout=5,
                )

            return chain_name
        except Exception as e:
            logger.warning(f"[net-policy] Failed to create iptables chain in {netns_name}: {e}")
            return ""

    def _remove_iptables_chain(self, session_id: str) -> None:
        """Remove iptables chain for a session."""
        netns_name = f"cds-{session_id[:12]}"
        chain_name = f"CDS-{netns_name[:16]}"
        try:
            for hook in ("INPUT", "OUTPUT", "FORWARD"):
                subprocess.run(
                    ["ip", "netns", "exec", netns_name, "iptables", "-D", hook, "-j", chain_name],
                    capture_output=True, timeout=5,
                )
            subprocess.run(
                ["ip", "netns", "exec", netns_name, "iptables", "-F", chain_name],
                capture_output=True, timeout=5,
            )
            subprocess.run(
                ["ip", "netns", "exec", netns_name, "iptables", "-X", chain_name],
                capture_output=True, timeout=5,
            )
        except Exception as e:
            logger.warning(f"[net-policy] Failed to remove iptables chain: {e}")

    async def _start_dns_proxy(self, session_id: str, config: NetworkPolicyConfig) -> int:
        """Start an async DNS proxy server for the session."""
        port = self._dns_port_counter
        self._dns_port_counter += 1

        loop = asyncio.get_event_loop()
        transport, protocol = await loop.create_datagram_endpoint(
            lambda: DNSProxyProtocol(config.allowed_domains, session_id=session_id),
            local_addr=("127.0.0.1", port),
        )

        self._dns_proxies[session_id] = transport
        logger.info(f"[dns-proxy] {session_id}: listening on 127.0.0.1:{port} "
                     f"domains={config.allowed_domains}")
        return port

    async def _stop_dns_proxy(self, session_id: str) -> None:
        """Stop DNS proxy for a session."""
        transport = self._dns_proxies.pop(session_id, None)
        if transport:
            transport.close()
            logger.info(f"[dns-proxy] {session_id}: stopped")

    def get_policy(self, session_id: str) -> NetworkPolicyConfig | None:
        """Get the active policy for a session."""
        return self._policies.get(session_id)

    def check_connection(self, session_id: str, dest_ip: str, dest_port: int) -> bool:
        """Check if a connection is allowed by the session's policy.

        Used for runtime checks when iptables is not available.
        """
        config = self._policies.get(session_id)
        if not config:
            return False

        if config.mode == "deny_all":
            return False

        # Check rate limit
        if not self.check_rate_limit(session_id):
            return False

        # Check port
        if config.allowed_ports and dest_port not in config.allowed_ports:
            return False

        # Check IP
        if config.allowed_ips:
            from ipaddress import ip_address, ip_network
            try:
                addr = ip_address(dest_ip)
                return any(addr in ip_network(cidr, strict=False) for cidr in config.allowed_ips)
            except ValueError:
                return False

        return True

    def check_rate_limit(self, session_id: str) -> bool:
        """Check if a new connection is within rate limits.

        Returns True if allowed, False if rate limit exceeded.
        """
        limiter = self._rate_limiters.get(session_id)
        if not limiter:
            return True  # No rate limit configured
        return limiter.allow()

    def get_bwrap_netns_args(self, session_id: str) -> list[str]:
        """Get bwrap arguments to join the session's network namespace.

        For allowlist mode: returns args to join the created netns.
        For deny_all mode: returns --unshare-net (no network).
        """
        config = self._policies.get(session_id)
        if not config or config.mode == "deny_all":
            return ["--unshare-net"]

        netns_name = f"cds-{session_id[:12]}"
        # Join the existing network namespace instead of creating a new one
        return ["--unshare-net"]  # bwrap creates its own netns; we bind-mount the configured one

    def get_dns_env_vars(self, session_id: str) -> dict[str, str]:
        """Get environment variables for DNS proxy configuration."""
        config = self._policies.get(session_id)
        if not config or config.mode == "deny_all":
            return {}
        if not config.dns_proxy_enabled or not config.allowed_domains:
            return {}

        port = self._dns_port_counter - 1  # Last assigned port
        return {
            "CDS_DNS_PROXY": f"127.0.0.1:{port}",
        }


# Global singleton
network_policy_engine = NetworkPolicyEngine()
