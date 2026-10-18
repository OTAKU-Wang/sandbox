// 2-4 任务详情的共享夹具：字段名一律对齐 `services/flow` 的 `Notebook` / `DatasetBundleRow`。
// NOTEBOOK 的 task_no / debug_method / export_mode / description 故意全为 null：
// 存量行就是 null，页面得写「未记录」而不是默认值。
import type { DatasetBundleRow, Notebook } from '../../../services/flow';

export const emptyPage = { items: [], total: 0, page: 1, page_size: 10 };

export const NOTEBOOK: Notebook = {
  id: 'n-1',
  contract_id: 'c-1',
  title: '销售分析',
  path: '/nb/sales.ipynb',
  kernel: null,
  language: null,
  content: null,
  version: 1,
  status: 'draft',
  session_id: null,
  execution_backend: null,
  last_executed_at: null,
  created_at: '2026-10-16T09:12:00Z',
  updated_at: '2026-10-16T09:12:00Z',
  task_no: null,
  debug_method: null,
  export_mode: null,
  debug_memory_mb: 8192,
  exec_memory_mb: 16384,
  description: null,
  data_resource_ids: null,
};

/** 合约选项：交付方式与使用场景取自合约（关联数据表那两列的唯一来源）。 */
export const CONTRACT = {
  id: 'c-1',
  contract_no: 'CT-2026-0001',
  title: '零售流水合约',
  status: 'active',
  purpose: '模型训练与效果验证',
  delivery_methods: ['API 导出'],
  product_ids: ['p-1'] as string[] | null,
};

/** 已有 T5/水印结论的包：行数据与断言里的「36 行」同源；file_name 是图 2-4「结果集名称」的唯一真值来源。 */
export const BUNDLE_PACKAGED: DatasetBundleRow = {
  bundle_id: 'db_packaged',
  contract_id: 'c-1',
  contract_no: 'CT-2026-0001',
  session_id: 's-1',
  file_count: 2,
  size_bytes: 20480,
  file_name: 'user_behavior_result_20260812.csv',
  row_count: 36,
  t5_review_status: 'passed',
  watermark_status: 'applied',
  created_at: '2026-10-16T12:30:00Z',
  download_url: null,
};

/** 早于 T5/水印两列上线、也没有 file_name 的包：页面退回 bundle_id，不拿旧包猜一个文件名。 */
export const BUNDLE_EMPTY: DatasetBundleRow = {
  bundle_id: 'db_empty',
  contract_id: 'c-1',
  contract_no: 'CT-2026-0001',
  session_id: null,
  file_count: 1,
  size_bytes: 1024,
  file_name: null,
  row_count: 8,
  t5_review_status: null,
  watermark_status: null,
  created_at: '2026-10-15T08:00:00Z',
  download_url: null,
};

/** 数据产品载荷：关联数据表每一列都从这份真载荷取（产品类型/供应方 id）。 */
export const PRODUCT = {
  id: 'p-1',
  name: '零售交易明细',
  description: '脱敏后的门店流水',
  product_type: 'structured',
  industry: '零售',
  status: 'published',
  provider_id: 'u-2',
  resource_id: 'r-1',
  row_count: 1200,
  data_schema: null,
  security_level: 'internal',
  allowed_operations: null,
  output_constraints: null,
  created_at: '2026-09-01T00:00:00Z',
  updated_at: '2026-09-01T00:00:00Z',
};

/** GET /users/options：组织名称列的唯一来源（provider_id → organization）。 */
export const USER_OPTIONS = [
  { id: 'u-1', username: 'buyer', role: 'buyer', organization: '沈阳数据中心R' },
  { id: 'u-2', username: 'provider', role: 'data_provider', organization: '沈阳数据中心R' },
];

/** GET /sandbox-sessions?status=running 的一行：本任务真挂着的那个开发会话。 */
export const RUNNING_SESSION = {
  id: 's-1',
  user_id: 'u-1',
  data_product_id: 'p-1',
  contract_id: 'c-1',
  sandbox_level: 'L3',
  sandbox_mode: 'product_dev',
  status: 'running',
  container_id: 'bwrap-s-1',
  session_key_id: 'k-1',
  timeout_seconds: 3600,
  resource_limits: { cpu_cores: 1, memory_mb: 512 },
  error_message: null,
  started_at: null,
  ended_at: null,
  created_at: '2026-10-17T08:00:00Z',
  updated_at: '2026-10-17T08:00:00Z',
};

/**
 * GET /flow/dashboard/home 的关键段：内存实测占用后端自己说没采样源，
 * 详情页「资源使用情况」的 tooltip 直接引这段话。
 */
export const HOME_MEMORY_UNMEASURED = {
  generated_at: '2026-10-17T08:00:00Z',
  memory: {
    quota_mb: 8192,
    quota_source: 'sandbox_manager 租户内存配额',
    debug: { reserved_mb: 512, active_sessions: 1, state: 'busy', per_task_ceiling_mb: 32768, used_mb: null, measured: false },
    exec: { reserved_mb: 0, active_sessions: 0, state: 'idle', per_task_ceiling_mb: 65536, used_mb: null, measured: false },
  },
  not_collected: [
    {
      key: 'memory.measured',
      label: '沙箱内存实测占用（RSS/cgroup）',
      reason: '后端没有任何实测内存来源：无 cgroup/psutil 读取，Cube 节点 RPC 只暴露 get_storage_metrics（存储字节）。',
    },
  ],
};

export const pageOf = <T,>(items: T[]) => ({ items, total: items.length, page: 1, page_size: 100 });
