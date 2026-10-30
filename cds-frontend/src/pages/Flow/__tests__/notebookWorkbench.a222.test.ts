/**
 * A222（静态源闸）：调试环境页样式零断点 + 两栏定高/定宽写死。
 * 台账 ocr-verified-findings.md §A222：`grep -c '@media' notebookWorkbench.css` = 0，且
 * `.nbw-columns { height: calc(100vh - 250px); min-height: 700px }`——视口高 < 950 时 calc 值
 * 不足 700，min-height 反压使实际高超过可用高，外层 overflow:hidden 直接裁切；
 * 左栏 `flex: 0 0 250px` 定宽 + 零断点 ⇒ 窄宽下宽度全给左树。
 *
 * jsdom 不做布局，测不了像素，闸的是「定高/定宽成立的区间有没有写进样式」：
 * 700 的地板只允许出现在 `@media (min-height: N)` 且 N ≥ 700 + 250 = 950 里（门槛低于
 * 地板+减数时地板又会反压过 calc 值，等于没修），250 定宽必须在窄断点被放开。
 *
 * 读文件用 node:fs：vitest 配置 css:false ⇒ `../x.css?raw` 在本池返回空串（实测 0 字节）。
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

/** 只解「选择器 + 声明 + 是否在某条 @media 内」，嵌套一层足够覆盖本文件。 */
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

function mediaValue(condition: string, feature: string): number | null {
  const hit = new RegExp(`${feature}\\s*:\\s*(\\d+)px`).exec(condition);
  return hit ? Number(hit[1]) : null;
}

const css = readSourceFromDisk('src/pages/Flow/notebookWorkbench.css');
const source = css.replace(/\/\*[\s\S]*?\*\//g, '');
const rules = parseRules(css);
const columnsRules = rules.filter((rule) => rule.selector === '.nbw-columns');

describe('A222 调试环境页的自适应区间', () => {
  it('源文件里确实有这两条被测规则（防空读把闸跑成恒真）', () => {
    expect(columnsRules.length).toBeGreaterThan(0);
    expect(rules.some((rule) => rule.selector === '.nbw-left')).toBe(true);
  });

  it('本文件不再零断点', () => {
    expect(source).toMatch(/@media/);
  });

  it('700 的定高地板不得无条件挂着（只允许 min-height: 0）', () => {
    const floors = columnsRules
      .filter((rule) => !rule.media && rule.decls['min-height'])
      .map((rule) => Number.parseInt(rule.decls['min-height'], 10));
    expect(floors.every((px) => px <= 0)).toBe(true);
  });

  it('定高判据要写清成立区间：地板 min-height 只允许出现在门槛 ≥ 地板+250 的媒体查询里', () => {
    const floors = columnsRules.filter((rule) => rule.media && rule.decls['min-height']);
    expect(floors.length).toBeGreaterThan(0);
    for (const floor of floors) {
      const px = Number.parseInt(floor.decls['min-height'], 10);
      if (px <= 0) continue; // 地板为 0 时不可能反压过 calc 值，无需门槛
      const gate = mediaValue(floor.media as string, 'min-height');
      expect(gate, `min-height ${floor.decls['min-height']} 缺视口高门槛`).not.toBeNull();
      expect(gate as number).toBeGreaterThanOrEqual(px + 250);
    }
  });

  it('高度用 dvh（iframe / 地址栏伸缩下 vh 不是可用高），vh 只作回退', () => {
    // 两条 height 声明是刻意的回退写法（同键会被上面的解析去重），故对规则原文断言
    const blocks = rules.filter((rule) => rule.selector === '.nbw-columns' && rule.decls.height);
    expect(blocks.length).toBeGreaterThan(0);
    expect(blocks.some((rule) => Object.values(rule.decls).join(';').includes('dvh'))).toBe(true);
  });

  it('窄宽断点放开左栏 250 定宽并让两栏换行', () => {
    const narrow = rules.filter((rule) => rule.media && mediaValue(rule.media, 'max-width') !== null);
    const left = narrow.filter((rule) => rule.selector === '.nbw-left');
    expect(left.length).toBeGreaterThan(0);
    expect(
      left.some((rule) => rule.decls.width === 'auto' || /(^|\s)1 1 100%(\s|$)/.test(rule.decls.flex ?? '')),
    ).toBe(true);
    expect(
      narrow.some((rule) => rule.selector === '.nbw-columns' && rule.decls['flex-wrap'] === 'wrap'),
    ).toBe(true);
  });
});
