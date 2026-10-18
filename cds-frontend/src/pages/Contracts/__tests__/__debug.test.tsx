import { describe, it, vi } from 'vitest';
import { writeFileSync } from 'node:fs';
import { fireEvent, screen } from '@testing-library/react';
import { renderWithProviders } from '../../../test/test-utils';
import ContractCreate from '../ContractCreate';
import api from '../../../services/api';

Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: (query: string) => ({
    matches: false, media: query, onchange: null,
    addListener: vi.fn(), removeListener: vi.fn(),
    addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: vi.fn(),
  }),
});
vi.mock('../../../services/api', () => ({ default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() } }));
const get = api.get as unknown as ReturnType<typeof vi.fn>;
const post = api.post as unknown as ReturnType<typeof vi.fn>;

describe('debug', () => {
  it('drive form and dump state', async () => {
    get.mockImplementation((url: string) => {
      if (url === '/data-products') return Promise.resolve({ items: [{ id: 'p-1', name: '授权零售表', industry: null }], total: 1, page: 1, page_size: 100 });
      if (url === '/users/options') return Promise.resolve([{ id: 'u-buyer', username: 'buyer1', role: 'buyer', organization: null }]);
      return Promise.resolve(undefined);
    });
    post.mockResolvedValue({ id: 'ct-new' });
    renderWithProviders(<ContractCreate />);
    fireEvent.change(screen.getByPlaceholderText('合约标题'), { target: { value: '零售合作合约' } });
    await screen.findByText('合约类型');

    const probe: Record<string, unknown> = {};
    for (const [ph, opt] of [['选择数据产品', '授权零售表'], ['选择买方', 'buyer1']] as const) {
      const phEl = screen.getByText(ph);
      const sel = phEl.closest('.ant-select')?.querySelector('.ant-select-selector');
      if (sel) fireEvent.mouseDown(sel);
      await new Promise((r) => setTimeout(r, 50));
      const options = Array.from(document.querySelectorAll('.ant-select-item-option')).map((n) => n.textContent);
      const optEl = Array.from(document.querySelectorAll('.ant-select-item-option-content')).find((n) => n.textContent === opt);
      probe[ph] = { open: Boolean(sel), options, optionFound: Boolean(optEl) };
      if (optEl) fireEvent.click(optEl);
      await new Promise((r) => setTimeout(r, 50));
    }
    const items = Array.from(document.querySelectorAll('.ant-select-selection-item')).map((n) => `${n.getAttribute('title')}|${n.textContent}`);
    const btn = Array.from(document.querySelectorAll('button')).find((n) => n.textContent?.replace(/\s/g, '') === '创建合约');
    if (btn) fireEvent.click(btn);
    await new Promise((r) => setTimeout(r, 150));
    const errs = Array.from(document.querySelectorAll('.ant-form-item-explain-error')).map((n) => n.textContent);
    writeFileSync('C:/Users/清彧/AppData/Local/Temp/ocr-cds/_fe-debug.json', JSON.stringify({
      probe,
      items,
      submitFound: Boolean(btn),
      submitType: btn?.getAttribute('type'),
      errs,
      postCalls: post.mock.calls.map((c) => [c[0], c[1]]),
    }, null, 2), 'utf8');
  });
});
