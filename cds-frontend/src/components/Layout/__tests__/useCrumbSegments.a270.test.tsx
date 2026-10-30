/**
 * A270：`useCrumbSegments` 的两条 latent 一起钉。
 *
 * ① 段名往返：旧实现 `segs.join('|')` → `key.split('|')`，段名里只要有一个 `|`，
 *    面包屑就凭空多一段（今天真值表里没有带 `|` 的段名，所以是潜伏）。
 * ② 卸载清空写在**全局共享**的栈上、且不认归属：先挂载的那位在切换视图时卸载，
 *    会把后挂载那位刚写进去的栈一起清掉——面包屑少一段，而且看不出是谁清的。
 * 两条都不是现行缺陷，正因为如此才更要钉：它们只靠「现在只有一个调用点、段名都干净」撑着。
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { render } from '@testing-library/react';
import { useCrumbSegments } from '../useCrumbSegments';
import { useCrumbStackStore } from '../../../stores/crumbStackStore';

function Drill({ segs }: { segs: string[] }) {
  useCrumbSegments(segs);
  return null;
}

const stack = () => useCrumbStackStore.getState().stack;

beforeEach(() => {
  useCrumbStackStore.setState({ stack: [] });
});

describe('A270①：段名往返不靠 join/split', () => {
  it('段名里带竖线时原样进栈，不拆成两段', () => {
    render(<Drill segs={['OpenAPI导出', '接口|监控']} />);
    expect(stack()).toEqual(['OpenAPI导出', '接口|监控']);
  });

  it('空段数组就是空栈（不给面包屑留空段）', () => {
    render(<Drill segs={[]} />);
    expect(stack()).toEqual([]);
  });
});

describe('A270②：卸载清空只认自己那份', () => {
  it('先挂载的钻取组件卸载，不得清掉后挂载者刚写的栈', () => {
    const first = render(<Drill segs={['OpenAPI导出', '接口列表']} />);
    const second = render(<Drill segs={['我的结果集', '数据集交付']} />);
    // 后写者赢（当前视图的段就是它写的）。
    expect(stack()).toEqual(['我的结果集', '数据集交付']);

    first.unmount();
    // 旧实现在这里无条件 setStack([]) —— 第二个消费者还在挂载，面包屑却少了两段。
    expect(stack()).toEqual(['我的结果集', '数据集交付']);

    second.unmount();
    expect(stack()).toEqual([]);
  });

  it('自己的段变化仍会跟着换（同归属内的正常更新，不被归属判定挡死）', () => {
    const { rerender } = render(<Drill segs={['OpenAPI导出', '接口列表']} />);
    expect(stack()).toEqual(['OpenAPI导出', '接口列表']);
    rerender(<Drill segs={['OpenAPI导出', '新建API']} />);
    expect(stack()).toEqual(['OpenAPI导出', '新建API']);
  });
});
