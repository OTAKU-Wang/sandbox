// SS-18「任务详情 / 运行记录」的诚实口径：只列真发生过的执行；一条都没有就明说没有，
// 不出示例行、不编统计数；正式执行只认后端 compute run 里确实由本任务负载产生的那些。
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { renderWithProviders } from '../../../test/test-utils';
import TaskRunRecords from '../TaskRunRecords';
import type { DebugRunRecord } from '../TaskRunRecords';
import api from '../../../services/api';

Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }),
});

vi.mock('../../../services/api', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));

const get = api.get as unknown as ReturnType<typeof vi.fn>;

const notebook = {
  id: 'n-1',
  contract_id: null as string | null,
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
  task_no: 'TASK-20261016-001',
  debug_method: 'web_ide',
  export_mode: 'bundle',
  debug_memory_mb: 8192,
  exec_memory_mb: null,
};

const cellRecord: DebugRunRecord = {
  key: 'cell-1',
  kind: 'cell',
  label: '单元格 #1',
  at: '2026-10-16T03:00:00.000Z',
  ok: true,
  backend: 'cds_kernel',
  exit_code: 0,
  blocked: null,
};

const page = <T,>(items: T[]) => ({ items, total: items.length, page: 1, page_size: 20 });

beforeEach(() => {
  vi.clearAllMocks();
  get.mockResolvedValue(page([]));
});

/** 表头文字：图 2-4 的运行记录卡就六列，逐列照图。 */
function headerTitles(): string[] {
  return Array.from(document.querySelectorAll('thead th'))
    .map((cell) => (cell.textContent ?? '').trim())
    .filter(Boolean);
}

/** 找到含某段文字的那一行，按图上的列序返回它的单元格文字。 */
function rowCellsContaining(text: string): string[] {
  const row = Array.from(document.querySelectorAll('tbody tr')).find((tr) => (tr.textContent ?? '').includes(text));
  if (!row) throw new Error(`表里没有包含「${text}」的行`);
  return Array.from(row.querySelectorAll('td')).map((td) => (td.textContent ?? '').trim());
}

describe('运行记录区', () => {
  it('什么都没见过：直说暂无，而且不发无谓的请求', () => {
    renderWithProviders(<TaskRunRecords notebook={notebook} debugRecords={[]} />);
    expect(screen.getByText(/本任务未记录关联合约，正式执行记录无处归位/)).toBeInTheDocument();
    // 未下发的执行内存/后端就写「未记录」，不拿 0 或猜测值凑。
    expect(screen.getByText(/执行内存 未记录/)).toBeInTheDocument();
    expect(screen.getByText(/执行后端 未记录/)).toBeInTheDocument();
    expect(get).not.toHaveBeenCalled();
  });

  it('本页真跑过的调试执行会如实列出来（含实际后端）', () => {
    renderWithProviders(<TaskRunRecords notebook={notebook} debugRecords={[cellRecord]} />);
    expect(screen.getByText('调试执行')).toBeInTheDocument();
    expect(screen.getByText('单元格 #1 · 后端 cds_kernel')).toBeInTheDocument();
   // 图 2-4 的状态列只有三态：ok:true 的调试行落在「执行成功」，不再是自造的「通过」
    expect(screen.getByText('执行成功')).toBeInTheDocument();
    expect(screen.getByText(/调试执行的时间取本页记录时刻/)).toBeInTheDocument();
    // 六列里的执行环境：图上写作「调试环境 · N G」，N 取任务上后端下发的调试内存配额（8192MB → 8 G）
    expect(screen.getByText('调试环境 · 8 G')).toBeInTheDocument();
  });

  it('正式执行只认本任务派生负载的 compute run，别的不算', async () => {
    const withContract = { ...notebook, contract_id: 'c-1' };
    get.mockImplementation((url: string) => {
      if (url === '/workloads') {
        return Promise.resolve(page([
          { id: 'w-own', source_notebook_id: 'n-1', status: 'published' },
          { id: 'w-other', source_notebook_id: 'n-9', status: 'published' },
        ]));
      }
      if (url === '/compute/runs') {
        return Promise.resolve(page([
          { id: 'r-1', workload_id: 'w-own', status: 'succeeded', trigger: 'scheduled', started_at: '2026-10-16T04:00:00Z', finished_at: null, output_rows: 12 },
          { id: 'r-2', workload_id: 'w-other', status: 'failed', trigger: 'manual', started_at: '2026-10-16T05:00:00Z', finished_at: null, output_rows: 0 },
        ]));
      }
      return Promise.resolve(page([]));
    });

    renderWithProviders(<TaskRunRecords notebook={withContract} debugRecords={[]} />);
    await screen.findByText('正式执行');
    expect(screen.getByText('触发方式 scheduled')).toBeInTheDocument();
    expect(screen.queryByText('触发方式 manual')).toBeNull();
    // 图上的三态文案：succeeded → 执行成功（不是自造的「成功」）
    await waitFor(() => expect(screen.getByText('执行成功')).toBeInTheDocument());
  });

  it('六列逐列照图，并把「最多保留 90 天」的来源交代清楚', async () => {
    renderWithProviders(<TaskRunRecords notebook={notebook} debugRecords={[cellRecord]} />);
    await screen.findByText('调试执行');

    // 图 2-4 的六列，逐列照图、逐列同序
    expect(headerTitles()).toEqual(['执行时间', '操作类型', '执行环境', '状态', '耗时', '操作']);
    // 卡题旁的固定说明照图原文；后端没有运行记录保留期字段，所以另有一句来源披露
    expect(screen.getByText('记录调试与正式执行的历史，最多保留 90 天')).toBeInTheDocument();
    expect(screen.getByText(/后端只披露审计日志\/告警的保留期配置/)).toBeInTheDocument();
    expect(screen.getByText('查看全部记录')).toBeInTheDocument();
    expect(screen.getByText('查看日志')).toBeInTheDocument();
  });

  it('耗时只从后端起止时间算；后端没给耗时的调试行记破折号', async () => {
    const withContract = { ...notebook, contract_id: 'c-1' };
    get.mockImplementation((url: string) => {
      if (url === '/workloads') {
        return Promise.resolve(page([{ id: 'w-own', source_notebook_id: 'n-1', status: 'published' }]));
      }
      if (url === '/compute/runs') {
        return Promise.resolve(page([{
          id: 'r-1', workload_id: 'w-own', status: 'succeeded', trigger: 'scheduled',
          started_at: '2026-10-16T04:00:00Z', finished_at: '2026-10-16T04:01:20Z', output_rows: 12,
        }]));
      }
      return Promise.resolve(page([]));
    });

    renderWithProviders(<TaskRunRecords notebook={withContract} debugRecords={[cellRecord]} />);
    await screen.findByText('正式执行');

    // 第 5 列耗时：正式执行 = 后端两个时间戳之差；执行内存未下发时环境列也只能记未记录
    expect(rowCellsContaining('正式执行')[4]).toBe('1 分 20 秒');
    expect(rowCellsContaining('正式执行')[2]).toBe('执行环境 · 未记录');
    // 调试明细后端没有起止时间 → 该格记破折号（图上那一格也是破折号），不拿 0 秒充数
    expect(rowCellsContaining('调试执行')[4]).toBe('—');
    expect(screen.getAllByText('执行成功').length).toBe(2);
  });

  it('一条运行记录都没有时，六个列头照样在（空态落在表体里，不是把整张表换成空盒子）', () => {
    renderWithProviders(<TaskRunRecords notebook={notebook} debugRecords={[]} />);
    expect(screen.getByText(/本任务未记录关联合约，正式执行记录无处归位/)).toBeInTheDocument();
    expect(headerTitles()).toEqual(['执行时间', '操作类型', '执行环境', '状态', '耗时', '操作']);
    // 图 2-4 全页无分页器
    expect(document.querySelector('.ant-pagination')).toBeNull();
  });
  it('三态之外的后端状态：灰底原样透出，不静默归成执行成功/执行失败', async () => {
    const withContract = { ...notebook, contract_id: 'c-1' };
    get.mockImplementation((url: string) => {
      if (url === '/workloads') {
        return Promise.resolve(page([{ id: 'w-own', source_notebook_id: 'n-1', status: 'published' }]));
      }
      if (url === '/compute/runs') {
        return Promise.resolve(page([
          { id: 'r-skip', workload_id: 'w-own', status: 'skipped_overlap', started_at: '2026-10-16T02:00:00Z', finished_at: '2026-10-16T02:00:01Z' },
          { id: 'r-cancel', workload_id: 'w-own', status: 'cancelled', started_at: '2026-10-16T02:00:00Z', finished_at: '2026-10-16T02:00:02Z' },
        ]));
      }
      return Promise.resolve(page([]));
    });
    renderWithProviders(<TaskRunRecords notebook={withContract} debugRecords={[]} />);
    await screen.findAllByText('正式执行');
    // 图上只有 执行成功/执行失败/运行中 三态：其余后端取值原样透出，且不静默归进三态
    expect(screen.getByText('跳过（上一轮未结束）')).toBeInTheDocument();
    expect(screen.getByText('已取消')).toBeInTheDocument();
    expect(screen.queryByText('执行成功')).toBeNull();
    expect(screen.queryByText('执行失败')).toBeNull();
  });
});
