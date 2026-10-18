# CDS 错误码目录（W2 · 统一错误契约）

所有错误响应统一为：

```json
{"code": "RATE_LIMITED", "message": "...", "detail": {...}, "request_id": "0a1b2c3d4e5f6071"}
```

- `request_id` 贯穿日志与响应头 `X-Request-ID`，用于跨组件追踪（W1）
- `Retry-After` 头仅在 429/503 暂态错误时出现，单位秒
- 兼容性：StarletteHTTPException 与 422 校验错误仍保留 legacy `detail` 字段

## 错误码清单（PZ-15：用户提示 / 下一步）

| code | HTTP | 触发场景 | 用户提示（zh） | 下一步 |
|---|---|---|---|---|
| `AUTH_MFA_REQUIRED` | 401 | MFA 已确认用户登录时缺少 mfa_code，或 TOTP/恢复码不正确（PZ-10 双轨，`auth.py` 登录门禁） | 需要完成双因素验证 | 打开身份验证器输入 TOTP，或使用恢复码；均不可用时联系管理员 |
| `SESSION_TERMINATED` | 410 Gone | 会话已终态（terminated/completed/failed/expired），或 W9 自动 resume 被治理契约阻断（唯一抛出点 `sandbox_sessions.py:~1454`） | 会话已终止，数据不可恢复 | 不要重试；如需数据联系管理员（审计保留） |
| `SESSION_STATE_CONFLICT` | 409 | 状态不允许该操作（如 SUSPENDED 态执行、终态 pause/terminate；`sandbox_sessions.py` 各状态校验点，W2 接线后替代原 400） | 会话当前状态不允许该操作 | 先 resume/refresh 再操作（提示当前状态，见 `message`） |
| `OPERATION_LOCKED` | 503 + Retry-After | 操作互斥（如同会话回滚进行中重复触发） | 另一操作进行中 | 按 Retry-After 等待后自动重试 |
| `REQUEST_TIMEOUT` | 408 | 同步 execute 等待超时（`sandbox_sessions.py` execute 端点；操作可能已在后台完成） | 同步执行等待超时，操作可能仍在沙箱内完成 | 先查询结果（会话状态/日志/输出）再决定重试 |
| `RATE_LIMITED` | 429 + Retry-After | 限流（IP/用户/登录专用桶，`core/security.py`）；单用户并发会话数达上限（create 前置检查） | 请求过于频繁（或并发会话已达上限） | 按 Retry-After 退避；并发上限时先终止已有会话 |
| `RESOURCE_EXHAUSTED` | 429 | 资源耗尽（无可用节点等） | 资源耗尽，暂无可用节点 | 不要立即重试；等待资源释放 |
| `QUOTA_EXCEEDED` | 429 | 租户资源配额不足（create 前置 `check_tenant_quota`）；网关调用日配额（`app/api/gateway.py` → `gateway_service.check_quota`） | 租户配额不足 | 释放配额或申请提额 |
| `STREAM_UNSUPPORTED` | WS close / JSON 错误 | 该级别不支持流式执行（`session_stream.py` 仅 L0/k8s 可流式，L1/L2/L3 拒绝） | 该级别不支持流式执行 | 用同步 execute（能力矩阵指引） |
| `SANDBOX_NOT_FOUND` | 404 | 底层沙箱已不存在（节点侧，SS-10 新码） | 底层沙箱已不存在（节点侧） | 终止会话重建；报告 request_id |
| `RUNTIME_UNAVAILABLE` | 400 | L2' 数据面未启用 / 节点未就绪（SS-10 新码，dataplane 关闭时 l2c 请求） | L2' 数据面未启用/节点未就绪 | 联系管理员检查 cube 节点（FG-017） |
| `TEE_NODE_UNAVAILABLE` | 400 | `tee_policy=tee_required` 时没有 TEE 能力（capabilities.tee ∈ {tdx, sev_snp}）的 cube 节点（SS-13 新码，抛出点 `sandbox_sessions.py` l2c create 门） | 无可用 TEE 节点（诚实降级：不伪装 TEE） | 联系管理员确认 TEE 节点（RT-06），或改用 `tee_if_available` |
| `LEGACY_LEVEL_RETIRED` | 400 | 请求了已退役的传统沙箱级别（L0/L1/L2/L3/k8s），且 `CDS_SANDBOX_LEGACY_ENABLED` 未开启（SS-13 单路径切换，抛出点 `sandbox_sessions.py` create） | 该沙箱级别已退役，仅支持 l2c | 使用默认级别 `l2c`；dev/test 可设 `CDS_SANDBOX_LEGACY_ENABLED=true` |
| `API_KEY_INVALID` | 401 | 平台 API 密钥不存在或已过期（SS-12 新码，`core/deps.py` key 认证路径：`cds_ak_` 凭据 SM3 哈希未命中 / 惰性过期判定） | 密钥格式不符 / 不存在 / 已过期 | 检查密钥是否完整、是否已过期；联系平台管理员 |
| `API_KEY_INACTIVE` | 401 | 密钥已禁用 / 已吊销 / 连续认证失败进入冷却（SS-12 新码，`detail.reason` ∈ disabled / revoked / cooldown；冷却为进程内计数器，多实例下近似） | 密钥被禁用 / 吊销 / 失败冷却中 | 密钥被管理员禁用或吊销，或连续失败进入冷却；联系管理员或稍后重试 |
| `API_KEY_SCOPE_DENIED` | 403 | 该密钥无目标功能域的权限（SS-12 新码，`core/scopes.py` scope 门 / human-only 门 / 未注册前缀 default-deny） | 该密钥无此功能域的权限（含 human-only 路由） | 该 API 密钥权限不足；请管理员调整 scope 或改用其他密钥 |
| `BAD_REQUEST` | 400 | 平台密钥管理请求语义不合法（`app/api/api_keys.py`：scope 语法错误、吊销后重新启用、轮换已吊销或已过期的密钥） | 请求不被允许（见 `message` 与 `detail.reason`） | 按 `detail.reason` 修正；过期密钥请新建而不是轮换 |
| `POLICY_COMPILE_ERROR` | 400 | L2' 网络策略包编译失败（`app/services/policy_compiler.py`：策略包声明 `allow_internet_access=true`，或某条 egress 规则无任何 match 约束——空 match 等于放行全部，拒绝编译 fail-closed） | 网络策略包不合法，未下发 | 修正策略包（必须 default-deny，每条规则给出 host/domain/path/methods 之一） |
| `NO_NODE_TEMPLATE` | 503 | 无节点具备所需模板（节点均未 READY，SS-10 新码） | 无节点具备所需模板 | 等待模板分发完成或联系管理员 |
| `SESSION_NOT_FOUND` | 404 | 会话不存在（`sandbox_sessions.py` 各会话 404 点，W2 接线后替代原 legacy 404） | 会话不存在或已删除 | 不要重试；核对会话 ID，必要时重新创建 |
| `INTERNAL_ERROR` | 500 | 未预期异常（服务端已记录 request_id） | 服务端内部错误 | 携带 request_id 报告；可安全重试一次 |
| `VALIDATION_ERROR` | 422 | 请求参数校验失败（detail.errors 明细） | 请求参数不合法 | 修正请求，不要重试 |
| `RESOURCE_OVERRIDE_EXCEEDED` | 400 | 请求的资源规格超出合约已批准的 `sandbox_resource_spec` 上限（`app/api/compute.py` 规格覆盖、`app/api/notebooks.py` 开发任务内存） | 该规格高于合约配额，不能申请放大 | 下调到不超过合约规格；需要更高配额走合约变更，不要重试 |
| `NOTEBOOK_NO_CELLS` | 409 | Notebook 整本运行时没有任何非空单元格（`app/api/notebooks.py` `/run`）——空跑会白起开发沙箱并自增版本 | 这本 Notebook 没有可执行代码 | 先写代码并保存再运行；不要在运行前重试 |
| `NOTEBOOK_UNAVAILABLE` | 503 | Notebook 工作台记录暂不可写（`app/api/notebooks.py` dev-session：开发会话已建但记录写回失败） | 开发沙箱暂时不可写，请重试 | 重试一次；仍失败带 request_id 报告（不降级为本地执行） |
| `SANDBOX_SESSION_MISSING` | 503 | 在线开发沙箱创建后按 ID 取不到会话记录（`notebooks.py` dev-session 后置校验） | 开发会话记录缺失 | 重试或联系管理员；不会静默改用其它运行时 |
| `SANDBOX_MODE_NOT_PERMITTED` | 400 | 请求的沙箱模式不在合约允许清单内（`app/services/workflow_service.py` 模式门，词表归一后判定，SS-15 §4.9） | 合约未放行该沙箱模式 | 改用在合约允许模式清单内的模式，或请买方调整合约 |
| `SANDBOX_MODE_UNSUPPORTED` | 400 | 请求的沙箱模式不在平台词表内（`workflow_service.py`，别名归一后仍未知） | 不支持的沙箱模式 | 用 `l2c`（MicroVM 单路径）；未知词直接拒绝，不做近似匹配 |
| `WORKLOAD_NOT_PUBLISHED` | 412 | 合约下没有 published 生产工作负载就创建 API 端点或触发计算（`app/api/deliveries.py` 端点创建、`compute_scheduler` 运行前置） | 还没有已发布的生产负载 | 先在测试沙箱跑通测试再发布（§五 ③），不要重试 |
| `WORKLOAD_NOT_TEST_ENV` | 412 | 发布非测试沙箱（`env != test`）的工作负载（`workflow_service.publish`，`GATE_NOT_TEST_ENV`） | 只有测试沙箱里的负载可以发布到生产 | 在测试沙箱环境内创建/复制该负载后再发布 |
| `WORKLOAD_TEST_REQUIRED` | 412 | 发布前未通过测试沙箱验证：状态非 `test_passed`，或缺少测试运行记录 `test_run_id`（`workflow_service.publish`） | 负载还没有通过测试沙箱验证 | 先 `POST /api/v1/workloads/{id}/test` 跑通再发布；不要重试发布 |
| `WORKLOAD_TEST_NOT_PASSED` | 412 | 关联的测试运行存在但 `passed=false`（`workflow_service.publish`；三项检查 entrypoint_executed / output_policy_clear / no_runtime_exception 任一失败即不通过，无权重） | 最近一次测试未通过 | 按 `detail.checks` 修复代码后重新测试 |
| `WORKLOAD_HASH_MISMATCH` | 412 | 测试通过后 entrypoint 又被改动：被测 `entrypoint_hash` 与当前内容哈希不一致（`workflow_service.publish`，detail 含 `tested_hash`/`current_hash`；防“测的是 A、发的是 B”） | 代码在测试之后被修改，必须重新测试 | 对该负载重新执行测试沙箱验证后再发布 |
| `WORKLOAD_STATE_CONFLICT` | 412 | 负载状态机不允许该转换（`ALLOWED_TRANSITIONS` 白名单，`workflow_service.assert_transition`） | 负载当前状态不允许该操作 | 查询负载状态，按 draft→testing→test_passed→published→retired 顺序操作 |

| `DELIVERY_METHOD_NOT_ALLOWED` | 403 | 交付通道不在合约 `delivery_methods` 内（`deliveries.py::_require_method`，`detail.configured` 回显已选方式） | 合约未开启该交付方式 | 在合约交付配置里勾选该方式后重试 |
| `CONTRACT_MISMATCH` | 403 | 传入的会话/负载不属于该合约（`deliveries.py` 跨资源校验，防跨合约取数） | 该资源不属于此合约 | 核对 session_id / workload_id 与 contract_id 的配对；不要重试 |
| `NO_OUTPUT_FILES` | 409 | 会话没有可交付输出文件就打成果包（`deliveries.py` `POST /deliveries/dataset/bundle`） | 会话还没有输出文件 | 先在会话内产出结果再打包；平台不产出空包 |
| `BUNDLE_TOO_LARGE` | 413 | 成果包超过 512MB 上限（`deliveries.py`） | 交付包过大 | 分批交付，或改走数据产品通道 |
| `WEBHOOK_EVENT_UNSUPPORTED` | 400 | 订阅了平台不支持的推送事件类型（`deliveries.py` 订阅创建/更新） | 不支持的推送事件类型 | 按 `detail` 的支持清单选择事件 |
| `ENDPOINT_SLUG_TAKEN` | 409 | OpenAPI 端点 slug 与既有端点冲突（`deliveries.py`，slug 全局唯一） | 该 slug 已被占用 | 换一个 slug |
| `INVALID_REQUEST_BODY` | 400 | `/open/v1/{slug}` 请求体不是 JSON 对象（`app/api/open_api.py`） | 请求体必须是 JSON 对象 | 修正请求体；该码可安全重试同一修正后的请求 |
| `COMPUTE_RUN_ACTIVE` | 409 | 同一合约已有未终结的计算运行又手动触发（`app/api/compute.py` claim 门） | 该合约已有计算在跑 | 等当前运行结束或取消后再触发 |
| `COMPUTE_RUN_RUNNING` | 409 | 取消仍在执行的计算运行（会牵连沙箱内真实执行）（`compute.py` cancel） | 运行中不能静默中止 | 终止其沙箱会话以停止执行 |
| `COMPUTE_RUN_TERMINAL` | 409 | 取消已处于终态的运行（`compute.py` cancel） | 运行已结束 | 刷新运行记录查看结果 |
| `ENDPOINT_DISABLED` | 502 | 调用了已停用的 OpenAPI 端点（`delivery_service.execute_endpoint`） | 端点已停用 | 启用端点或改用其它端点；勿高频重试 |
| `WORKLOAD_UNAVAILABLE` | 502 | 端点关联负载不可用或未绑定数据产品（`execute_endpoint`） | 端点背后的负载不可用 | 重新发布负载或重绑端点 |
| `WORKLOAD_FAILED` | 502 | 沙箱内执行退出码非 0（`execute_endpoint`，`message` 含退出码） | 计算失败 | 回 Notebook/测试沙箱复现，修完再发布 |
| `OUTPUT_BLOCKED` | 502 | 输出被出域策略拦截（`execute_endpoint` 取 `blocked_reason`；`/deliveries/dataset/bundle` 在打包时命中 T5 critical 也返回本码，包体不上传） | 输出被策略拦截 | 按拦截原因调整产出；被拦数据不会返回 |
| `OUTPUT_REVIEW_REQUIRED` | 409 | 下载出口审查加固之前生成的交付包（`/deliveries/dataset/{id}/download`，包内 `MANIFEST.json` 无审查标记） | 旧交付包未经出口审查 | 重新生成交付包（自动过 T5 审查）后下载 |
| `CALLER_UNKNOWN` | 502 | 调用方用户记录不存在（`execute_endpoint`） | 调用方身份异常 | 重新签发 API 密钥并核对绑定用户 |
| `HTTP_{status}` | 4xx/5xx | 未接入目录的既有 HTTPException（legacy `detail` 保留） | 按 `message`/`detail` 理解 | 按 HTTP 语义处理 |

## 降级语义（诚实声明）

- **限流 fail-open**（W3）：Redis 不可用时回退进程内本地限流并输出 WARNING——限流组件故障不拒绝全量流量。这是显式决策，非静默降级。
- **MPC 托管语义**（N3/N11）：`mpc_service` 为 **Shamir 秘密托管**（密钥份额拆分/重建/轮换/销毁，跨重启持久化），**非 MPC 计算协议**。HE/MPC 计算（SecretFlow HEU/SPU）处于评估阶段，未部署——`GET /api/v1/mpc/capabilities` 与监控 `/security-posture` 的 `mpc` 项如实披露 `compute=evaluating`。调用方不得假设任何跨方密文计算能力。
- 410 语义为 CubeSandbox 对齐项（C2）：客户端 SDK（`cds_sdk.CDSApiError.code == "SESSION_TERMINATED"`）应据此停止重试。

## 既有端点语义对齐（PZ-15 收口）

| 场景 | 现状 | 目录登记 |
|---|---|---|
| 会话已 TERMINATED 后取详情/执行 | 404（会话已不存在/已清理） | `SESSION_TERMINATED_OBSERVED` — **漂移收口（PZ-15）**：410 `SESSION_TERMINATED` 是契约语义（唯一 410 抛出点 `sandbox_sessions.py:~1454`，行为不变）；e2e 对 404 的依赖是测试侧的显式契约——`tests/test_e2e_flows.py::test_e2e_minimal_sandbox_execution` 已显式断言“终态会话 execute 返回 400（dev-sandbox 路径）/ 会话路由 404（已清理路径）”，后续单独 PR 将执行类端点迁移到 410 时需同步更新这些显式断言 |
| 暂停态执行 | 409（W2 接线后） | `SESSION_STATE_CONFLICT`（原 Round 39 门禁 400 已升级为 409；SDK 按码处理） |
| 快照回滚进行中重复触发 | 503 + Retry-After（W11 引入后） | `OPERATION_LOCKED` |

## 级别能力（PZ-13 · 权威矩阵摘要）

> 单一权威源为 `app/services/capability_matrix.py`（`GET /api/v1/meta/capability-matrix` + `/health` 能力区）；前端（`cds-frontend/src/types/enums.ts`）与 SDK（`cds_sdk.CDSClient.capability_matrix()`）必须与本节一致——`tests/test_capability_matrix_consistency.py` 锁定 API ↔ FE ↔ SDK ↔ 本节四源。

| level | 标签 | 定位 | 弃用 |
|---|---|---|---|
| `L0` | L0 | 开发级 | 否 |
| `L1` | L1 | 软件机密级 | 否 |
| `L2` | L2 | 软件增强(弃用观察) | 是 |
| `l2c` | L2' | 生产默认(规划中) | 否 |
| `L3` | L3 | 进程隔离 | 否 |
| `k8s` | k8s | Pod隔离(弃用观察) | 是 |
| `CONTRACT_TERMINATED` | 410 Gone | 合约已终止仍要改交付通道/计算调度配置（`app/api/contracts.py:320`） | 合约已终止，配置不可修改 | 不要重试；需要变更请新建合约（终态数据按审计保留） |
| `FORBIDDEN` | 403 | 资源存在但不属于当前主体：共享卷非本人（`shared_volumes.py:51`）、会话非本人（`deliveries.py:74`）、API Key 无权调用该开放端点（`open_api.py:39`，detail 带 `slug`） | 无权访问该资源 | 核对当前身份/密钥的资源授权范围；不要重试 |
| `INVALID_TIMEZONE` | 400 | 合约 `compute_tz` 不是可识别时区（`contracts.py:325`，detail 带原值） | 无法识别的时区 | 使用 IANA 时区名（如 `Asia/Shanghai`）后重新提交 |
| `NO_NODE_AVAILABLE` | 429 | 在线节点全部被人工隔离（`scheduling_disabled`），`detail.disabled_count` 给出台数（`node_selector.py:113`）；与 `RESOURCE_EXHAUSTED` 的区别是“无人可调度”而非“容量满” | 所有在线节点已隔离，无法调度 | 不要立即重试；联系管理员解除节点隔离 |
| `VOLUME_EXISTS` | 409 | 同名共享卷已存在（`shared_volumes.py:34`） | 卷名已被占用 | 换个卷名或复用已有卷 |
| `VOLUME_NOT_FOUND` | 404 | 按 id 找不到共享卷（`shared_volumes.py:49`） | 卷不存在 | 刷新卷列表后重试（可能已被删除） |
| `WEBHOOK_URL_REJECTED` | 400 | Webhook 回调地址未通过出网校验（SSRF / 私网 / 协议白名单，`deliveries.py:254,307,427`；`message` 即拒绝原因，detail 带 `url`） | 回调地址被拒绝 | 改用可公网到达的 HTTPS 地址，并按 `message` 指出的原因调整 |
