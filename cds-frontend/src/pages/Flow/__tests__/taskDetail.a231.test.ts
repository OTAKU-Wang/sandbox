/**
 * A231（静态源闸）：任务详情页整屏零断点 + 三处定宽/定尺寸。
 * 台账 docs/ocr-review-2026-10-18/ocr-verified-findings.md §A231：`grep -c '@media' taskDetail.css` = 0（473 行），
 * `grep 'max-width' 全文件 0 命中`，其中最钢的是 `:466 .cds-t24-modal.ant-modal { width: 720px !important; top: 64px }`
 * —— antd Modal 走 portal 挂在 body 上，不受 `.cds-t24` 约束，720 定宽没有视口兑底 ⇒ 视口 <720 直接横向滚动。
 *
 * 闸校的是「定宽/定尺寸成立区间自洽」，不是「有没有断点」：
 * 1) 弹窗的定宽必须有 vw 相对的 max-width 兑底（内联 style 只有 width，没有 max-width ⇒ clamp 能夹住）；
 * 2) 全文任何 ≥240px 的定宽 width 同条必须带 max-width（防以后再加一条没有兑底的定宽）；
 * 3) 卡 E 的 328px 定宽网格轨必须在窄档被释放，且释放点按算术不能太窄、也不能吃掉 1440 原型档；
 * 4) 弹窗顶距 64px 在矮屏会把 footer 顶出屏，窄高档必须真的把总高压回视口内。
 */
import { existsSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { describe, expect, it } from 'vitest';

interface CssRule {
  media: string | null;
  mediaMaxWidth: number | null;
  mediaMaxHeight: number | null;
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

// 注释先整体剥掉：注释里的花括号会骗过下面的括号计数；@media 递归一层。
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
      const maxW = /max-width:\s*(\d+(?:\.\d+)?)px/.exec(media ?? '');
      const maxH = /max-height:\s*(\d+(?:\.\d+)?)px/.exec(media ?? '');
      out.push({
        media,
        mediaMaxWidth: maxW ? Number.parseFloat(maxW[1]) : null,
        mediaMaxHeight: maxH ? Number.parseFloat(maxH[1]) : null,
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
  const hit = /^(-?[\d.]+)px/.exec((value ?? '').replace(/!important/, '').trim());
  return hit ? Number.parseFloat(hit[1]) : null;
}

/** 把 `62vh` / `calc(100vh - 220px)` / `420px` 在给定视口高度下算成 px；算不出返回 null。 */
function resolveVh(value: string | undefined, viewportHeight: number): number | null {
  const v = (value ?? '').trim();
  const calc = /^calc\(\s*100vh\s*([+-])\s*([\d.]+)px\s*\)$/.exec(v);
  if (calc) {
    const delta = Number.parseFloat(calc[2]);
    return calc[1] === '-' ? viewportHeight - delta : viewportHeight + delta;
  }
  const vh = /^([\d.]+)vh$/.exec(v);
  if (vh) return (viewportHeight * Number.parseFloat(vh[1])) / 100;
  return pxOf(v);
}

function fixedTracks(value: string | undefined): number[] {
  return (value ?? '')
    .trim()
    .split(/\s+/)
    .filter(Boolean)
    .map((track) => pxOf(track))
    .filter((w): w is number => w !== null);
}

const MODAL = '.cds-t24-modal.ant-modal';
const MODAL_BODY = '.cds-t24-modal .ant-modal-body';
const RES = '.cds-t24 .t24-res';
const TILE_ROW = '.cds-t24 .t24-tile-row';

const css = readSourceFromDisk('src/pages/Flow/taskDetail.css');
const rules = parseRules(css);
const baseOf = (selector: string) => rules.find((rule) => rule.media === null && rule.selector === selector);

const modal = baseOf(MODAL);
const modalBody = baseOf(MODAL_BODY);
const res = baseOf(RES);

// 横向扣减（左栏右边界 x=219 与 .cds-content 内距取 index.css:182/186 定标注释；
// 1440→clientWidth 1403 的实测扣减 37 已含滚动条，index.css:187-188；卡身内缩 24×2 见本文件头注）
const SIDEBAR_RIGHT_EDGE = 219;
const CONTENT_PADDING_INLINE = 23 + 25;
const CARD_PADDING_INLINE = 24 * 2;
const WEBVIEW_CHROME = 1440 - 1403;
const CHROME_INLINE = SIDEBAR_RIGHT_EDGE + CONTENT_PADDING_INLINE + CARD_PADDING_INLINE + WEBVIEW_CHROME;
// 两条流式列在配额盒（justify-content: space-between）下还能读数可读的下限
const MIN_FLUID_TRACK = 200;
// 弹窗非体部分（标题行 22 + 上下内距、体 padding、脚 32+8）的保守上界
const MODAL_CHROME = 150;

const resTracks = fixedTracks(res?.decls['grid-template-columns']);
const resGap = pxOf(res?.decls['column-gap'] ?? res?.decls.gap) ?? 0;
// 328 定宽轨 + 两条流式列各留 MIN_FLUID_TRACK + 间距 + 外壳扣减 ⇒ 释放点不得低于此
const RES_RELEASE_MIN = (resTracks[0] ?? 0) + resGap * 2 + MIN_FLUID_TRACK * 2 + CHROME_INLINE;

const modalTop = pxOf(modal?.decls.top) ?? 0;
const bodyCapVh = /([\d.]+)vh/.exec(modalBody?.decls['max-height'] ?? '');
const baseCapFraction = bodyCapVh ? Number.parseFloat(bodyCapVh[1]) / 100 : 0;
// 顶距 + 非体 + capFraction·H > H ⇒ 溢出；解出 H 的下界 = 需要窄高释放的那一档
const SHORT_VIEWPORT_THRESHOLD = Math.ceil((modalTop + MODAL_CHROME) / (1 - baseCapFraction));

const maxHeightGuards = rules.filter((rule) => rule.mediaMaxHeight !== null && rule.selector === MODAL);
const resReleases = rules.filter((rule) => rule.mediaMaxWidth !== null && rule.selector === RES);
const widthMediaCount = rules.filter((rule) => rule.mediaMaxWidth !== null).length;

describe('A231 任务详情页定宽/定尺寸的成立区间', () => {
  it('非空解析：弹窗、弹窗体、卡 E 三条被测规则都在（防止改名把闸跑空）', () => {
    expect(modal, `${MODAL} 规则不见了`).toBeDefined();
    expect(modalBody, `${MODAL_BODY} 规则不见了`).toBeDefined();
    expect(res, `${RES} 规则不见了`).toBeDefined();
    expect(pxOf(modal?.decls.width), '弹窗定宽应仍是 720px（台账口径）').toBe(720);
    expect(resTracks, '卡 E 应仍含 328px 定宽轨').toEqual([328]);
  });

  it('弹窗定宽必须有 vw 相对的 max-width 兑底（portal 挂 body，视口 <720 会横向滚动）', () => {
    const maxWidth = modal?.decls['max-width'];
    expect(maxWidth, `${MODAL} 没有 max-width ⇒ 720px !important 在窄视口无从夹住`).toBeDefined();
    expect(maxWidth, `max-width: ${maxWidth} 必须相对视口（含 vw），否则夹不住横向溢出`).toMatch(/100vw/);
    const gutter = pxOf((maxWidth ?? '').replace(/^calc\(\s*100vw\s*-\s*/, ''));
    expect(gutter ?? 0, `max-width: ${maxWidth} 的预留边距超过 64px，等于没兑底`).toBeLessThanOrEqual(64);
  });

  it('全文任何 ≥240px 的定宽 width 必须同条带 max-width（防再引入无兑底定宽）', () => {
    const offenders = rules
      .filter((rule) => {
        const width = pxOf(rule.decls.width);
        return width !== null && width >= 240 && !rule.decls['max-width'];
      })
      .map((rule) => `${rule.media ?? '(基线)'} ${rule.selector} ⇒ width:${rule.decls.width} 无 max-width`);
    expect(offenders, `以下定宽没有视口兑底：\n${offenders.join('\n')}`).toEqual([]);
  });

  it('卡 E 的 328px 定宽轨要在窄档释放，且释放点自洽（既不晚于算术下限、也不吃掉 1440 原型档）', () => {
    expect(resReleases.length, `${RES} 有 328px 定宽轨却没有任何窄档释放（本文件 @media 数 = 0）`).toBeGreaterThan(0);
    for (const release of resReleases) {
      expect(fixedTracks(release.decls['grid-template-columns']),
        `${release.media} 里的释放只是换了一套定宽：${release.decls['grid-template-columns']}`).toEqual([]);
      const breakpoint = release.mediaMaxWidth ?? 0;
      expect(breakpoint,
        `释放点 ${breakpoint}px 太窄：328 + 2×${resGap} + 2×${MIN_FLUID_TRACK} + 外壳 ${CHROME_INLINE} = ${RES_RELEASE_MIN}，` +
          `低于该视口两条流式列已被压到可读下限以下`).toBeGreaterThanOrEqual(RES_RELEASE_MIN);
      expect(breakpoint, `释放点 ${breakpoint}px 越过 1440 原型档，会改掉真值图的三列版式`).toBeLessThan(1440);
    }
  });

  it('弹窗在矮屏必须有窄高释放：顶距压下来且 顶距+非体+体上限 ≤ 视口高', () => {
    expect(maxHeightGuards.length, `${MODAL} 在 max-height 档里没有任何释放（top:${modalTop}px + 体上限会顶出屏）`).toBeGreaterThan(0);
    for (const guard of maxHeightGuards) {
      const boundary = guard.mediaMaxHeight ?? 0;
      expect(boundary,
        `窄高档只覆盖到 ${boundary}px，而基线布局从 ${SHORT_VIEWPORT_THRESHOLD}px 起就溢出 ⇒ 中间那档没人管`).toBeGreaterThanOrEqual(
        SHORT_VIEWPORT_THRESHOLD,
      );
      expect(boundary, `窄高档宽到 ${boundary}px，远超必要（阈值 ${SHORT_VIEWPORT_THRESHOLD}px + 200 容差）`).toBeLessThanOrEqual(
        SHORT_VIEWPORT_THRESHOLD + 200,
      );
      const top = pxOf(guard.decls.top) ?? modalTop;
      expect(top, `窄高档的 top:${top}px 没比基线 ${modalTop}px 更矮`).toBeLessThan(modalTop);
      const capRule = rules.find(
        (rule) => rule.media === guard.media && rule.selector === MODAL_BODY && rule.decls['max-height'] !== undefined,
      );
      const capExpr = capRule?.decls['max-height'] ?? modalBody?.decls['max-height'];
      const cap = resolveVh(capExpr, boundary) ?? Number.POSITIVE_INFINITY;
      expect(top + MODAL_CHROME + cap,
        `在 ${boundary}px 高的视口里弹窗总高 ${top + MODAL_CHROME + Math.round(cap)}（cap=${capExpr}）仍超屏`).toBeLessThanOrEqual(boundary);
    }
  });

  it('本文件不得再是零断点（台账 §A231 的头条：473 行里 @media 计数 0）', () => {
    expect(widthMediaCount, 'taskDetail.css 里没有任何 @media (max-width: …) 档').toBeGreaterThan(0);
    // 四列小卡是本页最宽的并排结构，窄档必须退
    const tileReleases = rules.filter((rule) => rule.mediaMaxWidth !== null && rule.selector === TILE_ROW);
    expect(tileReleases.length, `${TILE_ROW} 四列并排在窄档没有退档规则`).toBeGreaterThan(0);
  });
});
