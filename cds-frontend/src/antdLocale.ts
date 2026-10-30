import type { Locale } from 'antd/es/locale';
import zhCNModule from 'antd/locale/zh_CN';

/** 最多脱几层壳。Vite 8 + rolldown 目前最多见到一层 `default`；留到 4 层是防打包器互操作再变。 */
const MAX_UNWRAP_DEPTH = 4;

/**
 * `antd/locale/zh_CN` 是 CJS 壳（`module.exports = require('../lib/locale/zh_CN')`），
 * 打包器互操作后 default 拿到的可能是 `{ default: { locale, Pagination, … } }` 外层壳。
 * 把外壳直接交给 ConfigProvider 不会报错，只会让全站 antd 内建文案静默回落成英文
 * （空表 "No data"、分页 "10 / page / Go to"）。所以脱壳必须在装配点显式做掉。
 *
 * A269：旧实现只脱一层。现在按「谁带 `locale` 字段谁就是真 locale」逐层往下剥，
 * `{default:{default:{…}}}` 这种多层壳也认；剥不动就返回原样，交给下面的启动自检。
 */
export function unwrapLocale(mod: unknown): Locale {
  let current: unknown = mod;
  for (let depth = 0; depth < MAX_UNWRAP_DEPTH; depth += 1) {
    if (current && typeof current === 'object' && 'locale' in (current as object)) {
      return current as Locale;
    }
    const next = (current as { default?: unknown } | null)?.default;
    if (next == null || next === current) break;
    current = next;
  }
  return current as Locale;
}

/**
 * A269 的启动自检：装配出来的东西必须真是一份 locale。
 *
 * 判据只有一条硬事实——antd 认 locale 的方式就是那个 `locale` 字符串字段。缺了它，
 * ConfigProvider 不报错，只把全站内建文案静默换成英文（空表 No data、分页 10 / page），
 * 而这正是这层脱壳存在的理由。所以这里当场抛：装配错就在启动时炸响，
 * 而不是让一屏英文文案冒充「中文界面已就绪」。
 */
export function assertUsableLocale(locale: Locale, source: string): Locale {
  const tag = (locale as { locale?: unknown } | null)?.locale;
  if (typeof tag !== 'string' || tag.trim() === '') {
    throw new TypeError(
      `antd locale 装配失败（${source}）：拿到的对象没有可用的 \`locale\` 字段，`
      + '继续装配会让全站 antd 内建文案静默回落成英文（空表 No data、分页 10 / page）。'
      + '这说明打包器互操作的性状变了——去调 unwrapLocale 的脱壳层数，不要放过这条自检。',
    );
  }
  return locale;
}

const baseLocale: Locale = assertUsableLocale(unwrapLocale(zhCNModule), 'antd/locale/zh_CN');

// 原型图 4-1 分页写的是「前往 __ 页」，antd zh_CN 自带的是「跳至」——纯文案覆盖，不动其它字段。
export const zhCN: Locale = {
  ...baseLocale,
  Pagination: { ...baseLocale.Pagination, jump_to: '前往' },
};
