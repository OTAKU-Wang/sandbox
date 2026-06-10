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
from app.api import auth, data_products, data_resources, sandbox_sessions, contracts, health, dev_sandbox, audit, monitoring, output_control, catalog, compliance, data_pipeline, certificates, mpc, sandbox_db, kms, sandbox_tasks, training, federation, field_exposure, connectors, gateway, users
from app.api import network_policy as network_policy_api
from app.api import admin
from app.models import pipeline_task, training_job, field_exposure as field_exposure_models, connector as connector_models, merkle_leaf, certificate, sandbox_node, policy_bundle, dp_budget, network_policy, app_credential, federation_trust, blockchain_anchor, alert  # Ensure tables are created


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: validate security configuration
    issues = settings.validate_jwt_security()
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

    # Startup: CDC agent (Kafka producer lifecycle)
    try:
        from app.services.cdc_agent import cdc_agent
        await cdc_agent.start()
        logger.info("[MAIN] CDCAgent started")
    except Exception as e:
        logger.error(f"CDCAgent start failed: {e}")

    yield
    # Shutdown: KMS TTL cleanup
    _ttl_task.cancel()
    try:
        await _ttl_task
    except _asyncio.CancelledError:
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

# Apply security middleware (disable rate limiting in test mode)
is_testing = os.environ.get("TESTING") == "1" or os.environ.get("PYTEST_CURRENT_TEST")
setup_security(app, allowed_origins=settings.CORS_ALLOWED_ORIGINS, enable_rate_limit=not is_testing)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Catch-all exception handler — prevents stack traces from leaking to clients."""
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


# Register routers
app.include_router(health.router, tags=["health"])
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

# Frontend admin UI (FE-1~FE-5)
from fastapi.staticfiles import StaticFiles
from pathlib import Path as _Path
_static_dir = _Path(__file__).parent / "static"
if _static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")
app.include_router(admin.router)
