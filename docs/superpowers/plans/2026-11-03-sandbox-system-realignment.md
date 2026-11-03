# 密态数据沙箱系统功能对齐与演进实施计划 (Sandbox System Realignment Plan v2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 CDS 密态数据沙箱系统与《可信数据空间服务平台建设 - 数据开发应用系统》及 TEE 沙箱规范全面对齐：在前端导航层收敛非沙箱越界入口（连接器、跨空间联邦、联邦训练、本地路径管线，并将 Shamir 密钥分片归位至密钥管理），同时在不破坏任何 CI 门禁（`test_frontend_api_contract.py`、`verify_schema_parity.py`、SS-12 权限域、SS-18 原型门禁）的前提下，补齐场景管理、建模项目与 DAG 编排、团队多租户、R/PySpark 多语言、自动化敏感数据探查、数据产品版本比对/回退、模型评估与安全扫描、计量对账单导出及顶栏通知中心。

**Architecture:**
1. **导航收敛而非破坏性删路由（遵循 B-2 退役不删路由原则）**：后端保持存量路由挂载与 `cds-frontend/src/services/*.ts` 契约不动，在 `Sidebar.tsx` 收敛管理台越界菜单并同步更新 `Sidebar.test.tsx`，确保前后端契约测试与存量测试 100% 绿灯；
2. **复用 SS-12 权限域前缀与 SS-15 流通闭环**：所有新增路由均挂载于已映射的 SS-12 权限组前缀下（`/api/v1/flow/*`、`/api/v1/users/*`、`/api/v1/deliveries/*`、`/api/v1/monitoring/*`），`ModelingProject` 作为 `Notebook`、`Workload`、`ComputeRun`、`Contract` 的上层聚合空间；
3. **严格遵守本仓 ORM 与 Alembic 铁律**：所有新表主键使用 PostgreSQL `UUID(as_uuid=True)`、扩展字段使用 `JSONB`、布尔默认使用 `sa_text("false")`、时间戳使用 `DateTime(timezone=True), server_default=func.now()`，在 `app/main.py` 注册模型（严禁修改 `app/models/__init__.py`），通过线性迁移 `0035_sandbox_realignment_entities.py` 保持 `verify_schema_parity.py` 100% 对齐；
4. **最大化复用既有核心服务**：直接复用 `pii_ner.py`、`test_data_generator.py`、`quota_manager.py`、`code_scanner.py`、`alert_center.py`，严禁重复编写第 4 套中文 PII 正则或孤立配额器。

**Tech Stack:** FastAPI (Python 3.12), SQLAlchemy 2.0 (asyncpg / aiosqlite), Alembic (`0034` → `0035`), React 19 + TypeScript, Vite 8, Ant Design 5, Vitest, Pytest.

---

## 阶段与任务总览 (12 Tasks)

```
┌──────────────────────────────────────────────────────────────────────────┐
│ Phase 1: 前端导航边界收敛与存量门禁对齐 (Menu Pruning & Gate Alignment)     │
│  - Task 1: 管理台侧边栏越界菜单收敛（移除连接器/联邦/联合训练/本地管线等）    │
│  - Task 2: 路由守卫与前后端 API 契约对账回归验证                           │
├──────────────────────────────────────────────────────────────────────────┤
│ Phase 2: 核心实体、团队多租户、场景与建模项目 (Teams, Scenarios, Projects)  │
│  - Task 3: ORM 模型定义与 Alembic 0035 线性迁移 (通过 schema parity 门禁)  │
│  - Task 4: 团队管理与團隊级配额隔离 API 及前端页面 (`/api/v1/users/teams`) │
│  - Task 5: 业务场景管理（开发场景/交付场景承接转项目）API 及前端页面        │
│  - Task 6: 建模项目协同空间、DAG 任务流编排与多语言 (R/PySpark) 支持        │
├──────────────────────────────────────────────────────────────────────────┤
│ Phase 3: 数据资产自动探查、产品版本比对与模型生命周期 (Data & Model)       │
│  - Task 7: 复用 pii_ner 与 test_data_generator 实现自动分类分级与调试采样  │
│  - Task 8: 数据产品版本 Schema 比对 (Diff) 与版本回退 (Rollback)           │
│  - Task 9: 模型安全扫描（反序列化后门拦截）与模型评估指标对比               │
├──────────────────────────────────────────────────────────────────────────┤
│ Phase 4: 产品计量对账单与顶栏通知/个性化中心 (Billing & Notifications)     │
│  - Task 10: 产品计量自动汇总与对账单生成/CSV 导出 (`/api/v1/deliveries/*`) │
│  - Task 11: 计量对账单前端查询与导出视图                                   │
│  - Task 12: 顶栏聚合通知中心（待签合约/待审出域/告警）与工作台个性化设置    │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## Phase 1: 前端导航边界收敛与存量门禁对齐

### Task 1: 管理台侧边栏越界菜单收敛与单测同步

**Files:**
- Modify: `cds-frontend/src/components/Layout/Sidebar.tsx`
- Modify: `cds-frontend/src/components/Layout/__tests__/Sidebar.test.tsx`

- [ ] **Step 1: 在 `Sidebar.test.tsx` 中添加管理台边界收敛断言**

在 `cds-frontend/src/components/Layout/__tests__/Sidebar.test.tsx` 末尾追加测试：

```tsx
describe('沙箱系统边界收敛（剔除非沙箱越界菜单）', () => {
  it('管理员工台不再展示跨域联邦、联合训练、连接器管理、数据管线与顶层MPC菜单', () => {
    useWorkspaceStore.setState({ workspace: 'admin' });
    useAuthStore.setState({
      user: { id: 'admin-1', username: 'admin', email: 'a@test.com', role: 'admin', organization: null },
    });
    renderWithProviders(<Sidebar />);
    expect(screen.queryByText('跨空间联邦')).not.toBeInTheDocument();
    expect(screen.queryByText('联合训练')).not.toBeInTheDocument();
    expect(screen.queryByText('连接器管理')).not.toBeInTheDocument();
    expect(screen.queryByText('数据管线')).not.toBeInTheDocument();
    expect(screen.queryByText('MPC 密钥管理')).not.toBeInTheDocument();
  });
});
```

- [ ] **Step 2: 运行前端单测验证失败**

Run: `cd cds-frontend && npx vitest run src/components/Layout/__tests__/Sidebar.test.tsx`
Expected: FAIL（当前 `buildAdminMenu` 仍渲染上述菜单项）

- [ ] **Step 3: 修改 `Sidebar.tsx` 收敛 `buildAdminMenu` 越界菜单**

在 `cds-frontend/src/components/Layout/Sidebar.tsx` 的 `buildAdminMenu` 中：
1. 移除 `/data-pipeline`（数据管线）、`/federation`（跨空间联邦）、`/training`（联合训练）、`/connectors`（连接器管理）、`/mpc`（顶层 MPC 菜单，Shamir 分片仅作为密钥底层能力保留路由可达性，不在一级导航暴露为隐私计算）；
2. 保留 `沙箱管理`、`数据产品`、`数据资源`、`数据目录`、`合约管理`、`我的开发`、`我的结果集`、`输出管控`、`安全管理`、`监控中心`、`身份管理`、`K8s 运营`（仅限 `k8sOpsReaders` 角色在管理员工台查看集群健康态，确保 `Sidebar.test.tsx` 既有断言通过）、`用户管理`、`API 密钥`；
3. 清理 `Sidebar.tsx` 中因移除菜单而不再使用的图标导入（如 `GlobalOutlined`、`ExperimentOutlined`、`NodeIndexOutlined`），防止 `tsc -b` 报 `TS6133: declared but never read`。

- [ ] **Step 4: 运行 `Sidebar.test.tsx` 与 TypeScript 编译验证通过**

Run: `cd cds-frontend && npx vitest run src/components/Layout/__tests__/Sidebar.test.tsx && npm run build`
Expected: PASS

---

### Task 2: 路由守卫与前后端 API 契约对账验证

**Files:**
- Test: `tests/test_frontend_api_contract.py`
- Test: `cds-frontend/src/components/Layout/__tests__/App.routes.test.tsx`

- [ ] **Step 1: 运行前后端 API 契约守卫与路由落点测试**

Run: `.venv/bin/python -m pytest tests/test_frontend_api_contract.py -v && cd cds-frontend && npx vitest run src/components/Layout/__tests__/App.routes.test.tsx`
Expected: PASS（后端路由与 `services/*.ts` 保持 100% 契约对齐，零 404 或悬空调用）

- [ ] **Step 2: 提交 Phase 1 变更**

```bash
git add cds-frontend/src/components/Layout/Sidebar.tsx cds-frontend/src/components/Layout/__tests__/Sidebar.test.tsx
git commit -m "refactor(fe): prune non-sandbox menu items from admin sidebar"
```

---

## Phase 2: 核心实体、团队多租户、场景与建模项目

### Task 3: ORM 模型定义与 Alembic 0035 线性迁移

**Files:**
- Create: `app/models/team.py`
- Create: `app/models/scenario.py`
- Create: `app/models/modeling_project.py`
- Create: `app/models/billing_invoice.py`
- Create: `app/models/user_notification.py`
- Modify: `app/main.py`
- Create: `alembic/versions/0035_sandbox_realignment_entities.py`
- Test: `scripts/verify_schema_parity.py`

- [ ] **Step 1: 创建符合本仓 ORM 规范的 5 个模型文件**

1. 创建 `app/models/team.py`：
```python
from enum import Enum
from uuid import uuid4
from sqlalchemy import Column, DateTime, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from app.core.database import Base


class TeamRole(str, Enum):
    ADMIN = "team_admin"
    DEVELOPER = "core_developer"
    ANALYST = "data_analyst"
    VIEWER = "viewer"


class Team(Base):
    __tablename__ = "teams"
    __table_args__ = (
        Index("ix_teams_org_id", "org_id"),
        Index("ix_teams_status", "status"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    name = Column(String(128), nullable=False)
    org_id = Column(String(128), nullable=True)
    description = Column(String(512), nullable=True)
    status = Column(String(32), nullable=False, default="active")
    cpu_quota_cores = Column(Integer, nullable=False, default=16)
    memory_quota_gb = Column(Integer, nullable=False, default=64)
    storage_quota_gb = Column(Integer, nullable=False, default=500)
    members = Column(JSONB, nullable=False, default=list)
    role_permissions = Column(JSONB, nullable=False, default=dict)
    created_by = Column(UUID(as_uuid=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
```

2. 创建 `app/models/scenario.py`：
```python
from enum import Enum
from uuid import uuid4
from sqlalchemy import Column, DateTime, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from app.core.database import Base


class ScenarioStatus(str, Enum):
    PUBLISHED = "published"
    ACCEPTED = "accepted"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class DeliverySceneType(str, Enum):
    API = "api"
    DATASET_FILE = "dataset_file"
    TEE_COMPUTE = "tee_compute"


class Scenario(Base):
    __tablename__ = "scenarios"
    __table_args__ = (
        Index("ix_scenarios_status", "status"),
        Index("ix_scenarios_delivery_scene", "delivery_scene"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    title = Column(String(256), nullable=False)
    industry = Column(String(64), nullable=True)
    business_background = Column(Text, nullable=True)
    target_description = Column(Text, nullable=True)
    delivery_scene = Column(String(32), nullable=False, default=DeliverySceneType.API.value)
    status = Column(String(32), nullable=False, default=ScenarioStatus.PUBLISHED.value)
    suggested_resource_ids = Column(JSONB, nullable=False, default=list)
    compliance_constraints = Column(JSONB, nullable=False, default=dict)
    delivery_tech_config = Column(JSONB, nullable=False, default=dict)
    accepted_team_id = Column(UUID(as_uuid=True), nullable=True)
    accepted_by = Column(UUID(as_uuid=True), nullable=True)
    created_by = Column(UUID(as_uuid=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
```

3. 创建 `app/models/modeling_project.py`：
```python
from enum import Enum
from uuid import uuid4
from sqlalchemy import Column, DateTime, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from app.core.database import Base


class ProjectStatus(str, Enum):
    ACTIVE = "active"
    SUBMITTED_REVIEW = "submitted_review"
    APPROVED = "approved"
    ARCHIVED = "archived"


class ModelingProject(Base):
    __tablename__ = "modeling_projects"
    __table_args__ = (
        Index("ix_modeling_projects_team_id", "team_id"),
        Index("ix_modeling_projects_scenario_id", "scenario_id"),
        Index("ix_modeling_projects_status", "status"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    name = Column(String(128), nullable=False)
    description = Column(Text, nullable=True)
    team_id = Column(UUID(as_uuid=True), nullable=True)
    scenario_id = Column(UUID(as_uuid=True), nullable=True)
    contract_id = Column(UUID(as_uuid=True), nullable=True)
    environment_type = Column(String(32), nullable=False, default="l2c")
    preferred_language = Column(String(32), nullable=False, default="python")
    status = Column(String(32), nullable=False, default=ProjectStatus.ACTIVE.value)
    participants = Column(JSONB, nullable=False, default=list)
    milestones = Column(JSONB, nullable=False, default=list)
    dag_definition = Column(JSONB, nullable=False, default=dict)
    linked_notebook_ids = Column(JSONB, nullable=False, default=list)
    linked_workload_ids = Column(JSONB, nullable=False, default=list)
    created_by = Column(UUID(as_uuid=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
```

4. 创建 `app/models/billing_invoice.py`：
```python
from enum import Enum
from uuid import uuid4
from sqlalchemy import Column, DateTime, Float, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from app.core.database import Base


class InvoiceStatus(str, Enum):
    GENERATED = "generated"
    CONFIRMED = "confirmed"
    DISPUTED = "disputed"
    SETTLED = "settled"


class BillingInvoice(Base):
    __tablename__ = "billing_invoices"
    __table_args__ = (
        Index("ix_billing_invoices_contract_id", "contract_id"),
        Index("ix_billing_invoices_billing_period", "billing_period"),
        Index("ix_billing_invoices_status", "status"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    invoice_no = Column(String(64), nullable=False, unique=True)
    contract_id = Column(UUID(as_uuid=True), nullable=False)
    product_id = Column(UUID(as_uuid=True), nullable=True)
    buyer_id = Column(UUID(as_uuid=True), nullable=True)
    billing_period = Column(String(32), nullable=False)
    api_calls_count = Column(Integer, nullable=False, default=0)
    compute_cpu_hours = Column(Float, nullable=False, default=0.0)
    storage_gb_days = Column(Float, nullable=False, default=0.0)
    egress_bytes = Column(Integer, nullable=False, default=0)
    unit_price_config = Column(JSONB, nullable=False, default=dict)
    total_amount = Column(Float, nullable=False, default=0.0)
    line_items = Column(JSONB, nullable=False, default=list)
    blockchain_tx_hash = Column(String(128), nullable=True)
    status = Column(String(32), nullable=False, default=InvoiceStatus.GENERATED.value)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
```

5. 创建 `app/models/user_notification.py`：
```python
from enum import Enum
from uuid import uuid4
from sqlalchemy import Boolean, Column, DateTime, Index, String, Text, func, text as sa_text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from app.core.database import Base


class NotificationCategory(str, Enum):
    TODO = "todo"
    SYSTEM = "system"
    BUSINESS = "business"


class UserNotification(Base):
    __tablename__ = "user_notifications"
    __table_args__ = (
        Index("ix_user_notifications_user_read", "user_id", "is_read"),
        Index("ix_user_notifications_category", "category"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id = Column(UUID(as_uuid=True), nullable=True)
    category = Column(String(32), nullable=False, default=NotificationCategory.BUSINESS.value)
    title = Column(String(256), nullable=False)
    content = Column(Text, nullable=True)
    target_path = Column(String(256), nullable=True)
    is_read = Column(Boolean, nullable=False, default=False, server_default=sa_text("false"))
    extra_meta = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
```

- [ ] **Step 2: 在 `app/main.py` 导入新模型（不碰 `app/models/__init__.py`）**

在 `app/main.py` 的模型导入块（约第 50-95 行）追加：
```python
import app.models.team  # noqa: F401
import app.models.scenario  # noqa: F401
import app.models.modeling_project  # noqa: F401
import app.models.billing_invoice  # noqa: F401
import app.models.user_notification  # noqa: F401
```

- [ ] **Step 3: 编写 Alembic 迁移 `alembic/versions/0035_sandbox_realignment_entities.py`**

`down_revision = "0034_integrity_patrol_columns"`，严格按上述 5 张表的列、类型、可空性、`server_default` 与索引名创建 `teams`、`scenarios`、`modeling_projects`、`billing_invoices`、`user_notifications`，并在 `downgrade()` 中按逆序 `drop_index` 与 `drop_table`。

- [ ] **Step 4: 运行 Alembic 升级与 Schema Parity 门禁验证**

Run:
```bash
CDS_DATABASE_URL="sqlite+aiosqlite:///./ci.db" .venv/bin/python -m alembic upgrade head && ALEMBIC_COMPARE_TYPES=0 .venv/bin/python -m alembic check && .venv/bin/python scripts/verify_schema_parity.py
```
Expected: PASS（0 差异）

---

### Task 4: 团队管理与团队级配额隔离 (`/api/v1/users/teams`)

**Files:**
- Create: `app/api/teams.py`
- Modify: `app/main.py`
- Create: `cds-frontend/src/services/teamApi.ts`
- Create: `cds-frontend/src/pages/Identity/TeamManagement.tsx`
- Modify: `cds-frontend/src/App.tsx`
- Modify: `cds-frontend/src/components/Layout/Sidebar.tsx`
- Test: `tests/test_teams_api.py`

- [ ] **Step 1: 编写团队管理与成员角色测试 `tests/test_teams_api.py`**

```python
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_team_crud_and_member_roles(client: AsyncClient, make_user):
    admin_headers, admin_id = await make_user(role="admin", prefix="team_adm")
    dev_headers, dev_id = await make_user(role="data_provider", prefix="team_dev")

    # 1. 创建团队并配置资源配额
    resp = await client.post(
        "/api/v1/users/teams",
        json={
            "name": "金融风控创新实验室",
            "org_id": "org-fin-01",
            "description": "跨机构风控建模团队",
            "cpu_quota_cores": 32,
            "memory_quota_gb": 128,
            "storage_quota_gb": 1000,
        },
        headers=admin_headers,
    )
    assert resp.status_code == 201
    team = resp.json()
    team_id = team["id"]
    assert team["cpu_quota_cores"] == 32

    # 2. 添加成员并分配团队内角色
    resp_mem = await client.post(
        f"/api/v1/users/teams/{team_id}/members",
        json={"user_id": dev_id, "role": "core_developer", "permissions": ["view", "edit", "run"]},
        headers=admin_headers,
    )
    assert resp_mem.status_code == 200
    assert len(resp_mem.json()["members"]) == 1

    # 3. 开发者查询所属团队与配额
    resp_list = await client.get("/api/v1/users/teams", headers=dev_headers)
    assert resp_list.status_code == 200
    assert resp_list.json()["total"] >= 1
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_teams_api.py -v`
Expected: FAIL (404 / 422)

- [ ] **Step 3: 实现 `app/api/teams.py` 并挂载到 `/api/v1/users/teams`**

在 `app/api/teams.py` 实现：
- `GET /`: 返回当前用户可见团队列表 `{items, total, page, page_size}`
- `POST /`: 创建团队并设置 CPU/内存/存储配额 + 记录 `audit_service.log(db, action="team.create", ...)`
- `PATCH /{team_id}`: 更新配额与自定义角色权限映射 `role_permissions`
- `POST /{team_id}/members`: 添加/更新团队成员角色
- `DELETE /{team_id}/members/{user_id}`: 移除团队成员

在 `app/main.py` 挂载（放在 `users.router` 之前以避免被 `/api/v1/users/{user_id}` 拦截，继承 `/api/v1/users` 的 `require_human` 域）：
```python
app.include_router(teams.router, prefix="/api/v1/users/teams", tags=["teams"], dependencies=[Depends(require_human)])
```

- [ ] **Step 4: 实现前端 `teamApi.ts` 与 `TeamManagement.tsx` 并注册路由 `/teams`**

- [ ] **Step 5: 运行后端测试与前端契约检查**

Run: `.venv/bin/python -m pytest tests/test_teams_api.py tests/test_frontend_api_contract.py -v`
Expected: PASS

---

### Task 5: 业务场景管理（数据开发场景与数据交付场景）

**Files:**
- Create: `app/api/scenarios.py`
- Modify: `app/main.py`
- Create: `cds-frontend/src/services/scenarioApi.ts`
- Create: `cds-frontend/src/pages/Flow/ScenarioCenter.tsx`
- Modify: `cds-frontend/src/App.tsx`
- Test: `tests/test_scenarios_api.py`

- [ ] **Step 1: 编写场景管理与承接转化测试 `tests/test_scenarios_api.py`**

```python
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_scenario_create_and_accept_into_project(client: AsyncClient, auth_headers: dict):
    # 1. 创建包含开发要求与交付场景预埋的场景
    create_resp = await client.post(
        "/api/v1/flow/scenarios",
        json={
            "title": "普惠金融中小微企业信贷评估场景",
            "industry": "金融",
            "business_background": "结合纳税与司法数据进行企业信贷违约预测",
            "target_description": "输出违约概率评分 API 服务",
            "delivery_scene": "api",
            "compliance_constraints": {"require_dp": True, "max_epsilon": 1.0},
            "delivery_tech_config": {"rate_limit_qps": 50, "response_format": "json"},
        },
        headers=auth_headers,
    )
    assert create_resp.status_code == 201
    scenario_id = create_resp.json()["id"]

    # 2. 开发者团队承接该场景并一键转化为建模项目
    accept_resp = await client.post(
        f"/api/v1/flow/scenarios/{scenario_id}/accept",
        json={"project_name": "中小微信贷评分模型项目", "preferred_language": "python"},
        headers=auth_headers,
    )
    assert accept_resp.status_code == 200
    body = accept_resp.json()
    assert body["scenario"]["status"] == "accepted"
    assert body["project"]["scenario_id"] == scenario_id
    assert body["project"]["name"] == "中小微信贷评分模型项目"
```

- [ ] **Step 2: 实现 `app/api/scenarios.py` 并挂载在 `/api/v1/flow/scenarios`**

支持：
- `GET /api/v1/flow/scenarios`（支持按 `status`、`delivery_scene`、`industry` 筛选）
- `POST /api/v1/flow/scenarios`（创建开发场景及交付场景技术预埋配置）
- `GET /api/v1/flow/scenarios/{scenario_id}`（查看场景上下文详情）
- `POST /api/v1/flow/scenarios/{scenario_id}/accept`（承接场景并自动创建 `ModelingProject`，把场景的合规约束和交付方式自动注入项目配置）

在 `app/main.py` 挂载：
```python
app.include_router(scenarios.router, prefix="/api/v1/flow/scenarios", tags=["flow"], dependencies=[Depends(require_group("sessions"))])
```

- [ ] **Step 3: 实现前端 `scenarioApi.ts` 与 `ScenarioCenter.tsx`（含开发场景与交付场景双视图）**

- [ ] **Step 4: 验证后端测试与前端契约门禁**

Run: `.venv/bin/python -m pytest tests/test_scenarios_api.py tests/test_frontend_api_contract.py -v`
Expected: PASS

---

### Task 6: 建模项目协同空间、DAG 工作流编排与多语言 (R / PySpark) 支持

**Files:**
- Create: `app/api/projects.py`
- Modify: `app/models/notebook.py`
- Modify: `app/services/code_scanner.py`
- Modify: `app/main.py`
- Create: `cds-frontend/src/services/projectApi.ts`
- Create: `cds-frontend/src/pages/Flow/ProjectCenter.tsx`
- Modify: `cds-frontend/src/App.tsx`
- Test: `tests/test_projects_and_dag.py`

- [ ] **Step 1: 编写建模项目、多方协作、DAG 编排与 R/PySpark 多语言测试 `tests/test_projects_and_dag.py`**

```python
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_modeling_project_dag_and_multilang(client: AsyncClient, auth_headers: dict):
    # 1. 创建支持 PySpark 的多方协作建模项目
    resp = await client.post(
        "/api/v1/flow/projects",
        json={
            "name": "跨机构联合特征工程项目",
            "preferred_language": "pyspark",
            "participants": [{"org_name": "数据提供方A", "role": "data_provider"}],
            "milestones": [{"title": "特征清洗完成", "status": "pending"}],
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201
    proj_id = resp.json()["id"]

    # 2. 保存可视化 DAG 任务编排定义（含 SQL 清洗 -> PySpark 特征 -> Python 训练）
    dag_payload = {
        "nodes": [
            {"id": "n1", "type": "sql_clean", "label": "SQL缺失值填充", "language": "sql"},
            {"id": "n2", "type": "feature_eng", "label": "PySpark特征衍生", "language": "pyspark"},
            {"id": "n3", "type": "train", "label": "R统计检验", "language": "r"},
        ],
        "edges": [
            {"source": "n1", "target": "n2"},
            {"source": "n2", "target": "n3"},
        ],
    }
    dag_resp = await client.put(
        f"/api/v1/flow/projects/{proj_id}/dag",
        json=dag_payload,
        headers=auth_headers,
    )
    assert dag_resp.status_code == 200
    assert len(dag_resp.json()["dag_definition"]["nodes"]) == 3

    # 3. 校验 DAG 拓扑无环并触发编排校验
    validate_resp = await client.post(
        f"/api/v1/flow/projects/{proj_id}/dag/validate",
        headers=auth_headers,
    )
    assert validate_resp.status_code == 200
    assert validate_resp.json()["valid"] is True
    assert validate_resp.json()["execution_order"] == ["n1", "n2", "n3"]
```

- [ ] **Step 2: 实现 `app/api/projects.py`（含项目看板、多参与方协同、里程碑、DAG 拓扑排序校验与提交审核）**

端点：
- `GET /api/v1/flow/projects`: 项目列表
- `POST /api/v1/flow/projects`: 创建建模项目（支持 `python`, `sql`, `r`, `pyspark`）
- `GET /api/v1/flow/projects/{project_id}`: 项目详情（聚合关联的 `notebooks` 与 `workloads`）
- `PUT /api/v1/flow/projects/{project_id}/dag`: 保存可视化工作流 DAG 节点与连线
- `POST /api/v1/flow/projects/{project_id}/dag/validate`: Kahn 拓扑排序检测有向无环图并返回执行序列
- `POST /api/v1/flow/projects/{project_id}/submit-review`: 将项目成果在线提交运营审核

- [ ] **Step 3: 扩展 `Notebook` 与前端支持 `python` / `sql` / `r` / `pyspark` 四种语言选项**

- [ ] **Step 4: 运行测试验证通过**

Run: `.venv/bin/python -m pytest tests/test_projects_and_dag.py tests/test_frontend_api_contract.py -v`
Expected: PASS

---

## Phase 3: 数据资产自动探查、产品版本比对与模型生命周期

### Task 7: 复用 `pii_ner` 与 `test_data_generator` 实现数据资源自动分类分级探查

**Files:**
- Modify: `app/api/data_resources.py`
- Modify: `cds-frontend/src/services/dataResourceApi.ts`
- Modify: `cds-frontend/src/pages/DataResources/ResourceList.tsx`
- Test: `tests/test_data_resource_auto_profile.py`

- [ ] **Step 1: 编写数据资源自动探查分类分级测试 `tests/test_data_resource_auto_profile.py`**

```python
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_auto_profile_data_resource_sensitivity(client: AsyncClient, auth_headers: dict):
    # 1. 上传含身份证与手机号的 CSV 数据资源
    csv_bytes = "name,id_card,mobile,revenue\n张三,110101199003072341,13800138000,50000\n".encode("utf-8")
    up_resp = await client.post(
        "/api/v1/data-resources/upload",
        files={"file": ("customers.csv", csv_bytes, "text/csv")},
        data={"name": "客户敏感信息表", "data_type": "structured"},
        headers=auth_headers,
    )
    assert up_resp.status_code == 201
    res_id = up_resp.json()["id"]

    # 2. 调用自动探查分类分级接口（底层复用 pii_ner）
    prof_resp = await client.post(
        f"/api/v1/data-resources/{res_id}/auto-profile",
        headers=auth_headers,
    )
    assert prof_resp.status_code == 200
    body = prof_resp.json()
    assert body["sensitivity_level"] in ("high", "L1", "sensitive")
    detected_types = {col["field"]: col["pii_type"] for col in body["field_classifications"] if col.get("pii_type")}
    assert "id_card" in detected_types or "mobile" in detected_types
```

- [ ] **Step 2: 在 `app/api/data_resources.py` 中新增 `POST /{resource_id}/auto-profile`**

直接调用仓内已有的 `app/services/pii_ner.py` 对存储层采样数据进行字段扫描，识别身份证、手机号、银行卡、邮箱，生成字段级敏感标签与分类分级依据并更新 `DataResource` 元数据，严禁新建重复的正则表。

- [ ] **Step 3: 在前端 `ResourceList.tsx` 增加「自动探查分级」按钮与分级报告展示**

- [ ] **Step 4: 运行测试验证通过**

Run: `.venv/bin/python -m pytest tests/test_data_resource_auto_profile.py tests/test_frontend_api_contract.py -v`
Expected: PASS

---

### Task 8: 数据产品版本 Schema 比对 (Diff) 与版本回退 (Rollback)

**Files:**
- Modify: `app/api/data_products.py`
- Modify: `cds-frontend/src/services/dataProductApi.ts`
- Modify: `cds-frontend/src/pages/DataProducts/ProductDetail.tsx`
- Test: `tests/test_data_product_version_diff.py`

- [ ] **Step 1: 编写版本比对与版本回退测试 `tests/test_data_product_version_diff.py`**

```python
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_product_version_diff_and_rollback(client: AsyncClient, auth_headers: dict):
    # 1. 创建 v1 数据产品
    r1 = await client.post(
        "/api/v1/data-products",
        json={
            "name": "企业纳税信用产品",
            "category": "政务",
            "data_type": "structured",
            "security_level": "L2",
            "schema_definition": {"fields": [{"name": "tax_id", "type": "string"}]},
        },
        headers=auth_headers,
    )
    assert r1.status_code == 201
    p1_id = r1.json()["id"]

    # 2. 创建新版本 v2（增加 credit_score 字段）
    r2 = await client.post(
        f"/api/v1/data-products/{p1_id}/versions",
        json={
            "version": "2.0.0",
            "schema_definition": {
                "fields": [
                    {"name": "tax_id", "type": "string"},
                    {"name": "credit_score", "type": "float"},
                ]
            },
            "changelog": "新增信用评分字段",
        },
        headers=auth_headers,
    )
    assert r2.status_code in (200, 201)

    # 3. 比对版本差异
    diff_resp = await client.get(
        f"/api/v1/data-products/{p1_id}/versions/diff?from_version=1.0&to_version=2.0.0",
        headers=auth_headers,
    )
    assert diff_resp.status_code == 200
    assert "credit_score" in diff_resp.json()["added_fields"]

    # 4. 回退到 1.0 版本
    rb_resp = await client.post(
        f"/api/v1/data-products/{p1_id}/versions/rollback",
        json={"target_version": "1.0"},
        headers=auth_headers,
    )
    assert rb_resp.status_code == 200
    assert rb_resp.json()["current_version"] == "1.0"
```

- [ ] **Step 2: 在 `app/api/data_products.py` 实现版本創建、Schema Diff 与版本回退端点**

- [ ] **Step 3: 在 `ProductDetail.tsx` 增加版本比对与一键回退抽屉**

- [ ] **Step 4: 运行测试验证通过**

Run: `.venv/bin/python -m pytest tests/test_data_product_version_diff.py tests/test_frontend_api_contract.py -v`
Expected: PASS

---

### Task 9: 模型安全扫描与模型评估指标对比

**Files:**
- Modify: `app/services/code_scanner.py`
- Modify: `app/api/workloads.py`
- Modify: `cds-frontend/src/pages/Flow/WorkloadCenter.tsx`
- Test: `tests/test_workload_model_scan_and_eval.py`

- [ ] **Step 1: 编写模型恶意载荷扫描与评估指标对比测试 `tests/test_workload_model_scan_and_eval.py`**

```python
from app.services.code_scanner import scan_model_artifact_bytes


def test_scan_model_artifact_blocks_pickle_exec():
    malicious_pickle = b"cos\nsystem\n(S'id'\ntR."
    result = scan_model_artifact_bytes(malicious_pickle, filename="model.pkl")
    assert result["passed"] is False
    assert any("os.system" in r or "pickle" in r.lower() for r in result["violations"])


def test_scan_model_artifact_allows_clean_safetensors_header():
    clean_bytes = b'{"__metadata__": {"format": "pt"}, "weight": {"dtype": "F32"}}'
    result = scan_model_artifact_bytes(clean_bytes, filename="model.safetensors")
    assert result["passed"] is True
```

- [ ] **Step 2: 在 `app/services/code_scanner.py` 实现 `scan_model_artifact_bytes` 并在 `app/api/workloads.py` 增加模型扫描与多版本指标对比接口 `GET /api/v1/workloads/metrics-compare`**

- [ ] **Step 3: 运行测试验证通过**

Run: `.venv/bin/python -m pytest tests/test_workload_model_scan_and_eval.py -v`
Expected: PASS

---

## Phase 4: 产品计量对账单与顶栏通知/个性化中心

### Task 10: 产品计量自动汇总与对账单生成/导出 (`/api/v1/deliveries/invoices`)

**Files:**
- Create: `app/api/billing.py`
- Modify: `app/main.py`
- Test: `tests/test_billing_invoices.py`

- [ ] **Step 1: 编写计量对账单生成与 CSV 导出测试 `tests/test_billing_invoices.py`**

```python
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_generate_and_export_billing_invoice(client: AsyncClient, auth_headers: dict):
    # 1. 创建测试数据产品与合约
    prod_resp = await client.post(
        "/api/v1/data-products",
        json={"name": "计量测试产品", "category": "金融", "data_type": "structured", "security_level": "L2"},
        headers=auth_headers,
    )
    product_id = prod_resp.json()["id"]

    contract_resp = await client.post(
        "/api/v1/contracts",
        json={
            "name": "计量测试合约",
            "product_id": product_id,
            "Subscriber_id": "00000000-0000-0000-0000-000000000001",
            "max_queries": 1000,
        },
        headers=auth_headers,
    )
    contract_id = contract_resp.json()["id"]

    # 2. 触发生成指定账期的对账单
    gen_resp = await client.post(
        "/api/v1/deliveries/invoices/generate",
        json={
            "contract_id": contract_id,
            "billing_period": "2026-11",
            "api_call_unit_price": 0.5,
            "cpu_hour_unit_price": 2.0,
        },
        headers=auth_headers,
    )
    assert gen_resp.status_code == 201
    inv = gen_resp.json()
    invoice_id = inv["id"]
    assert inv["billing_period"] == "2026-11"

    # 3. 导出 CSV 对账单
    csv_resp = await client.get(
        f"/api/v1/deliveries/invoices/{invoice_id}/export",
        headers=auth_headers,
    )
    assert csv_resp.status_code == 200
    assert "text/csv" in csv_resp.headers["content-type"]
    assert "2026-11" in csv_resp.text
```

- [ ] **Step 2: 实现 `app/api/billing.py` 并挂载在 `/api/v1/deliveries/invoices`**

汇总关联 `contract_id` 下的 `api_endpoints.calls_used`、`compute_runs` 执行时长、`delivery_bundles` 字节流量，生成结构化对账单并支持 CSV 流式导出与存证哈希关联。
在 `app/main.py` 挂载：
```python
app.include_router(billing.router, prefix="/api/v1/deliveries/invoices", tags=["deliveries"], dependencies=[Depends(require_group("gateway"))])
```

- [ ] **Step 3: 运行测试验证通过**

Run: `.venv/bin/python -m pytest tests/test_billing_invoices.py -v`
Expected: PASS

---

### Task 11: 计量对账单前端查询与导出视图

**Files:**
- Create: `cds-frontend/src/services/billingApi.ts`
- Create: `cds-frontend/src/pages/Flow/BillingCenter.tsx`
- Modify: `cds-frontend/src/App.tsx`
- Modify: `cds-frontend/src/components/Layout/Sidebar.tsx`

- [ ] **Step 1: 实现 `cds-frontend/src/services/billingApi.ts`**

封装 `/deliveries/invoices`、`/deliveries/invoices/generate`、`/deliveries/invoices/${id}/export`。

- [ ] **Step 2: 实现 `BillingCenter.tsx` 并挂载路由 `/deliveries/invoices`**

提供按账期、合约筛选的对账单表格、费用明细抽屉与一键导出 CSV 按钮。

- [ ] **Step 3: 运行契约对账测试与前端构建**

Run: `.venv/bin/python -m pytest tests/test_frontend_api_contract.py -v && cd cds-frontend && npm run build`
Expected: PASS

---

### Task 12: 顶栏聚合通知中心（待办/公告/消息）与工作台个性化设置

**Files:**
- Create: `app/api/notifications.py`
- Modify: `app/main.py`
- Create: `cds-frontend/src/services/notificationApi.ts`
- Modify: `cds-frontend/src/components/Layout/Header.tsx`
- Modify: `cds-frontend/src/stores/uiStore.ts`
- Test: `tests/test_notifications_api.py`

- [ ] **Step 1: 编写聚合通知中心测试 `tests/test_notifications_api.py`**

```python
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_notification_center_aggregation_and_mark_read(client: AsyncClient, auth_headers: dict):
    # 1. 创建一条通知
    create_resp = await client.post(
        "/api/v1/monitoring/notifications",
        json={
            "category": "todo",
            "title": "数据产品上架待审批",
            "content": "请尽快审核「企业纳税信用产品」",
            "target_path": "/data-products",
        },
        headers=auth_headers,
    )
    assert create_resp.status_code == 201
    notif_id = create_resp.json()["id"]

    # 2. 获取聚合通知列表（包含待办事项、系统消息、业务通知）
    list_resp = await client.get("/api/v1/monitoring/notifications", headers=auth_headers)
    assert list_resp.status_code == 200
    data = list_resp.json()
    assert data["unread_count"] >= 1
    assert any(item["id"] == notif_id for item in data["items"])

    # 3. 标记已读
    read_resp = await client.post(
        f"/api/v1/monitoring/notifications/{notif_id}/read",
        headers=auth_headers,
    )
    assert read_resp.status_code == 200
    assert read_resp.json()["is_read"] is True
```

- [ ] **Step 2: 实现 `app/api/notifications.py` 并挂载在 `/api/v1/monitoring/notifications`**

自动聚合：
1. `UserNotification` 表中的持久化通知；
2. 实时待办项：当前用户待签署的 `Contract`（状态 `PENDING_APPROVAL`）以及待审批的 `OutputControl` 出域请求，统一返回 `{items, total, unread_count, todo_count}`。

- [ ] **Step 3: 在 `Header.tsx` 与 `uiStore.ts` 中集成通知抽屉与首页模块显隐个性化配置（不破坏 `DevHome.tsx` 原型门禁测试）**

- [ ] **Step 4: 运行全套门禁验证**

Run:
```bash
.venv/bin/python scripts/verify_schema_parity.py
.venv/bin/python -m pytest tests/test_frontend_api_contract.py tests/test_teams_api.py tests/test_scenarios_api.py tests/test_projects_and_dag.py tests/test_data_resource_auto_profile.py tests/test_data_product_version_diff.py tests/test_workload_model_scan_and_eval.py tests/test_billing_invoices.py tests/test_notifications_api.py -v
cd cds-frontend && npm run build && npx vitest run
```
Expected: ALL PASS

---

## Self-Review 核验清单

1. **门禁兼容性**：
   - `test_frontend_api_contract.py`：后端不卸载存量路由，新增前端 `services/*` 与后端路由 1:1 匹配；
   - `verify_schema_parity.py`：新模型遵循 `UUID` / `JSONB` / `func.now()` / `sa_text` / 显式 `Index`，且仅在 `app/main.py` 导入，与 `0035` 迁移完全一致；
   - `test_api_keys_ss12.py`：所有新路由均复用既有已映射前缀（`/api/v1/flow/*`, `/api/v1/users/*`, `/api/v1/deliveries/*`, `/api/v1/monitoring/*`），不破坏 SS-12 权限组快照；
   - `DevHome.tsx` 原型像素级测试：通知中心与个性化开关挂在顶栏 `Header.tsx` 与 `uiStore.ts`，存量 `devTaskHomeA1.test.tsx` 保持全绿。
2. **复用性检查**：
   - 敏感数据探查直接复用 `app/services/pii_ner.py`；
   - 模型扫描扩展 `app/services/code_scanner.py`；
   - 项目空间聚合现有的 `Notebook`、`Workload`、`Contract`。
3. **需求全覆盖**：
   - 已完整覆盖：越界菜单收敛、团队管理与配额、开发/交付场景管理、建模项目与多方协作、DAG 编排、R/PySpark 多语言支持、自动分类分级探查、数据产品版本比对与回退、模型安全扫描与指标对比、计量对账单生成导出、通知中心与个性化设置。


---

## 执行记录与计划差异（2026-11-03 执行轮）

12 个 Task 全部落地。差异一律按「代码实测优先于计划草案」记录：

### 后端

- **Task 9**：`GET /api/v1/workloads/metrics-compare`（沿 `parent_id` lineage 逐版本取最近一次
  `WorkloadTestRun`，指标只认 `report["metrics"]` 的数值子集——计划未定义指标存储位，这是代码里的家）；
  `POST /api/v1/workloads/{id}/artifact-scan`（opcode 级 pickle 审计 + safetensors 头 + zip 内 pkl 成员，
  白名单外 GLOBAL/STACK_GLOBAL 或解析失败一律 fail-closed；32 MiB 上限；扫描留审计、不改状态机）。
- **Task 10**：路径按计划挂 `/api/v1/deliveries/invoices*`（gateway 组），生成 = `POST …/invoices/generate`、
  导出 = `GET …/invoices/{invoice_id}/export`（与 Task 11 的前端封装逐字对齐）；测试文件名按计划
  `tests/test_billing_invoices.py`。计量口径：API 调用取 `api_endpoints.calls_used` 累计计数器（无按期切片表，
  如实记进 `unit_price_config`）；CPU 小时优先 `compute_runs.resource_usage["cpu_seconds"]`，回落起止挂钟差，
  两者皆无按 0 并在 line item 里点名条数；出域取账期内 `delivery_bundles.size_bytes`；存储按合约产品挂接的
  `data_resources.file_size_bytes` × 账期天数（未到期只计到当下）。单价为固化进账单的占位快照（非合同价）。
  同 (contract, period) 重复生成 409——账单行不可变。
- **Task 12**：计划的状态机词 `PENDING_APPROVAL` 与 `OutputControl` 请求表在代码里都不存在。
  真实口径：待签 = 我是当事方且我方 `*_signed_at` 为空、状态 draft/negotiating；待审出域 =
  `SandboxTask.status == output_review`，仅对 operator/admin 出现。合成待办 id 带 `contract:`/`task:`
  前缀、标已读必 404。`POST /monitoring/notifications` 的 user_id 由后端强制为当前用户（无代发能力）。

### 前端

- **Task 11**：`/deliveries/invoices` 路由 + 「我的结果集」菜单两处接线 + 面包屑 + 开发者白名单，
  对账单表格/费用明细抽屉/逐单 CSV 导出。
- **Task 12**：顶栏原型门禁（`Header.test`：右侧只有人像+下拉，铃铛/徽标一律不出现）与计划的
  「顶栏抽屉」冲突——通知中心入口收进**用户下拉菜单**（未读/待办计数进菜单文案），顶栏本体零新增，
  门禁保持全绿；个性化落地为**通知分类显隐**（`uiStore.notificationCategories`，每个字段都有消费方）。
  「首页模块显隐」未做：`DevHome.tsx` 是像素原型、无模块注册表，引入注册表必破 A-1 门禁——记为差异。

### 门禁读数（本机，py3.14 替身 + SQLite；权威口径见 AGENTS 注意事项）

- `verify_schema_parity.py`：SCHEMA PARITY OK（60 表逐列一致）。
- `alembic upgrade head` → 0035 正常；`alembic check`：No new upgrade operations detected。
- 定向电池（frontend_api_contract/teams/scenarios/projects_dag/auto_profile/version_diff/
  model_scan/billing_invoices/notifications/capability_matrix/pz04）：**97 passed**。
- SS-12 四件套 + `test_ci_workflow_policy`：**108 passed**。
- `import app.main`：IMPORT_OK（含 billing/notifications 挂载）。
- FE：`npm run lint` 0 error 0 warning；`npm run build`（tsc+vite）绿；全量 `vitest run`：**116 files / 963 tests 全绿**（含 routeCrumb/Sidebar/Header/DevHome 全套原型门禁）。
