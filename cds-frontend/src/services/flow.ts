import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import api from './api';
import type { PaginatedResponse } from '../types/api';
import type { Contract } from '../types/models';

// SS-15 数据流通编排（交付方式 / 自动计算 / Notebook 开发 / 工作负载发布 / 成果交付）。
// 路径与载荷严格对齐冻结契约 docs 版 `/tmp/flow_api_contract.md`，不得自行发明端点。
// 错误一律以 W2 信封 {code,message,detail,request_id} 透出（api.ts 拦截器已 reject 原始响应体），
// 展示前统一走 describeFlowError()，不做任何"看起来可用"的粉饰。

// ---------------------------------------------------------------------------
// 枚举
// ---------------------------------------------------------------------------

export const DeliveryMethod = {
  Dataset: 'dataset',
  OpenApi: 'open_api',
  ResultPush: 'result_push',
} as const;
export type DeliveryMethod = typeof DeliveryMethod[keyof typeof DeliveryMethod];

export const DELIVERY_METHOD_OPTIONS: { label: string; value: DeliveryMethod; hint: string }[] = [
  { label: '数据集交付', value: DeliveryMethod.Dataset, hint: '会话工作空间成果打包（含水印）后下载' },
  { label: 'API 交付', value: DeliveryMethod.OpenApi, hint: '发布为 /open/v1/{slug} 在线端点' },
  { label: '结果推送', value: DeliveryMethod.ResultPush, hint: '计算完成后 Webhook 主动推送' },
];

export const ComputeFrequency = {
  Manual: 'manual',
  Hourly: 'hourly',
  Daily: 'daily',
} as const;
export type ComputeFrequency = typeof ComputeFrequency[keyof typeof ComputeFrequency];

export const NOTEBOOK_BACKENDS = {
  Kernel: 'cds_kernel',
  Stateless: 'cds_stateless',
} as const;
export type NotebookExecutionBackend = typeof NOTEBOOK_BACKENDS[keyof typeof NOTEBOOK_BACKENDS];

// ---------- 开发任务字段（SS-18 §3 / §8 P0-①，后端 app/models/notebook.py 同名字符串枚举） ----------

/** 诚实缺省位：后端没下发的字段一律渲染成这个，不用示例值、不用 0 充数（禁伪造铁律）。 */
export const UNKNOWN_TEXT = '未记录';

/** 首页（原型 A-1）的两个默认量：趋势 7 天窗口与两张表的「最近 5 条」（后端 recent_limit 上限 20）。 */
export const HOME_TREND_DAYS = 7;
export const HOME_RECENT_ROWS = 5;

/**
 * 任务调试方法。与后端 TaskDebugMethod 逐字对齐（只登记有真实后端语义的三项）：
 * 原型第 4 项无后端语义，不登记也不伪造（宁缺不造）。
 */
export const TaskDebugMethod = {
  WebIde: 'web_ide',
  LocalPlugin: 'local_plugin',
  Jupyter: 'jupyter',
} as const;
export type TaskDebugMethod = typeof TaskDebugMethod[keyof typeof TaskDebugMethod];

export const TASK_DEBUG_METHOD_OPTIONS: { label: string; value: TaskDebugMethod; hint: string }[] = [
  { label: 'Web IDE 在线调试', value: TaskDebugMethod.WebIde, hint: '本工作台的在线 Notebook 页面，单元格直连沙箱内核' },
  { label: '本地 IDE 插件', value: TaskDebugMethod.LocalPlugin, hint: '本地开发工具经平台开放接口通道调试' },
  { label: '原生 Jupyter', value: TaskDebugMethod.Jupyter, hint: '导入导出 .ipynb 文档开发，需沙箱内确实部署 Jupyter 服务' },
];

/**
 * 任务级成果导出方式：选的是成果离开开发环的通道，与合约的 DeliveryMethod 白名单不同层。
 * 原型的「结果推送」在任务级没有对应取值（它属于合约交付白名单），因此不提供该选项。
 */
export const TaskExportMode = {
  Bundle: 'bundle',
  OpenApi: 'openapi',
} as const;
export type TaskExportMode = typeof TaskExportMode[keyof typeof TaskExportMode];

export const TASK_EXPORT_MODE_OPTIONS: { label: string; value: TaskExportMode; hint: string }[] = [
  { label: '成果集打包导出', value: TaskExportMode.Bundle, hint: '产物进成果包，经成果交付中心审查后交付' },
  { label: '在线 API 导出', value: TaskExportMode.OpenApi, hint: '发布为 /open/v1/{slug} 在线端供调用' },
];

// 任务内存档位（GiB，UI 说 GiB、库里存 MB）：与后端 notebooks.py 的 MEMORY_MIN_MB /
// DEBUG_MEMORY_MAX_MB / EXEC_MEMORY_MAX_MB 同一口径；前端只把提示与前置校验写到这里一份。
export const TASK_MEMORY_LIMITS = {
  debug: { min_gib: 2, max_gib: 32, step: 1, precision: 0, label: '调试内存' },
  exec: { min_gib: 2, max_gib: 64, step: 1, precision: 0, label: '执行内存' },
} as const;
export type TaskMemoryKind = keyof typeof TASK_MEMORY_LIMITS;

export const WorkloadStatus = {
  Draft: 'draft',
  Testing: 'testing',
  TestPassed: 'test_passed',
  TestFailed: 'test_failed',
  Published: 'published',
  Retired: 'retired',
} as const;
export type WorkloadStatus = typeof WorkloadStatus[keyof typeof WorkloadStatus];

export type WorkloadEnv = 'test' | 'production';

export const ComputeRunStatus = {
  Pending: 'pending',
  Running: 'running',
  Succeeded: 'succeeded',
  Failed: 'failed',
  SkippedOverlap: 'skipped_overlap',
  Cancelled: 'cancelled',
} as const;
export type ComputeRunStatus = typeof ComputeRunStatus[keyof typeof ComputeRunStatus];

export const WebhookEvent = {
  ComputeRunSucceeded: 'compute_run.succeeded',
  ComputeRunFailed: 'compute_run.failed',
  WorkloadPublished: 'workload.published',
  WorkloadTestFinished: 'workload.test_finished',
} as const;
export type WebhookEvent = typeof WebhookEvent[keyof typeof WebhookEvent];

export const WEBHOOK_EVENT_OPTIONS: { label: string; value: WebhookEvent }[] = [
  { label: '计算成功 (compute_run.succeeded)', value: WebhookEvent.ComputeRunSucceeded },
  { label: '计算失败 (compute_run.failed)', value: WebhookEvent.ComputeRunFailed },
  { label: '负载发布 (workload.published)', value: WebhookEvent.WorkloadPublished },
  { label: '负载测试结束 (workload.test_finished)', value: WebhookEvent.WorkloadTestFinished },
];

export type WebhookStatus = 'active' | 'paused';
export type ApiEndpointStatus = 'enabled' | 'disabled';

export const DeliveryStatus = {
  Pending: 'pending',
  Delivering: 'delivering',
  Delivered: 'delivered',
  Failed: 'failed',
  Dead: 'dead',
} as const;
export type DeliveryStatus = typeof DeliveryStatus[keyof typeof DeliveryStatus];

export const DEFAULT_COMPUTE_TZ = 'Asia/Shanghai';

export const COMPUTE_TZ_OPTIONS = [
  { label: '亚洲/上海 (Asia/Shanghai)', value: 'Asia/Shanghai' },
  { label: '亚洲/新加坡 (Asia/Singapore)', value: 'Asia/Singapore' },
  { label: '亚洲/东京 (Asia/Tokyo)', value: 'Asia/Tokyo' },
  { label: '协调世界时 (UTC)', value: 'UTC' },
  { label: '欧洲/柏林 (Europe/Berlin)', value: 'Europe/Berlin' },
  { label: '美东 (America/New_York)', value: 'America/New_York' },
];

// 沙箱资源区间：UI 的 InputNumber min/max 与提交前校验共用同一份常量，避免两处漂移。
export const FLOW_RESOURCE_LIMITS = {
  cpu_cores: { min: 0.5, max: 64, step: 0.5, precision: 1, label: 'CPU（核）' },
  memory_mb: { min: 256, max: 262144, step: 256, precision: 0, label: '内存（MB）' },
  disk_gb: { min: 1, max: 2048, step: 1, precision: 0, label: '磁盘（GB）' },
  gpu_count: { min: 0, max: 8, step: 1, precision: 0, label: 'GPU（卡）' },
  timeout_seconds: { min: 60, max: 86400, step: 60, precision: 0, label: '超时（秒）' },
} as const;
export type FlowResourceField = keyof typeof FLOW_RESOURCE_LIMITS;

// 长耗时操作显式放宽 30s 默认超时（契约注明工作负载测试/全量运行为同步执行，可能数分钟）。
const TIMEOUT_TEST = 300_000;
const TIMEOUT_RUN_ALL = 300_000;
const TIMEOUT_CELL = 120_000;
const TIMEOUT_BUILD = 120_000;

// ---------------------------------------------------------------------------
// 对象类型
// ---------------------------------------------------------------------------

export interface SandboxResourceSpec {
  cpu_cores?: number;
  memory_mb?: number;
  disk_gb?: number;
  gpu_count?: number;
  image_template?: string;
  timeout_seconds?: number;
}

export interface FlowConfigPayload {
  delivery_methods: DeliveryMethod[];
  auto_compute: boolean;
  compute_frequency: ComputeFrequency;
  compute_minute: number;
  compute_hour: number;
  compute_tz: string;
  sandbox_resource_spec: SandboxResourceSpec;
}

/** flow-config 返回 ContractResponse 并附带下列字段；未下发的字段以缺省处理（如实显示"未配置"）。 */
export interface ContractFlowFields {
  delivery_methods?: DeliveryMethod[] | null;
  auto_compute?: boolean | null;
  compute_frequency?: ComputeFrequency | null;
  compute_minute?: number | null;
  compute_hour?: number | null;
  compute_tz?: string | null;
  sandbox_resource_spec?: SandboxResourceSpec | null;
  next_compute_at?: string | null;
  last_computed_at?: string | null;
}

export type ContractWithFlow = Contract & ContractFlowFields;

// ---------- Notebooks ----------

export interface Notebook {
  id: string;
  contract_id: string | null;
  title: string;
  path: string;
  kernel: string | null;
  language: string | null;
  content: Record<string, unknown> | null;
  version: number;
  status: string;
  session_id: string | null;
  execution_backend: string | null;
  last_executed_at: string | null;
  created_at: string;
  updated_at: string;
  // 以以下五项是 SS-18 P0-① 的开发者任务字段；**存量行全为 null**（迁移只加列不回填业务编号），
  // 后端未下发时同样当「未记录」处理，不得进筛选默认项。
  task_no?: string | null;
  debug_method?: string | null;
  export_mode?: string | null;
  debug_memory_mb?: number | null;
  exec_memory_mb?: number | null;
  description?: string | null;
  /** null = 未限定（注入合约授权的全部供数）；数组 = 只注入这些**数据资源** id（[] = 不注入）。 */
  data_resource_ids?: string[] | null;
}

export interface NotebookCreatePayload {
  title: string;
  path: string;
  contract_id?: string | null;
  session_id?: string | null;
  content?: string;
  language?: string;
  // 任务级字段：task_no **不在此列**——编号由服务端按当日序号生成，客户端不送也不改。
  debug_method?: TaskDebugMethod | null;
  export_mode?: TaskExportMode | null;
  debug_memory_mb?: number | null;
  exec_memory_mb?: number | null;
  description?: string | null;
  // 图 2-2「已选择数据」：送的是数据资源 id；服务端校在合约授权集内，越权整单 422。
  data_resource_ids?: string[] | null;
}

export interface NotebookUpdatePayload {
  title?: string;
  content?: string;
  session_id?: string | null;
}

/**
 * 列表只支持服务端过滤 contract_id / status（notebooks.py::list_notebooks）。
 * 关键词与任务级字段（调试方法/导出方式）没有服务端参数，因此只在已载入的一页内筛（见
 * pages/Flow/devTasksModel.ts），并如实告诉用户“只筛已载入的 N 条”，不自称全量检索。
 */
export interface NotebookListParams {
  contract_id?: string | null;
  status?: string;
  page?: number;
  page_size?: number;
}

export interface NotebookDevSession {
  session_id: string;
  created: boolean;
  sandbox_level: string;
  sandbox_mode: string;
  env: string;
}

export interface CellExecutePayload {
  code: string;
  cell_index?: number;
  persist?: boolean;
}

export interface CellExecuteResult {
  ok: boolean;
  backend: string;
  stateful: boolean;
  stdout: string;
  stderr: string;
  repr: string | null;
  exit_code: number;
  output_blocked: boolean;
  blocked_reason?: string | null;
  degraded_reason?: string | null;
  kernel_error?: string | null;
}

export interface CellRunResult extends CellExecuteResult {
  cell_index: number;
}

export interface NotebookRunResult {
  results: CellRunResult[];
  passed: boolean;
}

export interface JupyterDevEnv {
  available: boolean;
  sandbox_level: string | null;
  microvm: boolean;
  reason: string | null;
}

export interface JupyterStatus {
  jupyter_available: boolean;
  jupyter_path: string | null;
  // 探测链本身失败时后端给的原因（例：会话密钥分发失败）——与「未安装」是两回事。
  probe_error?: string | null;
  proxy_enabled: boolean;
  url: string | null;
  // 开发环（反代后的 JupyterLab）可用性与真实隔离等级：后端不说，前端就不给入口。
  dev_env?: JupyterDevEnv | null;
  session_id?: string | null;
  /** 该笔记当前记录的执行后端（后端 jupyter_status 一并下发，与 NotebookOut.execution_backend 同值）。 */
  notebook_execution_backend?: string | null;
}

export interface JupyterEnvTicket {
  url: string;
  ttl: number;
  sandbox_level: string | null;
  session_id: string;
  notebook_path: string;
  injected: string[];
  /** 供数为何没进来（后端原话）。空数组 = 本合约确实没有已就绪资源。 */
  inject_errors?: string[];
}

// ---------- Workloads ----------

export interface Workload {
  id: string;
  name: string;
  contract_id: string | null;
  source_notebook_id: string | null;
  language: string | null;
  entrypoint_hash: string | null;
  resource_spec: SandboxResourceSpec | null;
  env: string;
  status: string;
  version: number;
  is_latest: boolean;
  test_run_id: string | null;
  published_at: string | null;
  created_at: string;
}

export interface WorkloadCreatePayload {
  name: string;
  contract_id?: string | null;
  notebook_id?: string | null;
  entrypoint?: string;
  language?: string;
  resource_spec?: SandboxResourceSpec;
  data_product_ids?: string[];
}

export interface WorkloadListParams {
  contract_id?: string | null;
  env?: WorkloadEnv;
  status?: string;
  page?: number;
  page_size?: number;
}

export interface WorkloadTestCheck {
  name: string;
  passed: boolean;
  detail?: string | null;
}

export interface WorkloadTestRun {
  test_run_id: string;
  status: string;
  passed: boolean | null;
  checks: WorkloadTestCheck[];
  session_id?: string | null;
  created_at?: string | null;
  finished_at?: string | null;
}

// 发布门 412 错误码（契约 §3）
export const PUBLISH_GATE_CODES = {
  TestRequired: 'WORKLOAD_TEST_REQUIRED',
  TestNotPassed: 'WORKLOAD_TEST_NOT_PASSED',
  HashMismatch: 'WORKLOAD_HASH_MISMATCH',
} as const;

// ---------- Compute ----------

export interface ComputeScheduleItem {
  contract_id: string;
  contract_no: string;
  auto_compute: boolean;
  compute_frequency: string;
  next_compute_at: string | null;
  last_computed_at: string | null;
  delivery_methods: string[] | null;
}

export interface ComputeScheduleList {
  items: ComputeScheduleItem[];
}

export interface ComputeRun {
  id: string;
  contract_id: string;
  trigger: string | null;
  status: string;
  scheduled_for: string | null;
  started_at: string | null;
  finished_at: string | null;
  output_rows: number | null;
  output_files: string[] | null;
  error_message: string | null;
  // 后端 ComputeRunOut 带下列字段；旧用例与老返回可能不含，故全部可缺，缺失处渲染「未记录」。
  workload_id?: string | null;
  attempt?: number | null;
  resource_usage?: Record<string, unknown> | null;
  // 后端在 run 详情里可能附带执行日志；仅在字段存在时渲染，不做缺省伪造。
  output?: string | null;
}

export interface ComputeRunListParams {
  contract_id?: string | null;
  status?: string;
  page?: number;
  page_size?: number;
}

// ---------- Delivery：数据集打包 ----------

export interface DatasetBundleRequest {
  session_id: string;
  contract_id: string;
}

export interface DatasetBundle {
  bundle_id: string;
  files: { name: string; size_bytes: number }[];
  size_bytes: number;
  download_url: string | null;
  watermark_applied: boolean;
}

export interface BundlePreview {
  bundle_id: string;
  available: boolean;
  reason?: string;
  member: string | null;
  members?: string[];
  data?: string;
  rows_returned?: number;
  row_count_total?: number;
  truncated?: boolean;
  output_review?: {
    bundle_scope?: string | null;
    file_status?: string | null;
    file_findings?: number | null;
    preview_findings?: number;
    preview_blocked?: boolean;
  };
}

// ---------- Delivery：Webhook ----------

export interface WebhookSubscription {
  id: string;
  name: string;
  url: string;
  events: string[];
  status: string;
  /** 仅创建/轮转时下发一次，列表与详情不含该字段。 */
  secret?: string;
}

export interface WebhookCreatePayload {
  name: string;
  url: string;
  events: string[];
  contract_id?: string | null;
}

export interface WebhookUpdatePayload {
  status?: WebhookStatus;
  events?: string[];
  url?: string;
  name?: string;
}

export interface WebhookDelivery {
  id: string;
  event_type: string;
  status: string;
  attempt: number;
  max_attempts: number;
  response_status: number | null;
  last_error: string | null;
  created_at: string;
  delivered_at: string | null;
}

export interface WebhookDeliveryListParams {
  status?: string;
  page?: number;
  page_size?: number;
}

export interface WebhookTestResult {
  delivered: boolean;
  response_status: number | null;
  detail: string | null;
}

// ---------- Delivery：OpenAPI 端点 ----------

export interface ApiEndpoint {
  id: string;
  contract_id: string;
  workload_id: string | null;
  name: string;
  slug: string;
  request_schema: Record<string, unknown> | null;
  rate_limit_per_minute: number | null;
  quota_total: number | null;
  status: string;
  created_at?: string | null;
  /** 后端 _endpoint_dict 下发的 /open/v1/{slug}，不由前端拼接。 */
  path?: string | null;
  published_at?: string | null;
  /** 端点表尚无描述列：后端下发才展示，否则列表记「未记录」。 */
  description?: string | null;
}

export interface ApiEndpointCreatePayload {
  contract_id: string;
  workload_id?: string | null;
  name: string;
  /** 省略时后端按名称生成 slug，前端不再自己拼一个。 */
  slug?: string;
  description?: string | null;
  request_schema?: Record<string, unknown> | null;
  rate_limit_per_minute?: number | null;
  quota_total?: number | null;
}

export interface ApiEndpointUpdatePayload {
  status?: ApiEndpointStatus;
  rate_limit_per_minute?: number | null;
  quota_total?: number | null;
}

export interface OpenApiDoc {
  openapi?: string;
  info?: { title?: string; version?: string; description?: string } & Record<string, unknown>;
  paths?: Record<string, Record<string, OpenApiOperation>>;
  components?: Record<string, unknown>;
  servers?: { url?: string }[];
}

export const OPENAPI_CALL_STATUS = {
  Succeeded: 'succeeded',
  Failed: 'failed',
} as const;
export type ApiEndpointCallStatus = typeof OPENAPI_CALL_STATUS[keyof typeof OPENAPI_CALL_STATUS];

/**
 * 接口监控（PRD 3-3）的调用明细行：一行 = 后端一条带 Merkle 锚定的审计日志
 * （app/api/deliveries.py::_call_dict，action=openapi.call / openapi.call_failed）。
 * 字段逐字镜像后端：失败行只有 code/message，output_rows 与 duration_ms 保持 null，
 * 前端不按 0 交付；client_ip 取审计日志的 ip_address，没有就如实为 null。
 */
export interface ApiEndpointCall {
  id: string;
  endpoint_id: string;
  slug: string;
  /** 后端只写 succeeded / failed；其他取值原样透出，不并入成功率。 */
  status?: string | null;
  /** 仅失败行有值：delivery_service 里真实 raise 的 EndpointUnavailable.code。 */
  code?: string | null;
  message?: string | null;
  output_rows?: number | null;
  duration_ms?: number | null;
  request_sha256?: string | null;
  caller_id?: string | null;
  client_ip?: string | null;
  created_at?: string | null;
}

/** 明细一次取回的窗口条数：与后端 page_size 上界（le=100）对齐，成功率口径就是这最近若干条。 */
export const OPENAPI_CALLS_WINDOW = 100;

export interface ApiEndpointCallListParams {
  status?: ApiEndpointCallStatus | '';
  page?: number;
  page_size?: number;
}

export interface EndpointCallMetrics {
  /** 调用总次数：取响应 total（后端对审计日志做的 count）。 */
  total: number | null;
  /** total 缺失时按窗口条数兜底，口径要在界面上说清楚。 */
  totalIsFallback: boolean;
  /** 本次返回的明细条数（成功率与最新时间的计算窗口）。 */
  windowCount: number;
  /** 最新调用时间：窗口内 created_at 的最大值，不从当前时间推算。 */
  latestAt: string | null;
  /** 调用成功率（%）；没有成功/失败判定时为 null，由界面渲染「未记录」。 */
  successRate: number | null;
  /** 一句话口径：成功率怎么算的，或者为什么算不出来。 */
  rateNote: string;
}

/**
 * 指标 3 宫格的口径全部收在这一个纯函数里，页面不再自行算数。
 *
 * 成功率只认响应里真的带 succeeded/failed 判定的行：一条判定都没有就返回 null
 * （界面渲染「未记录」+ 原因），绝不用「有明细 ⇒ 全成功」反推成 100%。
 */
export function computeEndpointCallMetrics(
  payload: PaginatedResponse<ApiEndpointCall> | null | undefined,
): EndpointCallMetrics {
  if (!payload) {
    return { total: null, totalIsFallback: false, windowCount: 0, latestAt: null, successRate: null, rateNote: '调用数据尚未返回，不做推算' };
  }
  const items = (Array.isArray(payload.items) ? payload.items : []).filter(Boolean);
  const windowCount = items.length;
  const backendTotal = typeof payload.total === 'number' && Number.isFinite(payload.total) ? payload.total : null;

  let latestAt: string | null = null;
  for (const row of items) {
    const at = row.created_at;
    if (!at) continue;
    const stamp = new Date(at).getTime();
    if (Number.isNaN(stamp)) continue;
    if (latestAt == null || stamp > new Date(latestAt).getTime()) latestAt = at;
  }

  const rated = items.filter((row) => row.status === OPENAPI_CALL_STATUS.Succeeded || row.status === OPENAPI_CALL_STATUS.Failed);
  const windowSuffix = backendTotal != null && backendTotal > windowCount ? `（共 ${backendTotal} 次，只取最近 ${windowCount} 条）` : '';

  if (rated.length === 0) {
    return {
      total: backendTotal ?? windowCount,
      totalIsFallback: backendTotal == null,
      windowCount,
      latestAt,
      successRate: null,
      rateNote:
        windowCount === 0
          ? '该端点暂无调用明细，成功率无从计算'
          : `返回的 ${windowCount} 条明细未带成功/失败判定，成功率不做推算`,
    };
  }

  const succeeded = rated.filter((row) => row.status === OPENAPI_CALL_STATUS.Succeeded).length;
  return {
    total: backendTotal ?? windowCount,
    totalIsFallback: backendTotal == null,
    windowCount,
    latestAt,
    successRate: Math.round((succeeded / rated.length) * 1000) / 10,
    rateNote: `按 ${rated.length} 条带判定的调用计${windowSuffix}`,
  };
}

export interface OpenApiOperation {
  operationId?: string;
  summary?: string;
  description?: string;
  tags?: string[];
  parameters?: { name?: string; in?: string; required?: boolean; schema?: Record<string, unknown> }[];
  requestBody?: { required?: boolean; content?: Record<string, { schema?: Record<string, unknown> }> };
  responses?: Record<string, { description?: string }>;
  security?: unknown;
}

// ---------------------------------------------------------------------------
// W2 错误信封
// ---------------------------------------------------------------------------

export interface FlowErrorInfo {
  code: string | null;
  message: string | null;
  detail: string | null;
  reason: string | null;
  requestId: string | null;
  httpStatus: number | null;
}

const stringifyDetail = (detail: unknown): string | null => {
  if (detail == null) return null;
  if (typeof detail === 'string') return detail.trim() || null;
  if (typeof detail === 'number' || typeof detail === 'boolean') return String(detail);
  if (Array.isArray(detail)) {
    const parts = detail.map((item) => stringifyDetail(item)).filter((item): item is string => !!item);
    return parts.length ? parts.join('；') : null;
  }
  if (typeof detail === 'object') {
    const record = detail as Record<string, unknown>;
    const nested = record.error ?? record.msg ?? record.message;
    if (typeof nested === 'string' && nested.trim()) return nested.trim();
    try {
      return JSON.stringify(detail);
    } catch {
      return String(detail);
    }
  }
  return String(detail);
};

/** detail.reason（机读）→ 用户可读文案；与后端 flow_runtime.REASON_MESSAGES 同一口径。 */
export const RUNTIME_REASON_COPY: Record<string, string> = {
  dataplane_disabled:
    '当前部署未启用 MicroVM 沙箱数据面：MicroVM 档位的在线开发与测试/生产沙箱都无法创建，需先完成 Cube 计算节点部署（节点需 /dev/kvm）并开启 CDS_CUBE_DATAPLANE_ENABLED。',
  no_ready_node:
    'MicroVM 数据面已开启，但当前没有 online 的计算节点，沙箱创建会被直接拒绍：请检查节点心跳与 preflight 结果。',
};

export function describeRuntimeReason(reason: string | null | undefined): string | null {
  if (!reason) return null;
  return RUNTIME_REASON_COPY[reason] ?? null;
}

export interface FlowRuntimeStatus {
  available: boolean;
  reason: string | null;
  message: string | null;
  ready_nodes: number;
  total_nodes: number;
  checked_at: string;
}

/** 把 api.ts 抛出的未知错误解析成 W2 信封字段（含 HTTP 状态，412 发布门需要按 code 分支）。 */
export function parseFlowError(error: unknown): FlowErrorInfo {
  const info: FlowErrorInfo = { code: null, message: null, detail: null, reason: null, requestId: null, httpStatus: null };
  if (error == null) return info;
  if (typeof error === 'string') {
    info.message = error;
    return info;
  }
  if (typeof error !== 'object') {
    info.message = String(error);
    return info;
  }
  const envelope = error as Record<string, unknown>;
  const response = envelope.response as Record<string, unknown> | undefined;
  const source = (response?.data ?? envelope) as Record<string, unknown>;
  if (typeof response?.status === 'number') info.httpStatus = response.status;
  if (typeof envelope.status === 'number') info.httpStatus = envelope.status as number;
  if (typeof source.code === 'string') info.code = source.code;
  if (typeof source.message === 'string') info.message = source.message.trim() || null;
  info.detail = stringifyDetail(source.detail);
  if (source.detail && typeof source.detail === 'object' && !Array.isArray(source.detail)) {
    const rawReason = (source.detail as Record<string, unknown>).reason;
    if (typeof rawReason === 'string' && rawReason) info.reason = rawReason;
  }
  if (typeof source.request_id === 'string') info.requestId = source.request_id;
  if (!info.message && envelope instanceof Error && envelope.message) info.message = envelope.message;
  return info;
}

/** 面向用户的错误文案：detail 优先，其次 message，最后回退；末尾附错误码便于报障。 */
export function describeFlowError(error: unknown, fallback = '请求失败，请稍后重试'): string {
  const info = parseFlowError(error);
  const text = describeRuntimeReason(info.reason) ?? info.detail ?? info.message ?? fallback;
  if (info.code && !text.includes(info.code)) return `${text}（${info.code}）`;
  return text;
}

/** 412 发布门错误码 → 人话（契约 §3 publish）。 */
export function describePublishGateCode(code: string | null | undefined): string | null {
  switch (code) {
    case PUBLISH_GATE_CODES.TestRequired:
      return '该负载还没有测试记录：请先点击「发起测试」并跑完，再发布。';
    case PUBLISH_GATE_CODES.TestNotPassed:
      return '最近一次测试未通过：请修正负载后重新测试，通过才能发布到生产。';
    case PUBLISH_GATE_CODES.HashMismatch:
      return '负载内容与通过测试时不一致：入口代码已改动，请重新发起测试后再发布。';
    default:
      return null;
  }
}

// ---------------------------------------------------------------------------
// 边界归一化：列表信封 / 测试运行
// ---------------------------------------------------------------------------

/** 后端列表统一 {items,total,page,page_size}；对裸数组响应做同样归一（不猜测、只适配）。 */
export function toListPayload<T>(payload: unknown): PaginatedResponse<T> {
  if (Array.isArray(payload)) {
    return { items: payload as T[], total: payload.length, page: 1, page_size: payload.length };
  }
  const record = (payload ?? {}) as Record<string, unknown>;
  const items = Array.isArray(record.items) ? (record.items as T[]) : [];
  return {
    items,
    total: typeof record.total === 'number' ? record.total : items.length,
    page: typeof record.page === 'number' ? record.page : 1,
    page_size: typeof record.page_size === 'number' ? record.page_size : (items.length || 20),
  };
}

const asString = (value: unknown): string => (typeof value === 'string' ? value : '');

const asNullableString = (value: unknown): string | null =>
  typeof value === 'string' ? value : typeof value === 'number' ? String(value) : null;

const asChecks = (value: unknown): WorkloadTestCheck[] => {
  if (!Array.isArray(value)) return [];
  const truthy = (flag: unknown, alias?: unknown) => flag === true || flag === 1 || alias === true || alias === 1;
  return value.map((raw) => {
    const item = (raw ?? {}) as Record<string, unknown>;
    return {
      name: asString(item.name) || asString(item.check) || '检查项',
      passed: truthy(item.passed, item.ok),
      detail: asNullableString(item.detail ?? item.message ?? item.reason),
    };
  });
};

export function normalizeTestRun(payload: unknown): WorkloadTestRun {
  const record = (payload ?? {}) as Record<string, unknown>;
  const checks = asChecks(record.checks ?? record.check_results);
  const rawPassed = record.passed;
  const passed = typeof rawPassed === 'boolean' ? rawPassed : typeof rawPassed === 'number' ? rawPassed === 1 : null;
  return {
    test_run_id: asString(record.test_run_id) || asString(record.id),
    status: asString(record.status) || (passed === null ? 'unknown' : passed ? 'passed' : 'failed'),
    passed,
    checks,
    session_id: asNullableString(record.session_id),
    created_at: asNullableString(record.created_at),
    finished_at: asNullableString(record.finished_at),
  };
}

// ---------------------------------------------------------------------------
// Notebook 内容：单元格解析与回写（保持后端原有存储格式，不做有损改写）
// ---------------------------------------------------------------------------

export type NotebookContentFormat = 'ipython' | 'percent' | 'plain';

export interface ParsedNotebook {
  format: NotebookContentFormat;
  cells: string[];
  document?: Record<string, unknown>;
}

const cellSourceToText = (source: unknown): string => {
  if (typeof source === 'string') return source;
  if (Array.isArray(source)) return source.map((line) => (typeof line === 'string' ? line : '')).join('');
  return '';
};

/**
 * 解析 notebook content 为单元格列表（后端 `Notebook.content` 存的是 nbformat 4 文档对象，
 * 但历史数据与本地脚本也能是字符串，两种入口都要能解）：
 * - 文档对象 / ipython JSON（含 cells 数组）→ 逐个 cell 的 source（字符串或字符串数组）
 * - `# %%` 百分比脚本 → 按分隔行切分
 * - 其他 → 整篇作为单个单元格（不猜测、不拆行）
 */
export function parseNotebookContent(content?: unknown): ParsedNotebook {
  if (content && typeof content === 'object') {
    const doc = (Array.isArray(content) ? { cells: content } : content) as Record<string, unknown>;
    if (Array.isArray(doc.cells)) {
      const cells = (doc.cells as unknown[]).map((raw) => cellSourceToText((raw as Record<string, unknown>)?.source));
      return { format: 'ipython', cells: cells.length ? cells : [''], document: doc };
    }
    return { format: 'percent', cells: [''] };
  }

  const text = typeof content === 'string' ? content : '';
  if (!text.trim()) return { format: 'percent', cells: [''] };

  const trimmed = text.trim();
  if (trimmed.startsWith('{') || trimmed.startsWith('[')) {
    try {
      const parsed = JSON.parse(trimmed) as unknown;
      const doc = Array.isArray(parsed) ? { cells: parsed } : (parsed as Record<string, unknown>);
      if (doc && Array.isArray((doc as Record<string, unknown>).cells)) {
        const cells = ((doc as Record<string, unknown>).cells as unknown[]).map((raw) =>
          cellSourceToText((raw as Record<string, unknown>)?.source),
        );
        return { format: 'ipython', cells: cells.length ? cells : [''], document: doc as Record<string, unknown> };
      }
    } catch {
      // 不是合法 JSON：按下面的脚本格式处理
    }
  }

  if (/#\s*%%/.test(text)) {
    const cells = text.split(/^\s*#\s*%%.*$/m).map((cell) => cell.replace(/^\s*\n+/, '').trimEnd());
    const nonEmpty = cells.filter((cell) => cell.trim().length > 0);
    return { format: 'percent', cells: nonEmpty.length ? nonEmpty : [''] };
  }

  return { format: 'plain', cells: [text] };
}

/**
 * 回写统一下发 nbformat 4 文档：入库列是 JSON 文档，后端 `normalize_notebook` 只认它，
 * 所以百分比/纯脚本来源也先升成文档（保留其余字段与原有 cell 元数据）。
 */
export function serializeNotebookContent(parsed: ParsedNotebook, cells: string[]): string {
  const doc: Record<string, unknown> =
    parsed.format === 'ipython' && parsed.document ? { ...parsed.document } : { metadata: {}, nbformat: 4, nbformat_minor: 5 };
  if (doc.nbformat !== 4) doc.nbformat = 4;
  if (!doc.metadata || typeof doc.metadata !== 'object') doc.metadata = {};
  const sourceCells = Array.isArray(doc.cells) ? [...(doc.cells as unknown[])] : [];
  doc.cells = cells.map((source, index) => {
    const previous = (sourceCells[index] ?? {}) as Record<string, unknown>;
    return { ...previous, cell_type: asString(previous.cell_type) || 'code', source, outputs: previous.outputs ?? [], execution_count: previous.execution_count ?? null };
  });
  return JSON.stringify(doc, null, 1);
}

/** 服务端文档 / 历史字符串 → 与编辑器可比的规范文本（dirty 判定与“服务端已变”判定共用）。 */
export function canonicalizeNotebookContent(content?: unknown): string {
  const parsed = parseNotebookContent(content);
  return serializeNotebookContent(parsed, parsed.cells.length ? parsed.cells : ['']);
}

export function defaultNotebookPath(title: string): string {
  // `/nb/` 是负载元数据命名空间（entrypoint 字符串，随任务落库并参与 admission 的
  // entrypoint hash）；与平台页面路由 `/notebooks`、与采集口径 `/sandbox/*`
  // （见 directoryContract.ts）都不同层，互不解析——旧默认值 /notebooks/ 与页面
  // 路由撞名，易被误读成可点链接。
  const slug = title
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9\u4e00-\u9fa5]+/g, '-')
    .replace(/^-+|-+$/g, '');
  return `/nb/${slug || 'untitled'}.ipynb`;
}

// ---------------------------------------------------------------------------
// 开发任务表单（SS-18 M2′）：表单值 ↔ 单个 POST 建任务的载荷，以及错误→可读文案
// 任务编号由服务端生成：前端不开输入框、载荷里永远不出现 task_no（对外口径只能有一份）。
// ---------------------------------------------------------------------------

export interface DevTaskFieldIssue {
  /** 归位到表单字段名；null = 无法归位，页面按整体错误展示。 */
  field: string | null;
  text: string;
}

export const DEV_TASK_FIELD_LABELS: Record<string, string> = {
  title: '任务名称',
  path: '沙箱内路径',
  contract_id: '关联合约',
  debug_method: '调试方法',
  export_mode: '任务级导出方式',
  debug_memory_mb: '调试内存',
  exec_memory_mb: '执行内存',
  task_no: '任务编号',
  content: '笔记内容',
  language: '语言',
};

/** 任务枚举取值 → 中文标签；null/未下发给「未记录」，未登记取值原样透出（不编中文标签）。 */
export function taskEnumLabel(
  options: readonly { label: string; value: string }[],
  value: string | null | undefined,
  fallback: string = UNKNOWN_TEXT,
): string {
  if (!value) return fallback;
  return options.find((option) => option.value === value)?.label ?? value;
}

export function gibToMib(gib: number): number {
  return Math.round(gib * 1024);
}

/** MB → GiB（展示用）；非整数保留一位小数。缺字段返回 null，由页面渲染「未记录」。 */
export function mibToGib(mib: number | null | undefined): number | null {
  if (typeof mib !== 'number' || !Number.isFinite(mib) || mib <= 0) return null;
  return Math.round((mib / 1024) * 10) / 10;
}

export function formatMemoryGib(mib: number | null | undefined, fallback: string = UNKNOWN_TEXT): string {
  const gib = mibToGib(mib);
  return gib === null ? fallback : `${gib} GiB`;
}

export interface DevTaskFormValues {
  title: string;
  path?: string | null;
  contract_id?: string | null;
  debug_method?: TaskDebugMethod | null;
  export_mode?: TaskExportMode | null;
  /** 不填 = 沿用合约已批准规格（后端两列都留空时沿用合约 spec，不改旧客户端语义）。 */
  debug_memory_gib?: number | null;
  /** 「执行内存与调试内存不同」：关上时不送 exec_memory_mb，服务端继承调试值。 */
  separate_exec_memory?: boolean;
  exec_memory_gib?: number | null;
  description?: string | null;
  /** null = 未限定（全部授权供数）；数组 = 已选数据资源 id（[] = 明确不注入）。 */
  data_resource_ids?: string[] | null;
}

function isPositiveInteger(value: unknown): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value > 0;
}

/** 客户端范围校验：与后端 _resolve_task_memory 逐条同口径，避免把 422 留给用户看。 */
export function validateDevTaskForm(values: DevTaskFormValues): DevTaskFieldIssue[] {
  const issues: DevTaskFieldIssue[] = [];
  if (!values.title?.trim()) issues.push({ field: 'title', text: '请输入任务名称' });
  if (!values.contract_id) issues.push({ field: 'contract_id', text: '请选择关联合约' });

  const path = values.path?.trim();
  if (path && !path.startsWith('/')) issues.push({ field: 'path', text: '沙箱内路径需以 / 开头' });

  const debug = values.debug_memory_gib;
  const debugLimit = TASK_MEMORY_LIMITS.debug;
  if (debug !== null && debug !== undefined) {
    if (!isPositiveInteger(debug) || debug < debugLimit.min_gib || debug > debugLimit.max_gib) {
      issues.push({ field: 'debug_memory_gib', text: `调试内存范围为 ${debugLimit.min_gib}–${debugLimit.max_gib} GiB（整数）` });
    }
  }

  const exec = values.exec_memory_gib;
  const execLimit = TASK_MEMORY_LIMITS.exec;
  if (values.separate_exec_memory) {
    // 后端铁律：exec_memory_mb 只能与 debug_memory_mb 一起提交。
    if (debug === null || debug === undefined) {
      issues.push({ field: 'debug_memory_gib', text: '设置执行内存前需先填写调试内存（服务端要求两项一起提交）' });
    }
    if (exec === null || exec === undefined) {
      issues.push({ field: 'exec_memory_gib', text: '请填写执行内存，或关闭「执行内存与调试内存不同」' });
    } else if (!isPositiveInteger(exec) || exec < execLimit.min_gib || exec > execLimit.max_gib) {
      issues.push({ field: 'exec_memory_gib', text: `执行内存范围为 ${execLimit.min_gib}–${execLimit.max_gib} GiB（整数）` });
    } else if (typeof debug === 'number' && exec < debug) {
      issues.push({ field: 'exec_memory_gib', text: `执行内存不得低于调试内存（${debug} GiB）` });
    }
  }

  return issues;
}

/** 表单值 → 单个 POST /notebooks 的载荷；未填的任务级字段直接不送（由服务端取默认/沿用合约规格）。 */
export function buildDevTaskPayload(values: DevTaskFormValues): NotebookCreatePayload {
  const title = values.title?.trim() ?? '';
  const payload: NotebookCreatePayload = {
    title,
    path: values.path?.trim() || defaultNotebookPath(title),
    contract_id: values.contract_id || null,
  };
  if (values.description?.trim()) payload.description = values.description.trim();
  // 未碰过已选面板 = 不限定（不送该字段）；碰过就照实送，空数组代表“不注入任何供数”。
  if (Array.isArray(values.data_resource_ids)) payload.data_resource_ids = values.data_resource_ids;
  if (values.debug_method) payload.debug_method = values.debug_method;
  if (values.export_mode) payload.export_mode = values.export_mode;

  const debug = values.debug_memory_gib;
  if (typeof debug === 'number') payload.debug_memory_mb = gibToMib(debug);
  const exec = values.exec_memory_gib;
  if (values.separate_exec_memory && typeof exec === 'number' && typeof debug === 'number') {
    payload.exec_memory_mb = gibToMib(exec);
  }
  return payload;
}

const unwrapErrorPayload = (error: unknown): Record<string, unknown> => {
  if (error === null || typeof error !== 'object') return {};
  const envelope = error as Record<string, unknown>;
  const response = envelope.response as Record<string, unknown> | undefined;
  return (response?.data ?? envelope) as Record<string, unknown>;
};

/** HTTP 状态码在 axios 信封的 response.status 上（W2 体内则自带 status/http_status）。 */
function httpStatusOf(error: unknown): number | null {
  if (error === null || typeof error !== 'object') return null;
  const envelope = error as Record<string, unknown>;
  const response = envelope.response as Record<string, unknown> | undefined;
  const candidate = response?.status ?? envelope.status ?? unwrapErrorPayload(error).http_status;
  return typeof candidate === 'number' ? candidate : null;
}

/**
 * 建任务失败 → 可读、可定位到字段的文案（W2 信封）：
 * - 422 VALIDATION_ERROR：逐条取 detail.errors[] 的字段级 msg；
 * - 400 RESOURCE_OVERRIDE_EXCEEDED：拼出「超出合约已批准规格（合约内存 X GiB）」；
 * - 404：合约不存在；其余走 describeFlowError。
 * 只复述后端下发的字段，不自己编原因（禁伪造铁律）。
 */
export function describeNotebookCreateIssues(error: unknown, fallback = '创建开发任务失败'): DevTaskFieldIssue[] {
  const payload = unwrapErrorPayload(error);
  const code = typeof payload.code === 'string' ? payload.code : null;
  const detail = payload.detail;
  const status = httpStatusOf(error);

  if (code === 'RESOURCE_OVERRIDE_EXCEEDED') {
    const detailObject = (detail && typeof detail === 'object' ? detail : {}) as Record<string, unknown>;
    const approved = typeof detailObject.contract_memory_mb === 'number' ? detailObject.contract_memory_mb : null;
    const ceiling = typeof detailObject.effective_ceiling_mb === 'number' ? detailObject.effective_ceiling_mb : null;
    const matched = /^(debug_memory_mb|exec_memory_mb)\b/.exec(typeof payload.message === 'string' ? payload.message : '');
    const field = matched ? matched[1] : null;
    const scope = field ? DEV_TASK_FIELD_LABELS[field] ?? field : '任务内存';
    const approvedText = approved === null ? '未下发' : ` ${formatMemoryGib(approved)}`;
    const ceilingText = ceiling === null ? '' : `，本任务可用上限 ${formatMemoryGib(ceiling)}`;
    return [{ field, text: `${scope}超出合约已批准规格（合约内存${approvedText}）${ceilingText}：请下调内存，或先调整合约已批准的沙箱规格。` }];
  }

  if (code === 'VALIDATION_ERROR') {
    const raw = (detail && typeof detail === 'object' ? (detail as Record<string, unknown>).errors : null) as unknown;
    const items = Array.isArray(raw) ? raw : [];
    const issues = items
      .map((item): DevTaskFieldIssue | null => {
        const record = (item ?? {}) as Record<string, unknown>;
        const location = Array.isArray(record.loc) ? (record.loc as unknown[]) : [];
        const key = location.filter((part): part is string => typeof part === 'string').pop() ?? null;
        const message = typeof record.msg === 'string' && record.msg.trim() ? record.msg.trim() : null;
        if (!key && !message) return null;
        const label = key ? DEV_TASK_FIELD_LABELS[key] ?? key : null;
        return { field: key, text: label && message ? `${label}：${message}` : (message ?? `字段 ${label ?? '未知'} 取值不合法`) };
      })
      .filter((issue): issue is DevTaskFieldIssue => issue !== null);
    if (issues.length) return issues;
  }

  if (status === 404 || detail === 'contract not found') {
    return [{ field: 'contract_id', text: '合约不存在或已不可访问（404 contract not found）' }];
  }

  return [{ field: null, text: describeFlowError(error, fallback) }];
}

// ---------------------------------------------------------------------------
// flow-config 表单值 → 载荷（含客户端范围校验）
// ---------------------------------------------------------------------------

export interface FlowConfigFormValues {
  delivery_methods?: DeliveryMethod[];
  auto_compute?: boolean;
  compute_frequency?: ComputeFrequency;
  compute_minute?: number | null;
  compute_hour?: number | null;
  compute_tz?: string;
  cpu_cores?: number | null;
  memory_mb?: number | null;
  disk_gb?: number | null;
  gpu_count?: number | null;
  timeout_seconds?: number | null;
  image_template?: string | null;
}

export interface FlowConfigIssue {
  field: 'delivery_methods' | 'compute_frequency' | 'compute_minute' | 'compute_hour' | 'compute_tz' | FlowResourceField;
  message: string;
}

const isInteger = (value: unknown): value is number =>
  typeof value === 'number' && Number.isFinite(value) && Number.isInteger(value);

const outOfRangeMessage = (field: FlowResourceField, value: number): string => {
  const limit = FLOW_RESOURCE_LIMITS[field];
  return `${limit.label}需在 ${limit.min} ~ ${limit.max} 之间（当前 ${value}）`;
};

function checkResource(values: FlowConfigFormValues, field: FlowResourceField, issues: FlowConfigIssue[]) {
  const value = values[field];
  if (value == null || value === ('' as unknown as number)) return;
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    issues.push({ field, message: `${FLOW_RESOURCE_LIMITS[field].label}必须是数字` });
    return;
  }
  const limit = FLOW_RESOURCE_LIMITS[field];
  if (value < limit.min || value > limit.max) {
    issues.push({ field, message: outOfRangeMessage(field, value) });
    return;
  }
  if (limit.precision === 0 && !Number.isInteger(value)) {
    issues.push({ field, message: `${limit.label}需为整数（当前 ${value}）` });
  }
}

/**
 * 提交前的客户端校验：分钟 0-59、小时 0-23 越界、频率/开关互相矛盾、资源越界都在这里挡下，
 * 不依赖后端报错（契约允许后端再校验，但产品化要求前端先给出中文原因）。
 */
export function validateFlowConfig(values: FlowConfigFormValues): FlowConfigIssue[] {
  const issues: FlowConfigIssue[] = [];
  const methods = values.delivery_methods ?? [];
  if (!methods.length) {
    issues.push({ field: 'delivery_methods', message: '至少选择一种交付方式' });
  }

  const auto = values.auto_compute === true;
  const frequency = values.compute_frequency ?? ComputeFrequency.Manual;
  if (auto && frequency === ComputeFrequency.Manual) {
    issues.push({ field: 'compute_frequency', message: '已开启自动计算，频率需选择每小时或每日' });
  }

  if (!asString(values.compute_tz).trim()) {
    issues.push({ field: 'compute_tz', message: '请选择计算时区' });
  }

  const minute = values.compute_minute;
  const needMinute = auto && (frequency === ComputeFrequency.Hourly || frequency === ComputeFrequency.Daily);
  if (minute != null) {
    if (!isInteger(minute) || minute < 0 || minute > 59) {
      issues.push({ field: 'compute_minute', message: `执行分钟需在 0~59 之间的整数（当前 ${minute}）` });
    }
  } else if (needMinute) {
    issues.push({ field: 'compute_minute', message: '请填写执行分钟（0~59）' });
  }

  const hour = values.compute_hour;
  if (frequency === ComputeFrequency.Daily && auto) {
    if (hour != null) {
      if (!isInteger(hour) || hour < 0 || hour > 23) {
        issues.push({ field: 'compute_hour', message: `执行小时需在 0~23 之间的整数（当前 ${hour}）` });
      }
    } else {
      issues.push({ field: 'compute_hour', message: '每日执行请填写执行小时（0~23）' });
    }
  }

  (Object.keys(FLOW_RESOURCE_LIMITS) as FlowResourceField[]).forEach((field) => checkResource(values, field, issues));
  return issues;
}

/** 表单值 → 契约载荷：关闭自动计算时频率归 manual，未填的资源字段整键省略。 */
export function buildFlowConfigPayload(values: FlowConfigFormValues): FlowConfigPayload {
  const auto = values.auto_compute === true;
  const frequency: ComputeFrequency = auto
    ? (values.compute_frequency === ComputeFrequency.Daily ? ComputeFrequency.Daily : ComputeFrequency.Hourly)
    : ComputeFrequency.Manual;

  const spec: SandboxResourceSpec = {};
  (Object.keys(FLOW_RESOURCE_LIMITS) as FlowResourceField[]).forEach((field) => {
    const value = values[field];
    if (typeof value === 'number' && Number.isFinite(value)) {
      spec[field] = FLOW_RESOURCE_LIMITS[field].precision === 0 ? Math.round(value) : value;
    }
  });
  const imageTemplate = asString(values.image_template).trim();
  if (imageTemplate) spec.image_template = imageTemplate;

  return {
    delivery_methods: values.delivery_methods ?? [],
    auto_compute: auto,
    compute_frequency: frequency,
    compute_minute: isInteger(values.compute_minute) ? (values.compute_minute as number) : 0,
    compute_hour: isInteger(values.compute_hour) ? (values.compute_hour as number) : 0,
    compute_tz: asString(values.compute_tz).trim() || DEFAULT_COMPUTE_TZ,
    sandbox_resource_spec: spec,
  };
}

/** 合约上已存的 flow 配置 → 表单初值（未配置字段回落到默认值并如实标注）。 */
export function flowConfigToFormValues(contract: ContractWithFlow | undefined | null): FlowConfigFormValues {
  const spec = contract?.sandbox_resource_spec ?? {};
  return {
    delivery_methods: contract?.delivery_methods ?? [],
    auto_compute: contract?.auto_compute === true,
    compute_frequency: contract?.compute_frequency ?? ComputeFrequency.Manual,
    compute_minute: typeof contract?.compute_minute === 'number' ? contract.compute_minute : 0,
    compute_hour: typeof contract?.compute_hour === 'number' ? contract.compute_hour : 2,
    compute_tz: contract?.compute_tz || DEFAULT_COMPUTE_TZ,
    cpu_cores: typeof spec.cpu_cores === 'number' ? spec.cpu_cores : null,
    memory_mb: typeof spec.memory_mb === 'number' ? spec.memory_mb : null,
    disk_gb: typeof spec.disk_gb === 'number' ? spec.disk_gb : null,
    gpu_count: typeof spec.gpu_count === 'number' ? spec.gpu_count : null,
    timeout_seconds: typeof spec.timeout_seconds === 'number' ? spec.timeout_seconds : null,
    image_template: typeof spec.image_template === 'string' ? spec.image_template : '',
  };
}

export function describeFrequency(frequency: string | null | undefined): string {
  switch (frequency) {
    case ComputeFrequency.Hourly:
      return '每小时';
    case ComputeFrequency.Daily:
      return '每日';
    case ComputeFrequency.Manual:
      return '仅手动';
    default:
      return frequency || '未配置';
  }
}

export function describeDeliveryMethod(method: string): string {
  const hit = DELIVERY_METHOD_OPTIONS.find((option) => option.value === method);
  return hit ? hit.label : method;
}

// ---------------------------------------------------------------------------
// OpenAPI 文档：可读化预览 + curl 示例
// ---------------------------------------------------------------------------

export const HTTP_METHODS = ['get', 'post', 'put', 'patch', 'delete'] as const;

export interface OpenApiOperationRow {
  key: string;
  method: string;
  path: string;
  summary: string;
  parameters: string[];
  bodyFields: { name: string; type: string; required: boolean }[];
  responseCodes: string[];
  requestBodyRequired: boolean;
}

const schemaFieldRows = (schema: Record<string, unknown> | undefined, depth = 0): { name: string; type: string; required: boolean }[] => {
  if (!schema || depth > 1) return [];
  const properties = schema.properties as Record<string, Record<string, unknown>> | undefined;
  if (!properties) return [];
  const required = Array.isArray(schema.required) ? (schema.required as string[]) : [];
  return Object.entries(properties).map(([name, definition]) => {
    const type = asString(definition?.type) || (definition?.$ref ? 'object' : 'any');
    const format = asString(definition?.format);
    return { name, type: format ? `${type}(${format})` : type, required: required.includes(name) };
  });
};

/** 把 OpenAPI paths 摊平成可渲染的行（预览用；只读文档本身，不补任何后端未声明的接口）。 */
export function flattenOpenApiOperations(doc: OpenApiDoc | undefined | null): OpenApiOperationRow[] {
  const paths = doc?.paths;
  if (!paths || typeof paths !== 'object') return [];
  const rows: OpenApiOperationRow[] = [];
  Object.entries(paths).forEach(([path, operations]) => {
    if (!operations || typeof operations !== 'object') return;
    Object.entries(operations).forEach(([method, operation]) => {
      if (!(HTTP_METHODS as readonly string[]).includes(method.toLowerCase())) return;
      const jsonContent = operation?.requestBody?.content?.['application/json'];
      rows.push({
        key: `${method.toLowerCase()} ${path}`,
        method: method.toUpperCase(),
        path,
        summary: asString(operation?.summary) || asString(operation?.operationId),
        parameters: (operation?.parameters ?? [])
          .filter((param) => asString(param.name).length > 0)
          .map((param) => `${asString(param.in) || 'query'}: ${asString(param.name)}${param.required ? '（必填）' : ''}`),
        bodyFields: schemaFieldRows(jsonContent?.schema),
        responseCodes: Object.keys(operation?.responses ?? {}),
        requestBodyRequired: operation?.requestBody?.required === true,
      });
    });
  });
  return rows;
}

const placeholderForType = (type: string): unknown => {
  const base = type.split('(')[0];
  if (base === 'integer') return 0;
  if (base === 'number') return 0;
  if (base === 'boolean') return false;
  if (base === 'array') return [];
  if (base === 'object') return {};
  return '<必填>';
};

/**
 * 生成调用示例的 JSON body：字段全部取自返回文档的 request_schema/paths；
 * 文档里没有 schema 时给出空对象，并让 UI 明确标注"文档未声明请求体"。
 */
export function buildRequestExample(row: OpenApiOperationRow | undefined, endpoint?: ApiEndpoint | null): Record<string, unknown> {
  const fields = row?.bodyFields?.length ? row.bodyFields : schemaFieldRows((endpoint?.request_schema ?? undefined) as Record<string, unknown> | undefined);
  const body: Record<string, unknown> = {};
  fields.forEach((field) => {
    body[field.name] = placeholderForType(field.type);
  });
  return body;
}

/** 外部调用路径固定为 POST /open/v1/{slug}（契约 §5），用平台 API Key 鉴权。 */
export function buildCurlExample(slug: string, body: Record<string, unknown>, baseUrl = window.location.origin): string {
  const json = JSON.stringify(body, null, 2)
    .split('\n')
    .map((line, index) => (index === 0 ? line : `  ${line}`))
    .join('\n');
  const endpoint = `${baseUrl.replace(/\/+$/, '')}/open/v1/${slug}`;
  return [
    `curl -X POST '${endpoint}' \\`,
    `  -H 'Authorization: Bearer <平台 API Key>' \\`,
    `  -H 'Content-Type: application/json' \\`,
    `  -d '${json.replace(/'/g, "'\\''")}'`,
  ].join('\n');
}

/** 端点响应里可能附带的调用量字段名（后端下发哪个就用哪个；都没有则如实说明）。 */
const CALL_COUNT_FIELDS = ['call_count', 'calls', 'request_count', 'calls_total', 'used'] as const;

export function pickCallCount(endpoint: Record<string, unknown> | null | undefined): number | null {
  if (!endpoint) return null;
  for (const field of CALL_COUNT_FIELDS) {
    const value = endpoint[field];
    if (typeof value === 'number' && Number.isFinite(value)) return value;
  }
  return null;
}

export function formatBytes(size: number | null | undefined): string {
  if (size == null || !Number.isFinite(size)) return '-';
  if (size < 1024) return `${size} B`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let value = size / 1024;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  return `${value.toFixed(value >= 100 || value < 10 ? 1 : 0)} ${units[unitIndex]}`;
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString('zh-CN', { hour12: false });
}

// ---------------------------------------------------------------------------
// 端点函数
// ---------------------------------------------------------------------------

export const flowApi = {
  // ---------- 1. 合约交付/计算配置 ----------
  saveFlowConfig: (contractId: string, payload: FlowConfigPayload): Promise<ContractWithFlow> =>
    api.post(`/contracts/${contractId}/flow-config`, payload) as unknown as Promise<ContractWithFlow>,

  // ---------- 1.5 运行时可用性 ----------
  getRuntimeStatus: (): Promise<FlowRuntimeStatus> =>
    api.get('/flow/runtime-status') as unknown as Promise<FlowRuntimeStatus>,

  // ---------- 1.6 首页（原型 A-1）聚合面 ----------
  getHomeOverview: (params?: { days?: number; recent_limit?: number }): Promise<HomeOverview> =>
    api.get('/flow/dashboard/home', {
      params: { days: params?.days ?? HOME_TREND_DAYS, recent_limit: params?.recent_limit ?? HOME_RECENT_ROWS },
    }) as unknown as Promise<HomeOverview>,

  // ---------- 2. Notebooks ----------
  // 一个 POST 建完整个开发任务（响应回显服务端生成的 task_no）。
  createNotebook: (payload: NotebookCreatePayload): Promise<Notebook> =>
    api.post('/notebooks', payload) as unknown as Promise<Notebook>,

  listNotebooks: (params?: NotebookListParams): Promise<PaginatedResponse<Notebook>> =>
    api.get('/notebooks', {
      params: {
        contract_id: params?.contract_id || undefined,
        status: params?.status || undefined,
        page: params?.page ?? 1,
        page_size: params?.page_size ?? 20,
      },
    }) as unknown as Promise<PaginatedResponse<Notebook>>,

  getNotebook: (id: string): Promise<Notebook> =>
    api.get(`/notebooks/${id}`) as unknown as Promise<Notebook>,

  updateNotebook: (id: string, payload: NotebookUpdatePayload): Promise<Notebook> =>
    api.put(`/notebooks/${id}`, payload) as unknown as Promise<Notebook>,

  deleteNotebook: (id: string): Promise<void> =>
    api.delete(`/notebooks/${id}`) as unknown as Promise<void>,

  startDevSession: (id: string): Promise<NotebookDevSession> =>
    api.post(`/notebooks/${id}/dev-session`) as unknown as Promise<NotebookDevSession>,

  // 开发环反代挂在根路径（iframe 带不了 Authorization 头，靠一次性票据换 Cookie，
  // 见 app/api/jupyter_proxy.py）。这里只逐请求覆盖 baseURL，仍走同一个 axios 实例，
  // 鉴权与 401 刷新拦截器不变。
  openJupyterEnv: (id: string): Promise<JupyterEnvTicket> =>
    api.request({ baseURL: '/', url: '/api/jupyter/tickets', method: 'POST', data: { notebook_id: id } }) as unknown as Promise<JupyterEnvTicket>,

  executeCell: (id: string, payload: CellExecutePayload): Promise<CellExecuteResult> =>
    api.post(`/notebooks/${id}/cells/execute`, payload, { timeout: TIMEOUT_CELL }) as unknown as Promise<CellExecuteResult>,

  runNotebook: (id: string): Promise<NotebookRunResult> =>
    api.post(`/notebooks/${id}/run`, undefined, { timeout: TIMEOUT_RUN_ALL }) as unknown as Promise<NotebookRunResult>,

  getJupyterStatus: (id: string): Promise<JupyterStatus> =>
    api.get(`/notebooks/${id}/jupyter`) as unknown as Promise<JupyterStatus>,

  // ---------- 3. Workloads ----------
  createWorkload: (payload: WorkloadCreatePayload): Promise<Workload> =>
    api.post('/workloads', payload) as unknown as Promise<Workload>,

  listWorkloads: (params?: WorkloadListParams): Promise<PaginatedResponse<Workload>> =>
    api.get('/workloads', {
      params: {
        contract_id: params?.contract_id || undefined,
        env: params?.env || undefined,
        status: params?.status || undefined,
        page: params?.page ?? 1,
        page_size: params?.page_size ?? 20,
      },
    }) as unknown as Promise<PaginatedResponse<Workload>>,

  getWorkload: (id: string): Promise<Workload> =>
    api.get(`/workloads/${id}`) as unknown as Promise<Workload>,

  runWorkloadTest: (id: string): Promise<WorkloadTestRun> =>
    api.post(`/workloads/${id}/test`, undefined, { timeout: TIMEOUT_TEST }) as unknown as Promise<WorkloadTestRun>,

  listWorkloadTestRuns: (id: string): Promise<WorkloadTestRun[]> =>
    api.get(`/workloads/${id}/test-runs`).then((payload: unknown) => {
      const source = Array.isArray(payload)
        ? payload
        : Array.isArray((payload as Record<string, unknown>)?.items)
          ? ((payload as Record<string, unknown>).items as unknown[])
          : [];
      return source.map((item) => normalizeTestRun(item));
    }),

  publishWorkload: (id: string): Promise<Workload> =>
    api.post(`/workloads/${id}/publish`) as unknown as Promise<Workload>,

  retireWorkload: (id: string): Promise<Workload> =>
    api.post(`/workloads/${id}/retire`) as unknown as Promise<Workload>,

  rollbackWorkload: (id: string): Promise<Workload> =>
    api.post(`/workloads/${id}/rollback`) as unknown as Promise<Workload>,

  // ---------- 4. 计算调度 ----------
  getSchedule: (contractId?: string | null): Promise<ComputeScheduleList> =>
    api.get('/compute/schedule', { params: { contract_id: contractId || undefined } }) as unknown as Promise<ComputeScheduleList>,

  triggerRun: (contractId: string): Promise<ComputeRun> =>
    api.post('/compute/runs', { contract_id: contractId }, { timeout: TIMEOUT_BUILD }) as unknown as Promise<ComputeRun>,

  listRuns: (params?: ComputeRunListParams): Promise<PaginatedResponse<ComputeRun>> =>
    api.get('/compute/runs', {
      params: {
        contract_id: params?.contract_id || undefined,
        status: params?.status || undefined,
        page: params?.page ?? 1,
        page_size: params?.page_size ?? 20,
      },
    }) as unknown as Promise<PaginatedResponse<ComputeRun>>,

  getRun: (id: string): Promise<ComputeRun> =>
    api.get(`/compute/runs/${id}`) as unknown as Promise<ComputeRun>,

  cancelRun: (id: string): Promise<ComputeRun> =>
    api.post(`/compute/runs/${id}/cancel`) as unknown as Promise<ComputeRun>,

  // ---------- 5. 成果交付 ----------
  buildDatasetBundle: (payload: DatasetBundleRequest): Promise<DatasetBundle> =>
    api.post('/deliveries/dataset/bundle', payload, { timeout: TIMEOUT_BUILD }) as unknown as Promise<DatasetBundle>,

  downloadBundle: (bundleId: string): Promise<Blob> =>
    api.get(`/deliveries/dataset/${bundleId}/download`, { responseType: 'blob', timeout: TIMEOUT_BUILD }) as unknown as Promise<Blob>,

  /**
   * 成果包预览：只回前 N 行，且后端已对片段重跑出口审查（拦截时 403，不给字节）。
   * 它不是下载的廉价替身：未过 T5 审查的历史包会回 409 要求重新生成。
   */
  previewBundle: (bundleId: string, rows = 20): Promise<BundlePreview> =>
    api.get(`/deliveries/dataset/${bundleId}/preview`, { params: { rows } }) as unknown as Promise<BundlePreview>,

  /** 成果包历史（SS-17）：只看得见调用方作为合约当事方的包，归属口径与上面下载同一规则。 */
  listBundles: (params: DatasetBundleListParams = {}): Promise<PaginatedResponse<DatasetBundleRow>> =>
    api.get('/deliveries/dataset/bundles', {
      params: {
        contract_id: params.contract_id || undefined,
        page: params.page ?? 1,
        page_size: params.page_size ?? 20,
      },
    }) as unknown as Promise<PaginatedResponse<DatasetBundleRow>>,

  createWebhook: (payload: WebhookCreatePayload): Promise<WebhookSubscription> =>
    api.post('/deliveries/webhooks', payload) as unknown as Promise<WebhookSubscription>,

  listWebhooks: (contractId?: string | null): Promise<WebhookSubscription[]> =>
    api.get('/deliveries/webhooks', { params: { contract_id: contractId || undefined } }).then((payload: unknown) =>
      toListPayload<WebhookSubscription>(payload).items,
    ),

  updateWebhook: (id: string, payload: WebhookUpdatePayload): Promise<WebhookSubscription> =>
    api.patch(`/deliveries/webhooks/${id}`, payload) as unknown as Promise<WebhookSubscription>,

  deleteWebhook: (id: string): Promise<void> =>
    api.delete(`/deliveries/webhooks/${id}`) as unknown as Promise<void>,

  rotateWebhookSecret: (id: string): Promise<{ secret: string }> =>
    api.post(`/deliveries/webhooks/${id}/rotate-secret`) as unknown as Promise<{ secret: string }>,

  listDeliveries: (id: string, params?: WebhookDeliveryListParams): Promise<PaginatedResponse<WebhookDelivery>> =>
    api.get(`/deliveries/webhooks/${id}/deliveries`, {
      params: {
        status: params?.status || undefined,
        page: params?.page ?? 1,
        page_size: params?.page_size ?? 20,
      },
    }) as unknown as Promise<PaginatedResponse<WebhookDelivery>>,

  redeliver: (id: string, deliveryId: string): Promise<WebhookDelivery> =>
    api.post(`/deliveries/webhooks/${id}/deliveries/${deliveryId}/redeliver`) as unknown as Promise<WebhookDelivery>,

  testWebhook: (id: string): Promise<WebhookTestResult> =>
    api.post(`/deliveries/webhooks/${id}/test`) as unknown as Promise<WebhookTestResult>,

  getContractOpenApiDoc: (contractId: string): Promise<OpenApiDoc> =>
    api.get(`/deliveries/openapi/contract/${contractId}`) as unknown as Promise<OpenApiDoc>,

  createEndpoint: (payload: ApiEndpointCreatePayload): Promise<ApiEndpoint> =>
    api.post('/deliveries/openapi/endpoints', payload) as unknown as Promise<ApiEndpoint>,

  listEndpoints: (contractId?: string | null): Promise<ApiEndpoint[]> =>
    api.get('/deliveries/openapi/endpoints', { params: { contract_id: contractId || undefined } }).then((payload: unknown) =>
      toListPayload<ApiEndpoint>(payload).items,
    ),

  updateEndpoint: (id: string, payload: ApiEndpointUpdatePayload): Promise<ApiEndpoint> =>
    api.patch(`/deliveries/openapi/endpoints/${id}`, payload) as unknown as Promise<ApiEndpoint>,

  deleteEndpoint: (id: string): Promise<void> =>
    api.delete(`/deliveries/openapi/endpoints/${id}`) as unknown as Promise<void>,

  /**
   * 端点调用明细（PRD 3-3 / SS-18 §8 P0-③）。数据源是带 Merkle 锚定的审计日志，后端不另建影子表；
   * 在鉴权层就被拒的请求本来没有日志行，后端不补也不猜，前端同样不补。
   */
  listEndpointCalls: (id: string, params?: ApiEndpointCallListParams): Promise<PaginatedResponse<ApiEndpointCall>> =>
    api.get(`/deliveries/openapi/endpoints/${id}/calls`, {
      params: {
        status: params?.status || undefined,
        page: params?.page ?? 1,
        page_size: params?.page_size ?? 20,
      },
    }) as unknown as Promise<PaginatedResponse<ApiEndpointCall>>,
};

// ---------------------------------------------------------------------------
// react-query：query key + hooks
// ---------------------------------------------------------------------------

/**
 * 首页（原型 A-1「首页-平台总览」）聚合面—— GET /api/v1/flow/dashboard/home 的响应型。
 *
 * 与后端逐字段对齐（app/api/flow_runtime.py::dashboard_home）：口径字段（*_caliber /
 * visibility / source / not_collected）都在型里，页面直接引用它们写注释文案，
 * 不在前端重新发明口径。缺数的字段一律 null，渲染成 UNKNOWN_TEXT。
 */
export interface HomeOverviewScope {
  user_id: string;
  username: string | null;
  organization: string | null;
  role: string | null;
  note: string;
}

export interface HomeOverviewTaskRow {
  id: string;
  task_no: string | null;
  title: string;
  status: string;
  debug_method: string | null;
  created_at: string | null;
}

export interface HomeOverviewCountBlock {
  total: number;
  active: number;
  ended: number;
  status_caliber: Record<string, string>;
}

export interface HomeOverviewInterfaceBlock {
  total: number;
  online: number;
  offline: number;
  status_caliber: Record<string, string>;
}

export interface HomeOverviewCalls {
  count: number;
  failures: number;
  last_called_at: string | null;
  window: string;
  actions: string[];
  source: string;
}

export interface HomeOverviewOpsLog {
  count: number;
  failures: number;
  visibility: 'self' | 'platform';
  window: string;
  source: string;
  note: string;
}

export type HomeMemoryState = 'busy' | 'queued' | 'idle';

export interface HomeMemoryPanel {
  reserved_mb: number;
  active_sessions: number;
  state: HomeMemoryState;
  per_task_ceiling_mb: number;
  /** 实测占用：后端给不出（无 cgroup/psutil 来源），恒为 null。 */
  used_mb: number | null;
  measured: boolean;
  running_runs?: number;
  pending_runs?: number;
}

export interface HomeOverviewMemory {
  quota_mb: number | null;
  quota_source: string;
  debug: HomeMemoryPanel;
  exec: HomeMemoryPanel;
}

export interface HomeTrendPoint {
  date: string;
  debug: number;
  exec: number;
}

export interface HomeOverviewTrend {
  days: HomeTrendPoint[];
  window_days: number;
  series: { debug: string[]; exec: string[] };
  source: string;
  note: string;
}

export type HomeResultSetState = 'normal' | 'abnormal' | null;

export interface HomeResultSetRow {
  bundle_id: string;
  contract_id: string;
  contract_no: string | null;
  file_name: string | null;
  file_count: number | null;
  size_bytes: number | null;
  row_count: number | null;
  created_at: string | null;
  /** 合约下接口累计调用次数；该合约无接口时为 null（口径不适用，不是 0）。 */
  call_count: number | null;
  t5_review_status: string | null;
  watermark_status: string | null;
  state: HomeResultSetState;
  state_reason: string | null;
}

export interface HomeNotCollectedItem {
  key: string;
  label: string;
  reason: string;
}

export interface HomeOverview {
  generated_at: string;
  scope: HomeOverviewScope;
  dev_tasks: HomeOverviewCountBlock;
  task_filters: { all: number; active: number; ended: number };
  recent_tasks: HomeOverviewTaskRow[];
  export_interfaces: HomeOverviewInterfaceBlock;
  calls_today: HomeOverviewCalls;
  ops_log_today: HomeOverviewOpsLog;
  memory: HomeOverviewMemory;
  trend: HomeOverviewTrend;
  latest_result_sets: HomeResultSetRow[];
  call_count_caliber: string;
  not_collected: HomeNotCollectedItem[];
}

export const flowKeys = {
  all: ['flow'] as const,
  runtimeStatus: () => ['flow', 'runtime-status'] as const,
  notebooks: (contractId?: string | null) => ['flow', 'notebooks', contractId ?? null] as const,
  notebook: (id: string) => ['flow', 'notebook', id] as const,
  jupyter: (id: string) => ['flow', 'notebook', id, 'jupyter'] as const,
  workloads: (contractId?: string | null, env?: string | null, status?: string | null) =>
    ['flow', 'workloads', contractId ?? null, env ?? null, status ?? null] as const,
  workload: (id: string) => ['flow', 'workload', id] as const,
  workloadTestRuns: (id: string) => ['flow', 'workload', id, 'test-runs'] as const,
  schedule: (contractId?: string | null) => ['flow', 'compute', 'schedule', contractId ?? null] as const,
  runs: (contractId?: string | null, status?: string | null) => ['flow', 'compute', 'runs', contractId ?? null, status ?? null] as const,
  run: (id: string) => ['flow', 'compute', 'run', id] as const,
  webhooks: (contractId?: string | null) => ['flow', 'delivery', 'webhooks', contractId ?? null] as const,
  deliveries: (id: string, status?: string | null) => ['flow', 'delivery', 'webhook', id, 'deliveries', status ?? null] as const,
  endpoints: (contractId?: string | null) => ['flow', 'delivery', 'endpoints', contractId ?? null] as const,
  endpointCalls: (id?: string | null, status?: string | null, page?: number | null, pageSize?: number | null) =>
    ['flow', 'delivery', 'endpoint', id ?? null, 'calls', status ?? null, page ?? 1, pageSize ?? OPENAPI_CALLS_WINDOW] as const,
  openapi: (contractId: string) => ['flow', 'delivery', 'openapi', contractId] as const,
  bundles: (contractId?: string | null, page?: number | null, pageSize?: number | null) =>
    ['flow', 'delivery', 'bundles', contractId ?? null, page ?? 1, pageSize ?? 20] as const,
  homeOverview: (days?: number | null, recentLimit?: number | null) =>
    ['flow', 'home-overview', days ?? HOME_TREND_DAYS, recentLimit ?? HOME_RECENT_ROWS] as const,
};

type FlowMutationOptions<TRes, TVars> = {
  onSuccess?: (data: TRes, vars: TVars) => void;
  onError?: (error: unknown, vars: TVars) => void;
};

/**
 * 变更类 hook 工厂：成功后失效相关查询，页面只管传业务参数与提示文案。
 * 与页面里裸用 useMutation 的写法等价，集中在此是为了 SS-15 的十余个动作共享同一失效策略。
 */
function createFlowMutationHook<TVars, TRes>(
  mutationFn: (vars: TVars) => Promise<TRes>,
  invalidateKeys?: (vars: TVars) => readonly (readonly unknown[])[]
) {
  return function useFlowActionMutation(options: FlowMutationOptions<TRes, TVars> = {}) {
    const queryClient = useQueryClient();
    return useMutation<TRes, unknown, TVars>({
      mutationFn,
      onSuccess: async (data, vars) => {
        for (const key of invalidateKeys?.(vars) ?? []) {
          await queryClient.invalidateQueries({ queryKey: key });
        }
        options.onSuccess?.(data, vars);
      },
      onError: options.onError,
    });
  };
}

const notebookVars = (id: string) => [flowKeys.notebook(id), flowKeys.notebooks()];

export function useHomeOverview(days = HOME_TREND_DAYS, recentLimit = HOME_RECENT_ROWS) {
  return useQuery({
    queryKey: flowKeys.homeOverview(days, recentLimit),
    queryFn: () => flowApi.getHomeOverview({ days, recent_limit: recentLimit }),
  });
}

// ---------- Notebook hooks ----------

export function useNotebooks(contractId?: string | null, page = 1, pageSize = 50) {
  return useQuery({
    queryKey: flowKeys.notebooks(contractId),
    queryFn: () => flowApi.listNotebooks({ contract_id: contractId, page, page_size: pageSize }),
  });
}

export function useNotebook(id: string | null) {
  return useQuery({
    queryKey: flowKeys.notebook(id ?? ''),
    queryFn: () => flowApi.getNotebook(id as string),
    enabled: !!id,
  });
}

export function useJupyterStatus(id: string | null, refetchIntervalMs?: number) {
  return useQuery({
    queryKey: flowKeys.jupyter(id ?? ''),
    queryFn: () => flowApi.getJupyterStatus(id as string),
    enabled: !!id,
    refetchInterval: refetchIntervalMs,
  });
}

export const useCreateNotebook = createFlowMutationHook(
  (payload: NotebookCreatePayload) => flowApi.createNotebook(payload),
  () => [flowKeys.notebooks()],
);

export const useUpdateNotebook = createFlowMutationHook(
  (vars: { id: string; payload: NotebookUpdatePayload }) => flowApi.updateNotebook(vars.id, vars.payload),
  (vars) => notebookVars(vars.id),
);

export const useDeleteNotebook = createFlowMutationHook(
  (id: string) => flowApi.deleteNotebook(id),
  () => [flowKeys.notebooks()],
);

export const useStartDevSession = createFlowMutationHook(
  (id: string) => flowApi.startDevSession(id),
  (id) => notebookVars(id),
);

export const useOpenJupyterEnv = createFlowMutationHook(
  (id: string) => flowApi.openJupyterEnv(id),
  (id) => notebookVars(id),
);

export const useExecuteCell = createFlowMutationHook(
  (vars: { id: string; payload: CellExecutePayload }) => flowApi.executeCell(vars.id, vars.payload),
  (vars) => [flowKeys.notebook(vars.id)],
);

export const useRunNotebook = createFlowMutationHook(
  (id: string) => flowApi.runNotebook(id),
  (id) => notebookVars(id),
);

// ---------- 合约 flow 配置 hook ----------

export const useSaveFlowConfig = createFlowMutationHook(
  (vars: { contractId: string; payload: FlowConfigPayload }) => flowApi.saveFlowConfig(vars.contractId, vars.payload),
  (vars) => [
    ['contract', vars.contractId],
    ['contracts'],
    flowKeys.schedule(vars.contractId),
    flowKeys.openapi(vars.contractId),
  ],
);

// ---------- Workload hooks ----------

export function useWorkloads(params: { contractId?: string | null; env?: WorkloadEnv; status?: string } = {}, enabled = true) {
  return useQuery({
    queryKey: flowKeys.workloads(params.contractId, params.env, params.status),
    // 上一级维度（如合约）缺失时查询无意义，由调用方关掉，不发无谓请求。
    enabled,
    queryFn: () =>
      flowApi.listWorkloads({
        contract_id: params.contractId,
        env: params.env,
        status: params.status,
        page: 1,
        page_size: 100,
      }),
  });
}

export function useWorkload(id: string | null) {
  return useQuery({
    queryKey: flowKeys.workload(id ?? ''),
    queryFn: () => flowApi.getWorkload(id as string),
    enabled: !!id,
  });
}

export function useWorkloadTestRuns(id: string | null) {
  return useQuery({
    queryKey: flowKeys.workloadTestRuns(id ?? ''),
    queryFn: () => flowApi.listWorkloadTestRuns(id as string),
    enabled: !!id,
  });
}

export const useCreateWorkload = createFlowMutationHook(
  (payload: WorkloadCreatePayload) => flowApi.createWorkload(payload),
  () => [flowKeys.workloads()],
);

export const useRunWorkloadTest = createFlowMutationHook(
  (id: string) => flowApi.runWorkloadTest(id),
  (id) => [flowKeys.workload(id), flowKeys.workloadTestRuns(id), flowKeys.workloads()],
);

export const usePublishWorkload = createFlowMutationHook(
  (id: string) => flowApi.publishWorkload(id),
  (id) => [flowKeys.workload(id), flowKeys.workloads(), flowKeys.endpoints()],
);

export const useRetireWorkload = createFlowMutationHook(
  (id: string) => flowApi.retireWorkload(id),
  (id) => [flowKeys.workload(id), flowKeys.workloads()],
);

export const useRollbackWorkload = createFlowMutationHook(
  (id: string) => flowApi.rollbackWorkload(id),
  () => [flowKeys.workloads()],
);

// ---------- 计算调度 hooks ----------

export function useComputeSchedule(contractId?: string | null) {
  return useQuery({
    queryKey: flowKeys.schedule(contractId),
    queryFn: () => flowApi.getSchedule(contractId),
  });
}

export function useComputeRuns(params: { contractId?: string | null; status?: string } = {}, refetchIntervalMs?: number, enabled = true) {
  return useQuery({
    queryKey: flowKeys.runs(params.contractId, params.status),
    enabled,
    queryFn: () =>
      flowApi.listRuns({
        contract_id: params.contractId,
        status: params.status,
        page: 1,
        page_size: 20,
      }),
    refetchInterval: refetchIntervalMs,
  });
}

export function useComputeRun(id: string | null) {
  return useQuery({
    queryKey: flowKeys.run(id ?? ''),
    queryFn: () => flowApi.getRun(id as string),
    enabled: !!id,
  });
}

export const useTriggerComputeRun = createFlowMutationHook(
  (contractId: string) => flowApi.triggerRun(contractId),
  () => [flowKeys.runs(), flowKeys.schedule()],
);

export const useCancelComputeRun = createFlowMutationHook(
  (id: string) => flowApi.cancelRun(id),
  () => [flowKeys.runs()],
);

// ---------- 交付 hooks ----------

export const useBuildDatasetBundle = createFlowMutationHook(
  (payload: DatasetBundleRequest) => flowApi.buildDatasetBundle(payload),
);

export function useWebhooks(contractId?: string | null) {
  return useQuery({
    queryKey: flowKeys.webhooks(contractId),
    queryFn: () => flowApi.listWebhooks(contractId),
  });
}

export const useCreateWebhook = createFlowMutationHook(
  (payload: WebhookCreatePayload) => flowApi.createWebhook(payload),
  (payload: WebhookCreatePayload) => [flowKeys.webhooks(payload.contract_id)],
);

export const useUpdateWebhook = createFlowMutationHook(
  (vars: { id: string; payload: WebhookUpdatePayload }) => flowApi.updateWebhook(vars.id, vars.payload),
  () => [flowKeys.webhooks()],
);

export const useDeleteWebhook = createFlowMutationHook(
  (id: string) => flowApi.deleteWebhook(id),
  () => [flowKeys.webhooks()],
);

export const useRotateWebhookSecret = createFlowMutationHook(
  (id: string) => flowApi.rotateWebhookSecret(id),
  () => [flowKeys.webhooks()],
);

export function useWebhookDeliveries(id: string | null, params: { status?: string } = {}) {
  return useQuery({
    queryKey: flowKeys.deliveries(id ?? '', params.status),
    queryFn: () => flowApi.listDeliveries(id as string, { status: params.status, page: 1, page_size: 50 }),
    enabled: !!id,
  });
}

export const useRedeliver = createFlowMutationHook(
  (vars: { id: string; deliveryId: string }) => flowApi.redeliver(vars.id, vars.deliveryId),
  (vars) => [flowKeys.deliveries(vars.id)],
);

export const useTestWebhook = createFlowMutationHook(
  (id: string) => flowApi.testWebhook(id),
  (id) => [flowKeys.deliveries(id)],
);

export function useApiEndpoints(contractId?: string | null) {
  return useQuery({
    queryKey: flowKeys.endpoints(contractId),
    queryFn: () => flowApi.listEndpoints(contractId),
  });
}

export const useCreateEndpoint = createFlowMutationHook(
  (payload: ApiEndpointCreatePayload) => flowApi.createEndpoint(payload),
  (payload) => [flowKeys.endpoints(), flowKeys.openapi(payload.contract_id)],
);

export const useUpdateEndpoint = createFlowMutationHook(
  (vars: { id: string; payload: ApiEndpointUpdatePayload }) => flowApi.updateEndpoint(vars.id, vars.payload),
  () => [flowKeys.endpoints()],
);

export const useDeleteEndpoint = createFlowMutationHook(
  (id: string) => flowApi.deleteEndpoint(id),
  () => [flowKeys.endpoints()],
);

/**
 * 接口监控明细：一次取回最近 OPENAPI_CALLS_WINDOW 条（后端 page_size 上界），表格分页在前端做，
 * 这样指标宫格与表格读的是同一个窗口，不会出现"表格翻到第二页、成功率口径却变了"。
 * 调用明细随每次调用增长且不可变，60s 内重复打开抽屉不重复打后端。
 */
export function useEndpointCalls(endpointId: string | null, status?: ApiEndpointCallStatus | null) {
  return useQuery({
    queryKey: flowKeys.endpointCalls(endpointId, status, 1, OPENAPI_CALLS_WINDOW),
    queryFn: () =>
      flowApi.listEndpointCalls(endpointId as string, {
        status: status || undefined,
        page: 1,
        page_size: OPENAPI_CALLS_WINDOW,
      }),
    enabled: !!endpointId,
    staleTime: 60_000,
  });
}

export function useContractOpenApiDoc(contractId: string | null) {
  return useQuery({
    queryKey: flowKeys.openapi(contractId ?? ''),
    queryFn: () => flowApi.getContractOpenApiDoc(contractId as string),
    enabled: !!contractId,
  });
}

export function useFlowRuntimeStatus() {
  return useQuery({
    queryKey: flowKeys.runtimeStatus(),
    queryFn: () => flowApi.getRuntimeStatus(),
    staleTime: 60_000,
  });
}

// ---------------------------------------------------------------------------
// SS-17：成果包历史 / OpenAPI 在线试调用
// ---------------------------------------------------------------------------

/**
 * GET /deliveries/dataset/bundles 的一行。
 *
 * T5 审查状态 / 水印状态 / 数据表行数都是**打包当时落在交付包行上**的结论（SS-18 P0-②），
 * 后端直接下发；这两列上线前包的行与无数据表的包为 null，前端按「未记录」展示，不从包体反推。
 */
export interface DatasetBundleRow {
  bundle_id: string;
  contract_id: string;
  contract_no: string;
  session_id: string | null;
  file_count: number;
  size_bytes: number;
  /** 交付包在对象存储里的文件名（图 2-4「结果集名称」列的唯一真值来源）；旧包可能为 null。 */
  file_name?: string | null;
  row_count?: number | null;
  t5_review_status?: string | null;
  watermark_status?: string | null;
  created_at: string | null;
  download_url: string | null;
}

export interface DatasetBundleListParams {
  contract_id?: string | null;
  page?: number;
  page_size?: number;
}

export function useDatasetBundles(contractId?: string | null, page = 1, pageSize = 10) {
  return useQuery({
    queryKey: flowKeys.bundles(contractId, page, pageSize),
    queryFn: () => flowApi.listBundles({ contract_id: contractId, page, page_size: pageSize }),
  });
}

// ---------- OpenAPI 在线试调用 ----------

/** 响应体最多展示多少字符（超出截断并如实标注，不把整包结果塞进弹窗）。 */
export const OPEN_API_DEBUG_BODY_LIMIT = 8192;

/** 试调用超时：给网关自身限流/超时之前一个能自己收手的边界。 */
export const OPEN_API_DEBUG_TIMEOUT_MS = 30_000;

/** 试调用密钥申请的作用域：网关组的写动词 = POST /open/v1/{slug}（最小权限，不给 full）。 */
export const OPEN_API_DEBUG_KEY_SCOPES: string[] = ['gateway:write'];

export function openApiCallPathForSlug(slug: string): string {
  return `/open/v1/${encodeURIComponent(slug)}`;
}

/** 与后端创建端点的 slug 规则一致，避免把非法路径打到网关上。 */
export function isValidApiSlug(slug: string): boolean {
  return /^[a-z0-9][a-z0-9_-]{1,63}$/.test(slug);
}

export interface OpenApiDebugAttempt {
  /** true = HTTP 2xx。 */
  ok: boolean;
  /** 0 = 请求没能到达后端（网络/代理层失败），不是"后端回了 0"。 */
  httpStatus: number;
  statusText: string;
  latencyMs: number;
  contentType: string | null;
  /** 展示用响应体：JSON 缩进美化，超上限已截断。 */
  bodyText: string;
  bodyTruncated: boolean;
  bodyChars: number;
  /** 传输层失败原因（请求根本没发出去 / 超时中断）；成功时为 null。 */
  transportError: string | null;
  /** 非 2xx 且后端回了 W2 信封时的结构化错误。 */
  errorInfo: FlowErrorInfo | null;
}

function nowMs(): number {
  return typeof performance !== 'undefined' && typeof performance.now === 'function' ? performance.now() : Date.now();
}

function prettifyBody(text: string): string {
  try {
    return JSON.stringify(JSON.parse(text), null, 2);
  } catch {
    return text;
  }
}

/**
 * 用刚创建的**平台 API Key** 直连数据面 `POST /open/v1/{slug}`（SS-17 在线试调用）。
 *
 * 为什么这一条走 fetch 而不是共享 axios 实例：`api.ts` 的请求拦截器无条件把
 * `Authorization` 覆写为登录态 JWT，于是任何经共享实例的请求都是"人以 JWT 调用"，
 * 而不是"机器以 API Key 调用"——那是两条不同的鉴权路径（作用域门禁、按 key 限流、
 * key 停用/过期状态都不会被触发）。用 JWT 试调用后宣称"密钥可用"就是伪造结果，
 * 所以这一个跨凭据的请求必须自己带 `Bearer cds_ak_…`。本页其余调用照旧走共享实例；
 * 这里不新建 axios 实例，也不复用登录态。
 *
 * 返回结构化结果而不是抛错：调试台要如实展示"多少毫秒、什么状态码、后端原文"，
 * 包括失败本身。
 */
export async function callOpenApiWithApiKey(
  slug: string,
  apiKey: string,
  body: unknown,
  timeoutMs = OPEN_API_DEBUG_TIMEOUT_MS
): Promise<OpenApiDebugAttempt> {
  const startedAt = nowMs();
  const elapsed = () => Math.max(0, Math.round(nowMs() - startedAt));
  const base: OpenApiDebugAttempt = {
    ok: false,
    httpStatus: 0,
    statusText: '',
    latencyMs: 0,
    contentType: null,
    bodyText: '',
    bodyTruncated: false,
    bodyChars: 0,
    transportError: null,
    errorInfo: null,
  };
  if (!isValidApiSlug(slug)) {
    return { ...base, transportError: `slug「${slug}」不符合后端规则（小写字母、数字、下划线与短横线，长度 2~64），未发起调用。` };
  }
  if (!apiKey) {
    return { ...base, transportError: '还没有可用的平台 API Key：请先创建调试密钥再发起调用。' };
  }

  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(openApiCallPathForSlug(slug), {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${apiKey}`,
      },
      body: JSON.stringify(body ?? {}),
      signal: controller.signal,
    });
    const raw = await response.text();
    const truncated = raw.length > OPEN_API_DEBUG_BODY_LIMIT;
    const shown = truncated ? raw.slice(0, OPEN_API_DEBUG_BODY_LIMIT) : raw;
    let errorInfo: FlowErrorInfo | null = null;
    if (!response.ok) {
      let envelope: unknown = raw;
      try {
        envelope = JSON.parse(raw) as unknown;
      } catch {
        envelope = raw;
      }
      errorInfo = parseFlowError(envelope);
      errorInfo.httpStatus = response.status;
    }
    return {
      ...base,
      ok: response.ok,
      httpStatus: response.status,
      statusText: response.statusText,
      latencyMs: elapsed(),
      contentType: response.headers.get('content-type'),
      bodyText: prettifyBody(shown),
      bodyTruncated: truncated,
      bodyChars: raw.length,
      errorInfo,
    };
  } catch (error) {
    const aborted = typeof error === 'object' && error !== null && (error as { name?: string }).name === 'AbortError';
    const reason = error instanceof Error ? error.message : String(error);
    return {
      ...base,
      latencyMs: elapsed(),
      transportError: aborted
        ? `调用超过 ${Math.round(timeoutMs / 1000)}s 未返回，已中断（网关侧可能仍在处理）。`
        : `请求未能到达后端：${reason}。开发态需 vite 代理转发 /open，部署态需前端 nginx 同样反代 /open；两者都没转发时就是这一行。`,
    };
  } finally {
    window.clearTimeout(timer);
  }
}

