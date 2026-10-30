// 成果包预览必须带预览层水印（2026-10-30 拍板）：有内容可看时叠水印层 + 明写
// 「文件本体未修改，预览层水印」；没有可预览内容时不许凭空造一层水印。
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { screen } from '@testing-library/react';
import { renderWithProviders } from '../../../test/test-utils';
import api from '../../../services/api';
import { useAuthStore } from '../../../stores/authStore';
import type { User } from '../../../types/models';
import BundlePreviewModal from '../BundlePreviewModal';

vi.mock('../../../services/api', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));

// antd Descriptions column={2} / 响应式栅格依赖 window.matchMedia（jsdom 未实现）。
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

const get = api.get as ReturnType<typeof vi.fn>;
const buyer: User = { id: 'u9', username: 'alice', email: 'a@example.com', role: 'buyer', organization: null };

beforeEach(() => {
  get.mockReset();
  useAuthStore.setState({ user: buyer });
});

describe('BundlePreviewModal 预览层水印', () => {
  it('表格预览叠水印层：当前用户 + 包号 + 「文件本体未修改」文案', async () => {
    get.mockImplementation((url: string) =>
      url.includes('/preview')
        ? Promise.resolve({
            bundle_id: 'B-77',
            available: true,
            member: 'result.csv',
            data: 'id,score\n1,0.5\n',
            rows_returned: 1,
            row_count_total: 1,
            truncated: false,
            output_review: { preview_findings: 0 },
          })
        : Promise.resolve({ items: [], total: 0, page: 1, page_size: 20 }),
    );
    renderWithProviders(<BundlePreviewModal bundleId="B-77" onClose={() => undefined} />);
    expect(await screen.findByTestId('preview-watermark-layer')).toBeInTheDocument();
    const note = screen.getByTestId('preview-watermark-note');
    expect(note.textContent).toContain('文件本体未修改，预览层水印');
    expect(note.textContent).toContain('B-77');
    expect(note.textContent).toContain('user:alice');
    expect(screen.getByText('0.5')).toBeInTheDocument(); // 水印半透明，内容仍可读
  });

  it('无可预览内容不叠水印层——没有内容可护，也不借水印刷存在感', async () => {
    get.mockImplementation((url: string) =>
      url.includes('/preview')
        ? Promise.resolve({ bundle_id: 'B-78', available: false, reason: '只能整包下载', member: null, members: ['readme.txt'] })
        : Promise.resolve({ items: [], total: 0, page: 1, page_size: 20 }),
    );
    renderWithProviders(<BundlePreviewModal bundleId="B-78" onClose={() => undefined} />);
    expect(await screen.findByText('只能整包下载')).toBeInTheDocument();
    expect(screen.queryByTestId('preview-watermark-layer')).not.toBeInTheDocument();
  });
});
