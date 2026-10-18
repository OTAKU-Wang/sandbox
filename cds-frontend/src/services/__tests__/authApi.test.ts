// A258 的「死代码」这一半：服务端签名接口把 SM2 私钥放在**请求体**里逐次上行。
//
// 前端签名本来就在本地做（utils/crypto.ts 的 sm2Sign），`authApi.signData` 全仓零调用方，
// 留着只会被下一次「顺手接上」。这里钉两条不变量：
// 1) authApi 不再导出这个口子，且合法端点一个都没被顺手删掉；
// 2) services/ 这一层任何模块都不得把私钥放进上行请求体（跨模块的机器不变量，静态门）。
import { readFile, readdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { describe, it, expect } from 'vitest';
import { authApi } from '../authApi';

const SERVICE_FILES = async (): Promise<Array<{ name: string; source: string }>> => {
  const names = (await readdir(new URL('../', import.meta.url))).filter((name) => name.endsWith('.ts'));
  return Promise.all(
    names.map(async (name) => ({
      name,
      source: await readFile(fileURLToPath(new URL(`../${name}`, import.meta.url)), 'utf8'),
    })),
  );
};

// 上行请求体里出现私钥：api.post/put/patch/delete(...) 的入参里带 private_key / privateKey。
const PRIVATE_KEY_IN_REQUEST_BODY = /api\.(post|put|patch|delete)\s*\([\s\S]{0,200}?\b(?:private_key|privateKey)\b[^)]*\)/;

describe('authApi 不再把 SM2 私钥发给后端（A258）', () => {
  it('死代码 signData 已删除，其余端点原样在位（防止顺手删多了）', () => {
    expect(Object.keys(authApi)).not.toContain('signData');
    for (const kept of ['login', 'register', 'refresh', 'getMe', 'generateSM2Keys']) {
      expect(typeof (authApi as Record<string, unknown>)[kept]).toBe('function');
    }
  });

  it('services/ 全层没有任何请求把私钥放进上行 body', async () => {
    const offenders: string[] = [];
    for (const { name, source } of await SERVICE_FILES()) {
      if (PRIVATE_KEY_IN_REQUEST_BODY.test(source)) offenders.push(name);
    }
    expect(offenders).toEqual([]);
  });
});
