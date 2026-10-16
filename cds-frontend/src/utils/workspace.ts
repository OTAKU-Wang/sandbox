import { UserRole } from '../types/enums';
import { normalizeUserRole, type RoleLike } from './roles';

export type Workspace = 'developer' | 'admin';

export const WORKSPACE_STORAGE_KEY = 'cds.workspace';

export const ADMIN_WORKSPACE_ROLES: UserRole[] = [UserRole.OPERATOR, UserRole.REGULATOR, UserRole.ADMIN];

export const DEVELOPER_MENU_KEYS: string[] = [
  '/',
  '/data-products',
  '/data-products/create',
  '/data-resources',
  '/catalog',
  '/contracts',
  '/contracts/create',
  '/dev-sandbox',
  '/notebooks',
  '/workloads',
  '/compute',
  '/deliveries',
  '/flow/tasks',
  '/flow/guide',
  '/flow/quota',
  '/audit',
];

export function canAccessAdminWorkspace(role?: RoleLike): boolean {
  const normalized = normalizeUserRole(role);
  return normalized != null && ADMIN_WORKSPACE_ROLES.includes(normalized);
}

export function defaultWorkspace(role?: RoleLike): Workspace {
  return canAccessAdminWorkspace(role) ? 'admin' : 'developer';
}

type MenuKey = string | number | bigint;
type KeyedNode = { key?: MenuKey; children?: KeyedNode[] };

export function resolveWorkspace(role: RoleLike, stored: Workspace | null): Workspace {
  if (stored === 'admin' && !canAccessAdminWorkspace(role)) return defaultWorkspace(role);
  return stored ?? defaultWorkspace(role);
}

export function filterMenuByWorkspace<T>(
  items: readonly (T | null | undefined)[],
  workspace: Workspace,
): T[] {
  const nodes = items.filter((item): item is T => item != null);
  if (workspace === 'admin') return nodes;

  const prune = (item: unknown): T | null => {
    if (item == null) return null;
    const node = item as KeyedNode;
    if (DEVELOPER_MENU_KEYS.includes(String(node.key ?? ''))) return item as T;
    const children = (node.children ?? []).map(prune).filter((child): child is T => child != null);
    if (children.length === 0) return null;
    return { ...node, children } as unknown as T;
  };

  return nodes
    .map((item) => prune(item))
    .filter((entry): entry is T => entry != null);
}
