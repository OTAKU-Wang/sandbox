/**
 * 波次 2「低危清零」的 CSS 静态门（A210 / A225 / A227 / A230 / A234 / A236 / A237 / A238 / A239 / A240）。
 *
 * 这一族全是「今天不炸、改一行就静默」的可维护性缺陷：令牌抄成字面量、测试属性当样式钩子、
 * 注释与代码差 4px、hover 只改一条宽度为 0 的边框颜色。它们没法用 DOM 断言钉（jsdom 里
 * antd 的样式是 CSS-in-JS，这些页面级 .css 的层叠结果测不出来），所以走本仓既有的静态门口径：
 * 读源文件、断言形状。**断言只针对修过的那几条，不做全局洁癖扫描**，否则下一个人加一条普通
 * 字面量就要红一整屏。
 */
import { describe, it, expect } from 'vitest';
import { existsSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';

function readSource(rel: string): string {
  let dir = process.cwd();
  for (let depth = 0; depth < 6; depth += 1) {
    const candidate = join(dir, rel);
    if (existsSync(join(dir, 'package.json')) && existsSync(candidate)) return readFileSync(candidate, 'utf8');
    const parent = dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  throw new Error(`找不到源文件 ${rel}`);
}

/** 剥掉块注释：这些门断言的是代码里还留着什么，注释里引用旧写法不算残留。 */
function code(css: string): string {
  return css.replace(/\/\*[\s\S]*?\*\//g, '');
}

/** 取某个选择器块的正文（简单括号匹配，够这些定宽块用）。 */
function blockOf(raw: string, selector: string): string {
  const css = code(raw);
  const at = css.indexOf(selector);
  expect(at, `选择器不存在：${selector}`).toBeGreaterThan(-1);
  const open = css.indexOf('{', at);
  const close = css.indexOf('}', open);
  return css.slice(open + 1, close);
}

/** 只看声明行（注释里出现 `#1b6ff2` 不算抄了一份字面量）。 */
function declarations(css: string): string[] {
  return code(css).split('\n')
    .map((l) => l.trim())
    .filter((l) => l.includes(':') && !l.startsWith('*') && !l.endsWith('{') && !l.endsWith('}'));
}

/** 令牌回退值里的十六进制是合法的（`var(--x, #hex)`），要抓的是脱离令牌的裸字面量。 */
function bareLiterals(css: string, hexes: string[]): string[] {
  const stripped = code(css).replace(/var\(--[a-z0-9-]+,\s*#[0-9a-f]+\)/gi, 'var(--token)');
  // 令牌定义行（`--nbw-muted: #8c8c8c;`）正是这些十六进制该待的地方，抓的是它外面的复制品。
  return declarations(stripped).filter((l) => !/^--[a-z0-9-]+:/i.test(l) && hexes.some((h) => l.toLowerCase().includes(h)));
}

const index = readSource('src/index.css');
const dtc = readSource('src/pages/Flow/devTaskCreate.css');
const nbw = readSource('src/pages/Flow/notebookWorkbench.css');
const t24 = readSource('src/pages/Flow/taskDetail.css');
const dvo = readSource('src/pages/Flow/deliveryOpenapi.css');
const audit = readSource('src/pages/Audit/auditLog.css');

describe('A238：注释说的 hover 态必须真的存在', () => {
  it('.cds-task-card 基态给的是 1px 透明边，不是 border:none（否则 :hover 的 border-color 空转）', () => {
    const base = blockOf(index, '.cds-task-card {');
    expect(base).toMatch(/border:\s*1px solid transparent/);
    expect(base).not.toMatch(/border:\s*none/);
    // hover 侧仍然只染颜色——基态有宽度之后，这条才第一次真的会变色。
    expect(blockOf(index, '.cds-task-card:hover')).toMatch(/border-color:\s*#bcd4f6/);
  });

  it('三钮高度不再有「注释 36 / 代码 32」两套真值', () => {
    const foot = index.slice(index.indexOf('.cds-task-card-foot .ant-btn {'));
    expect(foot.slice(0, 400)).toMatch(/height:\s*32px/);
    expect(foot.slice(0, 400)).not.toMatch(/高\s*36/);
  });
});

describe('A239：窄端网格的最小轨可以收缩', () => {
  it('≤1023 档用 min(280px, 100%)，不留固定 280px 最小轨', () => {
    expect(index).toMatch(/repeat\(auto-fill,\s*minmax\(min\(280px,\s*100%\),\s*1fr\)\)/);
    expect(declarations(index).some((l) => l.includes('minmax(280px, 1fr)'))).toBe(false);
  });
});

describe('A240：全局样式的令牌与钩子', () => {
  it('index.css 里不再有用 data-testid 当样式选择器的规则', () => {
    const selectorLines = code(index).split('\n').filter((l) => l.includes('[data-testid') && l.trim().endsWith('{'));
    expect(selectorLines).toEqual([]);
  });

  it('药签交互态改挂真类名，且渲染处真的带上了这个类', () => {
    expect(index).toMatch(/:is\(\.cds-home,\s*\.cds-detail\)\s+\.cds-task-tab\s*{/);
    for (const rel of ['src/pages/Flow/DevHome.tsx', 'src/pages/Audit/AuditLog.tsx', 'src/pages/Flow/DevTaskDataPicker.tsx']) {
      expect(readSource(rel), rel).toContain('className="cds-task-tab"');
    }
  });

  it('页面底色有令牌，正文不再拄 #f0f5fb 字面量', () => {
    expect(index).toMatch(/--cds-page-bg:\s*#f0f5fb/);
    expect(declarations(index).some((l) => /background:\s*#f0f5fb/.test(l))).toBe(false);
  });

  it('空匹配的两条 KPI 规则已删（钩子指向的 antd Card 全站不存在）', () => {
    expect(code(index)).not.toMatch(/\.ant-card\[data-testid\^="kpi-"\]/);
  });
});

describe('A227 / A230：同屏 antd 覆盖族收拢', () => {
  it('deliveryOpenapi.css 的两处卡身内距共用一个令牌', () => {
    expect(dvo).toMatch(/--cds-card-body-padding:\s*24px/);
    const uses = declarations(dvo).filter((l) => /padding:\s*var\(--cds-card-body-padding\)/.test(l));
    expect(uses).toHaveLength(2);
    // 令牌定义行自己就写着 `--cds-card-body-padding: 24px`，它不是「又拄了一份字面量」。
    expect(declarations(dvo).some((l) => /(^|[^-])padding:\s*24px/.test(l) && !l.includes('padding-inline'))).toBe(false);
  });

  it('筛选项可缩（min-width: 0）、标签墨色走令牌、主钮不再只保底不封顶', () => {
    expect(blockOf(dvo, '.cds-openapi-filter .cds-filter-field {')).toMatch(/min-width:\s*0/);
    expect(blockOf(dvo, '.cds-openapi-filter .cds-filter-label {')).toMatch(/color:\s*var\(--cds-openapi-label-ink\)/);
    expect(declarations(dvo).some((l) => /color:\s*rgba\(0,\s*0,\s*0,\s*0\.88\)/.test(l))).toBe(false);
    expect(dvo).toMatch(/\.cds-filter-actions\s*>\s*\.ant-btn\s*\{[^}]*max-width:\s*100%/);
  });

  it('auditLog.css 不再拄第二份主色，末行下边线排除空态占位行', () => {
    expect(declarations(audit).some((l) => /#1b6ef3/.test(l))).toBe(false);
    expect(audit).toMatch(/tr:last-child:not\(\.ant-table-placeholder\)\s*>\s*td/);
    expect(audit).toMatch(/background:\s*var\(--cds-primary\)/);
  });
});

describe('A210 / A236 / A237：开发任务创建页', () => {
  it('那一支蓝只有令牌一个来源（声明行里不再出现裸 #1b6ff2）', () => {
    expect(dtc).toMatch(/--cds-dtc-ink-blue:\s*#1b6ff2/);
    expect(declarations(dtc).some((l) => /#1b6ff2/.test(l) && !l.includes('--cds-dtc-ink-blue'))).toBe(false);
  });

  it('页内私有类名一律收在 .cds-dev-task-create 作用域里，且不再用 data-testid 当样式钩子', () => {
    for (const cls of ['cds-mem-stepper', 'cds-picker-side', 'cds-quota-advice', 'cds-task-tab']) {
      const lines = code(dtc).split('\n').filter((l) => l.includes(`.${cls}`) && l.trim().endsWith('{'));
      expect(lines.length, cls).toBeGreaterThan(0);
      for (const l of lines) {
        expect(l.trim(), `${cls} 的选择器没有限定在页根：${l.trim()}`).toMatch(/\.cds-dev-task-create/);
      }
    }
    expect(code(dtc).split('\n').filter((l) => l.includes('[data-testid'))).toEqual([]);
  });

  it('卡头长标题有可截断的最小宽度（A236），备选列表超长可滚（A237）', () => {
    // 标题这一族分了两条：一条排 4×14 蓝条，一条把省略接回来（都限在页根内）。
    const titleBlocks = code(dtc).split('}').filter((b) => b.includes('.cds-dev-task-create .ant-card-head-title {'));
    expect(titleBlocks.join('\n')).toMatch(/min-width:\s*0/);
    expect(dtc).toMatch(/\.cds-picker-side\s+ul\s*\{[^}]*overflow-y:\s*auto/);
  });
});

describe('A225：笔记本工作台不再吃未定义的 --nbw-*', () => {
  it('每一处 var(--nbw-*) 都带兜底值', () => {
    const bad = declarations(nbw)
      .flatMap((l) => l.split(';').map((p) => p.trim()))
      .filter((p) => p.includes('var(--nbw-') && !/var\(--nbw-[a-z-]+,\s*\S/.test(p));
    expect(bad).toEqual([]);
  });

  it('危险色与灰各收成一支，正文不再散着 #cf1322 / #8c8c8c 字面量', () => {
    expect(nbw).toMatch(/--nbw-danger:\s*#cf1322/);
    expect(bareLiterals(nbw, ['#cf1322', '#8c8c8c'])).toEqual([]);
  });

  it('日志/路径不再用 word-break: break-all（词中间硬切），换 overflow-wrap: anywhere', () => {
    expect(nbw).not.toMatch(/word-break:\s*break-all/);
    expect(nbw).toMatch(/overflow-wrap:\s*anywhere/);
  });
});

describe('A234：任务详情两处盒子的重复声明收拢', () => {
  it('字段盒与配额盒共用一条规则，配额盒只留自己那条差异', () => {
    // 共用一条：形状声明（min-height/overflow/padding…）只出现在合并块里。
    const shared = blockOf(t24, '.cds-t24 .t24-field-box,\n.cds-t24 .t24-quota-box {');
    expect(shared).toMatch(/min-height:\s*42px/);
    expect(shared).toMatch(/overflow-wrap:\s*anywhere/);
    // 配额盒自己那条只剩差异声明（原来九条逐字重复两遍，改一处必漏一处）。
    const quotaOnly = blockOf(t24, '.cds-t24 .t24-quota-box {\n  justify-content');
    expect(quotaOnly.trim()).toMatch(/^justify-content:\s*space-between/);
    expect(quotaOnly).not.toMatch(/min-height|padding|background/);
  });

  it('进度条 line-height 只剩一处真值；头部按钮改 min-height（内联高度不再被顶掉）', () => {
    const lines = declarations(t24).filter((l) => /line-height:\s*8px/.test(l));
    expect(lines.length).toBeGreaterThan(0);
    const btn = blockOf(t24, '.cds-t24 .t24-head-actions .ant-btn {');
    expect(btn).toMatch(/min-height:\s*38px/);
    expect(btn).not.toMatch(/\n\s*height:/);
  });
});
