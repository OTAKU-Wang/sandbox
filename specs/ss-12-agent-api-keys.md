# SS-12：平台 Agent API 密钥（机器凭据与功能权限域）

> 版本：v1.0 | 分系统编号：SS-12 | 日期：2026-10-13
> 上游：SS-01（密钥/身份）、SS-02（契约/策略）、SS-11（产品化，PZ-10 角色矩阵 / PZ-11 网关凭据）
> 下游：SDK（cds_sdk）、管理前端、审计（SS-06）
> 执行方式：mass-ulw 分波次（§10），每节点父任务审计（file:line + 亲跑测试）

## 一、背景与目标

### 1.1 背景
CDS 平台当前仅有两类调用身份：
1. **人类用户 JWT**（`app/core/deps.py:14` `get_current_user`，`HTTPBearer` 解码 → `users` 表校验）——交互式登录（SM2 证书/密码 + MFA 双轨，`AUTH_MFA_REQUIRED`），外部智能体无法持有；
2. **网关应用凭据**（`app/models/app_credential.py` `AppCredential`，`app_id`/`app_secret`，SM3 哈希存储，`X-App-Id`/`X-App-Secret` 头，PZ-11⑥）——**仅覆盖契约网关面**（`/api/v1/gateway` 数据产品调用），按契约绑定、带行/字节配额，不能调用平台管理面与会话面。

平台管理员需要一个**平台级机器凭据**：在系统设置中签发，交给外部 agent 智能体（自动化管线、运维 agent、第三方集成），使其能以受控权限自动化调用平台业务与功能 API。

### 1.2 目标
1. 管理员（`admin` 角色）可在管理端创建/管理 API 密钥，全生命周期（创建/列表/轮转/禁用/吊销/活动查询）。
2. 每把 key 携带**显式功能权限域（scope）**：可限定到功能组 × 读/写粒度，也可选择全量访问（`full` 预设）。
3. 外部 agent 使用 `cds_ak_` 前缀密钥 + 标准 `Authorization: Bearer` 调用平台 API；越权返回 W2 契约错误。
4. 机器身份**全程可归因**：每次调用与每次管理操作进入审计（SS-06 管线，含 Merkle 锚定），管理端可见单 key 活动。
5. **使用体验**（用户明确要求）：一次性展示 + 复制、预设权限模板降低配置成本、状态/最近使用一目了然、操作后果（吊销=agent 立即 401）前置告知、可复制的 SDK 接入片段。

### 1.3 非目标
- 不替代网关应用凭据（`app_credentials`）：两者边界见 §3，网关面的配额/IP 白名单机制不动。
- 不做 OAuth2 client_credentials 授权服务器（无外部 IdP 联邦需求时不引入）；协议面预留（Bearer 传输相同）。
- 不做 key 级数据行过滤（bound_user 数据范围隔离）——表结构留扩展位，v1 不实现（§5.1 注明）。
- 不改变人类用户的 RBAC 行为；JWT 路径字节级不变。

## 二、设计原则（项目铁律对齐）
1. **fail-closed**：未注册前缀对 key 主体默认拒绝（default-deny，同 OPA 锚纪律）；认证材料缺失/不可解析一律 401，不降级。
2. **禁伪造**：key 的"最近使用"、"活动记录"一律来自真实审计/真实请求，无模拟数据；测试不 pin 文案。
3. **诚实披露**：暴力冷却为进程内计数器（多实例部署为近似），文档与 UI 均如实标注，不宣称全局精确。
4. **以代码为准回写**：实施中若与现状冲突，改本文件（沿用前序方案纪律）。
5. additive-only：迁移只加表/列，无回填、无改既有表结构。

## 三、与既有凭据体系的边界

| 维度 | 用户 JWT（SS-01） | 网关应用凭据（PZ-11） | **平台 API 密钥（本 spec）** |
|---|---|---|---|
| 主体 | 人类用户 | 应用（绑定契约+消费用户） | 机器主体（管理员签发，记 issued_by 归因） |
| 传输 | `Bearer <jwt>` | `X-App-Id`/`X-App-Secret` | `Bearer cds_ak_...` |
| 覆盖 | 全 API（RBAC 角色门） | 仅 `/api/v1/gateway` 面 | 全 API（scope 域门），human-only 路由除外 |
| 存储 | 无状态 JWT | `app_secret_hash`（SM3） | `key_hash`（SM3） |
| 限流 | 无 per-身份 | `rate_limit`+行/字节配额+IP 白名单 | per-key `rate_limit`（req/min） |
| 生命周期 | 会话过期 | active/suspended/revoked/expired | active/disabled/revoked + 可选过期 |

共存关系：同一请求只走一条认证路径（按 Authorization 头形态判定，§4.3）；`/api/v1/gateway` 路由同时保留 X-App-* 网关认证，scope 门对其正交（key 调 gateway 路由时按 `gateway` 组判域，网关内部凭据逻辑不变）。

## 四、设计

### 4.1 身份模型

新表 `api_keys`（迁移 0017，§5）：

| 列 | 类型 | 说明 |
|---|---|---|
| id | UUID pk | |
| name | String(64) | 管理员命名（必填，1-64 字符） |
| description | Text null | 用途说明（UX：创建时引导填写） |
| key_hash | String(64) **unique** | SM3(裸密钥) hex（复用 `sm3_hash`，`app/utils/crypto.py:34`）；**永不存裸密钥** |
| key_prefix | String(16) | 展示前缀：`cds_ak_` + 随机段前 4 字符 |
| scopes | JSON | 显式 scope 列表（`["sessions:write", "audit:read", ...]`） |
| status | String(32) | `active` / `disabled` / `revoked` |
| rate_limit | Integer | req/min，默认 120，创建时可设（1-1000） |
| issued_by | UUID fk users.id | 签发管理员（归因） |
| created_at / updated_at | timestamptz | |
| expires_at | timestamptz null | 可选过期（null = 永不过期） |
| last_used_at | timestamptz null | 节流写（§4.3） |
| revoked_at / revoke_reason | timestamptz / String(255) | 吊销留档（审计纪律：不可物理删除，同 app_credentials 先例） |

**密钥格式**：`cds_ak_` + 32 字节 `secrets.token_bytes` 的 base64url（43 字符），全长 50 字符。签发时裸密钥**仅响应一次**（创建/轮转），之后任何接口只回 `key_prefix`。
**扩展位（v1 不实现）**：`bound_user_id` 列 v1 不建（避免无意义的 nullable 外键）；未来需要数据范围绑定时以 additive 迁移加入。

### 4.2 权限模型（scope 域）

**语法**：scope 字符串 `<group>:<verb>`，`verb ∈ {read, write, *}`（`*` = 该组读+写）。无全局 `*`；全量访问 = 预设展开后的全组 `:*` 列表（显式落库，预设定义变更不影响存量 key）。

**功能组 → 路由前缀映射**（以 `app/main.py:232-270` 挂载为准，实施时以代码复核）：

| group | 覆盖前缀 | 说明 |
|---|---|---|
| `sessions` | `/api/v1/sandbox-sessions`、`/api/v1/dev-sandbox`、会话流 | 会话创建/执行/终止/检视 |
| `contracts` | `/api/v1/contracts` | 契约生命周期 |
| `data` | `/api/v1/data-products`、`/api/v1/data-resources`、`/api/v1/catalog` | 数据产品/资源/目录 |
| `pipeline` | `/api/v1/data-pipeline`、`/api/v1/training`、`/api/v1/mpc`、`/api/v1/rag`、`/api/v1/inference`、`/api/v1/sandbox-db`、`/api/v1/sandbox-tasks` | 业务管线与计算 |
| `audit` | `/api/v1/audit`、`/api/v1/monitoring`、`/api/v1/compliance`、`/metrics`、`/health` | 审计与合规查询 |
| `output` | `/api/v1/output-control`、`/api/v1/field-exposure`、`/api/v1/network-policies` | 输出治理 |
| `infra` | `/api/v1/sandbox-nodes`、`/api/v1/shared-volumes`、`/api/v1/cube-templates`、`/api/v1/kms`、`/api/v1/connectors`、`/api/v1/ops` | 基础设施管理 |
| `certs` | `/api/v1/certificates` | 证书管理 |
| `federation` | `/api/v1/federation` | 联邦互联 |
| `gateway` | `/api/v1/gateway` | 数据网关（与 X-App-* 正交，§3） |
| `agent` | `/api/v1/agent` | agent 面 |
| `meta` | `/api/v1/meta` | 元信息 |
| **human-only**（不存在 scope，永拒 key 主体） | `/api/v1/auth`、`/api/v1/users`、`/api/v1/api-keys`、`/internal/*`、`/static/*` | 登录、用户管理、密钥管理、内部面 |

**判定规则**（`require_group` 依赖，运行时解析）：
1. 请求前缀按映射表（**最长前缀优先**）归组 `g`；未命中任何组 → key 主体 403 `API_KEY_SCOPE_DENIED`（default-deny），用户主体放行（与现状一致）。
2. `verb`：`GET` → `read`，其余方法 → `write`。
3. key 通过 ⇔ 其 `scopes` 含 `g:verb` 或 `g:*`。
4. 人类用户主体（JWT）：scope 门 no-op（继续走既有 `require_roles` 角色门，字节级不变）。

**预设**（创建时展开为显式列表落库）：

| 预设 | 展开 |
|---|---|
| `readonly` | 12 个可触达组全部 `:read` |
| `operator` | `readonly` + `sessions/contracts/data/pipeline/output/gateway/agent` 的 `:write` |
| `full` | 12 个可触达组全部 `:*`（**全量访问**；human-only 组不在内） |
| `custom` | 勾选器自选（组 × 读/写，UI 见 §4.7） |

### 4.3 认证流

统一主体解析（新模块 `app/core/scopes.py` + `app/core/deps.py` 扩展）：

1. `HTTPBearer(auto_error=False)` 取凭证（沿用 `deps.py:41` 既有模式）。缺失 → 401（与现状一致）。
2. 凭证以 `cds_ak_` 开头 → **key 路径**：
   - `key_hash = sm3_hash(凭证)` → 按 unique 索引查 `api_keys`；未命中 → 401 `API_KEY_INVALID`（计 1 次失败）。
   - 命中但 `status != active` → 401 `API_KEY_INACTIVE`（detail 注明 disabled/revoked）。
   - `active` 但 `expires_at` 已过 → 401 `API_KEY_INVALID`（detail 注明 expired；惰性判定，不跑批）。
   - 暴力冷却：同 `key_hash` 连续 5 次认证失败 → 15 min 冷却期（期间 401 `API_KEY_INACTIVE`，detail=cooldown）。**进程内计数器**（部署多实例时为近似，UI/文档如实披露）。
   - per-key 限流：复用 `quota_manager` 既有模式（`app/services/quota_manager.py`），超限 → 429 + `Retry-After`。
   - 成功 → `AuthSubject(kind="api_key", api_key=row)`；`last_used_at` 60s 节流写。
3. 否则 → **JWT 路径**（现状 `get_current_user` 逻辑原样）→ `AuthSubject(kind="user", user=user)`。

**依赖改造**（向后兼容）：
- 新增 `get_auth_subject`（返回 `AuthSubject`）；`get_current_user` 保留并改为经 `get_auth_subject` 解析（key 主体调用户路由 → human-only 门 401，§4.2 规则 1 覆盖）。
- `require_group(g)` 依赖工厂挂到 `app.include_router(..., dependencies=[Depends(require_group(g))])`（`main.py` 挂载处逐路由组登记）——**人类主体 no-op，key 主体按 §4.2 判定**。
- human-only 组（auth/users/api-keys/internal）挂 `require_human` 依赖（key 主体 403 `API_KEY_SCOPE_DENIED`；与"不存在 scope"语义一致，不新增码）。
- 每次 key 主体成功调用 → `audit_service.log`（§4.5）；认证失败（401）同样记审计（action=`auth.api_key.denied`，detail 带原因），支撑管理端"活动"与取证。

### 4.4 生命周期管理 API

新路由 `app/api/api_keys.py`，挂载 `prefix="/api/v1/api-keys"`（`main.py` 追加一行）。全部端点：`require_roles(UserRole.ADMIN)`（`app/core/deps.py:30` 既有工厂，`UserRole.ADMIN` 见 `app/models/user.py`）+ human-only 门。

| 端点 | 语义 | 响应 |
|---|---|---|
| `POST /api/v1/api-keys` | 创建：`{name, description?, preset?, scopes?, expires_in_days?, rate_limit?}`（preset=custom 时 scopes 必填且逐条合法；expires_in_days ∈ {30,90,180,365,0=永不过期}，默认 90） | 201 `{key(裸,仅一次), id, name, key_prefix, scopes, status, expires_at, created_at}` |
| `GET /api/v1/api-keys` | 列表（含全部状态，按 created_at 倒序） | 200 数组，每行：`id,name,description,key_prefix,scopes,status,rate_limit,issued_by_username,created_at,expires_at,last_used_at,revoked_at`（**无裸密钥/哈希**） |
| `GET /api/v1/api-keys/{id}/activity` | 最近 50 次调用（取自审计，actor=该 key） | 200 数组：`time,path,method,status_code,ip,denied_reason?` |
| `POST /api/v1/api-keys/{id}/rotate` | 轮转：新行继承 name/scopes/rate_limit/未过期策略，**旧行立即 revoked**（revoke_reason="rotated"） | 201 `{key(裸,仅一次), id(新)}`；旧 id 不可再 rotate（409 语义：`SESSION_STATE_CONFLICT` 复用？——不：返回 400 `BAD_REQUEST` detail=already revoked，W2 既有码够用，不新增码） |
| `PATCH /api/v1/api-keys/{id}` | 状态切换：`{status: "active"\|"disabled"}`（revoked 终态不可切） | 200 更新后行 |
| `DELETE /api/v1/api-keys/{id}` | 吊销（幂等：已 revoked 再删 → 200，同 KMS 卷密钥端点纪律） | 200 `{id, status:"revoked", revoked_at}` |

命名冲突说明：与 `app_credentials`（网关应用凭据，`/api/v1/gateway` 面，契约绑定）**无路由重叠、无代码共享**；本路由前缀 `/api/v1/api-keys` 为新增，human-only。

### 4.5 审计与活动

- `audit_service.log`（`app/services/audit_service.py:61`）新增**可选**参数 `actor_type: str = "user"`、`actor_id: str | None = None`（additive，既有调用字节级不变）；key 主体调用传 `actor_type="api_key", actor_id=str(key.id), user_id=None`。
- SM2 签名事件串（`{action}|{resource_type}|{resource_id}|{user_id}`）不变——actor 信息进 `detail`（`{"actor_type","actor_id"}`），签名覆盖范围由既有管线决定，不扩。
- 管理操作高优审计：`api_key.created / api_key.rotated / api_key.disabled / api_key.enabled / api_key.revoked`（resource_type=`api_key`，detail 带 name/scopes/原因）。
- 活动查询端点（§4.4）从 PG 审计表按 `detail.actor_id` 过滤（建 `(resource_type, resource_id)` 查询路径：resource_id 存 key id，走既有索引面；实施时若审计表无该索引则 additive 补索引——**以实施时 schema 复核为准并回写本节**）。
- ClickHouse 镜像与 Merkle 锚定管线零改动（审计字段 additive，schema 漂移风险由 `verify_schema_parity` 与既有 40 列 ClickHouse 约定兜底；`actor_type/actor_id` 入 detail JSON，不改 ClickHouse 列）。

### 4.6 SDK（cds_sdk）

`CDSClient.__init__`（`sdk/cds_sdk/client.py:137`）新增关键字参数 `api_key: str | None = None`：
- 认证路径三选一互斥：`token` / `app_id+app_secret` / `api_key`（现有两路互斥校验扩展为三路；`api_key` 且非 `cds_ak_` 前缀 → 构造期 `CDSError(400, ...)`，快速失败不发起请求）。
- 生效 → `Authorization: Bearer <api_key>`（与 JWT 同头，服务端按 §4.3 判定）。
- 401 处理：现有重试集仅 429/503（`client.py` `_request`），401 天然不重试——`API_KEY_INVALID/INACTIVE` 经 `_parse_error` 得 `CDSApiError.code`，客户端应停止并提示管理员检查密钥状态（docstring 写明）。
- 文档：`sdk/README`（如有）+ 模块 docstring 更新示例。

### 4.7 前端与 UX（管理端）

新增（无 i18n 层——本项目前端为内联中文，`cds-frontend/src` 无 i18n 目录，遵循现状）：
- `cds-frontend/src/services/apiKeysApi.ts`（单 axios 实例约定，同 `usersApi.ts` 先例）+ 类型（`ApiKey`、`ApiKeyActivity`）。
- `cds-frontend/src/pages/ApiKeys.tsx` 管理页；`components/Layout/Sidebar.tsx` 增加「API 密钥」入口（**仅 admin 角色可见**——角色取自既有 auth store；`Sidebar.test.tsx` 同步补断言）。

**页面信息架构**：
1. 列表：名称 / 前缀（`cds_ak_ab12…` 等宽字体）/ 权限（预设名或 scope 徽章，超 4 个折叠 +N）/ 状态徽章（生效=绿、已禁用=灰、已吊销=红、已过期=橙）/ 最近使用（相对时间，>30 天显示日期；从未使用显示「从未使用」+ 提示）/ 到期 / 操作列（禁用/启用、轮转、吊销、活动）。
2. **创建对话框**：名称（必填）→ 用途说明（选填，placeholder 引导"哪个 agent、做什么"）→ 权限预设单选（只读 / 业务操作 / 全量 / 自定义，各附一句话说明；全量项带橙色警示「可调用除用户/密钥管理外的全部平台功能」）→ 自定义时展开组 × 读/写勾选矩阵（组名用中文标签 + 英文 group 名小字）→ 有效期（30/90/180/365/永不过期，默认 90）→ 限流（默认 120 req/min，可改）。
3. **一次性展示面板**（创建/轮转成功态）：裸密钥等宽字体 + 一键复制（`navigator.clipboard`，复制成功态「已复制」）；红色警示条「该密钥仅显示一次，请立即保存；平台无法找回」；下方**可复制 SDK 接入片段**（Python `CDSClient(base_url=..., api_key="...")`，密钥以 `<已复制的密钥>` 占位避免 UI 二次暴露全文？——裁定：片段内用前缀+`…`占位，旁边「粘贴密钥」输入框由用户自行粘贴，UI 不自动回填全文，降低肩窥风险；复制按钮仍可一键复制全文）。
4. **活动抽屉**（点「活动」）：最近 50 次调用表（时间/路径/方法/状态码/IP；401/403 行标红 + 拒绝原因），空态「该密钥尚未被使用」。
5. **确认对话框**：吊销=红色确认，正文写明后果「吊销后使用该密钥的 agent 将立即收到 401，且不可恢复」；禁用=说明「可随时恢复」；轮转=说明「旧密钥立即失效，请确保 agent 已切换到新密钥」。
6. 空状态：无密钥时给出「什么是 API 密钥」一段说明 + 创建按钮。
7. 冷却披露：key 处于冷却期时状态徽章旁显示「认证失败冷却中（约 N 分钟后恢复）」——活动/列表接口在 detail 中下发冷却剩余（进程内计数器，UI 如实标注"近似"）。

### 4.8 错误码（W2 契约新增 3 个，入 `docs/error-codes.md`）

| code | HTTP | 语义 | 用户提示（面向 agent 开发者） |
|---|---|---|---|
| `API_KEY_INVALID` | 401 | 密钥格式不符 / 不存在 / 已过期 | 检查密钥是否完整、是否已过期；联系平台管理员 |
| `API_KEY_INACTIVE` | 401 | 已禁用 / 已吊销 / 失败冷却中 | 密钥被管理员禁用或吊销，或连续失败进入冷却；联系管理员或稍后重试 |
| `API_KEY_SCOPE_DENIED` | 403 | 该密钥无此功能域的权限（含 human-only 路由） | 该 API 密钥权限不足；请管理员调整 scope 或改用其他密钥 |

格式沿用 W2 `{code, message, detail, request_id}`（`docs/error-codes.md` 表头约定）；SDK `CDSApiError.code` 直接可判。

## 五、数据库迁移

**`alembic/versions/0017_api_keys.py`**（实施时链头为 `0016_sandbox_tee_policy.py`（SS-13 先行落库），已核；additive-only 单表）：
- 建 `api_keys` 表（§4.1 全列）+ `uq_api_keys_key_hash`（unique）+ `ix_api_keys_status` + `ix_api_keys_issued_by`。
- 无回填、无既有表改动；`scripts/verify_schema_parity.py` 必须全绿（CI 门禁）。
- dev/test 的 `create_all` 路径自动获得新表（conftest 约定），`alembic upgrade head` + `alembic check` 双验。

## 六、安全设计
1. **存储**：仅 SM3 哈希 + 展示前缀；裸密钥只在创建/轮转响应出现一次；任何列表/详情/活动接口不回密钥材料；日志不打印 Bearer 头（沿用中间件脱敏，实施时核实 `request_id` 中间件对 Authorization 的既有处理并回写）。
2. **比较**：查库走 `key_hash` unique 索引（哈希等值查询，无时序侧信道问题；不做裸值常量时间比较——裸值不落库，无从比较，此为设计事实非豁免）。
3. **吊销即时性**：每次请求查库判定 status（单行索引查询，key 表小），无缓存层 → 吊销即时生效，延迟 0。
4. **防爆破**：哈希查找天然防枚举（无 key 序/探测反馈差异：不存在与错密码同为 401 `API_KEY_INVALID` 且同耗时档）；5 连败 15min 冷却（进程内，§2 披露）。
5. **签发者控制**：管理端点 `admin` 角色门 + human-only 门（key 不能管理 key，杜绝机器凭据自我繁殖/提权）；admin 会话本身受 PZ-10 MFA 双轨约束（登录面不变）。
6. **最小权限默认**：默认预设不预选（创建对话框必须显式选择）；默认有效期 90 天；`full` 预设带 UI 警示。
7. **审计不可删**：吊销=状态翻转+留档（同 `app_credentials` 先例），审计行只增（retention 0 纪律）。

## 七、测试策略（实施轮交付，场景表）

| 层 | 场景（节选，全表入实施节点 prompt） | 判定 |
|---|---|---|
| 单元 | 密钥生成格式/前缀/长度；SM3 哈希查库命中/未命中；scope 判定矩阵（组×verb×`*`×预设展开×未注册前缀 default-deny×human-only）；状态机（disabled 可恢复/revoked 终态）；last_used 60s 节流；冷却 5 连败触发与 15min 解除 | 各自断言 |
| API | admin 创建 201 裸密钥仅一次（二次 GET 列表无前缀以上材料）；operator 调管理端点 403；**key 主体调管理端点 401/403（human-only）**；rotate 后旧 key 401 `API_KEY_INACTIVE`；DELETE 幂等；W2 错误体四字段齐全（3 个新码各 1 例） | 状态码+body |
| 强制 | 同一 key：组内 GET 200 / POST 403（仅 :read 时）；`full` key 全域 200（抽 3 组）；未注册前缀 403；限流 429+Retry-After；JWT 路径回归（既有 `test_e2e_flows` 42 全绿即证据） | 状态码 |
| e2e | 管理员签发（UI 或 API）→ SDK `api_key=` 域内 200 → 域外 403 `API_KEY_SCOPE_DENIED` → 吊销 → 401 `API_KEY_INACTIVE` | 全链路 |
| 审计 | key 调用产生审计行（actor_type=api_key，SM2 签名非空）；管理操作 5 类 action 各 1 行；活动端点返回与审计一致 | 断言行 |
| 门禁 | `verify_schema_parity` 全绿；`compileall` 全绿；全量回归不劣于基线（`ci/known-failures.md` 现行行，失败数不得增长） | CI |
| FE | Sidebar admin 门（test 补断言）；ApiKeys 页渲染（列表/空态/创建对话框预设展开）；一次性展示面板复制态 | vitest |

## 八、UX 验收点（用户要求「考虑用户使用体验」——实施轮逐项核验）
1. 从签发到 agent 可用 ≤ 3 次点击（创建→复制→保存），全程无跳转。
2. 权限选择的认知成本：预设一句话说明 + 自定义矩阵组名中文化；误选 `full` 有橙色警示。
3. 密钥不可找回的事实**在复制前**告知（警示条先于成功态文案）。
4. 吊销等破坏性操作：后果前置（agent 立即 401）、红色确认、不可撤销明示。
5. 状态语义无歧义：生效/禁用/吊销/过期/冷却五态徽章 + 最近使用新鲜度。
6. agent 开发者排障路径：401/403 错误码 → `docs/error-codes.md` 用户提示列 → 联系管理员（闭环）。
7. 活动可见性：单 key 最近 50 次调用（含拒绝原因），支撑"谁在调、调了什么、为什么被拒"。
8. SDK 片段可复制（§4.7.3 占位+粘贴模式）。

## 九、执行边界
1. 不触碰 `Tongsuo/`、`cds-frontend/node_modules/`；不 git commit（用户未要求）。
2. 迁移 additive-only；生产禁 `create_all`（`app/main.py` 既有分支）。
3. 不改动 `app_credentials`/网关配额逻辑（§3 边界）；不引入新运行时依赖（仅既有栈）。
4. `docs/error-codes.md` 同步 3 新码（W2 表 + 场景表）；SDK 文档同步。
5. 回写：实施完成后本文件 §11 执行记录 + `ci/known-failures.md` 基线行 + SS-11 执行记录（若涉其文件）。

## 十、mass-ulw 执行波次计划

**Phase 1（单 workflow run，3 并行 lane；spec 即契约，lane 间零依赖、写域不相交）**

| lane | category | 写域 | 交付 + VERIFY |
|---|---|---|---|
| L1 backend | deep-low | `alembic/versions/0017*`、`app/models/api_key.py`、`app/core/scopes.py`、`app/core/deps.py`、`app/api/api_keys.py`、`app/main.py`（挂载行+依赖）、`app/services/audit_service.py`（additive 参数）、`tests/test_ss12_api_keys_*` | 迁移双验（upgrade head + check）+ 单元/API/强制场景表全绿 + `verify_schema_parity` 全绿。STOP WHEN：本文件 §4.1-4.5、§7 全行通过 |
| L2 sdk-docs | unspecified-low | `sdk/cds_sdk/client.py`、`docs/error-codes.md` | SDK 三路互斥 + 前缀快失 + 401 不重试（新增 `tests/test_sdk_api_key.py` 绿）；error-codes 3 码入表（W2 四字段列对齐）。STOP WHEN：§4.6 + §4.8 落码 |
| L3 frontend | visual-engineering | `cds-frontend/src/services/apiKeysApi.ts`、`src/types/`（或既有类型位置）、`src/pages/ApiKeys.tsx`、`src/components/Layout/Sidebar.tsx`（+test） | `tsc -b` + `vite build` + `npm run lint` + `npm test` 全绿；§4.7 六项 + §8 逐项自验截图/断言。STOP WHEN：§8 全 8 项可指认 |

**Phase 2（新 run，依赖 Phase 1 全部 settle）**
- L4 e2e（deep-low）：`tests/test_e2e_api_keys.py` 全链路场景（§7 e2e 行）——签发→域内 200→域外 403→吊销 401→活动查询。
- 父任务收口（非 lane）：L1/L3 触及文件相关测试亲跑 → 全量回归（`pytest tests/ -q --ignore=tests/test_e2e_full_lifecycle.py`，基线不劣化）→ `compileall` → 上游无关（纯 CDS 仓库）→ §11 执行记录回写 + known-failures 基线行。

**审计纪律**（同 SS-10/SS-11）：节点输出是 claim，父任务亲验 file:line + 亲跑测试；失败 retry ≤ 2 轮，超限上报。

## 十一、执行记录

（空 —— 待 mass-ulw Phase 1 启动）
