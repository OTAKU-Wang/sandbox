/**
 * A224（静态源闸）：「状态未知（接口未接入）」与「已关闭」在 UI 上像素级同色。
 * 台账 ocr-verified-findings.md §A224：notebookWorkbench.css 里
 * `.nbw-chip-off { color:#8c8c8c; background:#f5f5f5 }` 与 `.nbw-chip-unknown {...同}` 逐字相同；
 * 项目 honest-degradation 铁律要求未接入/降级态**可区分地披露**，屏幕上把「未知=接口没接」
 * 与「已关闭=真关」看成同一个，等于把降级状态并成真值。
 *
 * 闸口径：两条类的声明集合不许逐字相同，且 unknown 必须换掉**人眼可读的两个通道**
 * （字色 + 底色），再叠一个形状差异（虚线边）——只改 1/255 的近似色不算可区分。
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
const off = rules.find((rule) => rule.selector === '.nbw-chip-off');
const unknown = rules.find((rule) => rule.selector === '.nbw-chip-unknown');

describe('A224 降级态（未知/未接入）必须与「已关闭」可区分', () => {
  it('两条 chip 类都在源文件里（防改名把闸跑空）', () => {
    expect(off, '.nbw-chip-off 不见了').toBeDefined();
    expect(unknown, '.nbw-chip-unknown 不见了').toBeDefined();
  });

  it('两条类的声明集合不得逐字相同', () => {
    expect(unknown?.decls).not.toEqual(off?.decls);
  });

  it('字色与底色两个通道都要换掉（同色＝像素级同色缺陷）', () => {
    expect(unknown?.decls.color).toBeTruthy();
    expect(unknown?.decls.color).not.toBe(off?.decls.color);
    expect(unknown?.decls.background).toBeTruthy();
    expect(unknown?.decls.background).not.toBe(off?.decls.background);
  });

  it('再叠一个形状差异：unknown 带虚线边（灰底实底 vs 虚线框）', () => {
    expect(unknown?.decls.border).toMatch(/dashed/);
  });
});
