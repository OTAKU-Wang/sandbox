import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.config import get_settings

logger = logging.getLogger(__name__)
from app.core.database import engine, Base
from app.core.security import setup_security
from app.core.redis import get_redis, close_redis
from app.api import auth, data_products, data_resources, sandbox_sessions, contracts, health, dev_sandbox, audit, monitoring, output_control, catalog, compliance, data_pipeline, certificates, mpc, sandbox_db, kms, sandbox_tasks, training, federation, field_exposure, connectors, gateway, users, rag
from app.api import session_stream  # W10: WebSocket exec stream
from app.api import sandbox_nodes  # W14: node operations
from app.api import shared_volumes  # W15: shared volumes
from app.api import network_policy as network_policy_api
from app.api import admin
from app.api import metrics
from app.models import pipeline_task, training_job, field_exposure as field_exposure_models, connector as connector_models, merkle_leaf, certificate, sandbox_node, policy_bundle, dp_budget, network_policy, app_credential, federation_trust, blockchain_anchor, alert, mpc_key  # Ensure tables are created
from app.models import session_operation  # W11: async operation records
from app.models import task_queue as task_queue_models  # W16: durable queue table
from app.models import shared_volume  # W15: shared volume tables


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: validate security configuration (T12 — surfaces every enabled
    # simulation/fallback so no degradation is silent)
    issues = settings.validate_security_config()
    for issue in issues:
        logger.warning(f"[SECURITY] {issue}")

    # Startup: initialize Redis connection and wire into services
    try:
        redis_client = await get_redis()
        # Wire Redis into quota_manager
        from app.services.quota_manager import quota_manager
        quota_manager._redis = redis_client
    except Exception as e:
        logger.error(f"Redis initialization failed: {e}")

    # W3: seed per-tenant usage from the Redis write-through (restart
    # recovery); an empty Redis store falls back to a rebuild from the DB.
    try:
        from app.core.database import async_session as _quota_session
        from app.services.sandbox_manager import restore_tenant_quotas, rebuild_tenant_quotas
        _restored = await restore_tenant_quotas()
        if _restored == 0:
            async with _quota_session() as _quota_db:
                _rebuilt = await rebuild_tenant_quotas(_quota_db)
                await _quota_db.commit()
            if _rebuilt:
                logger.info("[MAIN] Tenant quotas rebuilt from DB for %d tenants", _rebuilt)
        else:
            logger.info("[MAIN] Tenant quotas restored from Redis for %d tenants", _restored)
    except Exception as e:
        logger.error(f"[MAIN] Tenant quota restore failed: {e}")

    # Startup: create tables only in dev/test. Production must use Alembic.
    allow_create_all = (
        settings.DEBUG
        or os.environ.get("TESTING") == "1"
        or "sqlite" in settings.DATABASE_URL
    )
    if allow_create_all:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    else:
        logger.info("[MAIN] Skipping Base.metadata.create_all; use Alembic migrations")

    # Startup: start task pipeline (P1-2 unified scheduler)
    try:
        from app.services.task_pipeline import task_pipeline
        await task_pipeline.start()
        logger.info("[MAIN] TaskPipeline started")
    except Exception as e:
        logger.error(f"TaskPipeline start failed: {e}")
        # Fallback: start legacy task worker
        try:
            from app.services.task_worker import start_worker
            await start_worker()
        except Exception as e2:
            logger.error(f"Task worker fallback also failed: {e2}")

    # Startup: start KMS TTL cleanup loop (#150)
    import asyncio as _asyncio
    from app.services.kms_service import _ttl_cleanup_loop
    _ttl_task = _asyncio.create_task(_ttl_cleanup_loop())
    logger.info("[MAIN] KMS TTL cleanup started")

    # Startup: restore persisted wrapped keys (gap B3 — restart recovery)
    try:
        from app.core.database import async_session as _async_session
        from app.services.kms_recovery import restore_wrapped_keys
        async with _async_session() as db:
            restored_keys = await restore_wrapped_keys(db)
            await db.commit()
        if restored_keys:
            logger.info("[MAIN] Restored %d wrapped keys from persistence", restored_keys)
    except Exception as e:
        logger.error(f"[MAIN] Wrapped key restore failed: {e}")

    # Startup: session lifecycle cleanup loop (gap A2/D1 — expired sessions,
    # dev sessions and contracts). Disabled under TESTING (tests disable the
    # lifespan anyway; this guard covers direct TestClient usage).
    _session_cleanup_task = None
    if os.environ.get("TESTING") != "1":
        from app.services.session_lifecycle import session_cleanup_loop
        _session_cleanup_task = _asyncio.create_task(
            session_cleanup_loop(settings.SESSION_CLEANUP_INTERVAL_SECONDS)
        )
        logger.info("[MAIN] Session lifecycle cleanup started")

    # Startup: CDC agent (Kafka producer lifecycle)
    try:
        from app.services.cdc_agent import cdc_agent
        await cdc_agent.start()
        logger.info("[MAIN] CDCAgent started")
    except Exception as e:
        logger.error(f"CDCAgent start failed: {e}")

    # Startup: Merkle batch pipeline (experimental, opt-in — spec N2).
    # Off by default; the audit path anchors via the synchronous
    # merkle_service. Enable only for evaluation.
    _merkle_pipeline_task = None
    if settings.MERKLE_PIPELINE_ENABLED:
        from app.services.merkle_pipeline import merkle_pipeline
        await merkle_pipeline.start()
        _merkle_pipeline_task = merkle_pipeline
        logger.warning(
            "[MAIN] Merkle batch pipeline ENABLED (experimental async mode); "
            "audit anchoring now also flows through the Redis Stream worker"
        )

    yield
    # Shutdown: session lifecycle cleanup
    if _session_cleanup_task is not None:
        _session_cleanup_task.cancel()
        try:
            await _session_cleanup_task
        except _asyncio.CancelledError:
            pass
    # Shutdown: Merkle batch pipeline
    if _merkle_pipeline_task is not None:
        try:
            await _merkle_pipeline_task.stop()
        except Exception as e:
            logger.error(f"[MAIN] Merkle pipeline stop failed: {e}")
    # Shutdown: KMS TTL cleanup
    _ttl_task.cancel()
    try:
        await _ttl_task
    except _asyncio.CancelledError:
        pass
    # Shutdown: egress audit flush (W13)
    try:
        from app.services.egress_audit import shutdown_egress_audit
        await shutdown_egress_audit()
    except Exception:
        pass
    # Shutdown: CDC agent
    try:
        from app.services.cdc_agent import cdc_agent
        await cdc_agent.stop()
    except Exception:
        pass
    # Shutdown
    try:
        from app.services.task_pipeline import task_pipeline
        await task_pipeline.stop()
    except Exception:
        pass
    try:
        from app.services.task_worker import stop_worker
        await stop_worker()
    except Exception:
        pass
    from app.services.opa_client import opa_client
    await opa_client.close()
    await close_redis()
    await engine.dispose()


settings = get_settings()

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    lifespan=lifespan,
)

# W1: telemetry middlewares. Starlette makes the LAST added middleware the
# OUTERMOST, so add Metrics first and RequestID second. Metrics is disabled
# under TESTING (TestClient assertions stay deterministic).
is_testing = os.environ.get("TESTING") == "1" or os.environ.get("PYTEST_CURRENT_TEST")
from app.core.telemetry import MetricsMiddleware, RequestIDMiddleware  # noqa: E402

app.add_middleware(
    MetricsMiddleware,
    enabled=(not is_testing) and settings.METRICS_ENABLED,
)
app.add_middleware(RequestIDMiddleware)

# Apply security middleware (disable rate limiting in test mode)
setup_security(app, allowed_origins=settings.CORS_ALLOWED_ORIGINS, enable_rate_limit=not is_testing)

# W1: logging pipeline (no-op unless CDS_LOG_JSON=true)
from app.core.logging import setup_logging  # noqa: E402

setup_logging(settings.LOG_JSON)


# W2: unified error contract — {code, message, detail, request_id} bodies,
# Retry-After for transient locks, 410 semantics for terminal states.
from app.core.error_handlers import register_exception_handlers  # noqa: E402

register_exception_handlers(app)


# Register routers
app.include_router(health.router, tags=["health"])
app.include_router(metrics.router, tags=["metrics"])
app.include_router(auth.router, prefix="/api/v1/auth", tags=["auth"])
app.include_router(users.router, prefix="/api/v1/users", tags=["users"])
app.include_router(data_products.router, prefix="/api/v1/data-products", tags=["data-products"])
app.include_router(data_resources.router, prefix="/api/v1/data-resources", tags=["data-resources"])
app.include_router(sandbox_sessions.router, prefix="/api/v1/sandbox-sessions", tags=["sandbox-sessions"])
app.include_router(contracts.router, prefix="/api/v1/contracts", tags=["contracts"])
app.include_router(dev_sandbox.router, prefix="/api/v1/dev-sandbox", tags=["dev-sandbox"])
app.include_router(audit.router, prefix="/api/v1/audit", tags=["audit"])
app.include_router(monitoring.router, prefix="/api/v1/monitoring", tags=["monitoring"])
app.include_router(output_control.router, prefix="/api/v1/output-control", tags=["output-control"])
app.include_router(catalog.router, prefix="/api/v1/catalog", tags=["catalog"])
app.include_router(compliance.router, prefix="/api/v1/compliance", tags=["compliance"])
app.include_router(data_pipeline.router, prefix="/api/v1/data-pipeline", tags=["data-pipeline"])
app.include_router(certificates.router, prefix="/api/v1/certificates", tags=["certificates"])
app.include_router(mpc.router, prefix="/api/v1/mpc", tags=["mpc"])
app.include_router(sandbox_db.router, prefix="/api/v1/sandbox-db", tags=["sandbox-db"])
app.include_router(kms.router, prefix="/api/v1/kms", tags=["kms"])
app.include_router(sandbox_tasks.router, prefix="/api/v1/sandbox-tasks", tags=["sandbox-tasks"])
app.include_router(training.router, prefix="/api/v1/training", tags=["training"])
app.include_router(federation.router, prefix="/api/v1/federation", tags=["federation"])
app.include_router(field_exposure.router, prefix="/api/v1/field-exposure", tags=["field-exposure"])
app.include_router(connectors.router, prefix="/api/v1/connectors", tags=["connectors"])
app.include_router(network_policy_api.router, prefix="/api/v1/network-policies", tags=["network-policies"])
app.include_router(gateway.router, prefix="/api/v1/gateway", tags=["gateway"])
app.include_router(rag.router, prefix="/api/v1/rag", tags=["rag"])

# W10: WebSocket exec stream (mounted without prefix — paths are absolute)
app.include_router(session_stream.router)
app.include_router(sandbox_nodes.router, prefix="/api/v1/sandbox-nodes", tags=["sandbox-nodes"])
app.include_router(shared_volumes.router, prefix="/api/v1/shared-volumes", tags=["shared-volumes"])

# Frontend admin UI (FE-1~FE-5)
from fastapi.staticfiles import StaticFiles
from pathlib import Path as _Path
_static_dir = _Path(__file__).parent / "static"
if _static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")
# W4: legacy Jinja admin pages carry no authentication — off by default, and
# validate_security_config() rejects enabling them in production. Factored
# into a function so tests can exercise the gate on a fresh app instance.
def _register_admin_pages(target_app: FastAPI) -> None:
    if settings.ADMIN_PAGES_ENABLED:
        logger.warning(
            "[SECURITY] Admin legacy pages enabled at /admin/* — NOT for production use"
        )
        target_app.include_router(admin.router)


_register_admin_pages(app)
