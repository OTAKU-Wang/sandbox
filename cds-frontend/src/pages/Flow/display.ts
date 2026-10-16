// SS-15 流通编排页的局部纯函数与状态元数据（页面互不引用约定下，仅供 pages/Flow/* 使用）。
// 状态标签一律"后端下发什么就映射什么"，未登记的取值原样展示，不做美化或补全。

import {
  ComputeRunStatus,
  DeliveryStatus,
  NOTEBOOK_BACKENDS,
  PUBLISH_GATE_CODES,
  WorkloadStatus,
  describePublishGateCode,
} from '../../services/flow';
import type { CellExecuteResult, Notebook, Workload, WorkloadTestRun } from '../../services/flow';

export interface StatusMeta {
  color: string;
  label: string;
}

export const WORKLOAD_STATUS_META: Record<string, StatusMeta> = {
  [WorkloadStatus.Draft]: { color: 'default', label: '草稿' },
  [WorkloadStatus.Testing]: { color: 'blue', label: '测试中' },
  [WorkloadStatus.TestPassed]: { color: 'green', label: '测试通过' },
  [WorkloadStatus.TestFailed]: { color: 'red', label: '测试失败' },
  [WorkloadStatus.Published]: { color: 'purple', label: '已发布' },
  [WorkloadStatus.Retired]: { color: 'default', label: '已退役' },
};

export const COMPUTE_RUN_STATUS_META: Record<string, StatusMeta> = {
  [ComputeRunStatus.Pending]: { color: 'blue', label: '排队中' },
  [ComputeRunStatus.Running]: { color: 'processing', label: '执行中' },
  [ComputeRunStatus.Succeeded]: { color: 'green', label: '成功' },
  [ComputeRunStatus.Failed]: { color: 'red', label: '失败' },
  [ComputeRunStatus.SkippedOverlap]: { color: 'orange', label: '跳过（上一轮未结束）' },
  [ComputeRunStatus.Cancelled]: { color: 'default', label: '已取消' },
};

export const DELIVERY_STATUS_META: Record<string, StatusMeta> = {
  [DeliveryStatus.Pending]: { color: 'blue', label: '待投递' },
  [DeliveryStatus.Delivering]: { color: 'processing', label: '投递中' },
  [DeliveryStatus.Delivered]: { color: 'green', label: '已送达' },
  [DeliveryStatus.Failed]: { color: 'red', label: '投递失败' },
  [DeliveryStatus.Dead]: { color: '#8c0f28', label: '死信（重试耗尽）' },
};

export const WEBHOOK_STATUS_META: Record<string, StatusMeta> = {
  active: { color: 'green', label: '启用中' },
  paused: { color: 'orange', label: '已暂停' },
};

export const ENDPOINT_STATUS_META: Record<string, StatusMeta> = {
  enabled: { color: 'green', label: '已启用' },
  disabled: { color: 'default', label: '已停用' },
};

export const WORKLOAD_ENV_META: Record<string, StatusMeta> = {
  test: { color: 'blue', label: '测试环境' },
  production: { color: 'purple', label: '生产环境' },
};

export function statusMeta(table: Record<string, StatusMeta>, value: string | null | undefined): StatusMeta {
  if (!value) return { color: 'default', label: '-' };
  return table[value] ?? { color: 'default', label: value };
}

// ---------- 执行后端（Notebook 页顶部常驻指示器） ----------

export interface BackendView {
  backend: string;
  label: string;
  color: string;
  stateful: boolean;
  degraded: boolean;
  reasons: string[];
  known: boolean;
}

/**
 * 执行后端视图：cds_kernel=有状态内核，cds_stateless=降级为无状态执行。
 * 降级原因优先取 degraded_reason，其次 kernel_error；三者都没有时明确说明后端未给出原因，
 * 不编造解释（禁伪造铁律：只披露下发的事实）。未登记的后端标识同样不推断为降级。
 */
export function resolveBackendView(input: {
  backend?: string | null;
  stateful?: boolean | null;
  degraded_reason?: string | null;
  kernel_error?: string | null;
  blocked_reason?: string | null;
}): BackendView {
  const backend = input.backend?.trim() || 'unknown';
  const isKernel = backend === NOTEBOOK_BACKENDS.Kernel;
  const isStateless = backend === NOTEBOOK_BACKENDS.Stateless;
  const stateful = typeof input.stateful === 'boolean' ? input.stateful : isKernel;
  const degraded = isStateless || input.stateful === false;

  const reasons: string[] = [];
  if (input.degraded_reason?.trim()) reasons.push(input.degraded_reason.trim());
  if (input.kernel_error?.trim()) reasons.push(input.kernel_error.trim());
  if (degraded && reasons.length === 0) {
    reasons.push('后端未返回降级原因（degraded_reason/kernel_error 为空）');
  }

  const label = isKernel ? '有状态内核' : isStateless ? '降级：无状态执行' : backend;
  const color = isKernel ? 'green' : isStateless ? 'orange' : 'default';
  return { backend, label, color, stateful, degraded, reasons, known: isKernel || isStateless };
}

export function backendFromCellResult(result: CellExecuteResult): BackendView {
  return resolveBackendView(result);
}

export function backendFromNotebook(notebook: Notebook | undefined | null): BackendView | null {
  if (!notebook?.execution_backend) return null;
  return resolveBackendView({ backend: notebook.execution_backend });
}

// ---------- 输出面板 ----------

export interface OutputPaneModel {
  stdout: string;
  stderr: string;
  repr: string;
  blocked: boolean;
  blockedReason: string | null;
  exitCode: number | null;
  hasContent: boolean;
}

export function toOutputPane(result: CellExecuteResult | null | undefined): OutputPaneModel | null {
  if (!result) return null;
  const pane: OutputPaneModel = {
    stdout: result.stdout ?? '',
    stderr: result.stderr ?? '',
    repr: result.repr ?? '',
    blocked: result.output_blocked === true,
    blockedReason: result.blocked_reason?.trim() || null,
    exitCode: typeof result.exit_code === 'number' ? result.exit_code : null,
    hasContent: false,
  };
  pane.hasContent = !!(pane.stdout || pane.stderr || pane.repr || pane.blocked);
  return pane;
}

// ---------- 发布门（与后端 412 判定同构，前端只用于禁用按钮 + 说明原因） ----------

export interface PublishGate {
  canPublish: boolean;
  reason: string | null;
  warning: string | null;
}

const GATE_HINT: Record<string, string> = {
  [PUBLISH_GATE_CODES.TestRequired]: '尚未有测试通过的测试记录',
  [PUBLISH_GATE_CODES.TestNotPassed]: '最近一次测试未通过',
  [PUBLISH_GATE_CODES.HashMismatch]: '负载内容与通过测试时不一致',
};

export function publishGateReason(code: string | null | undefined): string {
  if (!code) return '发布被拒绝';
  const human = describePublishGateCode(code);
  if (human) return human;
  return GATE_HINT[code] ?? `发布被拒绝（${code}）`;
}

/**
 * 发布门：只有测试通过且仍是最新版本、环境为 test 的负载才允许发布。
 * 后端仍是权威判定方（412 + 错误码），这里只为让按钮提前禁用并给出中文原因。
 */
export function evaluatePublishGate(workload: Workload | undefined | null, latestRun?: WorkloadTestRun | null): PublishGate {
  if (!workload) return { canPublish: false, reason: '未选择负载', warning: null };

  if (workload.env === 'production') {
    return { canPublish: false, reason: '该记录已是生产版本，无需重复发布', warning: null };
  }
  if (workload.status === WorkloadStatus.Retired) {
    return { canPublish: false, reason: '负载已退役，如需上线请回滚或新建负载后测试发布', warning: null };
  }
  if (workload.status === WorkloadStatus.Published) {
    return { canPublish: false, reason: '负载已发布为生产版本', warning: null };
  }

  const runPassed = latestRun ? latestRun.passed === true : null;
  const runFailed = latestRun ? latestRun.passed === false : null;

  if (workload.status === WorkloadStatus.TestFailed || runFailed) {
    return { canPublish: false, reason: publishGateReason(PUBLISH_GATE_CODES.TestNotPassed), warning: null };
  }

  const hasPassingRun = runPassed === true || workload.status === WorkloadStatus.TestPassed;
  if (!hasPassingRun) {
    return { canPublish: false, reason: publishGateReason(PUBLISH_GATE_CODES.TestRequired), warning: null };
  }

  if (workload.status === WorkloadStatus.Testing) {
    return { canPublish: false, reason: '测试正在执行中，请等待本次测试结束后再发布', warning: null };
  }

  const warning =
    latestRun && workload.test_run_id && workload.test_run_id !== latestRun.test_run_id
      ? '负载记录的测试编号与最新测试记录不一致，发布可能被判定为 ' + PUBLISH_GATE_CODES.HashMismatch + '，建议重新发起测试。'
      : null;

  return { canPublish: true, reason: null, warning };
}

export function checkSummary(run: WorkloadTestRun | null | undefined): { total: number; failed: string[] } {
  if (!run) return { total: 0, failed: [] };
  const failed = run.checks.filter((check) => check.passed !== true).map((check) => check.name);
  return { total: run.checks.length, failed };
}

// ---------- 投递日志 / HTTP 响应码 ----------

export function responseStatusColor(status: number | null | undefined): string {
  if (status == null) return 'default';
  if (status >= 200 && status < 300) return 'green';
  if (status >= 300 && status < 400) return 'blue';
  if (status >= 400 && status < 500) return 'orange';
  return 'red';
}

export function attemptLabel(attempt: number | null | undefined, maxAttempts: number | null | undefined): string {
  if (typeof attempt !== 'number') return '-';
  return typeof maxAttempts === 'number' ? `${attempt}/${maxAttempts}` : `${attempt}`;
}

export function openApiPathForSlug(slug: string): string {
  return `/open/v1/${slug}`;
}
