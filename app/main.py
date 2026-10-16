import json
import logging
import os
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.core.scopes import key_unmapped_deny, require_group, require_human

logger = logging.getLogger(__name__)
from app.core.database import engine, Base
from app.core.security import setup_security
from app.core.redis import get_redis, close_redis
from app.api import auth, data_products, data_resources, sandbox_sessions, contracts, health, dev_sandbox, audit, monitoring, output_control, catalog, compliance, data_pipeline, certificates, mpc, sandbox_db, kms, sandbox_tasks, training, federation, field_exposure, connectors, gateway, users, rag
from app.api import notebooks as notebooks_api, workloads as workloads_api, compute as compute_api, deliveries as deliveries_api, open_api as open_api_router, flow_runtime as flow_runtime_api  # SS-15
from app.api import session_stream  # W10: WebSocket exec stream
from app.api import sandbox_nodes  # W14: node operations
from app.api import shared_volumes  # W15: shared volumes
from app.api import network_policy as network_policy_api
from app.api import admin
from app.api import metrics
from app.api import inference  # N5: inference service sandbox
from app.api import k8s_ops  # N8: K8s runtime operations (read-only)
from app.api import meta  # PZ-13: capability matrix authority source
from app.api import cube_templates  # SS-10 CB-05: template pipeline (image → artifact → distribution)
from app.api import agent  # N10: sandbox-internal agent execution
from app.api import cube_egress  # CB-06: node egress audit ingestion (/internal/audit)
from app.api import api_keys  # SS-12: platform API key lifecycle (human-only)
from app.models import pipeline_task, training_job, field_exposure as field_exposure_models, connector as connector_models, merkle_leaf, certificate, sandbox_node, policy_bundle, dp_budget, network_policy, app_credential, federation_trust, blockchain_anchor, alert, mpc_key, trained_model, rag_generative_model  # Ensure tables are created
from app.models import api_key as api_key_models  # SS-12: api_keys table
from app.models import session_operation  # W11: async operation records
from app.models import task_queue as task_queue_models  # W16: durable queue table
from app.models import shared_volume  # W15: shared volume tables
from app.models import volume_key  # CB-01: volume DEK lifecycle tables
from app.models import cube_egress_dedup  # CB-06: egress audit idempotency keys
from app.models import cube_template  # CB-05: cube template registry + node distribution
from app.models import notebook, workload, workload_test_run, compute_run  # SS-15: dev-to-delivery flow
from app.models import api_endpoint, webhook, webhook_delivery, delivery_bundle  # SS-15: delivery channels


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

    _compute_scheduler_task = None
    if os.environ.get("TESTING") != "1" and settings.COMPUTE_SCHEDULER_ENABLED:
        from app.services.compute_scheduler import compute_scheduler_loop
        _compute_scheduler_task = _asyncio.create_task(
            compute_scheduler_loop(settings.COMPUTE_SCHEDULER_INTERVAL_SECONDS)
        )
        logger.info(
            "[MAIN] Compute scheduler started (interval=%ss; 多副本下靠 idempotency_key 唯一约束抢占)",
            settings.COMPUTE_SCHEDULER_INTERVAL_SECONDS,
        )

    _webhook_dispatcher_task = None
    if os.environ.get("TESTING") != "1" and settings.WEBHOOK_DISPATCH_ENABLED:
        from app.services.delivery_service import webhook_dispatcher_loop
        _webhook_dispatcher_task = _asyncio.create_task(
            webhook_dispatcher_loop(settings.WEBHOOK_DISPATCH_INTERVAL_SECONDS)
        )
        logger.info("[MAIN] Webhook dispatcher started (interval=%ss)", settings.WEBHOOK_DISPATCH_INTERVAL_SECONDS)

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
    for _bg_task in (_compute_scheduler_task, _webhook_dispatcher_task):
        if _bg_task is not None:
            _bg_task.cancel()
            try:
                await _bg_task
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

class Ss12KeyDefaultDenyMiddleware:
    """SS-12 rule 1: API key subjects hitting a path no functional group
    covers are rejected before routing — fail-closed default-deny.

    Raw ASGI (not a dependency): the previous app-level
    ``dependencies=[Depends(default_deny_unmapped)]`` carried an
    HTTPBearer/Request dependency that crashes WebSocket handshakes
    (``HTTPBearer.__call__() missing 'request'``), breaking every WS route.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        auth = ""
        for k, v in scope.get("headers") or []:
            if k == b"authorization":
                auth = v.decode("latin-1")
        if not key_unmapped_deny(path, auth):
            await self.app(scope, receive, send)
            return
        request_id = (scope.get("state") or {}).get("request_id") or uuid.uuid4().hex[:16]
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        body = json.dumps({
            "code": "API_KEY_SCOPE_DENIED",
            "message": f"No functional group covers path {path} for API key subjects",
            "detail": {"reason": "unmapped_prefix", "path": path},
            "request_id": request_id,
        }).encode()
        await send({
            "type": "http.response.start",
            "status": 403,
            "headers": [(b"content-type", b"application/json"),
                        (b"x-request-id", request_id.encode())],
        })
        await send({"type": "http.response.body", "body": body})


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    lifespan=lifespan,
)
app.add_middleware(Ss12KeyDefaultDenyMiddleware)

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
# SS-12: every router group carries its functional scope gate (require_group)
# or the human-only gate (require_human) — user subjects are untouched, API
# key subjects are scope-checked per specs/ss-12-agent-api-keys.md §4.2.
app.include_router(health.router, tags=["health"], dependencies=[Depends(require_group("audit"))])
app.include_router(metrics.router, tags=["metrics"], dependencies=[Depends(require_group("audit"))])
app.include_router(auth.router, prefix="/api/v1/auth", tags=["auth"], dependencies=[Depends(require_human)])
app.include_router(users.router, prefix="/api/v1/users", tags=["users"], dependencies=[Depends(require_human)])
app.include_router(api_keys.router, prefix="/api/v1/api-keys", tags=["api-keys"], dependencies=[Depends(require_human)])
app.include_router(data_products.router, prefix="/api/v1/data-products", tags=["data-products"], dependencies=[Depends(require_group("data"))])
app.include_router(data_resources.router, prefix="/api/v1/data-resources", tags=["data-resources"], dependencies=[Depends(require_group("data"))])
app.include_router(sandbox_sessions.router, prefix="/api/v1/sandbox-sessions", tags=["sandbox-sessions"], dependencies=[Depends(require_group("sessions"))])
app.include_router(contracts.router, prefix="/api/v1/contracts", tags=["contracts"], dependencies=[Depends(require_group("contracts"))])
app.include_router(dev_sandbox.router, prefix="/api/v1/dev-sandbox", tags=["dev-sandbox"], dependencies=[Depends(require_group("sessions"))])
app.include_router(audit.router, prefix="/api/v1/audit", tags=["audit"], dependencies=[Depends(require_group("audit"))])
app.include_router(monitoring.router, prefix="/api/v1/monitoring", tags=["monitoring"], dependencies=[Depends(require_group("audit"))])
app.include_router(output_control.router, prefix="/api/v1/output-control", tags=["output-control"], dependencies=[Depends(require_group("output"))])
app.include_router(catalog.router, prefix="/api/v1/catalog", tags=["catalog"], dependencies=[Depends(require_group("data"))])
app.include_router(compliance.router, prefix="/api/v1/compliance", tags=["compliance"], dependencies=[Depends(require_group("audit"))])
app.include_router(data_pipeline.router, prefix="/api/v1/data-pipeline", tags=["data-pipeline"], dependencies=[Depends(require_group("pipeline"))])
app.include_router(certificates.router, prefix="/api/v1/certificates", tags=["certificates"], dependencies=[Depends(require_group("certs"))])
app.include_router(mpc.router, prefix="/api/v1/mpc", tags=["mpc"], dependencies=[Depends(require_group("pipeline"))])
app.include_router(sandbox_db.router, prefix="/api/v1/sandbox-db", tags=["sandbox-db"], dependencies=[Depends(require_group("pipeline"))])
app.include_router(kms.router, prefix="/api/v1/kms", tags=["kms"], dependencies=[Depends(require_group("infra"))])
app.include_router(sandbox_tasks.router, prefix="/api/v1/sandbox-tasks", tags=["sandbox-tasks"], dependencies=[Depends(require_group("pipeline"))])
app.include_router(training.router, prefix="/api/v1/training", tags=["training"], dependencies=[Depends(require_group("pipeline"))])
app.include_router(federation.router, prefix="/api/v1/federation", tags=["federation"], dependencies=[Depends(require_group("federation"))])
app.include_router(field_exposure.router, prefix="/api/v1/field-exposure", tags=["field-exposure"], dependencies=[Depends(require_group("output"))])
app.include_router(connectors.router, prefix="/api/v1/connectors", tags=["connectors"], dependencies=[Depends(require_group("infra"))])
app.include_router(network_policy_api.router, prefix="/api/v1/network-policies", tags=["network-policies"], dependencies=[Depends(require_group("output"))])
app.include_router(gateway.router, prefix="/api/v1/gateway", tags=["gateway"], dependencies=[Depends(require_group("gateway"))])
app.include_router(notebooks_api.router, prefix="/api/v1/notebooks", tags=["notebooks"], dependencies=[Depends(require_group("sessions"))])
app.include_router(workloads_api.router, prefix="/api/v1/workloads", tags=["workloads"], dependencies=[Depends(require_group("sessions"))])
app.include_router(flow_runtime_api.router, prefix="/api/v1/flow", tags=["flow"], dependencies=[Depends(require_group("sessions"))])
app.include_router(compute_api.router, prefix="/api/v1/compute", tags=["compute"], dependencies=[Depends(require_group("pipeline"))])
app.include_router(deliveries_api.router, prefix="/api/v1/deliveries", tags=["deliveries"], dependencies=[Depends(require_group("gateway"))])
app.include_router(open_api_router.router, prefix="/open/v1", tags=["open-api"], dependencies=[Depends(require_group("gateway"))])
app.include_router(rag.router, prefix="/api/v1/rag", tags=["rag"], dependencies=[Depends(require_group("pipeline"))])
app.include_router(inference.router, prefix="/api/v1/inference", tags=["inference"], dependencies=[Depends(require_group("pipeline"))])
app.include_router(agent.router, prefix="/api/v1/agent", tags=["agent"], dependencies=[Depends(require_group("agent"))])
app.include_router(k8s_ops.router, prefix="/api/v1/ops", tags=["ops"], dependencies=[Depends(require_group("infra"))])
app.include_router(meta.router, prefix="/api/v1/meta", tags=["meta"], dependencies=[Depends(require_group("meta"))])  # PZ-13

# W10: WebSocket exec stream (mounted without prefix — paths are absolute;
# SS-12: machine subjects authenticate via the WS ticket, not Bearer keys, so
# no scope gate is attached here)
app.include_router(session_stream.router)
app.include_router(sandbox_nodes.router, prefix="/api/v1/sandbox-nodes", tags=["sandbox-nodes"], dependencies=[Depends(require_group("infra"))])
app.include_router(shared_volumes.router, prefix="/api/v1/shared-volumes", tags=["shared-volumes"], dependencies=[Depends(require_group("infra"))])
# CB-06: node audit tailer ingestion — service token + mTLS, NOT under /api/v1;
# SS-12: human-only surface, API key subjects are rejected
app.include_router(cube_egress.router, prefix="/internal/audit", tags=["cube-egress-audit"], dependencies=[Depends(require_human)])
app.include_router(cube_templates.router, prefix="/api/v1/cube-templates", tags=["cube-templates"], dependencies=[Depends(require_group("infra"))])

# Frontend admin UI (FE-1~FE-5)
from fastapi.staticfiles import StaticFiles
from pathlib import Path as _Path
_static_dir = _Path(__file__).parent / "static"
if _static_dir.exists():
    class _KeyBlockedStatic:
        """SS-12: /static is a human-only surface — machine (API key) Bearer
        requests get a W2 403 API_KEY_SCOPE_DENIED instead of the files."""

        def __init__(self, inner):
            self._inner = inner

        async def __call__(self, scope, receive, send):
            if scope.get("type") == "http":
                for k, v in scope.get("headers", []):
                    if k == b"authorization" and v.startswith(b"Bearer cds_ak_"):
                        body = ('{"code": "API_KEY_SCOPE_DENIED", '
                                '"message": "API key subjects cannot access human-only endpoints", '
                                '"detail": {"reason": "human_only"}, "request_id": ""}')
                        await send({"type": "http.response.start", "status": 403,
                                    "headers": [(b"content-type", b"application/json")]})
                        await send({"type": "http.response.body", "body": body.encode()})
                        return
            await self._inner(scope, receive, send)

    app.mount("/static", _KeyBlockedStatic(StaticFiles(directory=str(_static_dir))), name="static")
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
