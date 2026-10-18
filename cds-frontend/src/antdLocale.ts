import type { Locale } from 'antd/es/locale';
import zhCNModule from 'antd/locale/zh_CN';

/**
 * `antd/locale/zh_CN` 是 CJS 壳（`module.exports = require('../lib/locale/zh_CN')`），
 * 打包器互操作后 default 拿到的可能是 `{ default: { locale, Pagination, … } }` 外层壳。
 * 把外壳直接交给 ConfigProvider 不会报错，只会让全站 antd 内建文案静默回落成英文
 * （空表 "No data"、分页 "10 / page / Go to"）。所以脱壳必须在装配点显式做掉。
 */
export function unwrapLocale(mod: unknown): Locale {
  const inner = (mod as { default?: unknown } | null)?.default;
  return ((inner && typeof inner === 'object' && 'locale' in (inner as object)) ? inner : mod) as Locale;
}

const baseLocale: Locale = unwrapLocale(zhCNModule);

// 原型图 4-1 分页写的是「前往 __ 页」，antd zh_CN 自带的是「跳至」——纯文案覆盖，不动其它字段。
export const zhCN: Locale = {
  ...baseLocale,
  Pagination: { ...baseLocale.Pagination, jump_to: '前往' },
};
