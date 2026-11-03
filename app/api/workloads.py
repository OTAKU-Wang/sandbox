import base64
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_business_user
from app.core.errors import CDSError
from app.models.api_endpoint import ApiEndpoint, ApiEndpointStatus
from app.models.contract import Contract, WorkloadEnv
from app.models.notebook import Notebook
from app.models.user import User
from app.models.workload import Workload, WorkloadStatus
from app.models.workload_test_run import WorkloadTestRun
from app.schemas.flow import ArtifactScanRequest, WorkloadCreate, WorkloadOut, WorkloadTestRequest
from app.services import workflow_service as wf
from app.services.audit_service import audit_service
from app.services.code_scanner import scan_model_artifact_bytes

router = APIRouter()


def _gate_error(exc: wf.WorkloadGateError) -> CDSError:
    return CDSError(exc.message, code=exc.code, http_status=412, detail=exc.detail)


async def _load_workload(db: AsyncSession, workload_id: uuid.UUID, user: User) -> Workload:
    res = await db.execute(select(Workload).where(Workload.id == workload_id))
    workload = res.scalar_one_or_none()
    if workload is None:
        raise HTTPException(status_code=404, detail="workload not found")
    if workload.user_id != user.id and getattr(user, "role", None) not in ("admin", "platform_admin"):
        raise HTTPException(status_code=403, detail="not your workload")
    return workload


async def _contract_products(db: AsyncSession, contract_id: uuid.UUID | None) -> list[str]:
    if contract_id is None:
        return []
    res = await db.execute(select(Contract).where(Contract.id == contract_id))
    contract = res.scalar_one_or_none()
    return [str(p) for p in (contract.product_ids if contract and contract.product_ids else [])]


@router.post("", response_model=WorkloadOut, status_code=201)
async def create_workload(
    body: WorkloadCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_business_user),
):
    entrypoint = body.entrypoint
    notebook = None
    if body.notebook_id is not None:
        nres = await db.execute(select(Notebook).where(Notebook.id == body.notebook_id))
        notebook = nres.scalar_one_or_none()
        if notebook is None:
            raise HTTPException(status_code=404, detail="notebook not found")
        if notebook.user_id != current_user.id and getattr(current_user, "role", None) not in ("admin", "platform_admin"):
            raise HTTPException(status_code=403, detail="not your notebook")
        if not entrypoint:
            entrypoint = wf.entrypoint_from_notebook(notebook.content or {})
    if not entrypoint or not entrypoint.strip():
        raise HTTPException(status_code=400, detail="工作负载缺少 entrypoint：请提供代码或由包含代码单元格的 Notebook 生成")

    contract_id = body.contract_id or (notebook.contract_id if notebook else None)
    product_ids = [str(p) for p in (body.data_product_ids or [])] or await _contract_products(db, contract_id)

    workload = Workload(
        name=body.name,
        user_id=current_user.id,
        contract_id=contract_id,
        source_notebook_id=body.notebook_id,
        entrypoint=entrypoint,
        language=body.language,
        entrypoint_hash=wf.hash_entrypoint(entrypoint),
        resource_spec=(body.resource_spec.model_dump(exclude_none=True) if body.resource_spec else None),
        data_product_ids=product_ids,
        env=WorkloadEnv.TEST.value,
        status=WorkloadStatus.DRAFT.value,
    )
    db.add(workload)
    await db.flush()
    await audit_service.log(
        db, action="workload.created", resource_type="workload",
        user_id=current_user.id, resource_id=str(workload.id),
        detail={"name": body.name, "entrypoint_hash": workload.entrypoint_hash, "contract_id": str(contract_id) if contract_id else None},
    )
    await db.refresh(workload)
    return workload


@router.get("")
async def list_workloads(
    contract_id: uuid.UUID | None = None,
    env: str | None = Query(None, pattern="^(test|production)$"),
    status_filter: str | None = Query(None, alias="status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_business_user),
):
    stmt = select(Workload).where(Workload.user_id == current_user.id)
    if contract_id is not None:
        stmt = stmt.where(Workload.contract_id == contract_id)
    if env:
        stmt = stmt.where(Workload.env == env)
    if status_filter:
        stmt = stmt.where(Workload.status == status_filter)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    res = await db.execute(stmt.order_by(Workload.created_at.desc()).offset((page - 1) * page_size).limit(page_size))
    return {"items": [WorkloadOut.model_validate(w) for w in res.scalars().all()], "total": total, "page": page, "page_size": page_size}


@router.get("/metrics-compare")
async def metrics_compare(
    workload_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_business_user),
):
    """多版本评估指标对比（Task 9）：沿 parent_id lineage 逐版本取最近一次测试 run。

    指标的家是 ``WorkloadTestRun.report["metrics"]``（代码实测，计划草案未定义存储位）；
    只比较 ``{str: number}`` 型指标，非数值键不参与。无 metrics 的历史 run 如实返回
    ``metrics: null``（不参与 common_metrics，也不被当作 0）。
    路由必须声明在 ``/{workload_id}`` 之前：否则 ``metrics-compare`` 会被 UUID
    路径参数先匹配吃成 422。
    """
    wl = await _load_workload(db, workload_id, current_user)

    root = wl
    guard = 0
    while root.parent_id is not None and guard < 64:
        pres = await db.execute(select(Workload).where(Workload.id == root.parent_id))
        parent = pres.scalar_one_or_none()
        if parent is None:
            break
        root = parent
        guard += 1

    members: dict[uuid.UUID, Workload] = {root.id: root}
    frontier = [root]
    while frontier:
        cur = frontier.pop()
        cres = await db.execute(select(Workload).where(Workload.parent_id == cur.id))
        for child in cres.scalars().all():
            if child.id not in members:
                members[child.id] = child
                frontier.append(child)

    items: list[dict[str, Any]] = []
    common: set[str] | None = None
    prev_metrics: dict[str, float] | None = None
    for member in sorted(members.values(), key=lambda w: (w.version, w.created_at or 0)):
        rres = await db.execute(
            select(WorkloadTestRun)
            .where(WorkloadTestRun.workload_id == member.id)
            .order_by(WorkloadTestRun.created_at.desc(), WorkloadTestRun.id.desc())
            .limit(1)
        )
        run = rres.scalar_one_or_none()
        raw = (run.report or {}).get("metrics") if run else None
        metrics: dict[str, float] | None = None
        if isinstance(raw, dict):
            metrics = {
                k: float(v)
                for k, v in raw.items()
                if isinstance(v, (int, float)) and not isinstance(v, bool)
            }
        if metrics is not None:
            common = set(metrics) if common is None else (common & set(metrics))
        delta: dict[str, float] | None = None
        if metrics is not None and prev_metrics is not None:
            delta = {k: round(metrics[k] - prev_metrics[k], 10) for k in metrics if k in prev_metrics}
        if metrics is not None:
            prev_metrics = metrics
        items.append(
            {
                "workload_id": str(member.id),
                "name": member.name,
                "version": member.version,
                "status": member.status,
                "is_latest": member.is_latest,
                "test_run_id": str(run.id) if run else None,
                "passed": run.passed if run else None,
                "metrics": metrics,
                "delta_vs_previous": delta,
                "created_at": member.created_at,
            }
        )
    return {
        "lineage_root": str(root.id),
        "common_metrics": sorted(common or []),
        "items": items,
    }


@router.post("/{workload_id}/artifact-scan")
async def scan_workload_artifact(
    workload_id: uuid.UUID,
    body: ArtifactScanRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_business_user),
):
    """发布前模型工件扫描（Task 9）：opcode 级审计，不执行内容、不落盘。

    判定不改变工作负载状态机——扫描是留痕型建议动作（passed=false 不阻断 draft，
    但审计必落库，发布门禁如将来要消费此结论需另开闸，不隐式耦合）。
    """
    workload = await _load_workload(db, workload_id, current_user)
    try:
        blob = base64.b64decode(body.content_b64.encode("ascii"), validate=True)
    except Exception:
        raise HTTPException(status_code=400, detail="content_b64 不是合法 base64")
    # 32 MiB 上限：扫描器全量驻内存，入口不控就会被单个大文件拖垮请求 worker
    if len(blob) > 32 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="工件超过 32 MiB 扫描上限")
    result = scan_model_artifact_bytes(blob, filename=body.filename)
    await audit_service.log(
        db, action="workload.artifact_scan", resource_type="workload",
        user_id=current_user.id, resource_id=str(workload.id),
        detail={
            "filename": body.filename,
            "format": result["format"],
            "size_bytes": result["size_bytes"],
            "passed": result["passed"],
            "violation_count": len(result["violations"]),
            "violations": result["violations"][:20],
        },
    )
    return result


@router.get("/{workload_id}", response_model=WorkloadOut)
async def get_workload(workload_id: uuid.UUID, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_business_user)):
    return await _load_workload(db, workload_id, current_user)


@router.get("/{workload_id}/test-runs")
async def list_test_runs(
    workload_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_business_user),
):
    await _load_workload(db, workload_id, current_user)
    stmt = select(WorkloadTestRun).where(WorkloadTestRun.workload_id == workload_id)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    res = await db.execute(stmt.order_by(WorkloadTestRun.created_at.desc()).offset((page - 1) * page_size).limit(page_size))
    runs = list(res.scalars().all())
    items: list[dict[str, Any]] = []
    for run in runs:
        items.append(
            {
                "id": str(run.id),
                "workload_id": str(run.workload_id),
                "session_id": str(run.session_id) if run.session_id else None,
                "status": run.status,
                "passed": run.passed,
                "checks": run.checks,
                "exit_code": run.exit_code,
                "error_message": run.error_message,
                "entrypoint_hash": (run.report or {}).get("entrypoint_hash"),
                "started_at": run.started_at,
                "finished_at": run.finished_at,
                "created_at": run.created_at,
            }
        )
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.post("/{workload_id}/test")
async def test_workload(
    workload_id: uuid.UUID,
    body: WorkloadTestRequest | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_business_user),
):
    body = body or WorkloadTestRequest()
    workload = await _load_workload(db, workload_id, current_user)
    run = await wf.run_test(db, workload, current_user, keep_session=body.keep_session, timeout_seconds=body.timeout_seconds)
    await audit_service.log(
        db, action="workload.test_finished", resource_type="workload",
        user_id=current_user.id, resource_id=str(workload.id),
        detail={"test_run_id": str(run.id), "passed": run.passed, "status": run.status, "checks": [c.get("name") for c in (run.checks or [])]},
    )
    from app.services.delivery_service import dispatch_event

    await dispatch_event(
        db,
        "workload.test_finished",
        {"workload_id": str(workload.id), "workload_name": workload.name, "test_run_id": str(run.id), "passed": run.passed, "checks": run.checks},
        user_id=workload.user_id,
        contract_id=workload.contract_id,
    )
    return {
        "test_run_id": str(run.id),
        "status": run.status,
        "passed": run.passed,
        "checks": run.checks,
        "session_id": str(run.session_id) if run.session_id else None,
        "error_message": run.error_message,
    }


@router.post("/{workload_id}/publish", response_model=WorkloadOut)
async def publish_workload(workload_id: uuid.UUID, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_business_user)):
    workload = await _load_workload(db, workload_id, current_user)
    try:
        prod = await wf.publish(db, workload, current_user.id)
    except wf.WorkloadGateError as exc:
        raise _gate_error(exc) from exc
    await audit_service.log(
        db, action="workload.published", resource_type="workload",
        user_id=current_user.id, resource_id=str(prod.id),
        detail={"version": prod.version, "entrypoint_hash": prod.entrypoint_hash, "contract_id": str(prod.contract_id) if prod.contract_id else None},
    )
    from app.services.delivery_service import dispatch_event

    await dispatch_event(
        db,
        "workload.published",
        {"workload_id": str(prod.id), "name": prod.name, "version": prod.version, "entrypoint_hash": prod.entrypoint_hash, "contract_id": str(prod.contract_id) if prod.contract_id else None},
        user_id=prod.user_id,
        contract_id=prod.contract_id,
    )
    await db.refresh(prod)
    return prod


@router.post("/{workload_id}/retire", response_model=WorkloadOut)
async def retire_workload(workload_id: uuid.UUID, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_business_user)):
    workload = await _load_workload(db, workload_id, current_user)
    try:
        await wf.retire(db, workload)
    except wf.WorkloadGateError as exc:
        raise _gate_error(exc) from exc
    stale = await db.execute(
        select(ApiEndpoint).where(ApiEndpoint.workload_id == workload.id, ApiEndpoint.status == ApiEndpointStatus.ENABLED.value)
    )
    for endpoint in stale.scalars().all():
        endpoint.status = ApiEndpointStatus.DISABLED.value
    await audit_service.log(db, action="workload.retired", resource_type="workload", user_id=current_user.id, resource_id=str(workload.id), detail={"env": workload.env})
    await db.refresh(workload)
    return workload


@router.post("/{workload_id}/rollback", response_model=WorkloadOut)
async def rollback_workload(workload_id: uuid.UUID, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_business_user)):
    workload = await _load_workload(db, workload_id, current_user)
    try:
        target = await wf.rollback(db, workload)
    except wf.WorkloadGateError as exc:
        raise _gate_error(exc) from exc
    await audit_service.log(
        db, action="workload.rolled_back", resource_type="workload",
        user_id=current_user.id, resource_id=str(target.id),
        detail={"to_version": target.version, "from_workload_id": str(workload.id)},
    )
    await db.refresh(target)
    return target


@router.get("/{workload_id}/test-runs/{run_id}")
async def get_test_run(workload_id: uuid.UUID, run_id: uuid.UUID, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_business_user)):
    await _load_workload(db, workload_id, current_user)
    res = await db.execute(select(WorkloadTestRun).where(WorkloadTestRun.id == run_id, WorkloadTestRun.workload_id == workload_id))
    run = res.scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="test run not found")
    return {
        "id": str(run.id),
        "workload_id": str(run.workload_id),
        "session_id": str(run.session_id) if run.session_id else None,
        "status": run.status,
        "passed": run.passed,
        "checks": run.checks,
        "report": run.report,
        "exit_code": run.exit_code,
        "error_message": run.error_message,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
    }
