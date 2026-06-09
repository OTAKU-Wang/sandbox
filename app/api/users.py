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


@router.get("/options", response_model=list[UserOptionResponse])
async def list_user_options(
    role: str | None = Query(None),
    q: str | None = Query(None, min_length=1, max_length=64),
    limit: int = Query(50, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return minimal active-user options for product workflows."""
    if role and role not in ALLOWED_OPTION_ROLES:
        raise HTTPException(status_code=400, detail="Invalid role")

    query = select(User).where(User.is_active.is_(True))
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
