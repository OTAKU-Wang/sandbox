// SS-18 M2 卡片视图与筛选口径：筛选只能影响「看到哪些卡」，不能改写数据，也不能把
// 存量行（无 task_no / 无内存）默认藏起来——它们是该显示的，缺失处显示「未记录」。
import { describe, it, expect } from 'vitest';
import type { Notebook, Workload } from '../../../services/flow';
import { WorkloadStatus } from '../../../services/flow';
import {
  EMPTY_DEV_TASK_FILTERS,
  activeDevTaskFilterLabels,
  buildDevTaskCard,
  buildDevTaskCards,
  devTaskEmptyDescription,
  filterDevTaskCards,
  formatDevTaskTime,
  notebookStateMeta,
} from '../devTasksModel';

function notebook(overrides: Partial<Notebook> = {}): Notebook {
  return {
    id: 'n-1',
    contract_id: 'c-1',
    title: '客单价分析',
    path: '/nb/a.ipynb',
    kernel: 'python3',
    language: 'python',
    content: null,
    version: 3,
    status: 'draft',
    session_id: null,
    execution_backend: null,
    last_executed_at: null,
    created_at: '2026-10-16T00:00:00Z',
    updated_at: '2026-10-16T02:00:00Z',
    ...overrides,
  };
}

function workload(overrides: Partial<Workload> = {}): Workload {
  return {
    id: 'w-1',
    contract_id: 'c-1',
    source_notebook_id: null,
    status: WorkloadStatus.Draft,
    ...overrides,
  } as Workload;
}

const LEGACY = notebook({ id: 'legacy', title: '存量笔记本', path: '/nb/legacy.ipynb', task_no: null, debug_method: null, export_mode: null, debug_memory_mb: null, exec_memory_mb: null });
const NUMBERED = notebook({ id: 'n-2', title: '流失评分', task_no: 'TASK-20261016-001', debug_method: 'web_ide', export_mode: 'bundle', debug_memory_mb: 8192, exec_memory_mb: 16384 });
const ARCHIVED = notebook({ id: 'n-3', title: '归档任务', task_no: 'TASK-20261016-002', status: 'archived', debug_method: 'jupyter', export_mode: 'openapi', debug_memory_mb: 4096 });

const cards = buildDevTaskCards([LEGACY, NUMBERED, ARCHIVED], [workload({ source_notebook_id: 'n-2', status: WorkloadStatus.Published })]);

describe('开发任务卡片', () => {
  it('新任务把任务级字段都摆出来（内存按 GiB 显示）', () => {
    const card = cards.find((item) => item.id === 'n-2');
    expect(card?.task_no_text).toBe('TASK-20261016-001');
    expect(card?.debug_method_text).toBe('Web IDE 在线调试');
    expect(card?.export_mode_text).toBe('成果集打包导出');
    expect(card?.debug_memory_text).toBe('8 GiB');
    expect(card?.exec_memory_text).toBe('16 GiB');
    expect(card?.version_text).toBe('v3');
    expect(card?.workbench_path).toBe('/notebooks?task=n-2');
    expect(card?.updated_text).not.toBe('未记录');
  });

  it('存量行的缺失字段一律「未记录」，不是 0、不是示例值', () => {
    const card = buildDevTaskCard(LEGACY);
    expect(card.task_no_text).toBe('未记录');
    expect(card.debug_method_text).toBe('未记录');
    expect(card.export_mode_text).toBe('未记录');
    expect(card.debug_memory_text).toBe('未记录');
    expect(card.exec_memory_text).toBe('未记录');
    expect(buildDevTaskCard(notebook({ updated_at: null as unknown as string })).updated_text).toBe('未记录');
  });

  it('任务态：归档=已结束，其余=进行中，未登记状态原样透出', () => {
    expect(notebookStateMeta(ARCHIVED).label).toBe('已结束');
    expect(notebookStateMeta(NUMBERED).label).toBe('进行中');
    expect(notebookStateMeta(notebook({ status: 'unknown_state' })).label).toBe('unknown_state');
  });

  it('已发布负载把阶段推到「已发布生产」', () => {
    expect(cards.find((card) => card.id === 'n-2')?.stage.label).toBe('已发布生产');
    expect(cards.find((card) => card.id === 'legacy')?.stage.label).toBe('草稿');
  });
});

describe('筛选只影响列表，不吞存量', () => {
  it('没有任何筛选时逐字返回入参（引用同一份，不去重不排序）', () => {
    expect(filterDevTaskCards(cards, EMPTY_DEV_TASK_FILTERS)).toBe(cards);
  });

  it('默认口径下存量行仍在列表里', () => {
    expect(filterDevTaskCards(cards, EMPTY_DEV_TASK_FILTERS).map((card) => card.id)).toEqual(['legacy', 'n-2', 'n-3']);
  });

  it('关键词比名称/编号/路径，命中不到才少行', () => {
    expect(filterDevTaskCards(cards, { ...EMPTY_DEV_TASK_FILTERS, keyword: 'TASK-20261016-002' }).map((c) => c.id)).toEqual(['n-3']);
    expect(filterDevTaskCards(cards, { ...EMPTY_DEV_TASK_FILTERS, keyword: 'legacy.ipynb' }).map((c) => c.id)).toEqual(['legacy']);
  });

  it('枚举筛选按精确取值匹配（存量行的 null 不与任何取值相等）', () => {
    expect(filterDevTaskCards(cards, { ...EMPTY_DEV_TASK_FILTERS, debug_method: 'jupyter' }).map((c) => c.id)).toEqual(['n-3']);
    expect(filterDevTaskCards(cards, { ...EMPTY_DEV_TASK_FILTERS, export_mode: 'openapi' }).map((c) => c.id)).toEqual(['n-3']);
  });

  it('「只看已编号任务」只隐藏无编号行，其他行一个不少', () => {
    const visible = filterDevTaskCards(cards, { ...EMPTY_DEV_TASK_FILTERS, only_numbered: true });
    expect(visible.map((card) => card.id)).toEqual(['n-2', 'n-3']);
    expect(visible.every((card) => !!card.task_no)).toBe(true);
  });

  it('零结果文案点名是哪个筛选筛空的，且不编总数', () => {
    const filters = { ...EMPTY_DEV_TASK_FILTERS, keyword: 'zzz', only_numbered: true };
    expect(activeDevTaskFilterLabels(filters)).toEqual(['关键词“zzz”', '只看已编号任务']);
    expect(devTaskEmptyDescription(filters, 3)).toContain('已载入 3 个开发任务');
    expect(devTaskEmptyDescription(filters, 3)).toContain('关键词“zzz”');
    expect(devTaskEmptyDescription(EMPTY_DEV_TASK_FILTERS, 0)).toBe('还没有开发任务，点右上角新建一个');
  });

  it('时间解析不了就原样透出，编不出时间也不装', () => {
    expect(formatDevTaskTime('2026-10-16T02:00:00Z')).not.toBeNull();
    expect(formatDevTaskTime('昨天下午')).toBe('昨天下午');
    expect(formatDevTaskTime(null)).toBeNull();
  });
});
