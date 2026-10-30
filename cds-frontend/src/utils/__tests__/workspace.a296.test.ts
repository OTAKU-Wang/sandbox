/**
 * A296 的 `workspace.ts` 半边：工台枚举校验（M-195）+ 菜单树 keyless 三连（M-196/197/317）。
 *
 * 这三处的共同点是「坏输入不报错，只是安静地走错分支」：
 * - M-195：`stored` 来自 zustand persist 读回的 localStorage JSON，类型标注管不住它；
 * - M-196：历史数据里 `children` 是个非数组真值时被当叶子放行给开发角色（白名单该 deny-by-default）；
 * - M-317：分组没有 key 时旧写法把 groupKey 坐实成空串，调用方普遍的 `if (groupKey)` 判不到。
 * 当前菜单常量都带 key ⇒ 台账定的是潜伏，所以这里钉的是**判定本身**，不是某个现网现象。
 */
import { describe, it, expect } from 'vitest';
import { defaultWorkspace, filterMenuByWorkspace, matchMenuSelection, resolveWorkspace } from '../workspace';
import { UserRole } from '../../types/enums';

type Node = { key?: string; children?: unknown; type?: string };

describe('A296 M-195：stored 只认两个字面量', () => {
  it('合法值 + 有权角色才落在 admin 工台', () => {
    expect(resolveWorkspace(UserRole.OPERATOR, 'admin')).toBe('admin');
    expect(resolveWorkspace(UserRole.BUYER, 'developer')).toBe('developer');
  });

  it('越权的 admin 工台照旧回落到角色默认（这条原本就有）', () => {
    expect(resolveWorkspace(UserRole.BUYER, 'admin')).toBe(defaultWorkspace(UserRole.BUYER));
  });

  it('手改出来的非法值按「没存过」处理，而不是当成第三种工台', () => {
    const junk: unknown[] = ['Admin', 'ADMIN', '', 'developer ', 123, true, {}, [], ['admin']];
    for (const stored of junk) {
      const got = resolveWorkspace(UserRole.OPERATOR, stored as never);
      // 只可能是两个合法工台之一，且非法值不得把运营角色推出 admin。
      expect(['admin', 'developer']).toContain(got);
      expect(got).toBe(defaultWorkspace(UserRole.OPERATOR));
    }
  });
});

describe('A296 M-196：children 形态坏了的分组不当叶子放行', () => {
  it('children 是非数组真值 ⇒ 结构坏了的分组，开发角色下剔除', () => {
    const broken: Node[] = [
      { key: '/ops-only-thing', children: {} },
      { key: '/ops-only-thing2', children: 'yes' },
      { key: '/audit' },
    ];
    expect(filterMenuByWorkspace(broken, 'developer').map((n) => (n as Node).key)).toEqual(['/audit']);
  });

  it('无 key 无 children 的结构节点（分隔线）仍原样保留', () => {
    const items: Node[] = [{ type: 'divider' }, { key: '/audit' }];
    const out = filterMenuByWorkspace(items, 'developer');
    expect(out).toHaveLength(2);
    expect((out[0] as Node).type).toBe('divider');
  });

  it('无 key 分组的白名单子项照样被剪，子项全空则整个分组消失（CR N-2 口径不变）', () => {
    const leaked: Node[] = [{ children: [{ key: '/users' }] }];
    expect(filterMenuByWorkspace(leaked, 'developer')).toEqual([]);
  });
});

describe('A296 M-317：分组 key 缺失不再坐实成空串', () => {
  it('带 key 的分组照常回报 groupKey', () => {
    const items: Node[] = [{ key: 'grp', children: [{ key: '/audit' }] }];
    expect(matchMenuSelection(items, '/audit')).toEqual({ selectedKey: '/audit', groupKey: 'grp' });
  });

  it('前缀命中时也一样（子路由归属分组）', () => {
    const items: Node[] = [{ key: 'grp', children: [{ key: '/audit' }] }];
    expect(matchMenuSelection(items, '/audit/leaf')).toEqual({ selectedKey: '/audit', groupKey: 'grp' });
  });

  it('分组没有 key 时不会退回空串：空串在调用方等于「没有分组」，那是丢高亮的老 bug', () => {
    const items: Node[] = [{ children: [{ key: '/audit' }] }];
    const got = matchMenuSelection(items, '/audit');
    expect(got.selectedKey).toBe('/audit');
    // 与顶层叶子同一条口径：没有分组可归属时回报它自己，而不是 ''。
    expect(got.groupKey).toBe('/audit');
    expect(got.groupKey).not.toBe('');
    // 顶层叶子的既有行为不变（回归保护）。
    expect(matchMenuSelection([{ key: '/audit' }], '/audit')).toEqual({ selectedKey: '/audit', groupKey: '/audit' });
  });
});
