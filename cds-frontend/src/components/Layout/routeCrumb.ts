/**
 * 页面级面包屑的唯一真值表。
 *
 * 真值来源：`基础版数据沙箱原型/` PNG 的转录 `.cds-e2e/img-specs/group-{A,B,C}.md`
 * - 顶栏蓝条（A-1「顶栏」crop，两遍读数一致）只有品牌 + 人像问候，**没有面包屑**；
 *   面包屑一律在内容卡顶部（y 84–121 / 89–103 那条 ink 带）。
 * - A/B 组（我的开发系）PNG 上画的是一条「已打开页签」带（青绿块，2026-10-16 口径）；
 *   2026-10-17 用户定稿「平台全面使用面包屑导航 但是要考虑通用性」后改斜杠式，
 *   **2026-10-18 用户进一步裁定：页签带彻底删除**（`cds-page-tabs` / `pills` 样式与
 *   `crumbStackStore.visited` 同步消失）。开发系四屏 2-1/2-2/2-3/2-4 现在走斜杠面包屑。
 *   像素真值上的差异已在台账记下（strict-2-1「蓝药丸 bbox 321,84,99,37」、
 *   2-4-measured §1「两块实心蓝 h38」）：那条带在图上确实是页签而不是斜杠链，
 *   本处按用户裁定服从导航定稿，不假装图上画的就是斜杠。
 * - C 组 / 4-1 = **斜杠文字面包屑**：前段灰字、末段黑字（C-3 像素探针蓝=0，3-x 前两段实测灰），
 *   无填充块。
 * - 段名照图：一级入口只有 `首页 / 我的开发 / 我的结果集 / 安全管理`，所以开发系的域名段
 *   不再写「数据流通」（原型里没有这个词）。
 *
 * 覆盖口径（2026-10-18 本轮修）：这张表必须覆盖 `App.tsx` 里每一条可访问路由。
 * 旧实现漏了 15 条（`/ops/*`、`/users`、`/api-keys`、`/mpc`、`/system-status`、
 * `/capability-matrix`、`/data-pipeline`、`/compliance-disclosure`、`/flow/guide`、
 * `/flow/quota`、`/login`、`/register`），而兜底条目写的是
 * `{ prefix: '', title: '运营概览', segs: ['仪表盘'] }`：空前缀在 `startsWith` 下等于命中全站，
 * 于是这些页面的面包屑与标签页标题一律冒充成「运营概览」。现在
 * ① 补上漏的映射（段名逐字取 `Sidebar.tsx` 的左栏菜单文案），
 * ② 未命中改成诚实的 `UNKNOWN_CRUMB`（无前缀、`未知页面`），
 * ③ 命中按**段边界**（`/data-products` 不再顺带命中 `/data-products-archive`）。
 * 由 `__tests__/routeCrumb.test.ts` 当场解析 App.tsx 做差分钉住，改路由不用改注释里的数字。
 */
export type CrumbStyle = 'slash' | 'none';

export interface RouteCrumb {
  /**
   * 命中前缀。表内条目一律**非空且以 `/` 开头**（App.tsx 里子路由是相对路径 `path="contracts"`，
   * 这里统一带斜杠；空前缀 / 裸 `/` 在 `startsWith` 下会吞掉全站，见文件头）。
   * 合成条目（`UNKNOWN_CRUMB`）不带前缀 = 永不参与匹配；`HOME_CRUMB` 走精确相等，也不在表里。
   */
  prefix?: string;
  title: string;
  style: CrumbStyle;
  /** 面包屑段，最后一段 = 当前页。style='none' 时为空。 */
  segs: string[];
}

const HOME = '首页';
const DEV = '我的开发';

/**
 * 命中方式 = find + 段边界前缀命中的顺序命中：带参段与更深段必须排在父前缀之前，
 * 否则 `/flow/tasks/new` 会被 `/flow/tasks` 吞掉（本轮修的真缺陷，用例锁住顺序）。
 */
export const ROUTE_CRUMBS: RouteCrumb[] = [
  // 2-2 / 2-4：段名逐字照图（第三段）；样式 = 斜杠（页签带已于 2026-10-18 删除）。
  { prefix: '/flow/tasks/new', title: '新增开发任务', style: 'slash', segs: [HOME, DEV, '新增开发任务'] },
  { prefix: '/flow/tasks/', title: '任务详情', style: 'slash', segs: [HOME, DEV, '任务详情'] },
  // 2-1：`首页 / 我的开发`。
  { prefix: '/flow/tasks', title: DEV, style: 'slash', segs: [HOME, DEV] },
  // 2-3：`首页 / 我的开发 / 调试环境`。
  { prefix: '/notebooks', title: '调试环境', style: 'slash', segs: [HOME, DEV, '调试环境'] },
  // 3-x：壳层只给根段 `我的结果集`；`OpenAPI导出 / 新建API / 接口监控` 由页内钻取栈追加（crumbStackStore）。
  // 兄弟路由（数据集打包/结果推送）段名静态，必须排在 /deliveries 前（find + 前缀命中的顺序契约）。
  { prefix: '/deliveries/dataset', title: '数据集打包', style: 'slash', segs: ['我的结果集', '数据集打包'] },
  { prefix: '/deliveries/push', title: '结果推送', style: 'slash', segs: ['我的结果集', '结果推送'] },
  { prefix: '/deliveries', title: '我的结果集', style: 'slash', segs: ['我的结果集'] },
  // 首页的 y89–101 是黑色页面标题「首页」，不是面包屑（A-1 逐区逐字第 1 条）。
  { prefix: '/home', title: HOME, style: 'slash', segs: [HOME] },

  { prefix: '/data-products/create', title: '创建数据产品', style: 'slash', segs: ['数据产品', '创建产品'] },
  { prefix: '/data-products', title: '数据产品', style: 'slash', segs: ['数据产品'] },
  { prefix: '/data-resources', title: '数据资源', style: 'slash', segs: ['数据资源'] },
  { prefix: '/contracts/create', title: '创建合约', style: 'slash', segs: ['合约管理', '创建合约'] },
  { prefix: '/contracts', title: '合约管理', style: 'slash', segs: ['合约管理'] },
  { prefix: '/workloads', title: '负载测试与发布', style: 'slash', segs: [DEV, '负载测试与发布'] },
  { prefix: '/compute', title: '计算调度', style: 'slash', segs: [DEV, '计算调度'] },
  { prefix: '/sandbox-sessions', title: '沙箱会话', style: 'slash', segs: ['沙箱管理', '会话列表'] },
  { prefix: '/dev-sandbox', title: '开发沙箱', style: 'slash', segs: ['沙箱管理', '开发沙箱'] },
  { prefix: '/output-control', title: '输出管控', style: 'slash', segs: ['输出管控'] },
  // 4-1 逐字：`安全管理 / 操作日志`。
  { prefix: '/audit', title: '操作日志', style: 'slash', segs: ['安全管理', '操作日志'] },
  { prefix: '/catalog', title: '数据目录', style: 'slash', segs: ['数据目录'] },
  { prefix: '/monitoring', title: '监控中心', style: 'slash', segs: ['监控中心'] },
  { prefix: '/identity/keys', title: '密钥管理', style: 'slash', segs: ['身份管理', '密钥管理'] },
  { prefix: '/certificates', title: '证书管理', style: 'slash', segs: ['身份管理', '证书管理'] },
  { prefix: '/connectors', title: '连接器管理', style: 'slash', segs: ['连接器管理'] },
  { prefix: '/federation', title: '跨空间联邦', style: 'slash', segs: ['跨空间联邦'] },
  { prefix: '/training', title: '训练管理', style: 'slash', segs: ['训练管理'] },

  // ↓ 2026-10-18 补齐：App.tsx 早已注册、表里却没有的路由（旧口径下全部落「运营概览」兜底）。
  // 段名一律逐字取 Sidebar.tsx 的左栏菜单文案，不在这里新造导航词。
  { prefix: '/flow/guide', title: '开发指引', style: 'slash', segs: ['开发指引'] },
  { prefix: '/flow/quota', title: '环境配额', style: 'slash', segs: ['环境配额'] },
  { prefix: '/ops/deployments', title: '部署', style: 'slash', segs: ['K8s 运营', '部署'] },
  { prefix: '/ops/network-policies', title: '网络策略', style: 'slash', segs: ['K8s 运营', '网络策略'] },
  { prefix: '/ops/pvcs', title: '存储卷', style: 'slash', segs: ['K8s 运营', '存储卷'] },
  { prefix: '/ops/logs', title: '日志', style: 'slash', segs: ['K8s 运营', '日志'] },
  { prefix: '/capability-matrix', title: '能力矩阵', style: 'slash', segs: ['沙箱管理', '能力矩阵'] },
  { prefix: '/data-pipeline', title: '数据管线', style: 'slash', segs: ['数据管线'] },
  { prefix: '/compliance-disclosure', title: '合规披露', style: 'slash', segs: ['安全管理', '合规披露'] },
  { prefix: '/mpc', title: 'MPC 密钥共享', style: 'slash', segs: ['身份管理', 'MPC 密钥共享'] },
  { prefix: '/system-status', title: '系统状态', style: 'slash', segs: ['监控中心', '系统状态'] },
  { prefix: '/users', title: '用户管理', style: 'slash', segs: ['用户管理'] },
  { prefix: '/api-keys', title: 'API 密钥', style: 'slash', segs: ['API 密钥'] },
  // 登录/注册在 MainLayout 之外渲染（App.tsx 里它们是 ProtectedRoute 的兄弟节点），没有面包屑带；
  // 仍登记在此，是为了让「App.tsx 每条路由都有归属」这条契约是全量的，不留兜底例外。
  { prefix: '/login', title: '登录', style: 'none', segs: [] },
  { prefix: '/register', title: '注册', style: 'none', segs: [] },
];

/**
 * 首页条目。**不进表**：表是前缀命中，`/` 作为前缀等于吞掉全站（就是本轮修掉的 `prefix: ''` 那个坑），
 * 所以首页用精确相等判定。
 *
 * 首页也有面包屑带：strict-1-home.md §2 行 48「面包屑条（非卡片，浮于底色） 244,76,1172,47（y64–123）；
 * 图标 ink 245,90,13,11；文字 ink 269,89,28,14」+ §5 行 152「面包屑 245,89,52,13 —「首页」」。
 * 14px 墨高解不出 24px 页面大标题，所以这里不是「页面标题带」。
 */
export const HOME_CRUMB: RouteCrumb = { prefix: '/', title: HOME, style: 'slash', segs: [HOME] };

/**
 * 未命中时的诚实条目：只说「这是张没登记过的页面」，不冒充任何业务页。
 * 没有 `prefix` = 不参与匹配（旧兜底是 `prefix: ''` + `title: '运营概览'`，两者都是缺陷本身）。
 */
export const UNKNOWN_CRUMB: RouteCrumb = { title: '未知页面', style: 'slash', segs: ['未知页面'] };

/** 段边界前缀命中：`/data-products` 命中 `/data-products` 与 `/data-products/…`，不命中 `/data-products-archive`。 */
function hit(pathname: string, prefix: string | undefined): boolean {
  if (!prefix) return false;
  return pathname === prefix || pathname.startsWith(prefix.endsWith('/') ? prefix : `${prefix}/`);
}

export function getRouteCrumb(pathname: string): RouteCrumb {
  if (pathname === '/' || pathname === '') return HOME_CRUMB;
  return ROUTE_CRUMBS.find((item) => hit(pathname, item.prefix)) ?? UNKNOWN_CRUMB;
}

/** 浏览器标签页标题里的产品名，与 index.html 的静态 `<title>` 同一口径。 */
export const APP_TITLE = '密态沙箱系统 CDS';

/**
 * 标签页标题 = 当前页 title + 产品名。纯函数，全站唯一设置点在 `MainLayout`
 * （标题与面包屑同源，不再各处各写一份）。
 */
export function documentTitleFor(pathname: string): string {
  return `${getRouteCrumb(pathname).title} · ${APP_TITLE}`;
}
