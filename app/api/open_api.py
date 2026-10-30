import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_business_user
from app.core.errors import CDSError
from app.models.api_endpoint import ApiEndpoint
from app.models.contract import Contract
from app.models.user import User
from app.models.workload import Workload
from app.services import delivery_service as dv
from app.services.audit_service import audit_service

logger = logging.getLogger(__name__)

router = APIRouter()

_RETRYABLE = {"RATE_LIMITED", "QUOTA_EXCEEDED"}


@router.post("/{slug}")
async def call_open_endpoint(
    slug: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_business_user),
):
    # A168: 配额 calls_used 的门禁判（delivery_service.execute_endpoint 头部）与自增
    # （同函数尾部）之间隔着整次沙箱执行，无锁装载＝两个并发请求可从同一值出发
    # 双双过闸并互相覆盖计数（配额少计、可超用）。台账修法一：装载行时加行锁，
    # PG 上锁持续到请求事务提交，「判+增」整体串行化；SQLite 方言开不开行锁，
    # 本地/CI 行为不变。
    res = await db.execute(
        select(ApiEndpoint).where(ApiEndpoint.slug == slug).with_for_update()
    )
    endpoint = res.scalar_one_or_none()
    if endpoint is None:
        raise HTTPException(status_code=404, detail="endpoint not found")

    cres = await db.execute(select(Contract).where(Contract.id == endpoint.contract_id))
    contract = cres.scalar_one_or_none()
    if contract is None:
        raise HTTPException(status_code=404, detail="contract not found")
    if endpoint.owner_id != current_user.id and contract.buyer_id != current_user.id and getattr(current_user, "role", None) not in ("admin", "platform_admin"):
        raise CDSError("该 API Key 无权调用此端点", code="FORBIDDEN", http_status=403, detail={"slug": slug})

    raw = await request.body()
    try:
        body = json.loads(raw.decode("utf-8")) if raw else {}
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CDSError("请求体必须是 JSON 对象", code="INVALID_REQUEST_BODY", http_status=400, detail={"error": str(exc)[:200]}) from exc
    if not isinstance(body, dict):
        raise CDSError("请求体必须是 JSON 对象", code="INVALID_REQUEST_BODY", http_status=400)

    wres = await db.execute(select(Workload).where(Workload.id == endpoint.workload_id))
    workload = wres.scalar_one_or_none()

    try:
        result = await dv.execute_endpoint(db, endpoint, body, caller_id=current_user.id, workload=workload)
    except dv.EndpointUnavailable as exc:
        # A29: 拒绝/失败类审计按本仓契约走 log_standalone（见其 docstring 与
        # app/api/AGENTS.md）：旧写法 log(db)+commit 在审计一失败时会把正确的
        # 429/502 变成裸 500，且 raise 路径下 get_db 回滚请求会话有吞审计风险。
        # log_standalone 永不向上抛 ⇒ 拒访答案与 Retry-After 必达客户端。
        await audit_service.log_standalone(
            action="openapi.call_failed", resource_type="api_endpoint",
            user_id=current_user.id, resource_id=str(endpoint.id),
            ip_address=request.client.host if request.client else None,
            detail={"contract_id": str(contract.id), "endpoint_id": str(endpoint.id), "slug": slug, "code": exc.code, "message": exc.message},
        )
        status = 429 if exc.code in _RETRYABLE else 502
        raise CDSError(exc.message, code=exc.code, http_status=status, detail=exc.detail or None,
                       retry_after=60 if exc.code == "RATE_LIMITED" else None) from exc

    # 调用明细按被调端点归源（resource=api_endpoint，与 api_keys/training 的资源级审计同口径），
    # 合约留在 detail 里：交付中心的「端点调用明细」要精确分页，不能靠解 JSON 过滤。
    # A29: 沙箱此刻已真实执行（calls_used/计量是既成事实），审计行写入失败不得
    # 连带回滚业务提交（客户端对 5xx 重试会再真实执行一次）。审计用 SAVEPOINT
    # 隔离：写失败只回滚审计保存点，降级为 [CRITICAL] 日志 + log_standalone 补写
    # （honest degradation），业务提交照常；正常路径审计仍与业务同事务落库
    # （app/api/AGENTS.md 成功路径口径）。
    audit_detail = {"contract_id": str(contract.id), "endpoint_id": str(endpoint.id), "slug": slug, "output_rows": result["output_rows"], "duration_ms": result["duration_ms"], "request_sha256": result["request_sha256"]}
    try:
        async with db.begin_nested():
            await audit_service.log(
                db, action="openapi.call", resource_type="api_endpoint",
                user_id=current_user.id, resource_id=str(endpoint.id),
                ip_address=request.client.host if request.client else None,
                detail=audit_detail,
            )
    except Exception as exc:
        logger.error(
            "[CRITICAL] openapi.call audit write failed for endpoint=%s — "
            "metering commit continues, falling back to standalone write: %s",
            endpoint.id, exc,
        )
        await audit_service.log_standalone(
            action="openapi.call", resource_type="api_endpoint",
            user_id=current_user.id, resource_id=str(endpoint.id),
            ip_address=request.client.host if request.client else None,
            detail=audit_detail,
        )
    await db.commit()
    return result
