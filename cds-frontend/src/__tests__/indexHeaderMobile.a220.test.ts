/**
 * A220（样式源闸）：顶栏折叠到移动端时忘了把 `line-height: 64px` 一起收掉。
 * 台账 ocr-verified-findings.md §A220：基态 `index.css:107 .cds-header { line-height: 64px }`
 * 是桌面定高 64px 顶栏的垂直居中手段；`@media (max-width: 767px)` 里只重置了
 * `height: auto / min-height / padding / align-items`，行高仍然继承 64px，
 * 而同一个断点里 `.cds-brand-text span { white-space: normal }` 又允许品牌字换行
 * ⇒ 一折行就是 64px/行，顶栏被撑开并被 overflow 裁切，顶栏按钮/图标同样继承 64px 行高。
 * 闸算的是**折叠态实际生效的行高**（媒体查询里没写就继承基态），不是抄一个常数。
 */
import { readFileSync, existsSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { describe, expect, it } from 'vitest';

interface RuleBlock {
  selector: string;
  decls: Record<string, string>;
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

function parseDeclarations(body: string): Record<string, string> {
  const decls: Record<string, string> = {};
  for (const line of body.split(';')) {
    const colon = line.indexOf(':');
    if (colon < 0) continue;
    decls[line.slice(0, colon).trim()] = line.slice(colon + 1).trim();
  }
  return decls;
}

/** 把 `@media (…) { … }` 整块切出来（含嵌套规则的原始文本）。 */
function mediaBlocks(src: string): { prelude: string; body: string }[] {
  const text = src.replace(/\/\*[\s\S]*?\*\//g, '');
  const blocks: { prelude: string; body: string }[] = [];
  const re = /@media[^{]*/g;
  let match: RegExpExecArray | null;
  while ((match = re.exec(text))) {
    const brace = text.indexOf('{', match.index + match[0].length);
    if (brace < 0) break;
    let depth = 1;
    let j = brace + 1;
    while (j < text.length && depth > 0) {
      if (text[j] === '{') depth += 1;
      else if (text[j] === '}') depth -= 1;
      j += 1;
    }
    blocks.push({ prelude: match[0].trim().replace(/\s+/g, ' '), body: text.slice(brace + 1, j - 1) });
    re.lastIndex = j;
  }
  return blocks;
}

function ruleIn(body: string, selector: string): RuleBlock | undefined {
  const text = body.replace(/\/\*[\s\S]*?\*\//g, '');
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
    if (header === selector) return { selector, decls: parseDeclarations(text.slice(brace + 1, j - 1)) };
    i = j;
  }
  return undefined;
}

function topLevelRules(src: string): RuleBlock[] {
  const text = src.replace(/\/\*[\s\S]*?\*\//g, '');
  const rules: RuleBlock[] = [];
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
    if (!header.startsWith('@') && !header.includes(',')) {
      rules.push({ selector: header, decls: parseDeclarations(text.slice(brace + 1, j - 1)) });
    }
    i = j;
  }
  return rules;
}

/**
 * 行高的实际生效值 → px。`auto`/`normal` 与无单位倍数按字体度量（14px × 1.5 上界）计，
 * em/倍数值乘以 14px，px 直接取值；解析不出来返回 NaN（调用方判失败）。
 */
function lineHeightPx(value: string | undefined): number {
  const raw = (value ?? '').trim();
  if (raw === '' || raw === 'normal' || raw === 'auto') return 21;
  const px = /^(-?[\d.]+)px$/.exec(raw);
  if (px) return Number.parseFloat(px[1]);
  const unitless = /^(-?[\d.]+)$/.exec(raw);
  if (unitless) return Number.parseFloat(unitless[1]) * 14;
  const em = /^(-?[\d.]+)em$/.exec(raw);
  if (em) return Number.parseFloat(em[1]) * 14;
  return Number.NaN;
}

const MOBILE_PRELUDE = '@media (max-width: 767px)';
const css = readSourceFromDisk('src/index.css');
const base = topLevelRules(css).find((rule) => rule.selector === '.cds-header');
const mobile = mediaBlocks(css).find((block) => block.prelude === MOBILE_PRELUDE);
const mobileHeader = mobile ? ruleIn(mobile.body, '.cds-header') : undefined;
const mobileBrandWrap = mobile ? ruleIn(mobile.body, '.cds-brand-text span') : undefined;

// 顶栏内单行内容的可接受行高上限：44px 是触控目标高度，超过就说明定高行的行高漏进了折叠态。
const MAX_COLLAPSED_LINE_PX = 44;

describe('A220 顶栏折叠到移动端后的行高', () => {
  it('被测三处都还在源文件里（防改名把闸跑空）', () => {
    expect(base, '基态 .cds-header 规则不见了').toBeDefined();
    expect(mobile, `断点 ${MOBILE_PRELUDE} 不见了`).toBeDefined();
    expect(mobileHeader, '断点内的 .cds-header 规则不见了').toBeDefined();
    expect(mobileBrandWrap, '断点内的 .cds-brand-text span 规则不见了').toBeDefined();
  });

  it('基态确实靠 64px 行高做定高垂直居中（本闸的前提）', () => {
    expect(lineHeightPx(base?.decls['line-height'])).toBeGreaterThanOrEqual(60);
    expect(base?.decls.height).toBeDefined();
  });

  it('同一断点里品牌字允许换行 ⇒ 漏掉的行高会被逐行放大', () => {
    expect(mobileBrandWrap?.decls['white-space']).toBe('normal');
  });

  it('折叠态实际生效的行高不得超过触控行高（媒体查询里没写＝继承 64px 即红）', () => {
    const effective = lineHeightPx(mobileHeader?.decls['line-height'] ?? base?.decls['line-height']);
    expect(Number.isNaN(effective), '行高取值无法解析').toBe(false);
    expect(
      effective,
      `折叠态行高 ${effective}px：品牌字一折行就按整倍撑开顶栏并被 overflow 裁切`,
    ).toBeLessThanOrEqual(MAX_COLLAPSED_LINE_PX);
  });

  it('折叠态仍保留 64px 触达下限（不能把 min-height 一起删掉来「修」行高）', () => {
    expect(lineHeightPx(mobileHeader?.decls['min-height'])).toBeGreaterThanOrEqual(56);
  });
});
