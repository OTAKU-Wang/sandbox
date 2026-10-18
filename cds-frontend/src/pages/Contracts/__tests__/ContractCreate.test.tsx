// 创建合约页本地契约（A314/A315，全部离线跑）：
// ① A314：后端 ContractCreate 支持 purpose/purpose_scope（app/schemas/contract.py:26-28），
//    Gap A4 目的门（app/api/sandbox_tasks.py）只在合约带 purpose 时生效 —— UI 不出口这两字段，
//    界面建的合约就全部游离在目的门外。表单必须能把目的与用途白名单送进创建请求。
// ② A315：清空「允许操作」后提交必须被表单拦下。后端把空 allowed_operations 当「不限制」
//    （sandbox_sessions.py _operation_allowed_by_contract:178 `if not ...: return True`，
//     另见 connectors.py:75 / contract_fulfillment.py:225 / gateway_service.py:164），
//    所以「清空」既不能不发（静默恢复默认 read,analyze = 隐性授权），也不能发空集（= 不限制，反而扩权）。
// 只 mock 最外层 HTTP 与 antd 的静态 message（jsdom 下 v5 static toast 不落 DOM）。
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';
import { renderWithProviders } from '../../../test/test-utils';
import ContractCreate from '../ContractCreate';
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
  description: null,
  product_type: 'tabular',
  industry: null,
  status: 'published',
  provider_id: 'u-provider',
  resource_id: 'r-1',
  row_count: 10,
  data_schema: null,
  security_level: 'L2',
  allowed_operations: null,
  policy_config: null,
  current_version: 1,
  quality_score: null,
  created_at: '2026-10-01T00:00:00',
  updated_at: null,
};

beforeEach(() => {
  get.mockReset();
  post.mockReset();
  get.mockImplementation((url: string) => {
    if (url === '/data-products') {
      return Promise.resolve({ items: [PRODUCT], total: 1, page: 1, page_size: 100 });
    }
    if (url === '/users/options') {
      return Promise.resolve([{ id: 'u-buyer', username: 'buyer1', role: 'buyer', organization: null }]);
    }
    return Promise.resolve(undefined);
  });
  post.mockImplementation((url: string) => {
    if (url === '/contracts') return Promise.resolve({ id: 'ct-new', contract_no: 'CT-NEW' });
    return Promise.resolve(undefined);
  });
});

/** antd 会在两字按钮里插全角空格，按去空白的 textContent 比对才稳。 */
function findButton(label: string): HTMLButtonElement {
  const button = Array.from(document.querySelectorAll('button'))
    .find((node) => node.textContent?.replace(/\s/g, '') === label);
  if (!button) throw new Error(`按钮没渲染: ${label}`);
  return button as HTMLButtonElement;
}

/** 打开某个 antd Select（用 placeholder 文本定位）并点掉指定选项。 */
async function pickOption(placeholder: string, optionText: string) {
  const placeholderNode = screen.getByText(placeholder);
  const selector = placeholderNode.closest('.ant-select')?.querySelector('.ant-select-selector');
  if (!selector) throw new Error(`Select 没找到: ${placeholder}`);
  fireEvent.mouseDown(selector);
  const option = await screen.findByText(optionText, { selector: '.ant-select-item-option-content' });
  fireEvent.click(option);
}

async function fillBasics() {
  fireEvent.change(screen.getByPlaceholderText('合约标题'), { target: { value: '零售合作合约' } });
  await pickOption('选择数据产品', '授权零售表');
  await pickOption('选择买方', 'buyer1');
}

/** 当前真正生效的表单错词（排掉 antd 离场动画残留的 -leave 节点）。 */
function visibleFormErrors(): string[] {
  return Array.from(document.querySelectorAll('.ant-form-item-explain-error'))
    .filter((node) => !node.closest('.ant-form-item-explain-leave'))
    .map((node) => node.textContent ?? '');
}

/** 把「允许操作」默认勾选的 读取/分析 两项挨个点掉。 */
function clearAllowedOperations() {
  const removes = document.querySelectorAll(
    '.ant-select-selection-item[title="读取"] .ant-select-selection-item-remove, '
    + '.ant-select-selection-item[title="分析"] .ant-select-selection-item-remove',
  );
  expect(removes.length).toBe(2);
  removes.forEach((node) => fireEvent.click(node));
}

function submittedBody(): Record<string, unknown> {
  const call = post.mock.calls.find((c) => c[0] === '/contracts');
  if (!call) throw new Error('没有发出 POST /contracts');
  return call[1] as Record<string, unknown>;
}

describe('创建合约 — 提交载荷（A314/A315）', () => {
  it('守卫：默认勾选原样上行（allowed_operations 是显式非空串），purpose 留空不带上', async () => {
    renderWithProviders(<ContractCreate />);
    await fillBasics();
    fireEvent.click(findButton('创建合约'));

    await waitFor(() => expect(post).toHaveBeenCalledWith('/contracts', expect.anything()));
    const body = submittedBody();
    expect(body.allowed_operations).toBe('read,analyze');
    expect(body.allowed_sandbox_modes).toEqual(['query']);
    expect(body.purpose).toBeUndefined();
    expect(body.purpose_scope).toBeUndefined();
  });

  it('A314：数据用途与用途白名单随创建请求上行（Gap A4 目的门对 UI 建的合约可达）', async () => {
    renderWithProviders(<ContractCreate />);
    await fillBasics();

    fireEvent.change(screen.getByPlaceholderText('如：零售客单价建模'), { target: { value: '零售客单价建模' } });
    // rc-select 的 tags 输入没有 placeholder 属性，只能从占位 div 回定位内部 input；
    // tokenSeparators 使带逗号的 change 事件直接成词（比模拟高亮+Enter 稳）。
    const scopeInput = screen.getByText('输入标签后回车或逗号分隔').closest('.ant-select')!.querySelector('input')!;
    fireEvent.change(scopeInput, { target: { value: '销量分析,' } });
    fireEvent.change(scopeInput, { target: { value: '画像匹配,' } });

    fireEvent.click(findButton('创建合约'));
    await waitFor(() => expect(post).toHaveBeenCalledWith('/contracts', expect.anything()));
    const body = submittedBody();
    expect(body.purpose).toBe('零售客单价建模');
    expect(body.purpose_scope).toEqual(['销量分析', '画像匹配']);
  });

  it('A315：清空「允许操作」后提交被表单拦下 —— 不发出 POST，也不静默恢复默认', async () => {
    renderWithProviders(<ContractCreate />);
    await fillBasics();
    clearAllowedOperations();

    fireEvent.click(findButton('创建合约'));
    // 让微任务队列跑完：如果提交没被拦截，api.post 会在这一拍里被调用
    await waitFor(() => expect(visibleFormErrors().length).toBeGreaterThan(0));
    expect(post).not.toHaveBeenCalled();
  });
});
