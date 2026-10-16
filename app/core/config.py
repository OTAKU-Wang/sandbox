import logging
import os
import warnings

from pydantic_settings import BaseSettings
from functools import lru_cache


def is_production(settings: "Settings") -> bool:
    """Shared prod detection (PZ-04): DEBUG off and not a test environment."""
    return (
        not settings.DEBUG
        and os.environ.get("TESTING") != "1"
        and not os.environ.get("PYTEST_CURRENT_TEST")
    )

logger = logging.getLogger(__name__)

_JWT_DEFAULT_KEY = "change-me-in-production"
_JWT_MIN_KEY_LENGTH = 32  # bytes — RFC 7518 Section 3.2 recommends ≥ 256 bits for HS256


class Settings(BaseSettings):
    # App
    APP_NAME: str = "CDS - Confidential Data Sandbox"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = False
    # W4: legacy Jinja admin pages (/admin/*, FE-1~FE-5 leftovers) — the React
    # frontend is the real admin UI. These pages have NO authentication, so the
    # default is off and production enabling is rejected at startup.
    ADMIN_PAGES_ENABLED: bool = False

    # W1: observability — Prometheus metrics + structured logs.
    METRICS_ENABLED: bool = True
    METRICS_API_TOKEN: str | None = None  # set to require bearer auth on /metrics
    LOG_JSON: bool = False

    # W3: distributed rate limiting. Fixed windows are stored in Redis so
    # limits hold across replicas and restarts; Redis outage degrades to the
    # in-process fallback (fail-open — availability over strictness, see
    # docs/error-codes.md).
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_PER_MINUTE_IP: int = 60     # anonymous (IP scope)
    RATE_LIMIT_PER_MINUTE_USER: int = 600  # authenticated (user scope)
    RATE_LIMIT_AUTH_PER_MINUTE: int = 10   # /api/v1/auth/login dedicated bucket

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://cds:cds@localhost:5432/cds"
    DATABASE_ECHO: bool = False

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # JWT
    JWT_SECRET_KEY: str = _JWT_DEFAULT_KEY
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 60 * 24  # 24 hours

    # SM2 (Tongsuo)
    SM2_PRIVATE_KEY_PATH: str = ""
    SM2_PUBLIC_KEY_PATH: str = ""

    # KMS (Vault)
    VAULT_ADDR: str = "http://localhost:8200"
    VAULT_TOKEN: str = ""

    # OPA (Open Policy Agent)
    OPA_URL: str = "http://localhost:8181"

    # MinIO
    MINIO_ENDPOINT: str = "localhost:9000"
    MINIO_ACCESS_KEY: str = "minioadmin"
    MINIO_SECRET_KEY: str = "minioadmin"
    MINIO_BUCKET: str = "cds-data"

    # Sandbox
    SANDBOX_L3_ENABLED: bool = True
    SANDBOX_DEFAULT_TIMEOUT: int = 3600  # 1 hour
    SANDBOX_K8S_NAMESPACE: str = "cds-sandbox"
    SANDBOX_K8S_IMAGE: str = "python:3.12-slim"
    SANDBOX_K8S_IMAGE_PULL_POLICY: str = "IfNotPresent"
    SANDBOX_K8S_RUNTIME_CLASS: str = ""
    SANDBOX_K8S_READY_TIMEOUT_SECONDS: int = 30
    SANDBOX_K8S_POLL_INTERVAL_SECONDS: float = 1.0
    SANDBOX_K8S_KUBECONFIG: str = ""
    SANDBOX_K8S_FQDN_POLICY_PROVIDER: str = ""
    # N7: use the official kubernetes python client instead of shelling out to
    # kubectl. Falls back to kubectl when no cluster/kubeconfig is reachable.
    K8S_USE_PYTHON_CLIENT: bool = True
    # N7: storage class used for shared-volume PVCs (must support
    # ReadWriteMany). Empty = cluster default class; honest error if none.
    SANDBOX_K8S_STORAGE_CLASS: str = ""

    # N10: sandbox-internal agent execution framework — in-sandbox step/token
    # budgets enforced by the generated runner + verified host-side.
    AGENT_STEP_BUDGET: int = 15
    AGENT_TOKEN_BUDGET: int = 20000
    AGENT_MAX_TOOLS: int = 4

    # Session lifecycle (gap A2/D1): background sweep interval for expired
    # sessions, dev sessions and contracts.
    SESSION_CLEANUP_INTERVAL_SECONDS: int = 300

    COMPUTE_SCHEDULER_ENABLED: bool = True
    COMPUTE_SCHEDULER_INTERVAL_SECONDS: int = 60
    WEBHOOK_DISPATCH_ENABLED: bool = True
    WEBHOOK_DISPATCH_INTERVAL_SECONDS: int = 15
    WEBHOOK_ALLOW_PRIVATE_URLS: bool = True
    NOTEBOOK_KERNEL_ENABLED: bool = True
    JUPYTER_PROXY_BASE_URL: str | None = None

    # W9: idle expiry behavior for sessions that do not declare their own
    # idle_policy. "kill" terminates (CDS historical default); "pause"
    # suspends preserving state (CubeSandbox onTimeout=pause semantics).
    SESSION_DEFAULT_IDLE_POLICY: str = "kill"

    # W10: concurrent WebSocket exec streams allowed per session.
    EXEC_STREAM_MAX_CONCURRENT: int = 3

    # W13: egress audit JSONL trail for sandbox network decisions.
    EGRESS_AUDIT_ENABLED: bool = True
    EGRESS_AUDIT_LOG_PATH: str = "./logs/cds-egress/access.jsonl"

    # W14: a node whose heartbeat is older than this is health_state=stale
    # (alert-only detection; no automatic recovery).
    NODE_STALE_SECONDS: int = 300

    # W15: filesystem root backing shared volumes (bind-mounted into L0).
    SHARED_VOLUME_ROOT: str = "./data/shared-volumes"

    # W19: encrypt the egress audit JSONL at rest (SM4-GCM via
    # app/utils/crypto.py; key derived from CDS_AUDIT_ENCRYPTION_KEY).
    EGRESS_AUDIT_ENCRYPTION_ENABLED: bool = False
    AUDIT_ENCRYPTION_KEY: str = ""

    # W12: retention janitor. 0 = never purge (audit logs and merkle leaves
    # default to keep-forever — audit evidence must not self-destruct).
    # DRY_RUN defaults to true: real deletion is an explicit operator turn.
    RETENTION_JANITOR_ENABLED: bool = True
    AUDIT_LOG_RETENTION_DAYS: int = 0
    ALERT_RETENTION_DAYS: int = 180
    MERKLE_LEAF_RETENTION_DAYS: int = 0
    RETENTION_JANITOR_DRY_RUN: bool = True
    RETENTION_JANITOR_BATCH: int = 1000

    # Round 45+ (spec N2): Merkle batch pipeline (Redis Stream worker in
    # app/services/merkle_pipeline.py) is an experimental async batch-anchoring
    # mode, DISABLED by default — the audit path uses the synchronous
    # merkle_service anchoring. Enable only for evaluation; when enabled the
    # pipeline worker starts in the app lifespan and batches audit events into
    # Merkle trees anchored via the blockchain adapter.
    MERKLE_PIPELINE_ENABLED: bool = False

    # Session usability (Round 39): per-session file store and snapshot limits.
    # Uploaded files live inside the session workspace (sandbox-visible) and are
    # encrypted at rest with the session DEK; snapshots are tar archives of the
    # workspace stored outside the sandbox bind (DEK-encrypted content only).
    SESSION_FILE_MAX_BYTES: int = 50 * 1024 * 1024
    SESSION_MAX_FILES: int = 200
    SESSION_MAX_SNAPSHOTS: int = 10
    # Hard cap for total refresh extension per session (POST /{id}/refreshes).
    SESSION_MAX_EXTENDED_SECONDS: int = 7 * 24 * 3600

    # Session usability (Round 40): exec / templates / snapshot GC.
    # exec runs a shell command inside the live sandbox with a short hard cap —
    # it is an interactive tool, not a batch runner (use /execute for that).
    SESSION_EXEC_TIMEOUT_SECONDS: int = 120
    SESSION_EXEC_MAX_OUTPUT_CHARS: int = 100_000
    # Optional JSON override of the built-in session template registry
    # ({"name": {"description": ..., "files": {...}, "env": {...}}}).
    SESSION_TEMPLATES_JSON: str = ""
    # Whether session termination garbage-collects the snapshot archives.
    # Set false to keep "restore after terminate" possible for operators.
    SESSION_SNAPSHOT_GC_ON_TERMINATE: bool = True

    # KMS (gap B1): fail-closed — reject session key distribution without a
    # TEE attestation. Software-only sandbox levels (L0/L3) produce no quote,
    # so deployments relying on them must explicitly disable this.
    KMS_REQUIRE_ATTESTATION: bool = True

    # Dev sandbox (gap A3): require a valid contract covering the attached
    # data product before a dev session may load provider data.
    DEV_SANDBOX_REQUIRE_CONTRACT: bool = True

    # Gap B2/D4/B5 + F2 (T12): simulation / fallback fail-closed switches.
    # Production deployments must set these to false and prove the real
    # capability; software-only pilots keep them true explicitly. The startup
    # validation (validate_security_config) surfaces every enabled fallback so
    # degradation is never silent.
    ALLOW_SIMULATION: bool = True       # accept software-simulated TEE attestation quotes
    SECCOMP_FALLBACK_ALLOWED: bool = True  # retry sandbox exec without seccomp on EINVAL
    # D-e2e-01: admin self-registration escape hatch — test/dev bootstrap only;
    # validate_security_config refuses it in production (first-account rule).
    ALLOW_ADMIN_SELF_REGISTER: bool = False
    HSM_SOFTWARE_FALLBACK_ALLOWED: bool = True  # fall back to in-memory software KEK/signing
    FEDERATION_JWT_KEY_REQUIRED: bool = True    # refuse static federation JWT key in prod

    # PZ-04/PZ-05 (spec ss-11, MR-A4/A5/A9/A14 + MR-B1): crypto & federation
    # honesty gates. Production deployments MUST tighten these — the startup
    # validation (validate_security_config) raises when a weak default would
    # silently run in prod (SS-11 §7.1 config matrix).
    MIA_SHADOW_MODEL: bool = False  # real shadow-model MIA evaluation (advisory exposure)
    COLUMN_ENCRYPTION_KEY: str = ""  # 16-byte column DEK (hex or base64); empty = dev default
    SM3_FALLBACK_ALLOWED: bool = True  # fall back to SHA-256 when gmssl is missing (prod: raise)
    FEDERATION_ENABLED: bool = True  # master switch for the federation gateway channel
    FEDERATION_CA_BUNDLE: str = ""  # path to CA bundle PEM for federation mTLS
    FEDERATION_CERT: str = ""  # path to our federation client cert (PEM)
    FEDERATION_KEY: str = ""  # path to our federation client key (PEM)
    ATTESTATION_ALLOWED_MEASUREMENTS: str = ""  # csv measurement whitelist (prod: required)

    # PZ-10 (spec ss-11): auth enhancement — MFA (TOTP) + SM2 cert login
    # second factor. MFA_SECRET_ENCRYPTION_KEY is the master secret for the
    # SM4-GCM at-rest encryption of TOTP secrets / recovery codes; empty
    # falls back to the JWT secret in dev/test ONLY — production refuses to
    # boot without an explicit key (validate_security_config).
    AUTH_MFA_ENABLED: bool = True  # enforce TOTP at login for confirmed users
    MFA_TOTP_PERIOD: int = 30  # RFC 6238 time step (seconds)
    MFA_TOTP_DIGITS: int = 6
    MFA_RECOVERY_CODE_COUNT: int = 8
    MFA_SECRET_ENCRYPTION_KEY: str = ""
    AUTH_CERT_LOGIN: bool = True  # POST /auth/login/cert second-factor track

    # TEE runtime
    # Hardware TEE is an OPTIONAL opt-in capability, NOT a deployment
    # requirement: with no hardware hooks configured (the default), L1 runs
    # as an ordinary software-confidential sandbox with honest labeling
    # (tee_mode="software_confidential", hardware_available=False) and the
    # product is fully functional without TEE hardware.
    # TEE_MODE:
    #   auto: use hardware only when detected AND the operator has explicitly
    #         configured the command hooks below; otherwise software path.
    #   hardware: demand the hardware path (fails closed without it) — an
    #         explicit operator choice for TEE-guaranteed deployments.
    TEE_MODE: str = "auto"
    TEE_ALLOW_SOFTWARE_FALLBACK: bool = True
    TEE_HARDWARE_PROVISION_CMD: str = ""
    TEE_HARDWARE_EXEC_CMD: str = ""
    TEE_HARDWARE_ATTEST_CMD: str = ""
    TEE_HARDWARE_TERMINATE_CMD: str = ""

    # (PZ-04/MR-A6: TEE_SIMULATION_MODE removed — dead config, never read by
    # the runtime; TEE_MODE / TEE_ALLOW_SOFTWARE_FALLBACK are authoritative.)
    GPU_TEE_SIMULATION: bool = True

    # CORS
    CORS_ALLOWED_ORIGINS: list[str] = ["http://localhost:5173", "http://localhost:3000", "http://localhost:3001", "http://localhost:3002"]

    # Audit
    CLICKHOUSE_URL: str = "localhost:9000"

    # Alert center
    ALERT_WEBHOOK_URLS: list[str] = []
    ALERT_NOTIFICATION_TIMEOUT_SECONDS: float = 3.0
    ALERT_NOTIFICATION_RETRIES: int = 1

    # CDC Kafka
    CDC_KAFKA_BROKERS: str = "localhost:9092"
    CDC_KAFKA_TOPIC_PREFIX: str = "cds.events"
    CDC_KAFKA_CONNECT_URL: str = ""
    CDC_KAFKA_CONNECT_TIMEOUT_SECONDS: float = 10.0

    # PII NER ML
    # ner_engine selects the Layer-2 NER engine (T9):
    #   auto (default) / rule / lac / transformers / regex(off).
    # Results always report the engine that actually ran (honest labeling).
    PII_NER_ENGINE: str = "auto"
    PII_NER_USE_ML: bool = False
    PII_NER_MODEL_NAME: str = "bert-base-chinese-pii-ner"
    PII_NER_CONFIDENCE_THRESHOLD: float = 0.5

    # Training (gap C1/T10): fail-closed — reject training jobs when torch is
    # not installed instead of silently returning simulated results.
    TRAINING_REQUIRE_TORCH: bool = True

    # Gap E3: DP budget guardrails. Per-consumption epsilon below the floor is
    # rejected (prevents no-op/negative consumption); above the ceiling is
    # rejected (prevents a single query burning the whole budget). Allocation
    # is capped at DP_MAX_EPSILON_ALLOCATION when set.
    DP_MIN_EPSILON_CONSUMPTION: float = 0.0
    DP_MAX_EPSILON_PER_CONSUMPTION: float | None = None
    DP_MAX_EPSILON_ALLOCATION: float | None = None

    # RAG (gap T11, phase 1): in-domain retrieval-QA. Corpus never leaves the
    # sandbox; outputs always go through the contract output gateway. The
    # embedding engine is reported honestly on every result (auto probes for a
    # transformers backend, otherwise self-implemented char n-gram TF).
    RAG_DEFAULT_TOP_K: int = 5
    RAG_CHUNK_SIZE: int = 256
    RAG_CHUNK_OVERLAP: int = 32
    RAG_MAX_CORPUS_BYTES: int = 10 * 1024 * 1024
    RAG_MAX_DOCS: int = 200
    RAG_QUERY_MAX_CHARS: int = 2000
    RAG_EMBEDDING_BACKEND: str = "auto"  # auto / tf / transformers / regex
    RAG_EMBEDDING_MODEL: str = "shibing624/text2vec-base-chinese"
    RAG_REQUIRE_ENCRYPTION: bool = True

    # ── Cube data plane (SS-10 L2' / CB-01) ───────────────────────────────
    # L2' (Cube) node data-plane switch. Off by default. Enabling in prod
    # requires real node mTLS material + a results dir (validate_security_config
    # RAISEs when unwired). While off, or when no eligible node exists, l2c
    # create requests are honestly rejected with 400 RUNTIME_UNAVAILABLE
    # (honest degradation — never a silent fallback to another level).
    CUBE_DATAPLANE_ENABLED: bool = False
    # Legacy L2 (firecracker) / k8s adapters — deprecated in the CB-01
    # transition. Off by default; keep true to retain pre-SS-10 L2/k8s paths.
    LEGACY_L2_ENABLED: bool = False
    # SS-13 single-path cutover: gate for ALL legacy sandbox levels
    # (L0/L1/L2/L3/k8s). Off by default; production enabling is REJECTED at
    # startup (validate_security_config) — production is l2c (Cube MicroVM)
    # only. dev/test opt in via CDS_SANDBOX_LEGACY_ENABLED=true.
    SANDBOX_LEGACY_ENABLED: bool = False
    # Node TLS front-proxy sidecar: CDS dials node:PORT with mTLS. No silent
    # plaintext fallback — CUBE_NODE_GRPC_TLS=False is dev-only (grpc.dev).
    # CUBE fleet runtime pin override: the node fleet's actual release commit.
    # Empty (default) = the stub-generation pin from app/_stubs/cube/_VERSION.json.
    CUBE_PINNED_RUNTIME_VERSION: str = ""
    CUBE_NODE_GRPC_TLS: bool = True
    CUBE_NODE_TLS_CA: str | None = None
    CUBE_NODE_TLS_CERT: str = ""  # node-client cert PEM path (mTLS; CB-02 wire)
    CUBE_NODE_TLS_KEY: str = ""  # node-client key PEM path (mTLS; CB-02 wire)
    CUBE_NODE_TLS_PROXY_PORT: int = 9443
    # Per-RPC deadline tiers (ms). Default 5000; Update runs at 30000.
    CUBE_NODE_GRPC_DEADLINE_MS: int = 5000
    # Node health-probe cadence (s); merged into the session cleanup loop.
    CUBE_NODE_HEARTBEAT_SECONDS: int = 30
    # Host-side results output dir (cds-results-<sid> host_dir volume source).
    CUBE_RESULTS_DIR: str = "/var/lib/cds/sandbox-results"
    # Base sandbox image run on the cube node (image sync lands in CB-05).
    CUBE_SANDBOX_IMAGE: str = "cds/cube-sandbox:latest"
    # Per-VM resource sizing for cube sandboxes (string units: "1"/"500m", "1Gi").
    CUBE_SANDBOX_CPU: str = "1"
    CUBE_SANDBOX_MEMORY: str = "1Gi"
    # SS-14 RT-07 (2026-10-14): 镜像存储介质 —— 空 = OCI registry 拉取（默认,
    # cubelet LocalResolve/PullImage）; "ext4" = TC 模板分发的 pmem 路径
    # （imageRef 须 SafeID 无路径分隔符, 见上游 cube_image_pull_route.go 实证）。
    CUBE_SANDBOX_STORAGE_MEDIA: str = ""
    # SS-14 RT-07 (2026-10-14): cube-master 控制面地址（卷生命周期）。
    # 上游实证: 插件卷 (driver=cds-encrypted) 的 CreateVolume 只由 cube-master
    # 卷面 (POST /cube/volume) 触发 (CubeMaster/pkg/volume/plugin); cubelet 直连
    # RunCubeSandbox 不会预建卷 → Attach "volume not found" (fail-closed)。
    # 空 = 未配置 —— provision 拒绝创建 l2c 会话（诚实失败，不静默降级）。
    CUBE_MASTER_ADDR: str = ""
    # SS-14 RT-07 (2026-10-14): 节点卷插件 CDS 扩展 API 的 unix socket 路径
    # （插件 proto 注释: 控制面是唯一授权调用方; 本地单节点部署下 API 与
    # 节点同机, 直接用 unix socket。多节点拓扑需逐节点地址 —— 已知限制,
    # 见 SS-14 文档）。
    CUBE_VOLUME_PLUGIN_SOCKET: str = ""
    # CDS_DEK length (bytes) injected into the sandbox env; SM4 = 16.
    CUBE_DEK_LENGTH: int = 16
    # CB-04: CubeEgress root CA artifact (PEM) on the CDS build/probe host —
    # the CDS-side copy of the node bring-up output /etc/cube/ca/cube-root-ca.crt.
    # Consumed by app.services.cube_egress.check_cube_egress_ca_baked (CB-05
    # template probe + scripts/build-cube-sandbox-image.sh); empty → the
    # node-path constant is the fallback and a missing CA fails the probe.
    CUBE_EGRESS_CA: str | None = None
    # Machine-to-machine service token for the cube egress audit ingestion
    # endpoint (POST /internal/audit/egress). The node audit tailer
    # authenticates with `Authorization: Bearer <CDS_CUBE_AUDIT_SERVICE_TOKEN>`
    # over the mTLS transport; fail-closed: empty/None → 401 on every request.
    CUBE_AUDIT_SERVICE_TOKEN: str | None = None
    # CB-05: HMAC key signing the per-node artifact download token
    # (cube.master.rootfs.artifact.token) + presigned-URL TTL enforcement.
    # Production requires it (validate_security_config RAISEs when unset).
    CUBE_TEMPLATE_SIGNING_KEY: str | None = None
    # CB-05: presigned artifact URL TTL (seconds) per distribution attempt.
    CUBE_TEMPLATE_URL_TTL_SECONDS: int = 3600
    # CB-05: CreateImage+verify attempts per node (re-issues URL+token each
    # attempt — covers presigned-URL TTL expiry mid-distribution).
    CUBE_TEMPLATE_DIST_RETRY: int = 2
    # Machine-to-machine service token for the KMS volume-key endpoints
    # (POST/DELETE /api/v1/kms/volume-keys*). The Go volume plugin authenticates
    # with `Authorization: Bearer <CDS_VOLUME_PLUGIN_KMS_SERVICE_TOKEN>`; this
    # control-plane value MUST match that plugin credential. Fail-closed: when
    # empty/None the volume-key endpoints reject every request with 401.
    KMS_SERVICE_TOKEN: str | None = None

    # Round 45+ (spec N2): Merkle batch pipeline (Redis Stream worker in
    # app/services/merkle_pipeline.py) is an experimental async batch-anchoring
    # mode, DISABLED by default — the audit path uses the synchronous
    # merkle_service anchoring. Enable only for evaluation; when enabled the
    # pipeline worker starts in the app lifespan and batches audit events into
    # Merkle trees anchored via the blockchain adapter.
    MERKLE_PIPELINE_ENABLED: bool = False

    model_config = {"env_prefix": "CDS_", "env_file": ".env"}

    def validate_jwt_security(self) -> list[str]:
        """Validate JWT configuration for security issues.

        Returns:
            List of warning/error messages. Empty = secure.
        """
        issues = []
        if self.JWT_SECRET_KEY == _JWT_DEFAULT_KEY:
            if not self.DEBUG:
                raise ValueError(
                    "JWT_SECRET_KEY uses default value — set a unique secret via CDS_JWT_SECRET_KEY env var. "
                    "This is a security requirement for production deployments."
                )
            else:
                issues.append("WARN: JWT_SECRET_KEY uses default value (acceptable in DEBUG mode only)")
        elif len(self.JWT_SECRET_KEY.encode()) < _JWT_MIN_KEY_LENGTH:
            raise ValueError(
                f"JWT_SECRET_KEY must be at least {_JWT_MIN_KEY_LENGTH} bytes (got {len(self.JWT_SECRET_KEY.encode())}). "
                "RFC 7518 Section 3.2 recommends ≥ 256 bits for HS256."
            )
        return issues

    def validate_security_config(self) -> list[str]:
        """Validate the full security-relevant configuration (gap T12).

        Extends ``validate_jwt_security`` with the simulation/fallback switch
        matrix so that no crypto/isolating degradation is ever silent in
        production. Returns a list of warning strings (logged by the app
        lifespan); raises ValueError on configurations that are unsafe to run
        as-is (default JWT secret, or a required-hardware claim with no
        backend).
        """
        issues = self.validate_jwt_security()
        is_prod = not self.DEBUG and os.environ.get("TESTING") != "1" and not os.environ.get("PYTEST_CURRENT_TEST")
        if not is_prod:
            return issues

        if self.ALLOW_SIMULATION:
            issues.append("WARN: ALLOW_SIMULATION=true — software-simulated TEE attestation quotes are accepted in production")
        if self.WEBHOOK_ALLOW_PRIVATE_URLS:
            issues.append("WARN: WEBHOOK_ALLOW_PRIVATE_URLS=true — 结果推送回调可指向内网/保留地址（SSRF 面），生产应收敛为 false")
        # Pure-degradation switches have NO hardware dependency and MUST fail
        # closed in production — a deployment silently retrying without seccomp,
        # or using an in-memory software KEK, is a hard security failure, not a
        # warn-worthy degradation. Raise so misconfiguration aborts startup.
        if self.SECCOMP_FALLBACK_ALLOWED:
            raise ValueError(
                "SECCOMP_FALLBACK_ALLOWED=true is not permitted in production: "
                "sandbox exec must never retry without seccomp. Set "
                "CDS_SECCOMP_FALLBACK_ALLOWED=false (requires a seccomp-capable kernel)."
            )
        if self.HSM_SOFTWARE_FALLBACK_ALLOWED:
            raise ValueError(
                "HSM_SOFTWARE_FALLBACK_ALLOWED=true is not permitted in production: "
                "an in-memory software KEK/signing fallback has degraded crypto "
                "strength. Set CDS_HSM_SOFTWARE_FALLBACK_ALLOWED=false and back "
                "key management with a real HSM/Vault."
            )
        if self.ALLOW_ADMIN_SELF_REGISTER:
            raise ValueError(
                "ALLOW_ADMIN_SELF_REGISTER=true is not permitted in production: "
                "any account could self-register as admin (D-e2e-01). Admin is "
                "only self-selectable for the first (bootstrap) account; create "
                "further admins via admin-assigned role change."
            )
        if not self.FEDERATION_JWT_KEY_REQUIRED:
            raise ValueError(
                "FEDERATION_JWT_KEY_REQUIRED=false is not permitted in production: "
                "a static federation JWT signing key is a hard security failure. "
                "Set CDS_FEDERATION_JWT_KEY_REQUIRED=true and configure a "
                "per-deployment federation JWT key."
            )

        # PZ-04 (MR-A14): the SM3→SHA-256 fallback must never be ARMED in
        # production — national-crypto paths must run real SM3 or refuse to boot.
        if self.SM3_FALLBACK_ALLOWED:
            raise ValueError(
                "SM3_FALLBACK_ALLOWED=true is not permitted in production: the "
                "SHA-256 fallback for SM3 (gmssl missing) must never be armed "
                "in a GM/T deployment. Install gmssl and set "
                "CDS_SM3_FALLBACK_ALLOWED=false."
            )

        # PZ-04 (MR-A9): column encryption must use an operator-supplied DEK in
        # production, never the built-in development default key.
        if not self.COLUMN_ENCRYPTION_KEY:
            raise ValueError(
                "COLUMN_ENCRYPTION_KEY is required in production: the built-in "
                "default column DEK must never encrypt production data. Set "
                "CDS_COLUMN_ENCRYPTION_KEY to a 16-byte key (hex or base64)."
            )

        # PZ-10: TOTP secrets / recovery codes must never be encrypted at rest
        # with the dev fallback (JWT secret) in production.
        if not self.MFA_SECRET_ENCRYPTION_KEY:
            raise ValueError(
                "MFA_SECRET_ENCRYPTION_KEY is required in production: the dev "
                "fallback (JWT secret) must never encrypt production MFA "
                "secrets. Set CDS_MFA_SECRET_ENCRYPTION_KEY to a long random "
                "master secret."
            )

        # PZ-05 (MR-A8): the federation channel is mTLS-terminated; without
        # cert/key/CA material it must refuse to run in production (fail-closed).
        if self.FEDERATION_ENABLED and not (
            self.FEDERATION_CA_BUNDLE and self.FEDERATION_CERT and self.FEDERATION_KEY
        ):
            raise ValueError(
                "Federation is enabled in production but mTLS material is "
                "missing: set CDS_FEDERATION_CA_BUNDLE, CDS_FEDERATION_CERT and "
                "CDS_FEDERATION_KEY to real certificate/key/CA-bundle paths, or "
                "disable the channel with CDS_FEDERATION_ENABLED=false."
            )

        # SS-10 CB-01: cube (L2') data plane. This is a config-only check (no
        # DB); the ">=1 eligible node" requirement is a runtime check enforced
        # at the l2c create gate as 400 RUNTIME_UNAVAILABLE.
        if self.CUBE_DATAPLANE_ENABLED:
            if self.CUBE_NODE_GRPC_TLS and not self.CUBE_NODE_TLS_CA:
                raise ValueError(
                    "CDS_CUBE_DATAPLANE_ENABLED=true in production requires node "
                    "mTLS material (CDS_CUBE_NODE_TLS_CA) for the node TLS front-"
                    "proxy; refusing to start with plaintext node gRPC (no silent "
                    "plaintext fallback)"
                )
            if not self.CUBE_RESULTS_DIR:
                raise ValueError(
                    "CDS_CUBE_DATAPLANE_ENABLED=true in production requires "
                    "CDS_CUBE_RESULTS_DIR to be set (host-side results dir)"
                )
            # CB-05: the artifact capability token (cube.master.rootfs.artifact
            # .token) must be signed; unsigned dev tokens never run in prod.
            if not self.CUBE_TEMPLATE_SIGNING_KEY:
                raise ValueError(
                    "CDS_CUBE_DATAPLANE_ENABLED=true in production requires "
                    "CDS_CUBE_TEMPLATE_SIGNING_KEY (CB-05 artifact token "
                    "signing for the node distribution path)"
                )

        # SS-13 single-path cutover: legacy sandbox levels are retired from
        # production. A production deployment must not silently keep the
        # L0/L1/L2/L3/k8s adapters alive — l2c (Cube MicroVM) is the only
        # production session level.
        if self.SANDBOX_LEGACY_ENABLED:
            raise ValueError(
                "CDS_SANDBOX_LEGACY_ENABLED=true is not permitted in production "
                "(SS-13 single-path cutover): the legacy sandbox levels "
                "(L0/L1/L2/L3/k8s) are retired and production sessions run on "
                "the Cube MicroVM level (l2c) only. Keep the flag true only in "
                "dev/test, or set CDS_SANDBOX_LEGACY_ENABLED=false."
            )

        # PZ-05 (MR-B1): with TEE attestation required, the measurement
        # whitelist must be explicit — an empty allow-all policy is not a
        # production posture.
        if self.KMS_REQUIRE_ATTESTATION and not self.ATTESTATION_ALLOWED_MEASUREMENTS.strip():
            raise ValueError(
                "ATTESTATION_ALLOWED_MEASUREMENTS is required in production when "
                "KMS_REQUIRE_ATTESTATION=true: an empty measurement whitelist "
                "accepts any TEE. Set CDS_ATTESTATION_ALLOWED_MEASUREMENTS to a "
                "csv of approved SM3 measurements."
            )

        # W4: unauthenticated legacy admin pages must never serve in production.
        if self.ADMIN_PAGES_ENABLED:
            raise ValueError(
                "ADMIN_PAGES_ENABLED=true is not permitted in production: the legacy "
                "/admin/* pages have no authentication. Use the React frontend "
                "(cds-frontend) as the admin UI."
            )

        # Gap T11: embedding backend must be a known engine (auto resolves to
        # a concrete honest engine at runtime; unknown values would fail closed
        # confusingly later).
        _rag_engines = {"auto", "tf", "transformers", "regex"}
        if self.RAG_EMBEDDING_BACKEND not in _rag_engines:
            raise ValueError(
                f"RAG_EMBEDDING_BACKEND={self.RAG_EMBEDDING_BACKEND!r} is not one of {sorted(_rag_engines)}"
            )
        return issues


@lru_cache
def get_settings() -> Settings:
    return Settings()
