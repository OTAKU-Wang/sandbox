// auditDisplay 是 4-1/4-2 唯一的展示层真值源：动作中文词表 + 时间口径。
// 这里只锁两件事：① 词表覆盖住 4-1「操作类型」下拉的全部动作（差距表 4-1.3：
// 缺键的 5 个动作会渲染成「code (code)」原文重复）；② 时间格式是原型十行同形的
// `YYYY-MM-DD HH:mm:ss`（差距表 4-1.9 / 4-2.1，不是 toLocaleString 的 `2026/8/12`）。
import { describe, it, expect } from 'vitest';
import {
  AUDIT_TABS, auditActionColor, auditActionLabel, auditActionCodeText, auditTabOf, auditTimeText,
} from '../auditDisplay';

/** 与 AuditLog.tsx 的 ACTION_OPTIONS 同集：全部是 app/ 里真写进 audit_logs.action 的代码。 */
const DROPDOWN_ACTIONS = [
  'notebook.dev_session_created', 'compute.run.requested', 'sandbox.execute',
  'delivery.dataset.downloaded', 'openapi.call', 'user.login', 'user.login_failed',
  'contract.sign', 'data_product.publish', 'retention.purge',
];

describe('auditDisplay（4-1 词表 / 时间口径）', () => {
  it.each(DROPDOWN_ACTIONS)('下拉动作 %s 有中文展示名，不回落到原文', (action) => {
    expect(auditActionLabel({ action })).not.toBe(action);
    expect(auditActionLabel({ action })).not.toContain(action);
  });

  it('词表外的动作原样显示动作代码，不给它编一个中文名（禁伪造）', () => {
    expect(auditActionLabel({ action: 'vendor.mystery_action' })).toBe('vendor.mystery_action');
    expect(auditActionCodeText({ action: 'vendor.mystery_action' })).toBe('vendor.mystery_action');
  });

  it('页签命中的行取页签文案，页签 key 与后端 AUDIT_EVENT_TYPES 的四个键一致', () => {
    expect(AUDIT_TABS.map((tab) => tab.key)).toEqual(['dev_sandbox', 'production', 'export', 'call']);
    // 页签内动作带 event_type 时优先页签文案；不带 event_type 时才用上面的词表
    expect(auditActionLabel({ action: 'delivery.dataset.downloaded', event_type: 'export' }))
      .toBe('结果集导出');
    expect(auditTabOf({ event_type: 'call' })?.label).toBe('数据调用');
    expect(auditTabOf({ event_type: 'nope' })).toBeNull();
    expect(auditActionColor({ action: 'user.login_failed' })).toBe('red');
  });

  it('时间统一 YYYY-MM-DD HH:mm:ss：连字符、补零、秒级；无效值交回调用方走「未记录」', () => {
    expect(auditTimeText('2026-08-12T09:58:40')).toBe('2026-08-12 09:58:40');
    expect(auditTimeText('2026-01-02T03:04:05')).toBe('2026-01-02 03:04:05');
    // 带 Z 的值按本机时区渲染（列表/标题/基础信息三处同源，所以只锁形态不锁数字）
    expect(auditTimeText('2026-08-12T09:58:40Z')).toMatch(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/);
    expect(auditTimeText(null)).toBeNull();
    expect(auditTimeText(undefined)).toBeNull();
    expect(auditTimeText('')).toBeNull();
    expect(auditTimeText('库里没这个字段')).toBeNull();
  });
});
