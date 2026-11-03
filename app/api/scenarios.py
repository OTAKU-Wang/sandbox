"""业务场景管理 (`/api/v1/flow/scenarios`) — 2026-11-03 sandbox realignment Task 5.

场景是需求侧的入口实体（见 app/models/scenario.py 的口径）：运营发布场景，开发团队
**承接**后由场景一键转化为 ModelingProject，并把场景的合规约束（compliance_constraints）
与交付技术预埋（delivery_tech_config）注入项目配置。

「开发场景 / 交付场景」是同一张表的两个视图，不是两个实体，也不是第二套字段：
- 开发场景 = 承接视角（行业、业务背景、目标、合规约束、状态）；
- 交付场景 = `delivery_scene` 预埋的三分类（api 接口服务交付 / dataset_file 数据集文件
  交付 / tee_compute TEE 密态交付）。所以筛选参数就叫 `delivery_scene`，本模块不新造
  `scenario_type` 这种库里没有的字段。

挂载（app/main.py）：prefix `/api/v1/flow/scenarios`，`tags=["flow"]`，
`dependencies=[Depends(require_group("sessions"))]` —— 与 flow 面同组（SS-12 §4.2 的
`sessions` 前缀映射已含 `/api/v1/flow`）。**必须挂在 flow_runtime_api.router 之前**：
flow 面的路径更深，后挂就永久不可达（仓库既有口径：先挂更具体的前缀）。

鉴权与越权口径：
- 读面（列表/详情/我的）：flow 面全体业务账号可读 —— 场景是公开撮合的需求帖，
  不含租户隐私；与 flow_runtime / notebooks 同一口径（这些路由没有 require_roles）。
- 写面不收紧到 ADMIN：发布场景不授予任何数据权限（真正的授权仍走合约白名单），
  承接才是有风险动作，它的边界是「一支团队对一个场景只能建一个项目」+ 团队归属校验
  + 审计。所以越权 403 出现在这里：带 `team_id` 承接却不是该团队成员。
- 场景不存在 → 404；已承接/已归档再承接 → 409（重复承接会复制出第二支团队的配额占用）。

事务：`get_db` 持有事务（app/core/AGENTS.md 口径），本模块只 `flush()`，路由内不 commit。
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
from app.core.deps import get_current_user
from app.models.modeling_project import ModelingProject, ProjectStatus
from app.models.scenario import DeliverySceneType, Scenario, ScenarioStatus
from app.models.team import Team
from app.models.user import User, UserRole
from app.services.audit_service import audit_service

router = APIRouter()

logger = logging.getLogger(__name__)

DELIVERY_SCENE_VALUES = {scene.value for scene in DeliverySceneType}
SCENARIO_STATUS_VALUES = {s.value for s in ScenarioStatus}
# 与 ModelingProject.preferred_language 的注释同一集合（python / sql / r / pyspark）；
# R 与 PySpark 的执行面是 Task 6，这里先允许登记，执行侧未就绪时如实拒绝在执行面。
PREFERRED_LANGUAGES = ("python", "sql", "r", "pyspark")
ENVIRONMENT_TYPES = ("l1", "l2", "l2c", "l3")


class ScenarioCreate(BaseModel):
    title: str = Field(min_length=1, max_length=256)
    industry: str | None = Field(default=None, max_length=64)
    business_background: str | None = None
    target_description: str | None = None
    delivery_scene: str = Field(default=DeliverySceneType.API.value, max_length=32)
    suggested_resource_ids: list[uuid.UUID] = Field(default_factory=list, max_length=256)
    compliance_constraints: dict[str, object] = Field(default_factory=dict)
    delivery_tech_config: dict[str, object] = Field(default_factory=dict)

    @field_validator("delivery_scene")
    @classmethod
    def _scene_known(cls, value: str) -> str:
        if value not in DELIVERY_SCENE_VALUES:
            raise ValueError(
                f"unknown delivery_scene: {value}; allowed: {sorted(DELIVERY_SCENE_VALUES)}"
            )
        return value


class ScenarioAccept(BaseModel):
    """承接体。project_name 缺省用场景标题；team_id 缺省按成员关系推断。"""

    project_name: str | None = Field(default=None, min_length=1, max_length=128)
    preferred_language: Literal["python", "sql", "r", "pyspark"] = "python"
    environment_type: Literal["l1", "l2", "l2c", "l3"] = "l2c"
    team_id: uuid.UUID | None = None


class ProjectBrief(BaseModel):
    """承接产物的项目摘要（dag_definition 的 scenario_context 如实回显注入内容）。"""

    id: uuid.UUID
    name: str
    status: str
    team_id: uuid.UUID | None
    scenario_id: uuid.UUID | None
    environment_type: str
    preferred_language: str
    participants: list[object]
    scenario_context: dict[str, object]
    created_at: datetime | None
    updated_at: datetime | None


class ScenarioResponse(BaseModel):
    id: uuid.UUID
    title: str
    industry: str | None
    business_background: str | None
    target_description: str | None
    delivery_scene: str
    status: str
    suggested_resource_ids: list[str]
    compliance_constraints: dict[str, object]
    delivery_tech_config: dict[str, object]
    accepted_team_id: uuid.UUID | None
    accepted_by: uuid.UUID | None
    created_by: uuid.UUID | None
    created_at: datetime | None
    updated_at: datetime | None
    project: ProjectBrief | None = None


class ScenarioListResponse(BaseModel):
    """仓库统一分页信封（A309 同一条：缺 page/page_size 的裸数组会被 unwrapList 拒）。"""

    items: list[ScenarioResponse]
    total: int
    page: int
    page_size: int


class ScenarioAcceptResponse(BaseModel):
    scenario: ScenarioResponse
    project: ProjectBrief


def _as_uuid(value: object) -> uuid.UUID | None:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        return None


def _uuid_or_404(value: str, label: str = "场景") -> uuid.UUID:
    parsed = _as_uuid(value)
    if parsed is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{label}不存在")
    return parsed


def _json_dict(value: object) -> dict[str, object]:
    """JSON 列 → dict。类型不对时如实按空处理并记一条 WARNING，不猜内容。"""
    if value is None:
        return {}
    if not isinstance(value, dict):
        logger.warning("[scenarios] JSON 快照是 %s 而非对象，按空处理", type(value).__name__)
        return {}
    return dict(value)


def _json_list(value: object) -> list[object]:
    if value is None:
        return []
    if not isinstance(value, list):
        logger.warning("[scenarios] JSON 快照是 %s 而非数组，按空处理", type(value).__name__)
        return []
    return list(value)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _scenario_payload(scenario: Scenario, project: ModelingProject | None) -> dict[str, object]:
    return {
        "id": scenario.id,
        "title": scenario.title,
        "industry": scenario.industry,
        "business_background": scenario.business_background,
        "target_description": scenario.target_description,
        "delivery_scene": scenario.delivery_scene,
        "status": scenario.status,
        # JSON 列里可能是 str 也可能是 uuid.UUID（迁移前的历史行），出参统一成 str。
        "suggested_resource_ids": [str(item) for item in _json_list(scenario.suggested_resource_ids)],
        "compliance_constraints": _json_dict(scenario.compliance_constraints),
        "delivery_tech_config": _json_dict(scenario.delivery_tech_config),
        "accepted_team_id": scenario.accepted_team_id,
        "accepted_by": scenario.accepted_by,
        "created_by": scenario.created_by,
        "created_at": scenario.created_at,
        "updated_at": scenario.updated_at,
        "project": _project_payload(project) if project is not None else None,
    }


def _project_payload(project: ModelingProject) -> dict[str, object]:
    dag = _json_dict(project.dag_definition)
    context = dag.get("scenario_context")
    return {
        "id": project.id,
        "name": project.name,
        "status": project.status,
        "team_id": project.team_id,
        "scenario_id": project.scenario_id,
        "environment_type": project.environment_type,
        "preferred_language": project.preferred_language,
        "participants": _json_list(project.participants),
        "scenario_context": context if isinstance(context, dict) else {},
        "created_at": project.created_at,
        "updated_at": project.updated_at,
    }


def _is_member(team: Team, user_id: uuid.UUID) -> bool:
    target = str(user_id)
    return any(str(entry.get("user_id")) == target for entry in _json_list(team.members) if isinstance(entry, dict))


async def _my_teams(db: AsyncSession, user: User) -> list[Team]:
    """我所属的在用团队。

    成员名册是 `Team.members` 的 JSON 快照（仓内没有名册表，见 app/models/team.py），
    所以只能按团队行过滤 —— 与 app/api/teams.py 的可见性判定共用同一份事实来源。
    """
    rows = (
        await db.execute(select(Team).where(Team.status == "active").order_by(Team.created_at))
    ).scalars().all()
    return [team for team in rows if _is_member(team, user.id)]


async def _project_for(db: AsyncSession, scenario_ids: list[uuid.UUID]) -> dict[str, ModelingProject]:
    """一页场景对应的承接项目（一次批量查，避免逐行 N+1）。"""
    if not scenario_ids:
        return {}
    rows = (
        await db.execute(select(ModelingProject).where(ModelingProject.scenario_id.in_(scenario_ids)))
    ).scalars().all()
    # 业务上承接幂等（一个场景只有一个项目），真出现多行时按创建序取最早的一条，
    # 并在 payload 里如实回显 —— 静默丢行会让「已承接」与「项目」对不上号。
    out: dict[str, ModelingProject] = {}
    for project in sorted(rows, key=lambda p: (p.created_at is None, p.created_at)):
        key = str(project.scenario_id)
        if key in out:
            logger.warning(
                "[scenarios] scenario %s has more than one project (%s already bound)",
                key, project.id,
            )
            continue
        out[key] = project
    return out


async def _load_scenario(db: AsyncSession, scenario_id: uuid.UUID) -> Scenario:
    scenario = await db.get(Scenario, scenario_id)
    if scenario is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="场景不存在")
    return scenario


@router.get("", response_model=ScenarioListResponse)
async def list_scenarios(
    scenario_status: str | None = Query(default=None, alias="status"),
    delivery_scene: str | None = None,
    industry: str | None = None,
    keyword: str | None = None,
    mine: bool = False,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ScenarioListResponse:
    """场景列表：可按状态、交付场景（api/dataset_file/tee_compute）、行业、关键词筛选。

    `mine=true` 收窄到「我承接的 + 我的团队承接的」，用于场景中心的「我的承接」视图。
    """
    if scenario_status is not None and scenario_status not in SCENARIO_STATUS_VALUES:
        raise HTTPException(
            status_code=422,
            detail=f"未知的场景状态: {scenario_status}; 可选: {sorted(SCENARIO_STATUS_VALUES)}",
        )
    if delivery_scene is not None and delivery_scene not in DELIVERY_SCENE_VALUES:
        raise HTTPException(
            status_code=422,
            detail=f"未知的交付场景: {delivery_scene}; 可选: {sorted(DELIVERY_SCENE_VALUES)}",
        )

    stmt = select(Scenario)
    if scenario_status is not None:
        stmt = stmt.where(Scenario.status == scenario_status)
    if delivery_scene is not None:
        stmt = stmt.where(Scenario.delivery_scene == delivery_scene)
    if industry is not None:
        stmt = stmt.where(Scenario.industry == industry)
    if keyword:
        like = f"%{keyword}%"
        stmt = stmt.where(or_(Scenario.title.ilike(like), Scenario.industry.ilike(like)))
    if mine:
        teams = await _my_teams(db, user)
        team_ids = [team.id for team in teams]
        scope = [Scenario.accepted_by == user.id]
        if team_ids:
            scope.append(Scenario.accepted_team_id.in_(team_ids))
        stmt = stmt.where(or_(*scope))

    total = (
        await db.execute(select(func.count()).select_from(stmt.subquery()))
    ).scalar_one()
    rows = (
        await db.execute(
            stmt.order_by(Scenario.created_at.desc(), Scenario.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()
    projects = await _project_for(db, [row.id for row in rows])
    items = [
        ScenarioResponse.model_validate(_scenario_payload(row, projects.get(str(row.id))))
        for row in rows
    ]
    return ScenarioListResponse(items=items, total=int(total), page=page, page_size=page_size)


@router.post("", response_model=ScenarioResponse, status_code=status.HTTP_201_CREATED)
async def create_scenario(
    payload: ScenarioCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ScenarioResponse:
    """发布场景（初始态 published）。建议资源清单只登记意向，最终授权仍走合约白名单。"""
    scenario = Scenario(
        title=payload.title,
        industry=payload.industry,
        business_background=payload.business_background,
        target_description=payload.target_description,
        delivery_scene=payload.delivery_scene,
        status=ScenarioStatus.PUBLISHED.value,
        suggested_resource_ids=[str(item) for item in payload.suggested_resource_ids],
        compliance_constraints=dict(payload.compliance_constraints),
        delivery_tech_config=dict(payload.delivery_tech_config),
        created_by=user.id,
    )
    db.add(scenario)
    await db.flush()
    # server_default 的 created_at/updated_at 在 INSERT 后是 deferred 列，同步助手读到
    # 就是惰性 SELECT → MissingGreenlet（写路径先 refresh，再把普通值交给 payload）。
    await db.refresh(scenario)

    await audit_service.log(
        db,
        action="scenario.create",
        resource_type="scenario",
        user_id=user.id,
        resource_id=str(scenario.id),
        detail={
            "title": scenario.title,
            "industry": scenario.industry,
            "delivery_scene": scenario.delivery_scene,
        },
        actor_type="user",
        actor_id=str(user.id),
    )
    return ScenarioResponse.model_validate(_scenario_payload(scenario, None))


@router.get("/mine", response_model=ScenarioListResponse)
async def list_my_scenarios(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ScenarioListResponse:
    """我已承接的场景及其关联建模项目（无团队账号按承接人判定）。"""
    teams = await _my_teams(db, user)
    team_ids = [team.id for team in teams]
    scope = [Scenario.accepted_by == user.id]
    if team_ids:
        scope.append(Scenario.accepted_team_id.in_(team_ids))
    stmt = select(Scenario).where(or_(*scope))
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (
        await db.execute(
            stmt.order_by(Scenario.updated_at.desc(), Scenario.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()
    projects = await _project_for(db, [row.id for row in rows])
    items = [
        ScenarioResponse.model_validate(_scenario_payload(row, projects.get(str(row.id))))
        for row in rows
    ]
    return ScenarioListResponse(items=items, total=int(total), page=page, page_size=page_size)


@router.get("/{scenario_id}", response_model=ScenarioResponse)
async def get_scenario(
    scenario_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ScenarioResponse:
    """场景详情（含承接后的建模项目摘要）。"""
    sid = _uuid_or_404(scenario_id)
    scenario = await _load_scenario(db, sid)
    projects = await _project_for(db, [sid])
    return ScenarioResponse.model_validate(_scenario_payload(scenario, projects.get(str(sid))))


@router.post("/{scenario_id}/accept", response_model=ScenarioAcceptResponse)
async def accept_scenario(
    scenario_id: str,
    payload: ScenarioAccept,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ScenarioAcceptResponse:
    """一键承接：场景置 accepted，并新建绑定的建模项目。

    注入的项目配置 = 场景合规约束 + 交付方式预埋 + 建议资源清单，落在
    `dag_definition.scenario_context`（nodes/edges 留给 Task 6 的 DAG 编排，
    这里只立一个空壳，保证执行侧读到的是合法 DAG 结构而不是半截数据）。

    团队归属：带 team_id 必须是该团队成员（否则 403）；不带时，单一团队账号自动归属，
    多团队账号必须显式指定（不替用户猜哪支团队花配额），无团队账号如实建为未归属项目。
    """
    sid = _uuid_or_404(scenario_id)
    scenario = await _load_scenario(db, sid)
    if scenario.status != ScenarioStatus.PUBLISHED.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"场景当前状态为 {scenario.status}，只有已发布（published）的场景可承接",
        )

    existing = await _project_for(db, [sid])
    if str(sid) in existing:
        # 状态还没翻但项目已存在 = 上一笔承接落了一半，如实拒绝而不是再建一个。
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="该场景已存在承接项目，不能重复承接",
        )

    teams = await _my_teams(db, user)
    my_team_ids = {team.id: team for team in teams}
    team_id: uuid.UUID | None = None
    if payload.team_id is not None:
        team = await db.get(Team, payload.team_id)
        if team is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="团队不存在")
        if team.status != "active":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"团队当前状态为 {team.status}，不能用它承接场景",
            )
        is_platform = user.role in (UserRole.ADMIN, UserRole.OPERATOR)
        if team.id not in my_team_ids and not is_platform:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="不是该团队成员，不能代表该团队承接场景",
            )
        team_id = team.id
    elif len(my_team_ids) == 1:
        team_id = next(iter(my_team_ids))
    elif len(my_team_ids) > 1:
        raise HTTPException(
            status_code=422,
            detail="账号同时属于多个团队，请在承接时指定 team_id",
        )

    project_name = payload.project_name or scenario.title
    now = _now_iso()
    compliance = _json_dict(scenario.compliance_constraints)
    tech_config = _json_dict(scenario.delivery_tech_config)
    project = ModelingProject(
        name=project_name,
        description=scenario.target_description,
        team_id=team_id,
        scenario_id=scenario.id,
        environment_type=payload.environment_type,
        preferred_language=payload.preferred_language,
        status=ProjectStatus.ACTIVE.value,
        participants=[{
            "user_id": str(user.id),
            "username": user.username,
            "role": "owner",
            "added_at": now,
        }],
        milestones=[],
        dag_definition={
            "version": 1,
            "language": payload.preferred_language,
            "nodes": [],
            "edges": [],
            "scenario_context": {
                "scenario_id": str(scenario.id),
                "title": scenario.title,
                "industry": scenario.industry,
                "delivery_scene": scenario.delivery_scene,
                "compliance_constraints": compliance,
                "delivery_tech_config": tech_config,
                "suggested_resource_ids": [
                    str(item) for item in _json_list(scenario.suggested_resource_ids)
                ],
                "accepted_at": now,
            },
        },
        linked_notebook_ids=[],
        linked_workload_ids=[],
        created_by=user.id,
    )
    db.add(project)
    await db.flush()

    scenario.status = ScenarioStatus.ACCEPTED.value
    scenario.accepted_by = user.id
    scenario.accepted_team_id = team_id
    await db.flush()
    # 同上：UPDATE 后 onupdate 的 updated_at 处于 deferred 态，必须先 refresh 再出参。
    await db.refresh(scenario)
    await db.refresh(project)

    await audit_service.log(
        db,
        action="scenario.accept",
        resource_type="scenario",
        user_id=user.id,
        resource_id=str(scenario.id),
        detail={
            "project_id": str(project.id),
            "project_name": project.name,
            "team_id": str(team_id) if team_id else None,
            "preferred_language": project.preferred_language,
            "delivery_scene": scenario.delivery_scene,
            "injected_constraint_keys": sorted(compliance),
        },
        actor_type="user",
        actor_id=str(user.id),
    )
    return ScenarioAcceptResponse(
        scenario=ScenarioResponse.model_validate(_scenario_payload(scenario, project)),
        project=ProjectBrief.model_validate(_project_payload(project)),
    )
