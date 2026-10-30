from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.user import User, UserRole
from app.schemas.user import UserOptionResponse

router = APIRouter()

ALLOWED_OPTION_ROLES = {
    UserRole.DATA_PROVIDER,
    UserRole.BUYER,
    UserRole.OPERATOR,
    UserRole.REGULATOR,
    UserRole.ADMIN,
}

# A16: 名册可见性分级。本端点的产品用途是“选人”（数商建合约按 role=buyer 挑买方、
# 任务详情页解析组织名），而不是全平台通讯录：平台侧账号（运营/监管/管理）
# 的用户名+组织+角色只对平台侧角色开放，否则任意登录账号可拉出含管理员的
# 全平台名册供定向社工/找靶。未知角色按最小权限处理。
PLATFORM_SIDE_ROLES = {
    UserRole.OPERATOR,
    UserRole.REGULATOR,
    UserRole.ADMIN,
}
COUNTERPARTY_ROLES = {
    UserRole.DATA_PROVIDER,
    UserRole.BUYER,
}


def _directory_visible_roles(caller_role: str) -> set[str] | None:
    """None = 可枚举全平台名册；否则为可见角色集合（空集 = 名册不可读）。"""
    if caller_role in PLATFORM_SIDE_ROLES:
        return None
    return set(COUNTERPARTY_ROLES)


@router.get("/options", response_model=list[UserOptionResponse])
async def list_user_options(
    role: str | None = Query(None),
    q: str | None = Query(None, min_length=1, max_length=64),
    limit: int = Query(50, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return minimal active-user options for product workflows.

    A16: 业务主体（数商/买方）的可见集被裁到交易对手角色；平台侧角色的名册
    仅对运营/监管/管理员开放。
    """
    if role and role not in ALLOWED_OPTION_ROLES:
        raise HTTPException(status_code=400, detail="Invalid role")

    visible = _directory_visible_roles(current_user.role)
    if role and visible is not None and role not in visible:
        raise HTTPException(
            status_code=403,
            detail="该角色的名册仅限运营方/监管方/管理员账号查看",
        )

    query = select(User).where(User.is_active.is_(True))
    if visible is not None:
        query = query.where(User.role.in_(sorted(visible)))
    if role:
        query = query.where(User.role == role)

    if q:
        pattern = f"%{q.strip()}%"
        query = query.where(
            or_(
                User.username.ilike(pattern),
                User.organization.ilike(pattern),
            )
        )

    result = await db.execute(query.order_by(User.organization, User.username).limit(limit))
    return [UserOptionResponse.model_validate(user) for user in result.scalars().all()]
