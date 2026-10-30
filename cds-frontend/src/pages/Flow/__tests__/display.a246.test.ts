/**
 * A246（展示层拼接闸）：`openApiPathForSlug(slug)` 不校验、不编码就把 slug 注进 URL。
 * 台账 ocr-verified-findings.md §A246：`pages/Flow/display.ts:227-229`
 * `/open/v1/${slug}` —— slug 含 `/`、`#`、`?`、`..` 时会改变**实际命中的路径**
 * （DeliveryCenter 把这里的输出渲染成可复制的调用示例与 apiUrl）。
 * 前端拼路径不是安全边界，但也不该把用户输入原样注进 URL；服务层
 * `services/flow.ts` 的 `openApiCallPathForSlug` + `isValidApiSlug` 已经是这套规则的唯一口径，
 * 本闸要求两处输出一致（台账的修法就是「两处共用一个函数」）。
 */
import { describe, expect, it } from 'vitest';
import * as display from '../display';
import { openApiCallPathForSlug } from '../../../services/flow';

const BASE = 'http://cds.internal';
const PREFIX = '/open/v1/';

/** 拼完之后浏览器/curl 真实命中的路径（`..`、`?`、`#` 会被解析掉，所以只能这样看）。 */
function resolvedPath(slug: string): { pathname: string; search: string; hash: string } {
  const url = new URL(display.openApiPathForSlug(slug), BASE);
  return { pathname: url.pathname, search: url.search, hash: url.hash };
}

const placeholder = (): unknown =>
  (display as unknown as Record<string, unknown>).OPEN_API_PATH_PLACEHOLDER;

describe('A246 展示用外部调用路径不得被 slug 改写', () => {
  it('合法 slug 与原口径一致（既有断言不放宽）', () => {
    expect(display.openApiPathForSlug('churn-score')).toBe('/open/v1/churn-score');
  });

  it('与试调用路径同一口径：合法 slug 两处输出必须逐字相等', () => {
    for (const slug of ['churn-score', 'a_b', 'qwen-answer', 'x1']) {
      expect(display.openApiPathForSlug(slug)).toBe(openApiCallPathForSlug(slug));
    }
  });

  it('slug 里的 `/` 与 `..` 不得跳出 /open/v1/ 前缀', () => {
    for (const slug of ['../../admin/secrets', 'a/b', '/absolute']) {
      const { pathname } = resolvedPath(slug);
      expect(pathname.startsWith(PREFIX), `slug「${slug}」命中了 ${pathname}`).toBe(true);
      const tail = pathname.slice(PREFIX.length);
      expect(tail.includes('/'), `slug「${slug}」多出一段路径：${pathname}`).toBe(false);
      expect(tail.startsWith('..'), `slug「${slug}」可被解析回退：${pathname}`).toBe(false);
    }
  });

  it('slug 里的 `?`/`#` 不得造出查询串或片段', () => {
    for (const slug of ['x?debug=1', 'x#frag', 'a&b=c']) {
      const { search, hash } = resolvedPath(slug);
      expect(search, `slug「${slug}」带出了查询串`).toBe('');
      expect(hash, `slug「${slug}」带出了片段`).toBe('');
    }
  });

  it('不合法的 slug（含空、大写、空格、超长、点号）显示占位符，不显示一个假的可调用路径', () => {
    for (const slug of ['', 'AB-Upper', 'has space', 'x'.repeat(80), 'a.b']) {
      expect(display.openApiPathForSlug(slug), `slug「${slug}」不该拼成可调用路径`).toBe(placeholder());
    }
  });

  it('占位符是字面 {slug}，不含用户输入', () => {
    expect(placeholder()).toBe('/open/v1/{slug}');
  });

  it('合法 slug 不需要转义；编码由共用的服务层函数负责', () => {
    expect(display.openApiPathForSlug('churn-score')).not.toContain('%');
    expect(openApiCallPathForSlug('a b')).toBe('/open/v1/a%20b');
  });
});
