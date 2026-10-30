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


def _read(rel: str) -> str:
    """Deploy manifests are UTF-8 with Chinese comments; a bare ``read_text()``
    uses the OS locale codec (GBK on zh-CN Windows) and every gate in this file
    dies with ``UnicodeDecodeError`` before YAML is even parsed."""
    return (ROOT / rel).read_text(encoding="utf-8")

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
    data = yaml.safe_load(_read(rel))
    assert data is not None, f"{rel} parsed to empty"


@pytest.mark.parametrize("rel", K8S_MULTI_DOC)
def test_k8s_manifest_parses(rel):
    docs = list(yaml.safe_load_all(_read(rel)))
    assert docs and all(d is not None for d in docs), f"{rel} has an empty document"


def test_prod_compose_fail_closed_keys_in_place():
    data = yaml.safe_load(_read("docker-compose.prod.yml"))
    env = data["services"]["cds-api"]["environment"]
    for key, value in PROD_FAIL_CLOSED_ENV.items():
        assert env.get(key) == value, f"prod compose {key!r} expected {value!r}, got {env.get(key)!r}"


def test_prod_dev_service_topology_consistent():
    """dev/prod service sets must not drift silently.

    One asymmetry is intentional and pinned by name: dev ships the shared
    ``cds-jupyter`` dev-env server (SS-19), while prod compose deliberately
    carries no such service — without ``CDS_JUPYTER_PROXY_BASE_URL`` the reverse
    proxy has no upstream, so the feature honestly reports "no entry point"
    instead of shipping an always-on shared notebook server. The assertion stays
    closed: any *other* dev-only or prod-only service fails this gate.
    """
    dev_only_services = {"cds-jupyter"}
    prod = yaml.safe_load(_read("docker-compose.prod.yml"))
    dev = yaml.safe_load(_read("docker-compose.yml"))
    assert set(prod["services"]) - set(dev["services"]) == set(), (
        f"prod ships services dev does not have: {set(prod['services']) - set(dev['services'])}"
    )
    assert set(dev["services"]) - set(prod["services"]) == dev_only_services, (
        f"dev-only services drifted: got {set(dev['services']) - set(prod['services'])}, "
        f"expected exactly {dev_only_services}"
    )


def test_prod_compose_has_no_unresolved_env_placeholders():
    """Every ${VAR} in prod compose must be resolvable or fail-closed on use.

    We cannot resolve the secrets at test time (they live in .env), so instead
    we pin that the required placeholders are declared in .env.example — an
    operator diffing .env against .env.example would catch a missing secret.
    """
    env_example = _read(".env.example")
    placeholders = set()
    text = _read("docker-compose.prod.yml")
    for line in text.splitlines():
        for tok in line.split():
            if tok.startswith("${") and tok.endswith("}") or "${" in tok and "}" in tok:
                name = tok.split("${")[1].split("}")[0].split(":")[0]
                if name:
                    placeholders.add(name)
    missing = [p for p in sorted(placeholders) if p not in env_example]
    assert not missing, f"prod compose placeholders not documented in .env.example: {missing}"


# ── 2026-10-30 拍板：janitor 真删主开关默认关（docs/retention.md 白名单章节） ──
def test_janitor_real_delete_master_switch_off_in_every_deploy_copy():
    """CDS_JANITOR_ENABLED must read "false" in all four manifest copies plus the
    two scripted deploy paths (local / e2e-remote mirror compose 1:1).

    Production default-off is the pin: real deletion additionally needs the
    code-constant category whitelist and an explicit dry-run flip — the switch
    here is the first gate, so any copy drifting to "true" silently arms it.
    """
    for rel in ("docker-compose.yml", "docker-compose.prod.yml"):
        env = yaml.safe_load(_read(rel))["services"]["cds-api"]["environment"]
        assert env.get("CDS_JANITOR_ENABLED") == "false", f"{rel} janitor switch drifted"
    values = yaml.safe_load(_read("helm/cds/values.yaml"))
    assert values["api"]["env"].get("CDS_JANITOR_ENABLED") == "false", "helm values janitor switch"
    cm = next(
        d for d in yaml.safe_load_all(_read("k8s/cds-app.yaml"))
        if d and d.get("kind") == "ConfigMap" and d["metadata"]["name"] == "cds-app-env"
    )
    assert cm["data"].get("CDS_JANITOR_ENABLED") == "false", "k8s cds-app-env janitor switch"
    for script in ("deploy/local/local_deploy.sh", "deploy/e2e-remote/remote_deploy.sh"):
        assert '-e CDS_JANITOR_ENABLED="false"' in _read(script), f"{script} janitor switch"


def test_prod_compose_retention_keeps_forever_and_dry_run():
    """审计/存证永久保留 + dry-run 默认不许在 prod compose 里被翻（铁律：
    retention 0 = 永远，export-and-archive only；真删另有代码白名单硬守卫）。"""
    env = yaml.safe_load(_read("docker-compose.prod.yml"))["services"]["cds-api"]["environment"]
    assert env.get("CDS_RETENTION_JANITOR_DRY_RUN") == "true"
    assert env.get("CDS_AUDIT_LOG_RETENTION_DAYS") == "0"
    assert env.get("CDS_MERKLE_LEAF_RETENTION_DAYS") == "0"
    values = yaml.safe_load(_read("helm/cds/values.yaml"))["api"]["env"]
    assert values.get("CDS_RETENTION_JANITOR_DRY_RUN") == "true"
    assert values.get("CDS_AUDIT_LOG_RETENTION_DAYS") == "0"
    assert values.get("CDS_MERKLE_LEAF_RETENTION_DAYS") == "0"


# ─── P6（2026-10-30）部署面：钉版本 / 漂移收敛 / 模板化 secret / CDC 手动 ───

_MINIO_COPIES = [
    "docker-compose.yml",
    "docker-compose.prod.yml",
    "docker-compose.middleware.yml",
    "k8s/minio.yaml",
    "deploy/local/local_deploy.sh",
    "deploy/e2e-remote/remote_deploy.sh",
]


def test_minio_image_pinned_single_release_across_every_copy():
    """minio/minio 全线禁 :latest，且六个直接部署副本 + helm values 同一枚
    RELEASE 标签——升级归属 = 发布流程人工评审、一把同升（AGENTS 旧注记里
    "minio/minio:latest 未钉" 已过时，此门防止回潮）。"""
    tags = set()
    for rel in _MINIO_COPIES:
        text = _read(rel)
        assert "minio/minio:latest" not in text, f"{rel}: unpinned :latest regressed"
        for line in text.splitlines():
            if "minio/minio:" in line:
                tag = line.split("minio/minio:", 1)[1].split()[0].strip("'\"")
                assert tag.startswith("RELEASE."), f"{rel}: non-release tag {tag!r}"
                tags.add(tag)
    values = yaml.safe_load(_read("helm/cds/values.yaml"))["minio"]
    assert values["image"]["repository"] == "minio/minio"
    assert values["image"]["tag"].startswith("RELEASE.")
    tags.add(values["image"]["tag"])
    assert len(tags) == 1, f"minio tag drifted across copies: {sorted(tags)}"


def test_helm_chart_ships_gated_minio_template():
    """helm 补 MinIO 模板（P6）：默认关（存量外置 MinIO 安装不被翻），开启走
    existingSecret 的 MINIO_ROOT_* 两键且不带 optional——缺键 fail-closed。"""
    text = _read("helm/cds/templates/minio.yaml")
    assert text.lstrip().startswith("{{- if .Values.minio.enabled }}")
    assert "{{- end }}" in text
    for key in ("MINIO_ROOT_USER", "MINIO_ROOT_PASSWORD"):
        block = text[text.index(f"key: {key}") - 400: text.index(f"key: {key}")]
        assert "secretKeyRef" in block and "existingSecret" in block, key
        assert "optional" not in block, f"{key}: root creds must not be optional"
    values = yaml.safe_load(_read("helm/cds/values.yaml"))
    assert values["minio"]["enabled"] is False, "默认关：翻存量外置 MinIO 拓扑属显式决策"
    assert "minio" not in values.get("externalComponents", [])
    assert "type: Recreate" in text, "PVC 单例 RWO：RollingUpdate surge pod 永卡 ContainerCreating"


def test_postgres_app_user_single_source_cds():
    """postgres 应用用户单一真值源 = `cds`（P6 漂移收敛）。有效行（剥掉 # 与
    -- 注释）上绝不允许再出现 cds_admin；正方向逐个副本钉 cds。
    （注释里出现 cds_admin 是被允许的——存量卷迁移指令 ALTER USER 需要它。）"""
    effective = []
    for rel in (
        "docker-compose.yml", "docker-compose.prod.yml", "docker-compose.middleware.yml",
        "k8s/postgres.yaml", "k8s/secrets.yaml", "k8s/configmaps.yaml",
        "deploy/local/local_deploy.sh", "deploy/e2e-remote/remote_deploy.sh",
        "helm/cds/values.yaml", "scripts/init-db.sql",
    ):
        for line in _read(rel).splitlines():
            s = line.strip()
            if not s or s.startswith("#") or s.startswith("--"):
                continue
            assert "cds_admin" not in s, f"{rel}: cds_admin drift on effective line: {s}"
            effective.append((rel, s))
    def _has(rel, frag):
        assert any(r == rel and frag in s for r, s in effective), f"{rel}: missing {frag!r}"
    _has("docker-compose.yml", "POSTGRES_USER: cds")
    _has("docker-compose.prod.yml", "POSTGRES_USER: cds")
    _has("docker-compose.middleware.yml", "POSTGRES_USER: cds")
    _has("k8s/postgres.yaml", "value: cds")
    _has("k8s/postgres.yaml", '"pg_isready", "-U", "cds"')
    _has("k8s/configmaps.yaml", "GRANT ALL PRIVILEGES ON DATABASE cds TO cds;")
    _has("deploy/local/local_deploy.sh", "-e POSTGRES_USER=cds")
    _has("deploy/e2e-remote/remote_deploy.sh", "-e POSTGRES_USER=cds")


def test_boot_gate_encryption_keys_wired_via_secret_key_ref():
    """CDS_COLUMN_ENCRYPTION_KEY / CDS_MFA_SECRET_ENCRYPTION_KEY（boot 门位
    强制密钥；任务单口径 "DATA_ENCRYPTION/MFA_ENC" 的真实 Settings 字段名）
    在 k8s 与 helm 都以 secretKeyRef 形状接线——占位 secret 名值不出库。"""
    dep = next(
        d for d in yaml.safe_load_all(_read("k8s/cds-app.yaml"))
        if d and d.get("kind") == "Deployment" and d["metadata"]["name"] == "cds-api"
    )
    env = {e["name"]: e for e in dep["spec"]["template"]["spec"]["containers"][0]["env"]}
    for key in ("CDS_COLUMN_ENCRYPTION_KEY", "CDS_MFA_SECRET_ENCRYPTION_KEY"):
        assert key in env, f"cds-api env missing {key}"
        ref = env[key]["valueFrom"]["secretKeyRef"]
        assert ref["name"] == "cds-secrets" and ref["key"] == key
        assert ref.get("optional") is True, "缺键应落在 boot 拒启而不是 Pod 起不来"
    helm_src = _read("helm/cds/templates/deployment-api.yaml")
    for key in ("CDS_COLUMN_ENCRYPTION_KEY", "CDS_MFA_SECRET_ENCRYPTION_KEY"):
        block = helm_src[helm_src.index(f"- name: {key}"):]
        assert f"key: {key}" in block[:400] and "secretKeyRef" in block[:300]
        assert "optional: true" in block[:400]
    # secrets 模板（k3s 路径）两键占位在位（占位符命名吃去前缀的语义 token）：
    sec = next(d for d in yaml.safe_load_all(_read("k8s/secrets.yaml")) if d)
    for key in ("CDS_COLUMN_ENCRYPTION_KEY", "CDS_MFA_SECRET_ENCRYPTION_KEY"):
        assert sec["stringData"][key] == f"<REPLACE_ME_{key.removeprefix('CDS_')}>"


def test_k8s_secrets_yaml_is_placeholder_template():
    """k8s/secrets.yaml 模板化：键名全保留，凭据值一律 <REPLACE_ME_*>，
    历史入库口令绝不允许回流（git 历史剥离归发布负责人，工作树先交白卷）。"""
    sec = next(d for d in yaml.safe_load_all(_read("k8s/secrets.yaml")) if d)
    data = sec["stringData"]
    # 占位符命名 = 语义 token（不带 CDS_ 前缀）；同一凭据被 infra 与 app 两侧
    # 读的双键（vault token、minio root 对）必须复用同一占位符——替换一次两侧一致。
    expected = {
        "POSTGRES_PASSWORD": "<REPLACE_ME_POSTGRES_PASSWORD>",
        "REDIS_PASSWORD": "<REPLACE_ME_REDIS_PASSWORD>",
        "MINIO_ROOT_USER": "<REPLACE_ME_MINIO_ROOT_USER>",
        "MINIO_ROOT_PASSWORD": "<REPLACE_ME_MINIO_ROOT_PASSWORD>",
        "VAULT_TOKEN": "<REPLACE_ME_VAULT_TOKEN>",
        "CLICKHOUSE_PASSWORD": "<REPLACE_ME_CLICKHOUSE_PASSWORD>",
        "CDS_JWT_SECRET_KEY": "<REPLACE_ME_JWT_SECRET_KEY>",
        "CDS_VAULT_TOKEN": "<REPLACE_ME_VAULT_TOKEN>",
        "CDS_MINIO_ACCESS_KEY": "<REPLACE_ME_MINIO_ROOT_USER>",
        "CDS_MINIO_SECRET_KEY": "<REPLACE_ME_MINIO_ROOT_PASSWORD>",
        "CDS_COLUMN_ENCRYPTION_KEY": "<REPLACE_ME_COLUMN_ENCRYPTION_KEY>",
        "CDS_MFA_SECRET_ENCRYPTION_KEY": "<REPLACE_ME_MFA_SECRET_ENCRYPTION_KEY>",
    }
    for key, want in expected.items():
        assert data[key] == want, f"{key} placeholder drifted"
    assert "<REPLACE_ME_POSTGRES_PASSWORD>" in data["CDS_DATABASE_URL"]
    assert "://cds:" in data["CDS_DATABASE_URL"], "URL 用户也要吃单一真值源 cds"
    assert "<REPLACE_ME_REDIS_PASSWORD>" in data["CDS_REDIS_URL"]
    text = _read("k8s/secrets.yaml")
    for leaked in ("CdsSecurePass2026", "CdsRedis2026", "CdsMinio2026", "CdsCH2026",
                   "cds-vault-root-token", "cds_minio_admin",
                   "cds-jwt-secret-key-2026-production"):
        assert leaked not in text, f"historical dev credential {leaked!r} back in the tree"
    assert "DEV ONLY" in text and "REPLACE_ME" in text


def test_kafka_debezium_intentionally_manual_not_silently_unapplied():
    """apply 集合维持 10（本门不许有人顺手把 kafka/debezium apply 进去）：
    deploy.sh 的有效行不得 apply 两者，但注释必须写明「有意手动」+ 命令；
    k8s/README.md 承载完整说明。"""
    sh = _read("k8s/deploy.sh")
    effective = "\n".join(
        ln for ln in sh.splitlines() if not ln.strip().startswith("#")
    )
    assert "kafka.yaml" not in effective and "debezium.yaml" not in effective, \
        "apply 集合扩容需要先过拍板——当前决定是有利手动：CDC opt-in"
    assert "kafka.yaml" in sh and "debezium.yaml" in sh, "manual-apply 命令必须写在脚本注释里"
    readme = _read("k8s/README.md")
    for frag in ("CDC", "有意手动", "kubectl apply -f k8s/kafka.yaml",
                 "kubectl apply -f k8s/debezium.yaml", "CDS_CDC_KAFKA_BROKERS"):
        assert frag in readme, f"README missing {frag!r}"


def test_egress_access_log_committed_copy_is_masked_sample():
    """logs/cds-egress/access.jsonl 工作树副本已脱敏（P6）：结构保留可作测试
    样例，session_id 全部换成一向性 UUIDv5 掩码（原始真实记录均为 v4），
    真 session 与样本的可链接性被切断。该文件在 .gitignore（logs/）内，
    历史上从未入库——AGENTS「committed dev artifacts」一行已过时，隐私风险
    只在跑过真实 e2e 的本机工作树，本门钉的是「脱敏后不许再有 v4 混入」。

    ts 截止线是必需的：EGRESS_AUDIT_LOG_PATH 的出厂默认就指向这份入库文件，
    套里不覆写路径的测试会在 CI 同 worktree 里继续追加快照记录（conftest
    本 lane 禁改，写方治理不归本门）。判据因此钉「历史样本段」：截止线
    （2026-10-30 脱敏时刻）之前的记录必须全是 v5 掩码；之后的运行期追加
    不属入库样本，不作断言。"""
    import json as _json
    from datetime import datetime, timezone
    from uuid import UUID
    path = ROOT / "logs/cds-egress/access.jsonl"
    if not path.exists():
        # logs/ 在 .gitignore 里：这份样本只活在跑过真实 e2e 的工作树里，
        # 全新 CI checkout 没有该文件——skip-not-fail（入库与否不归本门）。
        pytest.skip("masked sample only exists in worktrees that ran the e2e lane")
    cutoff = datetime(2026, 10, 30, 7, 0, tzinfo=timezone.utc)  # 15:00 +08:00
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    historical, seen = 0, set()
    for ln in lines:
        rec = _json.loads(ln)  # 结构可解析即「结构保留」
        if datetime.fromisoformat(rec["ts"]) >= cutoff:
            continue
        sid = UUID(rec["session_id"])
        assert sid.version == 5, f"unmasked v4 session id leaked: {sid}"
        seen.add(str(sid))
        historical += 1
    assert historical >= 100, "结构保留：样本量不该被顺手裁剪"
    assert len(seen) >= 100, "session 多样性（同 session 多次访问 vs 多 session）要保住"
