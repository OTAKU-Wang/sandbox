import logging
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

    # TEE simulation (degraded mode when SGX/GPU hardware unavailable)
    TEE_SIMULATION_MODE: bool = True
    GPU_TEE_SIMULATION: bool = True

    # CORS
    CORS_ALLOWED_ORIGINS: list[str] = ["http://localhost:5173", "http://localhost:3000", "http://localhost:3001", "http://localhost:3002"]

    # Audit
    CLICKHOUSE_URL: str = "localhost:9000"

    # CDC Kafka
    CDC_KAFKA_BROKERS: str = "localhost:9092"
    CDC_KAFKA_TOPIC_PREFIX: str = "cds.events"

    # PII NER ML
    PII_NER_USE_ML: bool = False
    PII_NER_MODEL_NAME: str = "bert-base-chinese-pii-ner"
    PII_NER_CONFIDENCE_THRESHOLD: float = 0.5

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
