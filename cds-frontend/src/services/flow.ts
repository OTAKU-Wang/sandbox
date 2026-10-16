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
}

export interface NotebookCreatePayload {
  title: string;
  path: string;
  contract_id?: string | null;
  session_id?: string | null;
  content?: string;
}

export interface NotebookUpdatePayload {
  title?: string;
  content?: string;
  session_id?: string | null;
}

export interface NotebookListParams {
  contract_id?: string | null;
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

export interface JupyterStatus {
  jupyter_available: boolean;
  jupyter_path: string | null;
  proxy_enabled: boolean;
  url: string | null;
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
}

export interface ApiEndpointCreatePayload {
  contract_id: string;
  workload_id?: string | null;
  name: string;
  slug: string;
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
    '当前部署未启用 MicroVM 沙箱数据面，无法真实执行代码：需先完成 Cube 计算节点部署（节点需 /dev/kvm）并开启 CDS_CUBE_DATAPLANE_ENABLED。',
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
  const slug = title
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9\u4e00-\u9fa5]+/g, '-')
    .replace(/^-+|-+$/g, '');
  return `/notebooks/${slug || 'untitled'}.ipynb`;
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

  // ---------- 2. Notebooks ----------
  createNotebook: (payload: NotebookCreatePayload): Promise<Notebook> =>
    api.post('/notebooks', payload) as unknown as Promise<Notebook>,

  listNotebooks: (params?: NotebookListParams): Promise<PaginatedResponse<Notebook>> =>
    api.get('/notebooks', {
      params: {
        contract_id: params?.contract_id || undefined,
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
};

// ---------------------------------------------------------------------------
// react-query：query key + hooks
// ---------------------------------------------------------------------------

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
  openapi: (contractId: string) => ['flow', 'delivery', 'openapi', contractId] as const,
  bundles: (contractId?: string | null, page?: number | null, pageSize?: number | null) =>
    ['flow', 'delivery', 'bundles', contractId ?? null, page ?? 1, pageSize ?? 20] as const,
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

export function useWorkloads(params: { contractId?: string | null; env?: WorkloadEnv; status?: string } = {}) {
  return useQuery({
    queryKey: flowKeys.workloads(params.contractId, params.env, params.status),
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

export function useComputeRuns(params: { contractId?: string | null; status?: string } = {}, refetchIntervalMs?: number) {
  return useQuery({
    queryKey: flowKeys.runs(params.contractId, params.status),
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
    enabled: !!contractId,
  });
}

export const useCreateEndpoint = createFlowMutationHook(
  (payload: ApiEndpointCreatePayload) => flowApi.createEndpoint(payload),
  () => [flowKeys.endpoints()],
);

export const useUpdateEndpoint = createFlowMutationHook(
  (vars: { id: string; payload: ApiEndpointUpdatePayload }) => flowApi.updateEndpoint(vars.id, vars.payload),
  () => [flowKeys.endpoints()],
);

export const useDeleteEndpoint = createFlowMutationHook(
  (id: string) => flowApi.deleteEndpoint(id),
  () => [flowKeys.endpoints()],
);

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
 * 只列交付包表已落库的字段：水印标志与逐文件出口审查结论写在包内 MANIFEST.json 里、
 * 不在这个行上，后端不下发、前端也不猜（列表里的水印列如实标注"未下发"）。
 */
export interface DatasetBundleRow {
  bundle_id: string;
  contract_id: string;
  contract_no: string;
  session_id: string | null;
  file_count: number;
  size_bytes: number;
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

