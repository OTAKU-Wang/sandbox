/**
 * A226（静态源闸）：三列定宽 `360px 360px 360px` 只在「装得下」的视口成立，但退档媒体查询
 * 写的是 `@media (max-width: 1439px)` ⇒ 1440 起就用三列定宽，而这一档装不下。
 * 台账 docs/ocr-review-2026-10-18/ocr-verified-findings.md §A226：三列要 3×360 + 2×24 = **1128**，
 * 而 1440 视口的可用内容宽只有 **1088** ⇒ 定宽成立的视口下限高于断点，中间那一档必横向溢出。
 *
 * 闸不抄台账结论，自己算：
 * · 需要的宽度从**被测文件**解析（列宽、列间距、表单 padding-inline）；
 * · 外壳扣减从 `src/index.css` 的定标注释取（左栏右边界 x=219、`.cds-content` 内距 23/25、
 *   1440 视口 → documentElement.clientWidth **1403** 的定标）。两套口径都校：
 *   - 浏览器口径：扣 经典滚动条 15（index.css:187 注）；
 *   - WebView 定标口径：1440 → clientWidth 1403，即外壳总扣减 37（含滚动条，index.css:187-188）。
 * 台账标题写的 1488 与其自身加数（1128+48+48+219+15 = **1458**）对不上，WebView 定标口径给 **1480**；
 * 取两者较大值作下限，修法满足更严的一档即同时满足较松的一档。
 */
import { existsSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { describe, expect, it } from 'vitest';

interface CssRule {
  media: string | null;
  mediaMaxWidth: number | null;
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

// 注释整体剥掉：注释里的花括号会骗过下面的括号计数；@media 递归一层。
function parseRules(src: string, media: string | null = null, out: CssRule[] = []): CssRule[] {
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
    if (header.startsWith('@')) {
      parseRules(body, header, out);
    } else {
      const decls: Record<string, string> = {};
      for (const line of body.split(';')) {
        const colon = line.indexOf(':');
        if (colon < 0) continue;
        decls[line.slice(0, colon).trim()] = line.slice(colon + 1).trim();
      }
      const cond = /max-width:\s*(\d+(?:\.\d+)?)px/.exec(media ?? '');
      out.push({
        media,
        mediaMaxWidth: cond ? Number.parseFloat(cond[1]) : null,
        selector: header,
        decls,
        order: out.length,
      });
    }
    i = j;
  }
  return out;
}

function pxOf(value: string | undefined): number | null {
  const hit = /^(-?[\d.]+)px$/.exec((value ?? '').trim());
  return hit ? Number.parseFloat(hit[1]) : null;
}

interface Tracks {
  fixedPx: number[];
  fluid: boolean;
  count: number;
}

// 只认 `360px 360px 360px` 这种逐轨定宽；出现 fr/minmax/repeat 记为流式。
function parseTracks(value: string | undefined): Tracks {
  const tracks = (value ?? '').trim().split(/\s+/).filter(Boolean);
  const fixedPx: number[] = [];
  let fluid = false;
  let count = 0;
  for (const track of tracks) {
    const px = pxOf(track);
    if (px !== null) {
      fixedPx.push(px);
      count += 1;
    } else {
      fluid = true;
      // repeat(3, …) 这类写成一行，无法逐轨计数；对流式档只校「有没有再塞定宽」
      const rep = /^repeat\((\d+),/.exec(track);
      if (rep) count += Number.parseInt(rep[1], 10);
      else count += 1;
    }
  }
  return { fixedPx, fluid, count };
}

function columnGapOf(decls: Record<string, string>): number {
  const explicit = pxOf(decls['column-gap']);
  if (explicit !== null) return explicit;
  // `gap: <row> <column>`；单值时两轴同值
  const parts = (decls.gap ?? '').trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return 0;
  return pxOf(parts.length > 1 ? parts[1] : parts[0]) ?? 0;
}

const FORM = '.cds-api-create-form';
const ROW3 = '.cds-api-create-form .cds-row3';

const css = readSourceFromDisk('src/pages/Flow/deliveryOpenapi.css');
const rules = parseRules(css);
const baseRow3 = rules.find((rule) => rule.media === null && rule.selector === ROW3);
const baseForm = rules.find((rule) => rule.media === null && rule.selector === FORM);
const tiers = rules.filter((rule) => rule.mediaMaxWidth !== null && rule.selector === ROW3);

// 外壳扣减：四个常数全部取 src/index.css 的定标注释（逐条注了行号），不是拍的。
/** index.css:182「原型内容卡左边界 x=243，左栏右边界 x=219」 */
const SIDEBAR_RIGHT_EDGE = 219;
/** index.css:186 `.cds-content { padding: 23px 25px 23px 23px }` ⇒ 左右合计 48 */
const CONTENT_PADDING_INLINE = 23 + 25;
/** index.css:187「经典滞动条 15px」 */
const SCROLLBAR = 15;
/** index.css:187-188 定标：innerWidth 1440 时 documentElement.clientWidth **1403** ⇒ 外壳合计扣 37（已含滚动条） */
const WEBVIEW_CHROME = 1440 - 1403;

const tracks = parseTracks(baseRow3?.decls['grid-template-columns']);
const colGap = columnGapOf(baseRow3?.decls ?? {});
const formPaddingInline = (pxOf(baseForm?.decls['padding-inline']) ?? 0) * 2;
// Σ列宽 + (n−1)×列间距
const REQUIRED = tracks.fixedPx.reduce((sum, w) => sum + w, 0) + Math.max(0, tracks.fixedPx.length - 1) * colGap;
/** 浏览器口径：三列装得下的最小视口 */
const MIN_BROWSER_VIEWPORT = REQUIRED + formPaddingInline + CONTENT_PADDING_INLINE + SIDEBAR_RIGHT_EDGE + SCROLLBAR;
// WebView 定标口径（更严）：同一式子把 15 换成实测 37
const MIN_WEBVIEW_VIEWPORT = REQUIRED + formPaddingInline + CONTENT_PADDING_INLINE + SIDEBAR_RIGHT_EDGE + WEBVIEW_CHROME;
// 定宽档从「最宽那条退档断点 + 1」起生效
const FIXED_BAND_FROM = (tiers.length ? Math.max(...tiers.map((tier) => tier.mediaMaxWidth ?? 0)) : 0) + 1;

describe('A226 OpenAPI 导出表单三列定宽的成立区间', () => {
  it('非空解析：基线三列规则、表单内距与两条退档规则都在（防止改名把闸跑空）', () => {
    expect(baseRow3, `${ROW3} 基线规则不见了`).toBeDefined();
    expect(baseForm, `${FORM} 规则不见了`).toBeDefined();
    expect(tracks.fixedPx, '基线三列必须还是逐轨定宽写法（改成流式就该重写本闸）').toHaveLength(3);
    expect(tiers, '两条退档媒体查询（2 列 / 1 列）至少要有').toHaveLength(2);
    // 1128 = 3×360 + 2×24：台账 §A226 的加数，这里从文件现算再对一次
    expect(REQUIRED, `三列需要的内容宽应为 3×360 + 2×24 = 1128，实测 ${REQUIRED}`).toBe(1128);
  });

  it('浏览器口径：定宽档的起始视口必须装得下 1128（1440 档只有 1110 可用）', () => {
    expect(FIXED_BAND_FROM,
      `三列定宽从 ${FIXED_BAND_FROM}px 起生效，但浏览器口径要 ${MIN_BROWSER_VIEWPORT}px 才放得下 ` +
        `⇒ ${FIXED_BAND_FROM}–${MIN_BROWSER_VIEWPORT - 1} 整档横向溢出`).toBeGreaterThanOrEqual(MIN_BROWSER_VIEWPORT);
  });

  it('WebView 定标口径：同一档还要过 index.css 实测的 clientWidth 扣减（更严）', () => {
    expect(FIXED_BAND_FROM,
      `按 index.css 定标（1440 视口 clientWidth=1403）可用内容宽只有 ${1440 - WEBVIEW_CHROME - SIDEBAR_RIGHT_EDGE - CONTENT_PADDING_INLINE - formPaddingInline}，` +
        `三列要 ${REQUIRED} ⇒ 定宽档下限应为 ${MIN_WEBVIEW_VIEWPORT}，实际断点只到 ${FIXED_BAND_FROM}`).toBeGreaterThanOrEqual(
      MIN_WEBVIEW_VIEWPORT,
    );
  });

  it('退档后的每一档必须是流式（1fr/minmax），不得换一套更大的定宽继续溢出', () => {
    for (const tier of tiers) {
      const tierTracks = parseTracks(tier.decls['grid-template-columns']);
      expect(tierTracks.fixedPx, `${tier.media} 里又塞了定宽 ${tier.decls['grid-template-columns']}`).toHaveLength(0);
      expect(tierTracks.fluid, `${tier.media} 的列写法不是流式：${tier.decls['grid-template-columns']}`).toBe(true);
    }
  });

  it('媒体查询必须在基线之后、且按断点降序（否则窄档被宽档压住 = 留一档没人管）', () => {
    for (const tier of tiers) {
      expect(tier.order, `${tier.media} 写在基线规则之前，会被基线覆盖`).toBeGreaterThan(baseRow3?.order ?? -1);
    }
    const ascending = tiers.every(
      (tier, idx) => idx === 0 || (tier.mediaMaxWidth ?? 0) <= (tiers[idx - 1]?.mediaMaxWidth ?? 0),
    );
    expect(ascending, `媒体查询断点必须降序，实际 ${tiers.map((tier) => tier.mediaMaxWidth).join(' / ')}`).toBe(true);
    // 每一档之间也得有下界：最窄一档仍要覆盖到 0，所以只校「降序 + 都 ≤ 定宽档下限」
    for (const tier of tiers) {
      expect(tier.mediaMaxWidth ?? 0, `${tier.media} 比定宽档下限还宽，会和定宽档重叠`).toBeLessThan(FIXED_BAND_FROM);
    }
  });
});
