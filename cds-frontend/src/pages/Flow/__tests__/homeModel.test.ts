// 首页（原型 A-1）换算层的可失败判据：缺数必须渲染「未记录」而不是 0、口径标签必须说真话
// （内存分子=已分配、调用次数=合约下接口累计、操作日志可见性单独成句）、趋势刻度只能按日取 MM/DD。
import { describe, it, expect } from 'vitest';
import { UNKNOWN_TEXT } from '../../../services/flow';
import type { HomeMemoryPanel, HomeOverview, HomeTrendPoint } from '../../../services/flow';
import {
  formatCallCount,
  formatGibFromMb,
  lastCallText,
  memoryPercent,
  memoryStatePill,
  memoryTicks,
  notCollectedReason,
  opsLogScopeText,
  opsLogSubtext,
  resultSetName,
  resultSetStatePill,
  taskFilterTabs,
  taskStatePill,
  trendAxisLabel,
  trendBarHeight,
  trendScale,
} from '../homeModel';

function panel(patch: Partial<HomeMemoryPanel> = {}): HomeMemoryPanel {
  return {
    reserved_mb: 0,
    active_sessions: 0,
    state: 'idle',
    per_task_ceiling_mb: 32768,
    used_mb: null,
    measured: false,
    ...patch,
  };
}

function overview(patch: Partial<HomeOverview> = {}): HomeOverview {
  return {
    generated_at: '2026-10-17T09:00:00+00:00',
    scope: { user_id: 'u-1', username: 'ss19buy', organization: null, role: 'buyer', note: '口径备注' },
    dev_tasks: { total: 11, active: 9, ended: 2, status_caliber: { active: 'x', ended: 'y' } },
    task_filters: { all: 11, active: 9, ended: 2 },
    recent_tasks: [],
    export_interfaces: { total: 7, online: 4, offline: 3, status_caliber: { online: 'enabled', offline: 'disabled' } },
    calls_today: { count: 0, failures: 0, last_called_at: null, window: 'utc_day', actions: [], source: 'audit_logs' },
    ops_log_today: { count: 0, failures: 0, visibility: 'self', window: 'utc_day', source: 'audit_logs', note: '' },
    memory: {
      quota_mb: 8192,
      quota_source: 'sandbox_manager',
      debug: panel(),
      exec: panel({ per_task_ceiling_mb: 65536 }),
    },
    trend: { days: [], window_days: 7, series: { debug: [], exec: [] }, source: 'audit_logs', note: '' },
    latest_result_sets: [],
    call_count_caliber: '口径',
    not_collected: [{ key: 'memory.measured', label: '实测占用', reason: '后端无 cgroup/psutil 来源' }],
    ...patch,
  };
}

describe('formatGibFromMb / 配额刻度', () => {
  it('整 GB 不带小数，非整留一位，缺数走未记录', () => {
    expect(formatGibFromMb(8192)).toBe('8 G');
    expect(formatGibFromMb(0)).toBe('0 G');
    expect(formatGibFromMb(3891)).toBe('3.8 G');
    expect(formatGibFromMb(null)).toBe(UNKNOWN_TEXT);
    expect(formatGibFromMb(Number.NaN)).toBe(UNKNOWN_TEXT);
  });

  it('百分比封顶 100、分母缺失退 0（配额那侧另行渲染未记录）', () => {
    expect(memoryPercent(3891, 8192)).toBe(47);
    expect(memoryPercent(9000, 8192)).toBe(100);
    expect(memoryPercent(2048, null)).toBe(0);
    expect(memoryPercent(2048, 0)).toBe(0);
  });

  it('刻度取 0/半程/满三档；无配额就没有刻度', () => {
    expect(memoryTicks(8192)).toEqual(['0 G', '4 G', '8 G']);
    expect(memoryTicks(null)).toEqual([]);
  });
});

describe('状态胶囊', () => {
  it('内存三态逐字照图：占用中/待执行/空闲，词表外走未记录', () => {
    expect(memoryStatePill(panel({ state: 'busy' }))).toEqual({ label: '占用中', tone: 'success' });
    expect(memoryStatePill(panel({ state: 'queued' }))).toEqual({ label: '待执行', tone: 'default' });
    expect(memoryStatePill(panel({ state: 'idle' }))).toEqual({ label: '空闲', tone: 'default' });
    expect(memoryStatePill(undefined).label).toBe(UNKNOWN_TEXT);
  });

  it('任务状态只认 archived=已结束、draft=进行中，其余原样透出不猜第三种', () => {
    expect(taskStatePill('archived')).toEqual({ label: '已结束', tone: 'default' });
    expect(taskStatePill('draft')).toEqual({ label: '进行中', tone: 'success' });
    expect(taskStatePill('published')).toEqual({ label: 'published', tone: 'default' });
    expect(taskStatePill(null).label).toBe(UNKNOWN_TEXT);
  });

  it('结果集状态：null 是未记录，不是正常', () => {
    expect(resultSetStatePill('normal')).toEqual({ label: '正常', tone: 'success' });
    expect(resultSetStatePill('abnormal')).toEqual({ label: '异常', tone: 'danger' });
    expect(resultSetStatePill(null)).toEqual({ label: UNKNOWN_TEXT, tone: 'default' });
  });
});

describe('计数与口径文案', () => {
  it('调用次数按真值写「128次」：不带千分位、不带空格；null 是口径不适用，不能写成 0 次', () => {
    expect(formatCallCount(12004)).toBe('12004次');
    expect(formatCallCount(0)).toBe('0次');
    expect(formatCallCount(null)).toBe(UNKNOWN_TEXT);
  });

  it('最近调用只取日期部分，没有调用记录时写未记录', () => {
    expect(lastCallText('2026-09-30T06:15:00+00:00')).toBe('最近调用 2026-09-30');
    expect(lastCallText(null)).toBe(`最近调用 ${UNKNOWN_TEXT}`);
  });

  it('操作日志副标题只有真值的「失败 N」；可见性口径单独成句，本人口径不能冒充全平台', () => {
    expect(opsLogSubtext({ ...overview().ops_log_today, failures: 3, visibility: 'self' })).toBe('失败 3');
    expect(opsLogSubtext({ ...overview().ops_log_today, failures: 0, visibility: 'platform' })).toBe('失败 0');
    expect(opsLogScopeText({ ...overview().ops_log_today, visibility: 'self' })).toContain('仅本账号');
    expect(opsLogScopeText({ ...overview().ops_log_today, visibility: 'platform' })).toContain('全平台');
  });

  it('筛选标签的计数一律取后端 task_filters，不由列表行数推导', () => {
    expect(taskFilterTabs(overview())).toEqual([
      { key: 'all', label: '全部', count: 11 },
      { key: 'active', label: '进行中', count: 9 },
      { key: 'ended', label: '已结束', count: 2 },
    ]);
  });

  it('未采集项按 key 取后端原文，取不到就是 null（页面自己兜底）', () => {
    expect(notCollectedReason(overview(), 'memory.measured')).toBe('后端无 cgroup/psutil 来源');
    expect(notCollectedReason(overview(), 'nope')).toBeNull();
  });

  it('结果集没有文件名就是未记录，不拿 bundle_id 前缀冒充名字', () => {
    const row = { file_name: null } as never;
    expect(resultSetName(row)).toBe(UNKNOWN_TEXT);
    expect(resultSetName({ file_name: 'delivery/1.zip' } as never)).toBe('delivery/1.zip');
  });
});

describe('趋势换算', () => {
  const points: HomeTrendPoint[] = [
    { date: '2026-10-11', debug: 0, exec: 0 },
    { date: '2026-10-12', debug: 5, exec: 2 },
    { date: '2026-10-13', debug: 1, exec: 0 },
  ];

  it('x 轴标签取 MM/DD，不带年份', () => {
    expect(trendAxisLabel('2026-10-11')).toBe('10/11');
    expect(trendAxisLabel('bad')).toBe('bad');
  });

  it('两组柱共用一个 scaling；全 0 时分母退 1，柱高 0 而不是 NaN', () => {
    expect(trendScale(points)).toBe(5);
    expect(trendScale([])).toBe(1);
    expect(trendBarHeight(5, 5, 132)).toBe(132);
    expect(trendBarHeight(1, 5, 132)).toBe(26);
    expect(trendBarHeight(0, 1, 132)).toBe(0);
    expect(trendBarHeight(3, 0, 132)).toBe(0);
  });

  it('峰值柱铺满容器高，非零小计数不低于 2px', () => {
    expect(trendBarHeight(5, trendScale(points), 132)).toBe(132);
    expect(trendBarHeight(1, 1000, 132)).toBeGreaterThanOrEqual(2);
    expect(trendBarHeight(2, trendScale(points), 132)).toBeLessThan(132);
  });
});
