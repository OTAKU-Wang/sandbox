// 4-1 操作日志屏的权限门与导出落盘用例（只 mock services/auditApi 这一层）。
// ① 锚定本条 / 存证验真与导出共用同一个 canAuditOps 判定：后端 app/api/audit.py 对
//    /anchor、/verify/{id}、/merkle-proof/{id}、/export 四个端点写的是同一组
//    require_roles(OPERATOR, REGULATOR, ADMIN)，与 ROLE_GROUPS.auditReaders 一字不差，
//    所以「能读=能锚定」在前后端都不成立，按钮必须一起置灰；
// ② 悬浮层承诺的导出窗口 == 真正发给后端的 days，且不再把内部路由打印给用户；
// ③ 锚定成功的 toast 不得把缺失字段拼成「Merkle Root: undefined」；
// ④ 锚定成功后失效的是被锚定那条的详情缓存，不是 ['audit-record', null]
//    （弹窗在请求回来前被关掉时，onSuccess 闭包里的 detailId 已是 null）；
// ⑤ 落盘中途抛错也要 revokeObjectURL，否则 Blob URL 泄漏。
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { fireEvent, screen, within } from '@testing-library/react';
import { QueryClient } from '@tanstack/react-query';
import { message } from 'antd';
import { renderWithProviders } from '../../../test/test-utils';
import AuditLog from '../AuditLog';
import { auditApi, type AnchorResult, type AuditRecord, type AuditRecordDetail } from '../../../services/auditApi';
import { useAuthStore } from '../../../stores/authStore';
import { UserRole } from '../../../types/enums';

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

vi.mock('../../../services/auditApi', () => ({
  auditApi: {
    list: vi.fn(),
    get: vi.fn(),
    anchor: vi.fn(),
    verify: vi.fn(),
    getMerkleProof: vi.fn(),
    export: vi.fn(),
  },
}));

const RECORD_ID = '9b75d339-0000-4000-8000-000000000001';

function makeRecord(overrides: Partial<AuditRecord> = {}): AuditRecord {
  return {
    id: RECORD_ID,
    user_id: 'user-8899aabb',
    username: '李娜',
    role: 'operator',
    display_no: 'LOG-20261017-020000-9b75',
    session_id: null,
    action: 'delivery.dataset.downloaded',
    resource_type: 'delivery_bundle',
    resource_id: 'bundle-2026-1017',
    detail: { bundle: 'bundle-2026-1017' },
    ip_address: '100.112.3.84',
    blockchain_tx_hash: null,
    created_at: '2026-10-17T02:00:00Z',
    outcome: 'success',
    event_type: 'export',
    ...overrides,
  };
}

function makeDetail(overrides: Partial<AuditRecordDetail> = {}): AuditRecordDetail {
  return {
    ...makeRecord({ blockchain_tx_hash: null }),
    related_contract: null,
    ...overrides,
  };
}

function makeAnchor(overrides: Partial<AnchorResult> = {}): AnchorResult {
  return {
    anchored: 1,
    merkle_root: 'a1b2c3d4e5f60718293a4b5c6d7e8f90',
    tx_hash: '0x9f3a',
    success: true,
    error: null,
    block_number: null,
    ...overrides,
  };
}

function setRole(role: UserRole) {
  useAuthStore.setState({
    user: { id: 'u-1', username: 'auditor', email: 'a@t.cn', role, organization: null },
  });
}

function makeClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 60_000 }, mutations: { retry: false } },
  });
}

const createObjectURL = vi.fn(() => 'blob:audit-export');
const revokeObjectURL = vi.fn();

beforeEach(() => {
  vi.clearAllMocks();
  Object.defineProperty(URL, 'createObjectURL', { configurable: true, writable: true, value: createObjectURL });
  Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, writable: true, value: revokeObjectURL });
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
  setRole(UserRole.OPERATOR);
  vi.mocked(auditApi.list).mockResolvedValue({ items: [makeRecord()], total: 1, page: 1, page_size: 10 });
  vi.mocked(auditApi.get).mockResolvedValue(makeDetail());
  vi.mocked(auditApi.export).mockResolvedValue({
    blob: new Blob(['id,action\n'], { type: 'text/csv' }),
    filename: 'audit_export_30d.csv',
    contentType: 'text/csv',
  });
  vi.mocked(auditApi.anchor).mockResolvedValue(makeAnchor());
});

afterEach(() => {
  vi.restoreAllMocks();
});

type SuccessFn = typeof message.success;

/** antd 静态 message 在 jsdom 里弹的是 portal，接不了 spy 就看不到文案：直接拦 success 的入参。 */
function spyOnSuccess() {
  return vi.spyOn(message, 'success').mockImplementation((() => '') as unknown as SuccessFn);
}

async function renderPage(queryClient = makeClient()) {
  const utils = renderWithProviders(<AuditLog />, { queryClient });
  await screen.findByText('操作记录');
  // 等到列表行真的渲染出来：list 被调用 != 行已就位
  await screen.findByText('详情');
  return { ...utils, queryClient };
}

/** antd Tooltip 的内容挂在 body 的浮层里，按 .ant-tooltip-inner 取才不会撞上页面上的「导出」字样。 */
async function tooltipText() {
  return vi.waitFor(() => {
    const node = document.querySelector('.ant-tooltip-inner');
    if (!node) throw new Error('悬浮层还没渲染');
    return (node.textContent ?? '') as string;
  });
}

/** 打开详情弹窗并等到详情 query 落位（footer 先于 detail 渲染，只等 footer 会拿到空 footer）。 */
async function openDetail() {
  fireEvent.click(screen.getByText('详情'));
  return vi.waitFor(() =>
    expect(document.querySelector('.ant-modal-footer')?.textContent ?? '').toContain('锚定本条'),
  );
}

function footerButton(name: RegExp) {
  return within(document.querySelector('.ant-modal-footer') as HTMLElement).getByRole('button', { name });
}

describe('AuditLog 权限门 / 导出窗口 / 落盘', { timeout: 20_000 }, () => {
  it('后端 require_roles 之外的角色：锚定本条与存证验真同样置灰，点击不发请求', async () => {
    setRole(UserRole.BUYER);
    await renderPage();
    await openDetail();

    const anchorButton = footerButton(/锚定本条/);
    const verifyButton = footerButton(/存证验真/);
    expect(anchorButton).toBeDisabled();
    expect(verifyButton).toBeDisabled();

    fireEvent.click(anchorButton);
    expect(auditApi.anchor).not.toHaveBeenCalled();
  });

  it('auditReaders 内的角色三个动作都可用（同一判定，不额外发明权限）', async () => {
    setRole(UserRole.REGULATOR);
    await renderPage();
    await openDetail();

    expect(footerButton(/锚定本条/)).toBeEnabled();
    expect(footerButton(/存证验真/)).toBeEnabled();

    fireEvent.click(footerButton(/锚定本条/));
    await vi.waitFor(() => expect(auditApi.anchor).toHaveBeenCalledWith([RECORD_ID]));
  });

  it('无权角色的悬浮层也不得把后端内部符号名打印给用户', async () => {
    setRole(UserRole.BUYER);
    await renderPage();

    fireEvent.mouseEnter(screen.getByRole('button', { name: /导出日志/ }).parentElement as Element);
    const tipText = await tooltipText();

    expect(tipText).not.toMatch(/require_roles|ROLE_GROUPS|\/audit\/|GET /);
    expect(tipText).toContain('角色');
  });

  it('悬浮层写的天数就是发给后端的 days，且不再向用户打印内部路由', async () => {
    await renderPage();

    const exportButton = screen.getByRole('button', { name: /导出日志/ });
    fireEvent.mouseEnter(exportButton.parentElement as Element);
    const tipText = await tooltipText();

    // 内部实现路径（GET /audit/export?days=…）不属于给用户看的文案
    expect(tipText).not.toMatch(/\/audit\/|GET |format=/);
    expect(tipText).not.toContain('按所选时间窗口');
    const advertised = /(\d+)\s*天/.exec(tipText)?.[1];
    expect(advertised).toBeTruthy();

    fireEvent.click(exportButton);
    await vi.waitFor(() =>
      expect(auditApi.export).toHaveBeenCalledWith({ days: Number(advertised), format: 'csv' }),
    );
  });

  it('锚定响应没带 Merkle root 时，toast 不拼 undefined / 空根', async () => {
    // 后端 get_root() 在空树上返回 ""，网关吃掉字段时更是直接缺键：两种都得当「没有根」
    vi.mocked(auditApi.anchor).mockResolvedValue(
      makeAnchor({ merkle_root: '' }) as unknown as AnchorResult,
    );
    const successSpy = spyOnSuccess();
    await renderPage();
    await openDetail();

    fireEvent.click(footerButton(/锚定本条/));

    await vi.waitFor(() => expect(successSpy).toHaveBeenCalled());
    const toast = String(successSpy.mock.calls[0]?.[0] ?? '');
    expect(toast).toContain('已锚定 1 条记录');
    expect(toast).not.toContain('undefined');
    expect(toast).not.toMatch(/Merkle\s*Root\s*[:：]?\s*(\.\.\.|…)/);
  });

  it('锚定响应整个字段缺失时 toast 不写 undefined', async () => {
    vi.mocked(auditApi.anchor).mockResolvedValue(
      { anchored: 1, tx_hash: null, success: true, error: null } as unknown as AnchorResult,
    );
    const successSpy = spyOnSuccess();
    await renderPage();
    await openDetail();

    fireEvent.click(footerButton(/锚定本条/));

    await vi.waitFor(() => expect(successSpy).toHaveBeenCalled());
    const toast = String(successSpy.mock.calls[0]?.[0] ?? '');
    expect(toast).toContain('已锚定 1 条记录');
    expect(toast).not.toContain('undefined');
    expect(toast).not.toContain('null');
  });

  it('锚定响应带根时照旧展示截断根值', async () => {
    const successSpy = spyOnSuccess();
    await renderPage();
    await openDetail();

    fireEvent.click(footerButton(/锚定本条/));

    await vi.waitFor(() => expect(successSpy).toHaveBeenCalled());
    const toast = String(successSpy.mock.calls[0]?.[0] ?? '');
    expect(toast).toContain('a1b2c3d4e5f60718');
    expect(toast).not.toContain('undefined');
  });

  it('请求回来的时候弹窗已被关掉：失效的仍是那条记录的详情缓存', async () => {
    let settle: ((value: AnchorResult) => void) | undefined;
    vi.mocked(auditApi.anchor).mockReturnValue(
      new Promise<AnchorResult>((resolve) => { settle = resolve; }),
    );
    spyOnSuccess();
    const { queryClient } = await renderPage();
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
    await openDetail();

    fireEvent.click(footerButton(/锚定本条/));
    fireEvent.click(footerButton(/关\s*闭/));
    settle?.(makeAnchor());

    await vi.waitFor(() => expect(invalidate).toHaveBeenCalled());
    const keys = invalidate.mock.calls.map((call) => (call[0] as { queryKey?: unknown })?.queryKey);
    expect(keys).toContainEqual(['audit-record', RECORD_ID]);
    expect(keys.some((key) => Array.isArray(key) && key.includes(null))).toBe(false);
  });

  it('落盘点击抛错也要 revokeObjectURL（否则 Blob URL 泄漏）', async () => {
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {
      throw new Error('jsdom: a[download] not supported');
    });
    await renderPage();

    fireEvent.click(screen.getByRole('button', { name: /导出日志/ }));

    await vi.waitFor(() => expect(createObjectURL).toHaveBeenCalledTimes(1));
    await vi.waitFor(() => expect(revokeObjectURL).toHaveBeenCalledWith('blob:audit-export'));
  });
});
