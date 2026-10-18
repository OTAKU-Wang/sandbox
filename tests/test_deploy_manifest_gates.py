"""A301/A302/A303 — ops deploy-config gates (七份部署副本 fail-fast/持久化/钉定/弱口令).

Static gates (always run, no docker/cluster needed):
  * middleware/prod compose must not carry committed fallback passwords; every
    secret variable is required-interpolation and present in .env.example;
  * remote_deploy.sh fail-fast whitelist covers every -e secret it passes, and
    the e2e lane runs the production startup posture (CDS_DEBUG default false +
    the three boot-gate fallback switches pinned false) with a real alembic
    migration step (main.py skips create_all in prod posture);
  * local_deploy.sh: env file chmod 600, single CDS_KMS_REQUIRE_ATTESTATION,
    migrations re-run AFTER the app/ hot-sync, no SIGPIPE pipe in bootstrap;
  * k8s manifests: api replicas bounded by its RWO-free volume model, workspace
    persistence volumes present, every container has resources.limits, images
    tagged, PVC-backed singletons use Recreate, probes free of the literal
    `$(REDIS_PASSWORD)` kubelet does not expand;
  * helm chart: boot-gate switches present (prod compose parity), OPA URL not
    hardcoded to a deleted Service, replicas omitted under HPA, workspace PVC
    access mode from values, mounts cover every declared runtime write path;
  * posture parity across the deploy copies (prod compose / helm values /
    remote_deploy / k8s ConfigMap; dev compose & local_deploy are the deliberate
    relaxed twins and are pinned as such).

Functional gates (run through git-bash where available, skip otherwise):
  * remote_deploy.sh FATALs on a missing CDS_COLUMN_ENCRYPTION_KEY even when
    every other var is set;
  * local_deploy.sh FATALs on an unknown argument instead of ignoring it;
  * s1_check.sh exits non-zero (S1_CHECK_FAILED) when nothing is reachable;
  * make_env.sh never writes empty secrets (skipped when openssl is absent).
"""
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
K8S = ROOT / "k8s"
HELM = ROOT / "helm" / "cds"
E2E = ROOT / "deploy" / "e2e-remote"
LOCAL = ROOT / "deploy" / "local"

SECRET_VARS = [
    "POSTGRES_PASSWORD", "REDIS_PASSWORD", "CLICKHOUSE_PASSWORD",
    "MINIO_ROOT_PASSWORD", "VAULT_TOKEN",
]
COMMITTED_FALLBACK_SECRETS = [
    "CdsSecureP@ss2026", "CdsRedis2026", "CdsMinio2026", "CdsCH2026",
]
# Posture keys that must read identically in every production-shaped copy.
PROD_POSTURE = {
    "CDS_SECCOMP_FALLBACK_ALLOWED": "false",
    "CDS_HSM_SOFTWARE_FALLBACK_ALLOWED": "false",
    "CDS_SM3_FALLBACK_ALLOWED": "false",
    "CDS_FEDERATION_JWT_KEY_REQUIRED": "true",
}


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")


def load_manifests(path: Path):
    return [d for d in yaml.safe_load_all(read(path)) if d]


def bash_exe():
    from shutil import which
    b = which("bash") or which("bash.exe")
    if not b:
        return None
    try:
        subprocess.run([b, "-c", "true"], check=True, capture_output=True, timeout=20)
    except Exception:
        return None
    return b


def posix(p: Path) -> str:
    s = p.as_posix()
    return re.sub(r"^([A-Za-z]):/", lambda m: "/" + m.group(1).lower() + "/", s)


# --------------------------------------------------------------------------- A303
@pytest.mark.parametrize("compose", ["docker-compose.middleware.yml", "docker-compose.prod.yml"])
def test_compose_secret_vars_are_required_interpolation(compose):
    """A303/A283: no committed `${VAR:-fallback}` for secret vars — the fallback
    is a public, committed credential for anyone who misses the .env entry."""
    text = read(ROOT / compose)
    for var in SECRET_VARS + ["MINIO_ROOT_USER"]:
        assert re.search(rf"\$\{{{var}:-", text) is None, f"{compose}: {var} has a fallback default"
        if re.search(rf"\$\{{{var}[:=]", text):
            assert re.search(rf"\$\{{{var}:\?", text), f"{compose}: {var} must use ${{VAR:?...}}"


@pytest.mark.parametrize("compose", ["docker-compose.yml", "docker-compose.prod.yml",
                                     "docker-compose.middleware.yml"])
def test_no_committed_fallback_passwords_in_compose(compose):
    text = read(ROOT / compose)
    for secret in COMMITTED_FALLBACK_SECRETS:
        assert secret not in text, f"{compose} commits fallback credential {secret!r}"


def test_middleware_compose_vars_documented_in_env_example():
    """Required interpolation only closes the loop if .env.example lists the vars."""
    text = read(ROOT / "docker-compose.middleware.yml")
    env_example = read(ROOT / ".env.example")
    used = set(re.findall(r"\$\{([A-Z0-9_]+)(?::-|:\?)", text))
    missing = {v for v in used if f"\n{v}=" not in env_example}
    assert not missing, f"vars not in .env.example: {sorted(missing)}"


# --------------------------------------------------------------------------- A301
def test_remote_deploy_failfast_covers_every_passed_secret():
    """A301: every `-e VAR="${VAR:-}"` the lane passes must be in the fail-fast
    whitelist — an empty column DEK / MFA key boots with the gate bypassed."""
    text = read(E2E / "remote_deploy.sh")
    loop = re.search(r"for v in ([^;]+); do", text)
    assert loop, "fail-fast loop not found"
    fail_fast = {t for t in loop.group(1).split() if t.rstrip("\\").isidentifier()}
    # optional-by-design empty pass-throughs: consumed only when the feature is
    # explicitly enabled (pairing/boot validation happens in the app itself).
    optional_empty = {"CDS_FEDERATION_CERT", "CDS_FEDERATION_CA_BUNDLE",
                      "CDS_FEDERATION_KEY", "CDS_ATTESTATION_ALLOWED_MEASUREMENTS"}
    inline = set(re.findall(r'-e (CDS_[A-Z_]+|VAULT_[A-Z_]+)="\$\{\1:-\}"', text))
    missing = inline - fail_fast - optional_empty
    assert not missing, f"passed with :- but not fail-fast checked: {sorted(missing)}"
    for k in ("CDS_COLUMN_ENCRYPTION_KEY", "CDS_MFA_SECRET_ENCRYPTION_KEY",
              "CDS_JWT_SECRET_KEY", "CDS_KMS_SERVICE_TOKEN"):
        assert k in fail_fast, f"{k} missing from fail-fast whitelist"


def test_remote_deploy_runs_prod_startup_posture():
    """A301: `CDS_DEBUG:-true` bypasses validate_security_config (is_prod is the
    entry to all 15 boot gates). The e2e lane must mirror docker-compose.prod."""
    text = read(E2E / "remote_deploy.sh")
    assert 'CDS_DEBUG:-true' not in text, "CDS_DEBUG defaults true → boot gates bypassed"
    assert re.search(r'-e CDS_DEBUG="\$\{CDS_DEBUG:-false\}"', text), "CDS_DEBUG must default false"
    for key in ("CDS_SECCOMP_FALLBACK_ALLOWED", "CDS_HSM_SOFTWARE_FALLBACK_ALLOWED"):
        assert f"-e {key}=" in text, f"{key} must be passed"
        pinned = f"{key}:-false" + "}"
        assert pinned in text or f"-e {key}=false" in text, \
            f"{key}=false must be pinned (prod compose parity)"
    assert 'CDS_SM3_FALLBACK_ALLOWED:-false' in text, "SM3 fallback must default false"


def test_remote_deploy_migrates_before_api():
    """Prod posture skips create_all (main.py:80) — the lane must run alembic."""
    text = read(E2E / "remote_deploy.sh")
    assert "alembic upgrade head" in text, "prod posture without migrations = empty schema"
    assert text.index("alembic upgrade head") < text.index("run cds-api "), \
        "migration must run before cds-api starts"


def test_remote_deploy_fails_fast_on_missing_column_key(tmp_path):
    b = bash_exe()
    if not b:
        pytest.skip("no bash on this host")
    env = tmp_path / "cds.env"
    lines = {
        "POSTGRES_PASSWORD": "p", "REDIS_PASSWORD": "r", "CLICKHOUSE_PASSWORD": "c",
        "MINIO_ROOT_PASSWORD": "m", "VAULT_TOKEN": "v", "CDS_JWT_SECRET_KEY": "j" * 32,
        "CDS_KMS_SERVICE_TOKEN": "k", "CDS_CUBE_AUDIT_SERVICE_TOKEN": "a",
        "CDS_CUBE_TEMPLATE_SIGNING_KEY": "s", "CDS_MFA_SECRET_ENCRYPTION_KEY": "mf",
    }
    env.write_text("".join(f"{k}={v}\n" for k, v in lines.items()), encoding="utf-8")
    stub = tmp_path / "stub"
    stub.mkdir()
    (stub / "docker").write_text("#!/bin/sh\necho STUB-DOCKER-USED\n", encoding="utf-8")
    r = subprocess.run(
        [b, "-c",
         f'PATH="{posix(stub)}:$PATH" bash {posix(E2E / "remote_deploy.sh")} '
         f'/nonexistent/img.tar.gz {posix(env)}'],
        capture_output=True, text=True, timeout=120)
    out = r.stdout + r.stderr
    assert r.returncode != 0, "deploy must FATAL on a missing column DEK"
    assert "CDS_COLUMN_ENCRYPTION_KEY" in out, f"fail-fast must name the var; got: {out[:400]}"
    assert "STUB-DOCKER-USED" not in out, "fail-fast must happen before any docker call"


# ------------------------------------------------------------------ local_deploy
def test_local_deploy_secret_hygiene_and_ordering():
    text = read(LOCAL / "local_deploy.sh")
    assert re.search(r'chmod 600 "\$ENV_FILE"', text), "secret env file left to caller umask"
    assert text.count("CDS_KMS_REQUIRE_ATTESTATION=false") == 1, \
        "duplicated security switch — one silently overrides the other"
    assert "cat /tmp/l_bootstrap.json | head" not in text, "SIGPIPE under pipefail"
    assert "alembic upgrade head" in text
    assert text.rindex("alembic upgrade head") > text.index("tar xf /tmp/l_app_sync.tar"), \
        "migrations must be (re-)applied after the app/ hot-sync, not only before it"


def test_local_deploy_rejects_unknown_args(tmp_path):
    b = bash_exe()
    if not b:
        pytest.skip("no bash on this host")
    r = subprocess.run(
        [b, "-c", f'bash {posix(LOCAL / "local_deploy.sh")} --definitely-not-an-option'],
        capture_output=True, text=True, timeout=60)
    out = r.stdout + r.stderr
    assert r.returncode != 0, "unknown args are silently ignored"
    assert "unknown argument" in out.lower(), out[:300]


# ------------------------------------------------------------------- e2e-remote
def test_s1_check_fails_when_nothing_is_reachable(tmp_path):
    b = bash_exe()
    if not b:
        pytest.skip("no bash on this host")
    stub = tmp_path / "stub"
    stub.mkdir()
    (stub / "docker").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    r = subprocess.run(
        [b, "-c",
         f'PATH="{posix(stub)}:$PATH" CDS_BASE_URL=http://127.0.0.1:1 '
         f'CDS_FRONTEND_URL=http://127.0.0.1:1 bash {posix(E2E / "s1_check.sh")}'],
        capture_output=True, text=True, timeout=180)
    out = r.stdout + r.stderr
    assert r.returncode != 0, "s1_check must not exit 0 when every check fails"
    assert "S1_CHECK_DONE" not in out, "completion sentinel must be conditional"
    assert "S1_CHECK_FAILED" in out, "failed runs need a distinct sentinel"


@pytest.mark.parametrize("script", ["e2e_business_loop.py", "e2e_sandbox_security.py",
                                    "e2e_tds_scenarios.py", "e2e_node_live_session.py"])
def test_e2e_harness_hygiene(script):
    text = read(E2E / script)
    compile(text, script, "exec")
    assert '"CdsE2e!Admin123"' not in text, \
        "committed default admin password — the env var must be required"
    assert 'os.environ.get("CDS_E2E_ADMIN_PASS", ' not in text, \
        "CDS_E2E_ADMIN_PASS must fail fast, not fall back"
    if re.search(r"json\.dump\(", text):
        assert re.search(r'open\([^)]*"w"[^)]*encoding="utf-8"', text), \
            "evidence files must be UTF-8 regardless of container locale"


def test_sandbox_security_node_parsing_is_crash_free():
    """A301-批 claims: the nodes parser raised AttributeError exactly on the
    API-error branches that must produce FAIL evidence."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "e2e_sandbox_security", E2E / "e2e_sandbox_security.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    f = getattr(mod, "_as_items", None)
    assert f is not None, "e2e_sandbox_security.py must expose a pure _as_items(payload)"
    for payload in (None, {}, {"nodes": None}, {"nodes": {"items": [1, "x"]}},
                    [], ["plain-string"], {"items": [{"id": 1}]}):
        f(payload)  # must not raise for any shape the gateway can return


def test_make_env_never_writes_empty_secrets(tmp_path):
    b = bash_exe()
    if not b:
        pytest.skip("no bash on this host")
    from shutil import which
    if not which("openssl"):
        pytest.skip("openssl not available")
    out = tmp_path / "cds.env"
    r = subprocess.run([b, "-c", f'bash {posix(E2E / "make_env.sh")} {posix(out)} false false'],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    vals = dict(line.split("=", 1) for line in read(out).splitlines() if "=" in line)
    empty = [k for k, v in vals.items()
             if k.endswith(("_PASSWORD", "_TOKEN", "_KEY"))
             and vals.get("CDS_AUTH_MFA_ENABLED") == "true" and not v
             and k in ("POSTGRES_PASSWORD", "REDIS_PASSWORD", "CLICKHOUSE_PASSWORD",
                       "MINIO_ROOT_PASSWORD", "VAULT_TOKEN", "CDS_JWT_SECRET_KEY",
                       "CDS_MFA_SECRET_ENCRYPTION_KEY", "CDS_COLUMN_ENCRYPTION_KEY")]
    assert not empty, f"make_env wrote empty secrets: {empty}"


# ------------------------------------------------------------------------ k8s
def test_k8s_cds_app_volumes_and_replicas():
    """A302: the api Deployment had ZERO volumes and replicas: 2."""
    docs = load_manifests(K8S / "cds-app.yaml")
    dep = next(d for d in docs if d.get("kind") == "Deployment"
               and d["metadata"]["name"] == "cds-api")
    spec = dep["spec"]["template"]["spec"]
    assert dep["spec"]["replicas"] == 1, \
        "workspace is per-pod writable layer on single-node local-path; 2 replicas each wrote their own"
    mounts = {m["mountPath"] for c in spec["containers"] for m in c.get("volumeMounts", [])}
    for path in ("/tmp/cds-sandbox", "/tmp/cds-sandbox-l0", "/tmp/cds-storage",
                 "/data/egress-audit", "/data/shared-volumes"):
        assert path in mounts, f"runtime write path {path} is not on a volume"
    vols = {v["name"] for v in spec.get("volumes", [])}
    assert len(vols) >= 5


def test_k8s_cds_app_boot_posture_and_migrations():
    """Prod posture (no CDS_DEBUG=true anywhere) skips create_all and RAISES on
    the fallback defaults — the manifest must carry the posture + a migration Job."""
    docs = load_manifests(K8S / "cds-app.yaml")
    cm = next((d for d in docs if d.get("kind") == "ConfigMap"
               and d["metadata"]["name"] == "cds-app-env"), None)
    assert cm, "cds-app.yaml must ship a cds-app-env posture ConfigMap"
    for key, want in PROD_POSTURE.items():
        assert cm["data"].get(key) == want, f"cds-app-env {key} must be {want}"
    job = next((d for d in docs if d.get("kind") == "Job"), None)
    assert job and any("alembic" in part for part in
                       str(job["spec"]["template"]["spec"]["containers"][0].get("command", ""))), \
        "prod posture needs an alembic migration Job (main.py skips create_all)"
    dep = next(d for d in docs if d.get("kind") == "Deployment"
               and d["metadata"]["name"] == "cds-api")
    env_from = dep["spec"]["template"]["spec"]["containers"][0].get("envFrom", [])
    assert any(e.get("configMapRef", {}).get("name") == "cds-app-env" for e in env_from)


def test_k8s_stateful_singletons_use_recreate():
    """RWO + local-path + default RollingUpdate strands the surge Pod in
    ContainerCreating forever (multi-attach / node affinity)."""
    for f in sorted(K8S.glob("*.yaml")):
        for d in load_manifests(f):
            if d.get("kind") != "Deployment":
                continue
            spec = d.get("spec", {}).get("template", {}).get("spec", {})
            has_pvc = any("persistentVolumeClaim" in v for v in spec.get("volumes", []))
            if has_pvc and d["spec"].get("replicas", 1) <= 1:
                strategy = d["spec"].get("strategy", {}).get("type")
                assert strategy == "Recreate", \
                    f"{f.name}/{d['metadata']['name']}: PVC-backed singleton needs Recreate"


def test_k8s_probes_and_heap():
    redis = read(K8S / "redis.yaml")
    probe_zone = redis[redis.index("readinessProbe"):]
    assert "$(REDIS_PASSWORD)" not in probe_zone, \
        "kubelet does not expand $(VAR) in exec probes — literal password was authed"
    ch = read(K8S / "clickhouse.yaml")
    assert "/ping" in ch, "clickhouse probes must not depend on client credentials"
    minio = read(K8S / "minio.yaml")
    assert "/minio/health/live" in minio, "mc-based probe assumes a binary the server image may lack"
    vault = read(K8S / "vault.yaml")
    assert "/v1/sys/health" in vault, "vault status exit 2 on sealed = readiness deadlock"
    kafka = read(K8S / "kafka.yaml")
    assert "KAFKA_HEAP_OPTS" in kafka, "default JVM heap exceeds the 1Gi limit → OOMKill loop"
    assert "NodePort" not in kafka and "30092" not in kafka, \
        "advertised PLAINTEXT kafka:9092 makes the NodePort unconnectable AND unauthenticated"


def test_k8s_images_tagged_and_limits_present():
    for f in sorted(K8S.glob("*.yaml")):
        for d in load_manifests(f):
            kind = d.get("kind")
            if kind not in ("Deployment", "Job"):
                continue
            containers = d["spec"]["template"]["spec"]["containers"]
            for c in containers:
                image = c["image"]
                assert ":" in image.split("/")[-1], f"{f.name}/{c['name']}: untagged image"
                if image.endswith(":latest"):
                    assert c.get("imagePullPolicy") == "Never", \
                        f"{f.name}/{c['name']}: :latest must be node-local (Never), not mutable-pull"
                if kind == "Deployment":
                    limits = c.get("resources", {}).get("limits", {})
                    assert "memory" in limits and "cpu" in limits, f"{f.name}/{c['name']} lacks limits"
                    assert c.get("readinessProbe"), f"{f.name}/{c['name']} lacks readinessProbe"


def test_k8s_configmaps_clickhouse_ddl_matches_canonical():
    """scripts/clickhouse-init.sql is the canonical audit schema (AGENTS.md:
    'Change both copies') — lock the k8s ConfigMap copy against drift."""
    canonical = read(ROOT / "scripts" / "clickhouse-init.sql").strip()
    docs = load_manifests(K8S / "configmaps.yaml")
    cm = next(d for d in docs if d["metadata"]["name"] == "clickhouse-init-sql")
    embedded = cm["data"]["init.sql"].strip()
    assert embedded == canonical, "k8s clickhouse DDL drifted from scripts/clickhouse-init.sql"


def test_k8s_secrets_yaml_stays_dev_only_with_rotation_note():
    text = read(K8S / "secrets.yaml")
    assert "DEV ONLY" in text
    assert re.search(r"(?i)rotat", text), "committed dev credentials need an explicit rotation note"
    assert "openssl rand" in text, "rotation note must name the regeneration command"


def test_k8s_registries_no_dead_index_docker_io():
    reg = yaml.safe_load(read(K8S / "registries.yaml"))
    assert "index.docker.io" not in reg["mirrors"], \
        "k3s normalizes to docker.io; the index.docker.io block is dead config"
    assert "docker.io" in reg["mirrors"]


# ----------------------------------------------------------------------- helm
def test_helm_values_carries_boot_gate_switches():
    """values.yaml sets CDS_DEBUG=false → validate_security_config runs; without
    the two fallback switches every default install CrashLoops (claim: critical)."""
    values = yaml.safe_load(read(HELM / "values.yaml"))
    env = values["api"]["env"]
    assert env["CDS_DEBUG"] == "false"
    assert env["CDS_SECCOMP_FALLBACK_ALLOWED"] == "false"
    assert env["CDS_HSM_SOFTWARE_FALLBACK_ALLOWED"] == "false"
    assert env["CDS_SM3_FALLBACK_ALLOWED"] == "false"
    assert "LOG_LEVEL" not in env, "dangling key — Settings has no LOG_LEVEL field"


def test_helm_api_deployment_replicas_omitted_under_hpa():
    text = read(HELM / "templates" / "deployment-api.yaml")
    m = re.search(r"^(replicas:.*\n|{{- if not .Values.autoscaling.enabled }}\nreplicas:)",
                  text, re.M)
    assert m is None or "autoscaling.enabled" in text[:m.end()], \
        "replicas must be omitted when the HPA owns scaling"
    assert "checksum/config" in text and "checksum/secret" in text, \
        "config/secret changes must roll the pods"


def test_helm_opa_url_not_hardcoded_to_missing_service():
    text = read(HELM / "templates" / "deployment-api.yaml")
    assert 'CDS_OPA_URL="http://{{ include "cds.fullname" . }}-opa' not in text, \
        "chart ships no -opa Service; the hardcoded URL is guaranteed NXDOMAIN"
    assert ".Values.opa.url" in text


def test_helm_workspace_pvc_and_mounts_cover_declared_paths():
    values = yaml.safe_load(read(HELM / "values.yaml"))
    modes = values["sandbox"]["accessModes"]
    assert modes == ["ReadWriteMany"], \
        "replicas>1 + topologySpread + RWO = Multi-Attach stall; shared workspace needs RWX"
    dep = read(HELM / "templates" / "deployment-api.yaml")
    for path in ("/tmp/cds-sandbox", "/tmp/cds-storage", "/data/egress-audit",
                 "/data/shared-volumes"):
        assert path in dep, f"A302: declared runtime write path {path} is not mounted"
    pvc = read(HELM / "templates" / "pvc.yaml")
    assert ".Values.sandbox.accessModes" in pvc
    assert 'storageClassName: {{ .Values.sandbox.storageClass | quote }}' in pvc


def test_helm_templates_conditionals_balanced():
    for template in (HELM / "templates").glob("*.yaml"):
        text = read(template)
        opens = text.count("{{- if") + text.count("{{- range") + text.count("{{- with")
        closes = text.count("{{- end")
        assert opens == closes, template.name


def test_helm_pdb_guarded():
    text = read(HELM / "templates" / "pdb.yaml")
    assert "if .Values.podDisruptionBudget" in text, \
        "an overlay deleting podDisruptionBudget must not nil-pointer the whole release"


# --------------------------------------------------------- seven-copy posture
@pytest.mark.parametrize("key,want", sorted(PROD_POSTURE.items()))
def test_prod_posture_synced_across_copies(key, want):
    prod = read(ROOT / "docker-compose.prod.yml")
    assert re.search(rf'{key}: "{want}"', prod), f"prod compose {key} != {want}"
    values = yaml.safe_load(read(HELM / "values.yaml"))
    assert values["api"]["env"].get(key) == want, f"helm values {key}"
    remote = read(E2E / "remote_deploy.sh")
    pinned = f"{key}:-{want}" + '}"'
    ok = (pinned in remote) or (f"-e {key}={want} " in remote) \
        or (f"-e {key}={want}\n" in remote)
    assert ok, f"remote_deploy.sh does not pin {key}={want}"
    cm = next(d for d in load_manifests(K8S / "cds-app.yaml")
              if d.get("kind") == "ConfigMap" and d["metadata"]["name"] == "cds-app-env")
    assert cm["data"].get(key) == want, f"k8s cds-app-env {key}"


def test_dev_twins_keep_the_relaxed_posture_pinned():
    """Root dev compose and deploy/local are the documented relaxed twins — this
    test locks WHY they are not posture-synced (they must stay DEBUG=true +
    SM3 fallback true, and local_deploy must say dev out loud)."""
    dev = yaml.safe_load(read(ROOT / "docker-compose.yml"))
    env = dev["services"]["cds-api"]["environment"]
    assert env["CDS_DEBUG"] == "true"
    assert env["CDS_SM3_FALLBACK_ALLOWED"] == "true"
    local = read(LOCAL / "local_deploy.sh")
    assert "dev-only" in local, "local_deploy must keep declaring itself a dev stack"
    assert re.search(r'-e CDS_DEBUG=true', local), "local node track is DEBUG by design"
