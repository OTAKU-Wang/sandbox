/**
 * A223（静态源闸）：notebookWorkbench.css 全文件零 `:focus-visible`，而图标按钮自己把
 * border/background 抹平了 ⇒ 键盘用户在这页（文件树 / tab / 图标按钮 / 文字菜单）看不到焦点落在哪。
 * 台账 ocr-verified-findings.md §A223：`grep -c 'focus-visible' notebookWorkbench.css` = 0。
 *
 * 闸的口径不是「有任意一条 focus-visible」，而是台账要求的统一焦点环：挂在 `.nbw-root` 的
 * 后代选择器上（覆盖页内全部可聚焦元素，而不是逐个类补一条），且必须真的画出 outline。
 * 焦点环本身是否可见属浏览器渲染，无法在 jsdom 断言 ⇒ 见证据块的 visual-only 说明。
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

const css = readSourceFromDisk('src/pages/Flow/notebookWorkbench.css');
const rules = parseRules(css);

/** 台账点名的可交互类：焦点环必须覆盖到它们（靠 .nbw-root 后代选择器，而不是逐个补）。 */
const INTERACTIVE = ['.nbw-menu-label', '.nbw-lang-pick', '.nbw-folder-toggle', '.nbw-tab', '.nbw-icon-btn'];

const focusRules = rules.filter((rule) => rule.selector.includes(':focus-visible'));

describe('A223 调试环境页的键盘焦点环', () => {
  it('前提成立：图标按钮确实自己抹掉了 border 与 background', () => {
    const icon = rules.find((rule) => rule.selector === '.nbw-icon-btn');
    expect(icon).toBeDefined();
    expect(icon?.decls.border).toBe('0');
    expect(icon?.decls.background).toBe('transparent');
  });

  it('台账点名的可交互类都在本文件里（防选择器改名把闸跑空）', () => {
    for (const klass of INTERACTIVE) {
      expect(rules.some((rule) => rule.selector.startsWith(klass)), klass).toBe(true);
    }
  });

  it('全文件不再零 :focus-visible', () => {
    expect(focusRules.length).toBeGreaterThan(0);
  });

  it('焦点环挂在 .nbw-root 后代上（一条覆盖全页可聚焦元素，不是逐类补）', () => {
    const scoped = focusRules.filter((rule) => /^\.nbw-root\s/.test(rule.selector));
    expect(scoped.length).toBeGreaterThan(0);
  });

  it('这条规则真的画出环：outline 为实线且非 none，并给 outline-offset', () => {
    const ring = focusRules.find((rule) => /^\.nbw-root\s/.test(rule.selector));
    expect(ring?.decls.outline, '缺 outline').toMatch(/\d+px\s+solid/);
    expect(ring?.decls.outline).not.toMatch(/\bnone\b/);
    expect(ring?.decls['outline-offset']).toBeTruthy();
  });
});
