// SS-23「调试环境」外框渲染测试（原型图 1 / 2-3）。
//
// 锁两件事：
// 1) 外框的逐字槽位（标题卡、左栏、工具条五项、状态条、输出卡页签）与图 1 一致；
// 2) 铁律「没有假控件、没有假数字」——每个数字都能追到一条被 mock 的后端响应，
//    没有计量位的地方必须出现「未计量 / 未记录 / 未接入」，图标按钮必须打到真接口。
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { renderWithProviders } from '../../../test/test-utils';
import NotebookWorkbench, { AUTO_SAVE_DEBOUNCE_MS } from '../NotebookWorkbench';
import {
  NBW_SUBTITLE,
  TERMINAL_TAB_NOTE,
} from '../notebookWorkbenchModel';
import api from '../../../services/api';

vi.mock('@monaco-editor/react', () => ({ default: () => null }));
vi.mock('../monacoSetup', () => ({}));

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
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(), request: vi.fn() },
}));

const get = api.get as unknown as ReturnType<typeof vi.fn>;
const post = api.post as unknown as ReturnType<typeof vi.fn>;
const put = api.put as unknown as ReturnType<typeof vi.fn>;
const del = api.delete as unknown as ReturnType<typeof vi.fn>;

const NOTEBOOK = {
  id: 'n-1',
  contract_id: 'c-1',
  title: '流失评分',
  path: '/nb/churn.ipynb',
  kernel: 'python3',
  language: 'python',
  content: { cells: [{ cell_type: 'code', source: 'print(1)' }] },
  version: 3,
  status: 'draft',
  session_id: 's-1',
  execution_backend: 'cds_kernel',
  last_executed_at: '2026-10-17T08:00:09Z',
  debug_memory_mb: 1024,
  created_at: '2026-10-01T00:00:00Z',
  updated_at: '2026-10-17T07:59:00Z',
};

const pageOf = <T,>(items: T[]) => ({ items, total: items.length, page: 1, page_size: 50 });

const okLog = {
  id: 'l-3',
  action: 'sandbox.execute',
  created_at: '2026-10-17T08:00:09Z',
  user_id: 'u-1',
  detail: { language: 'python', exit_code: 0, duration_ms: 200 },
};

interface Stack {
  status: string;
  logs: unknown[];
  count: number;
  quota?: unknown;
  /** 把配额接口探错（验证分母不会回落成任何默认档位） */
  quotaDown?: boolean;
  /** 覆盖 /files 的返回（后端对 VM 内工作区会直说 files=null） */
  filesRaw?: unknown;
  /** GET /sandbox-sessions/{id}/metering 的返回（缺省＝后端读不到 cgroup） */
  metering?: unknown;
}

/** 只装 mock 好的后端响应；每个上屏的数字都出自这里某一行。 */
function mockStack({ status, logs, count, quota, quotaDown, filesRaw, metering }: Stack) {
  get.mockImplementation((url: string) => {
    if (url === '/notebooks') return Promise.resolve(pageOf([NOTEBOOK]));
    if (url === '/notebooks/n-1') return Promise.resolve(NOTEBOOK);
    if (url === '/contracts') return Promise.resolve(pageOf([]));
    if (url === '/deliveries/quota-usage') {
      if (quotaDown) return Promise.reject(new Error('配额接口挂了'));
      return Promise.resolve(quota === undefined ? {} : quota);
    }
    if (url === '/sandbox-sessions/s-1') {
      return Promise.resolve({ id: 's-1', status, resource_limits: { cpu_cores: 1, memory_mb: 512, disk_mb: 1024 } });
    }
    if (url === '/sandbox-sessions/s-1/files') {
      if (filesRaw !== undefined) return Promise.resolve(filesRaw);
      // 后端真实键是 filename（app/services/session_files.py:92），这里就按真载荷给。
      return Promise.resolve({ files: [{ filename: 'notes.md', size: 2048 }, { filename: 'train.py', size: 1024 }] });
    }
    if (url === '/sandbox-sessions/s-1/usage') {
      return Promise.resolve({ workspace: { files: 2, bytes: 3072 }, uploaded_files: { count: 2, bytes: 3072 } });
    }
    if (url === '/sandbox-sessions/s-1/logs') return Promise.resolve({ logs, count });
    if (url === '/sandbox-sessions/s-1/metering') {
      return Promise.resolve(metering ?? { available: false, reason: '运行时无可读 cgroup', cpu_percent: null, memory_used_mb: null, memory_limit_mb: null, pids: null });
    }
    return Promise.resolve({});
  });
}

function renderWorkbench() {
  return renderWithProviders(<NotebookWorkbench />, { route: '/notebooks?task=n-1' });
}

beforeEach(() => {
  vi.clearAllMocks();
  post.mockImplementation((url: string) => Promise.resolve({ id: 's-1', status: 'terminated', url }));
  put.mockImplementation((url: string) => Promise.resolve({ ...NOTEBOOK, version: 4, updated_at: '2026-10-17T09:00:00Z', url }));
  del.mockImplementation(() => Promise.resolve({}));
});

describe('调试环境外框 · 标题卡与左栏文件目录', () => {
  it('标题卡只有「返回列表」一个按钮，没有状态徽标；副标题逐字', async () => {
    mockStack({ status: 'running', logs: [okLog], count: 1 });
    renderWorkbench();

    expect(await screen.findByText('调试环境')).toBeInTheDocument();
    expect(screen.getByText(NBW_SUBTITLE)).toBeInTheDocument();

    const card = document.querySelector('.nbw-titlecard') as HTMLElement;
    expect(card).not.toBeNull();
    const buttons = within(card).getAllByRole('button');
    expect(buttons).toHaveLength(1);
    expect(buttons[0]).toHaveTextContent('返回列表');
    expect(within(card).queryByText(/running|ready|L2|准备中/i)).toBeNull();
  });

  it('左栏只有「文件目录」+ 一个刷新图标钮，没有搜索框；行=勾选框+图标+名字', async () => {
    mockStack({ status: 'running', logs: [okLog], count: 1 });
    renderWorkbench();

    const left = await screen.findByRole('complementary', { name: '文件目录' });
    expect(within(left).getAllByRole('button', { name: '刷新文件目录' })).toHaveLength(1);
    expect(left.querySelectorAll('.nbw-card-head button')).toHaveLength(1);
    expect(left.querySelectorAll('input[placeholder]')).toHaveLength(0);
    expect(within(left).queryByRole('button', { name: /新建|上传/ })).toBeNull();

    await waitFor(() => expect(left.querySelectorAll('.nbw-file-row')).toHaveLength(2));
    expect(within(left).getByLabelText('选择 train.py')).toBeEnabled();
    expect(within(left).getByText('train.py')).toBeInTheDocument();
    expect(within(left).getByText('notes.md')).toBeInTheDocument();
  });

  it('底部两行统计来自 files/usage/quota 三条真响应，分母是租户池 20 G 而不是原型的 2 GB', async () => {
    mockStack({
      status: 'running',
      logs: [okLog],
      count: 1,
      quota: { tenant_resources: { limits: { cpu_cores: 8, memory_mb: 8192, disk_mb: 20480 } } },
    });
    renderWorkbench();

    const left = await screen.findByRole('complementary', { name: '文件目录' });
    await waitFor(() => expect(left).toHaveTextContent('共 2 个文件'));
    expect(left).toHaveTextContent('占用 3.0 KB / 20 G');
    expect(left).not.toHaveTextContent('2 GB');
  });

  it('后端说 files=null（工作区在 VM 内）时不画成空目录，也不统计 0 个文件', async () => {
    mockStack({ status: 'running', logs: [okLog], count: 1, filesRaw: { files: null } });
    renderWorkbench();

    const left = await screen.findByRole('complementary', { name: '文件目录' });
    await waitFor(() => expect(left).toHaveTextContent('files=null'));
    expect(left).not.toHaveTextContent('共 0 个文件');
    expect(left.querySelectorAll('.nbw-file-row')).toHaveLength(0);
  });

  it('配额查不到时分母写「配额未记录」，不回落到任何默认档位', async () => {
    mockStack({ status: 'running', logs: [okLog], count: 1, quotaDown: true });
    renderWorkbench();

    const left = await screen.findByRole('complementary', { name: '文件目录' });
    await waitFor(() => expect(left).toHaveTextContent('配额未记录'));
    expect(left).toHaveTextContent('共 2 个文件');
  });
});

describe('调试环境外框 · 编辑器卡工具条与状态条', () => {
  it('工具条左侧就是文件/编辑/查看/语言/运行五项，右侧是缩进、编码与连接态', async () => {
    mockStack({ status: 'running', logs: [okLog], count: 1 });
    const { container } = renderWorkbench();
    await screen.findByText('churn.ipynb');

    expect(Array.from(container.querySelectorAll('.nbw-menu-label')).map((node) => node.textContent)).toEqual([
      '文件',
      '编辑',
      '查看',
      '语言',
      '运行',
    ]);
    expect(screen.getByText('缩进：4 空格')).toBeInTheDocument();
    expect(screen.getByText('编码：UTF-8')).toBeInTheDocument();
    // 连接态要等 GET /sandbox-sessions/{id} 回来说 running，不能一进来就画绿点。
    expect(await screen.findByText('● 沙箱已连接')).toBeInTheDocument();
    // 卡头文案只有在防抖 PUT 真发生之后才允许出现——没有任何编辑时不得预先写「已自动保存」。
    expect(screen.queryByText(/已自动保存/)).toBeNull();
    expect(screen.getByText('无改动')).toBeInTheDocument();
  });

  it('状态条（无计量口时）：metering 回 available:false 就只能写未计量；分母是会话真配额', async () => {
    mockStack({ status: 'running', logs: [okLog], count: 1 });
    renderWorkbench();

    // 配额分母取会话 resource_limits.memory_mb，这条要等 GET /sandbox-sessions/{id}。
    expect(await screen.findByText('内存 未计量 / 0.5 G 配额')).toBeInTheDocument();
    expect(screen.getByText('行 —, 列 —')).toBeInTheDocument();
    expect(screen.getByText('UTF-8')).toBeInTheDocument();
    expect(screen.getByText('LF')).toBeInTheDocument();
    expect(screen.getByText('python · 版本未计量')).toBeInTheDocument();
    expect(screen.getByText('CPU 未计量')).toBeInTheDocument();
    // 画一个读不到时的 CPU 百分比就是编数字
    expect(screen.queryByText(/CPU\s+\d/)).toBeNull();
    expect(screen.queryByText(/内存\s+\d+(\.\d+)?\s*G\s*\//)).toBeNull();
  });

  it('状态条（有计量时）：cgroup 真读数上屏 CPU% 与 内存 用量/上限（D-e2e-48）', async () => {
    mockStack({
      status: 'running',
      logs: [okLog],
      count: 1,
      metering: {
        available: true,
        source: '/sys/fs/cgroup/cds-l0/s1',
        cpu_percent: 12.3,
        memory_used_mb: 3891.2,
        memory_limit_mb: 8192,
        pids: 7,
        sampled_ms: 250,
      },
    });
    renderWorkbench();

    expect(await screen.findByText('CPU 12.3%')).toBeInTheDocument();
    expect(await screen.findByText('内存 3.8 G / 8 G 配额')).toBeInTheDocument();
    // 有读数时不得再出现「未计量」占位
    expect(screen.queryByText('CPU 未计量')).toBeNull();
  });

  it('编辑后前端防抖自动保存，卡头文案变「已自动保存」（D-e2e-48）', async () => {
    mockStack({ status: 'running', logs: [okLog], count: 1 });
    renderWorkbench();
    expect(await screen.findByText('无改动')).toBeInTheDocument();

    fireEvent.click(screen.getByText('新增单元格'));
    // 防抖窗口内不得提前 PUT（订阅结果事件，不靠 sleep 碰运气）
    await new Promise((r) => setTimeout(r, Math.max(0, AUTO_SAVE_DEBOUNCE_MS - 700)));
    expect(put).not.toHaveBeenCalled();
    await waitFor(
      () => expect(put).toHaveBeenCalledWith('/notebooks/n-1', expect.objectContaining({ content: expect.anything() })),
      { timeout: AUTO_SAVE_DEBOUNCE_MS + 4000 },
    );
    await waitFor(() => expect(screen.getByText(/已自动保存/)).toBeInTheDocument(), { timeout: 3000 });
    // 自动保存静默（不弹 toast），只在卡头报状态
    expect(screen.queryByText(/已保存（v4）/)).toBeNull();
  });

  it('会话没在 running 时连接点是灰的「● 沙箱未连接」，不因会话存在就点亮', async () => {
    mockStack({ status: 'ready', logs: [okLog], count: 1 });
    renderWorkbench();

    expect(await screen.findByText('● 沙箱未连接（ready）')).toBeInTheDocument();
    expect(screen.queryByText('● 沙箱已连接')).toBeNull();
  });
});

describe('调试环境外框 · 输出卡', () => {
  it('页签是 输出/问题 N/终端；执行态与日志正文都取自审计流', async () => {
    mockStack({ status: 'running', logs: [okLog], count: 1 });
    renderWorkbench();

    expect(await screen.findByText('执行成功 · 0.2s')).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '输出' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tab', { name: '问题 0' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '终端' })).toBeInTheDocument();
    expect(screen.getByText('执行 · language=python · exit_code=0 · duration_ms=200')).toBeInTheDocument();
    expect(screen.getByText(/>_ 沙箱会话 s-1 · 本页读的是后端会话审计流最近 1 条/)).toBeInTheDocument();
    expect(screen.getByText(/查看完整日志/)).toBeInTheDocument();
  });

  it('终端页签不复制一套假终端，只说明命令行的真去处', async () => {
    mockStack({ status: 'running', logs: [okLog], count: 1 });
    renderWorkbench();

    fireEvent.click(await screen.findByRole('tab', { name: '终端' }));
    expect(await screen.findByText(TERMINAL_TAB_NOTE)).toBeInTheDocument();
  });

  it('停止图标钮打到真接口 POST /sandbox-sessions/{id}/terminate（二次确认后）', async () => {
    mockStack({ status: 'running', logs: [okLog], count: 1 });
    renderWorkbench();

    const stop = await screen.findByRole('button', { name: '终止沙箱会话' });
    // 只有后端说 running 才允许终止：先等到会话状态回来。
    await waitFor(() => expect(stop).toBeEnabled());
    fireEvent.click(stop);
    expect(await screen.findByRole('dialog', { name: /终止沙箱会话/ })).toBeInTheDocument();
    expect(post).not.toHaveBeenCalled();
    // antd 会在两个汉字的按钮文案里插空格（「终 止」），所以名称用宽松匹配。
    fireEvent.click(screen.getByRole('button', { name: /^终\s*止$/ }));

    await waitFor(() => expect(post).toHaveBeenCalledWith('/sandbox-sessions/s-1/terminate'));
  });

  it('垃圾桶只是清空视图：不发任何删除请求，后端日志一条不动', async () => {
    mockStack({ status: 'running', logs: [okLog], count: 1 });
    renderWorkbench();

    expect(await screen.findByText('执行 · language=python · exit_code=0 · duration_ms=200')).toBeInTheDocument();
    const trash = screen.getByRole('button', { name: '清空本页输出显示' });
    fireEvent.click(trash);

    await waitFor(() =>
      expect(screen.queryByText('执行 · language=python · exit_code=0 · duration_ms=200')).toBeNull(),
    );
    expect(del).not.toHaveBeenCalled();
    expect(post).not.toHaveBeenCalled();
  });

  it('审计流里一条执行都没有就写「尚未执行」，会话已终态则停止钮禁用并说明原因', async () => {
    mockStack({ status: 'terminated', logs: [], count: 0 });
    renderWorkbench();

    expect(await screen.findByText('● 沙箱未连接')).toBeInTheDocument();
    expect(screen.getByText('尚未执行')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '终止沙箱会话' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '清空本页输出显示' })).toBeDisabled();
    await waitFor(() =>
      expect(screen.getByRole('complementary', { name: '文件目录' })).toHaveTextContent('沙箱会话已是终态（status=terminated）'),
    );
  });
});

describe('调试环境外框 · 工具条菜单都是真动作', () => {
  it('运行菜单里的「运行全部单元格」走后端 run 接口，不是空下拉', async () => {
    mockStack({ status: 'running', logs: [okLog], count: 1 });
    post.mockImplementation((url: string) => Promise.resolve({ ok: true, backend: 'cds_kernel', results: [], url }));
    const { container } = renderWorkbench();

    // 单元格里也有一个「运行」按钮，这里点的是工具条那项下拉。
    fireEvent.click(await screen.findByText('运行', { selector: '.nbw-menu-label' }));
    fireEvent.click(await screen.findByText('运行全部单元格'));

    await waitFor(() =>
      expect(
        post.mock.calls.map((call) => String(call[0])).some((url) => /^\/notebooks\/n-1\/(run|cells\/execute)$/.test(url)),
      ).toBe(true),
    );
    expect(container).toBeTruthy();
  });

  it('语言菜单是只读口径：language 有真字段，沙箱内解释器版本没有任何接口', async () => {
    mockStack({ status: 'running', logs: [okLog], count: 1 });
    renderWorkbench();

    fireEvent.click(await screen.findByText('语言', { selector: '.nbw-menu-label' }));
    expect(await screen.findByText(/创建时确定，PUT \/notebooks 载荷里没有 language/)).toBeInTheDocument();
  });
});
