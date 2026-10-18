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
 */
export type CrumbStyle = 'slash' | 'none';

export interface RouteCrumb {
  prefix: string;
  title: string;
  style: CrumbStyle;
  /** 面包屑段，最后一段 = 当前页。style='none' 时为空。 */
  segs: string[];
}

const HOME = '首页';
const DEV = '我的开发';

/**
 * 命中方式 = find + startsWith 的顺序命中：带参段与更深段必须排在父前缀之前，
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
  // 兄弟路由（数据集打包/结果推送）段名静态，必须排在 /deliveries 前（find+startsWith 顺序命中）。
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
];

const FALLBACK: RouteCrumb = { prefix: '', title: '运营概览', style: 'slash', segs: ['仪表盘'] };

export function getRouteCrumb(pathname: string): RouteCrumb {
  // 首页也有面包屑带：strict-1-home.md §2 行 48「面包屑条（非卡片，浮于底色） 244,76,1172,47（y64–123）；
  // 图标 ink 245,90,13,11；文字 ink 269,89,28,14」+ §5 行 152「面包屑 245,89,52,13 —「首页」」。
  // 14px 墨高解不出 24px 页面大标题，所以这里不是「页面标题带」。
  if (pathname === '/' || pathname === '') return { prefix: '/', title: HOME, style: 'slash', segs: [HOME] };
  return ROUTE_CRUMBS.find((item) => pathname.startsWith(item.prefix)) ?? FALLBACK;
}
