"""Network Policy API — CRUD for sandbox network access control.

Zero-trust networking: default deny, explicit allowlist per session.
Supports IP CIDR ranges, domain names, and port restrictions.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import NETPOL_PENDING_KEY, get_db
from app.core.deps import get_current_user
from app.core.errors import CDSError
from app.models.sandbox_session import SandboxSession
from app.models.user import User, UserRole
from app.models.network_policy import NetworkPolicy
from app.schemas.network_policy import (
    NetworkPolicyCreate,
    NetworkPolicyUpdate,
    NetworkPolicyResponse,
)
from app.services.audit_service import audit_service
from app.services.network_policy import network_policy_engine

router = APIRouter()


def _can_view_all_network_policies(user: User) -> bool:
    return user.role in (UserRole.OPERATOR, UserRole.REGULATOR, UserRole.ADMIN)


def _can_operate_network_policies(user: User) -> bool:
    return user.role in (UserRole.OPERATOR, UserRole.ADMIN)


async def _get_authorized_session(
    db: AsyncSession,
    session_id: str,
    user: User,
    *,
    write: bool,
) -> SandboxSession:
    try:
        session_uuid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid session ID")
    result = await db.execute(select(SandboxSession).where(SandboxSession.id == session_uuid))
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Sandbox session not found")
    if session.user_id == user.id:
        return session
    if write and _can_operate_network_policies(user):
        return session
    if not write and _can_view_all_network_policies(user):
        return session
    raise HTTPException(status_code=403, detail="Not your session")


class NetworkPolicyCreateResponse(NetworkPolicyResponse):
    """POST 创建响应：在基线字段外如实披露强制面状态（A20b）。

    enforcement 只会是 "queued"：推送在 commit 成功后由 get_db drain 执行，
    路由不得在落库确认前谎称已生效。
    """

    enforcement: str = "queued"


def _queue_enforcement(db: AsyncSession, op: dict) -> None:
    """把强制面操作登记到请求会话，由 get_db 在 commit 成功后 drain（A20b）。

    登记而非直推：commit 失败时会话连同登记一起废弃，引擎永远收不到
    一条数据库里不存在的策略（A20 的记录侧/强制侧分叉）。
    """
    db.info.setdefault(NETPOL_PENDING_KEY, []).append(op)


class NetworkPolicyConflict(CDSError, HTTPException):
    """session_id 唯一约束冲突的 W2 领域错误（A20）。

    口径（照 `deliveries.py::_slug_conflict` 的 `ENDPOINT_SLUG_TAKEN`）：领域冲突用
    `CDSError(code=...)`，经 `app/core/error_handlers.py` 渲染成
    `{code, message, detail, request_id}`，而不是 `http_exception_handler` 兑底的
    `HTTP_409`（客户端只能按状态码猜）。Starlette 按 `type(exc).__mro__` 选处理器，
    CDSError 排在 HTTPException 之前，语义码不会被兑底覆盖。

    同时保留 HTTPException 身份是刻意向后兼容：既有的 `except HTTPException`
    （含兄弟轨持有的 `tests/test_ocr_a20_network_policy_unique_race.py`）与 legacy
    客户端继续成立；这些捕获点统一到 CDSError 后，删掉 HTTPException 这个基类即可。
    """

    code = "NETWORK_POLICY_EXISTS"
    http_status = 409

    def __init__(self, session_id: str) -> None:
        message = f"Network policy already exists for session {session_id}"
        # 不调 CDSError.__init__：它的 `super().__init__(message)` 沿 MRO 会落进
        # HTTPException.__init__，把非整数当 status_code 去构造 http.HTTPStatus 直接 ValueError。
        Exception.__init__(self, message)
        self.message = message
        self.detail = {"session_id": session_id}
        self.retry_after = None
        self.status_code = self.http_status  # legacy 捕获点读的字段
        self.headers = None


@router.post("", response_model=NetworkPolicyCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_network_policy(
    body: NetworkPolicyCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a network policy for a sandbox session."""
    session = await _get_authorized_session(db, body.session_id, current_user, write=True)
    # Check if policy already exists for this session
    existing = await db.execute(
        select(NetworkPolicy).where(NetworkPolicy.session_id == body.session_id)
    )
    if existing.scalar_one_or_none():
        raise NetworkPolicyConflict(body.session_id)

    policy = NetworkPolicy(
        session_id=body.session_id,
        user_id=str(session.user_id),
        mode=body.mode,
        allowed_ips=body.allowed_ips,
        allowed_domains=body.allowed_domains,
        allowed_ports=body.allowed_ports,
        dns_proxy_enabled=body.dns_proxy_enabled,
        max_connections_per_second=body.max_connections_per_second,
        max_bandwidth_bytes_per_second=body.max_bandwidth_bytes_per_second,
    )
    db.add(policy)
    try:
        async with db.begin_nested():
            await db.flush()
    except IntegrityError:
        # 保存点只回滚这条 INSERT；处理器不得 rollback 请求事务（请求尾由 get_db 统一回滚）。
        # 对外与先查分支同一个领域码，不是 500。
        raise NetworkPolicyConflict(body.session_id)
    await db.refresh(policy)

    # A20b（方案 A）：路由不再在 commit 前触碰强制面 —— 只登记，get_db 在
    # commit 成功后 drain 再推；响应如实披露 enforcement="queued"。
    from app.services.network_policy import NetworkPolicyConfig
    _queue_enforcement(db, {
        "action": "apply",
        "session_id": body.session_id,
        "user_id": str(current_user.id),
        "config": NetworkPolicyConfig(
            mode=policy.mode,
            allowed_ips=policy.allowed_ips or [],
            allowed_domains=policy.allowed_domains or [],
            allowed_ports=policy.allowed_ports or [],
            dns_proxy_enabled=policy.dns_proxy_enabled,
            max_connections_per_second=policy.max_connections_per_second,
            max_bandwidth_bytes_per_second=policy.max_bandwidth_bytes_per_second,
        ),
    })

    await audit_service.log(
        db,
        action="network_policy.create",
        resource_type="network_policy",
        user_id=current_user.id,
        session_id=body.session_id,
        detail={"mode": body.mode, "allowed_ips": body.allowed_ips, "allowed_domains": body.allowed_domains, "enforcement": "queued"},
    )

    resp = NetworkPolicyCreateResponse.model_validate(policy)
    return resp


@router.get("", response_model=dict)
async def list_network_policies(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List network policies for the current user."""
    base = select(NetworkPolicy).where(NetworkPolicy.user_id == str(current_user.id))
    count_base = select(func.count()).select_from(NetworkPolicy).where(
        NetworkPolicy.user_id == str(current_user.id)
    )

    total_result = await db.execute(count_base)
    total = total_result.scalar() or 0

    result = await db.execute(base.offset(skip).limit(limit))
    items = [NetworkPolicyResponse.model_validate(p) for p in result.scalars().all()]
    return {"items": items, "total": total, "page": skip // limit + 1, "page_size": limit}


@router.get("/session/{session_id}", response_model=NetworkPolicyResponse)
async def get_policy_by_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get network policy for a specific sandbox session."""
    await _get_authorized_session(db, session_id, current_user, write=False)
    result = await db.execute(
        select(NetworkPolicy).where(NetworkPolicy.session_id == session_id)
    )
    policy = result.scalar_one_or_none()
    if not policy:
        raise HTTPException(status_code=404, detail="No network policy for this session")
    return NetworkPolicyResponse.model_validate(policy)


@router.get("/session/{session_id}/egress-audit")
async def get_session_egress_audit(
    session_id: str,
    limit: int = Query(50, ge=1, le=500),
    since: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Tail the W13 egress audit JSONL for one session (newest first)."""
    from app.services.egress_audit import read_egress_records

    await _get_authorized_session(db, session_id, current_user, write=False)
    records = read_egress_records(session_id, limit=limit, since=since)
    return {"session_id": session_id, "records": records, "count": len(records)}


@router.get("/{policy_id}", response_model=NetworkPolicyResponse)
async def get_network_policy(
    policy_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get a specific network policy."""
    result = await db.execute(select(NetworkPolicy).where(NetworkPolicy.id == policy_id))
    policy = result.scalar_one_or_none()
    if not policy:
        raise HTTPException(status_code=404, detail="Network policy not found")
    if policy.user_id != str(current_user.id) and not _can_view_all_network_policies(current_user):
        raise HTTPException(status_code=403, detail="Not your policy")
    return NetworkPolicyResponse.model_validate(policy)


@router.patch("/{policy_id}", response_model=NetworkPolicyResponse)
async def update_network_policy(
    policy_id: uuid.UUID,
    body: NetworkPolicyUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update a network policy (hot-reload enforcement).

    A20 口径 = 先提交、后下发：本函数只改记录面并把 remove+apply 登记给
    `_queue_enforcement`，由 get_db 在 commit 成功后按序 drain。原实现先
    `remove_policy` 再 `create_policy`，而事务要到请求尾才提交 —— 提交失败时旧策略
    还在库里、运行面已被拆成空白（零信任默认静默失效）。
    没选 A36（sandbox_sessions 的 PUT）那种请求内补偿：补偿仍会先于 commit 拆掉旧
    策略，那个窗口内两侧就是分叉的；本文件三条写入口统一走 drain。
    """
    result = await db.execute(select(NetworkPolicy).where(NetworkPolicy.id == policy_id))
    policy = result.scalar_one_or_none()
    if not policy:
        raise HTTPException(status_code=404, detail="Network policy not found")
    if policy.user_id != str(current_user.id) and not _can_operate_network_policies(current_user):
        raise HTTPException(status_code=403, detail="Not your policy")

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(policy, field, value)

    await db.flush()
    await db.refresh(policy)

    # A20: 变更登记，commit 成功后才真去碰强制面（顺序与原来的热加载一致：先拆后装）
    _queue_enforcement(db, {
        "action": "remove",
        "session_id": policy.session_id,
        "user_id": str(current_user.id),
    })
    if policy.active:
        from app.services.network_policy import NetworkPolicyConfig
        config = NetworkPolicyConfig(
            mode=policy.mode,
            allowed_ips=policy.allowed_ips or [],
            allowed_domains=policy.allowed_domains or [],
            allowed_ports=policy.allowed_ports or [],
            dns_proxy_enabled=policy.dns_proxy_enabled,
            max_connections_per_second=policy.max_connections_per_second,
            max_bandwidth_bytes_per_second=policy.max_bandwidth_bytes_per_second,
        )
        _queue_enforcement(db, {
            "action": "apply",
            "session_id": policy.session_id,
            "user_id": str(current_user.id),
            "config": config,
        })

    return NetworkPolicyResponse.model_validate(policy)


@router.delete("/{policy_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_network_policy(
    policy_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete a network policy (removes enforcement after the row is committed).

    A20: 撤销强制面同样登记到 commit 之后 —— 先拆后提交失败会留下「库里有策略、
    运行面已放行」的静默降级。
    """
    result = await db.execute(select(NetworkPolicy).where(NetworkPolicy.id == policy_id))
    policy = result.scalar_one_or_none()
    if not policy:
        raise HTTPException(status_code=404, detail="Network policy not found")
    if policy.user_id != str(current_user.id) and not _can_operate_network_policies(current_user):
        raise HTTPException(status_code=403, detail="Not your policy")

    # A20: 登记而非直推（drain 在 commit 成功之后）
    _queue_enforcement(db, {
        "action": "remove",
        "session_id": policy.session_id,
        "user_id": str(current_user.id),
    })

    await db.delete(policy)
    await audit_service.log(
        db,
        action="network_policy.delete",
        resource_type="network_policy",
        user_id=current_user.id,
        session_id=policy.session_id,
    )
