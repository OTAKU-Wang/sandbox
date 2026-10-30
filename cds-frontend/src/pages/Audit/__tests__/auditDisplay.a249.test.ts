// A249：审计详情/列表对 record.action 直接 .endsWith —— 详情接口或历史数据缺 action 字段时
// 直接 TypeError；全站零 ErrorBoundary（A244）⇒ 一个空字段就把整个应用白屏。
// 本 spec 锁「缺 action 的行必须渲染成中性 default，而不是抛错」。
import { describe, expect, it } from 'vitest';
import { auditActionColor, auditActionCodeText, auditActionLabel } from '../auditDisplay';

type ColorInput = Parameters<typeof auditActionColor>[0];

const rec = (action: unknown): ColorInput => ({ action } as unknown as ColorInput);

describe('A249 auditActionColor 对缺失/异常 action 不抛错', () => {
  it('action 缺失（undefined）返回 default，不抛 TypeError', () => {
    expect(() => auditActionColor(rec(undefined))).not.toThrow();
    expect(auditActionColor(rec(undefined))).toBe('default');
  });

  it('action 为空串 / null / 非字符串（历史脏数据）同样不抛错', () => {
    for (const value of ['', null, 0, 42, {}, []]) {
      expect(() => auditActionColor(rec(value))).not.toThrow();
      expect(auditActionColor(rec(value))).toBe('default');
    }
  });

  it('回归锁：失败/拒绝后缀仍判红，页签命中优先，普通动作 default', () => {
    expect(auditActionColor(rec('user.login_failed'))).toBe('red');
    expect(auditActionColor(rec('sandbox.output.blocked'))).toBe('red');
    expect(auditActionColor(rec('sandbox.code_scan_rejected'))).toBe('red');
    expect(auditActionColor(rec('user.login'))).toBe('default');
    expect(auditActionColor({ action: 'anything', event_type: 'export' } as ColorInput)).toBe('green');
  });

  it('回归锁：缺失 action 时标签/详情文本不抛错（渲染层走空态而不是崩）', () => {
    expect(() => auditActionLabel(rec(undefined))).not.toThrow();
    expect(() => auditActionCodeText(rec(undefined))).not.toThrow();
  });
});
