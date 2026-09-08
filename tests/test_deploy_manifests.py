"""N1: Deployment manifest gate — parse validity + prod fail-closed posture.

Round 45+ productization plan (specs/sandbox-productization-round3-spec.md N1)
regressed when docker-compose.prod.yml shipped a mis-indented key
(CDS_LOG_JSON) that made the whole production compose unparseable, and no CI
gate existed to catch it. This file makes every deploy manifest parseable and
pins the production fail-closed posture + dev/prod topology so the regression
cannot silently recur.

K8s manifests are multi-document streams (one `---` per resource), so those are
parsed with safe_load_all; everything else is a single document.
"""
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent

# (relative path, multi_document)
SINGLE_DOC_MANIFESTS = [
    "docker-compose.yml",
    "docker-compose.prod.yml",
    "docker-compose.middleware.yml",
    "docker-compose.blockchain.yml",
    "helm/cds/values.yaml",
    "helm/cds/Chart.yaml",
]

K8S_MULTI_DOC = sorted(
    p.relative_to(ROOT).as_posix()
    for p in (ROOT / "k8s").glob("*.yaml")
    if p.name != "deploy.sh"
)

# Production fail-closed keys that must be pinned in docker-compose.prod.yml.
# If any is dropped or flipped, the production posture silently weakens.
PROD_FAIL_CLOSED_ENV = {
    "CDS_DEBUG": "false",
    "CDS_KMS_REQUIRE_ATTESTATION": "true",
    "CDS_SECCOMP_FALLBACK_ALLOWED": "false",
    "CDS_HSM_SOFTWARE_FALLBACK_ALLOWED": "false",
}


@pytest.mark.parametrize("rel", SINGLE_DOC_MANIFESTS)
def test_single_doc_manifest_parses(rel):
    data = yaml.safe_load((ROOT / rel).read_text())
    assert data is not None, f"{rel} parsed to empty"


@pytest.mark.parametrize("rel", K8S_MULTI_DOC)
def test_k8s_manifest_parses(rel):
    docs = list(yaml.safe_load_all((ROOT / rel).read_text()))
    assert docs and all(d is not None for d in docs), f"{rel} has an empty document"


def test_prod_compose_fail_closed_keys_in_place():
    data = yaml.safe_load((ROOT / "docker-compose.prod.yml").read_text())
    env = data["services"]["cds-api"]["environment"]
    for key, value in PROD_FAIL_CLOSED_ENV.items():
        assert env.get(key) == value, f"prod compose {key!r} expected {value!r}, got {env.get(key)!r}"


def test_prod_dev_service_topology_consistent():
    prod = yaml.safe_load((ROOT / "docker-compose.prod.yml").read_text())
    dev = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    assert set(prod["services"]) == set(dev["services"]), (
        f"dev/prod service topology drifted: "
        f"only-prod={set(prod['services']) - set(dev['services'])} "
        f"only-dev={set(dev['services']) - set(prod['services'])}"
    )


def test_prod_compose_has_no_unresolved_env_placeholders():
    """Every ${VAR} in prod compose must be resolvable or fail-closed on use.

    We cannot resolve the secrets at test time (they live in .env), so instead
    we pin that the required placeholders are declared in .env.example — an
    operator diffing .env against .env.example would catch a missing secret.
    """
    env_example = (ROOT / ".env.example").read_text()
    placeholders = set()
    text = (ROOT / "docker-compose.prod.yml").read_text()
    for line in text.splitlines():
        for tok in line.split():
            if tok.startswith("${") and tok.endswith("}") or "${" in tok and "}" in tok:
                name = tok.split("${")[1].split("}")[0].split(":")[0]
                if name:
                    placeholders.add(name)
    missing = [p for p in sorted(placeholders) if p not in env_example]
    assert not missing, f"prod compose placeholders not documented in .env.example: {missing}"
