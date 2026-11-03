import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import api from '../../../services/api';
import type { ProjectDetail } from '../../../services/projectApi';
import { renderWithProviders } from '../../../test/test-utils';
import ProjectCenter from '../ProjectCenter';

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
const post = api.post as unknown as ReturnType<typeof vi.fn>;
const put = api.put as unknown as ReturnType<typeof vi.fn>;

const { messageSpy } = vi.hoisted(() => ({
  messageSpy: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn(), loading: vi.fn() },
}));

// 静态 message 在 React 19 下不渲染 DOM（见 src/antdReact19Patch.ts），toast 只能接管参数断言。
vi.mock('antd', async (importOriginal) => {
  const actual = await importOriginal<typeof import('antd')>();
  return { ...actual, message: messageSpy as unknown as typeof actual.message };
});

const envelope = <T,>(items: T[]) => ({ items, total: items.length, page: 1, page_size: 20 });

const DETAIL: ProjectDetail = {
  id: 'p1',
  name: '多中心影像联合建模',
  description: '三家医院联合训练肺结节模型',
  team_id: 'team-1',
  scenario_id: null,
  contract_id: null,
  environment_type: 'l2c',
  preferred_language: 'r',
  status: 'active',
  participants: [{ org_name: '数据提供方A', role: 'owner' }],
  milestones: [{ title: '模型评估通过', due_date: '2026-12-31', status: 'pending' }],
  dag_definition: {
    version: 1,
    nodes: [
      { id: 'n1', label: '数据接入', type: 'input', language: 'r' },
      { id: 'n2', label: '特征工程', type: 'feature_eng', language: 'r' },
    ],
    edges: [{ source: 'n1', target: 'n2' }],
  },
  linked_notebook_ids: ['nb-1'],
  linked_workload_ids: ['wl-1'],
  created_by: 'u-1',
  created_at: '2026-11-03T02:00:00Z',
  updated_at: '2026-11-03T02:10:00Z',
  notebooks: [
    { id: 'nb-1', title: '清洗草稿', language: 'r', status: 'draft', task_no: null, updated_at: null },
  ],
  workloads: [{ id: 'wl-1', name: '批量评分', language: 'pyspark', env: 'l2c', status: 'active', updated_at: null }],
  unresolved_notebook_ids: [],
  unresolved_workload_ids: [],
};

const VALID = {
  valid: true,
  execution_order: ['n1', 'n2'],
  cyclic_nodes: [],
  errors: [],
  node_count: 2,
  edge_count: 1,
};

function route() {
  get.mockImplementation((url: string) => {
    if (url === '/flow/projects') return Promise.resolve(envelope([DETAIL]));
    if (url === '/flow/projects/p1') return Promise.resolve(DETAIL);
    return Promise.resolve(undefined);
  });
  post.mockImplementation((url: string) => {
    if (url === '/flow/projects/p1/dag/validate') return Promise.resolve(VALID);
    if (url === '/flow/projects/p1/submit-review') {
      return Promise.resolve({ id: 'p1', status: 'submitted_review', name: DETAIL.name });
    }
    if (url === '/flow/projects') return Promise.resolve({ ...DETAIL, name: '新的联合建模项目' });
    return Promise.resolve(undefined);
  });
  put.mockImplementation((url: string) => {
    if (url === '/flow/projects/p1/dag') return Promise.resolve(DETAIL);
    return Promise.resolve(undefined);
  });
}

async function openBoard(): Promise<HTMLElement> {
  // antd 会在两字按钮里插空格（DOM 里是「看 板」），所以用位类正则认。
  fireEvent.click(await screen.findByRole('button', { name: /看\s*板/ }));
  return screen.findByText('DAG 任务编排');
}

function drawerBody(): HTMLElement {
  const body = document.querySelector('.ant-drawer-body');
  if (!body) throw new Error('项目看板抽屉未渲染');
  return body as HTMLElement;
}

beforeEach(() => {
  vi.clearAllMocks();
  route();
});

describe('ProjectCenter', () => {
  it('列表按统一分页信封渲染项目、语言与画布节点数', async () => {
    renderWithProviders(<ProjectCenter />);

    expect(await screen.findByText('多中心影像联合建模')).toBeInTheDocument();
    expect(screen.getByText('三家医院联合训练肺结节模型')).toBeInTheDocument();
    expect(screen.getByText('进行中')).toBeInTheDocument();
    expect(screen.getByText('共 1 个项目')).toBeInTheDocument();
    expect(get).toHaveBeenCalledWith('/flow/projects', {
      params: { page: 1, page_size: 20, keyword: undefined, status: undefined, preferred_language: undefined },
    });
  });

  it('新建项目把多方协作者与里程碑按后端结构提交', async () => {
    renderWithProviders(<ProjectCenter />);

    fireEvent.click(await screen.findByRole('button', { name: /新建项目/ }));
    expect(await screen.findByText('多方协作者')).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText('例如：多中心影像联合建模'), {
      target: { value: '新的联合建模项目' },
    });
    fireEvent.change(screen.getByPlaceholderText('机构方，如 数据提供方A'), { target: { value: '数据提供方B' } });

    const footer = document.querySelector('.ant-modal-footer') as HTMLElement;
    fireEvent.click(within(footer).getByRole('button', { name: /创建项目/ }));

    await waitFor(() =>
      expect(post).toHaveBeenCalledWith(
        '/flow/projects',
        expect.objectContaining({
          name: '新的联合建模项目',
          preferred_language: 'python',
          environment_type: 'l2c',
          participants: [{ org_name: '数据提供方B', username: undefined, role: 'collaborator' }],
          milestones: [{ title: '编排与自检', due_date: undefined, status: 'pending' }],
        }),
      ),
    );
    await waitFor(() => expect(messageSpy.success).toHaveBeenCalled());
  });

  it('看板抽屉展示协同名册、里程碑与联动画作', async () => {
    renderWithProviders(<ProjectCenter />);
    await openBoard();

    const body = drawerBody();
    expect(await within(body).findByText('数据提供方A · owner')).toBeInTheDocument();
    expect(within(body).getByText(/模型评估通过 · 未开始 · 到期 2026-12-31/)).toBeInTheDocument();
    expect(within(body).getByText('Notebook · 清洗草稿 · r · draft')).toBeInTheDocument();
    expect(within(body).getByText('负载 · 批量评分 · pyspark · l2c · active')).toBeInTheDocument();
    expect(get).toHaveBeenCalledWith('/flow/projects/p1');
  });

  it('保存画布把节点与既有依赖一起 PUT 到 dag 面', async () => {
    renderWithProviders(<ProjectCenter />);
    await openBoard();

    const body = drawerBody();
    fireEvent.change(within(body).getByPlaceholderText('节点标识 clean_01'), { target: { value: 'n3' } });
    fireEvent.change(within(body).getByPlaceholderText('节点名称，如 数据清洗'), { target: { value: '成果输出' } });
    fireEvent.click(within(body).getByRole('button', { name: /添加节点/ }));

    expect(await within(body).findByText('有未保存修改')).toBeInTheDocument();
    fireEvent.click(within(body).getByRole('button', { name: /保存编排/ }));

    await waitFor(() =>
      expect(put).toHaveBeenCalledWith('/flow/projects/p1/dag', {
        nodes: [
          { id: 'n1', label: '数据接入', type: 'input', language: 'r' },
          { id: 'n2', label: '特征工程', type: 'feature_eng', language: 'r' },
          { id: 'n3', label: '成果输出', type: 'task', language: undefined },
        ],
        edges: [{ source: 'n1', target: 'n2' }],
      }),
    );
    await waitFor(() => expect(messageSpy.success).toHaveBeenCalledWith('任务编排已保存'));
  });

  it('一键拓扑校验回显后端 Kahn 执行顺序', async () => {
    renderWithProviders(<ProjectCenter />);
    await openBoard();

    const body = drawerBody();
    fireEvent.click(within(body).getByRole('button', { name: /一键拓扑校验/ }));

    expect(await within(body).findByText('无环，执行顺序共 2 个节点')).toBeInTheDocument();
    expect(within(body).getByText('1. n1')).toBeInTheDocument();
    expect(within(body).getByText('2. n2')).toBeInTheDocument();
    expect(post).toHaveBeenCalledWith('/flow/projects/p1/dag/validate', {});
  });

  it('后端判环时如实点名环路节点，不显示执行顺序', async () => {
    post.mockImplementation((url: string) =>
      Promise.resolve(
        url.endsWith('/dag/validate')
          ? {
              valid: false,
              execution_order: [],
              cyclic_nodes: ['n1', 'n2'],
              errors: ['画布存在环路，无法确定执行顺序'],
              node_count: 2,
              edge_count: 2,
            }
          : undefined,
      ),
    );
    renderWithProviders(<ProjectCenter />);
    await openBoard();

    const body = drawerBody();
    fireEvent.click(within(body).getByRole('button', { name: /一键拓扑校验/ }));

    expect(await within(body).findByText('拓扑校验未通过')).toBeInTheDocument();
    expect(within(body).getByText('画布存在环路，无法确定执行顺序')).toBeInTheDocument();
    expect(within(body).getByText('环路节点：n1 → n2')).toBeInTheDocument();
    expect(messageSpy.error).toHaveBeenCalledWith('拓扑校验未通过，请按提示修正画布');
  });

  it('引用清单里的失效动作单独告警，不静默少一条', async () => {
    get.mockImplementation((url: string) => {
      if (url === '/flow/projects') return Promise.resolve(envelope([DETAIL]));
      if (url === '/flow/projects/p1') {
        return Promise.resolve({ ...DETAIL, notebooks: [], unresolved_notebook_ids: ['nb-gone'] });
      }
      return Promise.resolve(undefined);
    });
    renderWithProviders(<ProjectCenter />);
    await openBoard();

    const body = drawerBody();
    expect(await within(body).findByText('存在失效引用')).toBeInTheDocument();
    expect(within(body).getByText(/1 个 Notebook、0 个负载已不存在/)).toBeInTheDocument();
  });

  it('提交审核打 submit-review 并提示画布随后冻结', async () => {
    renderWithProviders(<ProjectCenter />);
    await openBoard();

    const submit = await screen.findByRole('button', { name: /提交审核/ });
    fireEvent.click(submit);

    await waitFor(() => expect(post).toHaveBeenCalledWith('/flow/projects/p1/submit-review', {}));
    await waitFor(() =>
      expect(messageSpy.success).toHaveBeenCalledWith(`项目「${DETAIL.name}」已提交审核，画布随后冻结`),
    );
  });
});
