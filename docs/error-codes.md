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
