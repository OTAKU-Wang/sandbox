# CDS 错误码目录（W2 · 统一错误契约）

所有错误响应统一为：

```json
{"code": "RATE_LIMITED", "message": "...", "detail": {...}, "request_id": "0a1b2c3d4e5f6071"}
```

- `request_id` 贯穿日志与响应头 `X-Request-ID`，用于跨组件追踪（W1）
- `Retry-After` 头仅在 429/503 暂态错误时出现，单位秒
- 兼容性：StarletteHTTPException 与 422 校验错误仍保留 legacy `detail` 字段

## 错误码清单

| code | HTTP | 触发场景 | 客户端应对 |
|---|---|---|---|
| `INTERNAL_ERROR` | 500 | 未预期异常（服务端已记录 request_id） | 携带 request_id 报告；可安全重试一次 |
| `VALIDATION_ERROR` | 422 | 请求参数校验失败（detail.errors 明细） | 修正请求，不要重试 |
| `HTTP_{status}` | 4xx/5xx | 未接入目录的既有 HTTPException（legacy `detail` 保留） | 按 HTTP 语义处理 |
| `SESSION_NOT_FOUND` | 404 | 会话不存在或无权访问 | 不要重试 |
| `SESSION_TERMINATED` | 410 Gone | 会话已终态（terminated/completed/failed/expired） | **停止重试**；如需数据联系管理员 |
| `SESSION_STATE_CONFLICT` | 409 | 状态不允许该操作（如 SUSPENDED 态执行） | 依据业务先 resume/refresh 再操作 |
| `OPERATION_LOCKED` | 503 + Retry-After | 操作互斥（如同会话回滚进行中重复触发） | 按 Retry-After 退避后重试 |
| `REQUEST_TIMEOUT` | 408 | 同步等待超时（操作可能已在后台完成） | 先查询结果再决定重试 |
| `RATE_LIMITED` | 429 + Retry-After | 限流（IP/用户/登录专用桶） | 按 Retry-After 退避 |
| `RESOURCE_EXHAUSTED` | 429 | 资源耗尽（无可用节点等） | 不要立即重试；等待资源释放 |
| `QUOTA_EXCEEDED` | 429 | 租户配额不足 | 释放配额或申请提额后重试 |

## 降级语义（诚实声明）

- **限流 fail-open**（W3）：Redis 不可用时回退进程内本地限流并输出 WARNING——限流组件故障不拒绝全量流量。这是显式决策，非静默降级。
- **MPC 托管语义**（N3/N11）：`mpc_service` 为 **Shamir 秘密托管**（密钥份额拆分/重建/轮换/销毁，跨重启持久化），**非 MPC 计算协议**。HE/MPC 计算（SecretFlow HEU/SPU）处于评估阶段，未部署——`GET /api/v1/mpc/capabilities` 与监控 `/security-posture` 的 `mpc` 项如实披露 `compute=evaluating`。调用方不得假设任何跨方密文计算能力。
- 410 语义为 CubeSandbox 对齐项（C2）：客户端 SDK（`cds_sdk.CDSApiError.code == "SESSION_TERMINATED"`）应据此停止重试。

## 既有端点语义对齐（以测试期望为准）

| 场景 | 现状 | 目录登记 |
|---|---|---|
| 会话已 TERMINATED 后取详情/执行 | 404（e2e 依赖 404） | `SESSION_TERMINATED_OBSERVED` — SDK 文档注明；后续单独 PR 迁移到 410 |
| 暂停态执行 | 400 | `SESSION_STATE_CONFLICT`（Round 39 门禁保留 400，SDK 按码处理） |
| 快照回滚进行中重复触发 | 503 + Retry-After（W11 引入后） | `OPERATION_LOCKED` |
