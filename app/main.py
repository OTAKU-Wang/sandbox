import json
import logging
import os
import uuid
from contextlib import asynccontextmanager
from collections.abc import Callable

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.core.scopes import (
    effective_key_credential,
    is_key_credential,
    key_unmapped_deny,
    require_group,
    require_human,
)

logger = logging.getLogger(__name__)
from app.core.database import engine, Base
from app.core.security import setup_security
from app.core.redis import get_redis, close_redis, redact_url
from app.api import auth, data_products, data_resources, sandbox_sessions, contracts, health, dev_sandbox, audit, monitoring, output_control, catalog, compliance, data_pipeline, certificates, mpc, sandbox_db, kms, sandbox_tasks, training, federation, field_exposure, connectors, gateway, users, rag
from app.api import integrity as integrity_api  # P2-5 (D-e2e-18): MinIO 一致性巡检面
from app.api import notebooks as notebooks_api, workloads as workloads_api, compute as compute_api, deliveries as deliveries_api, open_api as open_api_router, flow_runtime as flow_runtime_api, quota as quota_api  # SS-15 / SS-17
from app.api import session_stream  # W10: WebSocket exec stream
from app.api import jupyter_proxy  # SS-19: 开发环 JupyterLab 反代
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
from app.api import teams  # 2026-11-03 sandbox realignment Task 4: 团队管理与团队级配额隔离
from app.api import scenarios as scenarios_api  # 2026-11-03 sandbox realignment Task 5: 业务场景中心
from app.api import projects as projects_api  # 2026-11-03 sandbox realignment Task 6: 建模项目与 DAG 编排
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
import app.models.team  # noqa: F401
import app.models.scenario  # noqa: F401
import app.models.modeling_project  # noqa: F401
import app.models.billing_invoice  # noqa: F401
import app.models.user_notification  # noqa: F401


def _create_all_allowed(cfg, env: dict | None = None) -> bool:
    """只有 dev/test（或 sqlite 开发库）才允许 create_all；生产必须走 Alembic（A75）。

    门从内联表达式提出来是为了可测：「生产不建表」不能只能靠读代码相信。
    """
    environ = os.environ if env is None else env
    return bool(cfg.DEBUG or environ.get("TESTING") == "1" or "sqlite" in cfg.DATABASE_URL)


def durable_queue_wiring() -> dict:
    """W16 持久队列的接线事实（A75）：表由本进程建，但没有任何生产消费者在跑它。"""
    return {
        "table": "task_queue",
        "schema_created_at_boot": True,
        "production_consumer": False,
        "terminal_write_guard": "none",
        "stale_requeue_guard": "none",
    }


def _announce_boot_wiring() -> None:
    """T12：启动时把半成品接线说出来 —— 「表建好了」不等于「功能可用了」（A75）。"""
    wiring = durable_queue_wiring()
    logger.warning(
        "[MAIN] durable task queue wiring: %s —— 接线生产消费者之前需先补 claimed_by 归属守卫与 requeue 退避（A75）",
        " ".join(f"{key}={value}" for key, value in wiring.items()),
    )


def jwt_key_source() -> tuple[str, str]:
    """A89（部署/启动披露腿）：归类本进程实际使用的 JWT 签名密钥来源。

    分支顺序镜像 ``app.services.auth_service._effective_jwt_secret_key``：
    显式密钥 > TESTING/PYTEST 测试密钥 > CDS_ALLOW_LOCAL_DEFAULT_JWT_KEY 公开
    密钥 opt-in > DEBUG 进程内随机 > validate_jwt_security() 后接受的非生产公开
    默认。只回类别与运维建议，绝不返回或回显密钥本身及其任何前缀。
    """
    from app.core.config import _JWT_DEFAULT_KEY
    from app.services import auth_service as _auth

    if settings.JWT_SECRET_KEY != _JWT_DEFAULT_KEY:
        return "explicit", "CDS_JWT_SECRET_KEY 已配置；令牌跨重启有效、多副本互验一致"
    if os.environ.get("TESTING") == "1" or os.environ.get("PYTEST_CURRENT_TEST"):
        return "test-local", "TESTING/PYTEST 环境变量生效；使用源码内置测试密钥（仅限测试套件）"
    if _auth._env_flag(_auth._LOCAL_DEFAULT_JWT_KEY_ENV):
        return "published-opt-in", (
            f"{_auth._LOCAL_DEFAULT_JWT_KEY_ENV}=true；使用源码公开的默认密钥——持有源码者可伪造令牌，"
            "应改用 CDS_JWT_SECRET_KEY"
        )
    if settings.DEBUG:
        return "ephemeral-random", (
            "DEBUG 回退；使用进程内随机密钥——重启后全部令牌失效、多副本无法互验，"
            "稳定部署请设 CDS_JWT_SECRET_KEY"
        )
    return "published-default", "使用源码公开的默认密钥签名；请配置 CDS_JWT_SECRET_KEY"


def _dp_budget_sync_client():
    """A192d：跨进程 ε 预算账本要的是**同步** redis 客户端（`*_sync` 路径跑在同步上下文里）。"""
    import redis as redis_lib

    client = redis_lib.Redis.from_url(
        settings.REDIS_URL,
        decode_responses=True,
        socket_connect_timeout=2,
        socket_timeout=2,
    )
    client.ping()
    return client


def dp_budget_backend_wiring(
    client_factory: Callable[[], object] | None = None,
) -> dict[str, object]:
    """A192d：装配期把跨进程 ε 预算后端接到账本上，并如实披露接没接上。

    接不上时账本仍可读写，但那份 ε 预算只是本进程的：多副本各花各的、重启即清零。
    所以失败路径只报 `budget_backend_health()` 的真实读数（backend/authoritative），
    不得静默把进程内的旧数当权威继续对外说「预算还有/已耗尽」。
    客户端自带 ping 门：`attach_redis` 只在 ping 通过后才调，避免接上一个
    ping 都失败却仍被 health 报成 authoritative=True 的后端。
    """
    from app.services.dp_budget import dp_budget_ledger

    factory = client_factory or _dp_budget_sync_client
    try:
        client = factory()
    except Exception as exc:
        health = dp_budget_ledger.budget_backend_health()
        logger.warning(
            "[MAIN] DP ε-budget backend NOT attached (%s: %s) — ε 预算自此只是本进程的："
            "不跨副本共享、重启即清零；backend=%s authoritative=%s（可查："
            "dp_budget_ledger.budget_backend_health()，A192d）",
            exc.__class__.__name__, redact_url(str(exc)),
            health["backend"], health["authoritative"],
        )
        return health

    dp_budget_ledger.attach_redis(client)
    health = dp_budget_ledger.budget_backend_health()
    log = logger.info if health["authoritative"] else logger.warning
    log(
        "[MAIN] DP ε-budget backend=%s authoritative=%s redis_errors=%s"
        "（A192d：多副本预算可见性由它决定）",
        health["backend"], health["authoritative"], health["redis_errors"],
    )
    return health


def hsm_backend_disclosure() -> dict[str, object]:
    """T12/A94：开机就说清当前 HSM 后端到底是不是硬件（诚实降级铁律）。

    backend-25b 已把 `is_simulation`/`backend` 落在适配器上，但只有调用方读了
    它们才成得了「可辨别」——软件 KEK 与 Vault/HSM KEK 在开机日志里必须一字之差。
    取不到后端时也得如实报 `unknown`，不能静默跳过这条披露。
    """
    try:
        from app.services.hsm_adapter import describe_hsm_backend

        desc = describe_hsm_backend()
    except Exception as exc:
        logger.error(
            "[MAIN] HSM backend=unknown is_simulation=unknown — 无法确定密钥后端 "
            "(%s: %s)，不得当作硬件 HSM 使用（A94/T12）",
            exc.__class__.__name__, exc,
        )
        return {"backend": "unknown", "is_simulation": True, "error": str(exc)}

    log = logger.warning if desc["is_simulation"] else logger.info
    # P2-3 持久化事实从适配器属性读（describe_hsm_backend 的两键口径被
    # test_ocr_a094_hsm_disclosure 锁死，不扩键）。
    from app.services import hsm_adapter as _hsm
    _adapter = getattr(_hsm, "hsm_adapter", None)
    _mk_persistent = bool(getattr(_adapter, "master_key_persistent", False))
    _mk_file = str(getattr(_adapter, "master_key_file", "") or "")
    if desc["is_simulation"] and _mk_persistent:
        detail = (
            f"软件主密钥文件（非 HSM）：{_mk_file} — KEK 由它确定性派生，"
            "跨重启可解密既有信封（P2-3）；SM2 签名密钥对仍为内存态。"
            "真 KMS/HSM 属 P5 硬件轨道，本文件不是硬件豁免"
        )
    elif desc["is_simulation"]:
        detail = (
            "KEK 活在本进程、不是硬件 HSM（dev/test only；生产请接 Vault 并置 "
            "CDS_HSM_SOFTWARE_FALLBACK_ALLOWED=false）。P2-3：设 "
            "CDS_KMS_MASTER_KEY_FILE=<挂载卷路径> 可让主密钥跨重启"
        )
    else:
        detail = "KEK 由外部 HSM/Vault 托管，本进程不持明文 KEK"
    log(
        "[MAIN] HSM backend=%s is_simulation=%s — %s",
        desc["backend"], desc["is_simulation"], detail,
    )
    return {**desc, "master_key_persistent": _mk_persistent, "master_key_file": _mk_file}


DUCKDB_ENGINE_SWEEP_SECONDS = 300


async def _duckdb_engine_sweeper_loop() -> None:
    """按 TTL 回收 sandbox-db 的 DuckDB 引擎（A56：明文表与列 DEK 不该等下一个请求）。"""
    import asyncio as _asyncio

    while True:
        await _asyncio.sleep(DUCKDB_ENGINE_SWEEP_SECONDS)
        swept = sandbox_db.sweep_expired_duckdb_engines()
        if swept:
            logger.info("[MAIN] Reaped %d expired sandbox-db DuckDB engines (A56 TTL)", swept)


def _start_duckdb_engine_sweeper():
    import asyncio as _asyncio

    return _asyncio.create_task(_duckdb_engine_sweeper_loop())


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: validate security configuration (T12 — surfaces every enabled
    # simulation/fallback so no degradation is silent)
    issues = settings.validate_security_config()
    for issue in issues:
        logger.warning(f"[SECURITY] {issue}")

    _announce_boot_wiring()

    # A94/T12：模拟 HSM 后端必须在启动日志里自报（不得静默），同时留一份可查健态。
    _hsm_desc = hsm_backend_disclosure()
    app.state.hsm_backend = _hsm_desc

    # A192d：ε 预算的跨进程后端必须在装配期接上并如实披露——静默退回进程内账本
    # 再照旧报预算，就是 owner-output-chain 已经修掉的那个错口径（A192/M-03）。
    app.state.dp_budget_backend = dp_budget_backend_wiring()

    # Startup: initialize Redis connection and wire into services
    try:
        redis_client = await get_redis()
        # Wire Redis into quota_manager
        from app.services.quota_manager import quota_manager
        quota_manager._redis = redis_client
    except Exception as e:
        # A38: 初始化失败的原因字符串可能回显含密码的 REDIS_URL
        logger.error("Redis initialization failed: %s", redact_url(str(e)))

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
    if _create_all_allowed(settings):
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    else:
        logger.info("[MAIN] Skipping Base.metadata.create_all; use Alembic migrations")

    # A89：在任何令牌签发之前披露本次启动的 JWT 密钥来源类别（运维从开机日志
    # 就能判断令牌是否活过重启），绝不打印密钥本身。
    _jwt_source, _jwt_advice = jwt_key_source()
    _jwt_log = logger.info if _jwt_source == "explicit" else logger.warning
    _jwt_log(
        "[MAIN] JWT signing-key source=%s — %s", _jwt_source, _jwt_advice
    )

    # A82：重启遗留的 `running` 操作行没有任何执行者在跑，启动时先判 failed，
    # 否则回滚互斥门会把该 session 永久锁死。必须在第一个请求进来之前执行。
    try:
        from app.core.database import async_session as _ops_session
        from app.services.session_operations import reclaim_stale_operations

        async with _ops_session() as _ops_db:
            _reclaimed = await reclaim_stale_operations(_ops_db)
            await _ops_db.commit()
        if _reclaimed:
            logger.warning(
                "[MAIN] Reclaimed %d stale running session operations at boot (A82 lease)", _reclaimed,
            )
    except Exception as e:
        logger.error(f"[MAIN] Stale operation reclaim failed: {e}")

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
    # A138: the sweep now has a real writer (distribute_key mints ledger rows);
    # disclose the honest delay boundary — hand-offs persist within one sweep.
    logger.info(
        "[MAIN] KMS TTL cleanup started — key_distributions 台账由 distribute_key 交接点写入，"
        "落库延迟 ≤ 300s（循环周期），到期对 session_key_enc 做 crypto-erase（A138）"
    )

    _duckdb_sweeper_task = _start_duckdb_engine_sweeper()
    logger.info(
        "[MAIN] sandbox-db DuckDB engine sweeper started (interval=%ss)", DUCKDB_ENGINE_SWEEP_SECONDS
    )

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

    # P2-3: 软件主密钥文件若是本次首启生成，落一条 kms.master_key.generated 审计
    # （适配器构造在 import 期，没有 DB，事件只能落在 lifespan 里）。审计写不进
    # 只披露不吞（core 铁律）：生成事实此刻也已在本进程 WARNING 日志里。
    try:
        from app.services import hsm_adapter as _hsm_mod
        _adapter = getattr(_hsm_mod, "hsm_adapter", None)
        if _adapter is not None and getattr(_adapter, "master_key_just_generated", False):
            from app.core.database import async_session as _mk_session
            from app.services.audit_service import audit_service
            async with _mk_session() as db:
                await audit_service.log(
                    db, action="kms.master_key.generated", resource_type="kms",
                    detail={
                        "backend": "software-fallback",
                        "file": str(getattr(_adapter, "master_key_file", "")),
                        "note": "首启生成 0600 软件主密钥文件（非 HSM，P2-3）；"
                                "备份/迁移该文件即迁移全部既有信封的可解密性",
                    },
                    actor_type="system",
                )
                await db.commit()
            logger.info("[MAIN] kms.master_key.generated 审计已落库（软件主密钥文件首启生成）")
    except Exception as e:
        logger.error(f"[MAIN] kms.master_key.generated 审计落库失败（生成事实已在 WARNING 日志留痕）: {e}")

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

    # A147: 会话行与真实容器状态的周期对账（启动期一次性收敛只能覆盖重启，
    # 运行期容器异外退出/回收失败会长期留下「行活容器没」与「容器活行终」两类孤儿）。
    _container_reconcile_task = None
    if os.environ.get("TESTING") != "1":
        async def _container_state_reconcile_loop() -> None:
            from app.core.database import async_session
            from app.services.sandbox_runtime import sandbox_runtime
            while True:
                await _asyncio.sleep(settings.SESSION_RECONCILE_INTERVAL_SECONDS)
                try:
                    async with async_session() as _db:
                        await sandbox_runtime.reconcile_container_state(_db, limit=200)
                        await _db.commit()
                except Exception as e:  # 对账失败不得带倒服务，但必须响亮披露
                    logger.error("[MAIN] Container state reconcile failed: %s", e)

        _container_reconcile_task = _asyncio.create_task(_container_state_reconcile_loop())
        logger.info(
            "[MAIN] Container state reconcile started (interval=%ss)",
            settings.SESSION_RECONCILE_INTERVAL_SECONDS,
        )

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
    for _bg_task in (_compute_scheduler_task, _webhook_dispatcher_task, _container_reconcile_task):
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
    # Shutdown: sandbox-db DuckDB engine sweeper (A56)
    _duckdb_sweeper_task.cancel()
    try:
        await _duckdb_sweeper_task
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
        header_key = ""
        # A65: 同名头必须按 starlette `Headers.get` 的「取第一个」口径解析；取最后一个
        # 会让 `[Bearer cds_ak_…, Bearer <别的>]` 这类重复头在守卫眼里不是 key，
        # 而路由仍然按第一个头把它认证成 key 主体。
        for k, v in scope.get("headers") or []:
            if k == b"authorization":
                if not auth:
                    auth = v.decode("latin-1")
            elif k == b"x-cds-api-key":
                if not header_key:
                    header_key = v.decode("latin-1")
        # 文档化的数据面凭据（X-CDS-API-Key）同样是 key subject：默认拒绝必须
        # 看得见它，否则换一个头就绕过了未映射前缀的 fail-closed 判定。
        # A67：但优先级必须与 `deps.get_auth_subject` 一致——Authorization 里
        # 出现任何 Bearer token 就是本请求的凭据，头凭据只在 bearer 缺失/非
        # bearer 形态时说话。旧写法把「bearer 不是 key」直接折叠成头 key，人
        # JWT + 残留 key 头会在未映射路径被守卫当 key 主体拒 403（路由本会
        # 按人处理——自我伤害）。判定收敛进 `key_unmapped_deny` 的同口径谓词。
        if not key_unmapped_deny(path, auth, api_key_header=header_key):
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
# A43: bind the request_id contextvar for the request lifetime so log records
# carry it. Added BEFORE RequestIDMiddleware on purpose → it sits just INSIDE
# that middleware and reads the id RequestIDMiddleware stamped on
# scope["state"]. The three documented middlewares keep their relative order.
from app.core.logging import RequestIDContextMiddleware  # noqa: E402

app.add_middleware(RequestIDContextMiddleware)
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
# 团队面挂在 users 之前（`/api/v1/users/teams` 是 `/api/v1/users` 的子前缀）：两者同属
# human-only 组，先挂具体前缀可避免将来 users 添 `/{user_id}` 类路由时遮蔽团队面。
app.include_router(teams.router, prefix="/api/v1/users/teams", tags=["teams"], dependencies=[Depends(require_human)])
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
# P2-5（D-e2e-18）：巡检台账与手动触发挂 audit 组（与审计面同群，不新开
# scope）；OPERATOR+/ADMIN 收紧在路由级 require_roles。
app.include_router(integrity_api.router, prefix="/api/v1/integrity", tags=["integrity"], dependencies=[Depends(require_group("audit"))])
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
# Task 5 场景中心必须先挂：flow_runtime 的面（/runtime-status、/dashboard/home）路径更深，
# 而 `/api/v1/flow` 这一通用前缀先挂会让深路径永久不可达（仓内既有口径：先挂更具体的前缀）。
app.include_router(
    scenarios_api.router,
    prefix="/api/v1/flow/scenarios",
    tags=["flow"],
    dependencies=[Depends(require_group("sessions"))],
)
# Task 6 建模项目面同样先于通用 flow 前缀挂载（理由与上一条相同）。
app.include_router(
    projects_api.router,
    prefix="/api/v1/flow/projects",
    tags=["flow"],
    dependencies=[Depends(require_group("sessions"))],
)
app.include_router(flow_runtime_api.router, prefix="/api/v1/flow", tags=["flow"], dependencies=[Depends(require_group("sessions"))])
# SS-17: 配额用量只读面——挂在 deliveries 前缀（gateway 组已在 SS-12 前缀映射内；
# 未映射前缀会被 resolve_group 判为未映射而对 API Key 默认拒绝）。
app.include_router(quota_api.router, prefix="/api/v1/deliveries", tags=["flow"], dependencies=[Depends(require_group("gateway"))])
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
# SS-19 开发环：根级挂载，与 session_stream 同一先例 —— 鉴权不走 Authorization 头
# （iframe 带不了），而是 /api/jupyter/tickets 发的一次性票据 → 签名 Cookie；
# 不加入 scopes.GROUP_PREFIXES = 机器凭据默认进不来（SS-12 default-deny）。
app.include_router(jupyter_proxy.router, tags=["jupyter-dev-env"])
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
                auth = ""
                header_key = ""
                for k, v in scope.get("headers", []):
                    if k == b"authorization" and not auth:
                        auth = v.decode("latin-1")
                    elif k == b"x-cds-api-key" and not header_key:
                        header_key = v.decode("latin-1")
                # A65: 与路由同口径 —— `HTTPBearer` 对 scheme 大小写不敏感、`deps` 会
                # strip 凭据，字节比较 `b"Bearer cds_ak_"` 差一个字节就看不见 key。
                # A67: 优先级也与路由同口径（`effective_key_credential`）——人 JWT + 残留
                # key 头仍是人主体，人机分流不得误伤人。
                if is_key_credential(effective_key_credential(auth, header_key)):
                    body = ('{"code": "API_KEY_SCOPE_DENIED", '
                            '"message": "API key subjects cannot access human-only endpoints", '
                            '"detail": {"reason": "human_only"}, "request_id": ""}')
                    await send({"type": "http.response.start", "status": 403,
                                "headers": [(b"content-type", "application/json")]})
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
