"""工作负载生命周期服务 — 开发 -> 测试沙箱 -> 生产沙箱 的准入门禁（SS-15）。

门禁是本模块的唯一存在理由：生产沙箱只允许运行"在同一份 entrypoint 哈希上
通过测试沙箱验证"的工作负载。任何放宽都必须显式改这里的转移表，而不是在调用方绕过。
"""
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.contract import WorkloadEnv
from app.models.workload import Workload, WorkloadStatus
from app.models.workload_test_run import WorkloadTestRun, TestRunStatus
from app.services.notebook_service import extract_entrypoint
from app.utils.crypto import sm3_hash

logger = logging.getLogger(__name__)

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    WorkloadStatus.DRAFT.value: {WorkloadStatus.TESTING.value, WorkloadStatus.RETIRED.value},
    WorkloadStatus.TESTING.value: {WorkloadStatus.TEST_PASSED.value, WorkloadStatus.TEST_FAILED.value, WorkloadStatus.DRAFT.value},
    WorkloadStatus.TEST_PASSED.value: {WorkloadStatus.PUBLISHED.value, WorkloadStatus.TESTING.value, WorkloadStatus.RETIRED.value},
    WorkloadStatus.TEST_FAILED.value: {WorkloadStatus.TESTING.value, WorkloadStatus.DRAFT.value, WorkloadStatus.RETIRED.value},
    WorkloadStatus.PUBLISHED.value: {WorkloadStatus.RETIRED.value, WorkloadStatus.TESTING.value},
    WorkloadStatus.RETIRED.value: set(),
}

GATE_TEST_REQUIRED = "WORKLOAD_TEST_REQUIRED"
GATE_TEST_NOT_PASSED = "WORKLOAD_TEST_NOT_PASSED"
GATE_HASH_MISMATCH = "WORKLOAD_HASH_MISMATCH"
GATE_NOT_TEST_ENV = "WORKLOAD_NOT_TEST_ENV"
GATE_STATE_CONFLICT = "WORKLOAD_STATE_CONFLICT"


class WorkloadGateError(Exception):
    def __init__(self, code: str, message: str, detail: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail or {}


def hash_entrypoint(code: str) -> str:
    return sm3_hash((code or "").encode("utf-8"))


def resolve_sandbox_mode(contract, preferred: str) -> str:
    """在合约放行的模式里挑出会话执行档（返回的一定是 VALID_SANDBOX_MODES 里的值）。

    合约的 allowed_sandbox_modes 是授权集合，不是供我们挑选的候选列表：
    合约没放行 preferred 时直接 fail-closed，而不是默默换成另一个执行档。
    """
    from app.core.errors import CDSError
    from app.schemas.sandbox_session import SANDBOX_MODE_ALIASES, canonical_sandbox_mode

    wanted = canonical_sandbox_mode(preferred)
    if wanted is None:
        raise CDSError(
            f"不支持的沙箱模式 {preferred}",
            code="SANDBOX_MODE_UNSUPPORTED",
            http_status=400,
            detail={"preferred": preferred},
        )
    allowed = {str(m).strip().lower() for m in (getattr(contract, "allowed_sandbox_modes", None) or []) if str(m).strip()}
    if not allowed or allowed & SANDBOX_MODE_ALIASES[wanted]:
        return wanted
    raise CDSError(
        "合约未放行该沙箱模式，请先在合约的允许模式里加上对应项",
        code="SANDBOX_MODE_NOT_PERMITTED",
        http_status=400,
        detail={"preferred": wanted, "allowed_sandbox_modes": sorted(allowed)},
    )


def entrypoint_from_notebook(notebook_content: dict[str, Any] | None) -> str:
    return extract_entrypoint(notebook_content or {})


def assert_transition(current: str, target: str) -> None:
    if target not in ALLOWED_TRANSITIONS.get(current, set()):
        raise WorkloadGateError(
            GATE_STATE_CONFLICT,
            f"工作负载状态不允许从 {current} 转到 {target}",
            {"from": current, "to": target, "allowed": sorted(ALLOWED_TRANSITIONS.get(current, set()))},
        )


def evaluate_checks(checks: list[dict[str, Any]]) -> bool:
    if not checks:
        return False
    return all(bool(c.get("passed")) for c in checks)


async def get_production_head(db: AsyncSession, contract_id: uuid.UUID | None) -> Workload | None:
    stmt = select(Workload).where(Workload.env == WorkloadEnv.PRODUCTION.value, Workload.is_latest.is_(True))
    if contract_id is not None:
        stmt = stmt.where(Workload.contract_id == contract_id)
    res = await db.execute(stmt.order_by(Workload.published_at.desc()))
    return res.scalars().first()


async def list_production_versions(db: AsyncSession, contract_id: uuid.UUID | None) -> list[Workload]:
    stmt = select(Workload).where(Workload.env == WorkloadEnv.PRODUCTION.value)
    if contract_id is not None:
        stmt = stmt.where(Workload.contract_id == contract_id)
    res = await db.execute(stmt.order_by(Workload.version.desc()))
    return list(res.scalars().all())


async def run_test(db: AsyncSession, workload: Workload, actor, *, keep_session: bool = False, timeout_seconds: int = 900) -> WorkloadTestRun:
    """在测试沙箱里跑一遍 entrypoint，把结果落成不可绕过的测试记录。"""
    from app.api.sandbox_sessions import create_sandbox_session, execute_in_sandbox
    from app.schemas.sandbox_session import SandboxSessionCreate, SandboxExecuteRequest
    from app.services.session_lifecycle import terminate_session
    from app.services.session_state_machine import session_state_machine
    from app.models.contract import Contract
    from app.models.sandbox_session import SandboxSession

    run = WorkloadTestRun(workload_id=workload.id, triggered_by=getattr(actor, "id", None), status=TestRunStatus.RUNNING.value, started_at=datetime.now(timezone.utc))
    db.add(run)
    workload.status = WorkloadStatus.TESTING.value
    workload.test_run_id = None
    await db.flush()

    checks: list[dict[str, Any]] = []
    report: dict[str, Any] = {"env": WorkloadEnv.TEST.value, "entrypoint_hash": workload.entrypoint_hash}
    session_id: uuid.UUID | None = None
    try:
        if not workload.entrypoint or not workload.entrypoint.strip():
            raise ValueError("工作负载没有 entrypoint，无法测试")
        if not workload.data_product_ids:
            raise ValueError("工作负载未绑定数据产品，测试沙箱无法挂载数据")

        contract = await db.get(Contract, workload.contract_id) if workload.contract_id else None
        body = SandboxSessionCreate(
            data_product_id=uuid.UUID(str(workload.data_product_ids[0])),
            sandbox_level="l2c",
            sandbox_mode=resolve_sandbox_mode(contract, "product_dev"),
            contract_id=str(workload.contract_id) if workload.contract_id else None,
            timeout_seconds=max(600, int(timeout_seconds)),
            resource_limits=workload.resource_spec or None,
        )
        created = await create_sandbox_session(body=body, db=db, current_user=actor)
        session_id = created.id
        sess_res = await db.execute(select(SandboxSession).where(SandboxSession.id == session_id))
        sess = sess_res.scalar_one_or_none()
        if sess is not None:
            sess.env = WorkloadEnv.TEST.value
            sess.workload_id = workload.id
        run.session_id = session_id
        report["session_id"] = str(session_id)
        report["sandbox_level"] = getattr(created, "sandbox_level", None)
        await db.flush()

        exec_res = await execute_in_sandbox(
            session_id=session_id,
            body=SandboxExecuteRequest(code=workload.entrypoint, language=workload.language or "python"),
            db=db,
            current_user=actor,
        )
        exit_code = int(exec_res.get("exit_code", -1) if isinstance(exec_res, dict) else -1)
        stdout = str((exec_res or {}).get("output") or "")
        stderr = str((exec_res or {}).get("stderr") or "")
        blocked = bool((exec_res or {}).get("output_blocked"))
        run.exit_code = exit_code
        report["stdout_tail"] = stdout[-4000:]
        report["stderr_tail"] = stderr[-4000:]
        report["output_bytes"] = len(stdout.encode("utf-8"))

        checks.append({"name": "entrypoint_executed", "passed": exit_code == 0, "detail": f"exit_code={exit_code}"})
        checks.append({"name": "output_policy_clear", "passed": not blocked, "detail": (exec_res or {}).get("blocked_reason") or "输出策略未拦截"})
        checks.append({"name": "no_runtime_exception", "passed": "Traceback" not in stderr, "detail": "stderr 中无 Traceback" if "Traceback" not in stderr else "stderr 含 Traceback"})
    except Exception as exc:
        logger.warning("workload test run failed: %s", exc)
        run.error_message = str(exc)[:2000]
        checks.append({"name": "entrypoint_executed", "passed": False, "detail": str(exc)[:500]})
    finally:
        if session_id and not keep_session:
            try:
                sess_res = await db.execute(select(SandboxSession).where(SandboxSession.id == session_id))
                sess = sess_res.scalar_one_or_none()
                if sess is not None and session_state_machine.is_active(sess.status):
                    await terminate_session(sess, db, reason="SS-15 测试沙箱运行结束")
            except Exception as exc:
                logger.warning("test session cleanup failed (session=%s): %s", session_id, exc)

    passed = evaluate_checks(checks)
    run.checks = checks
    run.report = report
    run.passed = passed
    run.status = TestRunStatus.PASSED.value if passed else TestRunStatus.FAILED.value
    run.finished_at = datetime.now(timezone.utc)
    workload.status = WorkloadStatus.TEST_PASSED.value if passed else WorkloadStatus.TEST_FAILED.value
    workload.test_run_id = run.id
    await db.flush()
    return run


async def publish(db: AsyncSession, workload: Workload, actor_id: uuid.UUID | None) -> Workload:
    """把通过测试的测试态工作负载提升为生产态新版本。"""
    if workload.env != WorkloadEnv.TEST.value:
        raise WorkloadGateError(GATE_NOT_TEST_ENV, "只有测试沙箱的工作负载可以发布到生产", {"env": workload.env})
    if workload.status != WorkloadStatus.TEST_PASSED.value:
        raise WorkloadGateError(GATE_TEST_REQUIRED, "工作负载尚未通过测试沙箱验证", {"status": workload.status})
    if not workload.test_run_id:
        raise WorkloadGateError(GATE_TEST_REQUIRED, "缺少测试运行记录，无法发布")

    res = await db.execute(select(WorkloadTestRun).where(WorkloadTestRun.id == workload.test_run_id))
    run = res.scalar_one_or_none()
    if run is None or not run.passed:
        raise WorkloadGateError(GATE_TEST_NOT_PASSED, "关联的测试运行未通过", {"test_run_id": str(workload.test_run_id)})

    tested_hash = (run.report or {}).get("entrypoint_hash")
    if not tested_hash or tested_hash != workload.entrypoint_hash:
        raise WorkloadGateError(
            GATE_HASH_MISMATCH,
            "entrypoint 在测试之后被修改，必须重新测试",
            {"tested_hash": tested_hash, "current_hash": workload.entrypoint_hash},
        )

    now = datetime.now(timezone.utc)
    previous = await get_production_head(db, workload.contract_id)
    versions = await list_production_versions(db, workload.contract_id)
    next_version = (max((v.version for v in versions), default=0) + 1)
    for old in versions:
        old.is_latest = False

    prod = Workload(
        name=workload.name,
        user_id=workload.user_id,
        contract_id=workload.contract_id,
        source_notebook_id=workload.source_notebook_id,
        source_task_id=workload.source_task_id,
        entrypoint=workload.entrypoint,
        language=workload.language,
        entrypoint_hash=workload.entrypoint_hash,
        resource_spec=workload.resource_spec,
        data_product_ids=workload.data_product_ids,
        env=WorkloadEnv.PRODUCTION.value,
        status=WorkloadStatus.PUBLISHED.value,
        version=next_version,
        parent_id=workload.parent_id or workload.id,
        is_latest=True,
        test_run_id=workload.test_run_id,
        published_at=now,
        published_by=actor_id,
    )
    db.add(prod)
    workload.published_at = now
    await db.flush()
    logger.info("workload published contract=%s version=%s previous=%s", workload.contract_id, prod.version, previous.id if previous else None)
    return prod


async def retire(db: AsyncSession, workload: Workload) -> Workload:
    assert_transition(workload.status, WorkloadStatus.RETIRED.value)
    workload.status = WorkloadStatus.RETIRED.value
    if workload.env == WorkloadEnv.PRODUCTION.value and workload.is_latest:
        others = await list_production_versions(db, workload.contract_id)
        remaining = [w for w in others if w.status == WorkloadStatus.PUBLISHED.value and w.id != workload.id]
        if remaining:
            remaining.sort(key=lambda w: w.version, reverse=True)
            remaining[0].is_latest = True
    await db.flush()
    return workload


async def rollback(db: AsyncSession, workload: Workload) -> Workload:
    """把上一版本生产负载重新设为 latest（不删数据，只做版本指针切换）。"""
    if workload.env != WorkloadEnv.PRODUCTION.value:
        raise WorkloadGateError(GATE_NOT_TEST_ENV, "只有生产负载可以回滚", {"env": workload.env})
    versions = await list_production_versions(db, workload.contract_id)
    candidates = [w for w in versions if w.version < workload.version and w.status == WorkloadStatus.PUBLISHED.value]
    if not candidates:
        raise WorkloadGateError(GATE_STATE_CONFLICT, "没有可回滚的更早版本", {"versions": [w.version for w in versions]})
    candidates.sort(key=lambda w: w.version, reverse=True)
    target = candidates[0]
    for w in versions:
        w.is_latest = (w.id == target.id)
    await db.flush()
    return target
