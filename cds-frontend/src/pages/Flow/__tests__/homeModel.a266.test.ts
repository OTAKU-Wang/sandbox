// A266：首页两处缺数处理——
// 1) taskFilterTabs 对 overview.task_filters 三连无可选链解引用：后端缺键/改名即 TypeError，
//    叠全站零 ErrorBoundary（A244）⇒ 首页白屏（tsc 拦不住：前端自己把它声明成了必填，A261 家族）；
// 2) opsLogSubtext 直接插值 ops.failures：后端不返该键就渲染出字面量「失败 undefined」，
//    违反本文件头注不变式 1「缺数一律渲染 UNKNOWN_TEXT，绝不折算成 0」。
import { describe, expect, it } from 'vitest';
import { opsLogSubtext, taskFilterTabs } from '../homeModel';
import { UNKNOWN_TEXT } from '../../../services/flow';

const asOverview = (v: unknown) => v as Parameters<typeof taskFilterTabs>[0];
const asOps = (v: unknown) => v as Parameters<typeof opsLogSubtext>[0];

describe('A266-1 taskFilterTabs：task_filters 缺键不得崩、缺的计数不折算 0', () => {
  it('task_filters 整体缺失：返回三枚页签且计数全部「未记录」，不抛 TypeError', () => {
    let tabs: ReturnType<typeof taskFilterTabs> | undefined;
    expect(() => {
      tabs = taskFilterTabs(asOverview({}));
    }).not.toThrow();
    expect(tabs!.map((tab) => tab.count)).toEqual([UNKNOWN_TEXT, UNKNOWN_TEXT, UNKNOWN_TEXT]);
    expect(tabs!.map((tab) => tab.key)).toEqual(['all', 'active', 'ended']);
  });

  it('三键缺一：只缺的走未记录，真值原样；绝不把缺的当 0（0 是「确实没有」）', () => {
    const tabs = taskFilterTabs(asOverview({ task_filters: { all: 5 } }));
    expect(tabs.map((tab) => tab.count)).toEqual([5, UNKNOWN_TEXT, UNKNOWN_TEXT]);
  });

  it('回归锁：三键齐全时逐字契约不变（含 0 就是 0）', () => {
    const tabs = taskFilterTabs(asOverview({ task_filters: { all: 11, active: 9, ended: 2 } }));
    expect(tabs).toEqual([
      { key: 'all', label: '全部', count: 11 },
      { key: 'active', label: '进行中', count: 9 },
      { key: 'ended', label: '已结束', count: 2 },
    ]);
    expect(taskFilterTabs(asOverview({ task_filters: { all: 0, active: 0, ended: 0 } }))[0].count).toBe(0);
  });

  it('overview 整体没给也不崩（调用点 DevHome.tsx:537/:561 渲染里直调，无包裹）', () => {
    expect(() => taskFilterTabs(asOverview(undefined))).not.toThrow();
  });
});

describe('A266-2 opsLogSubtext：failures 缺失渲染「失败 未记录」而不是「失败 undefined」', () => {
  it('failures 缺失 / null → 未记录口径', () => {
    expect(opsLogSubtext(asOps({ visibility: 'self' }))).toBe(`失败 ${UNKNOWN_TEXT}`);
    expect(opsLogSubtext(asOps({ failures: null, visibility: 'self' }))).toBe(`失败 ${UNKNOWN_TEXT}`);
  });

  it('整对象没给也不抛（DevHome.tsx:513 直调 overview.ops_log_today）', () => {
    expect(() => opsLogSubtext(asOps(undefined))).not.toThrow();
    expect(opsLogSubtext(asOps(undefined))).toBe(`失败 ${UNKNOWN_TEXT}`);
  });

  it('回归锁：真值 0 是 0、3 是 3，不吞进未记录', () => {
    expect(opsLogSubtext(asOps({ failures: 0, visibility: 'platform' }))).toBe('失败 0');
    expect(opsLogSubtext(asOps({ failures: 3, visibility: 'self' }))).toBe('失败 3');
  });
});
