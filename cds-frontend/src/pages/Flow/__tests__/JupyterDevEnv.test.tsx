// SS-19 开发环入口：票据必须走根路径的 /api/jupyter/tickets（不是 /api/v1 前缀），
// iframe 只能用后端返回的登录地址；非 l2c 必须把「不是 MicroVM」显式挂出来。
//
// 图 1（2-3 调试环境）外框用例追加在下方：左栏「文件目录」的内容必须逐字来自
// /api/jupyter/api/contents/<会话目录> 的真实返回（含递归一层 input/），
// 加载中 / 读失败（后端原话）/ 空目录三态分开，文件行必须是能真跳的 lab/tree 深链。
// 标题卡副标题与卡头第二行取 GET /notebooks/{id} 的任务真字段；图上没有后端事实支撑的元素
// （已自动保存、绿勾、编码/换行符、内核版本、CPU/内存）一律不得上屏。
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { renderWithProviders } from '../../../test/test-utils';
import { JupyterDevEnv } from '../JupyterDevEnv';
import api from '../../../services/api';
import type { Notebook } from '../../../services/flow';

const { navigate } = vi.hoisted(() => ({ navigate: vi.fn() }));
vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router-dom')>();
  return { ...actual, useNavigate: () => navigate };
});

vi.mock('../../../services/api', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(), request: vi.fn() },
}));

// antd 响应式栅格依赖 window.matchMedia（jsdom 未实现）
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

const request = api.request as unknown as ReturnType<typeof vi.fn>;
const get = api.get as unknown as ReturnType<typeof vi.fn>;

// 标题卡副标题与卡头第二行的唯一取数口（GET /notebooks/{id}）：字段形状与 app/models/notebook.py
// 对齐，值只当数据形状用，不进实现（图上示例文案不得硬编码）。
const devTask: Notebook = {
  id: 'nb-1',
  contract_id: 'c-1',
  title: '客单价分析',
  path: 'u-1/s-1/files/task.ipynb',
  kernel: 'python3',
  language: 'python',
  content: null,
  version: 3,
  status: 'active',
  session_id: 's-1',
  execution_backend: 'cds_kernel',
  last_executed_at: null,
  created_at: '2026-08-11T09:00:00',
  // 不带时区的本地时间：副标题日期到分钟不随本机 TZ 漂移
  updated_at: '2026-08-12T14:32:00',
  task_no: 'TASK-20260812-001',
  debug_method: 'web_ide',
  export_mode: 'bundle',
  debug_memory_mb: 8192,
  exec_memory_mb: 16384,
  description: null,
  data_resource_ids: null,
};

const ticket = {
  url: '/api/jupyter/login?ticket=abc123',
  ttl: 30,
  sandbox_level: 'L0',
  session_id: 's-1',
  notebook_path: 'u-1/s-1/files/task.ipynb',
  injected: ['res-1.csv'],
};

const SESSION_DIR_URL = '/api/jupyter/api/contents/u-1/s-1/files';
const INPUT_DIR_URL = '/api/jupyter/api/contents/u-1/s-1/files/input';

const dirModel = (path: string, children: unknown[]) => ({
  name: path.split('/').pop(),
  path,
  type: 'directory',
  format: null,
  mimetype: null,
  // 目录的 size 在 Jupyter 里是子项个数，绝不允许被当成磁盘占用累加
  size: children.length,
  writable: true,
  last_modified: '2026-10-17T02:00:00Z',
  created_at: '2026-10-17T02:00:00Z',
  content: null,
  children,
});

/** 父清单里的子目录只是“部分模型”：没有 children/content 数组，必须再取一层才看得到里面的文件。 */
const dirStub = (path: string) => ({
  name: path.split('/').pop(),
  path,
  type: 'directory',
  format: null,
  size: null,
  last_modified: '2026-10-17T02:00:00Z',
  content: null,
});

const fileModel = (path: string, size: number) => ({
  name: path.split('/').pop(),
  path,
  type: 'file',
  format: null,
  size,
  writable: true,
  last_modified: '2026-10-17T02:00:01Z',
  content: null,
});

const rootListing = () =>
  dirModel('u-1/s-1/files', [
    dirStub('u-1/s-1/files/input'),
    fileModel('u-1/s-1/files/train_model.py', 1024),
    fileModel('u-1/s-1/files/SS-19 任务.ipynb', 2048),
  ]);

/** 打开开发环 → iframe 拿到会话 Cookie（onLoad）→ 左栏才会去读目录。 */
async function openDevEnv(payload: unknown = ticket) {
  request.mockResolvedValue(payload);
  renderWithProviders(<JupyterDevEnv notebookId="nb-1" />);
  fireEvent.click(screen.getByRole('button', { name: /打开 Jupyter 开发环/ }));
  const frame = await screen.findByTitle('Jupyter 开发环');
  return frame;
}

beforeEach(() => {
  request.mockReset();
  navigate.mockReset();
  get.mockReset();
  get.mockResolvedValue(devTask);
});

describe('JupyterDevEnv', () => {
  it('申请票据用的是根路径端点，并把票据登录地址交给 iframe', async () => {
    request.mockResolvedValue(ticket);
    renderWithProviders(<JupyterDevEnv notebookId="nb-1" />);
    fireEvent.click(screen.getByRole('button', { name: /打开 Jupyter 开发环/ }));

    await waitFor(() => expect(screen.getByTitle('Jupyter 开发环')).toBeInTheDocument());
    expect(request).toHaveBeenCalledTimes(1);
    expect(request.mock.calls[0][0]).toMatchObject({ baseURL: '/', url: '/api/jupyter/tickets', method: 'POST' });
    expect(request.mock.calls[0][0].data).toEqual({ notebook_id: 'nb-1' });
    expect((screen.getByTitle('Jupyter 开发环') as HTMLIFrameElement).src).toContain('/api/jupyter/login?ticket=abc123');
  });

  it('等级不是 l2c 时如实标注容器隔离，并交代注入的授权数据', async () => {
    request.mockResolvedValue(ticket);
    renderWithProviders(<JupyterDevEnv notebookId="nb-1" />);
    fireEvent.click(screen.getByRole('button', { name: /打开 Jupyter 开发环/ }));

    expect(await screen.findByText(/容器隔离，非 MicroVM/)).toBeInTheDocument();
    expect(screen.getByText(/沙箱等级 L0/)).toBeInTheDocument();
    expect(screen.getByText(/已注入 1 个文件到 input/)).toBeInTheDocument();
  });

  it('没有注入任何授权数据时不粉饰成“已就绪”', async () => {
    request.mockResolvedValue({ ...ticket, injected: [] });
    renderWithProviders(<JupyterDevEnv notebookId="nb-1" />);
    fireEvent.click(screen.getByRole('button', { name: /打开 Jupyter 开发环/ }));

    expect(await screen.findByText(/未注入/)).toBeInTheDocument();
  });

  it('供数失败时把后端原话摊在开发环上，不只给一个空目录', async () => {
    request.mockResolvedValue({
      ...ticket,
      injected: [],
      inject_errors: ['数据资源「订单流水」供数读取失败：原件不在对象存储里'],
    });
    renderWithProviders(<JupyterDevEnv notebookId="nb-1" />);
    fireEvent.click(screen.getByRole('button', { name: /打开 Jupyter 开发环/ }));

    expect(await screen.findByText(/供数异常 1 项/)).toBeInTheDocument();
    expect(screen.getByText(/原件不在对象存储里/)).toBeInTheDocument();
  });

  it('确实没有就绪供数时不凭空报“供数异常”', async () => {
    request.mockResolvedValue({ ...ticket, injected: [], inject_errors: [] });
    renderWithProviders(<JupyterDevEnv notebookId="nb-1" />);
    fireEvent.click(screen.getByRole('button', { name: /打开 Jupyter 开发环/ }));

    expect(await screen.findByText(/未注入/)).toBeInTheDocument();
    expect(screen.queryByText(/供数异常/)).not.toBeInTheDocument();
  });

  it('后端拒绝发票据时不给 iframe，只把 W2 文案摊开', async () => {
    request.mockRejectedValue({ response: { status: 503, data: { code: 'HTTP_503', message: '开发环未配置' } } });
    renderWithProviders(<JupyterDevEnv notebookId="nb-1" />);
    fireEvent.click(screen.getByRole('button', { name: /打开 Jupyter 开发环/ }));

    expect(await screen.findByText(/开发环未配置/)).toBeInTheDocument();
    expect(screen.queryByTitle('Jupyter 开发环')).not.toBeInTheDocument();
  });
});

// 实测依据（本机 2026-10-17，单跑本文件）：单条最慢 585ms，整文件 15 条 2.42s。抬到 20s 不是给
// 慢用例让路，而是沿台账里 Flow/__tests__ 的既有口径：全量并行时同一批 antd 页面用例会因 CPU
// 争用被抬到 5s 闸附近（偶发红）。没有任何用例靠 sleep/轮询时间通过——异步一律 findBy/waitFor 等信号。
describe('JupyterDevEnv · 图 1 外框与真实文件目录', { timeout: 20_000 }, () => {
  it('标题卡逐字按图：调试环境 + 副标题四段取任务真值（交付场景那句在 ⓘ 里）+ 返回列表跳我的开发', async () => {
    request.mockResolvedValue(ticket);
    renderWithProviders(<JupyterDevEnv notebookId="nb-1" />);

    expect(screen.getByText('调试环境')).toBeInTheDocument();
    // 原型副标题是「客单价分析 / WebIDE / 结果集导出 / 8G」那种四段形态：任务名 / 调试方法 /
    // 导出方式 / 调试内存，逐段取 GET /notebooks/nb-1 的真字段（枚举走中文标签、内存走「8 G」口径）。
    // 副标题是 GET /notebooks/nb-1 回来的值，等取数完成再比整串。
    expect(await screen.findByText('客单价分析 / Web IDE 在线调试 / 成果集打包导出 / 8 G')).toBeInTheDocument();
    // 多出来的「交付场景」段不删，只搬到旁边 ⓘ：悬停后逐字上屏。
    fireEvent.mouseOver(screen.getByLabelText('交付场景说明'));
    expect(await screen.findByText('交付场景：数据沙箱开发 · 配置合约、资源与关联数据后即可启动调试')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /返回列表/ }));
    expect(navigate).toHaveBeenCalledWith('/flow/tasks');
  });

  it('任务字段没下发时副标题与卡头都写未记录，不拿原型示例值补位', async () => {
    get.mockResolvedValue({
      ...devTask,
      title: '',
      language: null,
      updated_at: '',
      debug_method: null,
      export_mode: null,
      debug_memory_mb: null,
    });
    renderWithProviders(<JupyterDevEnv notebookId="nb-1" />);

    expect(screen.getByText('未记录 / 未记录 / 未记录 / 未记录')).toBeInTheDocument();
    expect(screen.getByText('更新于 未记录')).toBeInTheDocument();
    expect(screen.queryByText(/WebIDE|结果集导出/)).not.toBeInTheDocument();
  });

  it('卡头第二行只给有真值的语言与更新日期（到分钟），没自动保存事实就不上屏', async () => {
    request.mockResolvedValue(ticket);
    const { container } = renderWithProviders(<JupyterDevEnv notebookId="nb-1" />);
    fireEvent.click(screen.getByRole('button', { name: /打开 Jupyter 开发环/ }));
    await screen.findByTitle('Jupyter 开发环');

    const head = screen.getByText('task.ipynb').closest('.ant-card-head') as HTMLElement;
    expect(within(head).getByText('python')).toBeInTheDocument();
    expect(within(head).getByText('更新于 2026-08-12 14:32')).toBeInTheDocument();
    // 红线：后端没有自动保存时间可下发，绿勾与「已自动保存 HH:MM:SS」不得上屏。
    expect(within(head).queryByText(/已自动保存/)).not.toBeInTheDocument();
    expect(head.querySelector('.anticon-check-circle')).toBeNull();
    expect(container.querySelector('.anticon-check-circle')).toBeNull();
  });

  it('没打开开发环前不画任何文件树，也不偷发目录请求', async () => {
    request.mockResolvedValue(ticket);
    renderWithProviders(<JupyterDevEnv notebookId="nb-1" />);

    expect(screen.getByText('文件目录')).toBeInTheDocument();
    expect(screen.getByText(/沙箱目录要连上本会话的开发环才读得到/)).toBeInTheDocument();
    expect(screen.queryByText(/个文件/)).not.toBeInTheDocument();
    expect(request).not.toHaveBeenCalled();
  });

  it('文件目录面板与右栏等高：不按内容收缩，跟随两栏行的 stretch', async () => {
    request.mockResolvedValue(ticket);
    renderWithProviders(<JupyterDevEnv notebookId="nb-1" />);

    const panel = screen.getByText('文件目录').closest('.ant-card') as HTMLElement;
    const row = panel.parentElement as HTMLElement;
    // 原先左栏写死 alignSelf: 'flex-start' → 面板按内容收缩；图 1 里它要从 y249 拉到 ≈1358 与右栏等高。
    expect(panel.style.alignSelf).toBe('');
    expect(row.style.alignItems).toBe('stretch');
  });

  it('左栏文件树逐字来自 Contents API：按会话目录发 GET，并递归读 input/ 子目录', async () => {
    const frame = await openDevEnv();
    request.mockImplementation(async (config: { url?: string }) => {
      if (config.url === INPUT_DIR_URL) return dirModel('u-1/s-1/files/input', [fileModel('u-1/s-1/files/input/orders.csv', 512)]);
      if (config.url === SESSION_DIR_URL) return rootListing();
      throw new Error(`未预期的请求: ${config.url}`);
    });
    fireEvent.load(frame);

    expect(await screen.findByText('train_model.py')).toBeInTheDocument();
    await waitFor(() =>
      expect(request).toHaveBeenCalledWith(expect.objectContaining({ baseURL: '/', url: SESSION_DIR_URL, method: 'GET' })),
    );
    await waitFor(() =>
      expect(request).toHaveBeenCalledWith(expect.objectContaining({ baseURL: '/', url: INPUT_DIR_URL, method: 'GET' })),
    );

    const panel = screen.getByText('文件目录').closest('.ant-card') as HTMLElement;
    expect(within(panel).getByText('input')).toBeInTheDocument();
    expect(within(panel).getByText('SS-19 任务.ipynb')).toBeInTheDocument();
    expect(await within(panel).findByText('orders.csv')).toBeInTheDocument();
    // 目录的 size（子项个数）不得混进占用；1024+2048+512 = 3.5 KB
    expect(within(panel).getByText('共 3 个文件')).toBeInTheDocument();
    expect(within(panel).getByText(/占用 3\.5 KB/)).toBeInTheDocument();
    expect(within(panel).getByText(/磁盘配额未记录/)).toBeInTheDocument();
    expect(within(panel).queryByText(/128 MB|2 GB/)).not.toBeInTheDocument();
  });

  it('文件行是真深链：点 .ipynb 与子目录里的 csv 走 lab/tree，不是弹提示', async () => {
    const frame = await openDevEnv();
    request.mockImplementation(async (config: { url?: string }) => {
      if (config.url === INPUT_DIR_URL) return dirModel('u-1/s-1/files/input', [fileModel('u-1/s-1/files/input/orders.csv', 512)]);
      return rootListing();
    });
    fireEvent.load(frame);

    const notebook = await screen.findByRole('link', { name: 'SS-19 任务.ipynb' });
    expect(notebook.getAttribute('href')).toBe('/api/jupyter/lab/tree/u-1/s-1/files/SS-19%20%E4%BB%BB%E5%8A%A1.ipynb');
    expect(notebook.getAttribute('target')).toBe('_blank');
    expect(await screen.findByRole('link', { name: 'orders.csv' })).toHaveAttribute(
      'href',
      '/api/jupyter/lab/tree/u-1/s-1/files/input/orders.csv',
    );
  });

  it('文件行高按图 1 的 44px：目录行与子目录里的文件行同一行高', async () => {
    const frame = await openDevEnv();
    request.mockImplementation(async (config: { url?: string }) => {
      if (config.url === INPUT_DIR_URL) return dirModel('u-1/s-1/files/input', [fileModel('u-1/s-1/files/input/orders.csv', 512)]);
      return rootListing();
    });
    fireEvent.load(frame);

    const fileRow = (await screen.findByRole('link', { name: 'orders.csv' })).closest('div') as HTMLElement;
    const dirRow = screen.getByRole('button', { name: '折叠 input' }).closest('div') as HTMLElement;
    expect(fileRow.style.height).toBe('44px');
    expect(dirRow.style.height).toBe('44px');
  });

  // 拒的是 services/api 拦截器剥完后的 W2 体（app/core/error_handlers.py 把 HTTPException 的
  // detail 放进 message、detail 置 null），与真链路收到的形状逐字一致。
  it('目录读取失败时摊开后端原话，不退回静态假树也不报文件数', async () => {
    const frame = await openDevEnv();
    request.mockImplementation(async (config: { url?: string }) => {
      if (config.url === SESSION_DIR_URL) {
        throw { code: 'HTTP_403', message: '越出本会话目录', detail: null, request_id: 'req-403' };
      }
      return rootListing();
    });
    fireEvent.load(frame);

    expect(await screen.findByText('文件目录读取失败')).toBeInTheDocument();
    expect(screen.getByText('越出本会话目录')).toBeInTheDocument();
    expect(screen.getByText('● 沙箱已断开')).toBeInTheDocument();
    expect(screen.queryByText(/共 \d+ 个文件/)).not.toBeInTheDocument();
    expect(screen.queryByText('train_model.py')).not.toBeInTheDocument();
  });

  it('后端返回空目录就照实说空，不画行也不给占用数字', async () => {
    const frame = await openDevEnv();
    request.mockImplementation(async (config: { url?: string }) => {
      if (config.url === SESSION_DIR_URL) return dirModel('u-1/s-1/files', []);
      return rootListing();
    });
    fireEvent.load(frame);

    expect(await screen.findByText(/沙箱目录为空/)).toBeInTheDocument();
    expect(screen.getByText('● 沙箱已连接')).toBeInTheDocument();
    expect(screen.queryByText(/共 \d+ 个文件/)).not.toBeInTheDocument();
    expect(screen.queryByText(/占用/)).not.toBeInTheDocument();
  });

  it('刷新图标按钮真的再取一次目录', async () => {
    const frame = await openDevEnv();
    request.mockImplementation(async (config: { url?: string }) => {
      if (config.url === INPUT_DIR_URL) return dirModel('u-1/s-1/files/input', [fileModel('u-1/s-1/files/input/orders.csv', 512)]);
      return rootListing();
    });
    fireEvent.load(frame);
    await screen.findByText('train_model.py');

    fireEvent.click(screen.getByRole('button', { name: '刷新文件目录' }));
    await waitFor(() =>
      expect(
        request.mock.calls.filter((call) => (call[0] as { url?: string }).url === SESSION_DIR_URL),
      ).toHaveLength(2),
    );
  });

  it('子目录读失败时父清单照常列出，并标注不完整', async () => {
    const frame = await openDevEnv();
    request.mockImplementation(async (config: { url?: string }) => {
      if (config.url === INPUT_DIR_URL) {
        throw {
          code: 'HTTP_502',
          message: '开发环上游不可达（cds-jupyter 未就绪）',
          detail: null,
          request_id: 'req-502',
        };
      }
      return rootListing();
    });
    fireEvent.load(frame);

    expect(await screen.findByText('train_model.py')).toBeInTheDocument();
    expect(screen.getByText(/开发环上游不可达/)).toBeInTheDocument();
    expect(screen.getByText(/1 个子目录读取失败，其内容未计入/)).toBeInTheDocument();
    expect(screen.getByText('共 2 个文件')).toBeInTheDocument();
  });

  it('后端没下发目录前缀时不给猜测的树，直说缺目录前缀', async () => {
    const frame = await openDevEnv({ ...ticket, notebook_path: 'task.ipynb' });
    fireEvent.load(frame);

    expect(screen.getByText(/后端未下发本会话的目录前缀/)).toBeInTheDocument();
    await waitFor(() => expect(request).toHaveBeenCalledTimes(1));
    expect(request.mock.calls[0][0]).toMatchObject({ url: '/api/jupyter/tickets' });
  });
});
