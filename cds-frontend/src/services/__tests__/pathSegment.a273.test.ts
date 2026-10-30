/**
 * A273（路径拼接不百分号编码，台账记 112 处）的**类级收口**配套测试。
 *
 * 台账口径：`/resource/${id}` 这种写法本身是仓内约定，不是 112 个独立缺陷 ⇒ 不去撒 112 个
 * patch，而是两处收口：① `services/pathSegment.ts` 的 `p()` 供显式点名（用户可控段）；
 * ② `api.ts` 的请求拦截器对**每一条**出站路径做一次 `normalizeRequestPath`（幂等补编码）。
 * 本文件钉这两层的行为边界，尤其是「幂等」——拦截器会看到所有请求，把已经编码好的
 * `/a/b%20c` 再编码成 `%2520` 就是把 112 处旧账换成一片新故障。
 */
import { describe, it, expect } from 'vitest';
import { isSafeSegment, normalizeRequestPath, p } from '../pathSegment';

describe('p()：单个路径段', () => {
  it('安全字符集原样通过（后端下发的 id 绝大多数落在这里，不该被改写成 % 串）', () => {
    expect(p('sess_2026-10-30')).toBe('sess_2026-10-30');
    expect(p('a.b~c-d_e')).toBe('a.b~c-d_e');
    expect(p('SM4')).toBe('SM4');
  });

  it('能改写路由的字符一律编码', () => {
    expect(p('a/b')).toBe('a%2Fb');
    expect(p('a?b=c&d=e')).toBe('a%3Fb%3Dc%26d%3De');
    expect(p('a b')).toBe('a%20b');
    expect(p('#frag')).toBe('%23frag');
    expect(p('..')).toBe('..'); // 点段在安全集内原样保留，交给 normalizeRequestPath/后端处理
  });

  it('空段当场报错，而不是拼出 /a//b 打到别的路由上', () => {
    expect(() => p('')).toThrow();
    expect(() => p(null as unknown as string)).toThrow();
    expect(() => p(undefined as unknown as string)).toThrow();
  });

  it('isSafeSegment 与 p() 的白名单同一条（不一致就等于两套口径）', () => {
    expect(isSafeSegment('ok_1')).toBe(true);
    expect(isSafeSegment('bad/1')).toBe(false);
    for (const probe of ['a b', 'a/b', 'a?b', 'a#b', 'a%b', 'a&b', 'a=b']) {
      expect(isSafeSegment(probe)).toBe(false);
      expect(p(probe)).not.toBe(probe);
    }
  });
});

describe('normalizeRequestPath()：整条出站路径', () => {
  it('幂等——已编码的 %XX 保持不动（拦截器作用于全部请求，二次编码就是自我污染）', () => {
    const once = normalizeRequestPath('/sandbox/sessions/a%20b/files');
    expect(once).toBe('/sandbox/sessions/a%20b/files');
    expect(normalizeRequestPath(once)).toBe(once);
    expect(normalizeRequestPath('/x/%2F/y')).toBe('/x/%2F/y');
  });

  it('未编码的空格被补上编码', () => {
    expect(normalizeRequestPath('/a/b c/d')).toBe('/a/b%20c/d');
  });

  it('`#` 是片段边界：整条路径层面不吞不编（要当字面量进段里的 id 必须走 p()）', () => {
    // 拦截器看到的是已经拼好的整串，这里若把 `#` 之后当普通段编码，等于把 axios/浏览器
    // 一直以来的片段语义改掉了；段内含 `#` 的 id 由 p() 在拼进去之前就编成 %23（上面钉过）。
    expect(normalizeRequestPath('/a/#x')).toBe('/a/#x');
    expect(p('a#b')).toBe('a%23b');
  });

  it(' lone % 不是合法转义 ⇒ 自身编成 %25，而不是留下一个后端解不开的悬空 %', () => {
    expect(normalizeRequestPath('/a/100%25/2')).toBe('/a/100%25/2');
    expect(normalizeRequestPath('/a/100%/2')).toBe('/a/100%25/2');
  });

  it('查询串与 hash 原样保留（编码口径属于 axios params，不在这里二次加工）', () => {
    expect(normalizeRequestPath('/a/b?x=1&y=a b')).toBe('/a/b?x=1&y=a b');
    expect(normalizeRequestPath('/a/b#frag')).toBe('/a/b#frag');
  });

  it('尾斜杠合法，中间空段（id 到手是空串）报错', () => {
    expect(normalizeRequestPath('/a/b/')).toBe('/a/b/');
    expect(() => normalizeRequestPath('/a//b')).toThrow();
    expect(() => normalizeRequestPath('//a')).toThrow();
  });

  it(' baseURL 相对的完整路径与前导斜杠都按同一条规则处理', () => {
    expect(normalizeRequestPath('/flow/openapi-calls/c%20d/stats')).toBe('/flow/openapi-calls/c%20d/stats');
    expect(normalizeRequestPath('/flow/openapi-calls/c d/stats')).toBe('/flow/openapi-calls/c%20d/stats');
  });
});
