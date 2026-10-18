# ocr (OpenCodeReview) 扫描原始发现

来源：本机 `ocr scan`（provider qwen-local / Qwen3.8-Flash-Next）。共 96 条去重后发现，按严重度分组；"锚点命中"= 评论里的 existing_code 能在当前工作区文件中逐字找到（防幻觉校验）。

严重度分布：critical 11 · high 26 · medium 43 · low 16

## app/templates/alerts.html（8）

### [critical/security] 锚点命中=true

XSS 风险：alert_type/type、message/description 直接拼进 innerHTML，未做任何 HTML 转义。告警内容通常来自被监控系统（日志、外部连接器、用户输入的错误信息等），若其中包含 `<img src=x onerror=...>` 之类的片段，会在管理端浏览器中执行，形成存储型/DOM XSS，配合 admin 权限可窃取 localStorage 中的 `cds_token`。admin.js 中目前没有 escapeHtml 工具函数，建议补充并在所有插值处使用。

```
<td>${a.alert_type || a.type || '-'}</td>
                <td>${truncate(a.message || a.description, 60)}</td>
```

<details><summary>建议改法</summary>

```
<td>${escapeHtml(a.alert_type || a.type || '-')}</td>
                <td>${escapeHtml(truncate(a.message || a.description, 60))}</td>
```
</details>

### [high/security] 锚点命中=false

XSS 风险：badge()/statusBadge() 内部同样是模板字符串拼接（`<span class="badge badge-${type}">${text}</span>`），此处把未转义的 `a.severity`、`status` 作为文本插入。后端若返回 `severity: "<img src=x onerror=alert(1)>"` 即可执行脚本；同时 severity 未被识别时还会拼出非法 class 名。建议在 badge/statusBadge 内统一转义，或在此处先用白名单归一化后再输出。

```
<td>${badge(a.severity || 'info', severityClass)}</td>
                <td>${statusBadge(status)}</td>
```

<details><summary>建议改法</summary>

```
<td>${badge(escapeHtml(a.severity || 'info'), severityClass)}</td>
                <td>${statusBadge(escapeHtml(status))}</td>
```
</details>

### [high/security] 锚点命中=true

XSS 风险：`API.get` 抛出的 Error message 形如 `${res.status}: ${await res.text()}`，即把整段响应体原文带进来，随后被直接写入 innerHTML。当后端以 4xx/5xx 回显请求参数、异常堆栈或上游返回内容时，攻击者可控文本会被当作 HTML 解析执行。此处应使用 textContent（或转义），并只展示可枚举的错误码/简短提示，避免透传原始响应体。

```
$('#alerts-table').innerHTML = `<tr><td colspan="6" class="empty">加载失败: ${e.message}</td></tr>`;
```

<details><summary>建议改法</summary>

```
const tr = document.createElement('tr');
        const td = document.createElement('td');
        td.colSpan = 6;
        td.className = 'empty';
        td.textContent = '加载失败，请稍后重试';
        tr.appendChild(td);
        $('#alerts-table').innerHTML = '';
        $('#alerts-table').appendChild(tr);
        console.error('load alerts failed', e);
```
</details>

### [high/bug] 锚点命中=true

时区/格式边界问题：`toISOString()` 返回的是 UTC 时间，截取到的日期与服务端本地时区（以及 `formatDate` 里 `toLocaleString('zh-CN')` 展示的本地时间）不一致，东八区下每天 00:00–08:00 产生的告警会被统计到“昨天”，导致“今日告警”长期偏低或跨天跳变。此外若 `created_at` 是 `"2026-10-18 14:17:00"`（空格分隔、无时区）或 epoch 秒，`startsWith(todayStr)` 会直接失配（或 formatDate 解析成 1970 年），统计恒为 0。建议统一按同一时区（如 `Asia/Shanghai`）比较，或直接用 `new Date(a.created_at)` 与本地当日 0 点做时间戳区间比较。

```
const todayStr = new Date().toISOString().slice(0, 10);
```

<details><summary>建议改法</summary>

```
const startOfDay = new Date();
        startOfDay.setHours(0, 0, 0, 0);
        const startTs = startOfDay.getTime();
```
</details>

### [high/bug] 锚点命中=true

空列表分支提前 return，导致两个问题：1) 三个统计卡片不会被写入 0，页面继续显示占位符 `-` 或上一次轮询的旧统计值（后续接入自动刷新后会出现“列表已清空但统计未归零”的错误展示）；2) 该分支只把 `#alerts-empty` 置为 block，而 `#alerts-table` 的旧行未清空，`#alerts-empty` 也从未在“有数据”时被重置回 `none`，一旦先空后有数据会同时出现“暂无告警”和表格行。建议在函数开头统一重置 empty 提示与统计值，并在各分支显式清空/填充表格。

```
if (!list.length) { $('#alerts-empty').style.display = 'block'; return; }
```

<details><summary>建议改法</summary>

```
$('#alerts-empty').style.display = list.length ? 'none' : 'block';
        $('#alerts-table').innerHTML = '';
        if (!list.length) {
            $('#active-alerts').textContent = 0;
            $('#critical-alerts').textContent = 0;
            $('#today-alerts').textContent = 0;
            return;
        }
```
</details>

### [medium/bug] 锚点命中=true

返回结构兼容性不足：`data.items || data.alerts || data || []` 假设三者之一必为数组。若接口返回 `{code, msg, data: {...}}` 这类包装体，`list` 会是普通对象，`list.length` 为 undefined → 被误判为“暂无告警”（掩盖真实异常）；若 `list` 是字符串或 `data.items` 为对象，则 `!list.length` 通过但 `list.map` 不存在，抛出 TypeError 后被 catch 成“加载失败”，错误信息与真实原因不符。建议显式做 `Array.isArray` 判定（含 `data.data`），不匹配时按数据结构异常单独提示。

```
const list = data.items || data.alerts || data || [];
```

<details><summary>建议改法</summary>

```
const candidates = [data && data.items, data && data.alerts, data && data.data, data];
        const list = candidates.find(Array.isArray) || [];
        if (!Array.isArray(list)) throw new Error('unexpected alerts payload');
```
</details>

### [medium/bug] 锚点命中=true

统计口径与大小写问题：critical 计数未区分状态，已 resolved/acknowledged 的历史告警也会计入“严重 (Critical)”卡片（而“活跃告警”按 active/firing 过滤），两个卡片口径不一致，容易误导值班判断；同时 `a.severity === 'critical'` 为大小写敏感比较，后端返回 `CRITICAL`/`Critical` 时会同时漏掉统计与样式（全部退化为 info）。建议统一先做 `String(a.severity).toLowerCase()` 归一化，并明确“严重”卡片是否只统计未恢复告警。

```
if (a.severity === 'critical') critical++;
            if ((a.created_at || a.timestamp || '').startsWith(todayStr)) today++;
            const severityClass = a.severity === 'critical' ? 'danger' : a.severity === 'warning' ? 'warning' : 'info';
```

<details><summary>建议改法</summary>

```
const severity = String(a.severity || 'info').toLowerCase();
            if (severity === 'critical' && isActive) critical++;
            if (isToday(a)) today++;
            const severityClass = severity === 'critical' ? 'danger' : severity === 'warning' ? 'warning' : 'info';
```
</details>

### [medium/maintainability] 锚点命中=true

功能不完整/可维护性：1) 表头有“操作”列但单元格恒为 `-`，未提供确认(ack)/恢复(resolve)/详情入口，而 `API.post/del` 已具备能力，告警治理闭环缺失；2) 在 `.map()` 中做统计副作用（边渲染边累加）使渲染与统计耦合，建议拆为 `computeStats(list)` 与 `renderRows(list)`；3) 列表一次性渲染全部数据且无分页/筛选参数、无自动轮询（其他管理页若已有轮询模式则此处不一致），告警量大时首屏 DOM 节点过多、且需手动刷新才能看到新告警。建议请求带 `limit/page`，并为统计与列表增加定时刷新（注意用自增序号或 AbortController 丢弃过期响应，避免竞态覆盖）。

```
<td>-</td>
```

<details><summary>建议改法</summary>

```
<td><button class="btn" data-id="${encodeURIComponent(String(a.id ?? ''))}" data-action="ack">确认</button></td>
```
</details>

## app/templates/base.html（5）

### [critical/security] 锚点命中=true

【XSS 风险 - 自动转义未生效】本文件所有动态块（`title`、`header`、`content`、`scripts`）的实际渲染由 `app/api/admin.py` 中 `_env = Environment(loader=FileSystemLoader(...))` 创建的环境决定，而 Jinja2 `Environment` 的 `autoescape` 默认值为 **False**。也就是说 `{% block title %}` / `{% block header %}` 一旦被任何子模板填入变量（或将来改为由后端传入页面标题、告警名、连接器名等），内容会原样写入 `<title>` / `<h1>`，直接构成 HTML/JS 注入点，且 `|e` 也不会被自动应用。建议：在创建环境时显式开启自动转义（`Environment(..., autoescape=select_autoescape(["html", "htm", "xml"]))`），并约定子模板中所有来自接口/DB 的变量必须走转义输出，禁止对块内容使用 `|safe`。

```
<title>{% block title %}CDS 密态沙箱{% endblock %}</title>
```

<details><summary>建议改法</summary>

```
<title>{% block title %}CDS 密态沙箱{% endblock %}</title>
```
</details>

### [high/security] 锚点命中=true

【属性位注入 + 无权限控制】两点问题：
1) `{% block nav_* %}` 的输出位置在 `class=""` 属性上下文内。由于当前 Jinja 环境未开启 autoescape，子模板只要传入含 `"`、`'` 或空格的内容即可闭合属性并追加 `onmouseover=...`/`onclick=...` 等事件属性，形成属性逃逸型 XSS。属性位内容应限定为白名单（如仅 `active` 或空串），或在环境开启 autoescape 后用 `{% block nav_identity %}{{ '' }}{% endblock %}` 形式并配合 `|e`。
2) 这五个入口（身份/密钥、证书、连接器、告警、区块链验证）是平台最敏感的管理面，但模板对导航项做了无条件渲染，任何拿到 `/admin/*` 页面的人都能看到完整攻击面清单；而 `app/api/admin.py` 注册的 `/admin` 路由本身没有任何鉴权依赖（`app/main.py` 中 `_register_admin_pages` 也只受 `ADMIN_PAGES_ENABLED` 开关控制）。建议：导航项按当前登录用户的角色/功能组（对应 `require_group`/`require_human` 的分组）条件渲染，并在 `/admin` 路由上补齐会话与角色校验，避免“页面可匿名访问、只有接口有鉴权”造成的信息泄露与 CSRF/探测面。

```
<li><a href="/admin/identities" class="{% block nav_identity %}{% endblock %}">身份/密钥管理</a></li>
```

### [high/security] 锚点命中=true

【脚本块缺少 CSP / SRI 约束】`{% block scripts %}` 允许子模板自由追加内联 `<script>`（如 `alerts.html` 中的 `loadAlerts()`），本文件加载 `/static/js/admin.js` 也没有 `integrity`/`crossorigin`；同时全链路未见 CSP 头。叠加后果被放大：`app/static/js/admin.js` 把访问令牌存放在 `localStorage('cds_token')`，任何一次内联脚本注入（模板未转义 + 无 CSP）都能直接窃取 Bearer 令牌，进而以人类身份调用全部管理接口。建议：(1) 通过中间件下发 `Content-Security-Policy`（`script-src 'self'`，尽量不用 `'unsafe-inline'`，必要时改用 nonce）；(2) 静态资源加 SRI；(3) 令牌改由 HttpOnly + SameSite=Strict Cookie 承载，避免 XSS 直接读取。

```
<script src="/static/js/admin.js"></script>
    {% block scripts %}{% endblock %}
```

### [medium/maintainability] 锚点命中=true

【硬编码路径，部署在子路径下会 404】静态资源与导航链接全部写死为根路径 `/static/...`、`/admin/...`。当服务挂在反向代理子路径（如 `https://host/cds/admin/`）或带 `SCRIPT_NAME` 前缀部署时，CSS/JS 会 404 导致页面完全无样式、无脚本；同时路径前缀与 `app/api/admin.py` 的 `prefix="/admin"` 形成隐式重复，改路由前缀必须同步改模板。建议改用 `url_for('admin:identities_page')` / `request.script_name` 拼接（或在模板上下文注入 `STATIC_URL`、`ADMIN_BASE` 变量），集中管理前缀。

```
<link rel="stylesheet" href="/static/css/admin.css">
```

### [low/maintainability] 锚点命中=true

【页面骨架不完整，影响可访问性与状态展示】作为全站基础模板缺少几处必要结构：(1) 没有用户/会话区与退出入口，管理员在敏感页面无法确认当前身份与租户（也与“无鉴权”问题相互掩盖）；(2) 没有 flash/全局错误容器，子页面只能各自把错误写进 `#app`（`admin.js` 的 `loadPage` 直接 `$('#app').innerHTML = ...` 会把整块业务内容连同表单一起清空）；(3) 侧边导航是 `nav` 内的裸 `ul`，缺少 `aria-label`、当前页 `aria-current="page"`，主内容区也未用 `<main>` 语义标签关联；(4) 若子模板依赖 JS 拉取数据，缺少 `<noscript>` 兜底。建议在基模板统一补上会话信息条、全局消息容器与语义/ARIA 属性，避免每个子页面重复实现且行为不一致。

```
<div id="app">
            {% block content %}{% endblock %}
        </div>
```

## app/templates/blockchain.html（9）

### [critical/security] 锚点命中=true

存储型 XSS：`detail` 直接来自后端 `/api/v1/audit/records` 返回的 `AuditLog.detail`（任意 JSON），`resource_id` 也是各业务写入的自由文本，这些值经 `truncate()`（app/static/js/admin.js 中 `truncate` 只做 slice，不做任何转义）后直接拼进 `innerHTML`。只要任意一条审计行的 `detail.merkle_root` / `detail.tx_hash` / `resource_id` 含 `<img src=x onerror=...>`，在监管/运维打开本页面时即执行脚本，可窃取 `localStorage` 里的 `cds_token`（见 admin.js `_headers`）。本文件 base.html 也未下发 CSP，没有任何兜底。建议渲染前统一转义。

```
$('#anchors-table').innerHTML = displayList.map(a => {
            const detail = a.detail || {};
            if (detail.merkle_root || detail.tx_hash) verified++;
```

<details><summary>建议改法</summary>

```
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
        $('#anchors-table').innerHTML = displayList.map(a => {
            const detail = a.detail || {};
            if (detail.merkle_root || detail.tx_hash) verified++;
```
</details>

### [critical/security] 锚点命中=true

同上：这两行把后端可控字段直接插入 HTML。哈希类字段更稳妥的做法是用正则做白名单校验（如 `/^[0-9a-fA-F]{16,128}$/`），不符合格式一律显示 `-`，既避免 XSS 也避免脏数据把表格撑坏；`block_height` 同理需要 `esc()`/数字校验。

```
<td><code>${truncate(detail.merkle_root || a.resource_id || '-', 24)}</code></td>
                <td><code>${truncate(detail.tx_hash || '-', 24)}</code></td>
```

<details><summary>建议改法</summary>

```
<td><code>${esc(truncate(detail.merkle_root || a.resource_id || '-', 24))}</code></td>
                <td><code>${esc(truncate(detail.tx_hash || '-', 24))}</code></td>
```
</details>

### [high/bug] 锚点命中=true

两处问题：1）`e.message` 由 API.get 构造为 `${res.status}: ${await res.text()}`，即后端响应体原文（FastAPI 的 400/404 detail 会回显入参、网关/HTML 错误页也在此处），直接写入 `innerHTML` 同样构成注入点；2）把后端原始错误（含 SQL/内部路径等实现细节）暴露给终端用户，属于敏感信息泄露。应改为固定文案 + console 记录，必要时用 textContent 渲染。

```
$('#anchors-table').innerHTML = `<tr><td colspan="5" class="empty">加载失败: ${e.message}</td></tr>`;
```

<details><summary>建议改法</summary>

```
console.error('loadAnchors failed', e);
        $('#anchors-empty').style.display = 'block';
        $('#anchors-table').innerHTML = '<tr><td colspan="5" class="empty">加载失败，请稍后重试</td></tr>';
```
</details>

### [high/bug] 锚点命中=true

字段名与后端契约不符：`GET /api/v1/verify/{record_id}`（app/api/audit.py `verify_audit_record`）返回的是 `verified`，没有 `valid` 字段，因此 `result.valid` 恒为 undefined，即使链上校验通过也永远显示红色「失败」，直接误导审计结论。同时后端明确返回的 `verification_note` / `backend`（是否联盟链、pg-log 还是 fisco/ant）被完全丢弃，而这两字段是后端 T8/F1「诚实披露锚定后端」的强制要求，前端必须展示，否则用户会把本地 PG 追加日志当成联盟链存证。另外 `record_id`、`merkle_root` 未转义，仍是 XSS 注入点。

```
<p><strong>验证结果:</strong> ${result.valid ? badge('通过', 'success') : badge('失败', 'danger')}</p>
            <p><strong>Record ID:</strong> <code>${result.record_id || recordId}</code></p>
            <p><strong>Merkle Root:</strong> <code>${result.merkle_root || '-'}</code></p>
```

<details><summary>建议改法</summary>

```
<p><strong>验证结果:</strong> ${result.verified ? badge('通过', 'success') : badge('失败', 'danger')}</p>
            <p><strong>Record ID:</strong> <code>${esc(result.record_id || recordId)}</code></p>
            <p><strong>Merkle Root:</strong> <code>${esc(result.merkle_root || '-')}</code></p>
            <p><strong>锚定后端:</strong> ${esc(result.backend ? result.backend.backend_label : '未知')}</p>
            <p><strong>说明:</strong> ${esc(result.verification_note || '-')}</p>
```
</details>

### [high/security] 锚点命中=true

两点问题：1）用户输入未经校验/编码直接拼进请求路径，`/`、`?`、`#`、空格等字符可改变实际请求路径与查询串（路径穿越/参数注入面），必须 `encodeURIComponent` 并做格式白名单；2）后端 `verify_audit_record` 只接受 UUID `record_id`（`uuid.UUID(record_id)`，否则 400），根本不支持 tx_hash 查询，而输入框 placeholder 与提示语都在引导用户输入 tx_hash，用户按提示输入必然报错。另外 catch 中 `'验证失败: ' + e.message` 同样把后端响应原文弹给用户（泄露内部错误），建议提示统一文案。

```
const recordId = $('#tx-hash').value.trim();
    if (!recordId) { alert('请输入 record_id 或 tx_hash'); return; }
    try {
        const result = await API.get(`/api/v1/audit/verify/${recordId}`);
```

<details><summary>建议改法</summary>

```
const recordId = $('#tx-hash').value.trim();
    if (!recordId) { alert('请输入 record_id'); return; }
    if (!/^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/.test(recordId)) {
        alert('record_id 必须为合法 UUID'); return;
    }
    try {
        const result = await API.get(`/api/v1/audit/verify/${encodeURIComponent(recordId)}`);
```
</details>

### [medium/bug] 锚点命中=true

统计口径错误：过滤不到锚定记录时回退展示「全部前 20 条审计记录」，并把它们计入「锚定记录」卡片（`#total-anchors = displayList.length`），等于把普通业务审计行伪装成 Merkle 锚定记录展示给监管方；`verified` 也在同一批数据上累加，两个统计互相矛盾且完全失真。正确做法是无数据时如实显示空列表与 0，而不是拼一批无关数据。

```
const anchorRecords = list.filter(r => r.action && (r.action.includes('anchor') || r.action.includes('blockchain')));
        const displayList = anchorRecords.length ? anchorRecords : list.slice(0, 20);
```

<details><summary>建议改法</summary>

```
const anchorRecords = list.filter(r => r.action && (r.action.includes('anchor') || r.action.includes('blockchain')));
        const displayList = anchorRecords;
```
</details>

### [medium/bug] 锚点命中=true

状态推断不成立：仅凭 `detail.tx_hash` 存在即显示绿色「verified」，但后端真正的锚定真值字段是行上的 `blockchain_tx_hash`（见 `/records` 返回体），且是否真正校验通过只有 `/verify` 接口能给出；当前实现会让任何写了 tx_hash 字符串（甚至伪造字符串）的记录都显示「已验证」。此外 `block_height` 字段后端并不存在（锚定/验证接口用的是 `block_number`，PG 追加日志场景为 null 且明确要求不得伪造 0），这里会恒显示 `-`。

```
<td>${detail.block_height || '-'}</td>
                <td>${statusBadge(detail.tx_hash ? 'verified' : 'pending')}</td>
```

<details><summary>建议改法</summary>

```
<td>${a.blockchain_tx_hash ? (detail.block_number ?? '-') : '-'}</td>
                <td>${statusBadge(a.blockchain_tx_hash ? 'pending' : 'pending')}</td>
```
</details>

### [medium/bug] 锚点命中=true

前端分页缺陷：`/api/v1/audit/records` 是分页接口（默认只返回第一页，返回体含 `total`/`page_size`），这里不带任何 `limit`/分页参数拉取，然后在「第一页」里做 action 过滤，导致真正的锚定记录只要不在第一页就永远看不到，「锚定记录」数字实为当前页命中数而非总量（应使用 `data.total` 或带过滤条件的后端查询，如 `?action=` / 专用锚定列表接口）。另外当后端返回错误对象（如 `{detail: ...}`）时 `list` 是对象，`!list.length` 为真会静默显示「暂无锚定记录」，把接口异常掩盖成空数据；建议加 `Array.isArray(list)` 判断。

```
const data = await API.get('/api/v1/audit/records');
        const list = data.items || data.records || data || [];
```

<details><summary>建议改法</summary>

```
const data = await API.get('/api/v1/audit/records?limit=200');
        const list = Array.isArray(data.items) ? data.items : (Array.isArray(data.records) ? data.records : []);
```
</details>

### [low/maintainability] 锚点命中=true

提前 return 时没有把 `#total-anchors` / `#verified-anchors` 置为 0，两个统计卡片会一直停在初始的 `-`，与「暂无锚定记录」的空态不一致；同时 map 中带 `verified++` 副作用可读性差，建议把统计计算与渲染分离。

```
if (!list.length) { $('#anchors-empty').style.display = 'block'; return; }
```

<details><summary>建议改法</summary>

```
if (!list.length) {
            $('#anchors-empty').style.display = 'block';
            $('#total-anchors').textContent = '0';
            $('#verified-anchors').textContent = '0';
            return;
        }
```
</details>

## app/templates/certificates.html（11）

### [critical/security] 锚点命中=true

存储型 XSS：subject / organization / cert_id 等字段在本页「签发新证书」表单中由用户自由输入并写入后端，列表渲染时又通过 `truncate()`（已确认 admin.js 中的 truncate 不做任何转义）直接拼进 `innerHTML`。攻击者只要签发一个 subject 为 `<img src=x onerror=...>` 或 `<script>` 的证书，任何管理员打开本页即执行脚本；由于 admin.js 的 token 存在 `localStorage('cds_token')`，脚本可直接窃取管理令牌并调用全部管理接口。statusBadge(status) 同理会把未知 status 原样输出。建议引入统一的 `escapeHtml()`（对 & < > " ' 转义）包裹所有插值，或改用 `textContent` / `createElement` 构建 DOM。

```
<td><code>${truncate(c.cert_id || c.id, 20)}</code></td>
                <td>${c.subject || c.subject_cn || '-'}</td>
                <td>${c.cert_type || c.type || '-'}</td>
```

<details><summary>建议改法</summary>

```
<td><code>${escapeHtml(truncate(c.cert_id || c.id, 20))}</code></td>
                <td>${escapeHtml(c.subject || c.subject_cn || '-')}</td>
                <td>${escapeHtml(c.cert_type || c.type || '-')}</td>
```
</details>

### [critical/security] 锚点命中=true

内联事件 JS 注入：cert_id 未做任何 JS 字符串转义就插入单引号包裹的 onclick。ID 只要包含 `'`（或 `\`、`</script>`）即可闭合字符串并注入任意 JS，例如 cert_id 为 `1');alert(document.cookie);//`。另外当 `c.cert_id`/`c.id` 都缺失时，onclick 会渲染成字面量 `verifyCert('undefined')`，向接口请求一个不存在的资源。建议改为 data 属性 + 事件委托，彻底避免把数据放进 JS 字面量。

```
<button class="btn btn-sm btn-primary" onclick="verifyCert('${c.cert_id || c.id}')">验证</button>
                    <button class="btn btn-sm btn-danger" onclick="revokeCert('${c.cert_id || c.id}')">吊销</button>
```

<details><summary>建议改法</summary>

```
<button class="btn btn-sm btn-primary" data-action="verify" data-id="${escapeHtml(c.cert_id || c.id)}">验证</button>
                    <button class="btn btn-sm btn-danger" data-action="revoke" data-id="${escapeHtml(c.cert_id || c.id)}" ${status === 'revoked' ? 'disabled' : ''}>吊销</button>
```
</details>

### [high/security] 锚点命中=true

签发结果渲染的三个问题：1) cert_id / subject / PEM 全部未转义直出 innerHTML，后端回显的 subject 可携带脚本形成 XSS（与列表同源风险，且此处响应完全由服务端控制，更难防御）。2) HTML 结构非法：`<pre>` 不能作为 `<p>` 的子元素，浏览器会在 `<pre>` 前自动闭合 `<p>`，导致结果卡片布局错乱。3) 用 `truncate(..., 200)` 截断 PEM：用户既拿不到完整证书链，也没有复制/下载入口，实际功能不可用；若响应中还含私钥，截断展示并不能降低风险（完整密钥已在 JS 内存/DOM 中），应改为不落盘的显式下载/一次性复制交互，并避免打印敏感密钥材料。

```
$('#cert-result').innerHTML = `<div class="card" style="background:var(--bg);margin-top:12px;">
```

### [medium/bug] 锚点命中=true

空列表分支的状态管理错误：1) 提前 return 后从不清空 `#certs-table.innerHTML`，当吊销/刷新后列表变空时，旧数据行仍显示在「暂无证书」提示上方（脏数据）；2) 统计卡片 `#total-certs / #valid-certs / #revoked-certs` 未重置为 0，会残留上一次（或初始 `-`）的值；3) 反向分支缺失——列表非空时从不把 `#certs-empty` 重新隐藏，一旦首次加载为空、随后签发了证书，「暂无证书」提示会一直错误地显示。建议先统一重置表格/空提示/统计，再按数据渲染。

```
if (!list.length) { $('#certs-empty').style.display = 'block'; return; }
```

### [medium/bug] 锚点命中=true

两处问题：1) `e.message` 由 admin.js 用 `${res.status}: ${await res.text()}` 拼成，即后端响应体原文（400 校验错误常会回显用户提交的 subject/organization），这里再拼进 innerHTML 等于又开一条反射型 XSS 通道，且会把后端原始报错暴露给 UI；应使用 textContent 或预定义的友好提示。2) catch 分支只替换表格，不重置 `#certs-empty` 与三个统计值，加载失败后页面会同时显示「加载失败」和上一次的过期统计数字，与 focus area「统计与加载状态正确性」不一致。

```
$('#certs-table').innerHTML = `<tr><td colspan="6" class="empty">加载失败: ${e.message}</td></tr>`;
    }
}
```

### [medium/bug] 锚点命中=true

签发前缺少输入校验：subject / organization 未做 trim、空值与长度（X.509 CommonName 上限 64 字符）校验，直接提交会得到后端 4xx 或生成非法证书；`validity_days` 也没有范围校验。此外整个流程没有防重复提交（签发期间按钮仍可点击），可能并发签发出多张重复证书。建议加前端校验并在请求期间禁用按钮。

```
const body = {
        subject: $('#cert-cn').value,
        organization: $('#cert-org').value,
```

### [medium/bug] 锚点命中=false

证书类型映射存在信息丢失（原三元表达式把 root 与 intermediate 都折叠成 `'signing'`，endpoint 一律映射为 `'encryption'`）：后端无法区分 Root CA / Intermediate CA（缺 basicConstraints CA:TRUE、pathlen 语义），也无法表达终端实体证书通常需要同时具备 digitalSignature + keyEncipherment 的用途；一旦签发就无法纠正。建议把原始 `cert_type` 直接传给后端（或明确定义 role + key_usages 两个字段），由后端统一推导。`parseInt` 也应加上基数 10 并对 NaN 做兜底。

```
cert_type: $('#cert-type').value === 'root' ? 'signing' : $('#cert-type').value === 'intermediate' ? 'intermediate' : 'encryption',
        validity_days: parseInt($('#cert-validity').value),
```

### [medium/security] 锚点命中=true

id 未经 `encodeURIComponent` 直接拼进 URL 路径，若 id 含 `/`、`?`、`#`、`..`（这些 id 可能来自上一步被注入的响应数据），会改变实际请求的目标路径，形成路径穿越式越权调用（如 `/api/v1/certificates/1/../../keys`）。revokeCert 中的 `${id}` 同样存在该问题。

```
const result = await API.get(`/api/v1/certificates/${id}/verify-chain`);
```

### [medium/bug] 锚点命中=true

吊销流程的健壮性问题：1) admin.js 的 `del()` 无条件 `res.json()`，若 DELETE 返回 204/空响应体将抛出 JSON 解析异常，被 catch 成「吊销失败」弹窗，而此时后端实际已吊销成功且级联挂起了 DEK/会话，界面状态与真实状态不一致（必须手动刷新才恢复）。2) `loadCerts()` 未 await，调用方无法确认列表已刷新。3) 请求期间「吊销」按钮未禁用，双击会发出两次 DELETE。建议：`await loadCerts()`、按 `res.status`/空体区分成功、请求期间禁用按钮；同时该操作是级联不可逆高危操作，仅一次 confirm 偏弱，建议要求输入证书 ID 确认。

```
await API.del(`/api/v1/certificates/${id}`);
        loadCerts();
```

### [low/bug] 锚点命中=true

统计口径不完整：statusBadge 已支持 expired / pending / suspended 等状态，但这里既不计入「有效证书」也不计入「已吊销」，导致 total 与 valid+revoked 不等，且 `status` 缺省被硬编码为 `'valid'`（后端未返回 status 字段的证书会被虚报为有效证书），过期证书也被当作有效。建议显式维护 expired/pending 计数或增加对应统计卡片，缺省状态应显示为 unknown 而非 valid。

```
if (status === 'valid' || status === 'active') valid++;
            if (status === 'revoked') revoked++;
```

### [low/test] 锚点命中=true

关键业务路径缺乏可测试性：证书签发/验证/吊销与统计逻辑全部写在 Jinja 模板的内联 `<script>` 中，无法被单元测试覆盖（状态计数、缺省 status 处理、空列表、错误分支均无对应测试）。建议把该页面脚本抽为 `static/js/certificates.js` 独立模块并导出纯函数（如 `computeStats(list)`、`renderCerts(list)`），配合前端测试覆盖空列表/未知状态/接口异常等边界；同时模板内大量内联 style 也建议归入 admin.css 以保持与其他页面一致的风格。

```
loadCerts();
</script>
```

## app/templates/connectors.html（8）

### [critical/bug] 锚点命中=true

注册请求与后端契约完全不一致：`app/api/connectors.py` 中 `POST /register` 的参数是 **查询参数** `space_id`、`space_name`、`space_url`（`description` 可选），而这里以 JSON body 提交 `space_name / space_type / endpoint_url`，既没有 `space_id`，也没有 `space_url`（`endpoint_url` 后端不识别），必然返回 422，注册功能实际不可用。此外表单里根本没有 `space_id`（外部空间唯一标识）输入项，需要补充。另注：后端注册成功时会返回一次性 `api_key`，此处直接丢弃响应，用户之后只能靠 rotate-key 才能拿到密钥，应在此处展示并提示妥善保存。

```
const body = {
        space_name: $('#conn-name').value,
        space_type: $('#conn-type').value,
        endpoint_url: $('#conn-url').value,
    };
    try {
        await API.post('/api/v1/connectors/register', body);
        loadConnectors();
```

<details><summary>建议改法</summary>

```
const spaceId = $('#conn-space-id').value.trim();
    const spaceName = $('#conn-name').value.trim();
    const spaceUrl = $('#conn-url').value.trim();
    const params = new URLSearchParams({ space_id: spaceId, space_name: spaceName, space_url: spaceUrl });
    try {
        const res = await API.post(`/api/v1/connectors/register?${params}`, null);
        if (res && res.api_key) {
            alert(`注册成功，请立即保存 API Key（仅此一次展示）：\n${res.api_key}`);
        }
        loadConnectors();
```
</details>

### [critical/security] 锚点命中=true

XSS 风险：`space_name` 等字段来自外部注册的连接器（外部可信数据空间自行命名），此处直接拼进 `innerHTML`，攻击者只需把连接器名称设为 `<img src=x onerror=...>` 即可在管理员页面执行任意脚本（存储型 XSS，且目标是 admin 后台）。`statusBadge()` 内部同样是模板字符串拼接，`c.status` 未转义。必须统一做 HTML 转义。

```
return `<tr>
                <td>${c.space_name || c.name || '-'}</td>
```

<details><summary>建议改法</summary>

```
const esc = s => String(s ?? '').replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
            return `<tr>
                <td>${esc(c.space_name || c.name || '-')}</td>
```
</details>

### [high/security] 锚点命中=true

两处问题：1) `c.id` 被插入到 `onclick="..."` 的单引号 JS 字符串上下文中，`'`/`)`/`</script>` 等字符可闭合属性并注入脚本，与上面的 XSS 是同一类根因；2) 状态为 `suspended` 时按钮文案显示“恢复”，但点击仍调用 `/suspend`，永远无法恢复。应改用 `data-id` + 事件委托，并按状态选择 `/suspend` 或 `/reactivate`。

```
<button class="btn btn-sm btn-primary" onclick="suspendConnector('${c.id}')">${c.status === 'suspended' ? '恢复' : '暂停'}</button>
```

<details><summary>建议改法</summary>

```
<button class="btn btn-sm btn-primary" data-conn-id="${esc(c.id)}" data-action="${c.status === 'suspended' ? 'reactivate' : 'suspend'}">${c.status === 'suspended' ? '恢复' : '暂停'}</button>
```
</details>

### [high/bug] 锚点命中=true

按钮文案与调用接口不一致，且请求体不符合后端校验：`/suspend` 与 `/reactivate` 都要求 `HighRiskOperationRequest`，其中 `reason` 为必填且 `min_length=8`，这里传 `{}` 必定 422；同时“恢复”分支仍然调用 `/suspend`（后端 `/reactivate` 才会把状态改回 active，且 `/suspend` 对已 suspended 的连接器是幂等无效操作）。高风险操作还应弹框收集 reason/ticket_id 并做二次确认。

```
async function suspendConnector(id) {
    try {
        await API.post(`/api/v1/connectors/${id}/suspend`, {});
        loadConnectors();
    } catch (e) { alert('操作失败: ' + e.message); }
}
```

<details><summary>建议改法</summary>

```
async function toggleConnector(id, action) {
    const reason = prompt(`请输入${action === 'suspend' ? '暂停' : '恢复'}原因（不少于 8 字）：`);
    if (!reason || reason.trim().length < 8) return;
    try {
        await API.post(`/api/v1/connectors/${id}/${action}`, { reason: reason.trim() });
        loadConnectors();
    } catch (e) { alert('操作失败: ' + e.message); }
}
```
</details>

### [high/security] 锚点命中=true

`API.get` 抛出的 Error message 形如 `${res.status}: ${await res.text()}`，包含服务端返回的**原始响应体**；这里再把它拼进 `innerHTML`，等于把服务端/请求路径可控文本当作 HTML 渲染，可能形成反射型 XSS 或页面结构破坏（同时把内部错误细节直接暴露给 UI）。应使用 `textContent` 或转义后再插入。

```
$('#connectors-table').innerHTML = `<tr><td colspan="5" class="empty">加载失败: ${e.message}</td></tr>`;
```

<details><summary>建议改法</summary>

```
$('#connectors-empty').style.display = 'block';
        $('#connectors-empty').textContent = `加载失败: ${e.message}`;
```
</details>

### [medium/bug] 锚点命中=true

响应形态与统计逻辑存在多个缺陷：1) `data.items || data.connectors || data || []` 在 `items` 为空数组时会回退成 `data` 对象（truthy），`list` 变成 `{items:[], total:...}`，靠 `!list.length` 侥幸走空分支，语义完全靠巧合；2) 提前 `return` 导致“连接器总数/活跃连接”停留在 `-` 而不是 0，也没有清空已渲染的表格行、不会重新隐藏 `#connectors-empty`；3) 后端 list 接口默认 `limit=100` 且翻页时 `total` 为 `null`（`X-Next-Cursor`），`list.length` 只是首页条数，作为“总数”不准确；4) 每次 `map` 内副作用累加 `active` 可读性差。建议显式 `Array.isArray` 判定并用 `data.total`。

```
const data = await API.get('/api/v1/connectors/');
        const list = data.items || data.connectors || data || [];
        if (!list.length) { $('#connectors-empty').style.display = 'block'; return; }
```

<details><summary>建议改法</summary>

```
const data = await API.get('/api/v1/connectors/');
        const list = [data.items, data.connectors].find(Array.isArray) || (Array.isArray(data) ? data : []);
        $('#total-connectors').textContent = (data.total != null ? data.total : list.length);
        if (!list.length) {
            $('#connectors-table').innerHTML = '';
            $('#active-connectors').textContent = '0';
            $('#connectors-empty').style.display = 'block';
            return;
        }
        $('#connectors-empty').style.display = 'none';
```
</details>

### [medium/bug] 锚点命中=true

字段名与后端 `_connector_payload` 返回的字段不匹配：payload 中没有 `space_type` / `connector_type`（类型信息在 `supported_protocols`），也没有 `updated_at`（心跳时间是 `last_heartbeat`）。结果是“类型”列恒为 `-`，“最后同步”列恒显示 `created_at`（注册时间）而非最后心跳，管理员会误判连接器健康状况；此外 payload 还包含 `is_healthy`，未用于状态展示。

```
<td>${c.space_type || c.connector_type || '-'}</td>
                <td>${statusBadge(c.status || 'active')}</td>
                <td>${formatDate(c.updated_at || c.created_at)}</td>
```

<details><summary>建议改法</summary>

```
<td>${esc((c.supported_protocols || []).join(', ') || '-')}</td>
                <td>${statusBadge(c.status || 'active')}${c.is_healthy === false ? ' <span class="badge badge-danger">心跳丢失</span>' : ''}</td>
                <td>${formatDate(c.last_heartbeat)}</td>
```
</details>

### [medium/maintainability] 锚点命中=true

表单缺少校验与重复提交防护：名称/URL 允许为空提交（后端注册后出现空名连接器），URL 无协议/格式校验，也没有 `space_id` 字段；请求进行中按钮不 disable，双击会重复发起注册（后端按 `space_id` 去重的查询与插入非原子，并发下还可能绕过 409 校验产生重复记录）。另外注册成功后输入框未清空。

```
<button class="btn btn-primary" style="margin-top:12px;" onclick="registerConnector()">注册</button>
```

<details><summary>建议改法</summary>

```
<button class="btn btn-primary" id="conn-register-btn" style="margin-top:12px;" onclick="registerConnector()">注册</button>
    <!-- 建议：input 增加 required/pattern 校验，registerConnector 内在 try 前 disabled=true、finally 中 disabled=false -->
```
</details>

## app/templates/identities.html（10）

### [critical/security] 锚点命中=true

存储型 XSS：整段行模板通过 `innerHTML` 拼接，`key_id / product_id / algorithm / status` 全部未做 HTML 转义。`admin.js` 中的 `truncate()`、`statusBadge()`（内部 `badge()`）都是直接字符串拼接，不转义，因此只要 KMS 中任一 DEK 的 `product_id` 或 `key_id`（通常由上游接口/用户输入派生）包含 `<img src=x onerror=...>` 之类内容，管理员打开本页即执行任意脚本，配合 `localStorage` 中的 `cds_token` 会造成管理端凭证被窃取。

建议先在 admin.js 增加统一的 `escapeHtml()`（转义 & < > " '），`badge/truncate` 内部调用或在此处对所有插值统一包裹 `${escapeHtml(k.product_id || '-')}`；更彻底的方案是用 `document.createElement` + `textContent` 构建行。

```
<td><code>${truncate(k.key_id, 30)}</code></td>
                <td>${k.product_id || '-'}</td>
                <td>${k.algorithm || 'SM4-GCM'}</td>
                <td>${statusBadge(k.status)}</td>
```

<details><summary>建议改法</summary>

```
<td><code>${escapeHtml(truncate(k.key_id, 30))}</code></td>
                <td>${escapeHtml(k.product_id || '-')}</td>
                <td>${escapeHtml(k.algorithm || 'SM4-GCM')}</td>
                <td>${statusBadge(k.status)}</td>
```
</details>

### [critical/security] 锚点命中=true

内联事件属性注入：`key_id` 被直接插入单引号包裹的 `onclick` 属性中。若 `key_id` 含 `'`（或 `"`、`</td>` 等），会提前闭合属性/标签，注入任意 JS，例如 key_id 为 `a');alert(document.cookie);('` 即可执行；即使不含恶意字符，含引号或反斜杠的合法 ID 也会造成语法错误、按钮完全失效。同时销毁是不可逆操作，被注入后可在管理员无感知的情况下批量销毁密钥。

建议去掉内联 onclick，改用 data 属性 + 事件委托：
`<td><button class="btn btn-danger btn-sm" data-destroy="${escapeHtml(k.key_id)}">销毁</button></td>`，然后 `tbody.addEventListener('click', e => { const id = e.target.dataset.destroy; if (id) destroyKey(id); });`

```
<td><button class="btn btn-danger btn-sm" onclick="destroyKey('${k.key_id}')">销毁</button></td>
```

<details><summary>建议改法</summary>

```
<td><button class="btn btn-danger btn-sm" data-key-id="${escapeHtml(k.key_id)}">销毁</button></td>
```
</details>

### [high/bug] 锚点命中=true

响应形状假设不成立，且空/异常分支会残留脏数据：
1) `data.items || data.keys || data` 在后端返回 `{detail: "..."}`、`{code, data}` 包装体或 `{total, items}` 但 items 为 null 时，`list` 会是非数组对象；`list.length` 为 undefined 时走空分支静默吞掉，若 `list` 有 length 但不是数组则下一行 `list.map` 直接抛 TypeError。
2) 空列表分支只设置了 `#keys-empty` 就直接 return：既没有清空 `tbody`（上一轮的旧行仍显示，出现“暂无密钥”与旧行并存），也没有把 `#total-keys/#active-keys/#revoked-keys` 复位为 0，统计与列表状态不一致。
3) 成功分支没有隐藏 `#keys-empty`，一旦先出现过空态再切换到有数据，空提示会一直挂在那里。

建议：`const list = Array.isArray(data) ? data : (Array.isArray(data.items) ? data.items : (Array.isArray(data.keys) ? data.keys : []));`，并在函数开头统一 `tbody.innerHTML=''; $('#keys-empty').style.display='none';`，根据 list 长度再决定显示空态。

```
const list = data.items || data.keys || data || [];
        if (!list.length) {
            $('#keys-empty').style.display = 'block';
            return;
        }
```

<details><summary>建议改法</summary>

```
const raw = Array.isArray(data) ? data : (Array.isArray(data.items) ? data.items : (Array.isArray(data.keys) ? data.keys : []));
        const tbody = $('#keys-table');
        tbody.innerHTML = '';
        $('#keys-empty').style.display = raw.length ? 'none' : 'block';
        const list = raw;
        if (!list.length) {
            $('#total-keys').textContent = '0';
            $('#active-keys').textContent = '0';
            $('#revoked-keys').textContent = '0';
            return;
        }
```
</details>

### [high/security] 锚点命中=true

两个问题：
1) 敏感密钥材料泄露：把 DEK 明文（`key_bytes`）直接渲染进 DOM，会长期驻留在页面源码/浏览器历史/自动补全缓存中，并可能被浏览器插件、截图、XSS 载荷一次性读取。明文密钥还应“仅展示一次、不落 DOM”，建议不渲染 hex，改为一次性“复制到剪贴板/下载”并给出警示文案，函数离开视图时主动 `$('#key-result').innerHTML=''`；若后端确实会返回 key_bytes，更应在服务端止住（只返回密文/wrapped key）。
2) 同一处 `result.key_id`/`result.key_bytes` 未转义直接 innerHTML，属于与列表相同的注入面。
3) `loadKeys()` 未 await 且与结果展示并发，用户可能在列表刷新完成前继续操作。

```
$('#key-result').innerHTML = `<div class="card" style="background:var(--bg);">
            <p><strong>Key ID:</strong> <code>${result.key_id}</code></p>
            <p><strong>Key (hex):</strong> <code style="word-break:break-all;">${result.key_bytes || '(stored in KMS)'}</code></p>
        </div>`;
        loadKeys();
```

<details><summary>建议改法</summary>

```
$('#key-result').innerHTML = `<div class="card" style="background:var(--bg);">
            <p><strong>Key ID:</strong> <code>${escapeHtml(result.key_id)}</code></p>
            <p>密钥材料已由 KMS 安全托管，不在页面明文展示。</p>
        </div>`;
        await loadKeys();
```
</details>

### [high/bug] 锚点命中=true

路径参数未编码：`keyId` 直接拼进 URL，若含 `/`、`?`、`#`、`..` 等字符会改变实际请求目标（如 `keys/a/../../users` 触发越权/误删其他资源，`#` 之后的内容会被丢弃导致删错对象）。必须 `encodeURIComponent(keyId)`。
另外：销毁为不可逆操作，但按钮无 pending 状态，双击/重复点击会重复发 DELETE；`confirm` 只展示 ID，缺乏强确认。建议禁用按钮 + 输入 key_id 后 6 位等方式二次确认，并在 finally 中恢复按钮、`await loadKeys()`。

```
await API.del(`/api/v1/kms/keys/${keyId}`);
        loadKeys();
```

<details><summary>建议改法</summary>

```
await API.del(`/api/v1/kms/keys/${encodeURIComponent(keyId)}`);
        await loadKeys();
```
</details>

### [medium/security] 锚点命中=true

`admin.js` 中 API 客户端用 `new Error(`${res.status}: ${await res.text()}`)` 把响应体原文塞进 message，这里又用 `innerHTML` 渲染，等于把服务端（或中间层）返回的任意内容当作 HTML 解析——一旦接口返回 4xx/5xx 且响应体包含回显的用户数据/HTML，就形成 DOM XSS；错误堆栈与内部报错细节也会直接暴露给前端。建议改为 `textContent` 赋值，并展示通用提示，原始信息只写入 console。

```
$('#keys-table').innerHTML = `<tr><td colspan="7" class="empty">加载失败: ${e.message}</td></tr>`;
```

<details><summary>建议改法</summary>

```
const msg = document.createElement('td');
        msg.colSpan = 7;
        msg.className = 'empty';
        msg.textContent = '加载失败，请稍后重试';
        $('#keys-table').innerHTML = '';
        $('#keys-table').appendChild(document.createElement('tr')).appendChild(msg);
        console.error('loadKeys failed:', e);
```
</details>

### [medium/bug] 锚点命中=true

统计口径存在问题：
1) 在 `map` 回调里做 `active++/revoked++` 副作用，把渲染与统计混在一起，易在后续调整（如过滤、排序）时漏改。
2) `destroyed`（已销毁）与 `revoked`（已吊销）被合并计入“已吊销”卡片，二者语义不同（销毁不可逆、吊销可留档），会让运维误判密钥生命周期状态，建议独立展示或改标签为“失效（吊销/销毁）”。
3) 三个统计值只基于当前返回的列表；若 `/api/v1/kms/keys` 支持分页/limit，`list.length` 不等于“密钥总数”，应使用后端返回的 `total/active_count/revoked_count` 等聚合字段，否则必须显式标注“当前页”。

```
if (k.status === 'active') active++;
            if (k.status === 'revoked' || k.status === 'destroyed') revoked++;
```

<details><summary>建议改法</summary>

```
const count = (s) => list.filter(k => k.status === s).length;
        // 注意：destroyed 单独统计，避免与 revoked 混淆；如后端支持聚合字段请优先使用 data.total 等
```
</details>

### [medium/bug] 锚点命中=true

使用默认值充当真实数据：当后端未返回 `max_usage` 时，页面把 10000 当成真实配额展示，运维会误判“用量安全”；同理 `algorithm || 'SM4-GCM'` 会在算法字段缺失时伪装成 SM4-GCM，在密态/合规场景下属于误导性展示。缺失值应显示 `-` 或“未限制”，不要用业务默认值填充。另外 `usage_count` 为 0 时 `|| 0` 正确，但 `max_usage` 为 0（表示不限制）会被替换成 10000，边界值处理错误。

```
<td>${k.usage_count || 0}/${k.max_usage || 10000}</td>
```

<details><summary>建议改法</summary>

```
<td>${k.usage_count ?? 0}/${k.max_usage ?? '-'}</td>
                <td>${escapeHtml(k.algorithm || '-')}</td>
```
</details>

### [medium/bug] 锚点命中=true

缺少并发/重入保护与输入校验：
1) 点击“生成密钥”后按钮未禁用，网络延迟内双击/多次点击会重复 POST，创建多个 DEK（产生孤儿密钥、配额浪费），且 `key-result` 只保留最后一次结果，前面的密钥 ID 直接丢失。
2) `product_id` 仅做了非空校验，未限制长度/字符集就直接提交；后端若未校验则可能写入异常数据（反过来又成为上面 XSS 的注入源）。

建议：`btn.disabled = true` + `finally` 恢复；或提交前 `if(!/^[A-Za-z0-9_.:-]{1,64}$/.test(productId))` 校验；失败时 `console.error` 保留原始错误，向用户展示通用提示。

```
const productId = $('#key-product-id').value.trim();
    if (!productId) { alert('请输入 Product ID'); return; }
    try {
        const result = await API.post('/api/v1/kms/keys', { product_id: productId });
```

<details><summary>建议改法</summary>

```
const productId = $('#key-product-id').value.trim();
    if (!productId) { alert('请输入 Product ID'); return; }
    if (!/^[A-Za-z0-9_.:-]{1,64}$/.test(productId)) { alert('Product ID 格式不合法'); return; }
    const btn = event.currentTarget;
    btn.disabled = true;
    try {
        const result = await API.post('/api/v1/kms/keys', { product_id: productId });
```
</details>

### [low/bug] 锚点命中=true

页面级健壮性不足：
1) `loadKeys()` 在模板内联脚本中直接执行，无 `DOMContentLoaded` 包裹（目前依赖 `#app` 先于脚本插入才安全，属于隐式依赖）；也未处理未登录/401 场景，`localStorage` 中无 `cds_token` 时只会看到“加载失败: 401: ...”堆料。
2) `truncate(k.key_id, 30)` 内部使用 `str.length`，若 `key_id` 是数字/对象（后端 schema 变更）会抛 TypeError 导致整表渲染失败；`formatDate` 对非法时间字符串会渲染出 “Invalid Date”。
3) 本页无任何自动化测试（前端关键渲染/销毁流程），建议至少对 `loadKeys` 的渲染与空态/异常态补充单元测试，并为内联脚本引入 CSP 时评估 `nonce` 方案。

```
loadKeys();
</script>
```

<details><summary>建议改法</summary>

```
document.addEventListener('DOMContentLoaded', () => { loadKeys(); });
</script>
```
</details>

## sdk/pyproject.toml（4）

### [critical/bug] 锚点命中=true

`readme = "README.md"` 被错误地放在 `[project.optional-dependencies]` 表下，TOML 会把它解析成一个名为 `readme` 的可选 extra（其值为字符串 `"README.md"`），而不是项目元数据。后果有两个：1) 打包元数据里完全没有 long_description，PyPI/内部源上的项目页会缺失 README；2) 该 extra 的取值不是合法的 PEP 508 requirement 列表，`pip install cds-sdk[readme]` 或 `pip install -e ".[stream]"` 在依赖解析/元数据校验阶段可能直接报错（setuptools 与 pip 对 optional-dependencies 的 value 类型校验很严格）。同时下游用户会误以为存在名为 readme 的可选功能。应把它移到 `[project]` 段（如 `description` 之后）。

```
stream = ["websocket-client>=1.6"]
readme = "README.md"
```

<details><summary>建议改法</summary>

```
stream = ["websocket-client>=1.6"]
```
</details>

### [high/bug] 锚点命中=true

打包版本与代码内声明的版本不一致：`sdk/cds_sdk/__init__.py` 仍是 `__version__ = "0.2.0"`，而此处为 `0.3.0`。用户通过 `pip show cds-sdk` 看到 0.3.0、通过 `cds_sdk.__version__` 看到 0.2.0，排障与依赖约束（以及问题上报）会出现偏差。建议二者同步，或改为单一来源的动态版本：在 `[project]` 增加 `dynamic = ["version"]`，并配置 `[tool.setuptools.dynamic] version = {attr = "cds_sdk.__version__"}`，避免后续再次漂移。

```
version = "0.3.0"
```

<details><summary>建议改法</summary>

```
version = "0.2.0"  # 与 cds_sdk.__version__ 保持一致，或改用 dynamic version
```
</details>

### [medium/maintainability] 锚点命中=true

核心运行时依赖只有下限、没有上限。`httpx` 在 0.x 阶段仍会引入破坏性变更（且规划中的 1.0 会移除/调整部分已弃用 API，如 `app=`/`proxies` 等参数与部分事件、代理相关接口），一旦用户环境中 pip 解析到不兼容版本，整个 SDK 会在 import 或构造 `httpx.Client` 时抛错，而错误信息与 SDK 本身无关，定位成本很高。建议加上与当前已验证通过的上界，例如 `httpx>=0.27,<1.0`；同理可评估 `stream` extra 的 `websocket-client` 是否需要上界。若项目选择不上界，也建议在 CI 中固定一份 lock 文件（如 pip-tools/uv lock）以保证可复现构建。

```
dependencies = ["httpx>=0.27.0"]
```

### [low/maintainability] 锚点命中=true

两点与打包内容相关的风险：1) 当前 `cds_sdk/` 为扁平布局（`__init__.py`/`client.py`/`stream.py`），`cds_sdk*` 可以覆盖，但一旦后续新增不带 `cds_sdk` 前缀的目录（例如把公共类型抽到同级 `common/`），会被静默漏打包，只有在安装后的环境里才暴露为 ImportError。建议同时显式声明 `packages = ["cds_sdk"]` 或让 CI 用 `python -m build` + 在干净 venv 中 `pip install dist/*.whl` 做一次安装冒烟测试来兜底。2) `cds_sdk/__init__.py` 对外宣称是 typed sync client，但包内没有 `py.typed` 标记文件，也没有 `package-data` 配置，安装后 mypy/pyright 等类型检查器不会采用 SDK 的类型标注，等于放弃了类型声明的价值。若希望类型对外生效，需新增 `sdk/cds_sdk/py.typed` 并配置 `[tool.setuptools.package-data] cds_sdk = ["py.typed"]`（配合 `include-package-data = true`）。

```
include = ["cds_sdk*"]
```

## app/api/catalog.py（5）

### [high/bug] 锚点命中=true

**缺少 `is_latest` 过滤，目录会返回同一产品的过期版本（重复条目）**：`DataProduct.is_latest` 的注释明确“Only latest version is active”，且 `data_products.create_new_version` 只把旧行置为 `is_latest=False`，并不修改其 `status`（旧行仍是 `published`）。新版本走 submit→approve→publish 后，同一产品的 v1、v2 会以 `status=published` 同时存在。本查询只按 `status == published` 过滤，因此买家在目录里会看到同一产品的多个版本重复出现，`total` 与分页页码同步虚高，且排序按 `created_at desc` 会把已废弃的旧版本也混入列表。应追加 `DataProduct.is_latest.is_(True)` 条件（`count_query` 同步追加，否则 total 与 items 不一致）。

```
query = select(DataProduct).where(DataProduct.status == DataProductStatus.PUBLISHED.value)
    count_query = select(func.count()).select_from(DataProduct).where(DataProduct.status == DataProductStatus.PUBLISHED.value)
```

<details><summary>建议改法</summary>

```
query = select(DataProduct).where(
        DataProduct.status == DataProductStatus.PUBLISHED.value,
        DataProduct.is_latest.is_(True),
    )
    count_query = select(func.count()).select_from(DataProduct).where(
        DataProduct.status == DataProductStatus.PUBLISHED.value,
        DataProduct.is_latest.is_(True),
    )
```
</details>

### [medium/security] 锚点命中=true

**LIKE 通配符注入（检索正确性 + 性能放大）**：`q` 直接拼进 `ilike(f"%{q}%")`，未转义 `%`、`_`、`\`。用户传入 `%` 即可匹配全部行（绕过关键字检索语义），传入 `_`/`%` 组合可构造任意模式；`%xxx%` 前导通配符本身无法走 B-tree 索引，恶意或偶然的宽泛模式（如 `%a%b%c%`）会对 `name`/`description`（Text 全文）触发全表扫描，是买家侧热路径上可被放大的慢查询入口。建议转义后再拼接并显式声明 escape 字符。

```
search_filter = or_(
            DataProduct.name.ilike(f"%{q}%"),
            DataProduct.description.ilike(f"%{q}%"),
        )
```

<details><summary>建议改法</summary>

```
escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        search_filter = or_(
            DataProduct.name.ilike(pattern, escape="\\"),
            DataProduct.description.ilike(pattern, escape="\\"),
        )
```
</details>

### [medium/bug] 锚点命中=true

同上：详情接口同样只按 `status=published` 过滤，`create_new_version` 后旧版本行仍是 `published`，因此旧版本 product_id 依然能被买家查到并展示（`version`/`parent_id` 也未返回，调用方无法判断这是非最新版）。这与“只有最新版生效”的模型契约冲突，买家可能基于已下线的旧版本元数据（schema、row_count、output_constraints）发起后续签约/交付。建议同样加上 `is_latest` 条件，或在响应中回传 `version`/`is_latest` 供调用方判别。

```
select(DataProduct).where(
            DataProduct.id == product_id,
            DataProduct.status == DataProductStatus.PUBLISHED.value,
        )
```

<details><summary>建议改法</summary>

```
select(DataProduct).where(
            DataProduct.id == product_id,
            DataProduct.status == DataProductStatus.PUBLISHED.value,
            DataProduct.is_latest.is_(True),
        )
```
</details>

### [low/bug] 锚点命中=true

**排序字段缺少稳定次序，翻页会重复/漏项**：`created_at` 使用 `server_default=func.now()`，在同一事务/同一语句内批量写入的多行时间戳完全相同（例如批量发布或新版本创建）。出现并列时 PostgreSQL 不保证 `ORDER BY created_at DESC` 的次序稳定，配合 `OFFSET` 翻页会导致同一条目在相邻页重复出现或整条被跳过。建议追加唯一列作为次级排序键（如 `DataProduct.id`）。

```
query = query.order_by(DataProduct.created_at.desc()).offset(skip).limit(limit)
```

<details><summary>建议改法</summary>

```
query = query.order_by(DataProduct.created_at.desc(), DataProduct.id.desc()).offset(skip).limit(limit)
```
</details>

### [low/performance] 锚点命中=true

**全量 COUNT + OFFSET 深翻页（买家侧热路径性能）**：本函数每次请求都执行两条查询，其中 `count_query` 会对全部 `published` 行做聚合，无法受 `limit` 约束；而表上仅有 `(provider_id, status)` 与 `(industry, status)` 两个复合索引，单独按 `status`/`security_level`/`product_type` 过滤加 `ORDER BY created_at DESC` 仍需顺序扫描 + 排序，`offset(skip)` 还要丢弃前 skip 行，目录页码越深越慢。此外四处过滤条件在 `query` 与 `count_query` 上手工重复，后续新增过滤项极易只改一处导致 total 与 items 不一致（可参考 data_products.py 用统一 `filters` 列表，或复用 `app.core.pagination.paginate_keyset` 提供游标分页）。

```
total_result = await db.execute(count_query)
    total = total_result.scalar() or 0
```

## app/api/compliance.py（4）

### [high/bug] 锚点命中=true

`datetime.now()` 返回的是无 tzinfo 的服务器本地时间，而 `AuditLog.created_at` 在模型中声明为 `DateTime(timezone=True)`（PostgreSQL `timestamptz`）。把 naive datetime 作为 `timestamptz` 的绑定参数传给 asyncpg 时会按 UTC 解释，于是当容器/主机时区不是 UTC 时，“最近 N 天”窗口会整体偏移若干小时（例如 CST 环境偏移 8 小时），导致周期首/末日的审计事件被漏算或多算，生成的合规报告时间范围与调用方预期不一致。建议统一使用带时区的 UTC 时间。

```
from datetime import datetime, timedelta
    end = datetime.now()
    start = end - timedelta(days=days)
```

<details><summary>建议改法</summary>

```
from datetime import datetime, timedelta, timezone
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
```
</details>

### [medium/maintainability] 锚点命中=true

该端点缺少 `response_model=ComplianceReportResponse`，与 POST /reports 不一致。服务层 `generate_report()` 返回的是裸 dict，FastAPI 在没有 response_model 时不做任何校验/字段裁剪，直接把内部结构序列化给客户端：一旦服务层新增、重命名或漏返回字段（例如 `disclosure_fields()` 的 PZ-06 披露字段、`anchoring`），OpenAPI 文档与实际响应会静默漂移，字段缺失也不会被发现，而这些字段正是合规披露内容，属于对外契约。建议显式声明与 POST 端点相同的响应模型。

```
@router.get("/reports/quick")
async def quick_compliance_report(
```

<details><summary>建议改法</summary>

```
@router.get("/reports/quick", response_model=ComplianceReportResponse)
async def quick_compliance_report(
```
</details>

### [medium/bug] 锚点命中=true

API 层与服务层都没有校验 `start_date <= end_date`（`ComplianceReportRequest` 中也只有字段声明，没有 model_validator）。当调用方把起止日期传反、或传入跨度极小/未来的区间时，所有 `created_at >= start AND created_at <= end` 聚合都会返回 0，进而 `_evaluate_compliance` 把 `audit_trail`/`access_control`/`data_protection` 判为 "warn"，最终产出一份看似合规但实际是入参错误导致的空报告，且响应中不会有任何错误提示。建议在服务端显式拒绝 `end_date < start_date`（返回 422），同时最好约束最大跨度以免无界区间扫描全表。

```
return await compliance_report_service.generate_report(
        db=db,
        start_date=body.start_date,
        end_date=body.end_date,
```

### [medium/bug] 锚点命中=true

`include_sections` 一路透传给 `ComplianceReportService.generate_report()`，但该服务函数体内（以及 `_build_audit_summary` / `_build_access_control` / `_build_data_protection`）从未使用这个参数——即它目前是无效的：无论调用方传哪个子集，报告都会包含全部 section 并原样返回。对外契约上这会让客户端误以为已完成分区裁剪（例如只请求非敏感 section 时仍拿到全量数据）。建议要么在服务层真正实现按 section 过滤输出，要么从请求模型与本调用中移除该字段，避免形成虚假承诺。

```
include_sections=body.include_sections,
```

## app/static/css/admin.css（9）

### [high/bug] 锚点命中=true

侧边栏使用 `position: fixed` + `height: 100vh` 但没有 `overflow-y: auto`，也缺少 `top/left` 显式定位。base.html 目前有 5 个导航项，一旦新增菜单项、或用户缩小窗口/放大页面缩放（Ctrl++，等价于视口高度变小），超出 100vh 的导航项将永远无法滚动访问（fixed 元素不会产生页面滚动条）。同时 `100vh` 在移动端浏览器上会大于可视视口（地址栏收起问题），建议改用 `100dvh`。

```
padding: 20px 0;
    position: fixed;
    height: 100vh;
}
```

<details><summary>建议改法</summary>

```
padding: 20px 0;
    position: fixed;
    top: 0;
    left: 0;
    height: 100vh;
    height: 100dvh;
    overflow-y: auto;
}
```
</details>

### [high/bug] 锚点命中=true

布局完全没有响应式处理：base.html 已声明 `width=device-width` 视口，说明页面会在移动端打开，但这里 `margin-left: 220px` 与 `.sidebar` 的 `width: 220px` 是硬编码耦合的。在 375px 宽的手机上，内容区只剩约 155px，再叠加 30px 内边距与下面的 `input{width:100%}`、7 列表格，必然横向溢出/被固定侧边栏遮挡，后台在窄屏下基本不可用。此外 `flex: 1` 的 flex item 默认 `min-width: auto`，表格内容过宽时 `.content` 无法收缩，会直接撑大整个 body（建议加 `min-width: 0`）。220px 也建议提取为 CSS 变量，避免两处魔数不同步。

```
.content {
    margin-left: 220px;
    flex: 1;
    padding: 30px;
}
```

<details><summary>建议改法</summary>

```
.content {
    margin-left: var(--sidebar-width, 220px);
    flex: 1;
    min-width: 0;
    padding: 30px;
}

@media (max-width: 768px) {
    body { display: block; }
    .sidebar {
        position: static;
        width: 100%;
        height: auto;
        border-right: none;
        border-bottom: 1px solid var(--border);
    }
    .content { margin-left: 0; padding: 16px; }
}
```
</details>

### [high/accessibility] 锚点命中=true

暗色主题存在多处对比度不达标（按 WCAG 2.1 相对亮度计算）：
1) `--text-muted: #8b8fa3` 在 `--surface: #1a1d27` 上仅约 **3.2:1**，而它被用在 12px 的 `th`、`.stat-card .label`、`.empty`、未选中导航链接上（正常小字号要求 4.5:1）。
2) `--primary: #4f7cff` 在 `--surface` 上仅约 **2.8:1**，连大号文本的 3:1 都不满足，却用于 `.logo`（18px）与 `.badge-info` 文本。
3) `.btn-primary` 白字配 `#4f7cff` 约 **3.7:1**，14px 字号同样低于 4.5:1；反而 hover 的 `#3d6af0` 约 4.65:1 是达标的。
建议提升 muted 文本亮度，并让文本/按钮场景使用更深的 primary（如按钮底色直接用现有 `--primary-hover`）。

```
--text-muted: #8b8fa3;
    --primary: #4f7cff;
    --primary-hover: #3d6af0;
```

<details><summary>建议改法</summary>

```
--text-muted: #a6abc0;
    --primary: #4f7cff;
    --primary-text: #7fa0ff;
    --primary-hover: #3d6af0;
```
</details>

### [medium/bug] 锚点命中=true

表格没有任何溢出/换行保护：identities.html 的表格有 7 列，且第 1 列渲染的是 `<code>${truncate(k.key_id,30)}</code>` 这类无空格的长十六进制串，CSS 中 `code` 也没有任何样式与 `word-break`。长 key、证书 base64、时间戳会让表格宽度超过 `.card`，而 `.card`/`.content` 都没有 `overflow-x`，最终导致整页横向滚动，且左侧列被 `position: fixed` 的侧边栏永久遮挡（横向滚动时 fixed 元素不动）。建议给单元格加换行兜底，并为表格提供横向滚动容器（如 `.table-wrap { overflow-x: auto; }` 包裹 `<table>`），同时补上 `tbody tr:hover` 提升宽表格可读性。

```
th, td {
    padding: 10px 14px;
    text-align: left;
    border-bottom: 1px solid var(--border);
    font-size: 14px;
}
```

<details><summary>建议改法</summary>

```
th, td {
    padding: 10px 14px;
    text-align: left;
    border-bottom: 1px solid var(--border);
    font-size: 14px;
    overflow-wrap: anywhere;
}

code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; word-break: break-all; }
```
</details>

### [medium/accessibility] 锚点命中=true

焦点可访问性问题：`outline: none` 移除默认焦点环后，仅把边框颜色改为 `--primary`（约 1px、且 primary 在深色背景上对比度仅 2.8:1），键盘用户几乎无法辨识焦点位置，不满足 WCAG 2.4.7/2.4.11。更严重的是 `.btn` 完全没有 `:focus`/`:focus-visible` 样式——表格里的“销毁”“生成密钥”等按钮被键盘 Tab 到时无任何视觉反馈。建议保留可见焦点环并用 `:focus-visible` 区分鼠标/键盘。

```
input:focus, select:focus {
    outline: none;
    border-color: var(--primary);
}
```

<details><summary>建议改法</summary>

```
input:focus, select:focus {
    outline: none;
    border-color: var(--primary);
    box-shadow: 0 0 0 3px rgba(79, 124, 255, 0.35);
}

input:focus-visible, select:focus-visible, .btn:focus-visible {
    outline: 2px solid var(--primary-text);
    outline-offset: 2px;
}
```
</details>

### [medium/bug] 锚点命中=true

元素级选择器过于宽泛，带来三个实际副作用：
1) `width: 100%` 会命中 **所有** input，一旦页面加入 `checkbox`/`radio`（告警/连接器管理页的开关、多选操作列很常见），复选框会被拉伸成整行宽度的异常控件；
2) `width: 100%` 与模板内联的 `style="display:flex;...;flex:1"` 组合时，`flex-basis:auto` + `width:100%` 会让该输入框占满整行，把同一行的“生成密钥”按钮挤到下一行，破坏 intended 的行内表单；
3) 表单控件默认不继承 `font-family`，这里未设置 `font-family: inherit`，输入框/下拉框字体与页面其余文字不一致；同时未覆盖 `textarea`、`input[type=date]` 等（暗色下日期选择图标可能不可见，需 `color-scheme: dark`）。建议按类型收窄选择器。

```
input, select {
    background: var(--bg);
    border: 1px solid var(--border);
    color: var(--text);
    padding: 8px 12px;
    border-radius: var(--radius);
    font-size: 14px;
    width: 100%;
    margin-bottom: 12px;
}
```

<details><summary>建议改法</summary>

```
input, select, textarea {
    font-family: inherit;
    background: var(--bg);
    border: 1px solid var(--border);
    color: var(--text);
    padding: 8px 12px;
    border-radius: var(--radius);
    font-size: 14px;
    width: 100%;
    margin-bottom: 12px;
}

input[type="checkbox"], input[type="radio"] {
    width: auto;
    margin-bottom: 0;
}
```
</details>

### [low/maintainability] 锚点命中=true

按钮状态不完整：定义了 `transition: all 0.2s` 却没有 `.btn-danger:hover`，导致表格中每行的“销毁”按钮悬停无任何反馈（只有无变化的 transition 开销）；同时缺少 `:disabled` / `.btn:disabled` 样式——前端 `destroyKey()`、`generateKey()` 是异步请求，按钮在请求期间不会禁用，用户可连点重复提交，即便后续补上 `disabled` 属性这里也没有视觉提示。另外按钮未设置 `font-family: inherit`，会使用浏览器默认字体。

```
.btn-primary { background: var(--primary); color: white; }
.btn-primary:hover { background: var(--primary-hover); }
.btn-danger { background: var(--danger); color: white; }
.btn-sm { padding: 4px 10px; font-size: 12px; }
```

<details><summary>建议改法</summary>

```
.btn-primary { background: var(--primary-hover); color: #fff; }
.btn-primary:hover { background: #2f59d8; }
.btn-danger { background: var(--danger); color: #fff; }
.btn-danger:hover { background: #dc2626; }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.btn-sm { padding: 4px 10px; font-size: 12px; }
```
</details>

### [low/style] 锚点命中=true

hover/active 时才加上 `border-left: 3px`，会让导航文字在悬停瞬间向右偏移 3px（布局跳动）。建议默认态就占好这 3px（透明边框），仅在状态态改颜色。另外导航项当前没有焦点态样式，配合上面第 5 点一并补上 `:focus-visible`。

```
.sidebar a:hover, .sidebar a.active {
    color: var(--text);
    background: rgba(79, 124, 255, 0.1);
    border-left: 3px solid var(--primary);
}
```

<details><summary>建议改法</summary>

```
.sidebar a {
    border-left: 3px solid transparent;
}

.sidebar a:hover, .sidebar a.active {
    color: var(--text);
    background: rgba(79, 124, 255, 0.1);
    border-left-color: var(--primary);
}
```
</details>

### [low/maintainability] 锚点命中=true

侧边栏宽度 220px 在 `.sidebar` 与 `.content` 两处硬编码，`#4f7cff` 的半透明变体（`rgba(79,124,255,.1)` / `.15`）也与 `--primary` 脱耦；`transition: all 0.2s` 未限定属性，会连带动画 width/padding 等非预期属性。建议把主色拆出 `--primary-rgb: 79 124 255;` 供 `rgba(var(--primary-rgb), .15)` 复用，并把 `transition` 收窄为 `transition: color .2s, background-color .2s, border-color .2s;`，避免主题色调整时出现漏改。

```
width: 220px;
    background: var(--surface);
```

<details><summary>建议改法</summary>

```
width: var(--sidebar-width, 220px);
    background: var(--surface);
```
</details>

## sdk/cds_sdk/client.py（8）

### [high/bug] 锚点命中=true

`_parse_error` 只在 `resp.json()` 抛出 `ValueError`（JSONDecodeError）时才走兜底分支，但 `payload.get(...)` 对非 dict 的合法 JSON 会抛 `AttributeError`，而这个异常没有被捕获：当服务端返回 JSON 数组（`[...]`）、字符串（`"error"`）或 `null` 时，`payload.get` 直接抛 `AttributeError`，真正的 API 错误（状态码/detail）被吞掉，调用方看到的是一个与业务无关的崩溃。建议先解析 JSON，再用 `isinstance(payload, dict)` 判断取 detail。

```
try:
        payload = resp.json()
        detail = payload.get("detail", payload)
    except ValueError:
        payload = None
        detail = resp.text
```

<details><summary>建议改法</summary>

```
try:
        payload = resp.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        detail = payload.get("detail", payload)
    elif payload is None:
        detail = resp.text
    else:
        detail = payload
```
</details>

### [high/security] 锚点命中=true

用户可控的 `filename` 未做 URL 编码就直接拼进请求路径。httpx 的 `_merge_url` 对相对路径只做拼接、不做 dot-segment 规范化，因此 `filename="../../admin/users"`、`"..%2F..%2Fx"`，或含 `?`/`#` 的文件名，都会改写实际请求的路径/查询串，可能越权访问同一 API 下的其他端点（路径穿越 / 请求走私式改写）。`delete_file()`（以及 `download_dataset_bundle` 中的 `bundle_id`）存在同样问题。建议用 `urllib.parse.quote(value, safe="")` 对所有插入路径的标识符统一编码后再拼接。

```
def download_file(self, session_id: str, filename: str) -> bytes:
        resp = self._client.get(f"/api/v1/sandbox-sessions/{session_id}/files/{filename}")
```

<details><summary>建议改法</summary>

```
def download_file(self, session_id: str, filename: str) -> bytes:
        safe_name = quote(filename, safe="")
        resp = self._client.get(f"/api/v1/sandbox-sessions/{session_id}/files/{safe_name}")
```
</details>

### [medium/bug] 锚点命中=true

把 `resp.headers` 转成普通 `dict` 会丢掉 httpx `Headers` 的大小写无关查找能力，而消费端是按固定大小写取值的：`list_sessions()` 里 `headers.get("x-next-cursor")`、`CDSApiError` 里 `headers.get("Retry-After") or headers.get("retry-after")`。HTTP/1.1 服务端（Starlette/uvicorn）通常原样回显 `X-Next-Cursor`，此时普通 dict 查不到 → `next_cursor` 恒为 `None`，`iter_sessions()` 会静默只返回第一页（分页数据丢失且无任何报错）；`Retry-After` 同理可能被忽略。建议直接返回 `resp.headers`（本身大小写无关），或在返回前用 `httpx.Headers(...)` 归一化。

```
return body, dict(resp.headers)
```

<details><summary>建议改法</summary>

```
return body, resp.headers  # httpx.Headers 大小写无关，勿转成普通 dict
```
</details>

### [medium/bug] 锚点命中=true

keyset 分支直接对 `body` 下标赋值，但 `_request_with_headers` 在 204 或空响应体时返回 `body=None`，且当响应是 JSON 数组时 `body` 是 list，这里会抛 `TypeError`/`IndexError`，调用方拿到的是与接口异常无关的堆栈。另外此分支的“模式判定”条件过弱：`limit` 只在 `page==1 and page_size==20`（默认值）时才生效，若调用方写 `list_sessions(page=3, limit=50)`，会静默走 offset 分支并把 `limit=50` 丢弃（实际仍按 `page_size=20` 翻页），属于难以排查的隐式行为。建议显式区分两种模式（例如用 `cursor is not None or limit is not None` 判定，并对与 `page/page_size` 混用给出明确报错），同时对 `body` 非 dict 做保护。

```
body["next_cursor"] = headers.get("x-next-cursor")
            return body
```

<details><summary>建议改法</summary>

```
if not isinstance(body, dict):
                body = {"items": body if isinstance(body, list) else []}
            body["next_cursor"] = headers.get("x-next-cursor")
            return body
```
</details>

### [medium/security] 锚点命中=true

该 POST 把 `code` / `rag_query` 等正文放进 query string（`params=params`）而不是 `json=`。两个实际风险：1) 沙箱脚本体积稍大即超出服务器/网关的 URI 长度上限（通常 8KB），返回 414/431 且难以定位；2) URL 查询串会被接入层 access log、代理日志、监控链路原样记录，等于把用户代码/查询语句明文落到日志里（可能含敏感逻辑或数据）。若后端确为 query 参数，建议在客户端限制大小并提示改用请求体；否则改为 `json=...`。

```
params: dict[str, Any] = {"session_id": session_id, "task_type": task_type}
        if code is not None:
            params["code"] = code
```

### [medium/bug] 锚点命中=true

重试逻辑对所有方法一视同仁，包括 `POST /sandbox-sessions`、`POST .../snapshots`、`POST .../terminate` 等非幂等写操作。503 常由网关在“后端已处理但未成功回包”时产生，自动重放会产生重复会话/重复快照/重复计费，而客户端又没有 `Idempotency-Key` 之类的幂等保护。建议只对幂等方法（GET/HEAD/DELETE/PUT）自动重试，写操作要么显式附带幂等键，要么把重试交给调用方决定。

```
if resp.status_code in (429, 503) and retries > 0:
            time.sleep(min(_retry_after_seconds(resp), 5.0))
            return self._request(method, path, _retries=retries - 1, **kwargs)
```

### [low/bug] 锚点命中=true

RFC 9110 规定 `Retry-After` 既可以是秒数也可以是 HTTP-date（如 `Wed, 21 Oct 2026 07:28:00 GMT`）。这里 `float(ra)` 解析日期失败后静默回落到 1.0，客户端会在服务端明确要求长时间等待时仍以 1s 间隔猛重试（放大 429/503 压力）。建议用 `email.utils.parsedate_to_datetime` 兜底解析日期并换算为剩余秒数。另外 `CDSApiError.__init__` 里又实现了一份几乎相同的 Retry-After 解析逻辑，两处行为还不一致（一个 `int()`、一个 `float()`），建议抽成单一函数复用。

```
def _retry_after_seconds(resp: httpx.Response) -> float:
    ra = resp.headers.get("Retry-After") or resp.headers.get("retry-after")
    if ra is not None:
        try:
            return max(0.0, float(ra))
        except ValueError:
            pass
    return 1.0
```

### [low/performance] 锚点命中=true

上传路径把整个文件一次性 `read()` 进内存再交给 httpx（`bytes(data)` 对 bytes/bytearray 还会再复制一份），大文件场景内存占用翻倍，且失去 httpx 分块流式上传能力。httpx 的 `files=` 直接支持文件对象/生成器，建议对文件路径与 BinaryIO 直接把句柄透传给 `files={"file": (filename, fh)}`，仅在需要 `Content-Length` 时提供 `content_type`/size。

```
if isinstance(data, str):
            with open(data, "rb") as fh:
                payload = fh.read()
        elif isinstance(data, (bytes, bytearray)):
            payload = bytes(data)
        else:
            payload = data.read()
```

## sdk/cds_sdk/stream.py（7）

### [high/bug] 锚点命中=true

**ticket / session_id 未做 URL 编码，凭证可能被截断且存在参数注入面**

`ticket` 直接拼接进 query。一次性 ws-ticket 通常是 base64/URL-不安全字符集（含 `+`、`/`、`=`），拼接后服务端按 `application/x-www-form-urlencoded` 解析 query 时，`+` 会被解成空格、`&`/`#` 会截断 ticket，导致握手 401/票据校验失败（表现为随机性连接失败，极难排查）；同理 `session_id` 若含 `/`、`?`、`#`、`&` 会篡改请求路径或注入额外 query 参数。建议对两个值都做显式转义。

```
path = (
        f"/api/v1/sandbox-sessions/{session_id}/exec/stream"
        f"?ticket={ticket}"
    )
```

<details><summary>建议改法</summary>

```
path = (
        f"/api/v1/sandbox-sessions/{quote(session_id, safe='')}/exec/stream"
        f"?ticket={quote(str(ticket), safe='')}"
    )
```
</details>

### [high/bug] 锚点命中=true

**recv 超时被当作“正常结束”，调用方拿到被截断的输出却无从分辨**

`_is_closed_error` 把 `WebSocketTimeoutException` / `TimeoutError` 与连接关闭同等对待并直接 `return`。但 recv 超时只代表 `recv_timeout`（默认 30s）内没有新帧——沙箱内长时间静默运行的命令（编译、拉数据、跑模型）完全可能超过这个间隔，此时迭代器静默结束且没有 `exit` 帧，调用方会误判为“命令执行完毕”，把不完整输出当成完整结果，这与本模块 fail-closed / 不做假流式的整体语义相冲突。此外文档声明客户端帧包含 `ping`，但代码从未发送任何心跳，长静默期极易被服务端 idle 超时断开，进一步扩大该误判窗口。建议：超时单独产出终止帧（或抛 `CDSError`），并实现 ping 心跳或明确文档化“超时即失败”。

```
except Exception as exc:  # server closed / timeout
                if _is_closed_error(exc):
                    return
```

<details><summary>建议改法</summary>

```
except Exception as exc:  # server closed / timeout
                if _is_timeout_error(exc):
                    yield {"type": "error", "code": "STREAM_RECV_TIMEOUT",
                           "message": f"no frame within {recv_timeout}s; output may be incomplete"}
                    return
                if _is_closed_error(exc):
                    return
```
</details>

### [medium/bug] 锚点命中=true

**握手阶段没有任何超时，`recv_timeout` 对 connect 完全无效**

`ws.settimeout(timeout)` 在 `connect` 之后才生效，握手（TCP 连接 + HTTP Upgrade 请求）期间不受任何约束。当网关/服务端 hang 住（例如 WS 路由未就绪、代理吞掉 Upgrade 请求）时，该调用会长时间甚至无限阻塞，`recv_timeout` 形同虚设，调用方（很可能是 Web 请求线程）被拖死。`websocket-client` 的 `WebSocket.connect` 本身支持 `timeout` 参数，应传入；同时 connect 失败时显式 `close()` 以释放半开 socket。

```
def _default_ws_open(url: str, timeout: float):
    ws = _import_websocket().WebSocket()
    ws.connect(url)
    ws.settimeout(timeout)
    return ws
```

<details><summary>建议改法</summary>

```
def _default_ws_open(url: str, timeout: float):
    ws = _import_websocket().WebSocket()
    try:
        ws.connect(url, timeout=timeout)
    except Exception:
        try:
            ws.close()
        except Exception:
            pass
        raise
    ws.settimeout(timeout)
    return ws
```
</details>

### [medium/bug] 锚点命中=true

**`ws.send` 未被包装，原始 websocket 异常破坏错误契约**

connect 与 recv 都被转成 `CDSError`，唯独发送 `start` 帧这一步裸奔：连接刚建立即被服务端 RST、或写超时，都会抛出 `WebSocketConnectionClosedException` / `WebSocketTimeoutException` 之类的 websocket-client 异常并直接冒泡。而模块文档与 SDK 的错误模型承诺“可选依赖缺失/流失败一律以 `CDSError` 呈现”，只捕获 `CDSError` 的调用方会在这里崩溃。

```
ws.send(json.dumps(frame))
```

<details><summary>建议改法</summary>

```
try:
            ws.send(json.dumps(frame))
        except CDSError:
            raise
        except Exception as exc:
            raise CDSError(502, f"websocket send failed: {exc}") from exc
```
</details>

### [medium/security] 锚点命中=true

**连接失败消息可能携带含 ticket 的完整 URL，造成凭证泄漏**

`websocket-client` 的握手异常（`WebSocketBadStatusException`、`WebSocketAddressException` 等）message 中常包含请求 URL 与响应头，而本模块的 URL 携带了 ws-ticket。将其原样嵌入 `CDSError.detail` 后，调用方通常会记录该异常（日志/错误上报/APM trace），一次性凭证便落入日志——虽票据 30s 过期，但会扩大重放窗口并违反“凭证不落日志”的基线。建议在消息中脱敏（仅保留 host/path 或直接把 `ticket=` 替换掉），并确保不把完整 URL 写入 detail/url 字段。

```
raise CDSError(502, f"websocket connect failed: {exc}") from exc
```

<details><summary>建议改法</summary>

```
raise CDSError(
            502,
            f"websocket connect failed: {redact_ticket(str(exc))}",
        ) from exc
```
</details>

### [low/bug] 锚点命中=true

**空帧即终止迭代的判断过于宽泛**

`if not raw: return` 把“空消息”与“连接关闭”混为一谈。空文本帧在 WebSocket 协议里是合法帧（常被用作应用层 keepalive/心跳），一旦服务端发出这类帧，流会被提前判定结束，剩余 stdout/stderr 全部丢失且不会看到 `exit` 帧。websocket-client 在连接真正关闭时会抛 `WebSocketConnectionClosedException`，因此这里更应只把 `None`（测试钩子表达 EOF 的约定）视为结束，空串交给上层 JSON 解析分支处理。

```
if not raw:
                return
```

<details><summary>建议改法</summary>

```
if raw is None:  # test-hook convention for EOF
                return
```
</details>

### [low/maintainability] 锚点命中=true

**依赖 client 私有属性拼 WS 地址，且能力校验在本函数内缺位**

两点问题：1) `client._client.base_url` 直接读取 `httpx.Client` 私有属性，`CDSClient` 并未暴露 base_url 公共访问器，任何内部重构（字段改名、改用 async client、base_url 归一化差异）都会在运行时才以 `AttributeError` 暴露；2) `SUPPORTED_STREAM_LEVELS` 与文档中“fails fast before any socket is opened”的承诺在本函数里并未落实——目前只靠 `CDSClient.stream_exec` 上层校验，而本函数是模块级公开入口，直接调用（含测试与非 `CDSClient` 的鸭子类型调用方）会绕过 fail-fast，先发出 ws-ticket 再收到服务端的 `STREAM_UNSUPPORTED`，既多消耗了一次性票据也违背文档语义。建议给 `CDSClient` 增加公开 `base_url` 属性，并在此处对能力做一次显式校验。

```
url = _to_ws_url(str(client._client.base_url), path)
```

<details><summary>建议改法</summary>

```
url = _to_ws_url(str(client.base_url), path)
```
</details>

## app/api/admin.py（3）

### [medium/security] 锚点命中=true

`Environment` 未开启 `autoescape`（Jinja2 默认为 `False`），而这里渲染的正是 `*.html` 模板并以 `text/html` 返回。当前模板恰好只输出静态骨架（数据由前端 fetch 后 DOM 注入），所以还没有实际 XSS；但一旦后续有人往 `render()` 里传上下文（如把身份/证书/告警数据改成服务端渲染），所有 `{{ value }}` 都不会转义，直接形成存储型/反射型 XSS，且这种缺陷在代码评审中很难被发现。建议显式打开 autoescape，成本为零。

```
_env = Environment(loader=FileSystemLoader(str(_template_dir)))
```

<details><summary>建议改法</summary>

```
_env = Environment(
    loader=FileSystemLoader(str(_template_dir)),
    autoescape=select_autoescape(("html", "htm", "xml")),
)
```
</details>

### [medium/security] 锚点命中=true

这些 `/admin/*` 路由在文件内和注册处都没有任何认证/授权依赖（`app/main.py` 中是裸 `app.include_router(admin.router)`），唯一的保护是 `settings.ADMIN_PAGES_ENABLED` 开关。SS-12 的 default-deny 中间件只拦 API key subject，匿名浏览器请求可直接命中；一旦该开关被误开（或 `validate_security_config()` 的校验被绕过/环境判定失误），管理台页面（身份/密钥、证书、连接器等）就会完全裸露。建议在 router 层加一道纵深防御，而不是仅依赖全局开关。

```
router = APIRouter(prefix="/admin", tags=["admin"])
```

<details><summary>建议改法</summary>

```
router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(require_human)],
)
```
</details>

### [low/performance] 锚点命中=true

模板首次加载时 `get_template()` 会做同步文件读取 + Jinja 源码编译（Jinja2 解析/编译单个页面可达数十毫秒），而调用点是 `async def`，会直接阻塞事件循环，拖慢同一 worker 上所有并发请求。默认 `cache_size=400` 只保证首次之后命中缓存，进程重启、多 worker、模板被缓存淘汰后都会再次触发。建议把处理函数改为同步 `def`，交给 FastAPI 放到 threadpool 执行。

```
def _render(template_name: str) -> HTMLResponse:
    html = _env.get_template(template_name).render()
    return HTMLResponse(content=html)
```

<details><summary>建议改法</summary>

```
from starlette.concurrency import run_in_threadpool


async def _render(template_name: str) -> HTMLResponse:
    html = await run_in_threadpool(lambda: _env.get_template(template_name).render())
    return HTMLResponse(content=html)
```
</details>

## app/api/cube_egress.py（4）

### [medium/bug] 锚点命中=true

`record: dict` 对来自节点上报的任意 JSON 对象不做任何校验：既没有限制键数量/嵌套深度/体积，也没有限制值类型。下游 `map_egress_record()` 会直接对它做类型假设（如 `",".join(creds.get("secret_ids") or [])` 要求元素必须是 str；`(policy.get("duration_us") or 0) // 1000` 要求是 int；`http.get("req_bytes")` 同理）。一旦某个上报行里这些字段是字符串/嵌套对象，就会在映射阶段抛 `TypeError`，而本路由没有做任何异常处理，整个批次（含同批中其它合法行）都会以 500 失败，tailer 会不断重试同一批次造成持续失败。建议用显式的 Pydantic 子模型描述 record（数值字段声明为 `int`、`secret_ids: list[str]` 等），至少对 dict 做键数上限约束，让非法行在进入业务层之前就被 422 拒绝。

```
record: dict
```

<details><summary>建议改法</summary>

```
# 期望 schema：{ts, request_id, event, sandbox{}, conn{}, tls{}, http{},
    # policy{}, credentials{}, security{}, redacted_*}。建议改为显式子模型，
    # 至少约束顶层键数量，避免超大/异常类型的 record 透传到业务层。
    record: dict = Field(max_length=64)
```
</details>

### [medium/performance] 锚点命中=true

`entries` 只约束了 `min_length=1`，没有上限。该接口是节点 tailer 批量摄取入口，单批次条目数完全由调用方决定，而 `ingest_egress_batch()` 会对每个 entry 串行执行一次 SELECT 去重查询 + 一次 `audit_service.log()` INSERT（`db.add` 后统一 commit），因此一个超大批次会长时间占用一个数据库连接/事务并在线性循环中累积大量 ORM 对象，同时每条 entry 又携带任意大小的 `record`（含 `redacted_request_body` / `redacted_response_body`），批量放大后很容易造成内存、连接池与审计表写入压力。请加上与 tailer 契约一致的 `max_length`（并在部署层配合请求体大小限制）。

```
entries: list[EgressEntry] = Field(min_length=1)
```

<details><summary>建议改法</summary>

```
entries: list[EgressEntry] = Field(min_length=1, max_length=1000)
```
</details>

### [medium/bug] 锚点命中=true

幂等键由 `(node_id, file_name, offset, line_no)` 组成（见 `ingest_egress_batch` 的 SELECT 去重与 `CubeEgressDedup` 插入）。请求契约里没有任何“文件代次”标识（generation/inode/rotate epoch），因此当日志文件被原地截断或 `copytruncate` 方式轮转、`file_name` 与 offset 归零重新开始时，新产生的审计行会命中同一组幂等键而被计入 `duplicates` 静默丢弃，且响应仍返回 200。对于需要 Merkle/链上锚定做防篡改证明的审计链路，这类静默丢失属于数据完整性问题。建议在请求模型中要求携带文件代次标识（如 inode + rotation 序号，或直接上报文件指纹）并纳入幂等键。

```
class EgressEntry(BaseModel):
    offset: int = Field(ge=0)
    line_no: int = Field(ge=1)
```

### [medium/bug] 锚点命中=true

路由直接 `await ingest_egress_batch(...)` 后原样返回，没有任何异常映射/回滚处理。业务层内部是“先 SELECT 查去重键，不存在则 INSERT `CubeEgressDedup`”的 check-then-act，没有 `ON CONFLICT DO NOTHING`，因此同一节点重放（例如 tailer 超时重试与在途请求并发）会触发唯一约束 `IntegrityError`；该异常加上 `_anchor_batch` 之外的任何数据库异常都会冒泡成裸 500，把驱动/SQL 细节暴露给调用方，并让整批数据（包括本已可写入的行）随事务回滚后被无限重试。建议在此处（或业务层）捕获 `IntegrityError` 等数据库异常：显式 `await db.rollback()`，将重复键计为 duplicate 或以受控的 `CDSError` 返回明确错误码。

```
result = await ingest_egress_batch(
        db,
        batch.node_id,
        batch.file_name,
        [e.model_dump() for e in batch.entries],
    )
```

## sdk/cds_sdk/__init__.py（1）

### [medium/bug] 锚点命中=true

版本号与打包元数据不一致（版本漂移）：`sdk/pyproject.toml` 中 `[project] version = "0.3.0"`，而此处 `__version__` 仍为 `"0.2.0"`。运行时读到的 `cds_sdk.__version__` 会与实际发布的发行版版本不符，用于诊断上报、User-Agent 或测试断言时都会给出错误版本，且每次发版都需手工同步、极易再次漂移。建议以打包元数据为单一来源，并保留源码直跑时的回退值。

```
__version__ = "0.2.0"
```

<details><summary>建议改法</summary>

```
try:
    from importlib.metadata import PackageNotFoundError, version as _pkg_version

    try:
        __version__ = _pkg_version("cds-sdk")
    except PackageNotFoundError:  # 源码直跑/未安装
        __version__ = "0.3.0"
except ImportError:
    __version__ = "0.3.0"
```
</details>

