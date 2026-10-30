/**
 * A209（静态源闸）：`@media (max-width:1280px)` 里的 `.cds-trio > *` 覆盖被 base 的
 * `> *:nth-child(3)` 压住 ⇒ ≤1280 换行后第三列仍按内容宽，同排列宽不齐。
 * 台账 ocr-verified-findings.md §A209（逐行亲验）：
 *   base  `…trio > *`             (0,2,0) flex: 0 0 373px
 *   base  `…trio > *:nth-child(3)` (0,3,0) flex: 1 1 auto
 *   media `…trio > *`             (0,2,0) flex: 1 1 300px   ← 媒体查询不加特异性，仍然输
 *
 * 两条可机检的判据：
 * 1) 断点内改了 `> *` 就必须补一条**同特异性**的 `> *:nth-child(3)`（否则等于没改）；
 * 2) base 的前两列不许是 `0 0 <定值>`（不涨不缩）：1281–1439 那一段完全没有弹性兼容，
 *    必须允许收缩（`flex-shrink ≥ 1`），同时不许 grow（`flex-grow = 0`）——第三列 basis 是 auto，
 *    一旦前两列也 grow，1440 档的 373px 列宽真值会被三等分掉。
 */
import { readFileSync, existsSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { describe, expect, it } from 'vitest';

interface Rule {
  selector: string;
  decls: Record<string, string>;
  media: string | null;
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

function parseRules(src: string, media: string | null = null, out: Rule[] = []): Rule[] {
  const text = src.replace(/\/\*[\s\S]*?\*\//g, '');
  let i = 0;
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
    if (header.startsWith('@media')) {
      parseRules(body, header.replace(/^@media\s*/, ''), out);
    } else if (!header.startsWith('@')) {
      const decls: Record<string, string> = {};
      for (const line of body.split(';')) {
        const colon = line.indexOf(':');
        if (colon < 0) continue;
        decls[line.slice(0, colon).trim()] = line.slice(colon + 1).trim();
      }
      out.push({ selector: header, decls, media });
    }
    i = j;
  }
  return out;
}

/** CSS 特异性 (id, 类/属性/伪类, 元素/伪元素)；`*` 与组合符不计。 */
function specificity(selector: string): [number, number, number] {
  const s = selector.replace(/>|\+|~/g, ' ');
  const ids = (s.match(/#[\w-]+/g) ?? []).length;
  const classes = (s.match(/\.[\w-]+|\[[^\]]+\]|:(?!:)[\w-]+(\([^)]*\))?/g) ?? []).length;
  const elements = (s.match(/(^|[\s>+~])(?![*#.\[:])[\w-]+/g) ?? []).length;
  return [ids, classes, elements];
}

/** 可比较的特异性整数：a*100 + b*10 + c（本仓选择器不会超过这个量级）。 */
function rank(selector: string): number {
  const [a, b, c] = specificity(selector);
  return a * 100 + b * 10 + c;
}

const css = readSourceFromDisk('src/pages/Flow/devTaskCreate.css');
const rules = parseRules(css);
const TRIO = '.cds-dev-task-create .cds-trio';
const trioAll = rules.filter((rule) => rule.selector === `${TRIO} > *`);
const trioThird = rules.filter((rule) => rule.selector === `${TRIO} > *:nth-child(3)`);

describe('A209 建任务页三列簇的断点覆盖与弹性', () => {
  it('两条被测选择器都在源文件里，且 nth-child(3) 特异度确实更高（缺陷前提）', () => {
    expect(trioAll.length).toBeGreaterThan(0);
    expect(trioThird.length).toBeGreaterThan(0);
    const base = trioAll[0];
    expect(rank(trioThird[0].selector)).toBeGreaterThan(rank(base.selector));
  });

  it('base 的 `> *` 不是「不涨不缩的定宽」（1281–1439 需要能收缩）', () => {
    const base = trioAll.find((rule) => !rule.media && rule.decls.flex);
    expect(base, 'base 缺 flex 声明').toBeDefined();
    const [grow, shrink] = (base?.decls.flex ?? '').split(/\s+/).map(Number);
    expect(Number.isFinite(shrink), `flex 写法解析不出 shrink：${base?.decls.flex}`).toBe(true);
    expect(shrink).toBeGreaterThanOrEqual(1);
    expect(grow).toBe(0);
  });

  it('断点内改了 `> *` 的 flex，就必须补同特异性的 `> *:nth-child(3)`（媒体查询不加特异度）', () => {
    const mediaScopes = [...new Set(trioAll.filter((rule) => rule.media).map((rule) => rule.media as string))];
    expect(mediaScopes.length, '1280 断点里找不到 trio 覆盖').toBeGreaterThan(0);
    for (const scope of mediaScopes) {
      const wide = trioAll.find((rule) => rule.media === scope && rule.decls.flex);
      expect(wide, `${scope} 的 > * 缺 flex`).toBeDefined();
      const patched = trioThird.find((rule) => rule.media === scope && rule.decls.flex);
      expect(patched, `${scope} 缺同特异性的 > *:nth-child(3) 覆盖 ⇒ 第三列仍按内容宽`).toBeDefined();
      expect(patched?.decls.flex).toBe(wide?.decls.flex);
      const baseThird = trioThird.find((rule) => !rule.media)!;
      expect(rank(patched!.selector)).toBeGreaterThanOrEqual(rank(baseThird.selector));
      expect(rules.indexOf(patched!)).toBeGreaterThan(rules.indexOf(baseThird));
    }
  });

  it('断点内的列宽不许再是定宽（`0 0 <px>` 在换行态会把第三列顶出卡体）', () => {
    for (const rule of [...trioAll, ...trioThird].filter((r) => r.media && r.decls.flex)) {
      const [grow, shrink] = rule.decls.flex.split(/\s+/).map(Number);
      expect(shrink, `${rule.media} 的 ${rule.selector} 不可收缩`).toBeGreaterThanOrEqual(1);
      expect(Number.isNaN(grow)).toBe(false);
    }
  });
});
