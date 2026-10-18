import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../api', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
  },
}));

import api from '../api';
import { federationApi, CATALOG_ENTRY_LIMIT } from '../federationApi';
import { ListShapeError } from '../../utils/unwrapList';

const get = api.get as unknown as ReturnType<typeof vi.fn>;

const entry = (i: number) => ({
  id: `p${i}`,
  remote_space_id: 'sp1',
  product_name: `[remote:sp1] 产品 ${i}`,
  product_id: `remote-${i}`,
  synced_at: '2026-10-01T00:00:00',
});

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
});

describe('federationApi.listSyncedEntries — 后端裸列表 + limit=Query(100,ge=1,le=500)（app/api/federation.py:261）', () => {
  it('显式带上 limit=500（后端默认 100，不传就静默截断）', async () => {
    get.mockResolvedValue([]);
    await federationApi.listSyncedEntries();
    expect(get).toHaveBeenCalledWith('/federation/catalog/entries', { params: { limit: CATALOG_ENTRY_LIMIT } });
    expect(CATALOG_ENTRY_LIMIT).toBe(500);
  });

  it('带 space_id 时同时带上 limit', async () => {
    get.mockResolvedValue([]);
    await federationApi.listSyncedEntries('sp1');
    expect(get).toHaveBeenCalledWith('/federation/catalog/entries', { params: { space_id: 'sp1', limit: 500 } });
  });

  it('返回 {items, limit, possiblyTruncated}', async () => {
    get.mockResolvedValue([entry(1), entry(2), entry(3)]);
    const res = await federationApi.listSyncedEntries('sp1');
    expect(res.items).toHaveLength(3);
    expect(res.limit).toBe(500);
    expect(res.possiblyTruncated).toBe(false);
  });

  it('条目数正好等于 limit → possiblyTruncated=true（后端无 total/has_more）', async () => {
    get.mockResolvedValue(Array.from({ length: 500 }, (_, i) => entry(i)));
    const res = await federationApi.listSyncedEntries();
    expect(res.possiblyTruncated).toBe(true);
  });

  it('后端返回多于 limit（防御性）→ 仍然报告可能截断', async () => {
    get.mockResolvedValue(Array.from({ length: 501 }, (_, i) => entry(i)));
    const res = await federationApi.listSyncedEntries();
    expect(res.possiblyTruncated).toBe(true);
  });

  it('接受 {items} 包装；未识别载荷抛错而不是把对象当数组返回', async () => {
    get.mockResolvedValue({ items: [entry(1)] });
    expect((await federationApi.listSyncedEntries()).items).toHaveLength(1);
    get.mockResolvedValue({ entries: [entry(1)] });
    await expect(federationApi.listSyncedEntries()).rejects.toBeInstanceOf(ListShapeError);
  });
});

describe('federationApi.listTrusts — 规范形态裸数组（app/api/federation.py:94）', () => {
  it('解出并归一化信任关系', async () => {
    get.mockResolvedValue([TRUST_ROW]);
    const res = await federationApi.listTrusts();
    expect(res[0]).toMatchObject({ trust_id: 't1', remote_space_id: 'sp1', remote_space_name: 'Space One' });
  });

  it('后端缺 remote_space 时不再伪造空字符串联合键', async () => {
    get.mockResolvedValue([{ ...TRUST_ROW, remote_space: undefined, remote_space_name: undefined }]);
    const [trust] = await federationApi.listTrusts();
    expect(trust.remote_space_id).toBeUndefined();
  });

  it('未识别载荷抛 ListShapeError', async () => {
    get.mockResolvedValue({ trusts: [TRUST_ROW] });
    await expect(federationApi.listTrusts()).rejects.toBeInstanceOf(ListShapeError);
  });
});

describe('federationApi.getSyncStatus — 规范形态裸数组（app/api/federation.py:211）', () => {
  it('解出裸数组', async () => {
    get.mockResolvedValue([{ space_id: 'sp1', space_name: 'S', last_sync_at: null, entry_count: 0, status: 'never_synced' }]);
    expect(await federationApi.getSyncStatus()).toHaveLength(1);
  });

  it('未识别载荷抛 ListShapeError', async () => {
    get.mockResolvedValue({ status: [] });
    await expect(federationApi.getSyncStatus()).rejects.toBeInstanceOf(ListShapeError);
  });
});
