// A309 的用户可见那一半：脱敏预览弹窗必须把后端算出的 masked_columns 原样披露出来。
// 没掩任何列就得显示“无”（旧行为下后端永远报 true，弹窗无从分辨真假）；
// 掩了哪几列就逐列点名。只钉“披露了什么结构化内容”，不钉提示文案。
import { describe, it, expect, vi, afterEach } from 'vitest';
import { screen } from '@testing-library/react';
import { renderWithProviders } from '../../../test/test-utils';
import DesensitizedPreviewModal from '../DesensitizedPreviewModal';
import { dataResourceApi, type DesensitizeResult } from '../../../services/dataResourceApi';

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

vi.mock('../../../services/dataResourceApi', () => ({
  dataResourceApi: { desensitize: vi.fn(), get: vi.fn(), list: vi.fn() },
}));

function payload(over: Partial<DesensitizeResult>): DesensitizeResult {
  return {
    resource_id: 'r-1',
    format: 'csv',
    data: 'order_id,amount\nA1,10\n',
    row_count: 1,
    masking_applied: false,
    masked_columns: [],
    ...over,
  };
}

async function renderModal() {
  // 弹窗没有 open -prop：父级按 resourceId 条件挂载（见 ResourceList），这里同构
  renderWithProviders(<DesensitizedPreviewModal resourceId="r-1" resourceName="订单" onClose={() => {}} />);
  // 标题是「脱敏预览 订单」一个文本节点，用正则匹配才算“弹窗真的渲染完了”
  await screen.findByText(/脱敏预览/);
}

afterEach(() => {
  vi.clearAllMocks();
});

describe('脱敏预览的掩码列披露（A309）', () => {
  it('后端说什么都没掩 ⇒ 披露位上是“无”，不许凭空列出不存在的掩码列', async () => {
    vi.mocked(dataResourceApi.desensitize).mockResolvedValue(payload({}));

    await renderModal();

    await vi.waitFor(() => expect(document.querySelector('.ant-alert')).toBeTruthy());
    expect(screen.getByText('无')).toBeTruthy();
  });

  it('后端说掩了 phone ⇒ 逐列点名，不再报“无”', async () => {
    vi.mocked(dataResourceApi.desensitize).mockResolvedValue(payload({
      data: 'order_id,phone\nA1,138****8000\n',
      row_count: 1,
      masking_applied: true,
      masked_columns: ['phone'],
    }));

    await renderModal();

    await vi.waitFor(() => expect(document.querySelector('.ant-alert')).toBeTruthy());
    // phone 只可能来自 masked_columns 或样例表头；两处都没有就是根本没披露
    expect(screen.getAllByText('phone').length).toBeGreaterThanOrEqual(1);
    expect(screen.queryByText('无')).toBeNull();
  });

  it('后端只给了 bool、没给列名（旧形状）⇒ 不编造列名', async () => {
    vi.mocked(dataResourceApi.desensitize).mockResolvedValue({
      resource_id: 'r-1',
      format: 'csv',
      data: 'email\ns***e@example.com\n',
      row_count: 1,
      masking_applied: true,
    });

    await renderModal();

    await vi.waitFor(() => expect(document.querySelector('.ant-alert')).toBeTruthy());
    expect(screen.getByText('无')).toBeTruthy();
  });
});
