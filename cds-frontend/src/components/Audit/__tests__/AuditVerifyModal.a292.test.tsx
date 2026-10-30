/**
 * A292：验真弹窗的结论与披露必须同源。
 *
 * `deriveVerdict` 只看 `GET /audit/verify/{id}`；而披露行（backend / block_number / anchor_id /
 * confirmed / tx_hash / merkle_root）原先一律写成 `verifyData?.x ?? proofData?.x`。两个端点对同一
 * 字段给不同值是真实存在的形态——`/verify` 找不到匹配锚定行时那几个字段全 null，
 * `/merkle-proof` 却会按「当前配置的后端」回一份披露 ⇒ 同屏出现「判定：存证后端未知」+
 * 「联盟链锚定信息齐全」。修法只有一个：披露字段不回退，只有叶子哈希（仅 /merkle-proof 下发）
 * 保留自己的来源。这条测试钉的是「不一致时不拿 B 补 A」，不是某个后端的形状。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { renderWithProviders } from '../../../test/test-utils';

// antd 的 Grid/响应式订阅 window.matchMedia，jsdom 没有；缺了它 Modal 一挂载就炸在响应式钩子里。
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: (query: string) => ({
    matches: false, media: query, onchange: null,
    addListener: vi.fn(), removeListener: vi.fn(),
    addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: vi.fn(),
  }),
});

vi.mock('../../../services/auditApi', () => ({
  auditApi: { verify: vi.fn(), getMerkleProof: vi.fn() },
}));

import { auditApi } from '../../../services/auditApi';
import AuditVerifyModal from '../AuditVerifyModal';

const verify = vi.mocked(auditApi.verify);
const proof = vi.mocked(auditApi.getMerkleProof);

beforeEach(() => {
  verify.mockReset();
  proof.mockReset();
});

describe('A292：两路披露不一致时不互相兜底', () => {
  it('/verify 说后端未知，就不许用 /merkle-proof 的链上披露把它补成「已锚定联盟链」', async () => {
    verify.mockResolvedValue({
      record_id: 'r1',
      verified: true,
      tx_hash: '0xfrom-verify',
      merkle_root: '0xroot',
      // 关键：/verify 找不到匹配锚定行 ⇒ 这几个字段全空
      backend: null,
      anchor_id: null,
      block_number: null,
      confirmed: false,
      verification_note: null,
      reason: null,
    } as never);
    proof.mockResolvedValue({
      record_hash: '0xleaf',
      proof_path: [],
      total_leaves: 1,
      leaf_index: 0,
      // 另一路按「当前配置的后端」回了一份完整披露——正是它不能被拿来兜上面的空值
      block_number: 987654,
      anchor_id: 'anchor-from-proof',
      confirmed: true,
      backend: { backend: 'fisco-bcos', backend_label: 'FISCO BCOS', is_consortium_chain: true, chain_anchoring: 'fisco-onchain' },
    } as never);

    renderWithProviders(<AuditVerifyModal recordId="r1" open onClose={() => {}} />);

    // ① 判定按 /verify 的口径：后端未知 ⇒ 降级，不点亮成功态
    await waitFor(() => expect(screen.getByText('root 比对一致，但存证后端未知')).toBeInTheDocument());
    expect(screen.queryByText('联盟链存证验真通过')).toBeNull();

    // ② 披露块也不得从另一路拿值：链名、锚定 ID、区块高度、确认态都不得出现 B 路口径
    expect(screen.queryByText(/FISCO BCOS/)).toBeNull();
    expect(screen.queryByText('是否联盟链')).toBeNull();
    const body = document.body.textContent ?? '';
    expect(body).not.toContain('987654');
    expect(body).not.toContain('anchor-from-proof');
    expect(screen.getByText(/后端未提供区块高度/)).toBeInTheDocument();

    // ③ 同源的那些字段仍如实展示（收紧回退不能把正例也清掉）
    expect(screen.getByText('0xfrom-verify')).toBeInTheDocument();

    // ④ 叶子哈希是唯一例外：只有 /merkle-proof 下发，没有可比对的同源对象
    await waitFor(() => expect(screen.getByText('0xleaf')).toBeInTheDocument());
  });

  it('/verify 自己就披露了联盟链后端时，披露块照常展示（不是把披露整块砍掉）', async () => {
    verify.mockResolvedValue({
      record_id: 'r2',
      verified: true,
      tx_hash: '0xfrom-verify',
      merkle_root: '0xroot',
      anchor_id: 'anchor-1',
      block_number: 42,
      confirmed: true,
      verification_note: null,
      reason: null,
      backend: { backend: 'fisco-bcos', backend_label: 'FISCO BCOS', is_consortium_chain: true, chain_anchoring: 'fisco-onchain' },
    } as never);
    proof.mockResolvedValue({ record_hash: '0xleaf2', proof_path: [], total_leaves: 1, leaf_index: 0 } as never);

    renderWithProviders(<AuditVerifyModal recordId="r2" open onClose={() => {}} />);

    await waitFor(() => expect(screen.getByText('联盟链存证验真通过')).toBeInTheDocument());
    expect(screen.getByText(/FISCO BCOS/)).toBeInTheDocument();
    expect(screen.getByText('42')).toBeInTheDocument();
    expect(screen.getByText('anchor-1')).toBeInTheDocument();
  });
});
