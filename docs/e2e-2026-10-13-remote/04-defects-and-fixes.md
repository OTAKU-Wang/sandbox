# 缺陷与修复记录（SS-14 e2e 轮）

> 每个缺陷：发现来源 → 根因 → 修复 → 复验证据。状态：`fixed` / `open` / `accepted-risk`。
> 铁律：根因修复优先，禁止以测试跳过或基线放宽掩盖。

## D-e2e-01 — `ALLOWED_SELF_REGISTER_ROLES` 默认含 admin（提权洞）

- **状态**: fixed
- **发现**: 部署轨代码审查——`app/schemas/user.py` PZ-10 收紧后 admin 为唯一可自选角色，但 bootstrap 语义（仅首个账号）从未在端点实现：任何后续账号均可自注册 admin。
- **修复**（first-account bootstrap + 测试逃生阀）:
  1. `app/api/auth.py::register`: `role=admin` 时查库，已有账号且逃生阀关 → 403。空库首账号可 claim admin（部署 bootstrap 路径保持）。
  2. `app/core/config.py`: 新开关 `ALLOW_ADMIN_SELF_REGISTER: bool = False`（CDS_ALLOW_ADMIN_SELF_REGISTER）；`validate_security_config` 生产模式 refuse（与 SECCOMP/HSM 同族 raise）。
  3. `tests/conftest.py`: 套件 env 钉 `CDS_ALLOW_ADMIN_SELF_REGISTER=true`（fixture helper 经 /register 建 admin 的既有模式需要；先例：CDS_LEGACY_L2_ENABLED 行）。
  4. 测试：新增 3 个 pin 新行为（首账号 201 / 后续 403 / 非 admin 角色不受开关影响仍 422）；12 个既有 prod 模拟测试补 gate 满足行（`_prod_baseline` 单点 + 1 内联 + admin_auth 1 + ss13 1 setenv）。
- **复验**: test_auth_extended + test_admin_auth + test_e2e_flows + test_security_config_matrix + test_ss13_default_and_legacy = **85/85 绿**（25.15s）。全量回归#3（进行中）。

## D-e2e-02 — 既有 schema parity 漂移（FK/index，非 SS-12 引入）

- **状态**: accepted-risk（本波次不修，低优先）
- **发现**: SS-12 verify 节点 `alembic check` 报 `trained_models` / `rag_generative_models` 相关 FK/index 漂移。
- **定性**: 漂移在 committed 态即存在（模型文件工作区零改动、迁移 0009/0010 为已提交版本）；CI 门 `verify_schema_parity.py`（列级比对）绿。属 PZ-01/SS-07 时代的遗漏，非本轮引入。
- **影响**: 不阻塞部署（`alembic upgrade head` 成功）；`alembic check`（类型级）非 CI 门。
- **处置**: 记录在案，后续波次以 additive 0018 迁移补齐（2 FK + 1 index）。

## D-e2e-03 — 路由器级 scope 门 401 拒绝机器凭据面（Bug A）

- **状态**: fixed
- **发现**: SS-12 全量回归#1（15 failed / 2853 passed）；6 文件独立复跑确认非测试污染。
- **症状**: `test_connectors`(7)、`test_kms_volume_keys`(3)、`test_cube_audit_schema`(2)、`test_observability::test_metrics_token_required`(1) 全部 401——机器凭据（KMS 服务令牌、connector 密钥、cube 审计服务令牌、metrics 令牌）在路由器级 `require_group` 门即被拒绝，端点自身的令牌校验从未执行。
- **根因**: `app/core/scopes.py::_optional_subject` 对**所有**非匿名 Bearer 凭据调用 `resolve_auth_subject`；该函数只对统一 subject 模型（`cds_ak_` key / 用户 JWT）有语义，其他凭据 decode 失败即抛 `HTTPException(401)`。SS-12 spec 判定规则（§4.2 行 128）为"人类主体 no-op、key 主体按组判定"——机器凭据面（SS-01 KMS 服务令牌、SS-08 connector 密钥、SS-10 cube 审计令牌、observability metrics 令牌）本就在统一 subject 模型之外，应由路由自身 fail-closed 令牌依赖裁决。L1 实现把"非 subject 凭据"误判为"非法主体"。
- **修复**: `_optional_subject` 捕获 `HTTPException` → 返回 `None`（放行，路由自身令牌依赖裁决）；`CDSError`（key 路径拒绝，含审计归因与冷却计数）**仍传播**，不吞。
- **复验**:
  - 原 6 失败文件 → **29/29 绿**（6.92s）
  - SS-12 四套件（unit/api/audit/sdk）→ **84/84 绿**（default-deny 场景经中间件仍正确 403）
  - 全量回归#2（进行中，预期 2868/0/3）

## D-e2e-04 — 应用级 default-deny 依赖崩溃所有 WebSocket 路由（Bug B）

- **状态**: fixed
- **发现**: 同批（`test_exec_stream` 2 失败）；探针实测 `TypeError: HTTPBearer.__call__() missing 1 required positional argument: 'request'`。
- **症状**: `WS /api/v1/sandbox-sessions/{id}/exec/stream` 握手即崩——FastAPI 应用级 `dependencies=[Depends(default_deny_unmapped)]` 携带 `HTTPBearer`（签名含 `request: Request`），WebSocket 路由的依赖求解无法注入 `Request` → TypeError → **所有 WS 路由**不可用。
- **根因**: SS-12 rule 1（key 主体命中无功能组覆盖路径 → default-deny）被实现为应用级 FastAPI 依赖；该机制与 WebSocket 路由结构不兼容（Request 依赖在 WS 握手期不可注入）。
- **修复**:
  1. `app/core/scopes.py`: `default_deny_unmapped` 依赖 → 纯谓词 `key_unmapped_deny(path, auth_header) -> bool`。
  2. `app/main.py`: 新增原生 ASGI 中间件 `Ss12KeyDefaultDenyMiddleware`（http + websocket 双覆盖；HTTP 拒绝走 W2 契约 403 `{code: API_KEY_SCOPE_DENIED, message, detail, request_id}`，`request_id` 取 `scope.state` 由最外层 `RequestIDMiddleware` 签发保证一致；WS 拒绝发 `websocket.close(1008)`）。
  3. 移除 `FastAPI(dependencies=[...])` 应用级依赖。
  4. 注册序：Ss12 先加（内层）→ Metrics → RequestID 最外（W1 顺序不破坏，RequestID 仍可 stamp 本中间件的 403 响应）。
- **复验**:
  - `test_exec_stream` 2/2 绿（WS 握手 + ticket 鉴权 + DLP 帧）
  - SS-12 四套件 84/84（rule 1 default-deny 语义保持，改由中间件执行）
  - 全量回归#2（进行中）

## D-e2e-05 — 节点选择器概率性测试内禀 flake

- **状态**: fixed
- **发现**: 全量回归#2（1 failed / 2867 passed）：`test_select_prefers_lower_error_rate` 断言 `reliable > 40/100`，实测 36/100；单发 23/23 绿。
- **根因**: 加权随机选择（`score + random.uniform(0, 0.1)` jitter + 累计抽样）的统计偏好断言落在抖动方差带内——无 seed 的概率性测试，内禀 flake（同文件另有 3 个同族测试：lower_load / more_capacity / custom_weights，同风险）。
- **修复**: `tests/test_node_selector.py` 新增 `_seeded_rng(seed)` 上下文管理器（`random.getstate/seed/setstate`，测试后恢复全局状态）；4 个概率性测试的 100 次选择循环全部包裹固定种子 20261013——统计偏好断言变为可复现，行为覆盖不变（仍走真实加权随机路径）。
- **复验**: test_node_selector.py 23/23 绿（种子序列确定通过）；全量回归#3 待结算。

## D-e2e-06 — `ToolRegistry.list` 方法遮蔽内建 `list`：Python 3.12 导入即崩（生产镜像死）

- **状态**: fixed
- **发现**: 远程部署 deploy#8——`cds-api` 容器 crash-loop，`docker logs` 栈：`app/services/agent_tools.py:113 def validate_run(self, tool_ids: list[str])` → `TypeError: 'function' object is not subscriptable`。
- **根因**: `ToolRegistry` 类体内第 110 行 `def list(self) -> ...` 把 `list` 绑进类命名空间；其后第 113 行方法注解 `list[str]` 在**类体求值期**解析 `list` 命中该函数而非内建 → 下标崩。本地 .venv 是 Python **3.14**（PEP 649 注解延迟求值为默认）→ 本地导入/全量回归均不炸；生产镜像 Python **3.12**（注解 def 时求值）→ 导入即崩。2871 全绿基线对此类缺陷结构性失明（解释器版本差）。
- **修复**: `app/services/agent_tools.py` `list` → `list_tools`（调用方 `tests/test_agent_tools.py` 同步）；全库扫描同类遮蔽（`def filter/compile/list/exec` 等，app/core/logging.py、policy_compiler.py、cube_node_client.py）——均无后续类体内建下标用法/字符串注解，安全。
- **复验**: `tests/test_agent_tools.py + test_agent_service.py` 18/18 绿（3.14）；**决定性验证在 3.12 镜像内**：`docker run --rm --entrypoint python cds-api:latest -c "import app.main"` → `PY312_IMPORT_OK`（修复前同一命令复现原 TypeError）。
- **教训**: 本仓库本地 3.14 / 镜像 3.12 的版本差是注解求值语义盲区——后续任何 `docker run --entrypoint python … -c "import app.main"` 应成为镜像构建后的固定冒烟步骤（已加入部署流程）。

## D-e2e-07 — 部署脚本 `remote_deploy.sh` 连环缺陷（部署工具自身，非应用代码）

- **状态**: fixed（随部署迭代逐一暴露，全部在脚本内修复）
- **发现**: deploy#1-#9 连环失败，每个败点独立定位：
  1. `run()` 丢弃 `$image`（`shift 2` 后未引用）→ `docker run` 无镜像参数（deploy#1）。
  2. **docker run 参数序**：镜像名之后的参数全部被当作容器命令——`-e` env 全丢（postgres 无密码 crash-loop 表象为"容器 crash"，实际是参数被吞；对照实验 p4：flag 前置即生效，deploy#2/#3）。
  3. 需自定义命令的容器（redis/minio/opa）与 flag 混排 → `redis-server` 被当镜像名（deploy#3）→ 引入标准 `--` 分隔（run() 内拆分 args/cmd）。
  4. **vault 双 listener**：`VAULT_DEV_LISTEN_ADDRESS` 与 `VAULT_LOCAL_CONFIG.listener` 同绑 8200 → vault 进程自撞（"Error parsing listener configuration" + bind in use；表象极似端口竞态，独立探针 100% 成功误导多轮——bash -x trace 定讫，deploy#4-#7）。
  5. **file 后端破坏重部署幂等**：dev mode + 持久卷 → 第二次部署 "Vault is already initialized"（deploy#9）→ 回退纯 dev 内存模式（无卷）。
  6. **续行链中插注释**：`-e X=\` 后接 `# comment` 行 → 注释终结 bash 续行，后续 `-e` 行被当命令执行（deploy#8 exit 127）。
  7. OPA 镜像无 wget → 容器内健康检查误报 FATAL（deploy#5）→ 改宿主侧 `curl :8181/health` 探测。
- **复验**: deploy#10 中间件六项全 healthy + vault 稳定（脚本面验证通过）；api 段暴露新缺陷（D-e2e-08，另案）。
- **教训**: `bash -n` 只查语法不查语义（参数序/续行注释/双 listener 全查不出）；部署脚本必须 `bash -x` 全链路跑一遍再信；镜像构建后跑目标解释器版本（3.12）的导入冒烟。

## D-e2e-08 — 跨方言 DDL 严格性缺陷族：SQLite 宽容过全绿，PostgreSQL 拒绝（三处 Boolean 整数默认值 + 迁移链缺索引 + ORM 链外 FK）

- **状态**: fixed
- **发现**: 远程 deploy#10——API 启动 create_all 崩：`column "scheduling_disabled" is of type boolean but default expression is of type integer`；随后在远程真实 PG 上跑 alembic 全链验证又暴露 0003 同族失败；`alembic check`（基线验证确认：非本轮改动引入，分支既有红）另报两处 ORM FK 链外 + 一处索引缺失。
- **根因**: 全量回归跑在 SQLite（对 bool 列接受整数默认值、不强制索引/FK），生产路径是 PostgreSQL（严格类型）——解释器/方言版本差与 D-e2e-06 同构（本地 3.14 vs 镜像 3.12 的注解求值盲区 + 本地 sqlite vs 远程 PG 的 DDL 严格性盲区），2871 全绿基线对此类缺陷双重结构性失明。
- **修复**（双点对齐 ORM↔迁移链，全部保 parity）：
  1. `app/models/sandbox_node.py` `scheduling_disabled` server_default `text("0")`→`text("false")` ↔ `alembic/0005_node_operations.py` 同列同改。
  2. `app/models/sandbox_session.py` `auto_resume` 同改 ↔ `alembic/0003_idle_policy.py` 同改。
  3. `alembic/0007_shared_volumes.py` 两处 `read_only` `text("0")/text("1")`→`text("false")/text("true")`（ORM 侧无 server_default，无需动）。
  4. 迁移 0003/0005/0007 就地修改的合理性：这三个迁移在 PG 上**从未可能成功应用**（严格性拒绝），不存在携带旧字面量的 PG 部署，in-place 修复不破坏任何在役 schema（SQLite 已 stamp 的库不受迁移文件修改影响）；已在各迁移 docstring 留 NOTE 记录。
  5. `alembic/versions/0018_owner_fks.py`（新增，additive-only）：补 `trained_models.product_id` 索引（链缺，ORM 有）；两处 ORM `owner_id` FK（rag_generative_models/trained_models）反向对齐链（去掉 ORM FK）——本仓库链必须保持 SQLite 可跑（CI gate），而 SQLite 不能 ALTER-ADD FK，FK 在生产链建 PG schema 中从未可强制，故 ORM 侧让步（已在 0018 docstring 记录决策）。
- **复验**: 本地 sqlite 新库 `alembic upgrade head` 绿 + `alembic check` "No new upgrade operations detected" + `verify_schema_parity` OK（46 表）+ 受影响测试 17/17；**远程真实 PG** 上 alembic 全链 + create_all 验证（deploy#11 前置门禁，严格退出码）；镜像内 `import app.main` 3.12 冒烟 `PY312_IMPORT_OK`。
- **教训**: ① 本仓库两条结构性验证盲区（解释器版本差、方言严格性差）——新增"镜像构建后目标解释器导入冒烟 + 部署前真实 PG 跑全链/create_all"为固定门禁；② 验证脚本中 `cmd | tail` 会掩盖退出码（本轮 "ALEMBIC_PG_OK" 假阳性即由此），严格验证用 `set -o pipefail` 或直查 `$?`。

## D-e2e-09 — SS-12 设计缺口：scope 门放行后业务路由 user-only 依赖 401，机器凭据无法完成业务闭环

- **状态**: fixed（镜像构建/传输中，远程复验待 deploy）
- **发现**: 远程 S2 业务闭环脚本——full-scoped key 在 `POST /api/v1/data-products` 得 401 "API key credentials cannot authenticate as a user"（readonly key 同一端点却得 403 scope-denied——scope 层与路由层自相矛盾：scope 注册表认定 data 域机器可达，路由依赖却只认人类用户）。根因：spec §4.3 只定义了 `get_current_user` 保留为 user 路由解析器，未定义业务路由应使用何种依赖；5 个闭环路由（data-products/sandbox-sessions/contracts/data-resources/meta）全部挂在 user-only 依赖上，`data:write`/`sessions:write` 等 scope 形同虚设（key 通过 scope 门后必 401）。
- **根因**: 归属语义缺失——key 无 user 身份，而业务对象（产品/会话）的 owner_id 必填；spec 未定义 key 动作归属谁。`api_keys.issued_by`（签发人，FK users.id，NOT NULL）是现成锚点。
- **修复**: 新增 `app/core/deps.py::get_business_user`（D-e2e-09）：user 主体字节级同 `get_current_user`；key 主体 → 加载 `issued_by` 签发人作为业务身份（缺失/停用 → 401）。机器动作归属签发人（owner/created_by 落在签发 admin），审计面保持 kind=api_key 独立可辨（scope 门 + 每次调用审计已记，SS-12 §4.3）。5 个闭环路由 58 处 `Depends(get_current_user)` → `Depends(get_business_user)`（机械替换 + 计数校验，编译过）。**残面补修（远程复跑后）**：`require_roles` 工厂仍走 user-only 解析 → key 主体在角色门路由（如 data-resources upload 的 DATA_PROVIDER/ADMIN 门）必 401，`data:write` 仍不可达——`require_roles` 改经 `get_business_user` 解析（key 按签发人角色裁决；human-only 面由 require_human 路由级门先拒，不受影响）。
- **复验**: SS-12 套件 84/84（5 处断言从"旧缺口 401 哨兵"更新为新契约 200：test_in_scope_get_passes_gate / test_rotate_semantics / test_rate_limit_429 / 审计套件 _key_calls 帮助函数）；业务面回归 86/86（e2e_flows/data_products/sandbox_sessions/contracts——人类主体行为不变）；auth/admin_auth 10/10 + data_resources_api 2/2（require_roles 改动面）；镜像 3.12 导入冒烟过×2；远程 S2 复跑：product.create 201（签发人身份）达成，资源上传/发布/会话腿待 API6 部署后全绿验收。
- **注**: 未迁移面（pipeline/infra/gateway 等组）的 key 调用仍 401——同模式可后续按组迁移，不在本次闭环范围（已如实记录）。

## D-e2e-10 — 能力矩阵 l2c 列滞后：SS-13 M1+M2 已落地仍标全 planned（披露与代码不一致）

- **状态**: fixed（本地绿；随下一轮镜像到远程后生效）
- **发现**: S3 前置探测——远程 `GET /api/v1/meta/capability-matrix` 中 l2c 全部单元格 planned、note 自述"M1 后回写本矩阵"，而 SS-13 M1+M2（级别枚举 SandboxLevel.L2C、节点注册表、fail-closed 创建门禁、gRPC Exec 适配）已全部落地并通过 2871/0/3 回归；/health 的 `l2c_planned: true` 同样滞后。
- **根因**: PZ-13 矩阵（SS-11 引入，2026-10-12 baseline）是单一权威披露源，但 SS-13 实施未触发其写回（§4.2 更新纪律：代码变更后必须回写）。披露面声称"未上线"与在运代码矛盾——违反"以代码为准回写 spec/文档"铁律（方向是少披露而非伪造，但仍是披露失真）。
- **修复**: `app/services/capability_matrix.py` l2c 列按代码逐格写回（2026-10-13）：sync_exec/files_snapshot/pause_resume = real（含 evidence file:line）；stream_ws = unsupported（adapter 诚实 stub，CB-04 落地）；tee_proof_gate/network_policy_hotswap = planned（RT-06/节点验收）。LEVEL_METADATA l2c positioning "生产默认(规划中)"→"生产默认(软件就绪，需节点)"。/health `l2c_planned` 随之变 False（软件已发布，诚实信号）。
- **复验**: 矩阵自校验（import 期 _validate）过；tests/test_pz13_capability_matrix.py 3 处旧不变量断言更新为精确形状断言（l2c 六格状态映射 + l2c_planned False）；矩阵套件 13/13 绿。**远程实测生效（API6 轮）**：`/health` 返回 `"l2c_planned":false`，capability-matrix l2c 列按新形状披露。
- **附带修复（同轮）**: S3 脚本 `GET /api/v1/meta` → `/capability-matrix`（裸路径 404，与 D-e2e-09 同族）；nodes 响应键 `nodes` 非 `items`（实测确认）；trust_root 断言精确化（attestation root 语义 local-simulated/external，非会话级 TEE trust_root——后者默认 none 在 sandbox_sessions.py:365 代码验证，节点轨活验）。

## D-e2e-11 — 部署链缺 Vault transit 引擎前置：KMS 在"Vault 可达但无 transit"态下 KEK 生成必炸

- **状态**: fixed（部署脚本层，无需镜像重建；Round C 复验）
- **发现**: 远程 S2 `loop.resource.upload` 500 "Encryption/storage failed: HSM unavailable — cannot generate KEK"。hsm_adapter 工厂：Vault 可达且可认证 → 选 VaultTransitAdapter；dev 模式 Vault 默认**不开 transit 引擎** → `create_key` 404/403 → generate_kek raise。工厂的 fail-closed 分支（HSM_SOFTWARE_FALLBACK_ALLOWED=false 拒软件回退）因 Vault 可达而不触发——卡在三态中间（非纯软件、非完整 transit）。
- **根因**: 远程部署脚本启动 vault dev 容器后未启用 transit 引擎（应用 KMS 的 KEK 管理依赖 transit；本地测试环境不走 vault 路径所以全绿——又一环境差异盲区）。
- **修复**: `deploy/e2e-remote/remote_deploy.sh` vault wait_healthy 后追加：`docker exec cds-vault vault secrets enable -path=transit transit`（幂等：已启用则 grep secrets list 确认；两者皆败 → FATAL 退出）——dev vault 内存态每次容器重启重置，故每次部署必跑。
- **复验**: Round C 部署后 S2 资源上传/闭环全链 + S3 探针产品发布链均依赖 KEK 生成成功；远程实测为最终验收。

## D-e2e-12 — VaultTransitAdapter 对 hvac 的 create_key 调用参数名错（type→key_type）：真 Vault 上 KEK 生成必炸

- **状态**: fixed（API7 镜像传输/部署复验中）
- **发现**: D-e2e-11 修复后远程仍 500——api 日志：`Vault KEK generation failed: Transit.create_key() got an unexpected keyword argument 'type'`（3 次，S2/S3/S4 各一，同进程同单例）。S3 探针的 PASS 是自身宽松断言的假阳性（上传 500→rid=None→产品无资源→发布 400 被 fire-and-forget 吞掉）。
- **根因**: `hsm_adapter.py:89` `client.secrets.transit.create_key(name=key_id, type="aes256-gcm96")`——hvac 2.4.0 的参数是 `key_type`（内省签名定讫：create_key(name, ..., key_type, ...)；encrypt_data/decrypt_data/rotate_key 其余调用均匹配，无同族）。本地测试环境走 SoftwareHSMAdapter（无 vault）→ 2871 全绿结构性失明——又一个"模拟全过、真实集成必炸"缺陷（SS-14 存在的意义）。
- **修复**: `type=` → `key_type=`（单处；注释记录教训）。
- **复验**: hvac 内省签名比对过；HSM 套件本地绿；远程真 Vault（transit 引擎已启用）上资源上传/闭环全链成功为最终验收。

## D-e2e-13 — VaultTransitAdapter 的 transit 编解码双向错（hex/base64 混淆 + vault:v1: 密文 fromhex）

- **状态**: fixed（真 Vault 容器内实测 wrap/unwrap 全通；API8 镜像固化中）
- **发现**: D-e2e-12 修复后错误前移："HSM unavailable — cannot wrap DEK"，api 日志 `Vault wrap failed: non-hexadecimal number found in fromhex() arg at position 0`（'v' 在位置 0 = "vault:v1:" 前缀）。容器内探针复现（docker cp 修复文件进运行中容器 → GENERATE_OK/WRAP_OK/UNWRAP_OK round-trip True）。
- **根因**: Vault transit REST 契约是 **BASE64 双向**（hvac 直通不做客户端转换，docstring 明示）：旧代码 ① `plaintext=dek_bytes.hex()`（应为 b64）② `bytes.fromhex(response["data"]["ciphertext"])`（密文是 `vault:v1:<b64>` 版本化串，非 hex）③ unwrap 对称两处同错 ④ sign 的 `input=data.hex()`（潜伏，SM2 路径不走 Vault 未触发）。与 D-e2e-12 同族：本地软件 HSM 适配器的格式契约（裸 nonce+tag+ct）掩盖了 Vault 适配器的全部编码缺陷——2871 全绿第三次结构性失明（同缺陷类的第三个成员）。
- **修复**: `app/services/hsm_adapter.py` wrap：`plaintext=base64.b64encode(dek).decode()`，返回 `response["data"]["ciphertext"]`（vault:v1: 版本化密文原样存储，跨 KEK 轮换自描述）；unwrap：密文字串直传，`base64.b64decode(response["data"]["plaintext"])`；sign：`input=base64.b64encode(data).decode()`。新增 `import base64`。
- **复验**: 远程真 Vault（transit 引擎 + cds-master-kek）容器内探针：GENERATE_OK b'hsm:cds-master-kek' / WRAP_OK b'vault:v1:…' / UNWRAP_OK True（round-trip 字节相等）；KMS 套件 30/30 绿；**API8 远程全链最终验收（Round E）**：S2 16/16（资源上传 200 + 闭环全链，真 Vault KEK 生成/wrap/unwrap 在生产路径实证）、S3 探针资源上传 200、S4 产品发布链 PASS。
- **教训沉淀**: 模拟适配器的格式契约与真实后端的格式契约是两套语义；"接口同形"不等于"编码同义"——真实集成面（transit REST）必须用真后端跑一次往返才能定讫（skill 已录）。

## D-e2e-14 — 节点注册无运行时路径：register_node 仅测试调用，任何节点无法进入调度

- **状态**: fixed（端点+测试 5/5 绿；活节点实测随 bring-up 轮）
- **发现**: 节点轨 bring-up 推进时发现：`node_selector.register_node` 的调用者只有 tests（grep 全库无 app 侧调用）；sandbox_nodes 路由仅 list/isolate/unisolate——注册表无运行时填充路径，S2 活会话/调度在任何真实节点在场时也永远 NO_NODE。
- **根因**: SS-13 M1+M2 落地了数据面客户端/调度门/探针，但注册表面（管理端点或节点自注册）未实现（spec 未定义运行时注册面，RT-06/07 划为硬件期边界）——软件侧缺口，非设计内留白。
- **修复**: `app/api/sandbox_nodes.py` 新增 `POST /api/v1/sandbox-nodes`（admin 门 + 422 缺 cubelet_endpoint + 审计 node.register）；**不伪造健康**：行以 OFFLINE/unknown 落库，内置 gRPC 探针（List+GetStorageMetrics）确认后才 healthy/online，响应回带 probe_ok；拨号目标经 capabilities JSON 的 `cubelet_endpoint`（cube_node_client.node_cubelet_endpoint 既有约定）。测试 tests/test_node_registration.py 5 例（admin 门/422/探针过→online+审计/探针败→offline 诚实保留/node_id 校验）。
- **复验**: 测试 5/5 绿 + node 套件 12/12；活节点注册后 e2e_node_live_session.py N1（脚本已加 --node-endpoint 自注册步）为最终验收。

## D-e2e-15 — 镜像 uids_file 机器缺陷：节点镜像 uid 校验拒绝 CDS 镜像（pull 成功但 Create 不可用）

- **状态**: fixed（节点侧机器修复，rmi+pull 复验）
- **发现**: 节点轨首跑——镜像已在 TemplateCenter（渡渡鸟加速 pull 成功），Create 仍报 uid 校验失败。镜像 OCI 层的 `uids_file`（uid/gid 清单）与 CDS 镜像实际 uid 布局不一致（镜像构建期生成的 uid 清单与节点 uid 策略冲突）。
- **根因**: 上游节点对模板镜像强制 uid 清单一致性（安全策略：镜像声明的 uid 集合必须与可写层 uid 匹配）；CDS 镜像经加速站中转后 uid 清单陈旧（构建机差异）。
- **修复**: 节点侧机器修复：TemplateCenter `rmi` 旧镜像 + 重新 pull（重新生成 uids_file）+ 重发模板；零上游 diff。
- **复验**: Create 成功（活会话 l2c running），N2 后续 12 轮 e2e 全部可建会话。
- **教训**: 镜像中转（加速站/仓库迁移）可能携带构建机相关的 uid 清单元数据——节点侧 uid 校验失败先查 uids_file 而非镜像层。

## D-e2e-16 — asyncpg 跨事件循环缺陷族：循环依赖死锁 + 桥 session 跨 loop 段错误（三态同源）

- **状态**: fixed（SyncDbBridge 统一 + to_thread 隔离）
- **发现**: 节点轨连环三态：(a) 销毁路径死锁——插件 Detach/Destroy 回调本 API KMS（DEK revoke），gRPC 处理线程占用事件循环，API 侧 to_thread 等待完成 → 循环依赖；(b) 同族复发于卷插件 Attach 路径；(c) 桥 session（`sync_bridge_session`）在主（请求）循环直接 await → asyncpg 连接属桥循环 → 跨 loop 空指针段错误（v18 段错误族，v34 execute 500 再现）。
- **根因**: 同步上下文（gRPC 回调线程、to_thread worker）与两个事件循环（主循环 + 桥循环）的 session 归属混淆——asyncpg 连接绑定创建时的事件循环，跨 loop 复用必炸；而循环依赖（API→插件→API）要求长同步调用不占事件循环。
- **修复**:
  1. 所有"API 调用插件/节点"的长同步路径切 `asyncio.to_thread`（KMS 回调得以被服务，死锁类消除）。
  2. **SyncDbBridge**（`app/core/database.py`）：独立线程 + 独立事件循环 + 独立 async 引擎，`sync_db_bridge.run(coro)` 供同步上下文执行异步 DB 路径；`sync_bridge_session()` 工厂只允许桥循环内的协程使用。
  3. 适配器全同步路径的 DB 切桥（prepare_session_volume 等）；`_resolve_node` 异步版内的 DB 查询经 `sync_db_bridge.run`（桥 session 属桥循环，主循环直接 await 必段错误——v34 execute 500 根因）。
- **复验**: v18 段错误族未再复现；v34 修复后 execute 200（print(6*7)→42）；N2 活会话 12 轮 e2e 建会话全通；S2/S4 各 2 轮会话创建无死锁/段错误。
- **教训**: 双事件循环架构（主循环 + 桥循环）下，session/连接的对象归属必须写进函数契约（本库 `sync_bridge_session()` docstring 已钉死"only on bridge loop"）。

## D-e2e-17 — 卷契约族：PutVolumeData 强制 HasData + rootfs 只读回环挂载契约 + writable rootfs

- **状态**: fixed（插件 RPC 扩展 + 上游契约源码实证）
- **发现**: 活会话数据面三轮实证暴露的卷面契约缺口：(a) 插件 Attach 时卷无数据（master 卷面只建卷壳）→ Create 后 /data 空；(b) 结果读回需要宿主读 rootfs，而 rootfs 镜像（XFS 稀疏 ext4 10GB）是 VMM 活写文件——宿主直接读写 = ext4 双挂载损坏风险；(c) 冷启动 VM 的 /results /scratch /data 需可写（rootfs overlay upper）。
- **修复**:
  1. 插件新 RPC `PutVolumeData`（Attach 前强制 HasData 检查，数据经 CDS 扩展 API 落卷）+ `prepare_session_volume`（异步预建卷 + DEK ref + 数据，路由取数）。
  2. 插件新 RPC `ReadSandboxFile`（rootfs.go）：glob 定位 rootfs 镜像（`sb-<cubebox>-cds-rootfs-*`）→ `mount -o ro,loop` **只读**回环 → 读 `disk/<cubebox>/upper/<rel>` → 卸载；path-traversal 守卫 + 64MB 上限 + 目录→exists=false。只读不变式（宿主绝不写 VMM 在写的 ext4）写入源码注释。
  3. Create 请求 rootfs 卷改 `empty_dir "/"` 10Gi（writable overlay）。
- **复验**: 探针端到端：`results/.probe-marker` 经 ReadSandboxFile 读回 b'marker-test\n' ✓；N3 print(6*7)→42（.code 中转文件读回）；N4 512B 上传/下载 byte-identical。

## D-e2e-18 — 上游冷启动卷面缺失（CubeSandbox v0.7.3-rc2 构建限制）+ 数据面 v2（exec 注入 + rootfs 只读读回）

- **状态**: fixed（CDS 侧数据面 v2，零上游 diff；上游限制如实记录）
- **发现**: N2 活会话打通前的决定性实证——CubeSandbox v0.7.3-rc2 **冷启动沙箱不把声明卷（host_dir/empty_dir/plugin）挂进 guest**：/results /scratch /data 仅为可写 rootfs overlay upper 里的普通目录。证据链：(a) 三目标写探针（/data /results /scratch 各写 .probe-*）宿主全盘 find 无痕 + 插件 staging 目录无痕；(b) shim 日志零 virtiofs 条目、宿主无 virtiofsd 进程、cubelet 日志无 [hostdir] generateSandboxVirtiofsOpt 行；(c) **决定性**：RO 回环挂载 rootfs 镜像 → 探针文件恰在 `disk/<cubebox>/upper/results/`；(d) 机制源码：hostdir/plugin 卷经 cube.virtiofs annotation（virtiofs.go:35）→ shim ANNO_VIRTIOFS（config.rs:233）→ VMM virtio-fs 设备，该路径冷启动未生效（pluginvolume.go:238-262 实证 plugin 卷也汇入同一 HostDirBackendInfos 通道）；(e) exec 进程确实在容器命名空间内跑（agent rustjail join_namespaces, shim "wait process summary ... status: 0"）——写成功但落 upper。
- **定性**: 上游构建限制（非 CDS 缺陷）——声明卷挂载路径存在但冷启动未生效；honest degradation：volume 保留为节点侧 SM4 at-rest 副本（数据仍在卷上加密保存），VM 内数据面改 v2。
- **修复（数据面 v2，honest）**: egress（exec 输出/下载）= 宿主侧**只读**回环挂载 rootfs 镜像读回（ReadSandboxFile）；ingress（上传/产品数据）= exec 通道（base64 120KB 分块 sh -c → /data/session-data，3 次重试）；产品数据注入失败 = **fail-closed**（destroy key + terminate + release 配额 + FAILED + audit sandbox.data_injection_failed，不静默降级——l2c 数据面是会话契约一部分）。
- **复验**: N3/N4 绿（文件中转 + 读回）；S2 22/22（上传/执行/下载全链）；注入块在 create 路径真实执行（/data/session-data 落位）。
- **教训**: 声明卷 ≠ 可见卷——数据面契约必须用"写探针 + 宿主全盘 find"实证，不能信 Create 请求接受即生效。

## D-e2e-19 — exec 通道契约族：fire-and-forget stdout 丢弃 → 结果文件中转 + container_id 右切 + Update action + Create deadline

- **状态**: fixed（适配器重写，上游契约源码/日志实证）
- **发现**: Cubelet Exec 是 fire-and-forget：stdout 丢弃（shim 日志实证 "wait process summary" 无 payload），退出码只落 shim 日志。N3 首轮实现（等 Exec 返回取 stdout）永远空输出 → 120s 超时。同族暴露：(b) container_id `l2c-<node>-<sandbox32hex>` 中 node_id 含连字符（node-local-1）→ 左切解析错；(c) cubelet Update 强制 annotations 携带 `cube.master.update_action`（update.go:67 实证——无 annotations = 130483 "must provide Annotations"）；(d) Create 实测 >5s（镜像检查 + 网络 + 卷 attach + vmm）→ 默认 5s deadline 必超时。
- **修复**:
  1. execute() 重写：exec 启动包装器（`inner > out 2>&1; echo $rc > code; echo $(wc -c < out) >> code; echo DONE >> code`），宿主侧 ReadSandboxFile 轮询 .code 三行标记（rc/size/DONE），按 size 截断读 out + 尺寸不符重读（guest ext4 写入经 VMM 异步刷宿主，读侧可落后 guest 视图——实证 N5/N6 空输出竞态）。
  2. `_parse_container_id` 正则右切（尾部 32 位 hex = cubelet sandbox_id，实证 cubebox ls）。
  3. Update 请求 annotations 携带 `cube.master.update_action=network`。
  4. Create 专用 deadline 120s（`_create_deadline_ms`）。
- **复验**: N3 42 / N4 roundtrip / N5 egress BLOCKED / N6 host-invisible 全绿；S2 execute PASS。

## D-e2e-20 — l2c 会话契约面缺口族：活动事件/下载审查/usage/egress 推送

- **状态**: fixed（路由层 l2c 分支补齐，与 L3 契约对齐）
- **发现**: 节点轨活会话全绿后，S2/S4 暴露 l2c 会话的四处契约面缺口（远程轨 L3 路径从未跑过这些断言）：(a) /audit 对 l2c 永远空——bwrap 专属的 in-VM 审计 helper 在 l2c 不存在，控制面不写活动事件；(b) l2c 文件下载无 T5 输出审查、无 X-CDS-Output-Review 头（S2/S4 T3 空头）；(c) /usage 对 l2c 400 "Session workspace not found on host"（无宿主 workspace）；(d) 策略移除时 default-deny 推送 130483 "key not found"（cubelet Update 对已销毁 cubebox 的 Get 失败原样填 ret_msg）——误报为 security_event。
- **修复**:
  1. /execute + /exec 路由：l2c 每次执行后写 application/code_execution / shell_exec 活动事件到 tmpfs jsonl（/audit fallback 读此文件；ClickHouse 在场时 collector 统一 flush）。
  2. 下载 l2c 分支：内容先过 T5 审查，critical 发现 409 不放出，响应携带 X-CDS-Output-Review（passed/redacted + findings 计数）。
  3. /usage l2c 分支：数据卷大小（节点侧 SM4 at-rest 副本，InspectStorageVolumes）+ VM 侧目录统计（exec du，best-effort，to_thread 隔离），无宿主枚举处诚实置 None（不伪造）。
  4. cube_egress：130483 "key not found" 指纹（上游 update.go:94 实证）映射为 sandbox_gone no-op（不记 security_event）。
- **复验**: N9 12/12 全绿（3 audit actions incl exec）；S2 22/22（X-CDS-Output-Review='passed; findings=0' + usage PASS）；S4 12/12（T3 同头）；egress 修复后 130483 错误 0 次（09:04 起 3 次 terminate 干净）。

## D-e2e-21 — e2e 脚本设计缺陷族（节点轨首跑暴露，远程轨未触达的路径）

- **状态**: fixed（脚本层，产品行为均正确）
- **发现**: 节点轨首跑 4/12 失败全部是脚本设计问题，产品行为经逐一核实均正确：
  1. N6 探针把**容器镜像自带的** /etc/passwd 当宿主文件断言（镜像内 /etc/passwd 合法可读，含 "root:"）→ 改断宿主独有内容（宿主用户 kratos 不在 VM passwd + 宿主 /home/kratos 不存在）。
  2. N7 从 register 响应取 username（TokenResponse 无该字段 → 422）→ 用请求时的用户名。
  3. N9 读 `action` 字段（/audit 返回 `event_type`）→ 字段兼容。
  4. S4 会话由 admin 创建、buyer A 访问 → 403 是**租户隔离的正确行为**（远程轨无节点、该路径从未运行过）→ buyer A 自建会话（TDS 正典流程）。
- **定性**: 远程轨 12 轮 e2e 只覆盖了 L3/无节点路径；节点轨活会话路径的首次执行暴露脚本与产品契约的漂移——产品侧四例全部行为正确（隔离/审查/披露均按设计）。
- **复验**: 修正后 N1-N10 12/12、S4 12/12 全绿（v38 / S4-v2）。

## D-e2e-22 — 全量回归 21-failed：RT-07 新契约 vs SS-13 时代测试 fixture 漂移（+1 真产品 bug）
- **发现**: 节点轨收口全量回归（排除 live-URL e2e 文件）21 failed，隔离复跑 21/21 一致复现（非测试间污染，diagnose 流程定性）。失败集中在 SS-13 时代文件（ss13_tee_policy/ss13_e2e/sandbox_sessions/e2e_flows/node_registration/node_operations/output_control），根因 = RT-07 在途 diff 新增的产品契约晚于这些测试的编写时点：
  1. l2c 会话创建 fail-closed 要求产品携带已上传数据资源（会话数据下载）→ 无资源产品 500；
  2. `prepare_session_volume` 卷预建（master EnsureVolume + 插件 KMS 注册 + PutVolumeData unix socket）硬件绑定、测试环境无 → 500；
  3. 节点注册探针 = 内联 List + GetStorageMetrics（D-e2e-14 新增，测试仍 monkeypatch 旧 `cube_node_health_probe` 包装）；
  4. container_id 右切解析要求 32 位 hex sandbox_id（D-e2e-19）→ conftest 桩 `sbx-test-N` 形态不符；
  5. `capabilities` dict 契约（node_cubelet_endpoint 按 dict 读）vs 旧测试 list 形态；
  6. provision 预建会话 results 目录（cubelet bind-mount 契约）→ 默认 `/var/lib/cds` 测试用户不可写。
- **真产品 bug（1 个）**: `sandbox_sessions.py` create 路径 `session_data` 在 `isinstance(adapter, CubeRuntimeAdapter)` 分支内赋值、分支外的注入块（:618）引用 → 非 Cube 适配器（测试 dummy/未来运行时）触发 `UnboundLocalError`。修复 = 无条件初始化 `session_data: bytes | None = None`（产品行为不变，仅消除未绑定引用）。
- **修复方式（零产品行为削弱）**: conftest 测试替身面扩展（prepare_session_volume 替身同 import-time 模式 + get_storage_metrics 桩 + list 桩按上游真实契约去 ret + 32-hex sandboxID + publish_product 发布时自动补已上传小 CSV 资源 + CDS_CUBE_RESULTS_DIR 钉 /tmp）；node 测试 capabilities list→dict；registration 探针 monkeypatch 改新入口；DummySandboxRuntime 增 get_adapter。会话数据契约（产品必须带已上传资源）保持对测试真实生效（fixture 上传资源而非绕过检查）。
- **复验**: 8 文件 113/113 → 16 文件 191/191 全绿；全量回归 **2876 passed / 3 skipped / 0 failed**（800.59s，不劣于 2871 基线，+5 = 节点轨新测试）。

## 登记规则

- 新缺陷追加 `D-e2e-NN`，状态流转：open → fixed → 复验证据落档。
- 测试发现但定性为既有的（非本轮引入）标 `accepted-risk` 并注明来源波次。
