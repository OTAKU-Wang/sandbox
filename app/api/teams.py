"""Team management & team-level quota isolation (`/api/v1/users/teams`).

Mounted in app/main.py BEFORE users.router, prefix `/api/v1/users/teams`, with the
human-only gate (`require_human`): teams are an organisational/quota construct, so a
platform API key must not mint or reshape one (SS-12 has no scope group for it).

Authority split: platform side (admin/operator) creates teams, sets the CPU/memory/
storage quotas and owns the member roster; a business account may only read the teams
it belongs to. A team is the quota boundary the scheduler will consume, so an
enumerable team list would leak other tenants' staffing — non-members get an empty
list and a 404 (not 403) on a specific id, which keeps existence unprovable.

Members live in `Team.members` as a JSON snapshot (see app/models/team.py — no
separate roster table by design), so every mutation rebuilds the list and reassigns
the attribute: a plain JSON column is not mutable-tracked, and mutating in place is a
silently lost write.
"""
import logging
import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user, require_roles
from app.models.team import Team, TeamRole
from app.models.user import User, UserRole
from app.services.audit_service import audit_service

router = APIRouter()

logger = logging.getLogger(__name__)

TEAM_ROLE_VALUES = {role.value for role in TeamRole}


class TeamMember(BaseModel):
    user_id: uuid.UUID
    username: str | None = None
    role: str
    permissions: list[str] = Field(default_factory=list)
    added_at: datetime | None = None
    added_by: uuid.UUID | None = None


class TeamResponse(BaseModel):
    id: uuid.UUID
    name: str
    org_id: str | None
    description: str | None
    status: str
    cpu_quota_cores: int
    memory_quota_gb: int
    storage_quota_gb: int
    members: list[TeamMember]
    member_count: int
    role_permissions: dict[str, list[str]]
    created_by: uuid.UUID
    created_at: datetime | None
    updated_at: datetime | None


class TeamListResponse(BaseModel):
    items: list[TeamResponse]
    total: int
    page: int
    page_size: int


class TeamCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    org_id: str | None = Field(default=None, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    cpu_quota_cores: int = Field(default=16, ge=1, le=4096)
    memory_quota_gb: int = Field(default=64, ge=1, le=16384)
    storage_quota_gb: int = Field(default=500, ge=1, le=1_048_576)
    role_permissions: dict[str, list[str]] = Field(default_factory=dict)

    @field_validator("role_permissions")
    @classmethod
    def _roles_known(cls, value: dict[str, list[str]]) -> dict[str, list[str]]:
        unknown = sorted(set(value) - TEAM_ROLE_VALUES)
        if unknown:
            raise ValueError(f"unknown team role(s): {', '.join(unknown)}; allowed: {sorted(TEAM_ROLE_VALUES)}")
        return value


class TeamPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    org_id: str | None = Field(default=None, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    status: Literal["active", "archived", "suspended"] | None = None
    cpu_quota_cores: int | None = Field(default=None, ge=1, le=4096)
    memory_quota_gb: int | None = Field(default=None, ge=1, le=16384)
    storage_quota_gb: int | None = Field(default=None, ge=1, le=1_048_576)
    role_permissions: dict[str, list[str]] | None = None

    @field_validator("role_permissions")
    @classmethod
    def _roles_known(cls, value: dict[str, list[str]] | None) -> dict[str, list[str]] | None:
        if value is None:
            return value
        unknown = sorted(set(value) - TEAM_ROLE_VALUES)
        if unknown:
            raise ValueError(f"unknown team role(s): {', '.join(unknown)}; allowed: {sorted(TEAM_ROLE_VALUES)}")
        return value


class TeamMemberUpsert(BaseModel):
    user_id: uuid.UUID
    role: str = Field(default=TeamRole.VIEWER.value, max_length=32)
    permissions: list[str] | None = Field(default=None, max_length=32)

    @field_validator("role")
    @classmethod
    def _role_known(cls, value: str) -> str:
        if value not in TEAM_ROLE_VALUES:
            raise ValueError(f"unknown team role: {value}; allowed: {sorted(TEAM_ROLE_VALUES)}")
        return value


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _roster(value: object) -> list[dict[str, object]]:
    """JSON snapshot → member dicts. A non-list snapshot is disclosed, never guessed at."""
    if value is None:
        return []
    if not isinstance(value, list):
        logger.warning("[teams] team roster snapshot is %s, not a list — treated as no members", type(value).__name__)
        return []
    return [entry for entry in value if isinstance(entry, dict)]


def _member_snapshot(team: Team) -> list[dict[str, object]]:
    """Detached copy of the roster — the JSON column is not mutable-tracked."""
    return [dict(member) for member in _roster(team.members)]


def _as_uuid(value: object) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        return None


def _as_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None


def _as_str_list(value: object) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []


def _role_permissions(value: object) -> dict[str, list[str]]:
    if not isinstance(value, dict):
        return {}
    return {str(key): _as_str_list(items) for key, items in value.items()}


def _member_models(raw: list[dict[str, object]], usernames: dict[uuid.UUID, str]) -> list[TeamMember]:
    members: list[TeamMember] = []
    for entry in raw:
        member_id = _as_uuid(entry.get("user_id"))
        if member_id is None:
            continue
        members.append(
            TeamMember(
                user_id=member_id,
                username=usernames.get(member_id),
                role=str(entry.get("role") or TeamRole.VIEWER.value),
                permissions=_as_str_list(entry.get("permissions")),
                added_at=_as_datetime(entry.get("added_at")),
                added_by=_as_uuid(entry.get("added_by")),
            )
        )
    return members


def _team_response(team: Team, usernames: dict[uuid.UUID, str]) -> TeamResponse:
    raw = _member_snapshot(team)
    return TeamResponse(
        id=team.id,
        name=team.name,
        org_id=team.org_id,
        description=team.description,
        status=team.status,
        cpu_quota_cores=team.cpu_quota_cores,
        memory_quota_gb=team.memory_quota_gb,
        storage_quota_gb=team.storage_quota_gb,
        members=_member_models(raw, usernames),
        member_count=len(raw),
        role_permissions=_role_permissions(team.role_permissions),
        created_by=team.created_by,
        created_at=team.created_at,
        updated_at=team.updated_at,
    )


async def _usernames_for(db: AsyncSession, teams: list[Team]) -> dict[uuid.UUID, str]:
    member_ids = {
        member_id
        for team in teams
        for entry in _member_snapshot(team)
        if (member_id := _as_uuid(entry.get("user_id"))) is not None
    }
    if not member_ids:
        return {}
    rows = await db.execute(select(User.id, User.username).where(User.id.in_(member_ids)))
    return {row.id: row.username for row in rows.all()}


async def _visible_team_ids(db: AsyncSession, user: User) -> list[uuid.UUID] | None:
    """Team ids a business caller may see; None = platform-side caller, unrestricted.

    The roster is a JSON snapshot, and JSON containment predicates are dialect
    specific (PG `@>` vs SQLite `json_each`), so membership is resolved in Python over
    the (id, members) projection — teams are small by design (see app/models/team.py)
    and the resolved id set keeps SQL-side total/offset pagination honest.
    """
    if user.role in (UserRole.ADMIN, UserRole.OPERATOR):
        return None
    rows = await db.execute(select(Team.id, Team.members))
    target = str(user.id)
    return [
        row.id
        for row in rows.all()
        if any(entry.get("user_id") == target for entry in _roster(row.members))
    ]


async def _get_team_or_404(db: AsyncSession, team_id: uuid.UUID) -> Team:
    team = await db.get(Team, team_id)
    if team is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="团队不存在")
    return team


@router.get("", response_model=TeamListResponse)
async def list_teams(
    q: str | None = Query(default=None, max_length=128),
    org_id: str | None = Query(default=None, max_length=128),
    team_status: str | None = Query(default=None, alias="status", max_length=32),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Team)

    visible_ids = await _visible_team_ids(db, current_user)
    if visible_ids is not None:
        if not visible_ids:
            return TeamListResponse(items=[], total=0, page=page, page_size=page_size)
        stmt = stmt.where(Team.id.in_(visible_ids))
    if q:
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(or_(Team.name.ilike(pattern), Team.org_id.ilike(pattern)))
    if org_id:
        stmt = stmt.where(Team.org_id == org_id)
    if team_status:
        stmt = stmt.where(Team.status == team_status)

    total = await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = await db.execute(
        stmt.order_by(Team.created_at.desc(), Team.id.desc()).offset((page - 1) * page_size).limit(page_size)
    )
    teams = list(rows.scalars().all())
    usernames = await _usernames_for(db, teams)
    return TeamListResponse(
        items=[_team_response(team, usernames) for team in teams],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("", response_model=TeamResponse, status_code=status.HTTP_201_CREATED)
async def create_team(
    payload: TeamCreate,
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR)),
    db: AsyncSession = Depends(get_db),
):
    now = _now()
    team = Team(
        name=payload.name,
        org_id=payload.org_id,
        description=payload.description,
        status="active",
        cpu_quota_cores=payload.cpu_quota_cores,
        memory_quota_gb=payload.memory_quota_gb,
        storage_quota_gb=payload.storage_quota_gb,
        members=[],
        role_permissions=dict(payload.role_permissions),
        created_by=current_user.id,
        created_at=now,
        updated_at=now,
    )
    db.add(team)
    await db.flush()
    await audit_service.log(
        db,
        action="team.create",
        resource_type="team",
        resource_id=str(team.id),
        user_id=current_user.id,
        detail={
            "name": team.name,
            "org_id": team.org_id,
            "cpu_quota_cores": team.cpu_quota_cores,
            "memory_quota_gb": team.memory_quota_gb,
            "storage_quota_gb": team.storage_quota_gb,
        },
        actor_type="user",
        actor_id=str(current_user.id),
    )
    return _team_response(team, {})


@router.get("/{team_id}", response_model=TeamResponse)
async def get_team(
    team_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    team = await _get_team_or_404(db, team_id)
    visible_ids = await _visible_team_ids(db, current_user)
    if visible_ids is not None and team.id not in visible_ids:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="团队不存在")
    usernames = await _usernames_for(db, [team])
    return _team_response(team, usernames)


@router.patch("/{team_id}", response_model=TeamResponse)
async def update_team(
    team_id: uuid.UUID,
    payload: TeamPatch,
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR)),
    db: AsyncSession = Depends(get_db),
):
    team = await _get_team_or_404(db, team_id)
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="没有需要更新的字段")

    before = {
        "name": team.name,
        "org_id": team.org_id,
        "description": team.description,
        "status": team.status,
        "cpu_quota_cores": team.cpu_quota_cores,
        "memory_quota_gb": team.memory_quota_gb,
        "storage_quota_gb": team.storage_quota_gb,
        "role_permissions": _role_permissions(team.role_permissions),
    }
    for field, value in changes.items():
        setattr(team, field, dict(value) if field == "role_permissions" else value)
    team.updated_at = _now()
    await db.flush()

    await audit_service.log(
        db,
        action="team.update",
        resource_type="team",
        resource_id=str(team.id),
        user_id=current_user.id,
        detail={"changes": {key: {"from": before[key], "to": value} for key, value in changes.items()}},
        actor_type="user",
        actor_id=str(current_user.id),
    )
    usernames = await _usernames_for(db, [team])
    return _team_response(team, usernames)


@router.post("/{team_id}/members", response_model=TeamResponse)
async def upsert_team_member(
    team_id: uuid.UUID,
    payload: TeamMemberUpsert,
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR)),
    db: AsyncSession = Depends(get_db),
):
    """Add a member, or re-role an existing one — the roster has no separate invite state."""
    team = await _get_team_or_404(db, team_id)
    target = await db.get(User, payload.user_id)
    if target is None or not target.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在或已停用")

    if payload.permissions is None:
        permissions = list((team.role_permissions or {}).get(payload.role, []))
    else:
        permissions = list(payload.permissions)

    members = _member_snapshot(team)
    replaced = any(member.get("user_id") == str(payload.user_id) for member in members)
    members = [member for member in members if member.get("user_id") != str(payload.user_id)]
    members.append(
        {
            "user_id": str(payload.user_id),
            "role": payload.role,
            "permissions": permissions,
            "added_at": _now().isoformat(),
            "added_by": str(current_user.id),
        }
    )
    team.members = members
    team.updated_at = _now()
    await db.flush()

    await audit_service.log(
        db,
        action="team.member.upsert",
        resource_type="team",
        resource_id=str(team.id),
        user_id=current_user.id,
        detail={
            "member_user_id": str(payload.user_id),
            "member_username": target.username,
            "role": payload.role,
            "permissions": permissions,
            "replaced": replaced,
        },
        actor_type="user",
        actor_id=str(current_user.id),
    )
    usernames = {target.id: target.username}
    return _team_response(team, usernames)


@router.delete("/{team_id}/members/{user_id}", response_model=TeamResponse)
async def remove_team_member(
    team_id: uuid.UUID,
    user_id: uuid.UUID,
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR)),
    db: AsyncSession = Depends(get_db),
):
    team = await _get_team_or_404(db, team_id)
    members = _member_snapshot(team)
    kept = [member for member in members if member.get("user_id") != str(user_id)]
    if len(kept) == len(members):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="该用户不是团队成员")
    removed = next(member for member in members if member.get("user_id") == str(user_id))

    team.members = kept
    team.updated_at = _now()
    await db.flush()

    await audit_service.log(
        db,
        action="team.member.remove",
        resource_type="team",
        resource_id=str(team.id),
        user_id=current_user.id,
        detail={
            "member_user_id": str(user_id),
            "previous_role": removed.get("role"),
            "previous_permissions": removed.get("permissions") or [],
        },
        actor_type="user",
        actor_id=str(current_user.id),
    )
    usernames = await _usernames_for(db, [team])
    return _team_response(team, usernames)
