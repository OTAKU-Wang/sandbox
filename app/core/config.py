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
    # auto: use hardware when detected and command hooks are configured; otherwise
    # fall back to ordinary software confidential sandbox isolation.
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
        if self.SECCOMP_FALLBACK_ALLOWED:
            issues.append("WARN: SECCOMP_FALLBACK_ALLOWED=true — sandbox seccomp failures silently retry without seccomp")
        if self.HSM_SOFTWARE_FALLBACK_ALLOWED:
            issues.append("WARN: HSM_SOFTWARE_FALLBACK_ALLOWED=true — software KEK/signing fallback is enabled (degraded crypto strength)")
        if not self.FEDERATION_JWT_KEY_REQUIRED:
            issues.append("WARN: FEDERATION_JWT_KEY_REQUIRED=false — static federation JWT fallback is permitted in production")
        return issues


@lru_cache
def get_settings() -> Settings:
    return Settings()
