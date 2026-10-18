import { UserRole } from '../types/enums';
import { normalizeUserRole, type RoleLike } from './roles';

export type Workspace = 'developer' | 'admin';

export const WORKSPACE_STORAGE_KEY = 'cds.workspace';

export const ADMIN_WORKSPACE_ROLES: UserRole[] = [UserRole.OPERATOR, UserRole.REGULATOR, UserRole.ADMIN];

export const DEVELOPER_MENU_KEYS: string[] = [
  '/',
  // 原型「安全管理」子菜单里的合规披露（SS-18 对齐）：不加进这份白名单，
  // Sidebar 的 filterMenuByWorkspace 会把开发者菜单里的这一子项剔掉，菜单就成了点了没反应的死链。
  '/compliance-disclosure',
  // 旧开发者首页 /home 已改为重定向到 '/'（SS-18 原型对齐）：菜单项随之删除，白名单同步不再收录。
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
  '/deliveries/dataset',
  '/deliveries/push',
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

/**
 * 面包屑/页签「当前页」的判定：原型 A-1 的首页（'/' 与旧的 '/home'）标题画在内容卡内部，
 * 顶栏不出现面包屑与标题；其它路由维持面包屑 + 高亮。
 */
export function isWorkspaceHome(pathname: string): boolean {
  return pathname === '/' || pathname === '/home';
}

/**
 * 顶栏右侧的问候名：组织名优先，缺组织回落用户名（后端 User.organization 可为 null），
 * 两者都缺返回空串，由调用方决定口径文案（原型里那个具体机构名是部署方示例，绝不能写进代码）。
 */
export function resolveGreetingName(user?: { username?: string | null; organization?: string | null } | null): string {
  return user?.organization?.trim() || user?.username?.trim() || '';
}

/**
 * 登录后的落地路径（顶栏品牌与面包屑「首页」共用）：能进开发者工台的人落开发者首页，
 * 其余落管理员工台的运营概览。
 */
export function homeLandingPath(role: RoleLike, stored: Workspace | null): string {
  return resolveWorkspace(role, stored) === 'developer' ? '/home' : '/';
}

type MenuKey = string | number | bigint;
type KeyedNode = { key?: MenuKey; type?: string; children?: KeyedNode[] };

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
    // 分隔线这类**无子项**结构节点不带路由 key，原样保留（路由白名单管不到它）；
    // CR N-2：带 children 的 keyless 子菜单不得直接放行——它是白名单，deny-by-default，
    // 将来漏写 key 的运营分组不能就这样暴露给开发角色。
    if (node.key == null && !Array.isArray(node.children)) return item as T;
    if (DEVELOPER_MENU_KEYS.includes(String(node.key ?? ''))) return item as T;
    const children = (node.children ?? []).map(prune).filter((child): child is T => child != null);
    if (children.length === 0) return null;
    return { ...node, children } as unknown as T;
  };

  return nodes
    .map((item) => prune(item))
    .filter((entry): entry is T => entry != null);
}

/** 把 antd Menu items 归一成「叶子路由 → 所属分组 key」的查找表（父项 key 不是路由，跳过）。 */
function collectLeafRoutes(nodes: readonly unknown[], groupKey?: string, into = new Map<string, string>()): Map<string, string> {
  for (const node of nodes) {
    if (node == null) continue;
    const item = node as KeyedNode;
    if (item.children?.length) {
      // 父项自身也可能挂路由（antd 允许），两者都登记：精确命中优先父项，前缀命中优先分组。
      if (item.key != null) into.set(String(item.key), String(item.key));
      collectLeafRoutes(item.children, String(item.key ?? ''), into);
    } else if (item.key != null) {
      into.set(String(item.key), groupKey ?? String(item.key));
    }
  }
  return into;
}

/**
 * 左栏选中态：路径完全等于某路由就用它；否则取**最长前缀**命中，并回报它所属分组 key，
 * 让 /flow/tasks/new 这类子路由把「我的开发」父项也点亮（原型 A-2 的页签带就是父项高亮）。
 */
export function matchMenuSelection(
  items: readonly unknown[],
  pathname: string,
): { selectedKey?: string; groupKey?: string } {
  const routes = collectLeafRoutes(items);
  const exact = routes.get(pathname);
  if (exact != null) return { selectedKey: pathname, groupKey: exact };

  let best = '';
  let bestGroup: string | undefined;
  for (const [route, group] of routes) {
    if (!route.startsWith('/') || route === '/') continue;
    if (pathname !== route && !pathname.startsWith(`${route}/`)) continue;
    if (route.length > best.length) {
      best = route;
      bestGroup = group;
    }
  }
  return best ? { selectedKey: best, groupKey: bestGroup } : {};
}
