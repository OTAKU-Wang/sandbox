import { describe, expect, it } from 'vitest';
import {
  canAccessAdminWorkspace,
  defaultWorkspace,
  filterMenuByWorkspace,
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
