import logging
import os
import warnings

from pydantic_settings import BaseSettings
from functools import lru_cache

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

    # Session lifecycle (gap A2/D1): background sweep interval for expired
    # sessions, dev sessions and contracts.
    SESSION_CLEANUP_INTERVAL_SECONDS: int = 300

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
    HSM_SOFTWARE_FALLBACK_ALLOWED: bool = True  # fall back to in-memory software KEK/signing
    FEDERATION_JWT_KEY_REQUIRED: bool = True    # refuse static federation JWT key in prod

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

    # Legacy compatibility flag for the old SGX-shaped simulator. New L1
    # fallback uses software_confidential semantics and software_hash evidence.
    TEE_SIMULATION_MODE: bool = True
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

    # Streaming output proxy (mitmproxy addon)
    STREAMING_PROXY_ENABLED: bool = False
    STREAMING_PROXY_PORT: int = 8080

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
        if not self.FEDERATION_JWT_KEY_REQUIRED:
            issues.append("WARN: FEDERATION_JWT_KEY_REQUIRED=false — static federation JWT fallback is permitted in production")

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
