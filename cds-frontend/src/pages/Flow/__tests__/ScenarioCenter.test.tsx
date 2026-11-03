import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import api from '../../../services/api';
import type { Scenario, ScenarioProjectBrief } from '../../../services/scenarioApi';
import { renderWithProviders } from '../../../test/test-utils';
import ScenarioCenter from '../ScenarioCenter';

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

const { messageSpy } = vi.hoisted(() => ({
  messageSpy: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn(), loading: vi.fn() },
}));

// 静态 message 在 React 19 下不渲染 DOM（项目已知事实，见 src/antdReact19Patch.ts），toast 只能接管参数断言。
vi.mock('antd', async (importOriginal) => {
  const actual = await importOriginal<typeof import('antd')>();
  return { ...actual, message: messageSpy as unknown as typeof actual.message };
});

const envelope = <T,>(items: T[]) => ({ items, total: items.length, page: 1, page_size: 20 });

function openDeliverySceneSelect() {
  const placeholder = Array.from(document.querySelectorAll('.ant-select-selection-placeholder')).find((el) =>
    (el.textContent ?? '').includes('交付场景'),
  );
  const select = placeholder?.closest('.ant-select') as HTMLElement;
  fireEvent.mouseDown(select.querySelector('.ant-select-selector') as Element);
}

function openDropdown(): HTMLElement {
  const dropdowns = document.querySelectorAll('.ant-select-dropdown:not(.ant-select-dropdown-hidden)');
  const last = dropdowns[dropdowns.length - 1];
  if (!last) throw new Error('下拉未打开');
  return last as HTMLElement;
}

function modalFooter(): HTMLElement {
  const footer = document.querySelector('.ant-modal-footer');
  if (!footer) throw new Error('弹窗底部未渲染');
  return footer as HTMLElement;
}

const PROJECT: ScenarioProjectBrief = {
  id: 'prj-1',
  name: '肺结节联合建模',
  status: 'active',
  team_id: 'team-1',
  scenario_id: 'scn-2',
  environment_type: 'l2c',
  preferred_language: 'python',
  participants: [],
  scenario_context: { delivery_scene: 'api' },
  created_at: '2026-11-03T02:00:00Z',
  updated_at: '2026-11-03T02:10:00Z',
};

const PUBLISHED: Scenario = {
  id: 'scn-1',
  title: '医疗影像科研协作场景',
  industry: '医疗',
  business_background: '三家医院联合训练肺结节模型',
  target_description: '输出模型权重与评估报告',
  delivery_scene: 'tee_compute',
  status: 'published',
  suggested_resource_ids: ['res-1'],
  compliance_constraints: { output_fields: '仅汇总指标' },
  delivery_tech_config: { tee_image: 'tee-cuda:12.4' },
  accepted_team_id: null,
  accepted_by: null,
  created_by: 'u-1',
  created_at: '2026-11-03T01:00:00Z',
  updated_at: '2026-11-03T01:00:00Z',
  project: null,
};

const ACCEPTED: Scenario = {
  ...PUBLISHED,
  id: 'scn-2',
  title: '金融风控联合建模',
  industry: '金融',
  delivery_scene: 'api',
  status: 'accepted',
  delivery_tech_config: { endpoint: '/risk/score' },
  accepted_team_id: 'team-1',
  accepted_by: 'u-2',
  project: PROJECT,
};

function route(scenarios: Scenario[] = [PUBLISHED, ACCEPTED], mine: Scenario[] = [ACCEPTED]) {
  get.mockImplementation((url: string, config?: { params?: Record<string, unknown> }) => {
    if (url === '/flow/scenarios') return Promise.resolve(envelope(scenarios));
    if (url === '/flow/scenarios/mine') return Promise.resolve(envelope(mine));
    if (url === '/flow/scenarios/scn-1') return Promise.resolve(PUBLISHED);
    if (url === '/users/teams') return Promise.resolve(envelope([]));
    void config;
    return Promise.resolve(undefined);
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  route();
});

describe('ScenarioCenter', () => {
  it('列表按统一分页信封渲染场景与承接状态', async () => {
    renderWithProviders(<ScenarioCenter />);

    expect(await screen.findByText('医疗影像科研协作场景')).toBeInTheDocument();
    expect(screen.getByText('金融风控联合建模')).toBeInTheDocument();
    expect(screen.getByText('已发布待承接')).toBeInTheDocument();
    expect(screen.getByText('已承接')).toBeInTheDocument();
    expect(screen.getByText('待承接')).toBeInTheDocument();
    expect(screen.getByText('已有人承接')).toBeInTheDocument();
    expect(screen.getByText('共 2 个场景')).toBeInTheDocument();
    expect(get).toHaveBeenCalledWith('/flow/scenarios', {
      params: { page: 1, page_size: 20, keyword: undefined, status: undefined },
    });
  });

  it('关键词搜索把 keyword 带进同一条列表请求', async () => {
    renderWithProviders(<ScenarioCenter />);
    const search = await screen.findByPlaceholderText('搜索场景标题 / 行业');

    fireEvent.change(search, { target: { value: '风控' } });
    fireEvent.keyDown(search, { key: 'Enter', code: 'Enter' });

    await waitFor(() =>
      expect(get).toHaveBeenCalledWith('/flow/scenarios', {
        params: { page: 1, page_size: 20, keyword: '风控', status: undefined },
      }),
    );
  });

  it('数据交付场景页签换成预埋视角，并能按 delivery_scene 收窄', async () => {
    route([PUBLISHED, { ...ACCEPTED, delivery_tech_config: {} }]);
    renderWithProviders(<ScenarioCenter />);
    fireEvent.click(await screen.findByRole('tab', { name: '数据交付场景' }));

    expect(await screen.findByText('交付预埋')).toBeInTheDocument();
    expect(screen.getByText('未预埋')).toBeInTheDocument();

    const selects = document.querySelectorAll('.ant-select');
    expect(selects.length).toBeGreaterThan(1);
    openDeliverySceneSelect();

    await waitFor(() => expect(document.querySelector('.ant-select-dropdown')).not.toBeNull());
    const dropdown = openDropdown();
    // 行内 Tag 里也有「TEE 密态交付」，只在下拉容器内认选项才不会误点成行。
    fireEvent.click(within(dropdown).getByText('TEE 密态交付'));

    await waitFor(() =>
      expect(get).toHaveBeenCalledWith('/flow/scenarios', expect.objectContaining({
        params: expect.objectContaining({ delivery_scene: 'tee_compute' }),
      })),
    );
  });

  it('一键承接弹窗如实展示将被注入的约束项数', async () => {
    route();
    post.mockImplementation((url: string) =>
      Promise.resolve(
        url.endsWith('/accept')
          ? { scenario: { ...PUBLISHED, status: 'accepted' }, project: { ...PROJECT, scenario_id: 'scn-1' } }
          : undefined,
      ),
    );
    renderWithProviders(<ScenarioCenter />);

    fireEvent.click((await screen.findAllByText('一键承接'))[0]);

    expect(await screen.findByText(/承接场景 · 医疗影像科研协作场景/)).toBeInTheDocument();
    expect(screen.getByText(/合规约束 1 项、交付预埋 1 项/)).toBeInTheDocument();
    expect(screen.getByDisplayValue('医疗影像科研协作场景')).toBeInTheDocument();
    expect(screen.getByText('未加入团队（项目不绑定团队）')).toBeInTheDocument();
  });

  it('确认承接调用 accept 并带上默认语言与沙箱环境', async () => {
    post.mockImplementation((url: string) =>
      Promise.resolve(
        url.endsWith('/accept')
          ? { scenario: { ...PUBLISHED, status: 'accepted' }, project: { ...PROJECT, scenario_id: 'scn-1' } }
          : undefined,
      ),
    );
    renderWithProviders(<ScenarioCenter />);

    fireEvent.click((await screen.findAllByText('一键承接'))[0]);
    await screen.findByText(/承接场景 · 医疗影像科研协作场景/);
    fireEvent.click(screen.getByRole('button', { name: '确认承接并创建项目' }));

    await waitFor(() =>
      expect(post).toHaveBeenCalledWith('/flow/scenarios/scn-1/accept', {
        project_name: '医疗影像科研协作场景',
        preferred_language: 'python',
        environment_type: 'l2c',
        team_id: undefined,
      }),
    );
    await waitFor(() =>
      expect(messageSpy.success).toHaveBeenCalledWith('已承接，建模项目「肺结节联合建模」已创建'),
    );
  });

  it('详情抽屉展示合规约束、交付预埋与承接项目', async () => {
    renderWithProviders(<ScenarioCenter />);

    // antd 会在两字按钮里插入空格（DOM 里是「详 情」），所以文案用位类正则认。
    fireEvent.click((await screen.findAllByText(/详\s*情/))[0]);

    expect(await screen.findByText('场景详情')).toBeInTheDocument();
    expect(await screen.findByText('三家医院联合训练肺结节模型')).toBeInTheDocument();
    expect(screen.getByText('仅汇总指标')).toBeInTheDocument();
    expect(screen.getByText('tee_image=tee-cuda:12.4')).toBeInTheDocument();
    expect(screen.getByText('建议清单只登记意向，能否读取仍由合约白名单决定。')).toBeInTheDocument();
    expect(screen.getByText('该场景尚未被承接')).toBeInTheDocument();
    expect(get).toHaveBeenCalledWith('/flow/scenarios/scn-1');
  });

  it('我的承接页签走 mine 面，状态已承接但缺项目时如实报警', async () => {
    const orphan = { ...ACCEPTED, id: 'scn-3', title: '状态漂移场景', project: null };
    route([PUBLISHED, ACCEPTED], [ACCEPTED, orphan]);
    renderWithProviders(<ScenarioCenter />);

    fireEvent.click(await screen.findByRole('tab', { name: '我的承接' }));

    await waitFor(() =>
      expect(get).toHaveBeenCalledWith('/flow/scenarios/mine', { params: { page: 1, page_size: 20 } }),
    );
    expect(await screen.findByText('肺结节联合建模')).toBeInTheDocument();
    expect(screen.getByText(/状态已变更但缺少承接项目/)).toBeInTheDocument();
  });

  it('发布场景的 JSON 字段不合法时当场报错且不打接口', async () => {
    renderWithProviders(<ScenarioCenter />);

    fireEvent.click(await screen.findByText('发布场景'));
    await screen.findByText('交付技术预埋（JSON 对象）');

    fireEvent.change(screen.getByPlaceholderText('例如：医疗影像科研协作场景'), {
      target: { value: '新的联合建模场景' },
    });
    fireEvent.change(screen.getByPlaceholderText('{"output_fields": "仅汇总指标"}'), { target: { value: '[]' } });
    fireEvent.click(within(modalFooter()).getByRole('button', { name: /发\s*布/ }));

    await waitFor(() =>
      expect(messageSpy.error).toHaveBeenCalledWith(expect.stringContaining('合规约束需要是 JSON 对象')),
    );
    expect(post).not.toHaveBeenCalled();
  });

  it('发布场景把 JSON 文本解析成对象再提交', async () => {
    post.mockResolvedValue({ ...PUBLISHED, title: '新的联合建模场景' });
    renderWithProviders(<ScenarioCenter />);

    fireEvent.click(await screen.findByText('发布场景'));
    await screen.findByText('交付技术预埋（JSON 对象）');

    fireEvent.change(screen.getByPlaceholderText('例如：医疗影像科研协作场景'), {
      target: { value: '新的联合建模场景' },
    });
    fireEvent.change(screen.getByPlaceholderText('{"output_fields": "仅汇总指标"}'), {
      target: { value: '{"output_fields": "仅汇总指标"}' },
    });
    fireEvent.click(within(modalFooter()).getByRole('button', { name: /发\s*布/ }));

    await waitFor(() =>
      expect(post).toHaveBeenCalledWith('/flow/scenarios', {
        title: '新的联合建模场景',
        industry: undefined,
        business_background: undefined,
        target_description: undefined,
        delivery_scene: 'api',
        suggested_resource_ids: [],
        compliance_constraints: { output_fields: '仅汇总指标' },
        delivery_tech_config: {},
      }),
    );
    await waitFor(() =>
      expect(messageSpy.success).toHaveBeenCalledWith('场景「新的联合建模场景」已发布，等待开发团队承接'),
    );
  });
});
