// 首页聚合端点（GET /api/v1/flow/dashboard/home）的响应夹具：两个首页测试文件共用。
// 数字刻意与原型示例值（12 / 5 / 7 / 1,204 次 / 张伟 / 2026-08-12）全部错开，
// 这样「屏上出现原型示例值」本身就是可失败的信号。
import type { HomeMemoryPanel, HomeOverview } from '../../../services/flow';

export const HOME_FIXTUREgenerated = '2026-10-17T09:00:00+00:00';

export function homePanelFixture(patch: Partial<HomeMemoryPanel> = {}): HomeMemoryPanel {
  return {
    reserved_mb: 0,
    active_sessions: 0,
    state: 'idle',
    per_task_ceiling_mb: 32768,
    used_mb: null,
    measured: false,
    running_runs: 0,
    pending_runs: 0,
    ...patch,
  };
}

export function homeOverviewFixture(patch: Partial<HomeOverview> = {}): HomeOverview {
  return {
    generated_at: HOME_FIXTUREgenerated,
    scope: { user_id: 'u-1', username: 'li-dev', organization: '星信数科', role: 'buyer', note: '本页数字按当前主体统计。' },
    dev_tasks: {
      total: 11,
      active: 9,
      ended: 2,
      status_caliber: { active: 'status != archived', ended: 'status == archived' },
    },
    task_filters: { all: 11, active: 9, ended: 2 },
    recent_tasks: [
      { id: 'n-2', task_no: 'TASK-20261016-002', title: '客单价回归', status: 'draft', debug_method: 'jupyter', created_at: '2026-10-16T08:00:00Z' },
      { id: 'n-1', task_no: 'TASK-20261015-001', title: '流失评分', status: 'draft', debug_method: 'web_ide', created_at: '2026-10-15T08:00:00Z' },
      { id: 'n-3', task_no: null, title: '存量笔记本', status: 'archived', debug_method: null, created_at: '2026-10-14T08:00:00Z' },
    ],
    export_interfaces: { total: 7, online: 4, offline: 3, status_caliber: { online: 'enabled', offline: 'disabled' } },
    calls_today: {
      count: 18,
      failures: 0,
      last_called_at: '2026-09-30T06:15:00+00:00',
      window: 'utc_day',
      actions: ['openapi.call', 'openapi.call_failed'],
      source: 'audit_logs',
    },
    ops_log_today: { count: 214, failures: 3, visibility: 'self', window: 'utc_day', source: 'audit_logs', note: '本人今日操作日志。' },
    memory: {
      quota_mb: 8192,
      quota_source: 'sandbox_manager',
      debug: homePanelFixture({ reserved_mb: 3891, active_sessions: 1, state: 'busy' }),
      exec: homePanelFixture({ per_task_ceiling_mb: 65536, running_runs: 0, pending_runs: 2, state: 'queued' }),
    },
    trend: {
      days: [
        { date: '2026-10-11', debug: 0, exec: 0 },
        { date: '2026-10-12', debug: 5, exec: 2 },
        { date: '2026-10-13', debug: 1, exec: 0 },
        { date: '2026-10-14', debug: 0, exec: 0 },
        { date: '2026-10-15', debug: 0, exec: 0 },
        { date: '2026-10-16', debug: 4, exec: 0 },
        { date: '2026-10-17', debug: 2, exec: 1 },
      ],
      window_days: 7,
      series: { debug: ['notebook.cell_executed', 'notebook.run'], exec: ['compute.run.requested'] },
      source: 'audit_logs',
      note: '调试=在线调试运行，执行=正式生产运行请求。',
    },
    latest_result_sets: [
      {
        bundle_id: 'b1a2c3d4-1111-2222-3333-444455556666',
        contract_id: 'c-1',
        contract_no: 'CT-0001',
        file_name: 'delivery/b1a2c3d4.zip',
        file_count: 3,
        size_bytes: 2048,
        row_count: 120,
        created_at: '2026-10-16T02:00:00Z',
        call_count: 8,
        t5_review_status: 'passed',
        watermark_status: 'applied',
        state: 'normal',
        state_reason: null,
      },
      {
        bundle_id: 'b2c3d4e5-1111-2222-3333-444455556666',
        contract_id: 'c-2',
        contract_no: 'CT-0002',
        file_name: null,
        file_count: null,
        size_bytes: null,
        row_count: null,
        created_at: null,
        call_count: null,
        t5_review_status: null,
        watermark_status: null,
        state: null,
        state_reason: '交付包未记录审查/水印状态',
      },
    ],
    call_count_caliber: '该合约下 OpenAPI 接口的累计调用次数',
    not_collected: [
      { key: 'memory.measured', label: '实测占用', reason: '后端无 cgroup/psutil 来源，分子为已分配' },
      { key: 'memory.exec.quota', label: '16 G 档', reason: '配置里没有 16 G 这一档' },
      { key: 'bundle.call_count', label: '结果集调用次数', reason: '按合约下接口累计，非单包下载数' },
    ],
    ...patch,
  } as HomeOverview;
}

export const HOME_FIXTURE_KEYS = {
  latestTaskTitle: '客单价回归',
  oldestTaskTitle: '存量笔记本',
  bundleWithFileName: 'delivery/b1a2c3d4.zip',
};
