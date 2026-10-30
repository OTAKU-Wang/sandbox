"""OCR A137 — 遗留后台 `app/static/css/admin.css`：fixed 侧边栏无滚动、侧栏宽度与内容区
`margin-left` 硬耦合且全文件零媒体查询（375px 屏内容区只剩 ~155px，flex item 默认
`min-width:auto` 遇宽表格直接撑大整页）、若干文字/背景对不过 WCAG AA 4.5:1。
台账见 docs/ocr-review-2026-10-18/ocr-verified-findings.md §A137。

jsdom 之外浏览器不做布局，本文件是**静态定量门**（与 poolC-fecss 同口径）：
布局三条按声明事实校验，对比度按 WCAG 2.x 相对亮度公式现算 —— 前景/背景都从 CSS 里
解析（含 `var()` 解引用与 `rgba(...)` 半透明底在实底上的合成），不在测试里写死颜色，
否则改调色板不会让门变色。真机滚动条/断点观感属 visual-only。
"""
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
CSS = REPO / "app" / "static" / "css" / "admin.css"


def _rules(text: str):
    """把样式表拆成 (selector, {prop: value})；@media 只保留块内规则（选择器同名）。"""
    body = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    out = []
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", body):
        sel = m.group(1).strip()
        if sel.startswith("@"):
            continue
        decls = {}
        for d in m.group(2).split(";"):
            if ":" in d:
                k, _, v = d.partition(":")
                decls[k.strip().lower()] = v.strip()
        for s in sel.split(","):
            out.append((s.strip(), decls))
    return out


def _blocks(text: str):
    """顶层规则 + 媒体查询块内的规则，分别返回。"""
    body = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    top, media = [], []
    pos = 0
    for m in re.finditer(r"@media[^{]*\{", body):
        top.extend(_rules(body[pos:m.start()]))
        depth, i = 1, m.end()
        while i < len(body) and depth:
            if body[i] == "{":
                depth += 1
            elif body[i] == "}":
                depth -= 1
            i += 1
        media.append((m.group(0), _rules(body[m.end():i - 1])))
        pos = i
    top.extend(_rules(body[pos:]))
    return top, media


@pytest.fixture(scope="module")
def sheet():
    text = CSS.read_text(encoding="utf-8")
    top, media = _blocks(text)
    # 非空跑：门必须真的看得见被校的那几条规则，否则一切"通过"都是解析失败的假绿
    for want in (".sidebar", ".content", "body"):
        assert any(sel == want for sel, _ in top), f"未能从 admin.css 解析出 {want} 规则"
    return text, top, media


def _decl(top, sel, prop):
    for s, decls in top:
        if s == sel and prop in decls:
            return decls[prop]
    return None


def _root_vars(top):
    for s, decls in top:
        if s == ":root":
            return {k: v for k, v in decls.items()}
    raise AssertionError("admin.css 没有 :root 调色板")


def _resolve(value, root):
    for _ in range(6):
        m = re.search(r"var\(\s*(--[\w-]+)\s*(?:,\s*([^()]*))?\)", value)
        if not m:
            return value.strip()
        value = root.get(m.group(1), m.group(2) or "transparent").strip()
    raise AssertionError(f"var() 循环: {value}")


NAMED = {"white": "#ffffff", "black": "#000000", "silver": "#c0c0c0", "gray": "#808080",
         "red": "#ff0000", "transparent": "#ffffff"}


def _rgb(color):
    c = color.strip()
    if c.lower() in NAMED:
        c = NAMED[c.lower()]
    m = re.fullmatch(r"#([0-9a-fA-F]{6})", c)
    if m:
        h = m.group(1)
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)), 1.0
    m = re.fullmatch(r"rgba?\(([^)]*)\)", c)
    if m:
        parts = [p.strip() for p in m.group(1).split(",")]
        vals = tuple(int(float(p)) for p in parts[:3])
        alpha = float(parts[3]) if len(parts) > 3 else 1.0
        return vals, alpha
    raise AssertionError(f"无法解析颜色（门只能算不透明/半透明 RGB）: {color!r}")


def _composite(fg, bg):
    """rgba 前景按 alpha 合成到实底上（badge 的 rgba 底色就是这种情况）。"""
    (r, g, b), a = _rgb(fg)
    (br, bg_, bb), ba = _rgb(bg)
    assert ba == 1.0, f"背景必须是不透明实底: {bg}"
    return "#" + "".join("%02x" % round(c * a + d * (1 - a)) for c, d in ((r, br), (g, bg_), (b, bb)))


def _lum(rgb_hex):
    vals = [int(rgb_hex.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in vals]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(fg, bg, root):
    f = _composite(_resolve(fg, root), "#ffffff") if fg.strip().startswith("rgba") else _resolve(fg, root)
    b = _composite(_resolve(bg, root), "#ffffff") if bg.strip().startswith("rgba") else _resolve(bg, root)
    lf, lb = _lum(_composite(f, "#ffffff")), _lum(_composite(b, "#ffffff"))
    hi, lo = max(lf, lb), min(lf, lb)
    return (hi + 0.05) / (lo + 0.05)


# (说明, 前景, 背景) — 令牌层的文字/背景对；组件（badge/btn）的颜色从 CSS 规则里解析，见下一个用例
TEXT_PAIRS = [
    ("正文 --text / 页面底 --bg", "var(--text)", "var(--bg)"),
    ("正文 --text / 卡片底 --surface", "var(--text)", "var(--surface)"),
    ("侧栏未选中链接 --text-muted / 侧栏底 --surface", "var(--text-muted)", "var(--surface)"),
    ("表头 --text-muted / 卡片底 --surface", "var(--text-muted)", "var(--surface)"),
    ("统计卡标签 --text-muted / 卡底 --surface", "var(--text-muted)", "var(--surface)"),
    ("空态 --text-muted / 卡底 --surface", "var(--text-muted)", "var(--surface)"),
    ("logo/强调字 --primary / 侧栏底 --surface", "var(--primary)", "var(--surface)"),
]

# 组件规则：badge 的 rgba 底落在卡片底 --surface 上，btn 的底就是自身
COMPONENT_RULES = [
    ".badge-success",
    ".badge-warning",
    ".badge-danger",
    ".badge-info",
    ".btn-primary",
    ".btn-primary:hover",
    ".btn-danger",
]


def _bg_over(bg, root):
    """支持 "rgba(r,g,b,a) over var(--surface)" 写法：先合成 rgba 底，再落到实底上。"""
    m = re.match(r"(rgba\([^)]*\))\s*over\s*(.+)$", bg.strip())
    if not m:
        return _resolve(bg, root)
    layer = _resolve(m.group(1), root)
    base = _resolve(m.group(2), root)
    return _composite(layer, base)


def test_sidebar_is_scrollable_when_fixed(sheet):
    """`position:fixed; height:100vh` 而无 `overflow-y` ⇒ 菜单变多或 Ctrl++ 后多出的项永远滚不到。"""
    _, top, _ = sheet
    sid = dict(next(d for s, d in top if s == ".sidebar"))
    if sid.get("position") != "fixed":
        pytest.skip("侧栏不再是 fixed，本条不再适用")
    assert sid.get("height") == "100vh"
    assert sid.get("overflow-y") in ("auto", "scroll"), (
        f".sidebar 是 fixed + 100vh 但没有纵向滚动：overflow-y={sid.get('overflow-y')!r}"
        " ⇒ 视口高不足时后面的菜单项永远无法到达"
    )


def test_sidebar_width_has_a_single_source(sheet):
    """内容区偏移必须与侧栏宽度同源，否则改一处就会错位。"""
    _, top, _ = sheet
    width = _decl(top, ".sidebar", "width")
    offset = _decl(top, ".content", "margin-left")
    assert width and offset, f".sidebar width={width!r} / .content margin-left={offset!r}"
    mw = re.fullmatch(r"var\(\s*(--[\w-]+)\s*\)", width or "")
    mo = re.fullmatch(r"var\(\s*(--[\w-]+)\s*\)", offset or "")
    assert mw and mo, (
        f"侧栏宽度与内容区偏移仍各自硬编码字面值（width={width!r}, margin-left={offset!r}）"
        " ⇒ 二者硬耦合，响应式改一档就得同时改两处"
    )
    assert mw.group(1) == mo.group(1), f"两处引用了不同的变量: {mw.group(1)} vs {mo.group(1)}"


def test_content_can_shrink_below_its_content_width(sheet):
    """flex item 默认 `min-width:auto`：宽表格会把 `.content` 直接撑大整页（375px 屏横滚）。"""
    _, top, _ = sheet
    assert _decl(top, ".content", "min-width") == "0", (
        f".content min-width={_decl(top, '.content', 'min-width')!r} ⇒ 默认 auto，"
        "遇到宽表格时整页被撑开而不是在卡片内滚动"
    )
    assert any(
        (_decl(top, sel, "overflow-x") or "") in ("auto", "scroll")
        for sel, _ in top
        if sel in (".card", ".content", "table")
    ), ".card/.content/table 没有任何一处允许横向滚动 ⇒ 宽表在窄屏无处可滚"


def test_has_responsive_breakpoint_releasing_the_sidebar_offset(sheet):
    """全文件零 @media：375px 屏上内容区只剩 375-220=155px。"""
    _, top, media = sheet
    assert media, "admin.css 里没有任何 @media 规则 ⇒ 完全无响应式，窄屏内容区被压成 ~155px"
    released = False
    for header, rules in media:
        m = re.search(r"max-width:\s*(\d+)px", header)
        if not m:
            continue
        for sel, decls in rules:
            if sel == ".content" and decls.get("margin-left") in ("0", "0px", "auto"):
                released = True
            if sel == ".sidebar" and decls.get("position") == "static":
                released = True
    assert released, (
        "媒体查询里没有把 .content 的 margin-left 释放为 0/auto，"
        "也没有把 .sidebar 改为 static ⇒ 窄屏仍然只有 视口-220px 可用"
    )
    narrow_vals = [int(m.group(1)) for h, _ in media
                   if (m := re.search(r"max-width:\s*(\d+)px", h))]
    assert narrow_vals, f"媒体查询头部里没有 max-width 档: {[h for h, _ in media]}"
    narrow = min(narrow_vals)
    assert narrow <= 768, f"最窄断点 {narrow}px 覆盖不到手机档（需 ≤768）"


def test_all_text_background_pairs_meet_wcag_aa(sheet):
    """小字号文字（12-14px）必须 ≥ 4.5:1；数字全部现场算，不写死。"""
    _, top, _ = sheet
    root = _root_vars(top)
    failing = []
    for label, fg, bg in TEXT_PAIRS:
        ratio = (contrast(fg, bg, root))
        if ratio < 4.5:
            failing.append(f"{label}: {ratio:.2f}:1")
    assert not failing, "以下文字/背景对不过 WCAG AA 4.5:1（暗色后台）→ " + "; ".join(failing)


def test_component_badge_and_button_colors_meet_wcag_aa(sheet):
    """badge/btn 的前景色与底色都从规则里读（改调色板就会变色，不是写死的对）。"""
    _, top, _ = sheet
    root = _root_vars(top)
    failing = []
    checked = 0
    for sel in COMPONENT_RULES:
        decls = next((d for s, d in top if s == sel), None)
        assert decls, f"未能从 admin.css 解析出 {sel} 规则（门不能空跑）"
        fg, bg = decls.get("color"), decls.get("background")
        if fg is None and ":" in sel:
            base = next((d for s, d in top if s == sel.split(":")[0]), {})
            fg = base.get("color")
        assert fg and bg, f"{sel} 缺 color/background: {decls}"
        if bg.strip().startswith("rgba"):
            bg = _composite(_resolve(bg, root), _resolve("var(--surface)", root))
        else:
            bg = _resolve(bg, root)
        checked += 1
        ratio = contrast(fg, bg, root)
        if ratio < 4.5:
            failing.append(f"{sel} {fg} on {bg}: {ratio:.2f}:1")
    assert checked == len(COMPONENT_RULES)
    assert not failing, "以下组件文字/底色不过 AA 4.5:1 → " + "; ".join(failing)


def test_gate_is_not_vacuous(sheet):
    """对照：已知不合格的一对必须被判红（防止公式退化成永远 ≥4.5）。"""
    _, top, _ = sheet
    root = _root_vars(top)
    assert contrast("#1a1d27", "#2a2d3a", root) < 4.5, "对比度公式失效：近邻底色被判成合格"
    assert contrast("#ffffff", "#ffffff", root) == pytest.approx(1.0, abs=0.01)
