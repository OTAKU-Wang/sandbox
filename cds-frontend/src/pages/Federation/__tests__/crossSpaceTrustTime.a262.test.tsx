/**
 * A262（渲染层半边）：跨空间互信表的创建时间列不得把缺失的 created_at 渲染成 1970。
 * 台账 ocr-verified-findings.md §A262 + fe-07a 已落的服务层口径：`created_at` 缺失归一为
 * null，展示必须走 `formatTrustTimestamp`（缺→「未记录」，解不了原样透出）。
 * 旧渲染 `new Date(v).toLocaleString()` 对 null 得到 1970-01-01、对坏串得到 Invalid Date，
 * 两者都是「看起来像时间的假值」——互信记录上的时间是证据，编不了也不能编成纪元零点。
 * 只 mock 最外层 HTTP（services/api），federationApi 归一化与页面逻辑跑真代码。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen } from '@testing-library/react';
import { renderWithProviders } from '../../../test/test-utils';
import CrossSpaceDashboard from '../CrossSpaceDashboard';
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

const get = api.get as unknown as ReturnType<typeof vi.fn>;

const VALID_TS = '2026-10-01T00:00:00';
const GARBAGE = 'not-a-timestamp';

beforeEach(() => {
  get.mockReset();
  get.mockImplementation(async (url: string) => {
    if (url === '/federation/trusts') {
      return [
        {
          trust_id: 't-missing',
          remote_space: 'sp1',
          remote_space_name: 'Space Missing',
          trust_level: 'verified',
          status: 'active',
          allowed_operations: ['read_catalog'],
          // created_at 缺失：契约漂移时后端的真实形态
        },
        {
          trust_id: 't-valid',
          remote_space: 'sp2',
          remote_space_name: 'Space Valid',
          trust_level: 'basic',
          status: 'active',
          allowed_operations: [],
          created_at: VALID_TS,
        },
        {
          trust_id: 't-garbage',
          remote_space: 'sp3',
          remote_space_name: 'Space Garbage',
          trust_level: 'none',
          status: 'suspended',
          allowed_operations: [],
          created_at: GARBAGE,
        },
      ];
    }
    return [];
  });
});

describe('A262 互信表创建时间列：缺了报缺，不编 1970', () => {
  it('缺 created_at 的行渲染「未记录」，全页不出现 1970/Invalid Date', async () => {
    renderWithProviders(<CrossSpaceDashboard />);
    await screen.findByText('Space Missing');
    expect(await screen.findByText('未记录')).toBeTruthy();
    expect(document.body.textContent).not.toMatch(/1970/);
    expect(document.body.textContent).not.toMatch(/Invalid Date/);
  });

  it('合法 created_at 仍按本地格式渲染（修复没把正常数据也吞掉）', async () => {
    renderWithProviders(<CrossSpaceDashboard />);
    const row = await screen.findByText('Space Valid');
    const expected = new Date(VALID_TS).toLocaleString();
    expect(row.closest('tr')?.textContent).toContain(expected);
  });

  it('解析不了的 created_at 原样透出（不是 Invalid Date，也不是兜底值）', async () => {
    renderWithProviders(<CrossSpaceDashboard />);
    const row = await screen.findByText('Space Garbage');
    expect(row.closest('tr')?.textContent).toContain(GARBAGE);
    expect(row.closest('tr')?.textContent).not.toMatch(/Invalid Date/);
  });
});
