/**
 * A247：`ERROR_HINTS[code]` 的原型链命中。
 *
 * 旧写法 `code in ERROR_HINTS` 走的是原型链：`code='constructor'` 也算「命中」，
 * 于是拿到 `Object.prototype.constructor` 当提示对象，后续 `.hint/.next` 全 undefined，
 * 卡片渲染成一条空白提示——错误码来自后端下发/用户可影响的响应头，这条路径不是纯理论。
 * 修法只有一处：`Object.hasOwn`（ES2022+，本仓 tsconfig lib 已含 ES2023）。
 */
import { describe, it, expect } from 'vitest';
import { hintForCode } from '../errorHints';

describe('A247：错误码查表只认自有键', () => {
  it('原型链上的键不再被当成命中的错误码', () => {
    for (const probe of ['constructor', '__proto__', 'toString', 'hasOwnProperty', 'valueOf']) {
      const hint = hintForCode(probe);
      // 未登记的码必须走兜底文案；兜底是「没查到」的样子，不是 undefined 拼出来的空卡。
      expect(typeof hint.hint).toBe('string');
      expect(hint.hint.length).toBeGreaterThan(0);
      expect(hint.hint).not.toBe('undefined');
    }
  });

  it('__proto__ 这个键名本身不会污染表（写进去也不会改到 Object.prototype）', () => {
    hintForCode('__proto__');
    expect(({} as Record<string, unknown>).polluted).toBeUndefined();
  });

  it('真登记的码照常命中（收紧查询不能把正例一起收紧）', () => {
    const hit = hintForCode('SESSION_TERMINATED', 410);
    expect(hit.retryable).toBe(false);
    expect(hit.hint.length).toBeGreaterThan(0);
  });

  it('未知码走兜底（FALLBACK 的口径是有状态码回退，别把它当成「查到了」）', () => {
    const miss = hintForCode('TOTALLY_UNKNOWN_CODE_2026_10_30');
    expect(miss.hint).toBe('请求失败');
    // 状态码回退仍然优先于「码不认识」：410 的码再怪也不给重试。
    expect(hintForCode('TOTALLY_UNKNOWN_CODE_2026_10_30', 410).retryable).toBe(false);
  });
});
