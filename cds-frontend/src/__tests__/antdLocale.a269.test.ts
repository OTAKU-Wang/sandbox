/**
 * A269：`antd/locale/zh_CN` 的解包层数与「解不出来就报出来」。
 *
 * 台账两条危害：① 解包只脱一层 `.default` 壳，打包器互操作再变一层就静默失效；
 * ② 交给 ConfigProvider 的对象如果没有 `locale` 字段，antd 不报错，只把全站内建文案
 *    悄悄换成英文（空表 No data、分页 10 / page）——页面自己的中文字面量还在，
 *    没人会发现组件内部的语言已经掉了。`assertUsableLocale` 就是把第二种变成启动期异常。
 */
import { describe, it, expect } from 'vitest';
import zhCNPkg from 'antd/locale/zh_CN';
import { assertUsableLocale, unwrapLocale, zhCN } from '../antdLocale';

const REAL = { locale: 'zh_CN', Pagination: {}, Empty: {} };

describe('A269：.default 壳可以脱多层', () => {
  it('一层壳（Vite 8 + rolldown 今天的形状）', () => {
    expect(unwrapLocale({ default: REAL })).toBe(REAL);
  });

  it('两层/三层壳（旧实现只脱一层，第二层起就会把壳当 locale 交出去）', () => {
    expect(unwrapLocale({ default: { default: REAL } })).toBe(REAL);
    expect(unwrapLocale({ default: { default: { default: REAL } } })).toBe(REAL);
  });

  it('本身就是 locale（没有壳）时原样返回，不无谓复制', () => {
    expect(unwrapLocale(REAL)).toBe(REAL);
  });

  it('脱到底还是没有 locale 字段：返回最里层，交给自检去炸，而不是返回一个假成功', () => {
    const shell = { default: { foo: 1 } };
    expect(unwrapLocale(shell)).toEqual({ foo: 1 });
    expect(() => assertUsableLocale(unwrapLocale(shell), 'test')).toThrow(TypeError);
  });

  it('壳超过层数上限时不死循环：脱不动就停手，剩下那条路是自检炸掉它', () => {
    let deep: unknown = REAL;
    for (let i = 0; i < 12; i += 1) deep = { default: deep };
    const out = unwrapLocale(deep);
    expect('locale' in (out as object)).toBe(false);
    expect(() => assertUsableLocale(out, 'test')).toThrow(TypeError);
  });
});

describe('A269：拿到假 locale 必须炸，而不是悄悄退回英文', () => {
  it('真正的 antd 包在启动路径上通过自检（这条红了就是全站文案要掉英文的信号）', () => {
    expect(() => assertUsableLocale(zhCNPkg as never, 'antd/locale/zh_CN')).not.toThrow();
    expect(typeof zhCN.locale).toBe('string');
    expect(zhCN.locale).toBe((zhCNPkg as { locale: string }).locale);
  });

  it('装配点仍然带着那处纯文案覆盖（原型 4-1「前往 __ 页」）', () => {
    expect(zhCN.Pagination?.jump_to).toBe('前往');
  });

  it('locale 字段缺失 / 空串 / 非字符串一律 TypeError', () => {
    for (const bad of [{}, { locale: '' }, { locale: 123 }, { locale: null }, null] as never[]) {
      expect(() => assertUsableLocale(bad, 'test')).toThrow(TypeError);
    }
  });

  it('错误文案点名来源（只说 invalid locale 等于让下一个人重新查一遍）', () => {
    expect(() => assertUsableLocale({ locale: '' } as never, 'antd/locale/zh_CN'))
      .toThrow(/antd\/locale\/zh_CN/);
  });
});
