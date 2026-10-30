/**
 * A259：开发者菜单白名单命中即整节点放行（latent）。
 *
 * `filterMenuByWorkspace` 的 `prune` 旧写法在 key 命中 `DEVELOPER_MENU_KEYS` 时
 * `return item`——连着 children 一起放行。今天不出事只靠一条没人写下来的约定：
 * Sidebar 里带 children 的分组 key 全是不带斜杠的 token，而白名单全是带前导斜杠的路由。
 * 这里钉三件事：①命中只授权这一层，子层递归各自裁；②正例（白名单叶子）不被削弱；
 * ③把那条"分组 key 不带斜杠"的命名约定变成机器门禁——它一旦被写反，泄露是静默的。
 */
import { describe, it, expect } from 'vitest';
import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { DEVELOPER_MENU_KEYS, filterMenuByWorkspace } from '../workspace';

type Item = { key?: string; label?: string; children?: Item[]; type?: string };

const prune = (items: Item[]) => filterMenuByWorkspace<Item>(items, 'developer');
const keysOf = (items: readonly Item[]): (string | undefined)[] => items.map((i) => i.key);

describe('A259：白名单命中只授权命中那一层', () => {
  it('命中白名单的分组不再把整棵子树递给开发角色', () => {
    // '/audit' 在白名单里；假设下一个人把某个分组也叫 '/audit'，它的子项必须各自过白名单。
    const tree: Item[] = [
      {
        key: '/audit',
        label: '审计（假想的斜杠分组）',
        children: [
          { key: '/audit', label: '审计日志' },
          { key: '/k8s-ops', label: 'K8s 运维（运营侧）' },
          { key: '/identity/keys', label: '密钥管理（运营侧）' },
        ],
      },
    ];
    const out = prune(tree);
    expect(out).toHaveLength(1);
    // 分组本身留着（它命中了），但子项只剩白名单里那一条。
    expect(keysOf(out[0].children ?? [])).toEqual(['/audit']);
  });

  it('命中白名单的分组若一个子项都没活下来，整块不渲染（空子菜单是死链）', () => {
    const out = prune([
      { key: '/audit', label: '假想分组', children: [{ key: '/identity/certs', label: '证书' }] },
    ]);
    expect(out).toEqual([]);
  });

  it('正例不弱化：白名单叶子照常保留、未命中叶子照常剔除、keyless 分隔线照常放行', () => {
    const out = prune([
      { key: '/catalog', label: '产品目录' },
      { key: '/identity/keys', label: '密钥管理' },
      { type: 'divider' },
    ]);
    expect(keysOf(out)).toEqual(['/catalog', undefined]);
  });

  it('未命中的分组仍按子项递归（原行为不变）', () => {
    const out = prune([
      {
        key: 'sandbox',
        label: '沙箱',
        children: [{ key: '/notebooks', label: 'Notebook' }, { key: '/k8s-ops', label: '运维' }],
      },
    ]);
    expect(keysOf(out)).toEqual(['sandbox']);
    expect(keysOf(out[0].children ?? [])).toEqual(['/notebooks']);
  });
});

/** 与其余静态门禁同一读法：从 vitest cwd 往上找 package.json 定位仓库里的 cds-frontend。 */
function readSource(rel: string): string {
  let dir = process.cwd();
  for (let i = 0; i < 8; i += 1) {
    const candidate = join(dir, 'cds-frontend', rel);
    if (existsSync(join(dir, 'package.json')) && existsSync(candidate)) return readFileSync(candidate, 'utf8');
    const candidateHere = join(dir, rel);
    if (existsSync(join(dir, 'package.json')) && existsSync(candidateHere)) return readFileSync(candidateHere, 'utf8');
    const parent = join(dir, '..');
    if (parent === dir) break;
    dir = parent;
  }
  const fallback = join(process.cwd(), rel);
  return readFileSync(fallback, 'utf8');
}

describe('A259 的根因：那条没人写下来的命名约定', () => {
  const sidebar = readSource('src/components/Layout/Sidebar.tsx');

  /** 抓「带 children 的分组 key」：`key: 'x'` 之后、下一个 `key:` 之前出现 `children` 即为分组。 */
  function groupKeys(source: string): string[] {
    const found: string[] = [];
    const re = /key:\s*'([^']*)'/g;
    let m: RegExpExecArray | null = re.exec(source);
    while (m !== null) {
      const tail = source.slice(m.index + m[0].length);
      const next = tail.indexOf('key:');
      const scope = next === -1 ? tail.slice(0, 400) : tail.slice(0, next);
      if (/\bchildren\s*:/.test(scope)) found.push(m[1]);
      m = re.exec(source);
    }
    return found;
  }

  it('白名单项全是带前导斜杠的路由（这条一旦破，命中就可能当父节点）', () => {
    const notRoutes = DEVELOPER_MENU_KEYS.filter((k) => !k.startsWith('/'));
    expect(notRoutes).toEqual([]);
  });

  it('Sidebar 里所有分组的 key 都不带前导斜杠 ⇒ 今天没有任何白名单项能当父节点', () => {
    const groups = groupKeys(sidebar);
    expect(groups.length).toBeGreaterThan(5); // 针本身别退化成对空数组断言
    const colliding = groups.filter((k) => DEVELOPER_MENU_KEYS.includes(k));
    expect(colliding).toEqual([]);
  });
});
