import { describe, expect, it } from 'vitest';
import {
  canAccessAdminWorkspace,
  defaultWorkspace,
  filterMenuByWorkspace,
  homeLandingPath,
  isWorkspaceHome,
  matchMenuSelection,
  resolveGreetingName,
  resolveWorkspace,
} from '../workspace';
import { UserRole } from '../../types/enums';

const menu = [
  { key: '/' },
  { key: 'sandbox', children: [{ key: '/sandbox-sessions' }, { key: '/dev-sandbox' }, { key: '/capability-matrix' }] },
  { key: 'identity', children: [{ key: '/identity/keys' }] },
  { key: 'audit', children: [{ key: '/audit' }] },
];

describe('双模式工作台外壳', () => {
  it('数商/买方默认落在开发者工作台，且不具备管理员工台权限', () => {
    for (const role of [UserRole.DATA_PROVIDER, UserRole.BUYER]) {
      expect(canAccessAdminWorkspace(role)).toBe(false);
      expect(defaultWorkspace(role)).toBe('developer');
    }
  });

  it('运营方/监管方/管理员默认管理员工台且可切回开发者', () => {
    for (const role of [UserRole.OPERATOR, UserRole.REGULATOR, UserRole.ADMIN]) {
      expect(canAccessAdminWorkspace(role)).toBe(true);
      expect(defaultWorkspace(role)).toBe('admin');
      expect(resolveWorkspace(role, 'developer')).toBe('developer');
    }
  });

  it('业务角色即便残留 admin 偏好也回落到开发者工作台', () => {
    expect(resolveWorkspace(UserRole.BUYER, 'admin')).toBe('developer');
    expect(resolveWorkspace(UserRole.ADMIN, null)).toBe('admin');
    expect(resolveWorkspace(undefined, null)).toBe('developer');
  });

  it('开发者视图按叶子保留父分组，并剔除管理态分组', () => {
    const pruned = filterMenuByWorkspace(menu, 'developer');
    expect(pruned.map((item) => item.key)).toEqual(['/', 'sandbox', 'audit']);
    // SS-18: 会话列表/能力矩阵不属开发者闭环, 但同一分组里的开发沙箱按叶子保留
    expect(pruned[1].children?.map((child) => child.key)).toEqual(['/dev-sandbox']);
  });

  it('管理员工台不裁剪菜单', () => {
    expect(filterMenuByWorkspace(menu, 'admin').map((item) => item.key)).toEqual([
      '/',
      'sandbox',
      'identity',
      'audit',
    ]);
  });

  it('未登录用户不会拿到管理员工台', () => {
    expect(filterMenuByWorkspace(menu, resolveWorkspace(null, null)).map((i) => i.key)).toEqual([
      '/',
      'sandbox',
      'audit',
    ]);
  });
});

describe('SS-18 顶栏与左栏的真值助手', () => {
  it('首页判定：原型 A-1 的 / 与旧的 /home 都不出面包屑', () => {
    expect(isWorkspaceHome('/')).toBe(true);
    expect(isWorkspaceHome('/home')).toBe(true);
    expect(isWorkspaceHome('/flow/tasks')).toBe(false);
  });

  it('问候名：组织优先 → 回落用户名 → 都没有就空串（顶栏按「用户」口径兜底）', () => {
    expect(resolveGreetingName({ username: 'dev', organization: '城市数据运营中心' })).toBe('城市数据运营中心');
    expect(resolveGreetingName({ username: 'dev', organization: null })).toBe('dev');
    expect(resolveGreetingName({ username: '  ', organization: '  ' })).toBe('');
    expect(resolveGreetingName(null)).toBe('');
  });

  it('登录后落点：开发者工台落 /home（开发者首页），管理员工台落 /', () => {
    expect(homeLandingPath(UserRole.DATA_PROVIDER, null)).toBe('/home');
    // 业务角色残留 admin 偏好 → resolveWorkspace 回落 developer，落点跟着回开发者首页
    expect(homeLandingPath(UserRole.DATA_PROVIDER, 'admin')).toBe('/home');
    expect(homeLandingPath(UserRole.OPERATOR, 'developer')).toBe('/home');
    expect(homeLandingPath(UserRole.OPERATOR, 'admin')).toBe('/');
    expect(homeLandingPath(undefined, null)).toBe('/home');
  });

  it('选中态：精确命中优先，子路由按最长前缀命中并回报所属分组', () => {
    const items = [
      { key: '/' },
      { key: 'dev', children: [{ key: '/flow/tasks' }, { key: '/notebooks' }] },
      { key: '/deliveries' },
    ];
    expect(matchMenuSelection(items, '/')).toEqual({ selectedKey: '/', groupKey: '/' });
    expect(matchMenuSelection(items, '/deliveries')).toEqual({
      selectedKey: '/deliveries',
      groupKey: '/deliveries',
    });
    // /flow/tasks/new 要让「我的开发」父项一起亮
    expect(matchMenuSelection(items, '/flow/tasks/new')).toEqual({ selectedKey: '/flow/tasks', groupKey: 'dev' });
    expect(matchMenuSelection(items, '/notebooks/123')).toEqual({ selectedKey: '/notebooks', groupKey: 'dev' });
    // 未命中（包括不被 '/' 这个前缀误命中）→ 什么都不选
    expect(matchMenuSelection(items, '/flow/quota')).toEqual({});
    expect(matchMenuSelection(items, '/unknown')).toEqual({});
  });

  it('分隔线是结构节点，不被开发者路由白名单剔掉', () => {
    type Node = { key?: string; type?: string; children?: Node[] };
    const devMenu: Node[] = [
      { key: '/' },
      { type: 'divider' },
      { key: 'identity', children: [{ key: '/identity/keys' }] },
    ];
    expect(filterMenuByWorkspace(devMenu, 'developer').map((node) => node.type ?? node.key)).toEqual(['/', 'divider']);
  });

  it('CR N-2：漏写 key 的带子项分组不得整块放行（白名单是 deny-by-default）', () => {
    type Node = { key?: string; children?: Node[] };
    // 将来新增的运营分组忘写 key：它的管理态子项必须照样被剪，只剩白名单叶子；
    // 子项全被剪光时整个分组消失，不能因为「没 key」就原样透出。
    const leaked: Node[] = [
      { children: [{ key: '/users' }, { key: '/api-keys' }] },
      { children: [{ key: '/audit' }] },
    ];
    const out = filterMenuByWorkspace(leaked, 'developer');
    expect(out).toEqual([{ children: [{ key: '/audit' }] }]);
  });

  it('开发者路由白名单收录了原型「安全管理」子菜单的两条路由', () => {
    type Node = { key?: string; children?: Node[] };
    const sec: Node[] = [{ key: 'sec', children: [{ key: '/audit' }, { key: '/compliance-disclosure' }] }];
    expect(filterMenuByWorkspace(sec, 'developer')[0].children?.map((child) => child.key)).toEqual([
      '/audit',
      '/compliance-disclosure',
    ]);
  });
});
