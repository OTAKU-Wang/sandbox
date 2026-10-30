/**
 * A271 的前端半：KMS 键的类型与算法读后端真值，不再代填。
 *
 * 台账：`app/api/kms.py` 的键列表/详情本来就逐条回 `key_type` 与 `algorithm`（后端半已就位），
 * 而前端 `normalizeKmsKey` 把 `key_type` 写死成 `'dek'`、把缺字段兑成 `'SM4'`。
 * 两类危害都在「编一个出来」这一族里：session/kek 键在 UI 上被标成 DEK（分类是编的），
 * 算法缺失时显示 SM4（这台机器可能压根不是 SM4）——与 A262 编时间、A257 缺字段判通过同源。
 * 缺就是缺，落到 `unknown` / `undefined`，由 UI 记「未记录」。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { existsSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';

vi.mock('../api', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));

import api from '../api';
import { kmsApi } from '../identityApi';

const get = vi.mocked(api.get);
const post = vi.mocked(api.post);

/** 本池 import.meta.url 不是 file: scheme ⇒ 静态门从 cwd 往上找源文件（同 workspace.test.ts）。 */
function readSource(rel: string): string {
  let dir = process.cwd();
  for (let depth = 0; depth < 6; depth += 1) {
    const candidate = join(dir, rel);
    if (existsSync(join(dir, 'package.json')) && existsSync(candidate)) return readFileSync(candidate, 'utf8');
    const parent = dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  throw new Error(`找不到源文件 ${rel}`);
}

beforeEach(() => {
  get.mockReset();
  post.mockReset();
});

async function listKeys(items: unknown[]) {
  get.mockResolvedValue({ items } as never);
  return kmsApi.listKeys();
}

describe('A271：key_type 读真值', () => {
  it('后端给 session/kek 时不再被改写成 dek', async () => {
    const out = await listKeys([
      { key_id: 'k1', key_type: 'dek', algorithm: 'SM4-GCM', status: 'active' },
      { key_id: 'k2', key_type: 'session', algorithm: 'SM4-GCM', status: 'active' },
      { key_id: 'k3', key_type: 'kek', algorithm: 'SM2', status: 'active' },
    ]);
    expect(out.map((k) => k.key_type)).toEqual(['dek', 'session', 'kek']);
  });

  it('字段缺失或不认识的值落 unknown，而不是默认 dek', async () => {
    const out = await listKeys([
      { key_id: 'k4', status: 'active' },
      { key_id: 'k5', key_type: null, status: 'active' },
      { key_id: 'k6', key_type: 'hsm-master', status: 'active' },
    ]);
    expect(out.map((k) => k.key_type)).toEqual(['unknown', 'unknown', 'unknown']);
  });
});

describe('A271：algorithm 缺失就是缺失', () => {
  it('后端下发的算法原样透出', async () => {
    const out = await listKeys([{ key_id: 'k1', key_type: 'dek', algorithm: 'AES-256-GCM', status: 'active' }]);
    expect(out[0].algorithm).toBe('AES-256-GCM');
  });

  it('缺失 / null / 空串都不兑成 SM4', async () => {
    const out = await listKeys([
      { key_id: 'k2', key_type: 'dek', status: 'active' },
      { key_id: 'k3', key_type: 'dek', algorithm: null, status: 'active' },
      { key_id: 'k4', key_type: 'dek', algorithm: '   ', status: 'active' },
    ]);
    expect(out.map((k) => k.algorithm)).toEqual([undefined, undefined, undefined]);
  });

  it('单条取回走同一个归一化（createKey 也不能自己编）', async () => {
    post.mockResolvedValue({ key_id: 'k9', key_type: 'session', status: 'active' } as never);
    const one = await kmsApi.createKey({ product_id: 'p1' });
    expect(one.key_type).toBe('session');
    expect(one.algorithm).toBeUndefined();
  });
});

describe('A271：字面量不再回到产品代码', () => {
  it('identityApi 里不再出现写死的 key_type: \'dek\'', () => {
    const src = readSource('src/services/identityApi.ts');
    expect(src).not.toMatch(/key_type:\s*'dek'/);
    expect(src).not.toMatch(/algorithm:\s*[^,\n]*\|\|\s*'SM4'/);
  });

  it('密钥管理页的算法列不再用 \'SM4\' 兜底，未知类型有「未记录」一档', () => {
    const src = readSource('src/pages/Identity/KeyManagement.tsx');
    expect(src).not.toMatch(/dataIndex:\s*'algorithm'[^}]*\|\|\s*'SM4'/);
    expect(src).toMatch(/unknown:\s*'未记录'/);
  });
});
