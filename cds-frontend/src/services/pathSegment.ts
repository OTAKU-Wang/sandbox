/**
 * A273（2026-10-30 收口）：路径段拼接的唯一出入口。
 *
 * 台账口径先说准：`grep 'api.x(`/...${`'` 数出来的 **112 处** `/resource/${id}` 是一个仓内约定，
 * 不是 112 个缺陷——那些 id 由后端签发，拼进路径不构成注入。真正危险的是「段里可能出现
 * `/`、`?`、`#`、空格、`..`」的那几处，它们已由 A246 单点收口（钉在
 * `__tests__/pathSegments.a246.test.ts`）。所以这里**不去撒 112 个补丁**，而是补类级修法：
 *
 * 1. `p()` —— 新代码与用户可控段用的段编码器：安全字符集原样透出（URL 保持可读），
 *    其它一律 `encodeURIComponent`；空段直接抛，因为「拿不到 id 还发请求」只会打到别的路由上。
 * 2. `normalizeRequestPath()` —— 挂在 `services/api.ts` 请求拦截器上的**整串幂等规范化**：
 *    已经编码过的 `%XX` 不二次编码，只把不该出现在路径段里的字符补编码掉，
 *    中间的 `//`（= 有个 id 是空串）当场报错。一处收干净比改 112 行现实，
 *    这也是台账给的建议收口位置。
 */

/** 后端签发 id 的形状（uuid / 数字 / slug / 带冒号的时间戳键）：命中即原样透出。 */
const SAFE_SEGMENT = /^[A-Za-z0-9._~-]+$/;

/**
 * RFC 3986 `pchar`（去掉 `/`，因为 `/` 是段的分隔符）。
 * `%` 单独放行，再靠 `repairEscapes` 判断它是不是一个合法的 `%XX`——这样对已编码的段是幂等的。
 */
const FOREIGN_SEGMENT_CHARS = /[^A-Za-z0-9\-._~!$&'()*+,;=:@%]/g;

const ALREADY_ENCODED = /%[0-9A-Fa-f]{2}/;

/** 把一个段里不该出现的字符补编码，已编码的 `%XX` 原样保留（幂等）。 */
function repairSegment(segment: string): string {
  const withEscapesRepaired = segment.replace(/%(?![0-9A-Fa-f]{2})/g, '%25');
  return withEscapesRepaired.replace(FOREIGN_SEGMENT_CHARS, (ch) => encodeURIComponent(ch));
}

/**
 * 路径段编码器：`api.get(`/mpc/keys/${p(keyId)}`)`。
 *
 * 空值/空串抛错是刻意的：`/keys/` 这种段被静默拼出来，命中的是另一条路由（或直接 422），
 * 用户看到的是「资源不存在」，而真因是前端手里根本没有那个 id。
 */
export function p(segment: string | number | null | undefined): string {
  if (segment === null || segment === undefined) {
    throw new Error('路径段缺失：请求里的 id 是 null/undefined，不发这个请求');
  }
  const raw = String(segment);
  if (raw === '') {
    throw new Error('路径段为空：请求里的 id 是空串，不发这个请求');
  }
  if (SAFE_SEGMENT.test(raw)) return raw;
  return encodeURIComponent(raw);
}

/**
 * 请求 URL 的幂等规范化：只动路径部分，query / hash 原样保留（查询参数一律走 axios
 * `params` 那条既有编码通道，不在这里发明第二套）。
 *
 * 抛错只有一种情形：路径中间出现空段（`/a//b`）——那说明某个 id 到手是空的，
 * 与其让网关按斜杠折叠去命中别的路由，不如在这里报清楚。尾斜杠是合法路由形状
 * （`/data-resources/`），不算空段。
 */
export function normalizeRequestPath(url: string): string {
  if (!url) return url;
  const hashAt = url.indexOf('#');
  const withoutHash = hashAt >= 0 ? url.slice(0, hashAt) : url;
  const hash = hashAt >= 0 ? url.slice(hashAt) : '';
  const queryAt = withoutHash.indexOf('?');
  const head = queryAt >= 0 ? withoutHash.slice(0, queryAt) : withoutHash;
  const query = queryAt >= 0 ? withoutHash.slice(queryAt) : '';

  const absolute = /^(https?:\/\/[^/]+)(\/.*)$/i.exec(head);
  const origin = absolute ? absolute[1] : '';
  const path = absolute ? absolute[2] : head;
  if (!path.startsWith('/')) return `${origin}${path}${query}${hash}`;

  const segments = path.split('/');
  // segments[0] 恒为空串（前导 /），末段允许为空（尾斜杠是合法形状）
  for (let i = 1; i < segments.length; i += 1) {
    if (segments[i] === '' && i !== segments.length - 1) {
      throw new Error(`请求路径里出现空段（${path}）：有一个路径参数是空串，不发这个请求`);
    }
    segments[i] = repairSegment(segments[i]);
  }
  return `${origin}${segments.join('/')}${query}${hash}`;
}

/** 给测试与排查用：某个段是否需要编码（不抛错）。 */
export function isSafeSegment(segment: string): boolean {
  return SAFE_SEGMENT.test(segment) && !ALREADY_ENCODED.test(segment);
}
