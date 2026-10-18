// 首页（原型 A-1「首页-平台总览」）的「后端值 → 屏上字符串」换算，集中成纯函数便于单测。
//
// 三条不变式（页面里不许再各写一遍）：
// 1. 缺数一律渲染 UNKNOWN_TEXT（未记录），绝不折算成 0 —— 0 在产品语义里是「确实没有」，
//    与「没这个数」是两回事（禁伪造铁律，见 app/api/flow_runtime.py 的 not_collected）；
// 2. 标签只写数据真实口径：内存分子是「已分配」，不是实测占用；调用次数是「合约下接口累计」，
//    不是文件下载次数；今日操作日志按主体可见性加后缀；
// 3. 后端词表之外的值原样透出（与 Flow/display.ts 同一约定），不猜第三种状态。
import { UNKNOWN_TEXT } from '../../services/flow';
import type {
  HomeMemoryPanel,
  HomeMemoryState,
  HomeOverview,
  HomeOverviewOpsLog,
  HomeResultSetRow,
  HomeResultSetState,
  HomeTrendPoint,
} from '../../services/flow';
import { formatDevTaskTime } from './devTasksModel';

/** 原型的内存写法是「3.8 G / 8 G 配额」：整 GB 不带小数，非整留一位。 */
export function formatGibFromMb(mb: number | null | undefined): string {
  if (typeof mb !== 'number' || !Number.isFinite(mb)) return UNKNOWN_TEXT;
  const gib = mb / 1024;
  return `${Number.isInteger(gib) ? String(gib) : gib.toFixed(1)} G`;
}

/** 进度条百分比：分母缺失或为 0 时返回 0（条子画空，但旁边的配额写「未记录」，不假装满配额）。 */
export function memoryPercent(reservedMb: number, quotaMb: number | null | undefined): number {
  if (!quotaMb || quotaMb <= 0) return 0;
  return Math.max(0, Math.min(100, Math.round((reservedMb / quotaMb) * 100)));
}

/** 进度条刻度（原型是 0/半程/满 三档）；配额取不到时返回空数组，页面就不画刻度。 */
export function memoryTicks(quotaMb: number | null | undefined): string[] {
  if (!quotaMb || quotaMb <= 0) return [];
  return [formatGibFromMb(0), formatGibFromMb(quotaMb / 2), formatGibFromMb(quotaMb)];
}

export interface StatePill {
  label: string;
  tone: 'success' | 'default' | 'danger' | 'warning';
}

const MEMORY_STATE_PILLS: Record<HomeMemoryState, StatePill> = {
  busy: { label: '占用中', tone: 'success' },
  queued: { label: '待执行', tone: 'default' },
  idle: { label: '空闲', tone: 'default' },
};

export function memoryStatePill(panel: HomeMemoryPanel | undefined): StatePill {
  const state = panel?.state;
  if (state === 'busy' || state === 'queued' || state === 'idle') return MEMORY_STATE_PILLS[state];
  return { label: UNKNOWN_TEXT, tone: 'default' };
}

/** 开发任务状态胶囊：后端词表只有 draft/archived，其余值原样透出（不猜第三种状态）。 */
export function taskStatePill(status: string | null | undefined): StatePill {
  if (status === 'archived') return { label: '已结束', tone: 'default' };
  if (status === 'draft') return { label: '进行中', tone: 'success' };
  return { label: status ?? UNKNOWN_TEXT, tone: 'default' };
}

/** 任务筛选标签（原型：全部 12 / 进行中 5 / 已结束 7），计数一律取后端下发值。 */
export function taskFilterTabs(overview: HomeOverview): { key: string; label: string; count: number }[] {
  return [
    { key: 'all', label: '全部', count: overview.task_filters.all },
    { key: 'active', label: '进行中', count: overview.task_filters.active },
    { key: 'ended', label: '已结束', count: overview.task_filters.ended },
  ];
}

/** 结果集状态：三态由后端从打包时的 T5/水印结论派生，null 是「未记录」而不是「正常」。 */
export function resultSetStatePill(state: HomeResultSetState): StatePill {
  if (state === 'normal') return { label: '正常', tone: 'success' };
  if (state === 'abnormal') return { label: '异常', tone: 'danger' };
  return { label: UNKNOWN_TEXT, tone: 'default' };
}

/** 「128次」（strict-1-home.md §5：无千分位、无空格；group-A 的「1,204 次」是另一读法，strict 优先）。
 *  null 是口径不适用（该合约下没有接口），不能说 0 次。 */
export function formatCallCount(value: number | null | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return UNKNOWN_TEXT;
  return `${value}次`;
}

/** KPI 副标题「最近调用 2026-08-12」；从来没有过调用时写「最近调用 未记录」。 */
export function lastCallText(iso: string | null | undefined): string {
  const stamp = formatDevTaskTime(iso ?? null);
  return `最近调用 ${stamp ? stamp.slice(0, 10) : UNKNOWN_TEXT}`;
}

/**
 * KPI「今日操作日志」的副标题：真值（strict-1-home.md §5）只有「失败 3」，可见行就只写这一个数。
 * 本人口径仍然必须说（opsLogScopeText）：GET /api/v1/audit/records 对非运营/监管/管理角色返回 403，
 * 这一格统计的只是当前账号自己的行 —— 那句话收进卡名的 Tooltip，不再挤在 12px 副行里，一字不丢。
 */
export function opsLogSubtext(ops: HomeOverviewOpsLog): string {
  return `失败 ${ops.failures}`;
}

/** 操作日志的可见性口径（Tooltip 文案）：platform 才是平台数，其余一律只是本账号的。 */
export function opsLogScopeText(ops: HomeOverviewOpsLog): string {
  const scope = ops.visibility === 'platform' ? '全平台' : '仅本账号';
  return `统计范围：${scope}——当前角色读不到的审计行不计入本格。`;
}

/** 趋势 x 轴标签：原型是 08/06 这种 MM/DD，不带年份。 */
export function trendAxisLabel(isoDate: string): string {
  const parts = isoDate.split('-');
  return parts.length === 3 ? `${parts[1]}/${parts[2]}` : isoDate;
}

/** 两组柱共用的纵 scaling：全 0 时分母退到 1，柱子高度 0 而不是 NaN。 */
export function trendScale(points: HomeTrendPoint[]): number {
  return Math.max(1, ...points.map((point) => Math.max(point.debug, point.exec)));
}

export function trendBarHeight(count: number, scale: number, maxHeight: number): number {
  if (!Number.isFinite(count) || count <= 0 || !scale) return 0;
  return Math.max(2, Math.round((count / scale) * maxHeight));
}

/** 未采集项按 key 取后端原文（页面上的披露直接引用后端 reason，不在前端另写一套理由）。 */
export function notCollectedReason(overview: HomeOverview, key: string): string | null {
  return overview.not_collected.find((item) => item.key === key)?.reason ?? null;
}

/** 结果集列名：文件名就是交付包对象名，后端没给时渲染「未记录」，不拿 bundle_id 前缀冒充名字。 */
export function resultSetName(row: HomeResultSetRow): string {
  return row.file_name ?? UNKNOWN_TEXT;
}
