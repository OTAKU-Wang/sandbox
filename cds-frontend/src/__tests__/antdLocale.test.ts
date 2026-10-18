import { describe, expect, it } from 'vitest';
import { unwrapLocale, zhCN } from '../antdLocale';

describe('antd zh_CN locale 装配', () => {
  it('已脱壳的 locale 原样返回', () => {
    const flat = { locale: 'zh-cn' } as never;
    expect(unwrapLocale(flat)).toBe(flat);
  });

  it('CJS 互操作产生的 { default: {...} } 外壳会被脱掉', () => {
    const inner = { locale: 'zh-cn', Pagination: { items_per_page: '条/页' } } as never;
    expect(unwrapLocale({ default: inner })).toBe(inner);
  });

  it('交给 ConfigProvider 的对象本身带 locale 字段，而不是只剩 default 键', () => {
    expect(zhCN.locale).toBeTruthy();
    expect((zhCN as { default?: unknown }).default).toBeUndefined();
  });

  it('分页内建文案是中文（对应原型「10条/页」「前往 N 页」）', () => {
    expect(String(zhCN.Pagination?.items_per_page)).toContain('条');
    expect(zhCN.Pagination?.jump_to).toBe('前往');
  });
});
