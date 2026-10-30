/**
 * A229（静态源闸）：审计页日期区间的两个输入框定宽 55px，选完值之后文字被裁。
 * 台账 ocr-verified-findings.md §A229：`auditLog.css:158-159
 * .cds-audit-screen .cds-audit-filter .f-range .ant-picker-input:first-child { width: 55px; flex: none }`
 * —— 55px 的来由是原型**空值占位态**的墨迹宽（只够 4 字占位符），而 `YYYY-MM-DD` 在 14px 下
 * 要 ~78px，input 没有 ellipsis 能力 ⇒ 真选了日期就看不到自己选了什么；叠 `flex: none` 不可压。
 *
 * 闸的两条：前字段必须给到放得下一个日期的 min-width（并允许收缩），
 * picker 整体的 min-width 必须放得下「两个日期 + 分隔符 + 左右内距」——这条不是抄常数，
 * 是从同文件解析出来的分隔符内距与字段 min-width 现算的。
 */
import { readFileSync, existsSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { describe, expect, it } from 'vitest';

interface Rule {
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

function parseRules(src: string, out: Rule[] = []): Rule[] {
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
    if (!header.startsWith('@')) {
      const decls: Record<string, string> = {};
      for (const line of text.slice(brace + 1, j - 1).split(';')) {
        const colon = line.indexOf(':');
        if (colon < 0) continue;
        decls[line.slice(0, colon).trim()] = line.slice(colon + 1).trim();
      }
      out.push({ selector: header, decls });
    }
    i = j;
  }
  return out;
}

function px(value: string | undefined): number {
  const hit = /(-?[\d.]+)px/.exec(value ?? '');
  return hit ? Number.parseFloat(hit[1]) : Number.NaN;
}

/** flex 简写里的 flex-shrink：`none` 就是 0，单值（`1`/`auto`）按规范取 1，没写则用 antd 默认。 */
function shrinkFactor(flex: string | undefined): number {
  const parts = (flex ?? '').trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return 1;
  if (parts[0] === 'none') return 0;
  if (parts.length === 1) return 1;
  return Number.parseFloat(parts[1]);
}

const RANGE = '.cds-audit-screen .cds-audit-filter .f-range';
const css = readSourceFromDisk('src/pages/Audit/auditLog.css');
const rules = parseRules(css);
const input = rules.find((rule) => rule.selector === `${RANGE} .ant-picker-input:first-child`);
const picker = rules.find((rule) => rule.selector === `${RANGE} .ant-picker`);
const separator = rules.find((rule) => rule.selector === `${RANGE} .ant-picker-range-separator`);

// `YYYY-MM-DD` = 10 个等宽数字/连字符，14px 下实测区间 74–78px；取 78 作下限。
const DATE_VALUE_PX = 78;

describe('A229 审计页日期区间输入框的宽度', () => {
  it('三条被测规则都在源文件里（防改名把闸跑空）', () => {
    expect(input, '.ant-picker-input:first-child 规则不见了').toBeDefined();
    expect(picker, '.ant-picker 规则不见了').toBeDefined();
    expect(separator, '分隔符规则不见了').toBeDefined();
  });

  it('前字段不得有装不下日期的定宽（55px 是空值占位态的墨迹宽）', () => {
    const fixed = px(input?.decls.width);
    if (input?.decls.width && input.decls.width !== 'auto') {
      expect(fixed, `定宽 ${input?.decls.width} 会截断 YYYY-MM-DD`).toBeGreaterThanOrEqual(DATE_VALUE_PX);
    }
    expect(px(input?.decls['min-width']), '前字段缺 min-width').toBeGreaterThanOrEqual(DATE_VALUE_PX);
  });

  it('前字段要可压：flex: none 在 i18n/长文案下会把整行顶出卡体', () => {
    expect(shrinkFactor(input?.decls.flex), `flex: ${input?.decls.flex ?? '(未写)'}`).toBeGreaterThanOrEqual(1);
  });

  it('picker 的 min-width 必须放得下「两个日期 + 分隔符 + 左右内距」（数从同文件现算）', () => {
    const field = px(input?.decls['min-width']);
    const sepPadding = px(separator?.decls['padding-inline']);
    const separatorPx = Number.isNaN(sepPadding) ? 0 : sepPadding * 2 + 5;
    const innerPadding = 22;
    const needed = field * 2 + separatorPx + innerPadding;
    const min = px(picker?.decls['min-width']);
    expect(min, `picker min-width ${picker?.decls['min-width']} < 需要的 ${needed}px ⇒ 日期仍会被裁`).toBeGreaterThanOrEqual(
      needed,
    );
  });
});
