// SS-23 「调试环境」（原型图 1 / 2-3）外框的纯逻辑层。
//
// 这里只放**可单测的纯函数**与逐字文案常量：外框的每一个槽位都要么有后端真值，要么
// 明确降级成「未计量 / 未接入 / 未记录」，判据全部集中在这里，页面只负责摆位置。
//
// 铁律（项目 honest degradation / 禁伪造）：
// - 没有任何后端字段支撑的数字（CPU 采样、内存已用、沙箱内 Python 版本、24 小时保留期、
//   2 GB 工作区配额）一律不上屏；配额侧用后端真给的口径并在 tooltip 里写清 scope。
// - 状态文案只映射后端真实值，未知值原样透出（与 Flow/display.ts 同一立场）。
import type { SessionLogEntry } from '../../services/sandboxApi';
import type { JupyterContentEntry } from '../../services/jupyterContents';
import { UNKNOWN_TEXT } from '../../services/flow';

/** 图 1 §2 标题卡逐字文案（副标题是产品口径，不随任务字段变化）。 */
export const NBW_TITLE = '调试环境';
export const NBW_SUBTITLE = '交付场景 / 数据沙箱开发 · 配置合约、资源与关联数据后即可启动调试';
export const NBW_BACK = '返回列表';
/** 图 1 §3.1 面板标题。 */
export const NBW_FILE_PANEL = '文件目录';
/** 图 1 §3.2 工具条左侧五项（逐字、顺序都不能改）。 */
export const NBW_MENUS = ['文件', '编辑', '查看', '语言', '运行'] as const;
/** 图 1 §3.3 页签。 */
export const NBW_TAB_OUTPUT = '输出';
export const NBW_TAB_TERMINAL = '终端';
export const NBW_NOT_RUN = '尚未执行';
/** 没有后端计量位的统一降级词（与 quota 页的「未采集」区分：这里是根本没有采集口）。 */
export const NBW_NOT_MEASURED = '未计量';
export const NBW_NOT_WIRED = '未接入';

/** 会话状态里算「活着」的集合：与后端 session_state_machine.is_active 同口径。 */
const ALIVE_SESSION_STATUSES = new Set(['pending', 'key_distributing', 'provisioning', 'ready', 'running']);

export const isAliveSessionStatus = (status: string | null | undefined): boolean =>
  !!status && ALIVE_SESSION_STATUSES.has(status);

/** 图上的绿点只在后端真说 running 时才亮；ready/provisioning 只是活着，不算已连上。 */
export const isRunningSessionStatus = (status: string | null | undefined): boolean => status === 'running';

// ---------- 文件目录（图 1 §3.1） ----------

export interface WorkbenchFileNode {
  key: string;
  name: string;
  path: string;
  isDirectory: boolean;
  /** 字节数；后端没给就是 null（不拿 0 顶替） */
  size: number | null;
  children: WorkbenchFileNode[] | null;
  /** 该文件能不能真的从本页取回（只有会话文件接口那条链能下载）。 */
  downloadable: boolean;
}

/**
 * `GET /sandbox-sessions/{id}/files` 一行的形状。后端真正下发的键是 `filename`
 * （app/services/session_files.py:92），前端 `SessionFile.name` 是旧叫法——这里按后端为准，
 * 兼容读 `name`，两者都没有时退成空串（不编造文件名）。
 */
export interface SessionFileRow {
  name?: string;
  filename?: string;
  size?: number;
  encrypted?: boolean;
}

/**
 * 会话上传文件（GET /sandbox-sessions/{id}/files）→ 树。
 * 后端返回的是扁平清单（files/ 顶层），这里就如实画平一层，不伪造目录层级。
 */
export function sessionFilesToTree(files: SessionFileRow[]): WorkbenchFileNode[] {
  return files
    .map((file) => ({ file, name: file.filename ?? file.name ?? '' }))
    .sort((a, b) => a.name.localeCompare(b.name, 'zh-CN'))
    .map(({ file, name }) => ({
      key: `f:${name}`,
      name,
      path: name,
      isDirectory: false,
      size: typeof file.size === 'number' ? file.size : null,
      children: null,
      downloadable: true,
    }));
}

/**
 * 开发环目录（Jupyter Contents API）→ 树。目录的 size 在 Jupyter 里是子项个数，
 * 绝不当字节数累加；文件下载走不通反代那条限制，所以 downloadable=false。
 */
export function jupyterEntryToNode(entry: JupyterContentEntry): WorkbenchFileNode {
  return {
    key: `j:${entry.path}`,
    name: entry.name,
    path: entry.path,
    isDirectory: entry.isDirectory,
    size: entry.isDirectory ? null : entry.size,
    children: entry.isDirectory ? (entry.children ?? []).map(jupyterEntryToNode) : null,
    downloadable: false,
  };
}

export interface WorkbenchFileSummary {
  count: number;
  bytes: number;
  /** 清单是不是全量（开发环目录读超预算 / 有子目录失败时不是） */
  partial: boolean;
}

export function summarizeFiles(nodes: WorkbenchFileNode[]): WorkbenchFileSummary {
  let count = 0;
  let bytes = 0;
  const walk = (list: WorkbenchFileNode[]) => {
    list.forEach((node) => {
      if (node.isDirectory) {
        walk(node.children ?? []);
        return;
      }
      count += 1;
      bytes += node.size ?? 0;
    });
  };
  walk(nodes);
  return { count, bytes, partial: false };
}

export function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined || !Number.isFinite(bytes) || bytes <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const power = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  const value = bytes / 1024 ** power;
  return `${power === 0 || value >= 100 ? Math.round(value) : value.toFixed(1)} ${units[power]}`;
}

/** MB → 「2 G」；后端没给配额就回未记录，不猜默认档位。 */
export function gibFromMb(mb: number | null | undefined): string {
  if (typeof mb !== 'number' || !Number.isFinite(mb) || mb <= 0) return UNKNOWN_TEXT;
  const gib = mb / 1024;
  return `${Number.isInteger(gib) ? gib : gib.toFixed(1)} G`;
}

/** 分母口径：租户池磁盘额度（GET /deliveries/quota-usage → tenant_resources.limits.disk_mb）。 */
export const DISK_QUOTA_SCOPE_HINT =
  '分母取账号级租户池磁盘额度（GET /deliveries/quota-usage → tenant_resources.limits.disk_mb）：'
  + '那是会话创建时分配出去的额度（sandbox_manager 记账），不是本会话工作区的实测配额——'
  + '平台没有按会话的工作区配额字段。单文件上限与会话文件数上限另见后端配置'
  + '（SESSION_FILE_MAX_BYTES=50 MiB、SESSION_MAX_FILES=200）。';

// ---------- 连接态（图 1 §3.2 工具条右侧） ----------

export type ConnectionTone = 'ok' | 'warn' | 'off' | 'unknown';

export interface ConnectionState {
  tone: ConnectionTone;
  label: string;
  hint: string;
}

export function connectionState(input: {
  sessionId: string | null;
  status: string | null | undefined;
  loading?: boolean;
  error?: unknown;
  /** 上一次执行实际落在 cds_stateless：内核没起来，连接在但执行是降级的。 */
  degradedBackend?: string | null;
  degradedReason?: string | null;
}): ConnectionState {
  if (!input.sessionId) {
    return { tone: 'off', label: '● 沙箱未连接', hint: '本任务还没有沙箱会话：GET /notebooks/{id} 的 session_id 为空。' };
  }
  if (input.loading && !input.status) {
    return { tone: 'unknown', label: '● 连接检测中', hint: '沙箱会话状态查询未返回（GET /sandbox-sessions/{id}）。' };
  }
  if (input.error) {
    return { tone: 'off', label: '● 沙箱未连接', hint: '沙箱会话状态查询失败，按未连接处理，不画绿点。' };
  }
  if (isRunningSessionStatus(input.status)) {
    if (input.degradedBackend === 'cds_stateless') {
      return {
        tone: 'warn',
        label: '● 沙箱已连接（执行降级）',
        hint: `会话 status=running，但上一次执行落在 cds_stateless（沙箱内 kernel daemon 没起来，变量不跨单元格保留）：${input.degradedReason ?? '后端未给降级原因'}`,
      };
    }
    return { tone: 'ok', label: '● 沙箱已连接', hint: `沙箱会话 status=running（GET /sandbox-sessions/${input.sessionId}）` };
  }
  if (isAliveSessionStatus(input.status)) {
    return { tone: 'warn', label: `● 沙箱未连接（${input.status}）`, hint: '会话存在但未进入 running，按未连接口径显示，不画绿点。' };
  }
  return {
    tone: 'off',
    label: '● 沙箱未连接',
    hint: `沙箱会话 status=${input.status ?? UNKNOWN_TEXT}（终态或未知），不画绿点。`,
  };
}

// ---------- 执行日志（图 1 §3.3 正文） ----------

export type LogTone = 'info' | 'ok' | 'warn' | 'error';

export interface WorkbenchLogLine {
  key: string;
  /** 后端事件时间（ISO）；本地补充行为本地时刻 */
  at: string;
  clock: string;
  text: string;
  tone: LogTone;
  /** 是否算「问题」页签里的一条（真实存在的问题条目，不是计数器） */
  problem: boolean;
}

/** 审计动作 → 中文语义；未登记的动作原样透出动作名（不翻译、不猜含义）。 */
export const LOG_ACTION_LABELS: Record<string, string> = {
  'sandbox.create': '沙箱会话创建',
  'sandbox.create_failed': '沙箱会话创建失败',
  'sandbox.template_seed_failed': '模板预置失败',
  'sandbox.data_injection_failed': '合约供数注入失败',
  'sandbox.key_distribution_failed': '会话密钥分发失败',
  'sandbox.key_distribution_denied': '会话密钥分发被拒',
  'sandbox.execute': '执行',
  'sandbox.exec': '命令执行',
  'sandbox.code_scan_rejected': '代码扫描拦截',
  'sandbox.output_inspected': '输出审查',
  'sandbox.file_upload': '上传文件',
  'sandbox.file_download': '下载文件',
  'sandbox.file_download_redacted': '下载文件（已脱敏）',
  'sandbox.file_download_blocked': '下载被输出管控阻断',
  'sandbox.file_delete': '删除文件',
  'sandbox.update_network_policy': '更新网络策略',
  'sandbox.pause': '会话暂停',
  'sandbox.resume': '会话恢复',
  'sandbox.refresh': '会话续期',
  'sandbox.terminate': '会话终止',
  'sandbox.snapshot_create': '创建快照',
  'sandbox.snapshot_delete': '删除快照',
  'sandbox.snapshot_rollback': '回滚快照',
  'sandbox.proof_bundle_generated': '生成存证包',
  'session.auto_resume': '会话自动恢复',
  'session.auto_resume_rejected': '会话自动恢复被拒',
};

const NOISY_DETAIL_KEYS = new Set(['_sm2_signature', 'signature', 'watermark', 'report_hash', 'released_output_hash', 'quote', 'attestation']);

function clockOf(value: string | null | undefined): string {
  if (!value) return '--:--:--';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '--:--:--';
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

export { clockOf };

function detailDigest(detail: unknown): string {
  if (!detail || typeof detail !== 'object') return '';
  const parts: string[] = [];
  Object.entries(detail as Record<string, unknown>).forEach(([key, value]) => {
    if (NOISY_DETAIL_KEYS.has(key) || value === null || value === undefined) return;
    if (typeof value === 'object') return; // 嵌套结构（severities/watermark 等）不进单行摘要
    parts.push(`${key}=${String(value)}`);
  });
  return parts.slice(0, 6).join(' · ');
}

function toneOf(action: string, detail: Record<string, unknown>): LogTone {
  if (action.endsWith('_failed') || action.includes('rejected') || action.includes('denied') || action.includes('blocked')) return 'error';
  if (action === 'sandbox.execute' || action === 'sandbox.exec') return Number(detail.exit_code ?? 0) === 0 ? 'ok' : 'error';
  if (action === 'sandbox.output_inspected') return detail.blocked ? 'error' : detail.passed ? 'ok' : 'warn';
  if (action === 'sandbox.terminate') return 'warn';
  return 'info';
}

function isProblem(action: string, detail: Record<string, unknown>): boolean {
  if (action === 'sandbox.execute' || action === 'sandbox.exec') return Number(detail.exit_code ?? 0) !== 0;
  if (action === 'sandbox.output_inspected') return !!detail.blocked;
  return toneOf(action, detail) === 'error';
}

/** 一条后端审计事件 → 一行日志（[HH:MM:SS] 语义 · 关键字段）。 */
export function logLineFromAudit(entry: SessionLogEntry): WorkbenchLogLine {
  const detail = (entry.detail && typeof entry.detail === 'object' ? entry.detail : {}) as Record<string, unknown>;
  const label = LOG_ACTION_LABELS[entry.action] ?? entry.action;
  const digest = detailDigest(detail);
  return {
    key: `a:${entry.id}`,
    at: entry.created_at,
    clock: clockOf(entry.created_at),
    text: digest ? `${label} · ${digest}` : label,
    tone: toneOf(entry.action, detail),
    problem: isProblem(entry.action, detail),
  };
}

/**
 * 日志正文 = 后端会话审计流（升序）+ 本页真实发生过、但审计流还没回的执行结果。
 * 审计流是权威来源：本地行只填「刚点完运行、下一轮轮询还没回」这段窗口，
 * 一旦审计流里出现更晚的事件，本地行就退场（避免同一次执行在屏上出现两遍）。
 */
export function buildLogLines(logs: SessionLogEntry[], local: WorkbenchLogLine[]): WorkbenchLogLine[] {
  const backend = [...logs]
    .filter((entry) => !!entry.created_at)
    .sort((a, b) => String(a.created_at).localeCompare(String(b.created_at)))
    .map(logLineFromAudit);
  const newest = backend.length ? backend[backend.length - 1].at : null;
  const known = new Set(backend.map((line) => line.at));
  const extra = local.filter((line) => !known.has(line.at) && (!newest || String(line.at) > String(newest)));
  return [...backend, ...extra].sort((a, b) => String(a.at).localeCompare(String(b.at)));
}

/** 「清空视图」：只保留比 clearAt 更晚的事件（后端审计日志一条都不会被删）。 */
export function applyViewCleared(lines: WorkbenchLogLine[], clearAt: string | null): WorkbenchLogLine[] {
  if (!clearAt) return lines;
  return lines.filter((line) => String(line.at) > clearAt);
}

export interface LastExecution {
  ok: boolean;
  at: string | null;
  durationMs: number | null;
  blocked: boolean;
}

/** 最近一次真实执行：审计流里最后一条 sandbox.execute / sandbox.exec。 */
export function lastExecutionFromLogs(logs: SessionLogEntry[]): LastExecution | null {
  const rows = logs
    .filter((entry) => entry.action === 'sandbox.execute' || entry.action === 'sandbox.exec')
    .sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)));
  const latest = rows[0];
  if (!latest) return null;
  const detail = (latest.detail && typeof latest.detail === 'object' ? latest.detail : {}) as Record<string, unknown>;
  const exit = typeof detail.exit_code === 'number' ? detail.exit_code : null;
  const duration = typeof detail.duration_ms === 'number' ? detail.duration_ms : null;
  return {
    ok: exit === 0,
    at: latest.created_at ?? null,
    durationMs: duration,
    blocked: !!detail.output_blocked,
  };
}

export function formatDuration(ms: number | null | undefined): string {
  if (typeof ms !== 'number' || !Number.isFinite(ms)) return '耗时未记录';
  return `${(ms / 1000).toFixed(1)}s`;
}

export interface RunStatusView {
  tone: 'ok' | 'error' | 'warn' | 'idle';
  text: string;
  hint: string;
}

/** 页签条右侧的执行态：真记录的状态 + 真耗时；一条都没跑就灰字「尚未执行」。 */
export function runStatusView(
  backend: LastExecution | null,
  local: { ok: boolean | null; durationMs: number | null; label: string; blocked?: boolean; at: string | null } | null,
): RunStatusView {
  if (backend) {
    const duration = formatDuration(backend.durationMs);
    if (backend.blocked) {
      return { tone: 'warn', text: `输出被阻断 · ${duration}`, hint: '最近一次执行退出码 0 但输出被管控策略阻断（sandbox.execute.detail.output_blocked）。' };
    }
    return {
      tone: backend.ok ? 'ok' : 'error',
      text: `${backend.ok ? '执行成功' : '执行失败'} · ${duration}`,
      hint: `取本会话审计流最后一条 sandbox.execute（${backend.at ?? '时间未记录'}）的 exit_code 与 duration_ms。`,
    };
  }
  if (local) {
    if (local.ok === null) {
      return { tone: 'warn', text: `${local.label} · 结果未记录`, hint: '请求没跑成（网络/后端报错），后端没有这条执行的状态与耗时，既不假称成功也不假称失败。' };
    }
    return {
      tone: local.ok ? 'ok' : 'error',
      text: `${local.ok ? '执行成功' : '执行失败'} · ${formatDuration(local.durationMs)}`,
      hint: '本页刚提交、审计流尚未回流的执行结果（后端 /notebooks/{id}/cells/execute 不带耗时字段，耗时随后由审计流补齐）。',
    };
  }
  return { tone: 'idle', text: NBW_NOT_RUN, hint: '本会话审计流里没有任何执行记录，也没有本页发起过的执行。' };
}

// ---------- 编辑器状态条（图 1 §3.2 底部） ----------

export function detectEol(text: string): 'CRLF' | 'LF' {
  return text.includes('\r\n') ? 'CRLF' : 'LF';
}

export function fileBasename(path: string | null | undefined, fallback = ''): string {
  const parts = String(path ?? '').split('/').filter(Boolean);
  return parts.length ? parts[parts.length - 1] : fallback;
}

export interface CursorView {
  line: number | null;
  column: number | null;
}

/** 单元格编辑器没拿到光标事件之前不编一个 1:1；开发环分支整个槽位是未接入。 */
export function cursorText(cursor: CursorView | null): string {
  if (!cursor) return '行 —, 列 —';
  return `行 ${cursor.line ?? '—'}, 列 ${cursor.column ?? '—'}`;
}

/** 终端页签的诚实文案（本页不复制一套沙箱终端）。 */
export const TERMINAL_TAB_NOTE =
  '终端未接入本页：沙箱命令行走 POST /sandbox-sessions/{id}/exec（在「沙箱会话」详情里用），'
  + '本页只读本会话审计流，不重复实现一套终端；JupyterLab 开发环是否带终端由后端按镜像口径决定。';
