"""A284 (deploy leg) — the two remaining carriers publish OPA's unauthenticated API to the host.

`docker-compose.prod.yml` was closed by backend-27 (loopback publish + read-only policy mount).
The same defect stayed live in the two carriers owned by this track:

  * `docker-compose.yml` published `8181:8181` (=> 0.0.0.0) and bind-mounted `./policies`
    read-write into the opa container;
  * `deploy/e2e-remote/remote_deploy.sh` published `-p 8181:8181`;
  * `deploy/local/local_deploy.sh` runs on `--network host`, so `--addr=0.0.0.0:8181` binds
    every host interface directly (no published mapping to narrow).

OPA's data API has no authentication and `policy_evaluator.py` treats a truthy OPA result as
ALLOW, so a peer on the host's network could `PUT /v1/policies/...` a `default allow = true`
bundle and switch the PDP off. The app reaches OPA over the compose bridge / the docker
network / the local loopback, so the loopback binding does not cut the PDP path — which is
what the last two gates below hold fixed.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "docker-compose.yml"
LOCAL = ROOT / "deploy" / "local" / "local_deploy.sh"
REMOTE = ROOT / "deploy" / "e2e-remote" / "remote_deploy.sh"

LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _services() -> dict[str, Any]:
    doc: dict[str, Any] = yaml.safe_load(_read(COMPOSE))
    return doc["services"]


def _published(service: dict[str, Any]) -> list[tuple[str | None, str]]:
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


def test_dev_compose_opa_is_not_published_on_all_interfaces():
    published = _published(_services()["opa"])
    assert published, "opa publishes no port — the CDS_OPA_URL gate below is stale"
    for host_ip, host_port in published:
        assert host_ip in LOOPBACK, (
            f"opa publishes host port {host_port} on {host_ip or '0.0.0.0 (all interfaces)'}: "
            "the OPA data API is unauthenticated, so a writable /v1/policies endpoint must "
            "not be reachable from the host network"
        )


def test_dev_compose_opa_policy_mount_is_read_only():
    mounts = [m for m in _bind_mounts(_services()["opa"]) if "policies" in m[0]]
    assert mounts, "opa no longer bind-mounts ./policies — re-check this gate"
    for source, target, read_only in mounts:
        assert read_only, (
            f"{source}:{target} is writable from the opa container: a compromised opa process "
            "could rewrite the rego that the next deploy loads"
        )


def test_dev_compose_app_still_reaches_opa_over_the_bridge():
    """The loopback fix must not have re-pointed the PDP client at the host mapping."""
    urls = [
        env.get("CDS_OPA_URL")
        for svc in _services().values()
        for env in ([svc.get("environment")] if isinstance(svc.get("environment"), dict) else [])
        if isinstance(env, dict) and "CDS_OPA_URL" in env
    ]
    assert urls, "no CDS_OPA_URL found in docker-compose.yml"
    for url in urls:
        assert isinstance(url, str) and "://opa:8181" in url, \
            f"CDS_OPA_URL must target the compose service: {url}"


def test_dev_compose_opa_still_listens_inside_its_netns():
    command = " ".join(_services()["opa"].get("command") or [])
    assert "--server" in command and "8181" in command, command


def _opa_block(text: str, container: str) -> str:
    m = re.search(rf"run {container} [^\n]*(?:\n[ \t]+[^\n]*)*", text)
    assert m, f"no `run {container}` invocation found — retarget this gate"
    return m.group(0)


def test_remote_deploy_publishes_opa_on_loopback_only():
    block = _opa_block(_read(REMOTE), "cds-opa")
    assert re.search(r"-p\s+127\.0\.0\.1:8181:8181", block), (
        f"opa is published outside the loopback:\n{block}"
    )
    assert re.search(r"-p\s+8181:8181", block) is None, \
        "bare `-p 8181:8181` binds 0.0.0.0 — every peer on the lane host could write policies"
    sep = block.index(" -- ")
    assert block.index("-p ") < sep, (
        "the -p flag sits after the literal `--`, so the run() wrapper hands it to opa as a "
        "container argument instead of passing it to docker run"
    )
    assert "--addr=0.0.0.0:8181" in block[sep:], \
        "opa must keep listening on all interfaces inside its own netns — cds-api joins $NET"


def test_local_deploy_opa_does_not_bind_every_interface():
    """`--network host` means the container's bind address *is* the host's."""
    block = _opa_block(_read(LOCAL), "l-opa")
    assert "--addr=127.0.0.1:8181" in block, (
        f"opa binds a host-network socket on a non-loopback address:\n{block}"
    )
    assert "--addr=0.0.0.0:8181" not in block
    assert 'CDS_OPA_URL="http://127.0.0.1:8181"' in _read(LOCAL), \
        "the api must keep reaching opa over loopback in this host-network stack"
