/**
 * A262（服务层归一化闸）：跨空间互信记录缺 `created_at` 时不得用「现在」兜底。
 * 台账 ocr-verified-findings.md §A262：`services/federationApi.ts:59`
 * `created_at: item.created_at || new Date().toISOString()` —— 后端字段缺失/改名/结构变化时，
 * UI 会出现一个「看起来完全正常」的当前时间。created_at 在互信记录上是**证据**，不是装饰：
 * 不知道的时候必须是 null（渲染层显示 UNKNOWN_TEXT），不能显示一个好看的值。
 * 同族口径见 `pages/Flow/devTasksModel.ts:314-315`「编不了时间也不装『刚刚』」，
 * 同文件 `:55/:58` 的 `remote_space_name || ''`、`allowed_operations || []` 都是中性兜底。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../api', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
  },
}));

import api from '../api';
import { federationApi, TRUST_TIME_UNKNOWN, formatTrustTimestamp } from '../federationApi';

const get = api.get as unknown as ReturnType<typeof vi.fn>;
const post = api.post as unknown as ReturnType<typeof vi.fn>;

const TRUST_ROW = {
  trust_id: 't1',
  remote_space: 'sp1',
  remote_space_name: 'Space One',
  endpoint: 'https://sp1.example.com/api',
  trust_level: 'verified',
  status: 'active',
  allowed_operations: ['read_catalog'],
  created_at: '2026-10-01T00:00:00',
};

beforeEach(() => {
  get.mockReset();
  post.mockReset();
});

describe('A262 互信记录 created_at 是证据，缺了只能报缺', () => {
  it('列表缺 created_at → null，而不是兜一个当前时间', async () => {
    const before = Date.now();
    get.mockResolvedValue([{ ...TRUST_ROW, created_at: undefined }]);
    const [trust] = await federationApi.listTrusts();
    expect(trust.created_at).toBeNull();
    const fabricatedNow = trust.created_at !== null && Date.parse(trust.created_at) >= before;
    expect(fabricatedNow, '兜了一个当前时间 = 伪造审计时间').toBe(false);
  });

  it('created_at 为空串/空白 → null（空值不是一个时刻）', async () => {
    get.mockResolvedValue([{ ...TRUST_ROW, created_at: '   ' }]);
    const [trust] = await federationApi.listTrusts();
    expect(trust.created_at).toBeNull();
  });

  it('后端给了就原样透出，不做本地时区/格式改写', async () => {
    get.mockResolvedValue([TRUST_ROW]);
    const [trust] = await federationApi.listTrusts();
    expect(trust.created_at).toBe(TRUST_ROW.created_at);
  });

  it('单条 getTrust 缺 created_at 同样返回 null', async () => {
    get.mockResolvedValue({ ...TRUST_ROW, created_at: undefined });
    const trust = await federationApi.getTrust('t1');
    expect(trust.created_at).toBeNull();
  });

  it('写操作（建立/吊销）的回包也不编时间', async () => {
    post.mockResolvedValue({ ...TRUST_ROW, created_at: undefined });
    const established = await federationApi.establishTrust({
      space_id: 'sp1',
      space_name: 'Space One',
      endpoint: 'https://sp1.example.com/api',
      trust_level: 'verified',
      allowed_operations: ['read_catalog'],
    });
    expect(established.created_at).toBeNull();

    post.mockResolvedValue({ ...TRUST_ROW, created_at: undefined });
    const revoked = await federationApi.revokeTrust('t1');
    expect(revoked.created_at).toBeNull();
  });
});

describe('A262 渲染层可用的不编造时间格式化', () => {
  it('null → 未记录（不是 1970，也不是今天）', () => {
    expect(formatTrustTimestamp(null)).toBe(TRUST_TIME_UNKNOWN);
  });

  it('能解析的时间照页面旧口径本地化', () => {
    expect(formatTrustTimestamp(TRUST_ROW.created_at)).toBe(new Date(TRUST_ROW.created_at).toLocaleString());
  });

  it('解不了的时间原样透出，不做美化', () => {
    expect(formatTrustTimestamp('not-a-timestamp')).toBe('not-a-timestamp');
  });
});
