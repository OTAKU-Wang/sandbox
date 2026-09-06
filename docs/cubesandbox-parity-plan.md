# CDS × CubeSandbox 能力对齐 · 正式产品化实现方案

> 日期：2026-09-06
> 基准：`D:\workspace\CubeSandbox`（腾讯开源 CubeSandbox v0.7，基于源码 + `openapi.yml` + `docs/` 实读，非仅 README）
> 性质：可执行落地方案，面向低级执行 agent。文中所有 CDS "代码现状" 均已于 2026-09-06 对源码复核（关键锚点直接复核：`app/core/security.py`、`app/api/admin.py`、`app/main.py`、`requirements.txt`、`.gitignore`；其余锚点来自全量代码审计）。实施时若与现状冲突，以代码为准并回写本文件。
> 前序：`docs/ai-sandbox-gap-remediation-plan.md`（Round 32–41）已完成 PPT 设计基线的安全闭环（141 个软件 gap 清零）。**本方案不重复该范围**，聚焦 CubeSandbox 所代表的「生产级沙箱平台运营能力」。
> 范围：P0 产品化基线 7 项（详细到函数级）、P1 平台语义对齐 7 项、P2 路线图 5 项、横切面（配置矩阵 / 迁移 / 测试 / CI / 发布顺序 / 风险）。

---

## 一、基准能力全景与本方案范围

### 1.1 CubeSandbox 的产品化能力清单（对齐目标）

以下能力全部来自源码实读（引用为 CubeSandbox 仓库内路径）：

| # | 能力域 | CubeSandbox 基准 | 引用 |
|---|---|---|---|
| C1 | 沙箱生命周期状态机 | `running/pausing/paused/resuming/terminated` 五态 + 空闲 TTL（`onTimeout: kill\|pause` + `autoResume`）+ 恢复后新超时计时 | `docs/guide/lifecycle.md` |
| C2 | 精确错误契约 | `408`(同步超时)/`409`(冲突)/`410 Gone`(终态，客户端停止重试)/`503+Retry-After`(暂态锁)/`429`(限流)；DELETE 已终态幂等 204 | `openapi.yml`、`lifecycle.md` |
| C3 | 出站安全网关 | 默认拒绝 L7 域名规则（first-match、AND 匹配 scheme/port/sni/host/method/path）+ 凭证代理注入（秘密不进沙箱）+ JSONL 三级审计（access/security_event/tls_handshake，秘密脱敏） | `docs/guide/security-proxy.md` |
| C4 | 模板就绪契约 | 模板构建 = OCI 镜像→rootfs→探活（HTTP probe 2xx）→READY；创建返回即保证端口可用，客户端零等待 | `docs/guide/templates.md` |
| C5 | 异步作业模型 | 长操作（模板构建/重建）返回 `202 + jobID + status/phase/progress` + 构建日志端点；快照/回滚返回 operationID | `openapi.yml` templates/snapshots |
| C6 | 调度器 | 节点过滤（cpu/mem/模板本地性/并发创建数/磁盘）+ 加权打分 + 受控随机；节点级配额 `mcpu_limit/mem_limit/mvm_limit/creation_concurrent_num` | `docs/guide/cubemaster-scheduler-config.md` |
| C7 | 节点运维操作 | isolate（cordon 调度摘除）/unisolate/delete；`cubeopscli node list/isolate/unisolate/delete` | `docs/guide/node-operations.md`、`CubeOps/cmd/cubeopscli/` |
| C8 | 可观测性 | Prometheus 指标（`host_sandbox`/`guest_workload` 双作用域、metrics epoch 处理快照计数器重置、采集缓存解耦、最大 2 并发 scrape 超出 503）；`X-RequestID` 跨组件链路追踪；两层日志模型 | `docs/guide/resource-metrics.md`、`CubeOps/README.md` |
| C9 | 优雅降级信号 | 所有失败返回可重试/可行动状态 + 精确数字（配额余量、Retry-After 秒数），不悬挂不裸 500 | `lifecycle.md`、`openapi.yml` |
| C10 | 无状态控制面 + GC | 控制面水平扩展 + Redis 协调（SETNX 锁防双暂停/恢复）+ 软删除 tombstone 有界清除 janitor（advisory lock、bounded passes、dry-run、默认关） | `docs/architecture/overview.md`、`docs/guide/soft-delete-purge.md` |
| C11 | 数据面流式交互 | in-VM agent（envd，端口 49983）经代理提供 run_code 流式回调 / pty / 文件监听 / 目录监听 | `agent/README.md`、`sdk/python/cubesandbox/_pty.py` |
| C12 | API 完备性 | API Key + Bearer 双认证模式、v2 列表游标分页（`nextToken`/`x-next-token`）、Volume 框架（引用计数、双角色插件）、版本兼容矩阵 | `openapi.yml`、`docs/guide/volume-plugin.md` |
| C13 | 部署形态 | 单机一键 / Helm K8s（控制面 Deployment + 计算面 4 DaemonSet）/ Terraform 云集群；systemd 单元；升级路径与组件多版本共存文档 | `deploy/`、`docs/guide/kubernetes/` |
| C14 | 控制台 | JWT（15min access + 7d refresh）+ 登录限速 5 次/分/IP + 12 页运维控制台（概览/沙箱/模板/节点/版本/网络/可观测/API Keys） | `docs/guide/webui.md` |

### 1.2 已达成对齐的能力（勿重复实施）

Round 32–41 已按 CubeSandbox 形态补齐（见 `docs/ai-sandbox-gap-remediation-plan.md` 执行记录），**执行 agent 不得重复实现**：

| Cube 能力 | CDS 已有实现 | 轮次 |
|---|---|---|
| 文件 API（files read/write/list） | `app/services/session_files.py` + 4 端点（DEK 加密落盘、下载过输出网关） | Round 39 |
| 快照/回滚 | `app/services/session_snapshots.py` + 4 端点（tar.gz、sha256 完整性、保留驱逐） | Round 39 |
| 暂停/恢复/续期 | SUSPENDED 状态机复用 + `pre_pause_status`/`extended_seconds` + refreshes 端点 | Round 39 |
| exec 交互命令 | `POST /{id}/exec`（bash + 120s 硬超时 + T5 输出网关 + 命令哈希审计） | Round 40 |
| logs / usage | `GET /{id}/logs`（since 增量 tail）+ `GET /{id}/usage` | Round 40 |
| 会话模板 | 内置 3 模板 + JSON 扩展 + 编程注册 + 创建前校验 | Round 40 |
| Python SDK | `sdk/cds_sdk`（会话/文件/快照/exec/logs/usage + wait_for_status） | Round 40 |
| 生命周期清理循环 | `app/services/session_lifecycle.py`（`session_cleanup_loop`）| T1/Round 32 |
| 密钥 TTL 清理 / wrapped keys 持久化 | `kms_service._ttl_cleanup_loop` / `kms_recovery.restore_wrapped_keys` | Round 32 |
| 输出网关强制路径 | `output_policy.build_output_policy` + 双路径接入 + 409 门禁 | T5/Round 32 |
| 凭证托管（部分） | `app_credentials` 模型 + `/gateway/credentials` CRUD | 既有 |
| 前端会话工作台 | SessionDetail 完整工作台（文件/快照/exec 终端/审计/用量/生命周期） | Round 41 |

### 1.3 明确不对齐项及理由（执行 agent 禁止实施）

| 不对齐项 | 理由 |
|---|---|
| E2B SDK 兼容 / E2B API 形态 | CDS 是合约治理的密态数据沙箱，不是通用算力沙箱；API 形态以自身业务为准 |
| envd in-VM agent / Jupyter 内核 | CDS 执行面由宿主侧 runtime（L0 Process/bwrap/K8s）驱动，无 guest agent 需求；WS 流式（W10）在宿主侧实现等效交互 |
| OCI 镜像模板构建 / Template Store / Warehouse / AgentHub | 生态运营能力，非产品化必需；CDS 模板是 JSON 配置型（Round 40 已定形） |
| 跨节点 pause/resume（S3 快照外置） | 依赖对象存储快照外置，属 P2 之后的远期 |
| 组件多版本共存矩阵 | CubeSandbox 特有（节点组件滚动升级），CDS 单体应用无此结构 |

### 1.4 CubeSandbox 自身也缺失的能力（差异化机会，可超越基准）

- 沙箱故障自动恢复（VM crash/shim 卡死/网络分区）——Cube roadmap 未做；
- API 层请求级限流（Cube 仅节点创建并发 + 登录限速）——本方案 W3 直接做分布式限流；
- 节点排空 + 会话迁移——Cube roadmap 未做。

---

## 二、能力差距矩阵

| # | 能力 | CubeSandbox 基准 | CDS 现状（证据） | 差距 | 任务 | 优先级 |
|---|---|---|---|---|---|---|
| G1 | Prometheus 指标 | C8：双作用域指标 + epoch | **无 `/metrics` 端点**（`requirements.txt` 无 prometheus-client；monitoring 路由仅 DB 计数） | 完全缺失 | W1 | P0 |
| G2 | 链路追踪 | `X-RequestID` 跨组件 | 无 Request-ID 中间件（`app/main.py`、`app/core/security.py` 均无） | 完全缺失 | W1 | P0 |
| G3 | 错误契约 | C2：408/409/410/503+Retry-After | 全局 500 返回裸 `{"detail":"Internal server error"}`（`app/main.py:150-157`），无 request_id 无错误码；各路由 shape 不一致 | 重大 | W2 | P0 |
| G4 | 限流/配额持久化 | C6+C12 | `RateLimitMiddleware` 进程内 dict（`security.py:12-38`，60/min 硬编码）；`sandbox_manager._tenant_quotas` 内存 dict（`sandbox_manager.py:138`）重启即失、多副本失效 | 重大 | W3 | P0 |
| G5 | 管理面认证 | C14：JWT+登录限速 | `/admin/*` 6 个 HTML 路由**零认证**（`app/api/admin.py:21-43`） | 高危 | W4 | P0 |
| G6 | 迁移完整性 | C13：升级路径 | 31 张模型表仅 2 个加列迁移；生产新库 `alembic upgrade head` 建不全表（`alembic/versions/` 仅 0001/0002） | 结构性阻断 | W5 | P0 |
| G7 | 密钥/仓库卫生 | — | `cds.db`、`cds_dev.db`、`.env.prod`、`files (1).zip`、`specs.zip` 被 git 追踪；`.gitignore` 无 `*.db`/`.env.prod` | 高危 | W6 | P0 |
| G8 | CI/CD | `.github/` 流水线 | 无 `.github/`；测试仅手动跑 | 重大 | W7 | P0 |
| G9 | 列表分页 | v2 nextToken 游标分页 | 多数列表端点无界返回（sandbox-sessions/data-products/contracts/connectors） | 中 | W8 | P1 |
| G10 | 空闲自动暂停 | C1：onTimeout=pause+autoResume | 过期仅 terminate（kill 语义，`session_lifecycle.py`）；暂停/恢复是手动 API | 语义缺失 | W9 | P1 |
| G11 | 流式交互 | C11：pty/流式输出 | exec 同步阻塞 120s；无 WebSocket（Round 40 明确未做） | 重大 | W10 | P1 |
| G12 | 异步操作模型 | C5：202+jobID+progress | 快照/回滚同步执行（tar.gz 拷贝阻塞请求），大工作区会超时 | 中 | W11 | P1 |
| G13 | 数据保留 GC | C10：tombstone janitor | audit_logs/merkle_leaves/alert_records 无界增长（快照 GC 已有） | 中 | W12 | P1 |
| G14 | 出站审计 | C3：JSONL 三级审计 | network_policy 有 iptables/DNS 真实执行，但无出站访问审计轨迹 | 中 | W13 | P1 |
| G15 | 节点运维 | C7：isolate/delete | sandbox_nodes 表 + 加权随机选点（P1-6）已有；无 isolate/drain/健康运维 | 中 | W14 | P1 |
| G16 | 共享卷 | Volume 框架（引用计数） | 无（会话文件仅会话内） | 低 | W15 | P2 |
| G17 | 多副本控制面 | 无状态+Redis 协调 | 限流/配额内存态（W3 解）；操作进程内 `create_task`（W11 引入 operation 表后仍需外置队列） | 中 | W16 | P2 |
| G18 | K8s 部署完备 | Helm chart | `helm/cds`、`k8s/` 目录存在，完备度未核 | 中 | W17 | P2 |
| G19 | 故障恢复 | Cube roadmap 未做 | 无进程/容器存活探测 | 差异化 | W18 | P2 |
| G20 | 国密完成度 | — | `app/utils/crypto.py:2-3`：`SM4Cipher` 自述"Dev/test: AES-256-GCM (pycryptodomex) as SM4-GCM equivalent; Production: Tongsuo SM4-GCM via system OpenSSL"——AEAD 路径实际是 AES-GCM（`column_encryption.py:242-243`、`secure_checkpoint.py:175-176` 同模式，gmssl 无 SM4-GCM）；Tongsuo 生产路径接线未验证 | 合规 | W19 | P2 |

---

## 三、P0：产品化基线（7 项 · 约 2 周 · 13.5 人日）

### W1 · 可观测性基线（/metrics + Request-ID + 结构化日志）

**目标**：Prometheus 抓取端点、每请求唯一 ID 贯穿日志与错误响应、生产 JSON 日志可选开启。

**代码现状**：
- 无任何 metrics 端点；`requirements.txt` 无 `prometheus-client`（有 `structlog>=24.0.0` 未接线）
- 无 Request-ID 中间件（`app/main.py:146-147` 中间件仅 CORS/headers/rate-limit/size）
- `app/api/health.py` 存在（保留不动）

**实现步骤**：

1. `requirements.txt` 增加：`prometheus-client>=0.20.0`
2. 新建 `app/core/metrics.py`：
   ```python
   from prometheus_client import Counter, Histogram, Gauge, CollectorRegistry, generate_latest

   REGISTRY = CollectorRegistry(auto_describe=True)

   HTTP_REQUESTS = Counter("cds_http_requests_total", "...", ["method", "route", "status"], registry=REGISTRY)
   HTTP_LATENCY = Histogram("cds_http_request_duration_seconds", "...", ["method", "route"], registry=REGISTRY)
   SESSIONS_ACTIVE = Gauge("cds_sessions_active", "...", ["status"], registry=REGISTRY)
   SESSIONS_TOTAL = Counter("cds_sessions_total", "...", ["transition"], registry=REGISTRY)
   TASKS_ACTIVE = Gauge("cds_tasks_active", "...", ["status"], registry=REGISTRY)
   TASKS_TOTAL = Counter("cds_tasks_total", "...", ["result"], registry=REGISTRY)
   OUTPUT_INSPECTIONS = Counter("cds_output_inspections_total", "...", ["verdict"], registry=REGISTRY)
   KMS_DISTRIBUTIONS = Counter("cds_kms_key_distribution_total", "...", ["result"], registry=REGISTRY)
   RATE_LIMITED = Counter("cds_rate_limited_total", "...", ["scope"], registry=REGISTRY)
   ```
   **基数红线（低级 agent 必须遵守）**：label 禁止使用 session_id / contract_id / user_id / filename 等无界维度；route label 一律用路由模板（`/api/v1/sandbox-sessions/{session_id}`）而非实际路径。会话状态枚举（pending/running/suspended/terminated…）是有界集合，允许。
3. 新建 `app/core/telemetry.py`：
   - `RequestIDMiddleware(BaseHTTPMiddleware)`：读取请求头 `X-Request-ID`（非法则忽略），否则 `uuid4().hex[:16]`；写入 `request.state.request_id` 并在响应头回写 `X-Request-ID`
   - `MetricsMiddleware(BaseHTTPMiddleware)`：`time.perf_counter()` 计时；`route` 取 `request.scope.get("route").path`（路由匹配后可用，取不到时用 `request.url.path` 的第一段做降级标记 `unmatched`）；完成后 `HTTP_REQUESTS.labels(...).inc()` + `HTTP_LATENCY.observe(...)`。`/metrics` 自身与 `/health` 不计
4. 新建 `app/api/metrics.py`：
   ```python
   router = APIRouter()
   @router.get("/metrics")
   async def metrics_endpoint():
       if settings.METRICS_API_TOKEN and request.headers.get("Authorization") != f"Bearer {settings.METRICS_API_TOKEN}":
           raise ...  # 401
       return Response(content=generate_latest(REGISTRY), media_type="text/plain; version=0.0.4; charset=utf-8")
   ```
   `app/main.py` 注册：`app.include_router(metrics_router)`（无 `/api/v1` 前缀，与 Prometheus 惯例一致）
5. 中间件接线（`app/main.py`，在 `setup_security(...)` 之前 add，保证 Request-ID 最外层）：
   ```python
   app.add_middleware(RequestIDMiddleware)
   app.add_middleware(MetricsMiddleware)   # TESTING=1 时跳过 Metrics，RequestID 保留
   ```
6. 业务打点（最小侵入，3 处）：
   - `app/services/session_lifecycle.py` 状态迁移处：`SESSIONS_TOTAL.labels(transition=f"{old}->{new}").inc()`，同步维护 `SESSIONS_ACTIVE`（按新状态 inc、旧状态 dec）
   - `app/services/output_policy.py` / `task_pipeline.py` 输出检查结论落库处：`OUTPUT_INSPECTIONS.labels(verdict=...).inc()`
   - `kms_service.distribute_key` 返回处（success/rejected）：调用方 async 侧打 `KMS_DISTRIBUTIONS`
7. `app/core/config.py` 新增：`METRICS_ENABLED: bool = True`、`METRICS_API_TOKEN: str | None = None`、`LOG_JSON: bool = False`
8. structlog 接线（新建 `app/core/logging.py`）：`setup_logging(json_mode: bool)` —— `json_mode=True` 时 structlog JSON renderer + `contextvars` 合并 `request_id`；默认 False 保持现有 logging 行为（存量测试断言日志文本的不破坏）。`main.py` 启动时调用 `setup_logging(settings.LOG_JSON)`
9. `docker-compose.prod.yml`：`CDS_LOG_JSON: "true"`；`helm/cds/values.yaml` 注释示例

**测试**（新建 `tests/test_observability.py`）：
- `GET /metrics` → 200，body 含 `cds_http_requests_total`；配置 token 后无 Authorization → 401
- 同一请求的 `X-Request-ID` 响应头 = 请求头；不带请求头 → 响应头为 16 位 hex
- 打两个请求后 `/metrics` 中 `cds_http_requests_total` 出现且 route label 为模板路径（构造 `/api/v1/sandbox-sessions/xxx` 请求，断言 label 值含 `{session_id}` 而非 uuid）
- `METRICS_ENABLED=False` → `/metrics` 404

**验收命令**：`pytest tests/test_observability.py -v`；手动 `curl -s localhost:8000/metrics | grep cds_`
**工时**：3 人日

---

### W2 · 统一错误契约（错误码目录 + 精确状态语义）

**目标**：机器可读错误码 + request_id 贯穿 + 408/409/410/503+Retry-After 语义 + SDK 异常解析。

**代码现状**：
- 全局兜底：`app/main.py:150-157` 返回 `{"detail": "Internal server error"}`，无 request_id、无错误码、异常细节只在服务端日志
- 各路由抛 `HTTPException(detail="字符串")`，shape 不一致；无统一 Retry-After/410 使用

**实现步骤**：

1. 新建 `app/core/errors.py`：
   ```python
   class CDSError(Exception):
       code: str = "INTERNAL_ERROR"; http_status: int = 500; retry_after: int | None = None
       def __init__(self, message: str, *, detail: dict | None = None, code=None, http_status=None, retry_after=None): ...

   class SessionNotFound(CDSError):      code="SESSION_NOT_FOUND";      http_status=404
   class SessionTerminated(CDSError):     code="SESSION_TERMINATED";     http_status=410   # 终态：客户端停止重试
   class SessionStateConflict(CDSError): code="SESSION_STATE_CONFLICT"; http_status=409
   class OperationLocked(CDSError):      code="OPERATION_LOCKED";       http_status=503; retry_after=2
   class RateLimited(CDSError):          code="RATE_LIMITED";           http_status=429; retry_after=60
   class ResourceExhausted(CDSError):    code="RESOURCE_EXHAUSTED";     http_status=429
   class QuotaExceeded(ResourceExhausted): code="QUOTA_EXCEEDED"
   # …按需扩展，最终以 docs/error-codes.md 目录为准
   ```
2. 新建 `app/core/error_handlers.py` `register_exception_handlers(app)`（`main.py` 中替换现有裸 handler）：
   - `CDSError` → `{code, message, detail, request_id}`；`retry_after` 存在 → `Retry-After` 头
   - `StarletteHTTPException` → 统一包装 `{code: f"HTTP_{status}", message: detail}`（保持向后兼容字段 `detail` 同时保留）
   - `Exception` → 500 `{code:"INTERNAL_ERROR", message:"Internal server error", request_id}`；服务端 `logger.error(..., extra={"request_id": ...})` 关联
   - request_id 取 `request.state.request_id`（W1 保证），缺失现场生成
3. 状态语义对齐（改造现有 raise，**先核对现有测试期望，以测试期望为准回写本节**）：
   - 会话已 TERMINATED 后取详情/执行：`SessionTerminated`（410，客户端 SDK 据此停止重试）—— 若现状 404 且 e2e 依赖，保留 404 但错误码目录登记 `SESSION_TERMINATED_OBSERVED`，SDK 文档注明
   - 暂停态执行（Round 39 门禁）：`SessionStateConflict`（409）
   - 快照回滚进行中重复触发回滚：`OperationLocked`（503 + Retry-After: 2）
   - 限流（W3 接入后）：429 + `Retry-After` + `RATE_LIMITED`
4. 新建 `docs/error-codes.md`：全部错误码 | HTTP | 触发场景 | 客户端应对（重试/退避/终止）
5. SDK 对齐：`sdk/cds_sdk/client.py` 增加异常类 `CDSApiError(code, message, detail, request_id, retry_after)`，在响应非 2xx 时解析统一 shape 抛出（旧 shape 兼容：无 `code` 字段时 code=`HTTP_{status}`）

**测试**（新建 `tests/test_error_contract.py`）：
- 制造未知异常（临时路由或 monkeypatch service 抛 RuntimeError）→ 500，body 含 `code="INTERNAL_ERROR"` 与非空 `request_id`，响应头 `X-Request-ID` 与 body 一致
- `SessionNotFound` → 404 + 完整 shape
- `OperationLocked` → 503 + `Retry-After: 2` 头
- 410 响应 SDK 端：`CDSApiError.code == "SESSION_TERMINATED"`
- 兼容性：现有关键端点（create/execute/terminate/result）错误响应在新增字段后仍含原 `detail` 字段（回归）

**验收命令**：`pytest tests/test_error_contract.py tests/test_session_lifecycle.py -v`；`curl -s -o /dev/null -w "%{http_code}" localhost:8000/api/v1/sandbox-sessions/00000000-0000-0000-0000-000000000000` → 404/410 与目录一致
**工时**：2.5 人日

---

### W3 · Redis 分布式限流 + 租户配额持久化

**目标**：限流与配额跨副本、跨重启有效；认证用户与 IP 双维度；登录端点独立更严限速。

**代码现状**：
- `app/core/security.py:12-38`：进程内 dict 固定窗口，仅 IP，60/min 硬编码（`security.py:97`），多副本各自计数
- `app/services/sandbox_manager.py:138`：`_tenant_quotas` 内存 dict，重启丢失
- 可复用模式：`app/services/quota_manager.py` 已是 Redis Lua 原子操作（`main.py:32-33` 接线）

**实现步骤**：

1. `app/core/config.py` 新增：
   ```
   RATE_LIMIT_ENABLED: bool = True
   RATE_LIMIT_PER_MINUTE_IP: int = 60          # 匿名（IP 维度）
   RATE_LIMIT_PER_MINUTE_USER: int = 600       # 认证后（用户维度）
   RATE_LIMIT_AUTH_PER_MINUTE: int = 10        # /auth/login 专用（对照 Cube 登录 5/min/IP，放宽至 10）
   ```
2. `security.py` 改造 `RateLimitMiddleware`：
   - Redis 固定窗口 Lua：`local n = redis.call('INCR', KEYS[1]); if n == 1 then redis.call('EXPIRE', KEYS[1], 60) end; return n`，key = `cds:rl:{scope}:{identifier}:{floor(now/60)}`
   - scope 判定：`request.url.path` 以 `/api/v1/auth/login` 结尾 → `auth`（identifier=IP）；有 `Authorization: Bearer` → `user`（identifier=JWT payload sub——**注意**：中间件层无 DB 依赖，仅 base64 解 payload 取 sub，不验签，限流标识被伪造只影响伪造者自己的桶，可接受）；否则 `ip`
   - 超限 → 429 + `Retry-After`（窗口剩余秒）+ W2 shape `{code:"RATE_LIMITED"}` + `RATE_LIMITED.labels(scope).inc()`
   - **降级语义**：Redis 异常 → WARNING 日志 + 回退现有本地 dict 逻辑（保留为 `_local_fallback`），**fail-open**（限流组件不可用不应拒绝全量流量），文档 `docs/error-codes.md` 注明该决策
   - 复用 `app/core/redis.py` 的连接单例
3. 租户配额持久化（`sandbox_manager.py`）：
   - `_tenant_quotas` 读写改为 Redis hash `cds:tenant_quota:{tenant_id}`（字段 max_sessions/max_cpu_cores/max_memory_mb/当前占用），用 `quota_manager` 同款 Lua 保证原子
   - 新增 `async def rebuild_tenant_quotas(db)`：扫描 DB 中 ACTIVE 态会话重算当前占用（Redis 数据丢失后的恢复路径）；`main.py` lifespan 启动时调用一次（在 session cleanup loop 之前）
   - `check_tenant_quota` / `acquire` / `release` 签名不变（调用方零改动）
4. `.env.example` 补充四个 RATE_LIMIT_* 键

**测试**（新建 `tests/test_rate_limit_distributed.py`、`tests/test_tenant_quota_persistence.py`；dev 依赖 `fakeredis>=2.21`，`tests/conftest.py` 注入 fake redis 到 `app.core.redis.get_redis`）：
- 同 IP 连续 61 次普通请求（认证关闭 scope=ip）→ 第 61 次 429 + Retry-After>0 + code=RATE_LIMITED
- 两个不同 IP 计数独立
- 登录端点第 11 次 → 429（auth 专用桶）
- Redis 断连（fakeredis 断开）→ 请求仍成功（fail-open）+ 出现 WARNING
- 配额：创建会话后构造**新的** sandbox_manager 实例（模拟重启）→ 从 Redis 读到相同占用；清空 Redis → `rebuild_tenant_quotas` 后占用恢复

**验收命令**：`pytest tests/test_rate_limit_distributed.py tests/test_tenant_quota_persistence.py tests/test_security_config_matrix.py -v`
**工时**：3 人日

---

### W4 · admin 路由认证治理

**目标**：消除未认证管理页面暴露面。

**代码现状**：`app/api/admin.py:21-43` 六个路由（identities/certificates/connectors/alerts/blockchain）零认证渲染 Jinja 模板；`app/main.py:194` 无条件注册。React 前端（cds-frontend）才是正式管理界面，Jinja 页面为 FE-1~FE-5 遗留。

**实现步骤**：

1. `app/core/config.py`：`ADMIN_PAGES_ENABLED: bool = False`（安全默认）
2. `app/main.py:194` 改为：
   ```python
   if settings.ADMIN_PAGES_ENABLED:
       logger.warning("[SECURITY] Admin legacy pages enabled at /admin/* — NOT for production use")
       app.include_router(admin.router)
   ```
3. `.env.example`、`docker-compose.yml` 注释说明（默认不开）；`docker-compose.prod.yml` 与 `helm/cds/values.yaml` **不得**开启（若当前开启则关闭）
4. `validate_security_config()`（`app/core/config.py:209-254`）追加：生产模式下 `ADMIN_PAGES_ENABLED=True` → RAISE

**测试**（新建 `tests/test_admin_auth.py`）：
- 默认（未开启）`GET /admin/identities` → 404
- monkeypatch `ADMIN_PAGES_ENABLED=True` → 200（本地调试路径可用）
- `validate_security_config` 生产 + 开启 → 包含 RAISE 级 issue

**验收命令**：`pytest tests/test_admin_auth.py -v`
**工时**：0.5 人日

---

### W5 · Alembic 基线迁移（生产部署阻断项）

**目标**：空库 `alembic upgrade head` 能建出全部 31 张表；CI 漂移检测。

**代码现状**：`alembic/versions/` 仅 `0001_p0_security_hardening.py`、`0002_*.py`（additive 加列）；表结构由 `Base.metadata.create_all` 创建（`app/main.py:43-45`，仅 dev/test/sqlite）。**生产 PG 新库跑迁移只会得到两张残缺表 → 生产部署直接失败。**

**实现步骤**：

1. 生成基线：对空 SQLite 临时库执行
   `DATABASE_URL=sqlite:///./baseline_tmp.db alembic revision --autogenerate -m "0000_baseline_all_tables"`
   - 编号确保在 0001 之前（ downgrade 到零可清库）；人工核对：31 张表、索引、可空性；剔除 autogenerate 对 JSON 列类型的 SQLite 特有噪音
2. 结构对等验证脚本 `scripts/verify_schema_parity.py`：
   - 库 A：`alembic upgrade head`（从零）；库 B：`create_all`
   - 用 SQLAlchemy inspector 比对表名集合 + 每表列名集合，输出 diff，exit code 非 0 表示漂移
3. 存量 dev 库（`cds.db` 等）升级路径：`alembic stamp head`（列已由 create_all 建好，不重放）；写入 `docs/` 迁移手册一节
4. CI（W7）跑 `alembic upgrade head` + `alembic check`（模型改动未生成迁移时 fail）
5. `app/main.py` 的 create_all 分支保留（dev/test 快速启动），但 `logger.info` 提示生产走 Alembic（现有 L47 已有，保留）

**测试**（新建 `tests/test_migrations.py`，`@pytest.mark.slow`）：
- tmp SQLite 路径 → 子进程 `alembic upgrade head` → inspector 断言 31 表全部存在
- `python scripts/verify_schema_parity.py` 在测试内以子进程跑一次 → exit 0

**验收命令**：`alembic upgrade head`（空库）；`python scripts/verify_schema_parity.py`
**工时**：2 人日

---

### W6 · Git 卫生与密钥治理

**目标**：仓库不含数据库文件/生产密钥/临时产物；增量止血（历史清洗单列决策）。

**代码现状**：`git ls-files` 含 `cds.db`、`cds_dev.db`、`.env.prod`、`files (1).zip`、`specs.zip`；`.gitignore` 覆盖 `.env`/`*.pem`/`*.key` 但**不含** `*.db`/`.env.prod`/`*.zip`。

**实现步骤**：

1. `git rm --cached cds.db cds_dev.db ".env.prod" "files (1).zip" specs.zip`（保留工作区文件；zip 若确认无用则直接删除工作区文件——执行前与用户确认一次）
2. `.gitignore` 追加：
   ```
   *.db
   .env.prod
   .env.*
   !.env.example
   !.env.prod.example
   *.zip
   ```
3. 新建 `.env.prod.example`（脱敏模板：全部键 + 占位值），`.env.prod` 真实文件仅存本地/密管
4. **密钥轮换清单（必做，因为历史已泄露）**：`.env.prod` 中出现的 DB 密码、JWT SECRET、Vault token 等，在目标环境全部轮换；轮换完成前视为已泄露
5. 历史清洗（`git filter-repo` / BFG）涉及 force-push 与协作者影响 → 列为**独立决策项**，本任务不执行，仅在 PR 描述与本文档登记
6. gitleaks 基线扫描：`gitleaks detect --source . -v`，新发现项处理或加入 `.gitleaksignore`（附理由）

**测试**：无单测；验收为命令输出：
- `git ls-files | grep -E "\.(db|zip)$"` → 空
- `git ls-files | grep -E "^\.env\.prod$"` → 空
- `gitleaks detect --no-git -v`（工作区）→ 无 CRITICAL/HIGH 未处置项

**工时**：0.5 人日（不含历史清洗与轮换执行）

---

### W7 · CI 流水线（GitHub Actions）

**目标**：push/PR 自动跑编译 + 迁移 + 测试（稳定子集起步，逐步收紧）+ 前端构建 + 密钥扫描。

**代码现状**：无 `.github/`；测试仅在本地/远程手动跑；Windows 有 2 个 `import resource` 收集错误（Linux CI 无此问题）。

**实现步骤**：

1. 新建 `.github/workflows/ci.yml`，触发 `push`（main）与 `pull_request`：
   - **job backend**（ubuntu-latest，Python 3.11，pip cache）：
     1. `pip install -r requirements.txt`
     2. `python -m compileall -q app`（项目既有惯例）
     3. 迁移验证：`DATABASE_URL=sqlite:///./ci.db alembic upgrade head` + `alembic check`
     4. 测试（**分层门禁**，务实 ramp 策略）：
        - 第一阶段（合并必需绿）：本方案全部新增测试文件 + 稳定核心集（`tests/test_session_lifecycle.py tests/test_error_contract.py tests/test_observability.py ...`，以实际通过为准圈定，初始约 15–25 个文件）
        - 第二阶段（ informational，不阻断）：全量 `pytest tests/ -q --ignore=tests/test_e2e_full_lifecycle.py --junitxml=report.xml`；已知环境性失败（bwrap/cgroup/TEE 设备/集群 e2e，约 75 项，见 `docs/ai-sandbox-gap-remediation-plan.md` 回归结论）记录到 `ci/known-failures.md`，以"**失败数不增长**"为软门禁（人工比对）
   - **job frontend**（node 20）：`cd cds-frontend && npm ci && npm run build`
   - **job secrets**：`gitleaks/gitleaks-action@v2`
2. `pyproject.toml` `[tool.pytest.ini_options]` 加 `timeout = 600` 级别的会话保护（若未设；防止挂死占满 CI）
3. README（或 docs/runbook）补一段徽章与本地复现命令

**验收**：CI 首次绿 run（backend/frontend/secrets 三 job）；`ci/known-failures.md` 记录基线失败数
**工时**：2 人日

---

## 四、P1：平台语义对齐（7 项 · 约 3–4 周 · 20 人日）

### W8 · 列表端点游标分页

**目标**：核心列表端点 keyset 分页，向后兼容，SDK 提供迭代器。

**代码现状**：`GET /sandbox-sessions`、`/data-products`、`/contracts`、`/connectors` 无界返回（monitoring/alerts、audit 有 skip/limit）。

**实现步骤**：

1. 新建 `app/core/pagination.py`：
   ```python
   async def paginate_keyset(db, stmt, *, cursor: str | None, limit: int, order_col, id_col) -> tuple[list, str | None]:
       # cursor = base64(f"{last_created_at_iso}|{last_id}")；WHERE (created_at, id) < (:ts, :id) ORDER BY created_at DESC, id DESC LIMIT limit+1
       # 取 limit+1 判断 has_more，返回 (items, next_cursor or None)
   ```
   （SQLite/PG 均支持元组比较的行值写法需验证；不稳妥则拆成 `created_at < :ts OR (created_at = :ts AND id < :id)`）
2. 端点改造（4 个：sandbox_sessions / data_products / contracts / connectors 的 list）：
   - 请求参数新增 `cursor: str | None = None`、`limit: int = Query(100, le=500)`
   - **响应 shape 向后兼容**：响应体保持现有 list 结构不变；分页信息放**响应头** `X-Next-Cursor`（无更多页时省略）+ `X-Total-Omitted`（可选）
   - 未传 cursor 时返回第一页（受 limit 约束）——注意：若现有调用方依赖全量，行为变更点写入 CHANGELOG
3. SDK：`client.list_sessions(cursor=None, limit=100)` + `iter_sessions()` 生成器（自动翻页）
4. 前端（可选，独立小任务）：SessionList 滚动加载

**测试**（新建 `tests/test_pagination.py`）：
- 造 250 条 → limit=100 三页遍历：无重复、无遗漏、第三页 `X-Next-Cursor` 缺省
- limit 边界（le=500 → 501 拒 422）
- 删除中间页数据后游标继续 → 不崩溃（容忍空洞，keyset 天然安全）

**验收命令**：`pytest tests/test_pagination.py -v`
**工时**：2 人日

---

### W9 · 空闲自动暂停（onTimeout: kill | pause 生命周期策略）

**目标**：会话可声明空闲到期行为为「暂停保留状态」而非「终止」；支持 autoResume（请求触达唤醒）——但唤醒必须重验合约（CDS 特有安全语义，与 Cube 不同）。

**代码现状**：
- 过期清理统一 terminate（`session_lifecycle.cleanup_expired_sessions`）
- 暂停/恢复 API 已有（Round 39：SUSPENDED + `pre_pause_status`）
- execute 门禁拦截 SUSPENDED 态

**实现步骤**：

1. 迁移（Alembic）：`sandbox_sessions` 加 `idle_policy: String(16) | None`（`kill`|`pause`，NULL=默认）、`auto_resume: Boolean default False`
2. `app/core/config.py`：`SESSION_DEFAULT_IDLE_POLICY: str = "kill"`（保守默认）
3. `app/services/session_lifecycle.py` `cleanup_expired_sessions` 分支：
   ```python
   policy = session.idle_policy or settings.SESSION_DEFAULT_IDLE_POLICY
   if policy == "pause" and session.status != SUSPENDED:
       ok = await pause_session(session, db, reason="session_idle_autopause")   # 复用 Round 39 暂停原语
   else:
       ok = await terminate_session(session, db, reason="session_expired")     # 现有路径不动
   ```
   审计 action=`session.idle_autopause`
4. **auto_resume 接线（安全链，顺序不可变）**：`execute` / `exec` / 文件读写 / 快照 端点入口（`sandbox_sessions.py` 各 handler 前）：
   ```python
   if session.status == SUSPENDED and session.auto_resume:
       contract = await _load_contract(session.contract_id)          # ① 合约存在且 ACTIVE
       if not contract_active(contract): raise SessionTerminated(...)  # 合约已终止 → 拒绝自动唤醒（提示手动处理）
       await resume_session(session, db, reason="auto_resume")      # ② 恢复（内部含配额校验）
       # ③ 恢复后新空闲计时（extended 语义，Round 39 已有机制）
   ```
   审计 action=`session.auto_resume`
5. 创建会话请求 schema 增加 `idle_policy` / `auto_resume` 字段（校验枚举）；dev_sandbox 同步
6. SDK：`create_session(..., idle_policy=..., auto_resume=...)` 透传

**测试**（新建 `tests/test_idle_autopause.py`）：
- `idle_policy="pause"` 过期 → 状态 SUSPENDED（非 TERMINATED）+ 审计 idle_autopause
- `auto_resume=True` + SUSPENDED + 合约 ACTIVE → execute 成功且状态回 RUNNING
- `auto_resume=True` 但合约已 TERMINATED → 拒绝（410/409，按 W2 目录）+ 不恢复
- 默认策略过期 → 仍 terminate（回归不破坏）

**验收命令**：`pytest tests/test_idle_autopause.py tests/test_session_lifecycle.py -v`
**工时**：3 人日

---

### W10 · WebSocket 流式执行通道（exec stream）

**目标**：交互式流式命令执行：实时 stdout/stderr 帧、stdin 注入、PII 逐行审查（fail-closed）、前端终端组件。

**代码现状**：exec 为同步 POST + 120s 硬超时（Round 40）；无任何 WS 端点；`uvicorn[standard]` 已含 websockets。

**实现步骤**：

1. 新建 `app/api/session_stream.py`（router：`/api/v1/sandbox-sessions`）：
   - `POST /api/v1/auth/ws-ticket`（JWT 认证）：签发 30s 一次性 ticket（JWT，claims: user_id, jti 入 Redis `cds:ws-ticket:{jti}` 30s TTL，用后删除）
   - `@router.websocket("/api/v1/sandbox-sessions/{session_id}/exec/stream")`：
     - 认证：`?ticket=` 校验（签名 + jti 未用过 + 未过期）；失败 close(code=4401)
     - 会话校验：归属 + 状态（SUSPENDED 且 auto_resume → 先走 W9 唤醒链；否则 close(4409)）
2. 协议（JSON 帧，双工）：
   - client→server：`{"type":"start","command":"...","timeout":120}`、`{"type":"stdin","data":"..."}`、`{"type":"ping"}`
   - server→client：`{"type":"stdout","data":"..."}`、`{"type":"stderr","data":"..."}`、`{"type":"blocked","code":"PII_CRITICAL"}`（流终止）、`{"type":"exit","code":0,"duration_ms":...}`、`{"type":"error","code":"..."}`、`{"type":"pong"}`
3. **输出网关约束（关键安全设计，不可省略）**：
   - 行缓冲执行器：进程输出按 `\n` 切分；每一完整行经 `enforce_text_output_policy`（复用 T5/W2 既有函数——`app/services/output_policy.py`）逐行审查
   - 行内命中 critical → 发送 `blocked` 帧后**立即 kill 进程**并关闭连接（与 T5 fail-closed 一致）；命中 PII 非阻断 → 行改写 `[REDACTED:*]` 后放行
   - 进程退出时 flush 半行（同样过审查）
   - `{"type":"exit"}` 帧仅携带退出码与耗时，不携带原始输出
   - 命令哈希审计：复用 Round 40 exec 的命令审计（action=`session.exec_stream`）
4. 心跳与断连：服务端 30s 无 ping → 关连接但**进程继续**（输出进日志文件，可经 `/logs` 补查）；同会话并发流上限 `MAX_STREAMS_PER_SESSION: int = 3`（超限 close(4409)）
5. 超时：`start.timeout` 上限沿用 120s（`EXEC_TIMEOUT_SECONDS`），到期 kill + `exit` 帧
6. 前端（委托 visual-engineering，独立验收）：SessionDetail 终端面板（xterm.js + WS 重连 + ticket 获取）

**测试**（新建 `tests/test_exec_stream.py`，FastAPI `TestClient.websocket_connect`）：
- echo 命令 → 收到 stdout 帧 + exit(code=0)
- 输出含身份证号 → stdout 帧中为 `[REDACTED:...]` 改写
- 输出含 critical PII（按现有 inspect 规则构造）→ blocked 帧 + 连接关闭 + 进程终止（审计落库）
- ticket 复用第二次 → close(4401)
- stdin 注入（`cat` 命令）→ 回显
- SUSPENDED 会话（无 auto_resume）→ close(4409)

**验收命令**：`pytest tests/test_exec_stream.py -v`；前端手工验收（Playwright 截图会话工作台终端交互）
**工时**：后端 4 人日 + 前端 2 人日

---

### W11 · 异步操作模型（202 + operation_id + 轮询）

**目标**：快照/回滚等长操作可选异步化：`?async=true` → `202 {operation_id}`，进度可查。

**代码现状**：快照创建/回滚同步执行（tar.gz 拷贝阻塞 HTTP 请求，大工作区风险）；模板种子 best-effort。

**实现步骤**：

1. 迁移：新表 `session_operations`（`op_id UUID PK`、`session_id`、`op_type: String(32)`（snapshot|rollback|template_seed）、`status: String(16)`（pending|running|succeeded|failed）、`progress: Float default 0.0`、`error: Text | None`、`result_ref: JSON | None`（如 snapshot_id）、`created_at/updated_at`，索引 `(session_id, created_at)`）+ 模型 `app/models/session_operation.py`（**记得加入 `app/models/__init__.py` 及 main.py import 保障 create_all**——参照 `main.py:17` 的现有模式）
2. 新建 `app/services/session_operations.py`：
   ```python
   async def submit_operation(db, session_id: str, op_type: str, params: dict, *, run_async: bool) -> SessionOperation
   # 同步路径：await _execute(op) 完成后返回最终态
   # 异步路径：状态置 pending 后 asyncio.create_task(_execute_wrapper(op))，立即返回
   async def _execute(op) -> None:  # 内部 dispatch 到现有快照/回滚 service 函数，更新 progress/status/result_ref
   async def get_operation(db, op_id) -> SessionOperation
   ```
   - 执行器内单轮异常 → status=failed + error 填充（不抛出，不吞——logger.error + 审计 `operation.failed`）
   - progress：快照按已归档文件数/总数（session_snapshots 内部逻辑加回调参数，缺省无回调）
3. API 改造（`sandbox_sessions.py`）：
   - `POST /{id}/snapshots` 与 `POST /{id}/snapshots/{sid}/rollback`：新增查询参数 `async_mode: bool = Query(False, alias="async")`
     - `async=false`（默认）：行为与现状完全一致（同步返回快照信息）——内部也创建 operation 记录（status 立即终态），便于审计统一
     - `async=true`：返回 `202 {"operation_id": "...", "status": "pending"}`（W2 shape）
   - 新端点 `GET /api/v1/sandbox-sessions/operations/{op_id}`：归属校验 + 返回 `{operation_id, op_type, status, progress, error, result_ref, created_at, updated_at}`
   - 重复回滚互斥：进行中 rollback 的会话再触发 → `OperationLocked`（503 + Retry-After: 2，W2）
4. SDK：`create_snapshot(async_mode=True)` + `wait_for_operation(op_id, timeout)` 轮询助手

**测试**（新建 `tests/test_async_operations.py`）：
- `?async=true` 快照 → 202 + operation_id → 轮询至 succeeded → result_ref.snapshot_id 存在且快照列表可见
- 制造失败（monkeypatch 快照函数抛错）→ operation status=failed + error 含异常信息
- 进行中 rollback 重复触发 → 503 + Retry-After
- 同步路径回归：行为不变 + operation 记录存在且为终态

**验收命令**：`pytest tests/test_async_operations.py tests/test_snapshots*.py -v`
**工时**：3 人日

---

### W12 · 数据保留 GC Janitor

**目标**：审计/告警等增长表的有界、可配置、可审计的保留清理（合规保守默认）。

**代码现状**：audit_logs / merkle_leaves / alert_records / blockchain_anchors 无界增长；快照 GC 已有（Round 40）。

**实现步骤**：

1. `app/core/config.py`：
   ```
   RETENTION_JANITOR_ENABLED: bool = True
   AUDIT_LOG_RETENTION_DAYS: int = 0        # 0 = 永不清理（合规保守：审计日志默认全留）
   ALERT_RETENTION_DAYS: int = 180
   MERKLE_LEAF_RETENTION_DAYS: int = 0      # 0 = 永不清理（存证证明依赖，默认全留）
   RETENTION_JANITOR_DRY_RUN: bool = True   # 默认演练！
   RETENTION_JANITOR_BATCH: int = 1000
   ```
2. 新建 `app/services/retention_janitor.py`：
   - `async def purge_cycle(db) -> dict[str, int]`：逐表 `DELETE WHERE id IN (SELECT id ... WHERE created_at < :cutoff LIMIT :batch)` 循环至无行（SQLite 无 DELETE LIMIT → 用子查询）；每表独立 try/except（单表失败不影响他表）且**必须 logger.warning**（不许 `except: pass`）
   - PG 环境 `pg_try_advisory_lock(hashtext('cds_retention_janitor'))` 防多副本并发；SQLite 跳过
   - `dry_run=True` → 只 COUNT 不 DELETE，结果记日志
   - **删除即审计**：每轮结束 `audit_service.log(action="retention.purge", detail={表名: 删除数, dry_run, cutoff})` —— 审计自身不可被 GC 清除（审计日志默认 0=永留，与此自洽）
3. 挂载：`session_cleanup_loop` 每轮末尾调用（同频 300s）；或独立 loop（二选一，推荐并入现有 loop 减少 task 数）
4. 运维文档：`docs/retention.md`（如何从 dry_run 切实删、合规审批流程建议）

**测试**（新建 `tests/test_retention_janitor.py`）：
- 造超期 alert → 关 dry_run 跑一轮 → 删除且审计含 `retention.purge`
- 保留期内记录不动；retention=0 的表不动
- dry_run → 计数日志正确但数据仍在
- 删除分批：batch=2 造 5 条 → 两轮后清空

**验收命令**：`pytest tests/test_retention_janitor.py -v`
**工时**：2 人日

---

### W13 · 出站访问审计（egress JSONL）

**目标**：网络策略判定与（若启用）凭证注入全部留痕 JSONL，秘密脱敏，可查询。

**代码现状**：`network_policy.py` 有真实 iptables/DNS 代理执行（`NetworkPolicyConfig` 支持 deny_all/allowlist 模式）；`app_credentials` + `/gateway/credentials` 凭证托管存在；**出站判定无审计轨迹**（文件内存在若干静默异常处理点，如 `:44-45` 的 `except OSError: pass`——该处为 resolv.conf 读取失败的合法回退，保留但加 debug 日志）。

**实现步骤**：

1. 新建 `app/services/egress_audit.py`：
   - `async def log_egress_event(*, session_id, event_type: str, method, host, path, verdict: str, rule_id: str | None, status_code: int | None, credential_used: str | None) -> None`
     - 追加 JSONL 到 `settings.EGRESS_AUDIT_LOG_PATH`（默认 `./logs/cds-egress/access.jsonl`，目录自动创建）
     - **异步队列写**：`asyncio.Queue(maxsize=1000)` + 单写协程，队满丢弃 + `RATE_LIMITED` 风格计数（不阻塞数据面）；shutdown flush
     - 脱敏规则：`credential_used` 只记凭据 ID 不记值；query string 中的 `token/key/secret` 参数值替换 `***`
   - 事件类型对齐 Cube 三类：`access`（判定通过）、`security_event`（deny/default-deny 命中）、`tls_handshake`（如适用）
2. `network_policy_engine` 打点：策略 apply/allow/deny/remove 的判定分支各调用 `log_egress_event(verdict=...)`
3. 凭证注入点（gateway credentials 使用处）：注入成功/失败 → `access`/`security_event` 事件，`credential_used=<cred_id>`
4. 查询端点（ADMIN/OPERATOR）：`GET /api/v1/network-policies/{session_id}/egress-audit?limit=&since=` —— tail JSONL + since 时间过滤
5. 顺带治理：本任务触碰的文件（`network_policy.py` 等）内所有**无日志的静默异常处理**统一加 `logger.warning`（含真实降级语义的保留行为、只补日志，如 `:44-45` resolv.conf 回退处加 debug 日志）；未触碰文件留给后续专项

**测试**（新建 `tests/test_egress_audit.py`）：
- 触发 allow/deny 判定 → JSONL 各出现一行，字段完整（event_type/host/verdict/rule_id）
- 含 secret 的 query string → 日志中为 `***`
- 队列满 → 不抛错（drop 计数增长）
- 查询端点：造 3 行 → limit=2 返回 2 行按时间倒序

**验收命令**：`pytest tests/test_egress_audit.py -v`
**工时**：2 人日

---

### W14 · 节点运维操作（isolate / 健康）

**目标**：节点可摘除调度、可观测健康；为运维窗口提供操作面（不做自动故障恢复——W18 差异化）。

**代码现状**：`sandbox_nodes` 表 + 加权随机选点（P1-6 已完成 Top-3 weighted-random jitter）；无 isolate/健康运维端点（先 grep 核对 `app/api/` 是否已有 sandbox-nodes 路由，有则在其上扩展）。

**实现步骤**：

1. 迁移：`sandbox_nodes` 加 `scheduling_disabled: Boolean default False`、`last_heartbeat_at: DateTime | None`、`health_state: String(16) default "unknown"`（unknown|healthy|stale|isolated）
2. 节点选择器（P1-6 所在 service）：过滤 `scheduling_disabled == False`；**全禁时抛明确错误** `ResourceExhausted(code="NO_NODE_AVAILABLE", detail={"disabled_count": n})`（不静默随机）
3. API（新建 `app/api/sandbox_nodes.py`，prefix `/api/v1/sandbox-nodes`，ADMIN）：
   - `GET /`：列表（id/hostname/capacity/在跑会话数/health_state/scheduling_disabled/last_heartbeat_at）
   - `POST /{node_id}/isolate` / `POST /{node_id}/unisolate`：切换 `scheduling_disabled` + health_state=isolated/healthy + 审计 `node.isolate`/`node.unisolate`
   - 孤儿检测**告警**（不自动恢复）：`session_cleanup_loop` 每轮顺带查 —— 会话 RUNNING 但其 node `last_heartbeat_at` 超 `NODE_STALE_SECONDS: int = 300` → node.health_state=stale + `alert_records` 落一条告警（复用现有告警模型）+ 审计 `node.stale_detected`
4. `NODE_STALE_SECONDS` 入 config；心跳来源：节点每次 provision/execute 时更新（若无心跳通道则仅 isolate 可用，health 检测退化为只读——以代码现状为准回写）

**测试**（新建 `tests/test_node_operations.py`）：
- isolate 后创建会话 → 不落在该节点（两节点 fixture）；全 isolate → `NO_NODE_AVAILABLE`
- stale 节点 → 告警落库 + health_state=stale
- isolate/unisolate 审计存在

**验收命令**：`pytest tests/test_node_operations.py -v`
**工时**：2 人日

---

## 五、P2：路线图（立项另估）

| # | 方向 | 路线 | 前置 |
|---|---|---|---|
| W15 | Volume 共享卷框架 | 跨会话共享**加密**数据卷：`volumes` 表 + 引用计数（attach/detach 原子）+ 删除保护（refcount≠0 → 409，对齐 Cube `volume-plugin.md` 模型）+ DEK 挂卷时按合约分发 | W11 |
| W16 | 多副本控制面 | W11 的进程内 `create_task` 外置为 Redis Stream/Celery 队列；暂停/恢复用 Redis SETNX 锁防双执行（对齐 Cube lifecycle-manager 模式）；W3 已完成限流/配额 Redis 化 | W3/W11 |
| W17 | K8s/Helm 完备化 | 核对 `helm/cds` vs `docker-compose.prod.yml` 差异：探针（liveness/readiness= alembic 后 /health）、资源配额、ConfigMap 全量键、升级路径文档（对齐 Cube `kubernetes/upgrade.md` 结构） | W5 |
| W18 | 沙箱故障恢复（差异化） | 进程/容器存活探测（exec 健康探针）→ 可配置恢复策略（restart/rebuild/notify-only）——Cube roadmap 未做，CDS 领先机会 | W14 |
| W19 | 国密完成度审计（SM4-GCM AEAD 路径） | **现状（已复核）**：确定性 SM4-SIV/CBC 是真 gmssl SM4（`column_encryption.py`、`deterministic_sm4.py`）；但所有 **AEAD** 路径（`app/utils/crypto.py:2-3` 的 `SM4Cipher`、`column_encryption.py:242-243`、`secure_checkpoint.py:175-176`、`kms_service.py`/`storage_service.py`/`policy_compiler.py` 均消费 `SM4Cipher`）因 gmssl 无 SM4-GCM 而实际用 AES-GCM，注释声称"Production: Tongsuo SM4-GCM via system OpenSSL"但该接线未验证。任务：① 清单化所有 AEAD 调用点；② 实现 Tongsuo（铜锁 CLI/子进程或 OpenSSL 3 SM4-GCM）真实路径 + 性能基准对比；③ 未完成前在 `validate_security_config` 与合规报告中**如实披露** "SM4-AEAD 实际算法=AES-256-GCM（Tongsuo 未接线）"，禁止静默 | 无 |

---

## 六、横切面

### 6.1 配置开关矩阵（本方案新增 Settings 汇总）

| 字段 | 默认 | dev/test | 生产 | 任务 |
|---|---|---|---|---|
| `METRICS_ENABLED` / `METRICS_API_TOKEN` | True / None | 同 | token 必配（内网可 None） | W1 |
| `LOG_JSON` | False | false | **true** | W1 |
| `RATE_LIMIT_ENABLED` / `RATE_LIMIT_PER_MINUTE_IP` / `_USER` / `_AUTH` | True / 60 / 600 / 10 | 同 | 按容量 | W3 |
| `ADMIN_PAGES_ENABLED` | **False** | 可 true | **false（RAISE 校验）** | W4 |
| `SESSION_DEFAULT_IDLE_POLICY` | kill | kill | kill | W9 |
| `EXEC_STREAM_MAX_CONCURRENT` | 3 | 同 | 同 | W10 |
| `RETENTION_JANITOR_ENABLED` / `_DRY_RUN` / 三表 `*_RETENTION_DAYS` | True / **True** / 0,180,0 | 同 | dry_run 审批后关 | W12 |
| `EGRESS_AUDIT_LOG_PATH` | ./logs/cds-egress/access.jsonl | 同 | 持久卷 | W13 |
| `NODE_STALE_SECONDS` | 300 | 同 | 同 | W14 |

原则：延续项目既有纪律——**新开关安全默认，宽松只在显式配置处**；`.env.example` / `docker-compose.yml` / `docker-compose.prod.yml` / `helm/cds/values.yaml` 四处同步（CDS_ 前缀）。

### 6.2 Alembic 迁移清单（依赖顺序）

| 迁移 | 内容 | 任务 |
|---|---|---|
| m0 | **基线**：31 张表全量建表（置于链首） | W5 |
| m1 | `sandbox_sessions` + `idle_policy` + `auto_resume` | W9 |
| m2 | 新表 `session_operations` | W11 |
| m3 | `sandbox_nodes` + `scheduling_disabled` + `last_heartbeat_at` + `health_state` | W14 |

命令：`alembic revision --autogenerate -m "..."` → 人工核对（**禁止直接信任 autogenerate**）→ `alembic upgrade head` → `python scripts/verify_schema_parity.py`。全部为 additive 可空/带默认列，存量数据零迁移风险。

### 6.3 测试与验证协议（每任务 DoD 模板）

每个任务合入前必须全部满足（执行 agent 逐项自检后报告）：

1. **新测试全绿**：`pytest tests/test_<本任务>.py -v`，覆盖方案列出的每一条用例
2. **受影响回归绿**：方案"验收命令"列出的既有测试文件运行，通过数不低于改动前基线
3. **编译绿**：`python -m compileall -q app tests`
4. **配置四同步**：`.env.example` / `docker-compose.yml` / `docker-compose.prod.yml` / `helm/cds/values.yaml` 中出现新键
5. **诚实性**：任何降级/模拟路径有 `logger.warning` + （涉及安全姿态时）`validate_security_config` 覆盖
6. **API 行为变更**：如改变响应 shape/默认值，本文件"执行记录"章节追加一行说明
7. **禁项自检**：未引入 `as any` 等价物（Python 侧：无裸 `except: pass` 新增）、未删除既有测试、未放宽任何 W2/T5 安全门禁

### 6.4 发布顺序与依赖

```
W4 (admin) ─┐
W6 (git)  ─┤ 速赢，先行
W1 (观测) ─┴→ W2 (错误契约，依赖 W1 的 request_id) → W3 (限流，产出 429 接 W2 shape) → 【P0 收口：全量回归】
W5 (基线迁移) ─→ W7 (CI，消费 W5 的迁移验证)
── P1 ──
W8 (分页) ∥ W9 (自动暂停) ∥ W12 (GC)  → 相互独立
W11 (异步操作) → W10 (WS 流，复用 W9 唤醒链 + W2 错误帧)
W13 (egress 审计) ∥ W14 (节点运维)
```

每项独立 PR，revert 边界干净；W2 是 W3/W10/W11 错误语义的公共依赖，必须先行合入。

### 6.5 里程碑与工时汇总

| 里程碑 | 内容 | 时点 |
|---|---|---|
| M1 | W4+W6+W2+W1（可观测 + 诚实错误 + 无暴露面） | 第 1 周末 |
| M2 | W3+W5+W7（P0 清零，CI 上线） | 第 2 周末 |
| M3 | W8+W9+W11+W12（平台语义） | 第 4 周末 |
| M4 | W10+W13+W14（交互与运维对齐） | 第 6 周末 |
| M5+ | P2 按立项启动 | 第 2 月起 |

**工时**：P0 = 13.5 人日；P1 = 20 人日（含 W10 前端 2）；P2 另估。

### 6.6 风险表

| 风险 | 影响 | 缓解 |
|---|---|---|
| 全量测试存在 ~75 既有环境性失败（Linux 基线，见前序方案回归结论） | CI 无法一步全绿 | W7 分层门禁：新增测试必须绿 + informational 全量 + 失败数不增长软门禁 |
| W10 流式逐行过输出网关的性能开销 | 大输出延迟 | 行缓冲批量审查；指标（W1 `cds_output_inspections`）观测；必要时抽样模式（明确标注降级） |
| W5 基线迁移 autogenerate 的 SQLite/PG 类型差异 | 生产 PG 建表偏差 | `verify_schema_parity.py` 双库验证（SQLite 本地 + CI；PG 由 W17 K8s 环境验收） |
| W9 auto_resume 与合约治理交互（暂停期间合约终止） | 已终止合约数据被唤醒访问 | 唤醒链强制重验合约 ACTIVE（步骤 4①），测试显式覆盖 |
| W3 限流 fail-open 决策 | Redis 故障时限流失效 | WARNING + `RATE_LIMITED` 指标可见；文档登记；auth 端点可考虑后续收紧 |
| W12 误删合规留痕 | 审计证据缺失 | 默认 retention=0（永留）+ 默认 dry_run + 删除即审计 |
| 存量调用方依赖无界列表 | W8 行为变更 | 响应 shape 不变 + 头部携带游标 + CHANGELOG 高亮 |
| W6 历史泄露未清洗 | 旧 commit 仍含密钥 | 本方案完成密钥轮换止血；filter-repo 单列决策 |

---

## 七、执行边界声明

- 本方案只覆盖**软件层可真实落地**的平台化能力。真实 TEE 硬件、FISCO 真链 e2e、生产 Vault/HSM、GPU 等按既有 FG-001..FG-016 产品化验收项跟踪，不在本方案内伪造完成。
- CubeSandbox 的 E2B 兼容/envd/OCI 模板/Template Store/AgentHub 明确**不对齐**（见 1.3），执行 agent 不得擅自引入。
- 所有"已有实现可复用"的判断基于 2026-09-06 源码复核；实施时若与现状冲突，以代码为准并回写本文件（沿用前序方案纪律）。
- 每轮实施完成后，在下方"执行记录"追加：任务 | 状态 | 关键产出 | 新增测试与结果 | 回归结论 | 未实施项。

---

## 八、执行记录

（待实施轮次填写）
