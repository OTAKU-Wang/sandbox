# 前端波次待判清单（w4，截至收割时刻）

## cds-frontend/src/pages/Flow/devTaskCreate.css (17)
### [F-1] high/bug
现码：
```
.cds-mem-stepper {
  display: flex;
  align-items: center;
  height: 40px;
  margin-inline: 4px;
  border: 1px solid #d9d9d9;
  border-radius: 6px;
  background: #fff;
}
```
步进器把边框/背景硬编码成常态值，导致 antd 的两个关键状态视觉丢失：
1) 校验错误态：`debug_memory_gib` / `exec_memory_gib` 有 required 规则，且 `showIssues()` 会把后端 422 写回字段错误（DevTaskCreatePage.tsx L134），antd 只加 `.ant-form-item-has-error` 去染控件边框，而这里的 InputNumber 是 `variant="borderless"`、边框画在自绘 div 上 ⇒ 用户只剩一行红字提示，输入盒仍灰边白底，错误定位不明显。
2) disabled 态：内部 InputNumber 被 `disabled` 时外层盒子依旧纯白。
建议补 `.cds-dev-task-create .ant-form-item-has-error .cds-mem-stepper { border-color: #ff4d4f; box-shadow: 0 0 0 2px rgba(255,77,79,.1); }` 以及 `:has(.ant-input-number-disabled)` 的灰底规则。

### [F-2] medium/bug
现码：
```
.cds-dev-task-create .cds-trio > *:nth-child(3) {
  flex: 1 1 auto;
  font-size: 12px;
  line-height: 20px;
```
用 `nth-child(3)` 同时承担「布局：尾列吃剩余」和「语义：配额列小字 12px」两件事，位置耦合很脆：本页「我的合约」卡的第三列是 `export_mode` 的 Select（DevTaskCreatePage.tsx L251-255），同样会被打上 12px/20px；一旦某张卡改成两列、或字段顺序调整，小字样式就会打到完全不相干的列上，且不会出现任何报错，只能靠肉眼回归发现。建议给真正的配额列显式挂语义类（如 `cds-trio-note`），布局与字号分别用两个类控制。

### [F-3] medium/bug
现码：
```
.cds-dev-task-create .cds-trio > * {
    flex: 1 1 300px;
  }
```
媒体查询里的这条覆盖不了第三列：`.cds-trio > *:nth-child(3)` 特异性为 (0,3,0)，高于本条的 (0,2,0)，而 `@media` 不增加特异性。因此 ≤1280px 换行后第三列仍是 `flex: 1 1 auto`（按内容宽度），与前两列 `1 1 300px` 混排会出现列宽不对齐/尾列忽宽忽窄。另外只有一个 1280 断点，但三列固定宽合计 373×2+25×2=821px，加上侧栏/内距在 1280~1400 区间已很紧张，仍按固定宽渲染易被挤压。建议：断点内补一条同特异性的 `> *:nth-child(3) { flex: 1 1 300px; }`，并把固定 px 改为 `flex: 1 1 min(373px, 100%)` 之类的弹性写法。

### [F-4] medium/bug
现码：
```
box-shadow: none !important;
  background: transparent;
}
```
`box-shadow: none !important` + `background: transparent` 一并压掉了 antd 的 focus/error ring 与 disabled 底色，而自绘盒子只实现了 `:focus-within` 的蓝色一种状态；配合 `!important` 后续任何想按状态覆盖的写法（含 error 态）都必须再叠一层 `!important`，可维护性差。建议去掉 `!important`，用 `border-color/box-shadow` 变量或状态类切换，disabled 时给 `.cds-mem-stepper` 一个 `background:#f5f5f5` 的可见反馈。

### [F-5] medium/bug
现码：
```
.cds-dev-task-create .ant-input:not(textarea),
.cds-dev-task-create .ant-input-affix-wrapper,
.cds-dev-task-create .ant-input-number:not(.cds-mem-input) {
  height: 40px;
}
```
`.ant-input-affix-wrapper` 没有排除「包裹 textarea 的 affix wrapper」：antd 5 中 `Input.TextArea` 只要加 `allowClear` 或 `showCount`，外层就会渲染 `.ant-input-affix-wrapper`，此时 `height: 40px` 会把 3 行文本域压成 40px（当前 description 字段没加这两个 props，属于尚未触发的潜在缺陷，但本页已有 maxLength，很容易有人顺手加 showCount）。建议写成 `.ant-input-affix-wrapper:has(> input)`，或给文本域容器加类并显式 `height: auto`。

### [F-6] medium/bug
现码：
```
.cds-dev-task-create .ant-form-item-vertical .ant-form-item-label {
  padding-bottom: 2px;
}
```
antd 5 的竖排类挂在 form 根（`.ant-form-vertical`），Form.Item 上是否带 `.ant-form-item-vertical` 并不在公开契约内（下面重绘星号的那组选择器就刻意没依赖它）。若该空匹配，「去掉冒号占位」与「label→输入盒 24px 间距」这两条像素判据会静默失效，computed 值仍是 antd 默认（pb 8），且没有任何告警。建议与星号规则保持同一层级写法，或用 `:is(.ant-form-item-vertical, .ant-form-vertical .ant-form-item)` 兜底。

### [F-7] medium/maintainability
现码：
```
.cds-dev-task-create .ant-form-item .ant-form-item-label > label.ant-form-item-required::before {
  display: none !important;
}
```
必填标识完全依赖 antd 内部 DOM/类名（`.ant-form-item-required` + `::before` 星号 / `::after` 冒号）并用 `!important` 硬压，属于对实现细节的隐式契约：antd 一旦改为真实元素渲染星号或调整伪元素职责，这里会出现「星号丢失」或「星号重复」，而纯 CSS 回归很难发现。建议：①把这两条规则与 antd 版本一起纳入升级回归清单；②给必填 label 的校验补一条断言型用例（jsdom 下 `getComputedStyle(label,'::after').content` 或改为在 TSX 里显式渲染 `<span aria-hidden>` 星号，CSS 只负责定位），避免依赖伪元素 content 承载语义。

### [F-8] medium/maintainability
现码：
```
.cds-picker-side {
  width: 256px;
  flex: 0 0 256px;
  border-left: 1px solid #f1f2f4;
  padding-left: 19px;
}
```
本文件里 `.cds-mem-stepper` / `.cds-mem-step` / `.cds-mem-g` / `.cds-picker-side` / `.cds-quota-advice*` 全部定义在页面根类之外。Vite 会把 CSS 打进同一份全局样式表（`import './devTaskCreate.css'` 不是作用域 CSS），因此这些规则对全站生效；其中 `.cds-picker-side`、`.cds-quota-advice` 命名偏通用，其它页面（如后续的数据选择弹窗）复用同名类就会莫名继承 256px 固定宽/左分隔线。建议统一收口到 `.cds-dev-task-create` 下，或把类名改成带页面前缀的形式。

### [F-9] medium/maintainability
现码：
```
.cds-dev-task-create [data-testid^='task-tab-'] {
  height: 28px;
  font-size: 12px;
  padding-inline: 21px;
}
```
用测试钩子 `[data-testid^='task-tab-']` 当样式选择器，把视觉规格和测试标识绑死：`DevTaskDataPicker` 里改 key 命名（L271 `task-tab-${tab.key}`）或测试重构删前缀，这两个药签立刻退化成浏览器默认按钮（无高度/内距/字号，且 TSX 内联 style 只给了边框与颜色），且没有失败用例兜底。建议加语义类 `cds-picker-tab` 承载样式，`data-testid` 只留给测试。

### [F-10] medium/bug
现码：
```
.cds-dev-task-create .cds-task-pager.ant-pagination {
  text-align: right;
}
```
antd 5 的 `.ant-pagination` 是 `display: flex`（Table 分页 className 直接落在该 ul 上），flex 容器的 `text-align: right` 对子项排布无效，「分页带右对齐」这条判据很可能并未真正生效，只是肉眼在小屏上不易察觉。建议改为 `justify-content: flex-end;`（必要时 `gap` 控制间距），并保留 text-align 仅作兜底。

### [F-11] low/performance
现码：
```
.cds-picker-side {
  width: 256px;
```
已选面板没有高度上限：`DevTaskDataPicker` 会把 `selected` 全量渲染成 `<li>`（L302-310），用户勾选上百项时「关联数据」卡被无限撑高、整页出现超长列表，也看不到表格。建议加 `max-height: 320px; overflow-y: auto;`（配合 `overscroll-behavior: contain`）。

### [F-12] low/bug
现码：
```
body .cds-dev-task-create .cds-unit-item .ant-form-item-control-input-content::after {
  content: none;
}
```
两条问题：①`body` 前缀纯粹为抬特异性（index.css 的 `.cds-unit-item` 后缀），这类特异性军备竞赛后续只能靠更多 `!important`/更深前缀解决；②它按「本页所有 `.cds-unit-item`」整片关掉单位后缀，若本页之后新增一个非步进器的带单位字段，后缀会静默消失。更干净的做法是让这两个 Form.Item 不再挂 `cds-unit-item`（单位已由 `.cds-mem-g` 画），本条规则即可删除。

### [F-13] low/bug
现码：
```
.cds-dev-task-create .cds-task-pager.ant-pagination .ant-pagination-item {
  min-width: 30px;
  height: 28px;
  line-height: 26px;
}
```
只同步了页码项的尺寸：分页配置是 `size: 'small'`（DevTaskDataPicker L163），`.ant-pagination-mini` 下 `.ant-pagination-prev/.next .ant-pagination-item-link`、`.ant-pagination-jump-prev/next` 仍是 mini 默认高度，会和 28px 的页码块高度不齐、`line-height: 26px` 也没覆盖到 `a`（antd 对 `.ant-pagination-item a` 有自己的行高）。建议把 prev/next/item-link 的 `height/line-height` 一并对齐。

### [F-14] low/style
现码：
```
.cds-mem-stepper:focus-within {
  border-color: #1b6ef3;
  box-shadow: 0 0 0 2px rgba(27, 110, 243, 0.1);
}
```
同一文件里品牌蓝混用了两个值：`#1b6ff2`（卡头蓝条、当前页、文件头注释）与 `#1b6ef3`（这里、hover、`.cds-quota-advice-title`），而 TSX 里选中态药签内联又写死 `#1b6ff2`。肉眼几乎不可辨但设计 token 已分裂，改主题时需逐处搜索。建议在根作用域定义 `--cds-brand: #1b6ff2; --cds-brand-ring: rgba(27,111,242,.1);` 统一引用。

### [F-15] low/bug
现码：
```
.cds-dev-task-create .ant-card-head-title {
  display: flex;
  align-items: center;
  gap: 9px;
}
```
把 `.ant-card-head-title` 改成 `display: flex` 会作废 antd 自带的单行省略（`overflow:hidden; white-space:nowrap; text-overflow:ellipsis` 对 flex 容器不生效）：卡头副标题（如「合约用于约束模型输出格式与导出通道」）在窄屏下不会截断，会把 `.ant-card-extra` 挤出卡头或撑出横向滚动。建议给 `.cds-card-head-inner` 加 `min-width: 0; overflow: hidden;`，并让其中的 `Text` 用 `ellipsis`（TSX L228/L264/L316 已可用 `style`/`ellipsis`）。

### [F-16] low/bug
现码：
```
.cds-dev-task-create .ant-btn:not(.ant-btn-sm):not(.ant-btn-link) {
  height: 40px;
  padding-inline: 22px;
}
```
`:not(.ant-btn-sm):not(.ant-btn-link)` 没排除 `.ant-btn-icon-only`：antd 5 对 icon-only 按钮用 `padding-inline: 0` + 固定宽（特异性 (0,1,0)），而本条选择器特异性是 (0,4,0) 会直接覆盖它 ⇒ 本页一旦放入 icon-only 按钮（例如输入框后缀/卡头的图标钮）会被撑成宽矩形且图标不居中。建议补 `:not(.ant-btn-icon-only)`，或用 `padding-inline: 22px` 放到更精确的类上。

### [F-17] low/bug
现码：
```
.cds-dev-task-create .cds-dev-task-foot .ant-card-body {
  padding: 16px 3px 16px 24px;
}
```
底部操作条把右内距压到 3px，且 TSX 里内层容器是 `display:flex; justify-content:space-between` 但无 `flex-wrap`（DevTaskCreatePage.tsx L325）：窄屏/浏览器缩放时左侧提示文案与「取消/完成」会互相挤压，文案被截或按钮溢出卡片右边界（加上本页三列固定 373px 已依赖 ≥1280px 宽度）。建议至少给右内距一个下限（如 `max(3px, …)` 或 12px）并允许换行。

## cds-frontend/src/index.css (15)
### [F-18] medium/bug
现码：
```
:is(.cds-home, .cds-detail) .ant-progress-inner,
:is(.cds-home, .cds-detail) .ant-progress-bg {
  height: 12px !important;
}
```
进度条高度 `!important` 选择器过宽：`.ant-progress-inner` 在 antd v5 里也是 circle / dashboard 类型进度环的外层容器（其宽高由内联 style 按 `width` prop 写入）。这条规则会把环形进度的容器高度强压成 12px，导致圆环被压扁/裁切。建议限定为折线类型，例如 `:is(.cds-home,.cds-detail) .ant-progress-line .ant-progress-inner, ... .ant-progress-line .ant-progress-bg`。另外上方注释写「原型实测填充带高 ≈15px」，代码却是 12px，注释与实现不一致，建议对齐一处。

### [F-19] medium/bug
现码：
```
.cds-header {
    height: auto;
    min-height: 64px;
    padding: 10px 14px;
```
移动端断点只重置了 `height`，`line-height: 64px` 仍生效。配合本断点内 `.cds-brand-text span { white-space: normal }`（允许换行）与 `.cds-header { height: auto; align-items: flex-start }`，品牌字换行后每行行高 64px，会在 header 内撑出巨大空隙并被 `overflow: hidden` 裁切；顶栏内的按钮/图标也会因继承 64px 行高而基线错位。建议此处显式重置 `line-height: normal`（或 1.4）。

### [F-20] medium/bug
现码：
```
grid-template-columns: minmax(320px, 1fr) minmax(420px, 520px);
```
两列最小宽度之和（320 + 420 = 740）加上 `.cds-auth-main` 左右 padding 64px = 804px，但折叠为单列的媒体查询在 `max-width: 767px` 才生效。因此视口在 768–803px 区间内 grid 轨道无法收缩，会产生横向滚动条（grid 轨道最小值不会被自动压缩）。建议把断点抬到 960px（或 ≥804px），或把右侧轨道改为 `minmax(min(420px, 100%), 520px)`。

### [F-21] medium/accessibility
现码：
```
.cds-menu-muted {
  color: #a6adb8;
  font-size: 12px;
}
```
弱化菜单项是可点击项，但 #a6adb8 在白底上对比度仅约 2.3:1，12px 小字远低于 WCAG AA 要求的 4.5:1（hover 底色更浅时更差），弱视用户基本无法辨认导航项。原型若确实要「弱化」，建议用 13–14px + ≥4.5:1 的灰（如 #6b7280/#636d7b）表达层级，只保留字号差异；如必须保留此色，至少给 `:hover/:focus-visible` 提升到合规色并保留焦点轮廓。

### [F-22] medium/accessibility
现码：
```
.cds-state-chip.is-gray { background: #f2f3f5; color: #86909c; }
```
灰态状态胶囊 #86909c on #f2f3f5 对比度约 2.9:1，12px 文本不满足 WCAG AA（4.5:1）；「已结束」状态还只靠颜色/圆点区分，色觉障碍用户难以辨别。同一问题也出现在 `.cds-task-card-specs dt`（#86909c 白底约 3.0:1）。建议统一把灰态文字提到 ≥4.5:1（如 #4e5969），并让状态语义由文案承载而非仅靠颜色。

### [F-23] medium/accessibility
现码：
```
content: 'G';
  position: absolute;
  right: 34px;
```
用伪元素画单位「G」有两个问题：① CSS `content` 生成的文本不会被多数屏幕阅读器稳定朗读，也不参与复制/搜索，InputNumber 的值对辅助技术来说是「无单位数字」，正确做法仍是给控件加 `aria-label`/`suffix`（或 antd `addonAfter`）；② `right: 34px` 是按 ± 步进按钮宽度硬编码的魔法值，一旦该 InputNumber 设置 `controls={false}` 或改用 size=large/small，单位就会压住数字或飘出框外。建议至少注释标注依赖 `controls` 默认可见，并给 `input[type=number]` 的 placeholder/aria 补单位。

### [F-24] medium/maintainability
现码：
```
:is(.cds-home, .cds-detail) .ant-card[data-testid^="kpi-"]:hover {
  transform: translateY(-2px);
}
```
多处生产样式挂钩 `data-testid`（`[data-testid^="kpi-"]` 的 hover 位移与 body padding、`[data-testid^="task-tab-"]` 的 cursor/hover/焦点态、`[data-bar]` 的过渡）。测试属性与视觉态强耦合后，测试同学重命名或清理 testid 会静默丢失交互反馈与键盘焦点样式（这里唯一的 `:focus-visible` 轮廓就挂在 testid 上）。建议为这些元素引入语义类名（如 `.cds-kpi-card`、`.cds-task-tab`）并把焦点/悬停态绑到类名，testid 只用于测试选择。

### [F-25] medium/maintainability
现码：
```
-webkit-font-smoothing: antialiased;
  background: #f0f5fb;
```
文件顶部声明了「组件里一律引用这些令牌」，但页面底色 #f0f5fb 在本文件内就硬编码了 3 次（body / .cds-app / .cds-content），另有 #1d2129、#86909c、#f2f3f5、#dfe6ee、#f7f9fc、#eef1f6、#98a2b3 等数十处字面量，且没有 `--cds-page-bg`/`--cds-text-*`/`--cds-border-*` 令牌。换肤或二次校准时必须逐处搜索，极易出现同一色值改一半的色漂。建议把文本/边框/页面底色也抽成 :root 令牌并在此文件内统一引用。

### [F-26] low/other
现码：
```
.cds-content-inner:has(> .cds-crumb-slash) {
  padding-top: 2px;
}
```
`:has()` 是这段间距定标的唯一实现（Safari 15.4+/Chrome 105+/Firefox 121+ 才可用）。在不支持的浏览器里两条规则整体失效，面包屑上内缩（2px）与「面包屑→首张卡 31px」会退化成通用值（16px + toolbar margin），原型还原度直接掉档且无任何兜底。建议为 `.cds-content-inner` 加一个由组件下发的显式类（如 `.has-crumb`）作为主路径、`:has()` 只作增强。另外同类的 `-webkit-mask` + `data:` SVG 图标在严格 CSP（未放开 `img-src data:`）下不渲染，会留下 14+12px 的空白间隙，建议保留图标字符或 `background-color` 兜底。

### [F-27] low/maintainability
现码：
```
:is(.cds-home, .cds-detail) .ant-card-head {
  align-items: flex-start;
}
```
该规则块与文件上方 `:is(.cds-home, .cds-detail) .ant-card-head { min-height/padding/border-bottom-color }` 选择器完全重复，`.ant-card-head-title` 也出现了两次且 `padding: 12px 0` 重复声明，`.cds-home .ant-card-head` / `.ant-card-head-title` 又第三次覆盖。三处分散叠加后，实际生效值只能靠文件顺序推断，后续改内距很容易只改一处。建议合并成单一块，并把 `.cds-home` 的覆盖集中到它自己的注释段内。

### [F-28] low/bug
现码：
```
.ant-table-wrapper .ant-table {
  border-radius: 8px;
}
```
`.ant-table` 本身没有 `overflow: hidden`，单独设 `border-radius` 不会裁切子元素；而下面的 `:is(.cds-home,.cds-detail) ... th { background: #f7f9fc }` 会把表头背景铺成方角，圆角实际不可见（在圆角卡内会露出方角灰块）。建议改在 `.ant-table-container` 上 `overflow: hidden`，或直接对首/末 `th` 设 `border-start-start-radius` / `border-start-end-radius`。

### [F-29] low/bug
现码：
```
.cds-task-card:hover {
  border-color: #bcd4f6;
```
`border: none` 使边框宽度为 0，hover 里只改 `border-color` 不会产生任何视觉效果，transition 中的 `border-color` 同样是空转——注释里想要的「悬浮蓝描边」其实没有实现。建议基础态写 `border: 1px solid transparent`（并把 box-sizing 计入卡宽 380 的校准），hover 再改颜色。

### [F-30] low/performance
现码：
```
:is(.cds-home, .cds-detail) [data-bar] {
  transition: height 0.25s cubic-bezier(0.4, 0, 0.2, 1);
}
```
`height` 不是合成属性，柱状图数值变化时每帧触发布局+重绘；多个柱同时动画会形成连续 reflow。建议改为 `transform: scaleY()` + `transform-origin: bottom`（或用 CSS 变量驱动），过渡效果一致但不掉帧。

### [F-31] low/bug
现码：
```
grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
```
`auto-fill + minmax(280px, 1fr)` 的最小轨宽是固定 px，容器窄于 280px 时轨道不会收缩而直接溢出（例如 320px 视口减去 `.cds-content`/`.cds-content-inner` 各 12px 内距后仅剩 284px，再叠加更窄机型或外层卡片内距就会冒横向滚动条）。建议写成 `minmax(min(280px, 100%), 1fr)`。

### [F-32] low/documentation
现码：
```
.cds-task-card-foot .ant-btn {
  width: 100%;
  min-width: 0;
  height: 32px;
```
注释写「三钮等宽满宽、高 36」，代码却是 `height: 32px`（且上一段注释的真值是「三铟 108×32」）。三处描述里 36 是转录残留，后续按注释复核像素会得出错误结论。建议把注释统一为 32。

## cds-frontend/src/pages/Flow/taskDetail.css (14)
### [F-33] high/bug
现码：
```
.cds-t24 .t24-res {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) 328px;
  column-gap: 26px;
  margin-top: 21px;
}
```
资源配置区把第三列写成固定 `328px`，前两列 `minmax(0,1fr)`，加上卡体左右各 24 内缩，实际需要 ~（2×配额列最小内容 + 2×26 + 328 + 48）的宽度。文件内没有任何 `@media` 断点，窄屏（1280 以下、缩放到 80%、或外层 Space/Drawer 里再嵌一层）时两列配额盒会被压到极窄，`.t24-quota-box` 里的 `justify-content: space-between` 会把值和 `G` 小片挤到重叠/换行。建议给面板列用 `minmax(280px, 328px)` 并补一个 `@media (max-width: 1200px)` 降级为两列/单列堆叠。

### [F-34] medium/bug
现码：
```
.cds-t24 .t24-field-box {
  display: flex;
  align-items: center;
  margin-top: 8px;
  min-height: 42px;
  padding: 11px 10px; /* 图值盒 h42：行高 20 + 上下 11（旧 12 得 44） */
```
值盒是 `display:flex` 且没有 `min-width:0` / `overflow:hidden` / `text-overflow` / `overflow-wrap`。本页字段值大量是后端原样下发或「未记录」「—」等长串（任务号、合约号、notebook 路径、导出方式说明），长英文/数字 token 不换行时会直接溢出 `#f7f8fa` 盒体外并与相邻列文字重叠。建议加 `min-width: 0; overflow-wrap: anywhere;`（或按需 `overflow: hidden; text-overflow: ellipsis; white-space: nowrap;`）。

### [F-35] medium/bug
现码：
```
.cds-t24-modal.ant-modal {
  width: 720px !important;
  top: 64px;
}
```
弹窗走 antd portal 渲染到 body，不受 `.cds-t24` 约束，这里用 `!important` 把宽度硬钉成 720px 且没有 `max-width`。视口宽度 < 720（小屏笔电开分屏、1024 缩放到 125%、移动端调试）时弹窗会超出视口产生横向滚动，且 `top: 64px` 在矮屏上会把 footer 顶出屏幕。建议同时给 `max-width: calc(100vw - 32px);` 并用 `@media (max-width: 768px) { width: calc(100vw - 32px) !important; }` 兜底。

### [F-36] medium/bug
现码：
```
.cds-t24 .t24-ht {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 16px;
```
卡头把 6×18 蓝条 + 标题 + 图标 + 12px 灰注放进同一个 `.t24-ht`（flex 且不换行），而它的父级是 antd 的 `.ant-card-head-title`，该类默认带 `overflow: hidden; white-space: nowrap; flex: 1`，右侧还要和 `.ant-card-extra` 的蓝字链接抢宽度。TaskDetail 里的 `RESULT_SET_NOTE`/`GRANTED_DATA_NOTE` 都是整句说明，宽度不足时会被父级直接 ellipsis 截断（且没有 tooltip 兜底），整句口径说明丢失。建议 `.t24-ht` 允许换行（`flex-wrap: wrap`）或把注释放到独立一行，并对 note 单独 `min-width: 0`。

### [F-37] medium/maintainability
现码：
```
.cds-t24 .t24-mem-bar .ant-progress-inner,
.cds-t24 .t24-mem-bar.ant-progress .ant-progress-inner {
  height: 6px !important;
  background: var(--t24-track);
  border-radius: 3px;
}
```
这里对 antd Progress 内部类做深链覆盖，且只有 `height` 带 `!important`：`background` / `border-radius` 没带。antd（v5 线形 Progress）会把 `strokeWidth`、`strokeColor` 写成内联样式，内联优先级高于普通声明，因此一旦业务侧传了 `strokeColor` 或组件升级改了内部结构，蓝色填充与圆角会静默失效，只剩 `!important` 的高度——视觉坏掉但没有任何报错。更稳的做法是用组件 props（`strokeWidth={6}`、`trailColor="#e3edfb"`、`strokeColor="#1b6ef3"`），CSS 只留必要的 margin/行高修正。

### [F-38] medium/accessibility
现码：
```
.cds-t24 .t24-tag.is-ok.ant-tag {
  background: #e8f7ee;
  color: #00a870;
  border-color: #e8f7ee;
}
```
状态标签全部靠「浅底 + 同色深字」表达，但对比度不达 WCAG AA：#00a870/#e8f7ee ≈ 2.5:1、#f53f3f/#fdecee ≈ 3.2:1、#1b6ef3/#e8f1ff ≈ 3.3:1，在 14px 字号下均低于 4.5:1；同理 `--t24-grey #86909c` 在白底 12px 只有约 3.0:1，被大量用于 label、行注和差额说明。加上全文没有 `:focus-visible`/`aria-live` 之类规则，弱视与键盘用户难以区分「进行中/成功/失败/未开始」。建议提高文字色深度（或按设计确认加 `font-weight: 500`）、必要时在文案里保留状态词而非只靠颜色。

### [F-39] medium/bug
现码：
```
.cds-t24 .t24-table .ant-table-tbody > tr > td {
  height: 48px;
  padding: 0 14px;
  border-bottom: 1px solid var(--t24-line);
  color: var(--t24-ink);
  font-size: 14px;
}
```
表头/单元格用 `height` + `padding: 0 14px` 钉死 45/48，但表格单元格 `height` 只是最小值，且没有声明 `vertical-align: middle`，也没有 `white-space: nowrap`。本页单元格里有 Tag、Tooltip 包裹的 span、以及可能折行的数据产品名/说明文字：一旦内容超过一行，行高会被撑到 72+，与「行 48」的像素稿全面错位，同时零上下内缩会让单行文字贴顶显示。建议显式 `vertical-align: middle`，并对确实需要单行的列加 `white-space: nowrap` 或改用 `min-height` 的行内盒。

### [F-40] medium/bug
现码：
```
.cds-t24 .t24-tile-row {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 16px;
}
```
统计小卡固定 `repeat(4, minmax(0,1fr))`（基本信息区 `repeat(3, minmax(0,1fr))`）也没有任何断点，且卡体左右各 24 内缩 + 间隙 16。小卡内部是「40 芯片 + 12 gap + 16 内缩」，列宽一旦低于 ~230px（对应页宽 1200 以下或页面再被嵌进抽屉/半屏），标题与值会逐字换行把 min-height 撑高，四张卡高度不再齐平。建议加 `@media (max-width: 1200px) { grid-template-columns: repeat(2, minmax(0,1fr)); }` 与单列兵底。

### [F-41] medium/accessibility
现码：
```
.cds-t24 .t24-table a.ant-typography,
.cds- t24 .t24-table .ant-table-tbody a {
  color: var(--t24-blue);
}
```
这里只重写了链接颜色，没有配套 `:hover` / `:focus-visible` / `:active` 规则。本选择器特异度高于 antd 自带的链接 hover 规则，会把表内「数据产品名称」「详情」链接的悬停变色和键盘 focus 环一并压住——鼠标/键盘用户仅能靠光标变化判断可点。建议补上 `.cds-t24 .t24-table a:hover { color: <浅蓝>; text-decoration: underline; }` 与 `:focus-visible { outline: 2px solid var(--t24-blue); outline-offset: 2px; }`。

### [F-42] medium/bug
现码：
```
.cds-t24 .t24-tag.ant-tag {
  margin: 0;
  padding: 0 12px;
  height: 21px;
  line-height: 19px;
  border: 1px solid transparent;
  border-radius: 4px;
  font-size: 14px;
}
```
标签 `height: 21px` + `line-height: 19px` + 左右 12px 内缩全部写死，无 `max-width` 与溢出策略；antd Tag 默认 `white-space: nowrap`。当状态文案是较长的枚举（如「部分执行失败」「等待资源调度」）时，它会与主标题同一行 flex 排列，直接撑宽 `.t24-head-row` 并把右侧 `.t24-head-actions` 按钮挤出可视区（flex 默认不收缩 overflow 文本）。建议改成 `min-height`，并给标签加 `max-width: 100%; overflow: hidden; text-overflow: ellipsis;`，同时给标题列加 `min-width: 0`。

### [F-43] low/maintainability
现码：
```
/* 包住 Progress 的 div 会继承 22 行高，把 6px 槽撑成 22（实测）；PNG 这块只有 8 */
.cds-t24 .t24-mem-bar {
  line-height: 8px;
}
```
上面 `.ant-progress` 设 `line-height: 6px`，这里 `.t24-mem-bar` 又设 `line-height: 8px`，两条规则可能命中同一个元素（前面的选择器已包含 `.t24-mem-bar.ant-progress`），数值互相矛盾、真实生效值取决于元素结构，后面改的人无法判断该信哪个。同时注释里的像素口径（6/8/2px gap/154 面板）与实现完全绑定，属于「一次截图量出来的魔法数」，建议合并为一处并写明单位含义。

### [F-44] low/maintainability
现码：
```
.cds-t24 .ant-alert {
  margin-top: 16px;
  font-size: 13px;
}
```
这条规则会命中页面内**所有** Alert（`QueryErrorAlert`、未关联合约提示、合约未下发清单告警、以及后续任何人新加的 Alert），并强制 13px 字号——这是按「比图多出来的告警」做的局部微调，却写成页面级通配。建议改成 `.cds-t24 .t24-alert`（在需要的那几处 Alert 上加类名），避免以后新增 Alert 被静默改样式；同时这里只给了 `margin-top`，连续两个 Alert 会紧挨，建议配合 `gap` 或父容器间距管理。

### [F-45] low/maintainability
现码：
```
.cds-t24 .t24-quota-box {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 8px;
  min-height: 42px;
  padding: 11px 10px;
  background: var(--t24-box);
```
两点维护性风险：1）`.t24-quota-box` 与前面的 `.t24-field-box` 除 `justify-content: space-between` 外几乎完全重复（margin-top/min-height/padding/背景/圆角/字号），改一处必忘另一处，建议抽公共类 `.t24-readonly-box` 再叠加；2）大量色值直接写死（#1b6ef3、#4e5969、#e8f7ee、#b6bec9、#5b6472、#f53f3f 等），顶部已经建了 `--t24-*` 变量体系却没用完，后续接暗色主题或改主题色需要逐处搜索替换。

### [F-46] low/bug
现码：
```
.cds-t24 .t24-head-actions .ant-btn {
  height: 38px;
  font-size: 14px;
```
按钮高度 38px 与 `padding-inline: 21px` 是按中文四字按钮量出来的固定值，而按钮文本包含动态/枚举文案。一旦改成两行文案、英文 i18n（如 “Back to list”）或带后缀图标，固定高度 + 固定横向内缩会直接把文字裁切或撑出主按钮边框（antd Button 默认 `white-space: nowrap` 不会换行）。建议用 `min-height: 38px` + `padding-inline: 20px`，宽度交给内容。

## cds-frontend/src/pages/Audit/auditLog.css (10)
### [F-47] high/bug
现码：
```
.cds-audit-screen,
.cds-audit-modal-root {
```
局部令牌的作用域类名 cds-audit-modal-root 在本屏并不存在：AuditLog.tsx 的详情弹窗只挂了 className=cds-detail-modal cds-audit-modal（无 wrapClassName/rootClassName），且 antd Modal 默认 portal 到 body，弹窗子树既不在 .cds-audit-screen 之下也取不到这些变量。后果：下文 .cds-audit-modal .cds-audit-panel 的 background: var(--audit-head-bg) 因变量未定义而解析失败（背景回到 transparent），4-2 的 #f7f9fc 灰面板底色会丢失。建议作用域改为实际存在的 .cds-audit-modal（或在弹窗根补上 cds-audit-modal-root）。

### [F-48] high/bug
现码：
```
.cds-audit-modal .cds-audit-modal-tag.ant-tag {
  height: 20px;
```
4-2 标题右侧标签拿不到注释所写的浅橙底+同色字：t-green/t-purple/t-blue/t-orange/t-plain 的配色全部挂在 .cds-audit-screen .cds-audit-records .cds-audit-tag.* 下，而弹窗经 portal 渲染在 body 上、不在 .cds-audit-screen 子树内。AuditLog.tsx 给标题 Tag 同时给了 cds-audit-modal-tag 与 auditTagClass(detail)，但那些 t-* 类在弹窗里无任何规则命中，最终只剩本条尺寸规则，落到 antd 默认灰底描边样式，与 4-1 的色板不一致。建议把 t-* 色板从列表作用域抽出，改为 :where(.cds-audit-screen .cds-audit-records .cds-audit-tag, .cds-audit-modal-tag).t-orange 这类可复用前缀。

### [F-49] high/bug
现码：
```
.cds-audit-screen .cds-audit-filter .f-range .ant-picker-input:first-child {
  width: 55px;
  flex: none;
}
```
55px 是按原型「空值占位态」的墨迹反推的，只够放 4 字占位符（14px 下约 56px）；用户选完日期后 YYYY-MM-DD 实际值宽约 78px，input 内文字会被裁切（input 无 ellipsis 能力），叠加上文 .ant-picker 仅 180px（两端值+分隔符+清除/日历图标约需 214px），选中态必然溢出或错位。另 flex: none 使该字段完全不可压缩，长文案（i18n）时整行会顶出卡体。建议给前字段和 picker 设最小宽（min-width: 88px / 220px）或以 clamp 给下限，把 180px 只当作 ≥1400 参考宽度下的目标值。

### [F-50] medium/bug
现码：
```
.cds-audit-screen .cds-audit-records .ant-pagination .ant-pagination-options {
  display: contents;
}
```
靠 display: contents 拆 antd 内部结构带来两个真实问题：1）可访问性：DOM/Tab 顺序仍是页码→条数选择器→快速跳转，视觉顺序却是总数→条数选择器→页码→快速跳转，键盘焦点与视觉顺序相反（WCAG 1.3.2/2.4.3），且 display: contents 在部分浏览器会把该节点从无障碍树移除；2）耦合：依赖 antd 把 size-changer 与 quick-jumper 包在同一个 li 内，升级改结构就静默失效。同时 display: contents 让该 li 自身 margin 失效，下面 .ant-pagination-options-size-changer 的 margin-inline-end: 0 实际是空操作（间距已由父级 gap: 8px 决定）。建议在渲染层调整顺序（pagination 的 items 或自定义 DOM），并删掉失效的那条 margin 规则。

### [F-51] medium/maintainability
现码：
```
--audit-success: #00a870;   /* 成功图标实心像素 x1040 y462 = #01a871（全局 --cds-success #00b070 差 8/255） */
  --audit-danger: #e5484d;    /* 失败图标实心像素 x1040 y718 = #e5484d */
  --audit-tag-green: #e8f7ee; /* 结果集导出标签底 x496 y452 */
  --audit-tag-purple: #f1ecff;/* 数据调用标签底 x496 y504（--cds-purple-soft #efeaff 差 2/255） */
  --audit-tag-orange: #fff3e8;/* 进入生产环境标签底 x496 y608 */
```
这几个本屏自测值与 index.css 全局令牌已完全同值，等于把调色板 fork 了一份：--audit-success #00a870 == --cds-success、--audit-tag-green #e8f7ee == --cds-success-soft、--audit-tag-purple #f1ecff == --cds-purple-soft、--audit-tag-orange #fff3e8 == --cds-orange-soft；注释里写的 --cds-success #00b070、--cds-purple-soft #efeaff 均已过期（全局早已校准成同值）。后续调色板再校准，本屏会静默漂移。建议改为引用全局令牌，仅保留确实不同的读数（如 --audit-danger），并清理过期注释。

### [F-52] medium/maintainability
现码：
```
.cds-content-inner:has(> .cds-audit-screen) > .cds-crumb-slash + .cds-audit-screen {
  margin-top: 15px;
}
```
这组壳层微调有三重脆弱性：1）强依赖 .cds-audit-screen 是 .cds-content-inner 的直接子元素，外层一旦新增 Suspense/ErrorBoundary/路由包装节点，:has(> ...) 与 + 同时失效，间距静默回落到 index.css 的 31px（16px 视觉回归无任何提示）；2）用 -4px / 15px 这类对账补偿值反向覆盖全局壳规则，与 index.css 的面包屑度量互相牵制，任一侧调整都会打架；3）:has() 在 Chrome 105 / Firefox 121 以下不支持，整块微调整体 no-op。建议由 Layout 依路由给壳节点加修饰类（如 .cds-content-inner[data-screen=audit]）或由页面声明 --crumb-gap 变量，摆脱结构耦合。

### [F-53] medium/maintainability
现码：
```
.cds-audit-screen .cds-audit-records .ant-pagination .ant-pagination-item-active {
  background: #1b6ef3;
  border-color: #1b6ef3;
}
```
#1b6ef3 就是全局 --cds-primary，这里硬编码又抄了一份主色（index.css 明确要求组件一律引用令牌、不再堆内联十六进制），主色调研时本屏当前页码会掉队。另 .cds-audit-section::before 的 #1f70f3 是第三支蓝，与 --cds-primary 有明显色差，会让 4-2 强调蓝与全站主色不一致。建议统一 var(--cds-primary)；若实测确为不同支蓝，请在注释里写明差异与保留理由。

### [F-54] medium/bug
现码：
```
.cds-audit-screen .cds-audit-records .ant-table-tbody > tr:last-child > td {
  border-bottom: 1px solid var(--audit-rule);
}
```
空数据时 antd 渲染的 .ant-table-placeholder 行就是 tbody 的 last-child，于是「仅末行一条下边线」在空态下变成空态占位块下方多出一条线（再叠加上一条 td height: 52px，空态观感更差）；若后续启用 fixed 列/展开行，measure 行与 expanded 行也会干扰 last-child 判定。建议排除空态行：> tr:not(.ant-table-placeholder):last-child > td。

### [F-55] medium/test
现码：
```
.cds-audit-modal.cds-detail-modal .ant-modal-footer {
  border-top: 0;
}
```
这条收掉共用件分隔线的规则依赖实例上同时存在 cds-detail-modal（AuditLog.tsx className=cds-detail-modal cds-audit-modal）——一旦有人清理这个看似多余的共用类，index.css 的 border-top 就回来了，且没有任何测试会报错；同样 index.css 的 .cds-detail-modal .ant-modal-footer{padding:12px 20px} 与 .ant-modal-body{padding-top:16px} 仍在本屏隐式生效，本屏对 footer/标题高度做像素对账却未声明覆盖它们，度量输入不透明。建议本屏显式写全 footer 的 padding/border（自成一体）或注释点名依赖 index.css 哪几条，并补一条 4-1/4-2 视觉回归（或计算样式快照）用例，否则这类像素对账结果在后续改动中极易被静默破坏。

### [F-56] low/maintainability
现码：
```
border-bottom: 0;
  font-size: 13px;
  font-weight: 500;
  color: #5b6472;
```
文件头声明细笔画文字一律用三档墨色律（ink #1f2a37 / secondary #4e5969 / muted #86909c），但表头用了体系外的 #5b6472；同时三档墨色在全文以字面量重复十余处，而 index.css 的 .cds-filter-label 又用了另一支 #636d7b。既违反本文件自定规则，也使跨屏墨色无法一次调齐。建议把三档墨色提成令牌后引用，并明确 #5b6472 应归入哪一档。

## cds-frontend/src/pages/Flow/deliveryOpenapi.css (9)
### [F-57] high/bug
现码：
```
.cds-api-create-form .cds-row3 {
  display: grid;
  grid-template-columns: 360px 360px 360px;
  gap: 0 24px;
}
```
定宽三列 + **视口**断点的组合仍然存在溢出风险，且恰好可能重现注释声称修掉的“右缘越过内容盒”：三列需要 3×360 + 2×24 = 1128px，加上本表单 `padding-inline: 24px`（48）与卡身内距（48）共约 1224px 的可用内容宽，但断点用的是 `max-width: 1439px`，即视口 ≥1440 就无条件三列。控制台类页面普遍有侧边栏（约 200–256）+ 页头/页面 padding，实际内容盒常小于 1224，于是 ≥1440 视口照样横向溢出。同时三列是定宽、降档后是 `1fr` 拉伸，控件宽度在断点两侧不一致（360 vs 半容器宽），视觉跳变。建议改为容器驱动：用 `@container` 查询或 `grid-template-columns: repeat(auto-fit, minmax(0, 360px))`，让列数随**容器**宽度而非视口切换；另外 `gap: 0 24px` 行间距为 0，换行/降档后完全依赖 Form.Item 的 margin-bottom，建议显式给出 `row-gap`。

### [F-58] medium/maintainability
现码：
```
.cds-delivery-tabs-card > .ant-card-body {
  padding: 24px;
}
```
直接覆盖 antd v5 内部类 `.ant-card-body` 属于脆弱耦合：antd 5 的样式由 cssinjs 运行时注入，其选择器形如 `:where(.cssHash).ant-card-small > .ant-card-body`，与本规则的特异性接近/相同（0,2,0），最终谁生效取决于 `<style>` 注入顺序（受 StyleProvider/hashPriority、SSR 提权等影响），存在“本地能生效、灰度环境失效”的隐性风险；一旦 antd 升级调整 Card DOM（如再包一层 wrapper）规则会静默失效，且失效后表现为“内距变 12/16”这类难排查的像素偏差。建议改用受支持的配置入口：`<Card styles={{ body: { padding: 24 } }}>` 或 ConfigProvider `theme.components.Card.bodyPadding / bodyPaddingSM`，把这类尺寸收进主题层。

### [F-59] medium/maintainability
现码：
```
.cds-openapi-filter > .ant-card-body {
  padding: 24px;
}
```
同上：这里与上面的 `.cds-delivery-tabs-card` 是同一类“改写 antd 卡身内距”的需求，两处重复同一魔法值 24px，且都依赖 antd 内部类名 + 注入顺序。建议统一由 theme token（Card bodyPadding）或一个共享的 `--cds-card-body-padding` CSS 变量提供，避免后续设计内距调整时要逐屏找散落规则。

### [F-60] medium/bug
现码：
```
.cds-openapi-filter .cds-filter-row {
  display: flex;
  align-items: center;
  gap: 24px;
  flex-wrap: wrap;
}
```
行内没有给 `.cds-filter-field` 设 `min-width: 0`，而标签又是 `flex: none; white-space: nowrap`，flex item 默认 `min-width: auto` 意味着字段区不可收缩：当标签文案变长（i18n/新增筛选项）或视口介于换行阈值之间时，整行宽度会超过卡身内容盒，产生横向溢出/出现横向滚动条，而不是按预期换行。建议给字段设置 `min-width: 0` 并让其控件（Select/Input）用 `width: 100%` 参与收缩，或明确给字段定宽并依赖 `flex-wrap` 换行。

### [F-61] medium/bug
现码：
```
.cds-openapi-filter .cds-filter-actions {
  margin-inline-start: auto;
  display: flex;
  gap: 12px;
}
```
父容器开了 `flex-wrap: wrap`，此时 `margin-inline-start: auto` 只能保证按钮组在“它所在的那一行”右靠，无法保证它与控件行同轴。一旦字段换行（新增筛选项、长文案、1280/1366 常见分辨率），按钮组会掉到下一行独占一行右靠，视觉上变成“按钮悬空在筛选卡底部”，与本文件声明的“按钮组贴卡内容右缘、与控件行同一像素簇”判据直接冲突。建议二选一：① 按钮组所在行 `flex-shrink: 0` 并把字段包进一个 `flex: 1 1 auto; min-width: 0` 的容器，使换行只发生在字段区；② 把按钮组作为独立的第二行并 `justify-content: flex-end`，明确接受换行布局。

### [F-62] medium/maintainability
现码：
```
.cds-openapi-filter .cds-filter-actions > .ant-btn {
  height: 35px;
}
```
两点问题：1) 35px 不在 antd 尺寸档位内（token controlHeight=32、lg=40），而本文件开头注释又要求“新建接口”按钮 h40，同一屏出现 32/35/40 三种按钮高度，交互一致性会被设计/测试反复质疑；2) 该规则用 `> .ant-btn` 命中按钮组内**所有**按钮，后续若在该组放入 icon-only 按钮、`Button size="sm/lg"` 或 `Space.Compact`/`Dropdown.Button` 组合，都会被强行拉到 35px，且写死高度在浏览器缩放（125%/150%）或大字号无障碍设置下容易裁掉文字。建议改为主题 token（`theme.components.Button.controlHeight`）或局部 CSS 变量，并把“查询 95×35”这类像素真值落成视觉回归断言而不是裸写数值。

### [F-63] medium/maintainability
现码：
```
.cds-api-create-form .cds-row3 > .cds-row3-placeholder-never-match {
  width: auto;
}
```
占位，勿用

### [F-64] low/maintainability
现码：
```
.cds-openapi-filter .cds-filter-actions > .ant-btn-primary {
  min-width: 95px;
}
```
`min-width: 95px` 只保证下限，不封顶：当按钮文案变长（英文 “Query/Search”、带 loading 图标、带 icon）时按钮会撑宽，注释里精心推算的 1220 左缘对齐即失效，而这套推算没有任何自动化校验兜底。建议把这类像素真值抽成变量并配一条视觉/样式单测（断言按钮宽度与右缘位置），否则“逐字对齐真值”的结论只能靠人工回归维持。

### [F-65] low/maintainability
现码：
```
color: rgba(0, 0, 0, 0.88);
}
```
硬编码 antd 默认亮色主题的 `colorText` 值（0.88 黑），绕过了 token 体系：一旦接入 `theme.darkAlgorithm`、品牌主题或 ConfigProvider 自定义色板，标签颜色不会跟随，会出现暗色背景下近黑文字的可读性问题。antd 5 已暴露 CSS 变量，建议用 `color: var(--ant-color-text);`（或经 ConfigProvider token 注入的自定义变量）。

## cds-frontend/src/pages/Flow/notebookWorkbench.css (7)
### [F-66] medium/bug
现码：
```
.nbw-columns {
  display: flex;
  align-items: stretch;
  gap: 24px;
  height: calc(100vh - 250px);
  min-height: 700px;
}
```
两栏高度用 `calc(100vh - 250px)` 加固定 `min-height: 700px`，有两类边界风险：
1. 视口高度 < 950px（笔记本 768/800 高、iframe 嵌入、缩放 125%）时 calc 结果小于 700px，`min-height` 生效使实际高度超过可用空间；若外层容器（Layout Content）是 `overflow: hidden`，标题卡下方内容会被直接裁切，若可滚动则出现「页面滚动 + 卡内滚动」双滚动条。
2. `100vh` 不感知嵌入容器/浏览器工具栏高度（iframe 内 100vh = 视口而非宿主可用高度），且 `height` 写死后，标题卡副标题换行撑高（min-height 93 只是下限）时总高会继续溢出。
另外该块没有任何 `@media` 断点，左栏 `flex: 0 0 250px` 在窄宽度下会把右栏挤到几乎不可用。
建议：改用 `min-height` 让内容不足时自然收拢，用 `100dvh` 或外层容器变量替代 `100vh`，并为窄屏补断点。

### [F-67] medium/bug
现码：
```
.nbw-card-grow { flex: 1 1 auto; }
.nbw-card-fixed { flex: 0 0 auto; height: 320px; }
```
输出卡固定 `height: 320px`（配合 `.nbw-card { overflow: hidden }`）在两栏高度被压缩时会挤压上层的编辑器卡：右栏可用高度 = 列高 − 24（gap）− 320，而编辑器卡自身还有卡头 + 工具条 + 状态条约 110px，`min-height: 0` 又允许它被压到 0，最终代码区可能只剩几十像素甚至完全不可见（Monaco 折叠后无任何提示）。建议把固定值改为可伸缩基准 + 上下限，并给 `.nbw-card-grow` 一个保底高度。

### [F-68] medium/bug
现码：
```
.nbw-menu-label:hover { background: #f2f6fd; color: var(--nbw-blue); }
```
整份文件里所有可交互元素（`.nbw-menu-label`、`.nbw-lang-pick`、`.nbw-folder-toggle`、`.nbw-tab`、`.nbw-icon-btn`）只定义了 `:hover` 与 `:disabled`，没有任何 `:focus-visible` 样式。`.nbw-icon-btn` / `.nbw-folder-toggle` 还显式写了 `border: 0; background: transparent`，一旦 antd reset 或浏览器默认 outline 被覆盖，键盘用户完全看不到焦点位置；`.nbw-menu-label`、`.nbw-lang-pick` 是纯文字触发器（无 hover 底色时点击性也不可见）。同时 `.nbw-icon-btn:disabled` 仅靠 `#c4c9d1` 与正常 `#7b8ba3` 区分禁用态，对比度差异过小。建议统一补 focus 环，并给禁用态增加更强的视觉区分（如降低不透明度 + cursor）。

### [F-69] low/maintainability
现码：
```
.nbw-chip-off { color: #8c8c8c; background: #f5f5f5; }
.nbw-chip-unknown { color: #8c8c8c; background: #f5f5f5; }
```
状态色在这份文件里出现了三套硬编码值且语义冲突：错误色有 `#c0392b`（`.nbw-tree-error`）、`#cf1322`（`.nbw-run-error` / `.nbw-log-error`）两种；警告色 `#ad6800` 重复三次；灰色 `#8c8c8c` 既有变量 `--nbw-muted` 又有多处直接写死。更实际的问题是 `.nbw-chip-unknown` 与 `.nbw-chip-off` 完全相同——「状态未知（接口未接入）」与「已关闭」在 UI 上无法区分，这违背了本模块 honest degradation 需要把「未接入」与「关闭」区分开的口径；若确需一致则应删掉冗余类，否则补上差异样式。建议在 `.nbw-root` 里补齐 `--nbw-error` / `--nbw-warn` / `--nbw-unknown` 令牌并全量替换字面量。

### [F-70] low/bug
现码：
```
white-space: pre-wrap;
  word-break: break-all;
}
```
`word-break: break-all` 会在任意字符处断行，日志里的文件路径、URL、异常标识（如 `FileNotFoundError`、`/home/user/...`）会被从中间切断，跨行拼接后可读性明显下降；`white-space: pre-wrap` 本身已能处理换行与缩进。建议用 `overflow-wrap: anywhere`（或 `break-word`）替代，仅在真正超长无空格 token 时才断行。

### [F-71] low/maintainability
现码：
```
.nbw-root {
  --nbw-border: #eceff4;
```
`--nbw-*` 变量只声明在 `.nbw-root` 上，而这些卡片类（`.nbw-card`、`.nbw-tab`、`.nbw-log-*` 等）可能被渲染进 antd Modal/Drawer/Tooltip 等 portal（挂载到 `document.body`，在 `.nbw-root` 之外）。此时 `var(--nbw-border)` 解析失败，整条 `border: 1px solid var(--nbw-border)` 声明会被丢弃，`color: var(--nbw-ink)` 也回退成继承色，表现为弹窗内外框样式不一致且难以排查。建议给变量加兜底值（`var(--nbw-border, #eceff4)`）或把令牌声明提升到 `:root`。

### [F-72] low/maintainability
现码：
```
.nbw-tree-error {
  padding: 0 8px 6px 30px;
```
这里的左内边距 `30px` 是对齐文件树缩进的魔数（TSX 里以 `paddingLeft: 6 + depth * 14` 内联计算），两处一旦调整（例如改缩进为 12px 或加图标列）就会静默错位；同时 `#c0392b` 与文件内其他错误色不统一。建议把行缩进下沉为 CSS 变量（如 `--nbw-tree-indent`），此处用 `calc(6px + var(--nbw-tree-indent) * 2)` 表达。
