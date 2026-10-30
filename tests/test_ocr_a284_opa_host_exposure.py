"""A284 — OPA's unauthenticated policy API must not be published to every host interface.

`docker-compose.prod.yml` ran `opa run --server` with no authentication and published
`8181:8181`, i.e. `0.0.0.0:8181` on the host. Anyone able to reach that port could
`PUT /v1/policies/...` a `default allow = true` bundle, and `policy_evaluator.py` trusts a
truthy OPA result as ALLOW — so the PDP was writable by any peer on the host's network.
The app talks to OPA over the compose bridge (`CDS_OPA_URL: http://opa:8181`), so the host
mapping is only ever needed for local operator access.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "docker-compose.prod.yml"
DOC = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
SERVICES = DOC["services"]

LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def _published(service: dict[str, Any]) -> list[tuple[str | None, str]]:
    """(host_ip_or_None, host_port) for every published mapping of a service."""
    out: list[tuple[str | None, str]] = []
    for entry in service.get("ports") or []:
        if isinstance(entry, dict):
            out.append((entry.get("host_ip"), str(entry.get("published"))))
            continue
        parts = str(entry).split(":")
        if len(parts) == 3:
            out.append((parts[0], parts[2]))
        elif len(parts) == 2:
            out.append((None, parts[0]))
        else:
            out.append((None, parts[0]))
    return out


def _bind_mounts(service: dict[str, Any]) -> list[tuple[str, str, bool]]:
    out: list[tuple[str, str, bool]] = []
    for entry in service.get("volumes") or []:
        if isinstance(entry, dict):
            out.append((str(entry.get("source")), str(entry.get("target")),
                        bool(entry.get("read_only"))))
            continue
        parts = str(entry).split(":")
        read_only = len(parts) > 2 and "ro" in parts[2].split(",")
        out.append((parts[0], parts[1] if len(parts) > 1 else "", read_only))
    return out


def test_opa_policy_api_is_not_published_on_all_interfaces():
    published = _published(SERVICES["opa"])
    assert published, "opa service publishes no port — CDS_OPA_URL wiring check below is stale"
    for host_ip, host_port in published:
        assert host_ip in LOOPBACK, (
            f"opa publishes host port {host_port} on {host_ip or '0.0.0.0 (all interfaces)'}: "
            "the OPA data API has no authentication, so a writable /v1/policies endpoint "
            "must not be reachable from the host network"
        )


def test_opa_policy_source_mount_is_read_only():
    policy_mounts = [m for m in _bind_mounts(SERVICES["opa"]) if "policies" in m[0]]
    assert policy_mounts, "opa no longer bind-mounts ./policies — re-check this gate"
    for source, target, read_only in policy_mounts:
        assert read_only, f"{source}:{target} is writable from the opa container"


def test_app_still_reaches_opa_over_the_internal_network():
    """The loopback binding must not have re-pointed the PDP client at the host port."""
    urls = [
        env.get("CDS_OPA_URL")
        for svc in SERVICES.values()
        for env in ([svc.get("environment")] if isinstance(svc.get("environment"), dict) else [])
        if isinstance(env, dict) and "CDS_OPA_URL" in env
    ]
    assert urls, "no CDS_OPA_URL found in the prod compose"
    for url in urls:
        assert isinstance(url, str) and "://opa:8181" in url, (
            f"CDS_OPA_URL must target the compose service: {url}"
        )


def test_opa_command_still_listens_inside_its_netns():
    command = " ".join(SERVICES["opa"].get("command") or [])
    assert "--server" in command and "8181" in command, command
