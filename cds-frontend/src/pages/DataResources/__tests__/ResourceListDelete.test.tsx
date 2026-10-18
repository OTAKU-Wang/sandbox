// A312：删除按钮的反馈必须跟着响应体走。
// 后端 200 只代表记录行没了；storage_deleted / key_destroyed 是密文对象与 DEK 的
// 真实销毁结果（storage_service.delete 会回 False，KMS destroy_key 也会失败）。
// 旧实现无条件 message.success('删除成功')，等于对数商谎称数据已清干净。
// 这里只钉「用了哪一种反馈」，不钉文案。
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { fireEvent, screen } from '@testing-library/react';
import { message } from 'antd';
import { renderWithProviders } from '../../../test/test-utils';
import ResourceList from '../ResourceList';
import { dataResourceApi, type DataResource, type DeleteResourceResult } from '../../../services/dataResourceApi';

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
  dataResourceApi: {
    list: vi.fn(),
    get: vi.fn(),
    upload: vi.fn(),
    delete: vi.fn(),
    sample: vi.fn(),
    desensitize: vi.fn(),
  },
}));

const RESOURCE: DataResource = {
  id: 'r-1',
  provider_id: 'p-1',
  name: '订单明细',
  description: null,
  resource_type: 'file',
  format: 'csv',
  status: 'ready',
  schema_fields: null,
  row_count: 2,
  file_size_bytes: 1024,
  error_message: null,
  created_at: '2026-10-18T00:00:00Z',
  updated_at: '2026-10-18T00:00:00Z',
};

type MessageFn = typeof message.success;
const spy = (fn: 'success' | 'warning' | 'error') =>
  vi.spyOn(message, fn).mockImplementation((() => '') as unknown as MessageFn);

async function renderList() {
  const utils = renderWithProviders(<ResourceList />);
  // 等到列表行真的渲染出来：list 被调用 != 行已就位
  await screen.findByText('订单明细');
  return utils;
}

/** 行尾的红色删除按钮 → Popconfirm 主按钮。 */
async function confirmDelete() {
  const trigger = screen.getAllByRole('button').find((b) => b.className.includes('ant-btn-dangerous'));
  if (!trigger) throw new Error('没有找到删除按钮');
  fireEvent.click(trigger);
  const ok = await vi.waitFor(() => {
    const node = document.querySelector('.ant-popover .ant-btn-primary') as HTMLElement | null;
    if (!node) throw new Error('删除确认浮层还没渲染');
    return node;
  });
  fireEvent.click(ok);
}

beforeEach(() => {
  vi.mocked(dataResourceApi.list).mockResolvedValue({ items: [RESOURCE], total: 1, page: 1, page_size: 20 });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('删除反馈跟着响应体走（A312）', () => {
  it('密文对象没删掉：不得报成功', async () => {
    const success = spy('success');
    const warning = spy('warning');
    vi.mocked(dataResourceApi.delete).mockResolvedValue({
      deleted: true,
      resource_id: RESOURCE.id,
      storage_deleted: false,
      key_destroyed: true,
    } satisfies DeleteResourceResult);

    await renderList();
    await confirmDelete();

    await vi.waitFor(() => expect(warning).toHaveBeenCalled());
    expect(success).not.toHaveBeenCalled();
  });

  it('密钥没销毁：同样不得报成功', async () => {
    const success = spy('success');
    const warning = spy('warning');
    vi.mocked(dataResourceApi.delete).mockResolvedValue({
      deleted: true,
      resource_id: RESOURCE.id,
      storage_deleted: true,
      key_destroyed: false,
    } satisfies DeleteResourceResult);

    await renderList();
    await confirmDelete();

    await vi.waitFor(() => expect(warning).toHaveBeenCalled());
    expect(success).not.toHaveBeenCalled();
  });

  it('三样都真删掉了才报成功', async () => {
    const success = spy('success');
    const warning = spy('warning');
    vi.mocked(dataResourceApi.delete).mockResolvedValue({
      deleted: true,
      resource_id: RESOURCE.id,
      storage_deleted: true,
      key_destroyed: true,
    } satisfies DeleteResourceResult);

    await renderList();
    await confirmDelete();

    await vi.waitFor(() => expect(success).toHaveBeenCalled());
    expect(warning).not.toHaveBeenCalled();
  });
});
