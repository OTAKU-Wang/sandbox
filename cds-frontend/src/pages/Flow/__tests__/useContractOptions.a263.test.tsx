/**
 * A263：`useContractById` 的 enabled 只挡了 null。
 *
 * `contractId !== null` 放行空串 ⇒ 每渲染一次真发一发 `GET /contracts/`（id 是空段，
 * 命中的是列表路由或 404），再把这个响应喂给 `toContractOption`——页面上「我的合约」卡
 * 会显示一条从别的合约身上取来的数据。空串和 null 在这里是同一件事：手里没有 id。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { createElement } from 'react';
import { createTestQueryClient } from '../../../test/test-utils';

vi.mock('../../../services/contractApi', () => ({
  contractApi: { get: vi.fn(), list: vi.fn() },
}));

import { contractApi } from '../../../services/contractApi';
import { useContractById } from '../useContractOptions';

const get = vi.mocked(contractApi.get);

beforeEach(() => {
  get.mockReset();
  get.mockResolvedValue({ id: 'c1', title: 't', contract_no: 'N1', status: 'active' } as never);
});

function renderWithClient(contractId: string | null) {
  const client = createTestQueryClient();
  const wrapper = ({ children }: { children: ReactNode }) =>
    createElement(QueryClientProvider, { client }, children);
  return renderHook(() => useContractById(contractId), { wrapper });
}

describe('A263：没有 id 就不该发这一枪', () => {
  it('null 不发请求（原有行为）', async () => {
    const { result } = renderWithClient(null);
    await new Promise((r) => setTimeout(r, 10));
    expect(get).not.toHaveBeenCalled();
    expect(result.current.fetchStatus).toBe('idle');
  });

  it('空串同样不发请求（这就是修的那一条）', async () => {
    const { result } = renderWithClient('');
    await new Promise((r) => setTimeout(r, 10));
    expect(get).not.toHaveBeenCalled();
    expect(result.current.fetchStatus).toBe('idle');
    // 不发也不许把状态说成成功：它是 idle，页面按「没有合约」那一支渲染。
    expect(result.current.isSuccess).toBe(false);
  });

  it('真有 id 时照常取回（收紧 enabled 不能把正例一起关掉）', async () => {
    const { result } = renderWithClient('c1');
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(get).toHaveBeenCalledWith('c1');
    expect(result.current.data?.id).toBe('c1');
  });
});
