"""A89 (deploy leg) — no default deploy carrier may sign JWTs with a key published in the source.

backend-17a closed the code half: `auth_service._effective_jwt_secret_key()` no longer
returns the source-published `_LOCAL_DEFAULT_JWT_KEY` under DEBUG (per-process random key
+ one-time WARNING). The deploy half is this file's subject: a carrier can hand the app an
explicit key of its own, and `docker-compose.yml` did exactly that with

    CDS_JWT_SECRET_KEY: ${CDS_JWT_SECRET_KEY:-dev_jwt_secret_change_in_prod_32bytes}

That literal differs from `app.core.config._JWT_DEFAULT_KEY` ("change-me-in-production"), so
on the default compose stack `settings.JWT_SECRET_KEY != _JWT_DEFAULT_KEY` is true and the
resolver returns the repo literal verbatim: every token on a stock `docker compose up` stack
is signed with a key anybody holding the source can forge `role=admin` with.

Posture chosen for the deploy leg (see .gate-scratch/evidence/owner-deploy.md): fail closed
at deploy time. `:?` required interpolation refuses the deploy before a container starts, so
no stack can boot signing with a published key. The rejected alternative (default to empty
and let the app fall back to its DEBUG ephemeral key) keeps `docker compose up` green while
silently issuing tokens that die on restart/replica.

`deploy/local/local_deploy.sh` generates its own random key into /tmp; its hole is passing
through a *missing* key from a stale env file, which the app consumes as an empty secret.
`deploy/e2e-remote/remote_deploy.sh` already fails fast on an empty key (A301 whitelist); its
HS256 length floor is pinned here so all three carriers stay in parity.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "docker-compose.yml"
LOCAL = ROOT / "deploy" / "local" / "local_deploy.sh"
REMOTE = ROOT / "deploy" / "e2e-remote" / "remote_deploy.sh"

COMPOSE_DEV_KEY = "dev_jwt_secret_change_in_prod_32bytes"
SOURCE_LOCAL_KEY = "cds-local-dev-test-jwt-secret-key-32-bytes-minimum"
CONFIG_DEFAULT_KEY = "change-me-in-production"
REQUIRED_INTERP = re.compile(r"^\$\{CDS_JWT_SECRET_KEY:\?\S.*\}$")
KEY_LENGTH_FLOOR = 32


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _api_env() -> dict[str, Any]:
    doc: dict[str, Any] = yaml.safe_load(_read(COMPOSE))
    return doc["services"]["cds-api"]["environment"]


def _bash() -> str | None:
    return shutil.which("bash")


def _have_compose() -> bool:
    try:
        return subprocess.run(["docker", "compose", "version"],
                              capture_output=True, timeout=60).returncode == 0
    except Exception:
        return False


def test_compose_ships_no_publishable_jwt_key():
    """A key that lives in this repository is not a secret, so it must not be a default."""
    text = _read(COMPOSE)
    for public in (COMPOSE_DEV_KEY, SOURCE_LOCAL_KEY, CONFIG_DEFAULT_KEY):
        assert public not in text, (
            f"docker-compose.yml commits JWT key literal {public!r} — anyone holding the "
            "source can forge tokens on a stack started with `docker compose up`"
        )


def test_compose_jwt_secret_is_required_interpolation():
    value = _api_env().get("CDS_JWT_SECRET_KEY")
    assert isinstance(value, str), "cds-api has no CDS_JWT_SECRET_KEY — this gate is stale"
    assert REQUIRED_INTERP.match(value), (
        f"CDS_JWT_SECRET_KEY must be ${{CDS_JWT_SECRET_KEY:?...}} required interpolation, "
        f"got {value!r}: any `:-` default is a signing key published in the source tree"
    )
    assert "${CDS_JWT_SECRET_KEY:-" not in _read(COMPOSE)


@pytest.mark.skipif(not _have_compose(), reason="docker compose not available")
def test_compose_refuses_to_render_without_an_operator_key(tmp_path):
    """Interpolation is read from the real compose CLI, not from a regex over the text."""
    env = {k: v for k, v in os.environ.items() if k != "CDS_JWT_SECRET_KEY"}
    empty = tmp_path / "empty.env"
    empty.write_text("", encoding="utf-8")
    r = subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE), "--env-file", str(empty), "config"],
        cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=180)
    out = r.stdout + r.stderr
    assert r.returncode != 0, (
        "docker compose config succeeded with CDS_JWT_SECRET_KEY unset, so the stack boots "
        f"on a fallback key:\n{out[-400:]}"
    )
    assert COMPOSE_DEV_KEY not in out, "compose still renders a committed fallback key"


@pytest.mark.skipif(not _have_compose(), reason="docker compose not available")
def test_compose_renders_the_operator_key_when_supplied(tmp_path):
    """Fail closed must mean "operator key required", not "stack cannot be deployed"."""
    marker = "k" * 48
    empty = tmp_path / "empty.env"
    empty.write_text("", encoding="utf-8")
    env = {**os.environ, "CDS_JWT_SECRET_KEY": marker}
    r = subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE), "--env-file", str(empty), "config"],
        cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, r.stderr[-600:]
    assert marker in r.stdout, "the supplied CDS_JWT_SECRET_KEY never reached the rendered env"


def test_local_deploy_fails_closed_before_starting_anything():
    text = _read(LOCAL)
    guard = re.search(r"for v in ([^;]*CDS_JWT_SECRET_KEY[^;]*); do", text)
    assert guard, "local_deploy.sh has no required-secret guard covering CDS_JWT_SECRET_KEY"
    assert "CDS_JWT_SECRET_KEY" in guard.group(1).split()
    body = text[guard.start():guard.end() + 300]
    assert re.search(r'\[\s*-z\s+"\$\{!v:-\}"\s*\]', body), "guard does not test for empty"
    first_run = text.index("run l-postgres")
    assert guard.start() < first_run, "JWT key guard runs after containers are already created"
    assert re.search(r'\$\{#CDS_JWT_SECRET_KEY\}"?\s*-lt\s*32', text), \
        "no HS256 key-length floor (app/core/config.py rejects < 32 bytes at boot)"


def test_local_deploy_aborts_on_stale_env_file_without_touching_docker(tmp_path):
    """/tmp/cds-local.env written by an older script version has no JWT key.

    `-e CDS_JWT_SECRET_KEY=""` is not "unset" as far as the app is concerned: pydantic sees an
    empty string that differs from the settings default and signs with an empty secret, so the
    deploy has to refuse before it creates a single container. The copy under test only
    redirects the hardcoded env-file path; the guard itself is byte-identical to the shipped
    script and nothing outside tmp_path is written.
    """
    b = _bash()
    if not b:
        pytest.skip("no bash on this host")
    env_file = tmp_path / "cds-local.env"
    env_file.write_text(
        "POSTGRES_PASSWORD=p\nREDIS_PASSWORD=r\nCLICKHOUSE_PASSWORD=c\n"
        "MINIO_ROOT_PASSWORD=m\nVAULT_TOKEN=v\n"
        "CDS_KMS_SERVICE_TOKEN=k\nCDS_CUBE_AUDIT_SERVICE_TOKEN=a\n"
        "CDS_CUBE_TEMPLATE_SIGNING_KEY=s\nCDS_ADMIN_USER=cdsadmin\nCDS_ADMIN_PASS=x\n",
        encoding="utf-8")
    script = tmp_path / "local_deploy_under_test.sh"
    redirected = _read(LOCAL).replace(
        "ENV_FILE=/tmp/cds-local.env", f"ENV_FILE={env_file}")
    assert redirected != _read(LOCAL), "ENV_FILE anchor moved — retarget this gate"
    script.write_text(redirected, encoding="utf-8")
    stub = tmp_path / "stub"
    stub.mkdir()
    marker = tmp_path / "docker-was-called"
    (stub / "docker").write_text(
        f"#!/bin/sh\nprintf used >> '{marker}'\necho STUB-DOCKER-USED\n", encoding="utf-8")
    (stub / "docker").chmod(0o755)
    r = subprocess.run(
        [b, "-c", f'PATH="{stub}:$PATH" bash {script}'],
        capture_output=True, text=True, timeout=180)
    out = r.stdout + r.stderr
    assert r.returncode != 0, f"deploy succeeded on an env file with no JWT key:\n{out[-400:]}"
    assert "cds_jwt_secret_key" in out.lower(), \
        f"the failure does not name the missing key:\n{out[-300:]}"
    assert not marker.exists(), \
        "local_deploy.sh created containers before checking the JWT key — fail closed means refuse first"


def test_remote_deploy_fail_fast_covers_jwt_key_with_length_floor():
    """A301 refuses an empty key; the HS256 floor keeps the three carriers in parity."""
    text = _read(REMOTE)
    loop = re.search(r"for v in ([^;]+); do", text)
    assert loop, "fail-fast loop not found"
    assert "CDS_JWT_SECRET_KEY" in loop.group(1).split(), \
        "remote lane passes CDS_JWT_SECRET_KEY without a fail-fast check"
    assert re.search(r'\$\{#CDS_JWT_SECRET_KEY\}"?\s*-lt\s*' + str(KEY_LENGTH_FLOOR), text), \
        "no HS256 length floor in the remote lane (a 1-byte key deploys, then crash-loops)"
    assert '-e CDS_JWT_SECRET_KEY="$CDS_JWT_SECRET_KEY"' in text, \
        "the lane must pass the operator key explicitly, never a literal default"
    assert COMPOSE_DEV_KEY not in text and SOURCE_LOCAL_KEY not in text
