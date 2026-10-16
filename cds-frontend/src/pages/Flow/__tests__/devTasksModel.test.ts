import { describe, expect, it } from 'vitest';
import { buildDevTaskRows } from '../devTasksModel';
import type { ComputeRun, Notebook, Workload } from '../../../services/flow';

function notebook(overrides: Partial<Notebook> = {}): Notebook {
  return {
    id: 'nb-1',
    contract_id: 'c-1',
    title: '客单价分析',
    path: '/notebooks/x.ipynb',
    kernel: 'python3',
    language: 'python',
    content: null,
    version: 1,
    status: 'draft',
    session_id: null,
    execution_backend: null,
    last_executed_at: null,
    created_at: '2026-10-16T00:00:00Z',
    updated_at: '2026-10-16T00:00:00Z',
    ...overrides,
  };
}

function workload(overrides: Partial<Workload> = {}): Workload {
  return {
    id: 'wl-1',
    name: '客单价',
    contract_id: 'c-1',
    source_notebook_id: null,
    language: 'python',
    entrypoint_hash: null,
    resource_spec: null,
    env: 'test',
    status: 'draft',
    version: 1,
    is_latest: true,
    test_run_id: null,
    published_at: null,
    created_at: '2026-10-16T00:00:00Z',
    ...overrides,
  };
}

function run(overrides: Partial<ComputeRun> = {}): ComputeRun {
  return {
    id: 'run-1',
    contract_id: 'c-1',
    trigger: 'schedule',
    status: 'succeeded',
    scheduled_for: '2026-10-16T01:00:00Z',
    started_at: null,
    finished_at: null,
    output_rows: null,
    output_files: null,
    error_message: null,
    ...overrides,
  };
}

describe('buildDevTaskRows', () => {
  it('按开发阶段给笔记本定级', () => {
    const rows = buildDevTaskRows({
      notebooks: [notebook(), notebook({ id: 'nb-2', session_id: 's-1' }), notebook({ id: 'nb-3' })],
      workloads: [workload({ status: 'published', source_notebook_id: 'nb-3' })],
      runs: [],
    });
    const byKey = new Map(rows.map((row) => [row.key, row.stage.label]));
    expect(byKey.get('notebook:nb-1')).toBe('草稿');
    expect(byKey.get('notebook:nb-2')).toBe('调试中');
    expect(byKey.get('notebook:nb-3')).toBe('已发布生产');
  });

  it('未登记的后端状态原样透出，不编造中文标签', () => {
    const rows = buildDevTaskRows({
      notebooks: [],
      workloads: [workload({ status: 'queued_by_backend' })],
      runs: [run({ status: 'paused_by_operator' })],
    });
    const stages = rows.map((row) => row.stage.label).sort();
    expect(stages).toEqual(['paused_by_operator', 'queued_by_backend']);
  });

  it('按最近更新时间倒序，没有时间的排最后', () => {
    const rows = buildDevTaskRows({
      notebooks: [notebook({ id: 'old', updated_at: '2026-10-01T00:00:00Z' }), notebook({ id: 'new', updated_at: '2026-10-16T00:00:00Z' })],
      workloads: [workload({ created_at: '2020-01-01T00:00:00Z' })],
      runs: [run({ started_at: null, finished_at: null, scheduled_for: null })],
    });
    expect(rows.map((row) => row.key)).toEqual(['notebook:new', 'notebook:old', 'workload:wl-1', 'run:run-1']);
  });

  it('每行跳回自己那类页面的入口', () => {
    const rows = buildDevTaskRows({
      notebooks: [notebook()],
      workloads: [workload()],
      runs: [run()],
    });
    expect(new Set(rows.map((row) => row.target))).toEqual(new Set(['/notebooks', '/workloads', '/compute']));
  });
});
