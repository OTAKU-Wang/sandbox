import dayjs from 'dayjs';
import type { AuditRecord } from '../../services/auditApi';

export const UNKNOWN_TEXT = '未记录';

export interface AuditTab {
  key: string;
  label: string;
  color: string;
}

/**
 * 4-1 记录区标题右侧的五个类型页签（原型逐字）。key 必须与后端
 * app/api/audit.py::AUDIT_EVENT_TYPES 的键一致：页签筛选走 event_type，
 * 动作集合由后端单源持有，前端不再抄一份 action 清单。
 */
export const AUDIT_TABS: AuditTab[] = [
  { key: 'dev_sandbox', label: '创建调试沙箱', color: 'blue' },
  { key: 'production', label: '进入生产环境', color: 'volcano' },
  { key: 'export', label: '结果集导出', color: 'green' },
  { key: 'call', label: '数据调用', color: 'purple' },
];

/**
 * 页签之外真实存在的动作 → 中文展示名。只列后端确实写入的 action
 * （tests/test_audit_query_filters.py 把 AUDIT_EVENT_TYPES 与代码词表对齐），
 * 表里没有的一律原样显示动作代码，不给它编一个中文名。
 *
 * 后补的 5 个是 4-1「操作类型」下拉里此前没有中文键的动作（下拉不带 event_type，
 * 取不到页签文案，于是渲染成「code (code)」原文重复）：每个都在 app/ 里有真写入点，
 * 中文名按写入点语义命名 —— notebook.dev_session_created（notebooks.py::ensure_dev_session，
 * 为开发任务建调试沙箱会话）、compute.run.requested（compute.py::request_run，计算运行）、
 * sandbox.execute（sandbox_sessions.py::execute_in_sandbox / sandbox_runtime.py）、
 * delivery.dataset.downloaded（deliveries.py 出交付包 zip）、openapi.call（open_api.py 数据面调用）。
 */
const ACTION_LABELS: Record<string, string> = {
  'user.login': '登录',
  'user.login_failed': '登录失败',
  'user.register': '注册',
  'user.role_changed': '角色变更',
  'data_resource.upload': '数据上传',
  'data_product.create': '产品创建',
  'data_product.submit': '产品送审',
  'data_product.approve': '产品审批',
  'data_product.publish': '产品上架',
  'contract.create': '合约创建',
  'contract.sign': '合约签署',
  'contract.flow_configured': '合约配置',
  'compute.run.requested': '发起计算运行',
  'notebook.created': '创建开发任务',
  'notebook.dev_session_created': '创建调试会话',
  'notebook.cell_executed': '执行代码单元',
  'notebook.jupyter_ticket': '获取调试环境票据',
  'sandbox.execute': '执行沙箱代码',
  'sandbox.output_inspected': '输出检查',
  'sandbox.code_scan_rejected': '代码扫描拒绝',
  'delivery.dataset.downloaded': '下载交付包',
  'openapi.call': 'OpenAPI 调用',
  'session.expired_cleanup': '会话到期清理',
  'retention.purge': '留存清理',
};

/**
 * 4-1 列表、4-2 弹窗标题与基础信息共用的时间口径：原型十行同形
 * `2026-08-12 10:42:13`（连字符 + 补零 + 秒级），不是 toLocaleString 的 `2026/8/12 10:42:13`。
 * 只在展示层格式化，服务层字段一个不动；拿不到合法时间就返回 null 让调用方走「未记录」。
 */
export function auditTimeText(value?: string | null): string | null {
  if (!value) return null;
  const parsed = dayjs(value);
  return parsed.isValid() ? parsed.format('YYYY-MM-DD HH:mm:ss') : null;
}

export function auditTabOf(record: Pick<AuditRecord, 'event_type'>): AuditTab | null {
  if (!record.event_type) return null;
  return AUDIT_TABS.find((tab) => tab.key === record.event_type) ?? null;
}

/** 操作类型列与详情标题右侧标签共用的展示名（标签名优先取页签文案）。 */
export function auditActionLabel(record: Pick<AuditRecord, 'action' | 'event_type'>): string {
  const tab = AUDIT_TABS.find((item) => item.key === record.event_type);
  if (tab) return tab.label;
  return ACTION_LABELS[record.action] ?? record.action;
}

export function auditActionColor(record: Pick<AuditRecord, 'action' | 'event_type'>): string {
  const tab = auditTabOf(record);
  if (tab) return tab.color;
  if (record.action.endsWith('_failed') || record.action.endsWith('.blocked') || record.action.endsWith('_rejected')) return 'red';
  return 'default';
}

/** 详情弹窗里「操作类型」用 `CODE (中文名)`，与原型 EXEC_ENV_ENTER (进入生产环境) 同形。 */
export function auditActionCodeText(record: Pick<AuditRecord, 'action' | 'event_type'>): string {
  const label = auditActionLabel(record);
  return label === record.action ? record.action : `${record.action} (${label})`;
}

/** detail 里真实存在的环境字段（后端无独立环境列，只有部分动作在 detail 带这些键）。 */
const ENV_DETAIL_KEYS = ['sandbox_level', 'sandbox_mode', 'cube_node_id', 'env', 'runtime'] as const;

export function auditEnvText(detail: Record<string, unknown> | null): string | null {
  if (!detail) return null;
  const parts = ENV_DETAIL_KEYS
    .map((key) => detail[key])
    .filter((value) => typeof value === 'string' || typeof value === 'number')
    .map((value) => String(value));
  return parts.length > 0 ? parts.join(' · ') : null;
}

/** detail 里真实存在的关联数据（如 jupyter 票据注入清单、显式的数据名列表）。 */
const DATA_DETAIL_KEYS = ['injected', 'data_resource_names', 'data_resources', 'resource_names'] as const;

export function auditLinkedData(detail: Record<string, unknown> | null): string[] | null {
  if (!detail) return null;
  for (const key of DATA_DETAIL_KEYS) {
    const value = detail[key];
    if (Array.isArray(value) && value.length > 0) {
      return value.map((item) => (typeof item === 'string' ? item : JSON.stringify(item)));
    }
  }
  return null;
}

/** 只有 detail 真带 duration_ms 时才有时长，单位换算不改变"有没有这个数"这件事。 */
export function auditDurationText(detail: Record<string, unknown> | null): string | null {
  const value = detail?.duration_ms;
  if (typeof value !== 'number' || Number.isNaN(value)) return null;
  return value >= 1000 ? `${(value / 1000).toFixed(2)}s` : `${value}ms`;
}
