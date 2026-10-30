/**
 * 波次 3「队列尾巴」的前端卫生门：A210 / A225 / A234 里那些**今天不炸、改一行就静默**的部分。
 *
 * 这些页面级 .css 的层叠结果在 jsdom 里测不出来（antd 自己是 CSS-in-JS），所以沿用本仓
 * 静态门口径：读源文件、断言形状；唯一的运行时断言是最后那条 Progress 几何——它要证明
 * 「高度交给组件 API」这条路真的把 6px 写进了 DOM，CSS 才敢不再用 !important 去抢。
 * 和波次 2 一样：**只钉本轮改过的那几条**，不做全局洁癖扫描。
 */
import { describe, it, expect } from 'vitest';
import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { render } from '@testing-library/react';
import { Progress } from 'antd';

function root(): string {
  let dir = process.cwd();
  for (let depth = 0; depth < 6; depth += 1) {
    if (existsSync(join(dir, 'package.json'))) return dir;
    const parent = dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  return process.cwd();
}

const BASE = root();
const readSource = (rel: string): string => readFileSync(join(BASE, rel), 'utf8');

/** 剥掉块注释：注释里引用旧写法（`.nbw-tree-error`、`#c0392b`）不算残留。 */
const code = (css: string): string => css.replace(/\/\*[\s\S]*?\*\//g, '');

type Rule = { selector: string; decls: string[] };

/** 括号匹配切块；@media 展开一层，里面的规则按自身选择器收集。 */
function rules(css: string): Rule[] {
  const src = code(css);
  const out: Rule[] = [];
  const walk = (s: string): void => {
    let i = 0;
    for (;;) {
      const open = s.indexOf('{', i);
      if (open === -1) return;
      let depth = 1;
      let j = open + 1;
      while (j < s.length && depth > 0) {
        if (s[j] === '{') depth += 1;
        else if (s[j] === '}') depth -= 1;
        j += 1;
      }
      const selector = s.slice(i, open).replace(/\s+/g, ' ').trim();
      const body = s.slice(open + 1, j - 1);
      if (selector.startsWith('@')) walk(body);
      else if (selector) out.push({ selector, decls: body.split(';').map((d) => d.trim()).filter(Boolean) });
      i = j;
    }
  };
  walk(src);
  return out;
}

/** 本仓**产品**代码全集：判断一个类名是不是真的没人用，只看单页文件会假阳性
 *  （`.cds-picker-side` 就活在兄弟文件 DevTaskDataPicker.tsx 里）；测试文件要排除，
 *  否则针会被自己的字符串喂成永远绿。 */
function sourceUniverse(): string {
  const files: string[] = [];
  const walkDir = (dir: string): void => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const p = join(dir, entry.name);
      if (entry.isDirectory()) {
        if (entry.name === '__tests__') continue;
        walkDir(p);
      } else if (/\.(ts|tsx)$/.test(entry.name)) files.push(p);
    }
  };
  walkDir(join(BASE, 'src'));
  return files.map((f) => readFileSync(f, 'utf8')).join('\n');
}

const dtc = readSource('src/pages/Flow/devTaskCreate.css');
const dtcPage = readSource('src/pages/Flow/DevTaskCreatePage.tsx');
const nbwCss = readSource('src/pages/Flow/notebookWorkbench.css');
const nbwTsx = readSource('src/pages/Flow/NotebookWorkbench.tsx');
const nbwPanels = readSource('src/pages/Flow/notebookWorkbenchPanels.tsx');
const t24 = readSource('src/pages/Flow/taskDetail.css');
const t24Tsx = readSource('src/pages/Flow/TaskDetail.tsx');
const universe = sourceUniverse();

describe('A210：布局选择器不再顺手管字号', () => {
  it('devTaskCreate.css 里任何带 nth-child 的块都不声明字号/行高', () => {
    const positional = rules(dtc).filter((r) => /:nth-child\(/.test(r.selector));
    expect(positional.length, '尾列布局规则本身还在（针别退化成对空数组断言）').toBeGreaterThan(0);
    const offenders = positional
      .flatMap((r) => r.decls.map((d) => `${r.selector} { ${d} }`))
      .filter((d) => /(^|;)\s*(font-size|line-height)\s*:/.test(`${d}`));
    expect(offenders).toEqual([]);
  });

  it('配额小字的 12px/20px 挂在真类 .cds-trio-note 上', () => {
    const note = rules(dtc).filter((r) => r.selector.includes('.cds-trio-note'));
    expect(note.length).toBeGreaterThan(0);
    const decls = note.flatMap((r) => r.decls).join(';');
    expect(decls).toMatch(/font-size:\s*12px/);
    expect(decls).toMatch(/line-height:\s*20px/);
  });

  it('TSX 侧只有配额建议那一列挂 .cds-trio-note；被误伤的 export_mode 那一列是裸 div', () => {
    expect((dtcPage.match(/className="cds-trio-note"/g) ?? []).length).toBe(1);
    const at = dtcPage.indexOf('className="cds-trio-note"');
    expect(dtcPage.slice(at, at + 300)).toContain('cds-quota-advice');
    // 合约卡那一行的第三列（当年被同一支笔染成 12px 的那一列）仍然是裸 <div>。
    expect(dtcPage).toMatch(/<div>\s*<Form\.Item name="export_mode"/);
  });
});

describe('A225：笔记本工作台的红与令牌只有一个来源', () => {
  it('本屏不再有第二支红：#c0392b 从 CSS 与 TSX 里退场，只留 --nbw-danger', () => {
    for (const [name, src] of [['css', nbwCss], ['tsx', nbwTsx], ['panels', nbwPanels]] as const) {
      expect(code(src).includes('#c0392b'), `${name} 里还有裸的 #c0392b`).toBe(false);
    }
    // 并支之后：这里钉的是「别名形状」，真值那一行由 cssLowClear.wave2 钉在 index.css 上。
    expect(nbwCss).toMatch(/--nbw-danger:\s*var\(--cds-danger,\s*#cf1322\)/);
    expect(nbwTsx).toMatch(/DANGER_INK = 'var\(--nbw-danger,\s*#cf1322\)'/);
    // 内联那三处一律走常量，没有第四处漏网。
    expect((nbwTsx.match(/DANGER_INK/g) ?? []).length).toBe(4);
  });

  it('死选择器清零：CSS 里定义的每个 .nbw-* 都在源码里被用到（模板拼出来的按前缀放行）', () => {
    // 模板拼出来的类名（`nbw-run-${tone}`、`nbw-chip-${tone}`、`nbw-log-line${…}`）按前缀放行，
    // 三个来源都在两个文件里，只扫其中一个会让针假阳性。
    const dynamic = [...`${nbwTsx}\n${nbwPanels}`.matchAll(/nbw-[a-z0-9-]*(?=\$\{)/g)].map((m) => m[0]);
    expect(dynamic.length, '动态类名前缀没抓到，针会假阳性').toBeGreaterThan(0);
    const defined = [...new Set([...code(nbwCss).matchAll(/\.nbw-[a-z0-9-]+/g)].map((m) => m[0].slice(1)))];
    const dead = defined.filter(
      (cls) => !universe.includes(cls) && !dynamic.some((prefix) => cls.startsWith(prefix)),
    );
    expect(dead).toEqual([]);
  });

  it('`.nbw-tree-error` 那个 30px 缩进魔数连同死规则一起消失', () => {
    expect(code(nbwCss)).not.toMatch(/nbw-tree-error/);
  });
});

describe('A234：任务详情的进度条与 Alert 作用域', () => {
  it('进度条规则里不再出现 !important：几何走 size，CSS 只留底色与圆角', () => {
    const bar = rules(t24).filter((r) => r.selector.includes('.t24-mem-bar'));
    expect(bar.length).toBeGreaterThan(0);
    const shouty = bar.flatMap((r) => r.decls.filter((d) => d.includes('!important')).map((d) => `${r.selector} { ${d} }`));
    expect(shouty).toEqual([]);
    const bg = bar.filter((r) => r.selector.includes('.ant-progress-bg'));
    expect(bg.flatMap((r) => r.decls).some((d) => /(^|\s)height\s*:/.test(d)), 'CSS 又去抢 antd 的内联高度了').toBe(false);
  });

  it('调用点用组件 API 给 6px，且不传 strokeColor/strokeWidth（CSS 的底色因此不会被内联悄悄盖掉）', () => {
    expect(t24Tsx).toMatch(/<Progress percent=\{0\} showInfo=\{false\} size=\{\['100%', 6\]\} \/>/);
    const progressTags = t24Tsx.match(/<Progress[^>]*>/g) ?? [];
    expect(progressTags.length).toBeGreaterThan(0);
    expect(progressTags.filter((t) => /strokeColor|strokeWidth/.test(t))).toEqual([]);
  });

  it('13px 只点名现存的三类 Alert，不再给本页将来任何 Alert 预设字号', () => {
    const pageWide = rules(t24).filter((r) => r.selector === '.cds-t24 .ant-alert');
    expect(pageWide.flatMap((r) => r.decls).filter((d) => d.startsWith('font-size'))).toEqual([]);
    const scoped = rules(t24).filter((r) => r.selector.includes('.ant-alert') && r.decls.some((d) => /font-size:\s*13px/.test(d)));
    expect(scoped.length, '那条 13px 不见了——收作用域不是删规则').toBeGreaterThan(0);
    const paths = scoped.flatMap((r) => r.selector.split(',').map((s) => s.trim()));
    expect(paths).not.toContain('.cds-t24 .ant-alert');
    // 卡内那一族（未关联合约等诚实披露）。
    expect(paths.some((p) => /t24-card \.ant-alert$/.test(p))).toBe(true);
    // 页根那一层：`.cds-t24` 挂在 antd <Space> 上，子项被包进 .ant-space-item ⇒
    // 少了这一支，执行失败那条 Alert（TaskDetail.tsx:390）就会静默掉回 14px（波次 2 踩过一次）。
    expect(paths.some((p) => p.endsWith('.ant-space-item > .ant-alert'))).toBe(true);
  });

  it('死选择器清零：每个 .t24-* 都在源码里被用到', () => {
    const defined = [...new Set([...code(t24).matchAll(/\.t24-[a-z0-9-]+/g)].map((m) => m[0].slice(1)))];
    expect(defined.length).toBeGreaterThan(20);
    expect(defined.filter((cls) => !universe.includes(cls))).toEqual([]);
  });
});

describe('A234 的机制本身：antd 的 size 真的把 6px 写进 DOM', () => {
  it('.ant-progress-bg 的内联高度来自 size，不靠 CSS 抢', () => {
    const { container } = render(<Progress percent={0} showInfo={false} size={['100%', 6]} />);
    const bg = container.querySelector('.ant-progress-bg') as HTMLElement | null;
    expect(bg, 'antd 没渲染出 .ant-progress-bg，这条针失去意义').not.toBeNull();
    expect((bg as HTMLElement).style.height).toBe('6px');
    // 不传 size 时 antd 写的是默认 8px——这正是当初 CSS 必须上 !important 的原因。
    const plain = render(<Progress percent={0} showInfo={false} />);
    const plainBg = plain.container.querySelector('.ant-progress-bg') as HTMLElement;
    expect(plainBg.style.height).toBe('8px');
  });
});

/**
 * 波次 3 追加小单：JupyterDevEnv 的内联红。这页**没挂路由**、也**没有自己的 .css**，
 * 所以「死选择器」在它身上换了一副面孔：能空转的不是页面级 CSS 里的死规则，而是
 * 「引用了一支没人声明的 CSS 变量」与「声明了一支没人引用的全局令牌」——两条都钉在这儿。
 */
describe('追加小单：JupyterDevEnv 那支红进令牌，且令牌不是空转', () => {
  const jde = readSource('src/pages/Flow/JupyterDevEnv.tsx');
  const indexCss = readSource('src/index.css');
  const rootAt = indexCss.indexOf(':root');
  const rootBlock = indexCss.slice(rootAt, indexCss.indexOf('}', rootAt));
  /** CSS 全集（剥注释）：注释里出现的旧字面量是史料，不算残留。 */
  const styleUniverse = [indexCss, dtc, nbwCss, t24, readSource('src/pages/Flow/deliveryOpenapi.css')]
    .map((c) => code(c))
    .join('\n');

  it('#c0392b 从产品源码里退场：TS/TSX 与 CSS 的可执行部分都不再引用它', () => {
    // 只禁被引用的字面量（单/双引号里）；注释里用反引号引用的 `#c0392b` 留着解释来历。
    expect(universe).not.toMatch(/['"]#c0392b['"]/i);
    expect(styleUniverse).not.toMatch(/#c0392b/i);
  });

  it('这页的危险色只有一支真值，而且那支令牌真的声明了', () => {
    expect(jde).toMatch(/const DANGER_INK = 'var\(--cds-danger,\s*#cf1322\)';/);
    // 1 处定义 + 3 处引用（目录读取失败那行、状态点「沙箱已断开」、供数异常那段）。
    expect(jde.match(/DANGER_INK/g)?.length).toBe(4);
    expect(rootBlock).toMatch(/--cds-danger:\s*#cf1322;/);
  });

  it('死令牌门：:root 里声明的每个 --cds-* 都得有人引用（零豁免）', () => {
    const declared = [...new Set([...rootBlock.matchAll(/(--cds-[a-z0-9-]+)\s*:/g)].map((m) => m[1]))];
    expect(declared.length, ':root 令牌块读空了，这条针就是空转').toBeGreaterThan(5);
    const refs = `${universe}${styleUniverse}`;
    // 零豁免：豁免表每多一行都是永续债（lead 裁定），0 引用就删、用到再加回来零成本——
    // `--cds-orange-soft` 就是这么走的。真要开豁免，先在这儿写清原因并同步台账。
    expect(declared.filter((t) => !refs.includes(`var(${t}`))).toEqual([]);
  });

  it('死选择器门扩到全局样式表：index.css 里每个 .cds-* 类都有人用（零豁免）', () => {
    // 只看项目自己的 `.cds-*` 前缀；antd 覆盖类归组件库，判定口径与 .nbw-*/.t24-* 那两条一致。
    const defined = [...new Set([...code(indexCss).matchAll(/\.cds-[a-z0-9-]+/g)].map((m) => m[0].slice(1)))];
    expect(defined.length).toBeGreaterThan(30);
    expect(defined.filter((cls) => !universe.includes(cls))).toEqual([]);
  });

  it('PNG 4-2 那两条旧一代规则不许回来——真值改了名字活在 auditLog.css', () => {
    // 更正记档：我上一轮把它定性成「像素真值只在 CSS、没落在 DOM」，查全之后是错的。
    // 同一批读数由 `.cds-audit-modal .cds-audit-section`（16px/600 + 4×16 蓝条 + 无分隔线）与
    // `.cds-audit-modal .cds-audit-panel`（--audit-head-bg = #f7f9fc）承担，index.css 里那两条是
    // 上一代同名遗留 ⇒ 删遗留、把正面断言放到 DOM 上（AuditLog.test.tsx 那条弹窗针）。
    expect(code(indexCss)).not.toMatch(/\.cds-detail-(section|panel)\s*\{/);
    const auditCss = code(readSource('src/pages/Audit/auditLog.css'));
    const auditRules = rules(auditCss);
    const section = auditRules.find((r) => /\.cds-audit-modal \.cds-audit-section$/.test(r.selector));
    const panel = auditRules.find((r) => /\.cds-audit-modal \.cds-audit-panel$/.test(r.selector));
    expect(section, '分区标题规则没了——那 PNG 4-2 就真没人管了').toBeDefined();
    expect(panel, '灰面板规则没了——同上').toBeDefined();
    // 真值换了名字，读数就得跟着钉一遍，否则改名之后没人保证还是图上那两个数。
    expect(section?.decls).toEqual(expect.arrayContaining(['font-size: 16px', 'font-weight: 600']));
    expect(auditCss).toMatch(/--audit-head-bg:\s*#f7f9fc/);
    expect(panel?.decls.some((d) => /background:\s*var\(--audit-head-bg/.test(d))).toBe(true);
    const bar = auditRules.find((r) => /\.cds-audit-modal \.cds-audit-section::before$/.test(r.selector));
    expect(bar?.decls).toEqual(expect.arrayContaining(['width: 4px', 'height: 16px']));
  });

  it('口径写死：这页没有私有样式表，所以红只能走全局令牌', () => {
    // 今天成立。哪天给它加了 JupyterDevEnv.css 或 import 了别的 .css，说明令牌该降回页面级，
    // 这条针会先红，逼改的人重看上面那条「为什么走 :root 而不是 --nbw-*」。
    expect(existsSync(join(BASE, 'src/pages/Flow/JupyterDevEnv.css'))).toBe(false);
    expect(jde).not.toMatch(/import\s+['"][^'"]+\.css['"]/);
  });
});
