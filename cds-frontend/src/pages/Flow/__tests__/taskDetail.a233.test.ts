/**
 * A233（静态源闸）：任务详情页状态标签/表内链接的可达性。
 * 台账 docs/ocr-review-2026-10-18/ocr-verified-findings.md §A233：三枚浅底同色系深字的状态标签都不过
 * WCAG AA 的 4.5:1（`#00a870/#e8f7ee = 2.78`、`#f53f3f/#fdecee = 3.25`、`#1b6ef3/#e8f1ff = 4.04`），
 * 灰注 `#86909c` 对白只有 3.24；`grep -c 'focus-visible' taskDetail.css` = 0；表内链接只重写了 color，
 * 而 `.cds-t24 .t24-table a.ant-typography` 特异度 (0,3,1) 高于 antd 自带的 `.ant-typography:hover` (0,2,0)
 * （v5 的 hash 类包在 `:where()` 里不计特异度）⇒ 悬停变色被一起压住。
 * 改动清单由 fe-06 出（.gate-scratch/evidence/fe-06.md §A233），本闸就是那份清单的算术版：
 * 对比度用 WCAG 相对亮度公式现算，特异度按 (id, 类/伪类, 类型) 三元组比。
 */
import { existsSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { describe, expect, it } from 'vitest';

interface CssRule {
  media: string | null;
  selector: string;
  decls: Record<string, string>;
  order: number;
}

function readSourceFromDisk(rel: string): string {
  let dir = process.cwd();
  for (let depth = 0; depth < 5; depth += 1) {
    for (const base of [dir, join(dir, 'cds-frontend')]) {
      const candidate = join(base, rel);
      if (existsSync(candidate)) return readFileSync(candidate, 'utf8');
    }
    const parent = dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  throw new Error(`找不到样式源文件 ${rel}（cwd=${process.cwd()}）`);
}

// 注释先整体剥掉：注释里的花括号会骗过括号计数；选择器组按逗号拆成单条。
function parseRules(src: string, out: CssRule[] = []): CssRule[] {
  const text = src.replace(/\/\*[\s\S]*?\*\//g, '');
  let i = 0;
  let order = 0;
  while (i < text.length) {
    const brace = text.indexOf('{', i);
    if (brace < 0) break;
    const header = text.slice(i, brace).trim().replace(/\s+/g, ' ');
    let depth = 1;
    let j = brace + 1;
    while (j < text.length && depth > 0) {
      if (text[j] === '{') depth += 1;
      else if (text[j] === '}') depth -= 1;
      j += 1;
    }
    const body = text.slice(brace + 1, j - 1);
    if (!header.startsWith('@')) {
      const decls: Record<string, string> = {};
      for (const line of body.split(';')) {
        const colon = line.indexOf(':');
        if (colon < 0) continue;
        decls[line.slice(0, colon).trim()] = line.slice(colon + 1).trim();
      }
      for (const selector of header.split(',')) {
        out.push({ media: null, selector: selector.trim(), decls, order: order });
      }
      order += 1;
    }
    i = j;
  }
  return out;
}

function hexToRgb(hex: string): [number, number, number] | null {
  const v = hex.trim().replace(/^#/, '');
  const full = v.length === 3 ? v.split('').map((c) => c + c).join('') : v;
  if (!/^[0-9a-fA-F]{6}$/.test(full)) return null;
  return [0, 2, 4].map((k) => Number.parseInt(full.slice(k, k + 2), 16)) as [number, number, number];
}

// WCAG 2.1 相对亮度 + 对比度：L = 0.2126R+0.7152G+0.0722B（线性化后），CR = (L1+0.05)/(L2+0.05)
function relativeLuminance(rgb: [number, number, number]): number {
  const [r, g, b] = rgb.map((channel) => {
    const c = channel / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(fg: string, bg: string): number | null {
  const a = hexToRgb(fg);
  const b = hexToRgb(bg);
  if (!a || !b) return null;
  const la = relativeLuminance(a);
  const lb = relativeLuminance(b);
  return Number((((Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05))).toFixed(2));
}

/** 特异度 (id, 类/属性/伪类, 类型/伪元素)；`:where()` 内容不计（antd v5 就是这么让特异度失效的）。 */
function specificity(selector: string): [number, number, number] {
  const s = selector.replace(/:where\([^)]*\)/g, '');
  const ids = (s.match(/#[\w-]+/g) ?? []).length;
  const classes = (s.match(/\.[\w-]+|\[[^\]]+\]|:(?!where)[\w-]+(\([^)]*\))?/g) ?? []).length;
  const types = (s.match(/(^|[\s>+~(])\b[a-z][\w-]*\b/g) ?? []).length;
  return [ids, classes, types];
}

function cmpSpecificity(a: [number, number, number], b: [number, number, number]): number {
  for (let k = 0; k < 3; k += 1) {
    if (a[k] !== b[k]) return a[k] - b[k];
  }
  return 0;
}

const AA_TEXT = 4.5;

const css = readSourceFromDisk('src/pages/Flow/taskDetail.css');
const rules = parseRules(css);
const vars = rules.find((rule) => rule.selector === '.cds-t24')?.decls ?? {};

// var(--t24-x) 解析成字面色，否则对比度算不出来
function resolveColor(value: string | undefined): string | undefined {
  let v = (value ?? '').trim();
  for (let step = 0; step < 4; step += 1) {
    const hit = /^var\((--[\w-]+)\)$/.exec(v);
    if (!hit) return v || undefined;
    v = (vars[hit[1]] ?? '').trim();
  }
  return undefined;
}

const ruleFor = (selector: string) => rules.find((rule) => rule.selector === selector);

const TAGS: [string, string][] = [
  ['状态标签 完成', '.cds-t24 .t24-tag.is-ok.ant-tag'],
  ['状态标签 失败', '.cds-t24 .t24-tag.is-err.ant-tag'],
  ['状态标签 进行中', '.cds-t24 .t24-tag.is-run.ant-tag'],
  ['状态标签 空闲', '.cds-t24 .t24-tag.is-idle.ant-tag'],
];
const CHIPS: [string, string][] = [
  ['统计芯片 green', '.cds-t24 .t24-chip.is-green'],
  ['统计芯片 blue', '.cds-t24 .t24-chip.is-blue'],
  ['统计芯片 purple', '.cds-t24 .t24-chip.is-purple'],
  ['统计芯片 orange', '.cds-t24 .t24-chip.is-orange'],
];
const TABLE_LINK_BASE = ['.cds-t24 .t24-table a.ant-typography', '.cds-t24 .t24-table .ant-table-tbody a'];

describe('A233 任务详情页状态标签/表内链接可达性', () => {
  it('非空解析：三枚状态标签、四枚芯片、灰注变量与表内链接规则都在', () => {
    for (const [, selector] of [...TAGS, ...CHIPS]) {
      expect(ruleFor(selector), `${selector} 规则不见了`).toBeDefined();
    }
    expect(resolveColor(vars['--t24-grey']), '--t24-grey 变量不见了').toBeDefined();
    for (const selector of TABLE_LINK_BASE) {
      expect(ruleFor(selector), `${selector} 规则不见了`).toBeDefined();
    }
  });

  it('三枚状态标签的字/底对比度过 AA 4.5:1（台账实测最低 2.78）', () => {
    for (const [label, selector] of TAGS) {
      const rule = ruleFor(selector);
      const fg = resolveColor(rule?.decls.color);
      const bg = resolveColor(rule?.decls.background ?? rule?.decls['background-color']);
      const ratio = fg && bg ? contrast(fg, bg) : null;
      expect(ratio, `${label}（${fg}/${bg}）算不出对比度`).not.toBeNull();
      expect(ratio!, `${label}（${fg} on ${bg} = ${ratio}:1）不过 AA ${AA_TEXT}:1`).toBeGreaterThanOrEqual(AA_TEXT);
    }
  });

  it('四枚统计芯片的前景色对浅底过 AA（台账未算的两枚一并校）', () => {
    for (const [label, selector] of CHIPS) {
      const rule = ruleFor(selector);
      const fg = resolveColor(rule?.decls.color);
      const bg = resolveColor(rule?.decls.background ?? rule?.decls['background-color']);
      const ratio = fg && bg ? contrast(fg, bg) : null;
      expect(ratio, `${label}（${fg}/${bg}）算不出对比度`).not.toBeNull();
      expect(ratio!, `${label}（${fg} on ${bg} = ${ratio}:1）不过 AA ${AA_TEXT}:1`).toBeGreaterThanOrEqual(AA_TEXT);
    }
  });

  it('灰注 --t24-grey 对卡片白底与 #f7f8fa 值盒底都要过 AA（台账 3.24 / 3.05）', () => {
    const grey = resolveColor(vars['--t24-grey']);
    const box = resolveColor(vars['--t24-box']) ?? '#ffffff';
    expect(contrast(grey ?? '', '#ffffff'), `--t24-grey ${grey} 对 #ffffff 不过 AA`).toBeGreaterThanOrEqual(AA_TEXT);
    expect(contrast(grey ?? '', box), `--t24-grey ${grey} 对 ${box} 不过 AA`).toBeGreaterThanOrEqual(AA_TEXT);
  });

  it('表内链接必须有同特异度或更高的 :hover（基线 (0,3,1) 会压住 antd 自带的悬停色）', () => {
    const hover = rules.filter((rule) => rule.selector.includes(':hover') && rule.selector.includes('a') && rule.decls.color);
    expect(hover.length, '表内链接没有任何 :hover 覆盖 ⇒ 悬停不变色，只能靠光标形状判断可点').toBeGreaterThan(0);
    for (const base of TABLE_LINK_BASE) {
      const baseRule = ruleFor(base);
      const covering = hover.filter(
        (rule) =>
          rule.selector.startsWith(base) &&
          cmpSpecificity(specificity(rule.selector), specificity(base)) >= 0 &&
          rule.order >= (baseRule?.order ?? 0) &&
          rule.decls.color !== undefined,
      );
      expect(covering.length, `没有一条 :hover 能压过 ${base}（特异度 ${JSON.stringify(specificity(base))}）`).toBeGreaterThan(0);
      const hoverColor = resolveColor(covering[0]?.decls.color);
      expect(contrast(hoverColor ?? '', '#ffffff'), `悬停色 ${hoverColor} 对白底不过 AA`).toBeGreaterThanOrEqual(AA_TEXT);
    }
  });

  it('必须有 :focus-visible 焦点环覆盖表内链接与表内按钮', () => {
    const focus = rules.filter((rule) => rule.selector.includes(':focus-visible'));
    expect(focus.length, 'taskDetail.css 里 :focus-visible 计数为 0 ⇒ 键盘用户看不到焦点落点').toBeGreaterThan(0);
    const outlines = focus.filter((rule) => (rule.decls.outline ?? '').includes('solid'));
    expect(outlines.length, ':focus-visible 没有画出 outline').toBeGreaterThan(0);
    const covers = (needle: RegExp) => outlines.some((rule) => needle.test(rule.selector));
    expect(covers(/a\.ant-typography|tbody a/), '焦点环没覆盖表内链接').toBe(true);
    expect(covers(/button/), '焦点环没覆盖表内按钮（如「详情」）').toBe(true);
  });
});
