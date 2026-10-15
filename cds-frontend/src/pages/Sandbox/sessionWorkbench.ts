// PZ-16（会话工作台增强）— SessionDetail 局部工具函数。
// 纯函数，无副作用，便于测试与复用。

import type { CapabilityMatrix } from '../../services/metaApi';

/** 能力矩阵不可用时的流式白名单回退（当前代码事实：session_stream.py 仅 L0/k8s 可流式）。 */
export const WS_STREAM_FALLBACK_LEVELS: readonly string[] = ['L0', 'k8s'];

/** 该级别是否支持 WS 流式执行：优先消费 PZ-13 能力矩阵（stream_ws=real），
 *  矩阵缺失/未加载时回退到 L0/k8s 白名单。 */
export function supportsWsStreaming(level: string, matrix?: CapabilityMatrix | null): boolean {
  const cell = matrix?.matrix?.[level]?.stream_ws;
  if (cell) return cell.state === 'real';
  return WS_STREAM_FALLBACK_LEVELS.includes(level);
}

/** 毫秒 → 人类可读时长（状态条步骤耗时）。 */
export function formatDuration(ms?: number | null): string {
  if (ms === undefined || ms === null || Number.isNaN(ms) || ms < 0) return '-';
  if (ms < 1000) return `${Math.round(ms)}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  const totalSec = Math.floor(ms / 1000);
  const m = Math.floor(totalSec / 60);
  const s = totalSec % 60;
  if (m < 60) return s > 0 ? `${m}m ${s}s` : `${m}m`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

/** 审计日志行级 T5 审查状态（PZ-16 ①）：
 *  依据 detail 中的 inspection 字段（sandbox.exec / sandbox.output_inspected）
 *  判定 passed / redacted / blocked；字段缺失时返回 null（不高亮）。 */
export type RowReviewStatus = 'passed' | 'redacted' | 'blocked';

export function rowReviewStatus(detail: unknown): RowReviewStatus | null {
  if (!detail || typeof detail !== 'object') return null;
  const d = detail as Record<string, unknown>;
  if (d.blocked === true || d.output_blocked === true) return 'blocked';
  const findings = d.findings_count;
  if (typeof findings === 'number' && findings > 0) return 'redacted';
  if (d.redacted === true || d.redacted_output != null) return 'redacted';
  if (d.passed === true) return 'passed';
  return null;
}

/** 会话状态元数据（颜色/中文标签）— 会话页局部共享，避免跨页 import（pages 互不引用约定）。 */
export const SESSION_STATUS_META: Record<string, { color: string; label: string }> = {
  pending: { color: 'blue', label: '等待中' },
  key_distributing: { color: 'cyan', label: '密钥分发中' },
  ready: { color: 'green', label: '已就绪' },
  provisioning: { color: 'blue', label: '创建中' },
  running: { color: 'green', label: '运行中' },
  suspended: { color: 'orange', label: '已暂停' },
  completed: { color: 'purple', label: '已完成' },
  failed: { color: 'red', label: '失败' },
  terminated: { color: 'default', label: '已终止' },
  revoked: { color: 'red', label: '已撤销' },
  expired: { color: 'default', label: '已过期' },
};

// ---------- W2 错误契约（{code, message, detail, request_id}） ----------

export interface W2Error {
  code?: string;
  message: string;
  detail?: unknown;
  requestId?: string;
  status?: number;
}

/** 从 react-query error 中解析 W2 载荷。
 *  共享 axios 拦截器以 `error.response?.data` 拒绝，故错误体即 W2 对象
 *  （legacy HTTPException 包装为 code=HTTP_{status} + message=str(detail)）。 */
export function parseW2Error(error: unknown): W2Error {
  const base: W2Error = { message: '未知错误' };
  if (!error) return base;
  if (error instanceof Error) {
    base.message = error.message || base.message;
    const ex = error as Error & { status?: number; detail?: unknown };
    if (typeof ex.status === 'number') base.status = ex.status;
    if (ex.detail !== undefined) base.detail = ex.detail;
    return base;
  }
  if (typeof error === 'string') return { message: error };
  if (typeof error === 'object') {
    const e = error as Record<string, unknown>;
    base.message = typeof e.message === 'string' && e.message ? e.message : base.message;
    if (typeof e.code === 'string') base.code = e.code;
    if (e.detail !== undefined) base.detail = e.detail;
    if (typeof e.request_id === 'string') base.requestId = e.request_id;
    if (typeof e.status === 'number') base.status = e.status;
    if (typeof e.code === 'string' && e.code.startsWith('HTTP_') && /^\d{3}$/.test(e.code.slice(5))) {
      base.status = Number(e.code.slice(5));
    }
    // detail 可能是 legacy 字符串（如 "Sandbox session not found"）
    if (typeof e.detail === 'string' && e.detail && !e.message) base.message = e.detail;
  }
  return base;
}

