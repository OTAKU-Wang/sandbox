"""建模项目协同空间 (`/api/v1/flow/projects`) — 2026-11-03 sandbox realignment Task 6.

ModelingProject 是 Notebook / Workload / ComputeRun / Contract 的上层聚合空间
（见 app/models/modeling_project.py）：项目持有协同名册、里程碑与 DAG 任务编排定义。
本模块是它的**编排与审核入口**，不做执行：DAG 在这里被保存与拓扑校验，真正按序调度
节点属于执行面（计划的后续 Task）。

三件本模块负责的事：
1. 多语言登记：`preferred_language` 与 DAG 节点 `language` 的可选集合取自
   `app/models/notebook.py` 的 `SUPPORTED_LANGUAGES`（python / sql / r / pyspark），
   两处共用一份事实来源，不各自写死字面量；
2. DAG 编排校验：Kahn 拓扑排序检测有向无环图，无环回 `execution_order`，有环回
   `valid=false` 并点名 Kahn 消不掉的节点（前端要能高亮，只回布尔没法用）；
3. 审核流：`submit-review` 把项目推进到 `submitted_review`，此后 DAG 写入一律 409 ——
   提交给运营的成果必须是冻结的。

挂载（app/main.py）：prefix `/api/v1/flow/projects`，`tags=["flow"]`，
`dependencies=[Depends(require_group("sessions"))]`，**必须挂在 flow_runtime_api.router
之前**（先挂更具体前缀的仓内既有口径）。用 `get_business_user` 而不是 `get_current_user`：
`sessions` 前缀组只对 API Key 主体有意义（SS-12 §4.2），而 `get_current_user` 会在进路由前
就把 Key 主体 401 掉，那道 scope 门就成了摆设。

可见性：创建人 + 项目所属团队成员 + 平台角色（OPERATOR/ADMIN，审核方需要看到待审项目）。
`participants` 是多方协同**名册**（org_name + role + 可选账号），展示与审计上下文用的，
不是 ACL —— 成员资格的权威是 Team（teams 面有自己的成员判定），这里不另起一套授权。

事务：`get_db` 持有事务（app/core/AGENTS.md 口径），本模块只 `flush()`；每次 flush 后
`refresh()` 再出参，否则 `onupdate` 的 `updated_at` 处于 deferred 态，序列化时会踩
MissingGreenlet。JSON 列不做脏跟踪，所以 dag/milestones 一律整体换新对象。
"""
import heapq
import logging
import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_business_user
from app.models.modeling_project import ModelingProject, ProjectStatus
from app.models.notebook import SUPPORTED_LANGUAGES, Notebook
from app.models.team import Team
from app.models.user import User, UserRole
from app.models.workload import Workload
from app.services.audit_service import audit_service

router = APIRouter()

logger = logging.getLogger(__name__)

PROJECT_LANGUAGES: tuple[str, ...] = SUPPORTED_LANGUAGES
ENVIRONMENT_TYPES = ("l1", "l2", "l2c", "l3")
MILESTONE_STATUSES = ("pending", "in_progress", "done")
MAX_PARTICIPANTS = 50
MAX_MILESTONES = 50
MAX_DAG_NODES = 60
MAX_DAG_EDGES = 200
DAG_VERSION = 1

_PROJECT_STATUS_VALUES = {s.value for s in ProjectStatus}
# 只有进行中的项目可以改画布；已提交/已批准/已归档都要冻结（审核链的可信性）。
_EDITABLE_STATUSES = (ProjectStatus.ACTIVE.value,)
_REVIEWER_ROLES = (UserRole.ADMIN, UserRole.OPERATOR)


class ParticipantSpec(BaseModel):
    """多方协作者名册条目。org_name 是机构方（数据提供方A…），user_id/username 可选。"""

    org_name: str | None = Field(default=None, max_length=128)
    username: str | None = Field(default=None, max_length=64)
    role: str = Field(default="collaborator", min_length=1, max_length=32)


class MilestoneSpec(BaseModel):
    title: str = Field(min_length=1, max_length=128)
    due_date: str | None = Field(default=None, max_length=32)
    status: Literal["pending", "in_progress", "done"] = "pending"


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=4000)
    scenario_id: uuid.UUID | None = None
    contract_id: uuid.UUID | None = None
    team_id: uuid.UUID | None = None
    environment_type: Literal["l1", "l2", "l2c", "l3"] = "l2c"
    preferred_language: Literal["python", "sql", "r", "pyspark"] = "python"
    participants: list[ParticipantSpec] = Field(default_factory=list, max_length=MAX_PARTICIPANTS)
    milestones: list[MilestoneSpec] = Field(default_factory=list, max_length=MAX_MILESTONES)


class DagNodeSpec(BaseModel):
    id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    label: str = Field(min_length=1, max_length=128)
    type: str = Field(default="task", min_length=1, max_length=32)
    # 缺省继承项目首选语言；显式写则必须落在同一份多语言登记表里
    language: Literal["python", "sql", "r", "pyspark"] | None = None
    notebook_id: uuid.UUID | None = None
    workload_id: uuid.UUID | None = None


class DagEdgeSpec(BaseModel):
    source: str = Field(min_length=1, max_length=64)
    target: str = Field(min_length=1, max_length=64)


class DagSave(BaseModel):
    nodes: list[DagNodeSpec] = Field(default_factory=list, max_length=MAX_DAG_NODES)
    edges: list[DagEdgeSpec] = Field(default_factory=list, max_length=MAX_DAG_EDGES)


class DagValidateResponse(BaseModel):
    valid: bool
    execution_order: list[str]
    cyclic_nodes: list[str]
    errors: list[str]
    node_count: int
    edge_count: int


class ProjectResponse(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    team_id: uuid.UUID | None
    scenario_id: uuid.UUID | None
    contract_id: uuid.UUID | None
    environment_type: str
    preferred_language: str
    status: str
    participants: list[object]
    milestones: list[object]
    dag_definition: dict[str, object]
    linked_notebook_ids: list[str]
    linked_workload_ids: list[str]
    created_by: uuid.UUID | None
    created_at: datetime | None
    updated_at: datetime | None


class ProjectDetailResponse(ProjectResponse):
    """详情看板：把 DAG 节点引用的 notebook / workload 聚合成一行一行的可点条目。

    `unresolved_*` 如实列出「引用还在、行已经没了」的 ID —— linked_* 是无外键的引用
    清单，静默少一条比报出来更难查。
    """

    notebooks: list[dict[str, object]] = Field(default_factory=list)
    workloads: list[dict[str, object]] = Field(default_factory=list)
    unresolved_notebook_ids: list[str] = Field(default_factory=list)
    unresolved_workload_ids: list[str] = Field(default_factory=list)


class ProjectListResponse(BaseModel):
    items: list[ProjectResponse]
    total: int
    page: int
    page_size: int


def _json_dict(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    return dict(value)


def _json_list(value: object) -> list[object]:
    if value is None:
        return []
    if not isinstance(value, list):
        logger.warning("[projects] JSON 快照是 %s 而非数组，按空处理", type(value).__name__)
        return []
    return list(value)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _is_reviewer(user: User) -> bool:
    return user.role in _REVIEWER_ROLES


def _project_payload(project: ModelingProject) -> dict[str, object]:
    dag = _json_dict(project.dag_definition)
    return {
        "id": project.id,
        "name": project.name,
        "description": project.description,
        "team_id": project.team_id,
        "scenario_id": project.scenario_id,
        "contract_id": project.contract_id,
        "environment_type": project.environment_type,
        "preferred_language": project.preferred_language,
        "status": project.status,
        "participants": _json_list(project.participants),
        "milestones": _json_list(project.milestones),
        "dag_definition": dag,
        "linked_notebook_ids": [str(item) for item in _json_list(project.linked_notebook_ids)],
        "linked_workload_ids": [str(item) for item in _json_list(project.linked_workload_ids)],
        "created_by": project.created_by,
        "created_at": project.created_at,
        "updated_at": project.updated_at,
    }


async def _my_team_ids(db: AsyncSession, user: User) -> set[str]:
    """我所属的在用团队 id 集合（成员名册是 Team.members 的 JSON 快照，与 teams 面同源）。"""
    target = str(user.id)
    rows = (await db.execute(select(Team).where(Team.status == "active"))).scalars().all()
    ids: set[str] = set()
    for team in rows:
        for entry in _json_list(team.members):
            if isinstance(entry, dict) and str(entry.get("user_id")) == target:
                ids.add(str(team.id))
                break
    return ids


async def _load_project(db: AsyncSession, project_id: uuid.UUID, user: User) -> ModelingProject:
    """取一个当前用户可见的项目；不可见与不存在一律 404（不泄露存在性）。"""
    project = await db.get(ModelingProject, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="项目不存在")
    if _is_reviewer(user):
        return project
    if str(project.created_by) == str(user.id):
        return project
    if project.team_id is not None:
        if str(project.team_id) in await _my_team_ids(db, user):
            return project
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="项目不存在")


def _require_editable(project: ModelingProject) -> None:
    if project.status not in _EDITABLE_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"项目当前状态为 {project.status}，画布与配置已冻结，不能修改",
        )


def _topological_order(
    node_ids: list[str], edges: list[tuple[str, str]]
) -> tuple[list[str], list[str], list[str]]:
    """Kahn 拓扑排序：返回 (执行序列, 成环节点, 错误说明)。

    平局用**节点声明顺序**（堆里放的是声明下标）而不是集合遍历序：同一份 DAG 每次
    校验必须给出同一条 execution_order，否则前端的编排图会与后端给出的顺序对不上号。
    Kahn 消不掉的剩余节点就是成环的那批 —— 只报「有环」而不点名，用户无从下手。
    """
    errors: list[str] = []
    if not node_ids:
        errors.append("画布为空：没有可校验的任务节点")
        return [], [], errors

    rank = {node_id: index for index, node_id in enumerate(node_ids)}
    indegree = {node_id: 0 for node_id in node_ids}
    outgoing: dict[str, list[str]] = {node_id: [] for node_id in node_ids}

    for source, target in edges:
        if source not in rank or target not in rank:
            errors.append(f"连线引用了不存在的节点: {source} -> {target}")
            continue
        outgoing[source].append(target)
        indegree[target] += 1

    ready = [rank[node_id] for node_id in node_ids if indegree[node_id] == 0]
    heapq.heapify(ready)
    order: list[str] = []
    while ready:
        current = node_ids[heapq.heappop(ready)]
        order.append(current)
        for nxt in outgoing[current]:
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                heapq.heappush(ready, rank[nxt])

    done = set(order)
    cyclic = [node_id for node_id in node_ids if node_id not in done]
    if cyclic:
        errors.append(
            f"存在环路：{len(cyclic)} 个节点无法确定先后顺序 -> {', '.join(cyclic)}"
        )
        return [], cyclic, errors
    return order, [], errors


@router.get("", response_model=ProjectListResponse)
async def list_projects(
    project_status: str | None = Query(default=None, alias="status"),
    preferred_language: str | None = None,
    keyword: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    user: User = Depends(get_business_user),
    db: AsyncSession = Depends(get_db),
) -> ProjectListResponse:
    """我的建模项目（团队成员的项目一并可见），统一分页信封。"""
    if project_status is not None and project_status not in _PROJECT_STATUS_VALUES:
        raise HTTPException(
            status_code=422,
            detail=f"未知的项目状态: {project_status}; 可选: {sorted(_PROJECT_STATUS_VALUES)}",
        )
    if preferred_language is not None and preferred_language not in PROJECT_LANGUAGES:
        raise HTTPException(
            status_code=422,
            detail=f"未知的语言: {preferred_language}; 可选: {list(PROJECT_LANGUAGES)}",
        )

    stmt = select(ModelingProject)
    if not _is_reviewer(user):
        team_ids = sorted(await _my_team_ids(db, user))
        scope = [ModelingProject.created_by == user.id]
        if team_ids:
            scope.append(ModelingProject.team_id.in_([uuid.UUID(team) for team in team_ids]))
        stmt = stmt.where(or_(*scope))
    if project_status is not None:
        stmt = stmt.where(ModelingProject.status == project_status)
    if preferred_language is not None:
        stmt = stmt.where(ModelingProject.preferred_language == preferred_language)
    if keyword:
        like = f"%{keyword}%"
        stmt = stmt.where(or_(ModelingProject.name.ilike(like), ModelingProject.description.ilike(like)))

    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (
        await db.execute(
            stmt.order_by(ModelingProject.created_at.desc(), ModelingProject.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()
    return ProjectListResponse(
        items=[ProjectResponse.model_validate(_project_payload(row)) for row in rows],
        total=int(total),
        page=page,
        page_size=page_size,
    )


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: ProjectCreate,
    user: User = Depends(get_business_user),
    db: AsyncSession = Depends(get_db),
) -> ProjectResponse:
    """新建建模项目：语言、协同名册与里程碑在这里一次配齐。

    名册首条固定是服务端按认证身份注入的 owner —— 客户端若在 participants 里自报
    owner，就能造出一支「别人的项目」并绕开后续的写权限判定。
    """
    if payload.team_id is not None:
        team = await db.get(Team, payload.team_id)
        if team is None:
            raise HTTPException(status_code=404, detail="团队不存在")
        if not _is_reviewer(user) and str(team.id) not in await _my_team_ids(db, user):
            raise HTTPException(status_code=403, detail="不是该团队成员，不能把项目挂到这支团队下")

    now = _now_iso()
    roster: list[dict[str, object]] = [
        {"user_id": str(user.id), "username": user.username, "role": "owner", "added_at": now}
    ]
    for entry in payload.participants:
        item = entry.model_dump(exclude_none=True)
        item["added_at"] = now
        roster.append(item)

    project = ModelingProject(
        name=payload.name,
        description=payload.description,
        team_id=payload.team_id,
        scenario_id=payload.scenario_id,
        contract_id=payload.contract_id,
        environment_type=payload.environment_type,
        preferred_language=payload.preferred_language,
        status=ProjectStatus.ACTIVE.value,
        participants=roster,
        milestones=[item.model_dump(exclude_none=True) for item in payload.milestones],
        dag_definition={"version": DAG_VERSION, "language": payload.preferred_language, "nodes": [], "edges": []},
        linked_notebook_ids=[],
        linked_workload_ids=[],
        created_by=user.id,
    )
    db.add(project)
    await db.flush()
    await db.refresh(project)

    await audit_service.log(
        db,
        action="project.create",
        resource_type="modeling_project",
        user_id=user.id,
        resource_id=str(project.id),
        detail={
            "name": project.name,
            "preferred_language": project.preferred_language,
            "environment_type": project.environment_type,
            "participant_count": len(roster),
            "milestone_count": len(payload.milestones),
        },
    )
    return ProjectResponse.model_validate(_project_payload(project))


@router.get("/{project_id}", response_model=ProjectDetailResponse)
async def get_project(
    project_id: uuid.UUID,
    user: User = Depends(get_business_user),
    db: AsyncSession = Depends(get_db),
) -> ProjectDetailResponse:
    """项目详情看板：项目本体 + DAG 节点引用的 Notebook / Workload 聚合。"""
    project = await _load_project(db, project_id, user)
    payload = _project_payload(project)

    notebook_ids = [str(item) for item in _json_list(project.linked_notebook_ids)]
    workload_ids = [str(item) for item in _json_list(project.linked_workload_ids)]

    notebooks: list[dict[str, object]] = []
    if notebook_ids:
        rows = (
            await db.execute(select(Notebook).where(Notebook.id.in_([uuid.UUID(i) for i in notebook_ids])))
        ).scalars().all()
        by_id = {str(row.id): row for row in rows}
        for item in notebook_ids:
            row = by_id.get(item)
            if row is None:
                continue
            notebooks.append(
                {
                    "id": str(row.id),
                    "title": row.title,
                    "language": row.language,
                    "status": row.status,
                    "task_no": row.task_no,
                    "updated_at": row.updated_at.isoformat() if row.updated_at else None,
                }
            )

    workloads: list[dict[str, object]] = []
    if workload_ids:
        rows = (
            await db.execute(select(Workload).where(Workload.id.in_([uuid.UUID(i) for i in workload_ids])))
        ).scalars().all()
        by_id = {str(row.id): row for row in rows}
        for item in workload_ids:
            row = by_id.get(item)
            if row is None:
                continue
            workloads.append(
                {
                    "id": str(row.id),
                    "name": row.name,
                    "language": row.language,
                    "env": row.env,
                    "status": row.status,
                    "updated_at": row.updated_at.isoformat() if row.updated_at else None,
                }
            )

    payload.update(
        {
            "notebooks": notebooks,
            "workloads": workloads,
            "unresolved_notebook_ids": [item for item in notebook_ids if item not in {row["id"] for row in notebooks}],
            "unresolved_workload_ids": [item for item in workload_ids if item not in {row["id"] for row in workloads}],
        }
    )
    return ProjectDetailResponse.model_validate(payload)


@router.put("/{project_id}/dag", response_model=ProjectResponse)
async def save_project_dag(
    project_id: uuid.UUID,
    payload: DagSave,
    user: User = Depends(get_business_user),
    db: AsyncSession = Depends(get_db),
) -> ProjectResponse:
    """保存可视化编排的节点与连线。

    结构错误（重复节点 id、悬空连线、重复连线）400 —— 这些是画布数据坏了，不是环路；
    环路留给 `dag/validate` 报 valid=false（200），因为「有环」是画布的合法状态，
    用户需要保存草稿后继续改。
    """
    project = await _load_project(db, project_id, user)
    _require_editable(project)

    node_ids = [node.id for node in payload.nodes]
    if len(set(node_ids)) != len(node_ids):
        dupes = sorted({node_id for node_id in node_ids if node_ids.count(node_id) > 1})
        raise HTTPException(status_code=400, detail=f"节点 ID 重复: {', '.join(dupes)}")

    seen_edges: set[tuple[str, str]] = set()
    edges: list[tuple[str, str]] = []
    for edge in payload.edges:
        pair = (edge.source, edge.target)
        if pair[0] not in set(node_ids) or pair[1] not in set(node_ids):
            raise HTTPException(
                status_code=400,
                detail=f"连线引用了画布上不存在的节点: {pair[0]} -> {pair[1]}",
            )
        if pair in seen_edges:
            raise HTTPException(status_code=400, detail=f"连线重复: {pair[0]} -> {pair[1]}")
        seen_edges.add(pair)
        edges.append(pair)

    known_ids = set(node_ids)
    nodes: list[dict[str, object]] = []
    notebook_refs: list[str] = []
    workload_refs: list[str] = []
    for node in payload.nodes:
        item: dict[str, object] = {
            "id": node.id,
            "label": node.label,
            "type": node.type,
            "language": node.language or project.preferred_language,
        }
        if node.notebook_id is not None:
            if await db.get(Notebook, node.notebook_id) is None:
                raise HTTPException(
                    status_code=404, detail=f"节点 {node.id} 引用的 Notebook 不存在: {node.notebook_id}"
                )
            item["notebook_id"] = str(node.notebook_id)
            notebook_refs.append(str(node.notebook_id))
        if node.workload_id is not None:
            if await db.get(Workload, node.workload_id) is None:
                raise HTTPException(
                    status_code=404, detail=f"节点 {node.id} 引用的 Workload 不存在: {node.workload_id}"
                )
            item["workload_id"] = str(node.workload_id)
            workload_refs.append(str(node.workload_id))
        nodes.append(item)

    # 整体换新对象：JSON 列没有脏跟踪，原地改 list 不会落库（见本模块头的事务说明）。
    existing = _json_dict(project.dag_definition)
    context = existing.get("scenario_context")
    dag: dict[str, object] = {
        "version": DAG_VERSION,
        "language": project.preferred_language,
        "nodes": nodes,
        "edges": [{"source": source, "target": target} for source, target in edges],
        "saved_at": _now_iso(),
    }
    if isinstance(context, dict):
        dag["scenario_context"] = context

    project.dag_definition = dag
    project.linked_notebook_ids = _dedup(notebook_refs)
    project.linked_workload_ids = _dedup(workload_refs)
    await db.flush()
    await db.refresh(project)

    await audit_service.log(
        db,
        action="project.update_dag",
        resource_type="modeling_project",
        user_id=user.id,
        resource_id=str(project.id),
        detail={
            "node_count": len(nodes),
            "edge_count": len(edges),
            "node_ids": [node_id for node_id in sorted(known_ids)],
            "languages": sorted({str(node["language"]) for node in nodes}),
        },
    )
    return ProjectResponse.model_validate(_project_payload(project))


@router.post("/{project_id}/dag/validate", response_model=DagValidateResponse)
async def validate_project_dag(
    project_id: uuid.UUID,
    user: User = Depends(get_business_user),
    db: AsyncSession = Depends(get_db),
) -> DagValidateResponse:
    """对库里的画布跑一次 Kahn 拓扑校验（不要求先保存草稿之外的任何东西）。"""
    project = await _load_project(db, project_id, user)
    dag = _json_dict(project.dag_definition)
    nodes = [item for item in _json_list(dag.get("nodes")) if isinstance(item, dict)]
    raw_edges = [item for item in _json_list(dag.get("edges")) if isinstance(item, dict)]

    node_ids = [str(node.get("id")) for node in nodes if node.get("id") is not None]
    edges = [
        (str(edge.get("source")), str(edge.get("target")))
        for edge in raw_edges
        if edge.get("source") is not None and edge.get("target") is not None
    ]
    order, cyclic, errors = _topological_order(node_ids, edges)
    return DagValidateResponse(
        valid=not errors,
        execution_order=order,
        cyclic_nodes=cyclic,
        errors=errors,
        node_count=len(node_ids),
        edge_count=len(edges),
    )


@router.post("/{project_id}/submit-review")
async def submit_project_review(
    project_id: uuid.UUID,
    user: User = Depends(get_business_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """把项目成果提交运营审核：状态进 submitted_review，画布随即冻结。"""
    project = await _load_project(db, project_id, user)
    if project.status != ProjectStatus.ACTIVE.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"项目当前状态为 {project.status}，只有进行中的项目可以提交审核",
        )

    dag = _json_dict(project.dag_definition)
    project.status = ProjectStatus.SUBMITTED_REVIEW.value
    await db.flush()
    await db.refresh(project)

    await audit_service.log(
        db,
        action="project.submit_review",
        resource_type="modeling_project",
        user_id=user.id,
        resource_id=str(project.id),
        detail={
            "name": project.name,
            "node_count": len(_json_list(dag.get("nodes"))),
            "edge_count": len(_json_list(dag.get("edges"))),
            "status": project.status,
        },
    )
    return {"id": str(project.id), "status": project.status, "name": project.name}


def _dedup(items: list[str]) -> list[str]:
    """去重但保持节点声明顺序（linked_* 的展示顺序就是编排顺序）。"""
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out
