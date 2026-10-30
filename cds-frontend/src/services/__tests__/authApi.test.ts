// A258 的「死代码」这一半：服务端签名接口把 SM2 私钥放在**请求体**里逐次上行。
//
// 前端签名本来就在本地做（utils/crypto.ts 的 sm2Sign），`authApi.signData` 全仓零调用方，
// 留着只会被下一次「顺手接上」。这里钉两条不变量：
// 1) authApi 不再导出这个口子，且合法端点一个都没被顺手删掉；
// 2) services/ 这一层任何模块都不得把私钥放进上行请求体（跨模块的机器不变量，静态门）。
import { readFile, readdir } from 'node:fs/promises';
import { resolve } from 'node:path';
import { describe, it, expect } from 'vitest';
import { authApi } from '../authApi';

// 【测试环境接线，不是断言】本仓库 vitest 跑在 environment: 'jsdom' 下，测试模块的
// `import.meta.url` 是 vite dev server 的 http: URL，`fileURLToPath()` 卜在调用当场抛
// `TypeError: The URL must be of scheme file`，导致下面那条静态门根本跑不到断言。
// 改用 vitest 进程的 cwd 定位源码目录（车道命令是 cds-frontend/ 下的
// `node_modules/.bin/vitest run`，cwd = cds-frontend），扫的文件集合与断言一字未改。
// 目录不存在时 readdir 直接抛错，一个 .ts 也没扫到时也抛错——避免拿空集合把静态门扫成假绿。
const SERVICES_DIR = resolve(process.cwd(), 'src/services');

const SERVICE_FILES = async (): Promise<Array<{ name: string; source: string }>> => {
  const names = (await readdir(SERVICES_DIR)).filter((name) => name.endsWith('.ts'));
  if (names.length === 0) throw new Error(`未在服务目录扫到任何 .ts 源文件：${SERVICES_DIR}（cwd 不对？）`);
  return Promise.all(
    names.map(async (name) => ({
      name,
      source: await readFile(resolve(SERVICES_DIR, name), 'utf8'),
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
