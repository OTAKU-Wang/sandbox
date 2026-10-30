// 拍板②（2026-10-30）：创建合约页的「允许沙箱级别」按**部署真实能力**披露渲染。
//
// 旧形态是一个 disabled 的固定 l2c（tooltip 还写着「SS-13 起只有 MicroVM 单一路径」），
// 而 dev compose 把 CDS_DEV_SANDBOX_LEVEL 设成 L3（docker-compose.yml:213）——本机一开
// 调试环，用这张表单建的合约建会话必 400（取证 specs/ss-19-full-platform-docker-e2e.md §10.1-4）。
// 现在选项、默认档、披露文案全部来自只读端点 GET /meta/sandbox-levels。
//
// 下面三份披露载荷不是手写的漂亮话，是本机把 CDS_CUBE_DATAPLANE_ENABLED=false /
// CDS_SANDBOX_LEGACY_ENABLED=true / CDS_DEV_SANDBOX_LEVEL=L3 打开后，从
// capability_matrix.sandbox_levels_payload() 真实打出来的载荷（披露文案逐字保留）。
// 铁律：披露能力 ≠ 降级隔离——表单允许勾选 l2c 不等于后端会放行，执行侧照旧 400，
// 这条由 tests/test_deployment_sandbox_levels.py 的「同请求对账」用例锁死。
//
// 只 mock 最外层 HTTP（services/api）与 antd 静态 message；services/metaApi 跑真代码。
import { existsSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';
import { renderWithProviders } from '../../../test/test-utils';
import ContractCreate from '../ContractCreate';
import api from '../../../services/api';
import type { SandboxLevelsDisclosure } from '../../../services/metaApi';

/** 本测试池的 import.meta.url 不是 file: scheme，只能从 cwd 往上找源文件（同 DataplaneBanner.a297）。 */
function readSourceFromDisk(rel: string): string {
  let dir = process.cwd();
  for (let depth = 0; depth < 5; depth += 1) {
    for (const base of [dir, join(dir, 'cds-frontend')]) {
      const candidate = join(base, rel);
      if (existsSync(candidate)) return readFileSync(candidate, 'utf8');
    }
    const parent = dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  throw new Error(`找不到源文件 ${rel}（cwd=${process.cwd()}）`);
}

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
vi.mock('antd', async (importOriginal) => {
  const actual = await importOriginal<typeof import('antd')>();
  return {
    ...actual,
    message: { ...actual.message, error: vi.fn(), success: vi.fn(), warning: vi.fn(), info: vi.fn() } as typeof actual.message,
  };
});

const get = api.get as unknown as ReturnType<typeof vi.fn>;
const post = api.post as unknown as ReturnType<typeof vi.fn>;

const PRODUCT = {
  id: 'p-1',
  name: '授权零售表',
  product_type: 'tabular',
  status: 'published',
  provider_id: 'u-provider',
  resource_id: 'r-1',
  row_count: 10,
  security_level: 'L2',
  current_version: 1,
  created_at: '2026-10-01T00:00:00',
};

/** 本机 dev compose 的真实披露（数据面关 + 历史档位观察期开 + DEV_SANDBOX_LEVEL=L3）。 */
const DEV_LOCAL_DISCLOSURE: SandboxLevelsDisclosure = {
  levels: [
    {
      level: 'L2',
      label: 'L2',
      positioning: '软件增强(弃用观察)',
      description: "firecracker 路径；SS-10 后弃用（CDS_LEGACY_L2_ENABLED 观察期），由 L2' 承接。",
      deprecated: true,
      registered: true,
      runtime_available: true,
      reason: 'legacy_observation_window',
      disclosure: '开发环境 / 观察期档位：本部署开启了历史档位观察期开关（CDS_SANDBOX_LEGACY_ENABLED 或 CDS_LEGACY_L2_ENABLED，生产禁止开启）',
      supply_data_eligible: false,
      contract_selectable: false,
      contract_reason: 'below_supply_data_floor',
      contract_note: '不可写入合约：低于平台供数下限（可承载契约供数的档位只有 l2c/L3），装载真实供数的会话在该档位会被 400 拒绝',
    },
    {
      level: 'l2c',
      label: "L2'",
      positioning: '生产默认(软件就绪，需节点)',
      description: "Cube MicroVM 数据面 L2'（SS-13 单一路径）：无 ready 节点时 fail-closed 400 RUNTIME_UNAVAILABLE。",
      deprecated: false,
      registered: false,
      runtime_available: false,
      reason: 'dataplane_disabled',
      disclosure: '需 Cube 计算节点：本部署按控制面档交付（CDS_CUBE_DATAPLANE_ENABLED=false），创建会话会返回 400 RUNTIME_UNAVAILABLE（SS-13 单一路径，不降级到进程/bwrap 隔离）',
      supply_data_eligible: true,
      contract_selectable: true,
      contract_reason: null,
      contract_note: null,
    },
    {
      level: 'L3',
      label: 'L3',
      positioning: '进程隔离',
      description: 'bwrap 命名空间隔离。',
      deprecated: false,
      registered: true,
      runtime_available: true,
      reason: 'legacy_observation_window',
      disclosure: '开发环境 / 观察期档位：本部署开启了历史档位观察期开关（CDS_SANDBOX_LEGACY_ENABLED 或 CDS_LEGACY_L2_ENABLED，生产禁止开启）',
      supply_data_eligible: true,
      contract_selectable: true,
      contract_reason: null,
      contract_note: null,
    },
  ],
  recommended_level: 'L3',
  recommended_disclosure: '开发环境 / 观察期档位：本部署开启了历史档位观察期开关（CDS_SANDBOX_LEGACY_ENABLED 或 CDS_LEGACY_L2_ENABLED，生产禁止开启）',
  supply_data_levels: ['l2c', 'L3'],
  dev_sandbox_level: {
    level: 'L3',
    known: true,
    runtime_available: true,
    disclosure: '开发环境 / 观察期档位：本部署开启了历史档位观察期开关（CDS_SANDBOX_LEGACY_ENABLED 或 CDS_LEGACY_L2_ENABLED，生产禁止开启）',
  },
  ready_nodes: 0,
  dataplane: {
    enabled: false,
    mode: 'control_plane_only',
    reason: 'dataplane_disabled',
    note: '未启用 MicroVM 数据面（需 /dev/kvm 或已透传 vmx/svm 的宿主 + Cube 计算节点）：平台按控制面档交付，目录/契约/审批/审计/交付可用，沙箱执行入口不可用',
  },
  note: '口径：只披露当前部署的真实能力，不改变任何执行判定；不可满足的请求照旧按 SS-13 fail-closed 400 拒绝，披露能力 ≠ 降级隔离',
};

const LEVELS_URL = '/meta/sandbox-levels';

function mockBasics(levelHandler: () => Promise<unknown>) {
  get.mockImplementation((url: string) => {
    if (url === '/data-products') {
      return Promise.resolve({ items: [PRODUCT], total: 1, page: 1, page_size: 100 });
    }
    if (url === '/users/options') {
      return Promise.resolve([{ id: 'u-buyer', username: 'buyer1', role: 'buyer', organization: null }]);
    }
    if (url === LEVELS_URL) return levelHandler();
    return Promise.resolve(undefined);
  });
  post.mockImplementation((url: string) => {
    if (url === '/contracts') return Promise.resolve({ id: 'ct-new', contract_no: 'CT-NEW' });
    return Promise.resolve(undefined);
  });
}

function findButton(label: string): HTMLButtonElement {
  const button = Array.from(document.querySelectorAll('button'))
    .find((node) => node.textContent?.replace(/\s/g, '') === label);
  if (!button) throw new Error(`按钮没渲染: ${label}`);
  return button as HTMLButtonElement;
}

/** 「允许沙箱级别」那一项的 Select 根节点（选项到齐后 placeholder 会消失，只能按 label 定位）。 */
function levelSelect(): HTMLElement {
  const item = Array.from(document.querySelectorAll('.ant-form-item'))
    .find((node) => node.textContent?.includes('允许沙箱级别'));
  const root = item?.querySelector('.ant-select');
  if (!root) throw new Error('没有渲染「允许沙箱级别」选择器');
  return root as HTMLElement;
}

/** 披露落地才有后端那句 note；拿它当「数据到齐」的信号，不去猜渲染时序。 */
async function disclosureLanded() {
  await screen.findByText(/只披露当前部署的真实能力/);
}

async function openLevelOptions() {
  await disclosureLanded();
  const selector = levelSelect().querySelector('.ant-select-selector') as HTMLElement;
  fireEvent.mouseDown(selector);
  await waitFor(() => expect(document.querySelector('.ant-select-item-option')).not.toBeNull());
}

/** 选项主行文本（披露走 optionRender 的第二行，混在 content 里会串味，按类名各取各的）。 */
function optionTexts(): string[] {
  return Array.from(document.querySelectorAll('.cds-level-option-label')).map((node) => node.textContent ?? '');
}

function optionNotes(): string[] {
  return Array.from(document.querySelectorAll('.cds-level-option-note')).map((node) => node.textContent ?? '');
}

async function fillBasicsAndSubmit() {
  fireEvent.change(screen.getByPlaceholderText('合约标题'), { target: { value: '零售合作合约' } });
  const productSelector = screen.getByText('选择数据产品').closest('.ant-select')!.querySelector('.ant-select-selector')!;
  fireEvent.mouseDown(productSelector);
  fireEvent.click(await screen.findByText('授权零售表', { selector: '.ant-select-item-option-content' }));
  const buyerSelector = screen.getByText('选择买方').closest('.ant-select')!.querySelector('.ant-select-selector')!;
  fireEvent.mouseDown(buyerSelector);
  fireEvent.click(await screen.findByText('buyer1', { selector: '.ant-select-item-option-content' }));
  fireEvent.click(findButton('创建合约'));
  await waitFor(() => expect(post).toHaveBeenCalledWith('/contracts', expect.anything()));
}

function submittedBody(): Record<string, unknown> {
  const call = post.mock.calls.find((c) => c[0] === '/contracts');
  if (!call) throw new Error('没有发出 POST /contracts');
  return call[1] as Record<string, unknown>;
}

describe('创建合约 — 允许沙箱级别按部署真实能力披露（拍板②）', { timeout: 20_000 }, () => {
  beforeEach(() => {
    get.mockReset();
    post.mockReset();
  });

  it('选项与披露来自 /meta/sandbox-levels：可写档全列出，不可执行的照实标注，退役档不进选项', async () => {
    mockBasics(() => Promise.resolve(DEV_LOCAL_DISCLOSURE));
    renderWithProviders(<ContractCreate />);
    await openLevelOptions();
    const options = optionTexts();
    // 可写档两条都在（l2c 即便跑不起来也仍是合法条款，不能从选项里抹掉）
    expect(options).toContain("L2' · 生产默认(软件就绪，需节点)（当前部署不可执行）");
    expect(options).toContain('L3 · 进程隔离');
    // 低于供数下限的档位后端标了 contract_selectable=false ⇒ 不进取项，并附原因
    expect(options.some((text) => text.startsWith('L2 · '))).toBe(false);
    // 披露文案逐字来自后端（选项第二行），不是前端另写一套说法
    const notes = optionNotes();
    const l2cRow = DEV_LOCAL_DISCLOSURE.levels.find((row) => row.level === 'l2c')!;
    expect(notes).toContain(l2cRow.disclosure);
    expect(notes).toContain(
      DEV_LOCAL_DISCLOSURE.levels.find((row) => row.level === 'L3')!.disclosure,
    );
    // 字段下方的口径行照抄后端 note
    expect(document.body.textContent).toContain(DEV_LOCAL_DISCLOSURE.note);
    // 退役/不可用的说法不许被前端美化成可用
    expect(document.body.textContent).not.toContain('全部档位可用');
  });

  it('默认档取披露的推荐档：dev 本机形态下提交 L3（旧实现恒提交 l2c，本机建会话必 400）', async () => {
    mockBasics(() => Promise.resolve(DEV_LOCAL_DISCLOSURE));
    renderWithProviders(<ContractCreate />);
    await fillBasicsAndSubmit();

    const body = submittedBody();
    expect(body.allowed_sandbox_levels).toBe('L3');
    // 推荐档为什么是它，界面上写的是后端那句话
    expect(document.body.textContent).toContain(DEV_LOCAL_DISCLOSURE.recommended_disclosure);
  });

  it('用户手动选档位之后不被推荐档覆盖，且多选按逗号串上行', async () => {
    mockBasics(() => Promise.resolve(DEV_LOCAL_DISCLOSURE));
    renderWithProviders(<ContractCreate />);
    await openLevelOptions();
    const l2cOption = Array.from(document.querySelectorAll('.ant-select-item-option-content'))
      .find((node) => (node.textContent ?? '').startsWith("L2'"));
    if (!l2cOption) throw new Error('选项里没有 l2c');
    fireEvent.click(l2cOption);

    await fillBasicsAndSubmit();
    // 推荐档 L3 是初值，用户又勾了 l2c ⇒ 两条都授权；顺序按表单选中顺序
    expect(String(submittedBody().allowed_sandbox_levels).split(',').sort()).toEqual(['L3', 'l2c']);
  });

  it('读不到披露时显示「未记录」，选择器不可选，且这个字段整个不上行（不替用户猜档位）', async () => {
    mockBasics(() => Promise.reject(new Error('披露端点不可达')));
    renderWithProviders(<ContractCreate />);
    await fillBasicsAndSubmit();

    expect(document.body.textContent).toContain('未记录：未能读取本部署的沙箱档位能力披露');
    expect(levelSelect().classList.contains('ant-select-disabled')).toBe(true);
    const body = submittedBody();
    expect(body.allowed_sandbox_levels).toBeUndefined();
    // 缺数不许被折成一个具体档位，也不许出现「默认为 l2c」这类前端自造说法
    expect(document.body.textContent).not.toMatch(/默认(档位|值)?(是|为)? ?l2c/);
  });

  it('静态门：页面里不再有第二个沙箱档位真值源（旧硬编码不许回来）', () => {
    const page = readSourceFromDisk('src/pages/Contracts/ContractCreate.tsx');
    // 旧写法：initialValues 里塞死 [SandboxLevel.L2C]，并从 types/enums 引进档位枚举
    expect(page).not.toMatch(/allowed_sandbox_levels:\s*\[SandboxLevel/);
    expect(page).not.toMatch(/import\s+\{[^}]*\bSandboxLevel\b[^}]*\}\s+from\s+'[^']*enums'/);
    expect(page).toContain('metaApi.getSandboxLevels');
    const service = readSourceFromDisk('src/services/metaApi.ts');
    expect(service).toContain("api.get('/meta/sandbox-levels')");
  });
});
