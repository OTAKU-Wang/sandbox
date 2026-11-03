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
  // Task 5 场景中心：开发者工台「我的开发」的首项，不加进白名单会被 filterMenuByWorkspace 剔掉。
  '/flow/scenarios',
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
 * 登录后的落地路径（顶栏品牌、面包屑「首页」与工台切换共用）。
 *
 * A295（2026-10-30）：旧实现给开发者工台返回 '/home'，而 App.tsx:113 早已把 `/home`
 * 改成 `<Navigate to="/" replace/>`（SS-18 把开发者首页提到 index 路由）。于是开发者点
 * 「首页」或切工台都会先落进一条**只做跳转的兼容路由**再被 replace 回 '/'：地址栏闪一下、
 * history 多一跳，页面还是同一个 WorkspaceHome。落地页因此钉在真实存在的 index 路由上。
 * 工台分派不在这里做，由 index 路由的 WorkspaceHome 按 resolveWorkspace(role, stored) 分派
 * （开发者 → FlowDevHome，其余 → Dashboard），所以落点与角色无关，本函数不再需要入参。
 * '/home' 本身保留为重定向（旧收藏/旧文档不 404），isWorkspaceHome 与 routeCrumb 仍认它，
 * 那是「跳转途中」的口径，不是落点。
 */
export function homeLandingPath(): string {
  return '/';
}

type MenuKey = string | number | bigint;
type KeyedNode = { key?: MenuKey; type?: string; children?: KeyedNode[] };

export function resolveWorkspace(role: RoleLike, stored: Workspace | null): Workspace {
  // A296（M-195）：`stored` 是 zustand persist 从 localStorage 里读回来的 JSON，
  // 类型标注管不住它——手改成一个非法值（"Admin"、123、{…}）时旧实现会原样当工台用，
  // 于是 resolveWorkspace 返回一个既不是 'admin' 也不是 'developer' 的"工台"，
  // 菜单裁剪与首页分派各自落到自己的 else 分支上。只认这两个字面量，其余按没存过处理。
  const known: Workspace | null = stored === 'admin' || stored === 'developer' ? stored : null;
  if (known === 'admin' && !canAccessAdminWorkspace(role)) return defaultWorkspace(role);
  return known ?? defaultWorkspace(role);
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
    // A296（M-196）：子项只认「没有 children」和「children 是数组」两种形态。
    // 历史数据里 children 是个非数组真值（{} / 字符串）时，旧判定 `!Array.isArray(children)`
    // 会把它当**叶子**原样放行给开发角色；它其实是个结构坏了的分组，deny-by-default 该剔。
    const childless = node.children == null;
    const hasChildren = Array.isArray(node.children);
    // 分隔线这类**无子项**结构节点不带路由 key，原样保留（路由白名单管不到它）；
    // CR N-2：带 children 的 keyless 子菜单不得直接放行——它是白名单，deny-by-default，
    // 将来漏写 key 的运营分组不能就这样暴露给开发角色。
    if (node.key == null && childless) return item as T;
    // A259（2026-10-30）：命中白名单**只授权这一层**，不再顺手把整棵子树放行。
    // 旧写法是 `if (白名单命中) return item`，直接连着 children 一起返回；今天不出事，
    // 全靠一条没人写下来的命名约定撑着——Sidebar 里带 children 的分组 key 全是不带斜杠的
    // token（data-products / contracts / sandbox / dev / results / output-control / audit /
    // monitoring / k8s-ops / identity / sec），而这份白名单全是带前导斜杠的路由，
    // 于是没有任何一项能当父节点（`/audit` 与分组 `audit` 就差一个斜杠）。
    // 下一个人把分组 key 写成 `/data-products`，那一整块运营子菜单就静默进开发菜单了
    // （泄露的是菜单可见性，路由层还有 RoleRoute 兜着，所以是 latent）。
    // 现在命中与否只决定这一层去留，子层一律递归各自主张——顺带把那条约定从"靠人记"
    // 变成由代码保证（静态针见 workspace.a259.test.ts）。
    const whitelisted = DEVELOPER_MENU_KEYS.includes(String(node.key ?? ''));
    if (!hasChildren) return whitelisted ? (item as T) : null;
    const children = (node.children as KeyedNode[]).map(prune).filter((child): child is T => child != null);
    // 一个子项都没活下来的分组不渲染（空子菜单点了没反应，比不出现更糟）。
    // 注意这也意味着「命中白名单的分组若子项全不可见 ⇒ 整块消失」，与旧的整树放行相比是收紧。
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
      // A296（M-317）：分组没有 key 时旧写法 `String(item.key ?? '')` 会把子项的 groupKey
      // 坐实成**空串**，调用方普遍的 `if (groupKey)` 判据当场失效（分组高亮没了，还不报错）。
      // 没有 key 就没有分组，沿用上一层的 groupKey（可能仍是 undefined）才是诚实的值。
      collectLeafRoutes(item.children, item.key != null ? String(item.key) : groupKey, into);
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
