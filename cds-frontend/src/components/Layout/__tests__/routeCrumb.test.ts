/**
 * 路由注册表 ↔ App.tsx 的一致性（数据级用例，锁本轮修掉的真缺陷）。
 *
 * 缺陷：`ROUTE_CRUMBS` 只覆盖了一部分路由，兜底条目写成 `{ prefix: '', title: '运营概览' }`，
 * 于是**每一条未映射的路径**（/ops/*、/users、/mpc、/system-status …）的面包屑与标题
 * 都冒充成「运营概览 / 仪表盘」。这里不写死条目数，而是当场解析 App.tsx 的路由表做差分：
 * 语料大小用 console.log 打出来，改路由不用改本文件。
 *
 * 两个解析坑（都在下面显式处理）：
 * - App.tsx 的子路由是**相对路径**（`path="contracts"`，无前置斜杠），表里一律带斜杠 → 归一再比。
 * - 空前缀 `''` 会让 `startsWith('')` 命中全站（`/` 同理）→ 表里禁止出现，用例钉住。
 */
import { describe, it, expect } from 'vitest';
import raw from '../../../App.tsx?raw';
import { ROUTE_CRUMBS, getRouteCrumb } from '../routeCrumb';

/** 旧兜底标签：任何真实路由都不该解出它。 */
const FALLBACK_LABEL = '运营概览';

// 用 Vite 的 ?raw 直接读路由源文件（?raw 在 vitest 与 tsc 两侧都可用；
// 本池的 import.meta.url 不是 file: scheme，fileURLToPath 会抱「URL must be of scheme file」）。
const appSrc = raw as string;

/** 抓 `<Route …>` 的 path（含 `<Route index>`，它 = 父路径 '/'）。 */
function parseRouteEntries(src: string): string[] {
  const out: string[] = [];
  for (const m of src.matchAll(/<Route\s+([^>]*?)\/?>/gs)) {
    const attrs = m[1];
    const pm = attrs.match(/path\s*=\s*"([^"]*)"/);
    if (pm) out.push(pm[1]);
    else if (/\bindex\b/.test(attrs)) out.push('');
  }
  return out;
}

/** 相对路径 → 可访问路径；`:param` 段换成一个真实形状的 id，前缀匹配才测得到。 */
const PARAM = '6f1c2c0a-0000-4111-8111-000000000001';
function toConcretePath(rel: string): string {
  return (
    '/' +
    rel
      .replace(/^\//, '')
      .split('/')
      .map((seg) => (seg.startsWith(':') ? PARAM : seg))
      .join('/')
  );
}

const ENTRIES = parseRouteEntries(appSrc);
// '*'（NotFound 通配）与 '/*'（MainLayout 外壳）不是可访问路径，差分里排除。
const PAGE_PATHS = ENTRIES.filter((p) => p !== '*' && p !== '/*').map(toConcretePath);

describe('routeCrumb 表 ↔ App.tsx 路由表差分', () => {
  it('语料规模自证：解析到的路由条目与表条目都打出来', () => {
    console.log(`[routeCrumb] App.tsx 路由条目 ${ENTRIES.length} 条: ${JSON.stringify(ENTRIES)}`);
    console.log(
      `[routeCrumb] ROUTE_CRUMBS 前缀 ${ROUTE_CRUMBS.length} 条: ${ROUTE_CRUMBS.map((c) => c.prefix).join(' ')}`,
    );
    console.log(`[routeCrumb] 参与差分的页面路径 ${PAGE_PATHS.length} 条:\n${PAGE_PATHS.join('\n')}`);
    // 解析器失灵（正则漂了）比差分红更难查，这里先钉住语料本身非空且含已知路径。
    expect(ENTRIES.length).toBeGreaterThan(30);
    expect(ENTRIES).toContain('/login');
    expect(ENTRIES).toContain('contracts/:id');
    expect(PAGE_PATHS).toContain(`/contracts/${PARAM}`);
  });

  it('App.tsx 每条路由都命中表条目：没有一条落「运营概览」兜底', () => {
    const unmatched = PAGE_PATHS.filter((p) => {
      const c = getRouteCrumb(p);
      return c.title === FALLBACK_LABEL || !c.prefix;
    });
    console.log(`[routeCrumb] 未命中/冒充运营概览: ${JSON.stringify(unmatched)}`);
    expect(unmatched).toEqual([]);
  });

  it('表里没有空前缀、也没有 / 这种吞全站的前缀；一律带前置斜杠', () => {
    const bad = ROUTE_CRUMBS.filter((c) => !c.prefix || c.prefix === '/' || !c.prefix.startsWith('/'));
    expect(bad.map((c) => JSON.stringify(c))).toEqual([]);
  });

  it('每条表条目都可达：不被排在更前面的前缀吞掉（find + 顺序命中的顺序契约）', () => {
    const dead = ROUTE_CRUMBS.filter((c) => getRouteCrumb(c.prefix as string) !== c);
    expect(dead.map((c) => c.prefix)).toEqual([]);
  });

  it('命中不到时诚实报「未知页面」，不再冒充运营概览/仪表盘', () => {
    for (const p of ['/nope', '/ops', '/flow', '/data-products-archive', '/contracts-history']) {
      const c = getRouteCrumb(p);
      expect(c.title, p).toBe('未知页面');
      expect(c.segs, p).toEqual(['未知页面']);
      // 合成条目没有前缀 = 绝不参与前缀匹配（旧实现是 prefix:'' → startsWith('') 命中全站）
      expect(c.prefix ?? '', p).toBe('');
    }
  });

  it('前缀按段边界命中：/data-products 不能把 /data-products-archive 也算进来', () => {
    expect(getRouteCrumb('/data-products').segs).toEqual(['数据产品']);
    expect(getRouteCrumb(`/data-products/${PARAM}`).segs).toEqual(['数据产品']);
    expect(getRouteCrumb('/data-products-archive').title).toBe('未知页面');
    expect(getRouteCrumb('/contracts-history').title).toBe('未知页面');
  });

  it('带参段与更深段排在父前缀之前（/flow/tasks/new 不被 /flow/tasks 吞）', () => {
    expect(getRouteCrumb('/flow/tasks/new').title).toBe('新增开发任务');
    expect(getRouteCrumb(`/flow/tasks/${PARAM}`).title).toBe('任务详情');
    expect(getRouteCrumb('/flow/tasks').title).toBe('我的开发');
    expect(getRouteCrumb('/deliveries/dataset').title).toBe('数据集打包');
    expect(getRouteCrumb('/deliveries').title).toBe('我的结果集');
  });

  it('补上的映射逐字（段名取左栏菜单文案，不新造词）', () => {
    const want: Record<string, string[]> = {
      '/flow/guide': ['开发指引'],
      '/flow/quota': ['环境配额'],
      '/ops/deployments': ['K8s 运营', '部署'],
      '/ops/network-policies': ['K8s 运营', '网络策略'],
      '/ops/pvcs': ['K8s 运营', '存储卷'],
      '/ops/logs': ['K8s 运营', '日志'],
      '/capability-matrix': ['沙箱管理', '能力矩阵'],
      '/data-pipeline': ['数据管线'],
      '/compliance-disclosure': ['安全管理', '合规披露'],
      '/mpc': ['身份管理', 'MPC 密钥共享'],
      '/system-status': ['监控中心', '系统状态'],
      '/users': ['用户管理'],
      '/api-keys': ['API 密钥'],
    };
    for (const [path, segs] of Object.entries(want)) {
      expect(getRouteCrumb(path).segs, path).toEqual(segs);
    }
  });

  it('登录/注册在 MainLayout 之外：style=none、无段（不画面包屑带）', () => {
    expect(getRouteCrumb('/login').style).toBe('none');
    expect(getRouteCrumb('/register').style).toBe('none');
  });
});
