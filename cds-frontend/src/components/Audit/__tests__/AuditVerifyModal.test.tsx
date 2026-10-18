// 4-2「存证验真」弹窗的判定用例（只 mock services/auditApi 这一层）：
// ① verify 重取失败时，缓存里上一轮的成功判定必须整块消失——「验真请求失败」与
//    绿色「验真通过」不得同屏（react-query 在 refetch 失败时保留旧 data）；
// ② proof_path 为空只有在 total_leaves===1 时才是「单叶批次」，多叶批次里的空路径
//    是路径不完整，绝不给正向结论（后端 audit.py::get_merkle_proof 在找不到目标叶
//    子时同样回 proof_path=[]，total_leaves 才是批次规模的真值）；
// ③ 判定只读 /verify，披露行不得再拿 /merkle-proof 的值兜底——两个接口对同一字段
//    给不同值时（后端把 /verify 的 anchor 缺失与 /merkle-proof 的「当前配置后端」
//    分别下发），绿色结论与「联盟链」披露会互相打脸；
// ④ 路径还没取回时不得先把结论点亮。
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen } from '@testing-library/react';
import { QueryClient } from '@tanstack/react-query';
import { renderWithProviders } from '../../../test/test-utils';
import AuditVerifyModal from '../AuditVerifyModal';
import { auditApi, type AuditVerifyResult, type MerkleProofResult } from '../../../services/auditApi';

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
    verify: vi.fn(),
    getMerkleProof: vi.fn(),
  },
}));

const RECORD_ID = '9b75d339-0000-4000-8000-000000000001';

const CONSORTIUM_BACKEND = {
  backend: 'fisco_bcos',
  backend_label: 'FISCO BCOS 联盟链',
  is_consortium_chain: true,
  chain_anchoring: 'fisco-sim',
};

function verifyOk(overrides: Partial<AuditVerifyResult> = {}): AuditVerifyResult {
  return {
    verified: true,
    tx_hash: '0x9f3a',
    record_id: RECORD_ID,
    anchor_id: 'anchor-verify-1',
    block_number: 4211,
    confirmed: true,
    merkle_root: 'root-from-verify',
    backend: CONSORTIUM_BACKEND,
    verification_note: '联盟链存证校验通过',
    ...overrides,
  };
}

function proofOneLeaf(overrides: Partial<MerkleProofResult> = {}): MerkleProofResult {
  return {
    record_id: RECORD_ID,
    record_hash: 'leaf-hash-1',
    tx_hash: '0x9f3a',
    anchor_id: 'anchor-verify-1',
    block_number: 4211,
    confirmed: true,
    merkle_root: 'root-from-verify',
    proof_path: [],
    leaf_index: 0,
    total_leaves: 1,
    backend: CONSORTIUM_BACKEND,
    ...overrides,
  };
}

function successAlerts() {
  return document.body.querySelectorAll('.ant-alert-success');
}

/** 预置缓存要用到 gcTime：共用测试客户端 gcTime=0，卸载即回收。 */
function makeClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 60_000 }, mutations: { retry: false } },
  });
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('AuditVerifyModal 判定与披露', { timeout: 20_000 }, () => {
  it('verify 重取失败：只剩错误态，上一轮的绿色判定不得同屏', async () => {
    const queryClient = makeClient();
    // 首轮成功的两份数据先躺进缓存，随后接口挂了（react-query 失败时保留旧 data）
    queryClient.setQueryData(['audit-verify', RECORD_ID], verifyOk());
    queryClient.setQueryData(['audit-merkle-proof', RECORD_ID], proofOneLeaf());
    vi.mocked(auditApi.verify).mockRejectedValue({ code: 'INTERNAL_ERROR', detail: '存证后端连接超时', request_id: 'r-1' });
    vi.mocked(auditApi.getMerkleProof).mockResolvedValue(proofOneLeaf());

    renderWithProviders(<AuditVerifyModal recordId={RECORD_ID} open onClose={() => {}} />, { queryClient });

    await screen.findByText('验真请求失败');
    await screen.findByText('存证后端连接超时');
    expect(document.body.textContent).not.toContain('联盟链存证验真通过');
    expect(successAlerts()).toHaveLength(0);
  });

  it('proof_path 空 + total_leaves>1：判为路径不完整，不给正向结论', async () => {
    vi.mocked(auditApi.verify).mockResolvedValue(verifyOk());
    vi.mocked(auditApi.getMerkleProof).mockResolvedValue(
      proofOneLeaf({ proof_path: [], leaf_index: 2, total_leaves: 4 }),
    );

    renderWithProviders(<AuditVerifyModal recordId={RECORD_ID} open onClose={() => {}} />);

    // 段内结论与顶部判定都得转灰（两段文案在修复前的 DOM 里都不存在）
    await screen.findByText('Merkle 路径不完整，无法离线复核');
    expect(document.body.textContent).toContain('root 比对一致，但 Merkle 路径不完整');
    expect(document.body.textContent).not.toContain('本批次仅此一条记录');
    expect(document.body.textContent).not.toContain('联盟链存证验真通过');
    expect(successAlerts()).toHaveLength(0);
    // 批次规模照后端 total_leaves 原样披露
    expect(document.body.textContent).toContain('4');
  });

  it('proof_path 空 + total_leaves===1 仍是合法单叶批次（绿色结论保留）', async () => {
    vi.mocked(auditApi.verify).mockResolvedValue(verifyOk());
    vi.mocked(auditApi.getMerkleProof).mockResolvedValue(proofOneLeaf());

    renderWithProviders(<AuditVerifyModal recordId={RECORD_ID} open onClose={() => {}} />);

    await screen.findByText('本批次仅此一条记录，Merkle root 即叶子哈希本身，无需兄弟节点。');
    expect(document.body.textContent).toContain('联盟链存证验真通过');
    expect(successAlerts().length).toBeGreaterThan(0);
  });

  it('披露行的五个字段只认 /verify：/merkle-proof 的不同取值不得顶上', async () => {
    vi.mocked(auditApi.verify).mockResolvedValue(
      // 后端找不到与该 tx_hash 匹配的锚定行时，/verify 的 backend/anchor_id/block_number 全为 null
      verifyOk({ backend: null, anchor_id: null, block_number: null, confirmed: undefined }),
    );
    vi.mocked(auditApi.getMerkleProof).mockResolvedValue(
      proofOneLeaf({
        anchor_id: 'anchor-proof-only',
        block_number: 12345,
        confirmed: true,
        merkle_root: 'root-from-proof',
        backend: CONSORTIUM_BACKEND,
      }),
    );

    renderWithProviders(<AuditVerifyModal recordId={RECORD_ID} open onClose={() => {}} />);

    await waitForProof();
    // 判定用的是 /verify（后端未知 → 降级），披露块就必须说「没有后端披露」，
    // 不能拿 /merkle-proof 的「当前配置后端」冒充这一条记录的锚定来源
    expect(document.body.textContent).toContain('未取得存证后端披露信息');
    expect(document.body.textContent).not.toContain('FISCO BCOS 联盟链');
    expect(document.body.textContent).not.toContain('12345');
    expect(document.body.textContent).not.toContain('anchor-proof-only');
    expect(document.body.textContent).not.toContain('已确认');
    expect(document.body.textContent).toContain('root-from-verify');
  });

  it('路径还在取回途中就下结论：先把结论压成待判定', async () => {
    vi.mocked(auditApi.verify).mockResolvedValue(verifyOk());
    vi.mocked(auditApi.getMerkleProof).mockReturnValue(new Promise<MerkleProofResult>(() => {}));

    renderWithProviders(<AuditVerifyModal recordId={RECORD_ID} open onClose={() => {}} />);

    await screen.findByText('联盟链存证校验通过');
    expect(document.body.textContent).not.toContain('联盟链存证验真通过');
    expect(successAlerts()).toHaveLength(0);
    expect(document.body.textContent).toContain('Merkle 路径尚未取回');
  });
});

/** proof 查询按 anchored 开启：等它至少发出去一次，避免把「还没轮到」读成「已经取回」。 */
async function waitForProof() {
  await vi.waitFor(() => expect(auditApi.getMerkleProof).toHaveBeenCalledWith(RECORD_ID));
  await vi.waitFor(() => expect(document.body.textContent).toContain('叶子序号'));
}
