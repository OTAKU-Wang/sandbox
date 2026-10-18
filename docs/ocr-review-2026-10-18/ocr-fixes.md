# 修复台账（只收本地可验证项）

判据：每个改动块必须有本机「改前红 / 改后绿」两次读数；做不出证据的块回退并登记到「待部署验收」。
**本机前端门跑法（必带旗标）**：`NODE_OPTIONS=--experimental-require-module npx vitest run ...` —— 本机 node v22.5.1 + jsdom 29 会 `ERR_REQUIRE_ESM`，不带旗标时**全部**前端用例收不到 worker（打印 `Test Files: no tests`、`RC=1`）。我已亲自对照验证：同一文件不带旗标 `RC=1 / no tests`，带旗标 `RC=0 / 1 passed`。
基线：HEAD 452ffc6 干净工作树 /d/workspace/ocr-base。**后端基线 = 83 passed / RC=0（68.23s，6 个目标文件：mpc_service / mpc_capabilities / audit_api / audit_service / merkle_service / merkle_pipeline，本机 .venv-gate py3.12 口径）**；前端 tsc 基线 = RC=0；前端 vitest 基线在干净树因 node_modules junction 导致 forks worker 起不来，正在用 --pool=threads 重取。

| 编号 | 严重度 | 文件 | 缺陷 | 组 | 本机证据类型 | 改前红 | 改后绿 | 状态 |
|---|---|---|---|---|---|---|---|---|
| A268 | critical | app/api/mpc.py | 分片读侧无权限门 | B1 | 本机 ASGI in-process pytest | 逐 hunk 回退后 `RC=1`：`2 failed, 16 deselected` / `1 failed, 17 deselected` | **`RC=0` 27 passed**（3 个 MPC 文件，基线 83 passed 的子集） | 已修 |
| A294b | high | app/core/security.py | CORS 缺 expose_headers | B2 | tests/test_cors_expose_headers.py（读 app.user_middleware kwargs） | 新用例对旧代码必红（旧配置无 expose_headers 键） | **我亲自复跑 `RC=0` 2 passed**；`compileall -q app` RC=0 | 已修 |
| A279 | high | app/api/audit.py, audit_service | Merkle 叶子原文字段歧义 | B3 | 逐字节等值 + 写入侧守卫 | 待回填 | 待回填 | 进行中 |
| A283/A285 | critical/high | docker-compose.prod.yml | 裸 `${VAR}` / 运行时写路径未入卷 | D1 | `docker compose config` | 剥掉守卫的副本 + 空 env ⇒ `RC=0` 且渲染出 `POSTGRES_PASSWORD: ""` 等空口令 | **我亲自复跑**：空 env ⇒ `RC=1` + 17 行 `required variable`；本机真实 `.env.prod` ⇒ `RC=1` **缺 4 项**（列 DEK / MFA key / KMS token / TEE 白名单，改前是静默空值）；完整 env ⇒ `RC=0` 且三个卷渲染到位 | 已修 |
| A288 | medium | cds-frontend/.dockerignore | COPY . . 带敏感文件 | D1 | 只能靠 `docker build` 证明 | — | — | **按约束回退**（已按字节还原，`git status` 干净），内容留在下方「待部署验收」 |
| A243/A298 | critical/medium | components/Layout/DataplaneBanner.tsx | 双重解包 + 探针失败静默 | F1 | vitest 渲染（桩返回拦截器拆包后的载荷） | `RC=1`：`Tests 5 failed / 4 passed`，横幅用例报 `Unable to find [data-testid="dataplane-banner"]`、沉默用例报 `expected "warn" to be called 1 times, but got 0` | **`RC=0` Test Files 3 passed / Tests 9 passed**，`tsc --noEmit` RC=0 | 已修 |
| A245 | high | components/Feedback/SimulationBadge.tsx | not_ 判定顺序反了 | F1 | vitest | `RC=1`：`expected 'simulated' to be 'unsupported'` | 同上 9 passed | 已修 |
| A299 | low | components/Feedback/QueryFeedback.tsx | Spin tip 不渲染 | F1 | vitest（antd 自己在 stderr 打出 `tip only work in nest or fullscreen`） | `RC=1`：`Unable to find an element with the text: 加载能力矩阵` | 同上 9 passed；`.ant-spin-spinning` 类名保留以兼容 13 个消费页的既有选择器 | 已修 |
| A272/A290/A291 | high | components/Audit/AuditVerifyModal.tsx | 错误态并存 / 空证明当单叶 / 披露回退 | F2 | vitest | 红 `.gate-f2-red.log`：**15 failed / 6 passed (21)** | 绿 `.gate-f2.log`：**21 passed**；存量回归 `.gate-f2-existing.log`：**26 passed** | 已修（绿读数我复点日志）|
| A275/A276/A277 | medium | pages/Audit/AuditLog.tsx | 死窗口 + 内部路径外泄 + Blob 泄漏 | F2 | vitest | 同上 F2 红读数 | 同上 F2 绿读数（同一批 21 例含页内三处）| 已修（绿读数我复点日志）|
| A280 | medium | services/auditApi.ts | 2xx blob 当 CSV | F2 | vitest | 同上 F2 红读数 | 同上 F2 绿读数；组件层合并门 13 files/91 passed 覆盖 | 已修（绿读数我复点日志）|
| A274/A257 | medium/high | services/identityApi.ts + 新增 utils/unwrapList.ts | 三套互不相容的列表解包 + 「取不到就当有效」 | F3 | vitest（红态即简报那批行为断言） | `.gate-f3-red.log`：**`Tests 17 failed / 33 passed`** | `.gate-f3.log`：**`3 files / 50 passed / RC=0`**；邻居回归 **107/107**；ESLint RC=0；仅这 6 个文件的 tsc RC=0 | 已修 |
| A281 | medium | services/federationApi.ts | 后端 `limit=Query(100,…)` 返回裸列表且无 total/has_more ⇒ UI 静默截断 | F3 | vitest | 红：`显式带上 limit=500`、`条目数等于 limit ⇒ possiblyTruncated=true`、`后端缺 remote_space 时不再伪造空字符串联合键` | 同上前一行的 50 passed | 已修 |
| A278 | medium | pages/Auth/Login.tsx | JSON.stringify(e) 外泄 + 空密码文案 | F4 | vitest | 红 `.gate-f4-red.log`：**10 failed / 2 passed (12)**（Login 单跑 `.gate-f4-red-login.log`：9 failed / 1 passed） | 绿 `.gate-f4.log`：**12 passed**，复跑 `.gate-f4-rerun.log` 仍 **12 passed**（非偶发） | 已修（绿读数我复点日志）|
| A261 | medium | pages/ComplianceDisclosure.tsx | 后端可空字段前端不判 | F4 | vitest | 同 F4 红读数（12 例同批）| 同 F4 绿读数 | 已修（绿读数我复点日志）|
| A244 | high | App.tsx + 新增 components/Feedback/ErrorBoundary.tsx | 全站没有渲染错误兜底 ⇒ 任何 null 解引用＝白屏 | F5 | vitest（可开关 fragile 子组件复现 null 解引用 TypeError） | 红：把 ErrorBoundary.tsx 移出后 RC=1（Failed to resolve import） | **RC=0 Test Files 1 passed / Tests 4 passed**；tsc --noEmit RC=0。**我另跑组件层合并门：src/components/{Audit,Feedback,Layout} ⇒ 13 files / 91 passed / RC=0** | 已修 |
| A252 | medium | components/Layout/routeCrumb.ts + MainLayout.tsx | `prefix:''` 兜底把 13 个真实页面都渲染成「运营概览」；全站无 `document.title` | F6 | vitest 数据级差分（用 `?raw` 读真实 App.tsx，改路由不必改用例） | 红 `.gate-f6-red2.log`：**`5 failed / 4 passed`**，并把修复前的 routeCrumb 原样塞回跑出 **15 条兜底清单**（跑完按 sha256 还原一致） | 绿 `.gate-f6.log`：**3 files / 34 passed**；回归 `.gate-f6-regression.log`：**34 files / 391 passed**；`.gate-f6-tsc.log` 空 ⇒ 兜底改 `UNKNOWN_CRUMB`（无 prefix 字段＝永不匹配）、命中改段边界（`/data-products` 不再吞 `-archive` 前缀）、首页走精确相等；**45/45 命中、0 兜底、0 死条目** | 已修（绿读数我复点日志）|
| A260 | medium | utils/csvPreview.ts | 引号内换行被当行分隔符（RFC4180 §2.6）+ 重名表头互相覆盖 | F6 | vitest（原 8 条原样保留） | 红 `.gate-f6-red.log` csvPreview 6 红：引号内换行 `totalRows 3≠2`、引号内空行被吃、重名列覆盖、引号内 CRLF、未闭合引号、`splitCsvRecords` 未导出 | 同上 34 passed 与 391 passed 回归 | 已修（绿读数我复点日志）|

## 回退 / 待部署验收

- **A288 `.dockerignore`**：应改为原 4 行 + `.env` / `.env.*` / `*.local` / `.npmrc` / `coverage` / `.vitest` / `*.log`（构建只需 `package*.json`、`src`、`public`、`index.html`、`tsconfig*.json`、`vite.config.ts`，`nginx.conf` 在运行阶段单独 COPY，故不影响构建；`git ls-files cds-frontend` 下没有已跟踪的 `.env*`/`.npmrc`）。验收动作 = 部署机上一次 `docker build`（正是每轮不该重复的那次）。另：`Dockerfile.api:30-33` 用的是定向 COPY，根目录缺 `.dockerignore` 不构成泄密面。

- **A294b 的浏览器侧效果**（跨源时响应头是否真的可见）本机无法验证 —— 本机证明的只是「中间件配置里带了这两个头」，浏览器行为需部署后看。
- **A268 的 UI 后果**：非 ops 角色打开 MPC 页会看到 403 而不是数据，需要部署后在浏览器验收（B1 未动任何前端文件）。

## 子代理带出的新发现（本轮未修，仅登记为待办）

1. **`deploy/e2e-remote/remote_deploy.sh` 是 A283 的完整版**：L11-13 的 fail-fast 白名单**漏了** `CDS_COLUMN_ENCRYPTION_KEY`、`CDS_MFA_SECRET_ENCRYPTION_KEY`（以 `${VAR:-}` 传，见 `:124`、`:148`），而 `:122` 传 `-e CDS_DEBUG="${CDS_DEBUG:-true}"` ⇒ app 侧 `validate_security_config` 的生产校验门被绕过，**API 真会带着空列 DEK / 空 MFA key 启动**（compose 那份救不了这条路径）。
2. **`k8s/cds-app.yaml` 一个卷都没有**（`volumeMounts|volumes:` 命中 0），且 `replicas: 2` ⇒ 连 `/tmp/cds-sandbox` 工作区都在容器可写层、两副本各写各的 —— 五份部署副本里 A285 类缺陷最严重的一份。
3. A283 同类：`docker-compose.middleware.yml:16,51,53,67,68,127` 用 `${VAR:-CdsSecureP@ss2026}` **把口令写进仓库**当兜底；`k8s/secrets.yaml:15-27` 明文入仓（自带 DEV-ONLY 横幅）；A285 同类：`helm/cds/templates/pvc.yaml` 只有 sandbox 一个 PVC、`docker-compose.yml:164/:186`、`deploy/local/local_deploy.sh:163-165`。
4. prod compose 剩余暴露（按设计暂不改，需硬件/验收决策）：10 个端口裸发布 0.0.0.0（`"5432:5432"` 等，无 `127.0.0.1:` 前缀）、Vault 仍是 dev 模式（`VAULT_DEV_ROOT_TOKEN_ID` + `0.0.0.0:8200`）。

## 我自己接手的两项（子代理容量满 / 无主）

- **A289（`tests/e2e_full_test.py` 会被裸 `pytest tests/` 采集）已修**：改前 `--collect-only` 报 **`12 tests collected`**（默认打 `http://172.21.0.2:30080` + 固定账号 `qatest`，失败路径 `time.sleep` 退避）⇒ 信息级全量跑会真去戳已部署集群。改后：默认 **0 collected / 1 skipped**（模块级 `pytest.skip(allow_module_level=True)`，需 `CDS_LIVE_E2E=1` 才跑），`CDS_LIVE_E2E=1` 仍 **`12 tests collected`**（手动通道没被我关死）；与同批用例共存 `4 passed, 1 skipped / RC=0`。⇒ 顺带纠正 AGENTS.md 的记载：该文件**并非**「never collected」，`pyproject.toml` 未覆盖 `python_files`，默认含 `*_test.py`。

- **A268 追加：分片明文读取补审计留痕 —— 已由我本人做完**。改前红：`1 failed … expected exactly one read audit row, got 0`；改后绿：**`RC=0` 28 passed**（3 个 MPC 文件，比子代理交付时的 27 多 1 条新用例）。落点：`app/api/mpc.py` 在 404 之后写 `action="mpc.key.read_shares", resource_type="mpc_key", detail={share_count, threshold}`（命名对齐同文件 `mpc.key.split/rotate/destroy`；成功路径走请求事务，符合 `app/api/AGENTS.md` 约定）。用例还钉住「被拒的 403 不得写成一条成功读取审计」。裁定：`POST /verify` 维持原状（只给存在性 oracle，不泄密钥材料）。

## 子代理纠正了我自己的两处结论（必须先改自己的账）

1. **简报里我把 `identityApi` 两处解包方向写反了**。盘上实际：`getKeyAudit`（`:156-158`）认的是 **`{items}` 包装**（**裸数组**载荷才 `|| []` 静默变空审计轨迹）；`listCertificates`（`:172-173`）认的是**裸数组**（包装载荷才 `.map is not a function`）。⇒「三处三种约定、两个静默失败模式」的结论不变，但谁认哪种是我写反的，台账按子代理读数改正。
2. **`identityApi.ts` / `federationApi.ts` 里没有任何 `try/catch`**（`grep -n catch` ⇒ 无）。所以 A257「失败即放行」的真实位置不是 catch 兜底，而是**取值默认值**：`:113` `Number.isNaN(...) || expiresAt >= now ? 'active' : 'expired'`（`not_after` 缺失/不可解析 ⇒ 绿色「有效」）与 `:195` `valid: data.valid !== false && !data.error`（响应里没有 `valid` ⇒ 绿色「链验证通过」）。修的是这两行的语义，另补「请求失败 ≠ 空/允许」两条锁测试（前后皆绿，防回归）。

## 我接手的小项（F3 越界未做）

- `pages/Identity/Certificates.tsx` 的 `statusLabels`/`statusColors` 需加 `unknown: '无法确认' / 'default'`，否则新引入的 `unknown` 态会显示英文裸字符串（编译不报错，是显示缺陷）。`possiblyTruncated` 无 UI 宿主（`listSyncedEntries` 目前零消费者），不改。
- 同类新发现（未修，登记）：**`/kms/audit` 默认 `limit=50`（`le=200`）而 `getKeyAudit` 不传 limit** ⇒ 与 A281 同一缺陷类，改法要动 `KeyManagement.tsx`；`/certificates/list` 后端只接 `subject`，前端 `listCertificates({status})` 传的 `status` 被静默忽略（当前无调用方传参）。

## 我对子代理一处结论的纠正（同样是回读盘上）

- F5 说「CI 那条 npm test 门禁在 node 22.5.1 上现在就是全红，值得单独拍一下」——**不成立**。.github/workflows/ci.yml 的前端 job 用 node-version 20，步骤只有 npm ci + npm run build（见 ci.yml 第 108-116 行），**根本不跑 vitest**（与 AGENTS.md「npm test — NOT in CI」一致）。⇒ jsdom/ESM 这个坑是**本机开发跑测的坑**，不是 CI 门禁的坑；本机跑法记在上方「必带旗标」。

### B3 邻接回归的既有失败 —— 我在干净树上复现，确认为既存
`.gate-b3-adjacent.log` 报 5 条 `test_p0_security_gaps.py` 失败（`Path(...).read_text()` 未指定编码，本机 cp936 解不了 UTF-8 的 em-dash）。我在**基线工作树 `D:/workspace/ocr-base`（detached 452ffc6，零本轮改动）**用同一解释器跑同一文件：`.gate-base-p0.log` ⇒ **`5 failed, 10 passed`，10 处 `UnicodeDecodeError`**。⇒ 该文件的失败与本轮无关，属信息级既存项，未顺手修（不在本轮范围，且修它要动别人文件的读取编码，属另一类改动）。

## 我自己接手的第三组：注册页同族缺陷（F4 越界未做，标号 F7）

**动因**：F4 报「`pages/Auth/Register.tsx:70` 同族未修」。回读盘上发现不止规则串台，catch 里还有比 A278 更糟的形态：`detail || '注册失败'` 在 `detail` 为对象时把**对象整个**交给 `message.error`。

| 项 | 落点 | 红（`.gate-f7-red.log`，`2 failed / 2 passed`） | 绿（`.gate-f7.log`，`RC=0 2 files / 14 passed`） |
|---|---|---|---|
| 空密码文案串台 | `Register.tsx` 密码规则拆成 `{required, message:'请输入密码'}` + `{min:8, message:'密码至少8位'}` | `expected '请输入用户名 请输入有效邮箱 密码至少8位' to contain '请输入密码'` | 同文件 4 例含此条转绿 |
| 提示外泄/丢失 | `Register.tsx` catch 改调 `describeAuthError(err, '注册失败')`，删掉 `JSON.stringify(e)` 与 `detail \|\| '注册失败'` | `expected '[object Object]' to contain '用户名已存在'`（⇒ 生产上访客看到的是 `[object Object]`，code/message 全丢） | 同上；另 1 条为「422 取 msg」防护用例（前后皆绿） |

**顺带的结构收尾**：把 mapper 从 `Login.tsx` 提到 `pages/Auth/loginError.ts`（`describeAuthError(err, fallback)`，签名多一个兜底文案），两页共用同一映射；`Login.tsx` 删掉 37 行同位函数与那条 `eslint-disable react-refresh`，`Login.test.tsx` 的 9 处引用同步改名。**没有**让页面互相 import（`pages/AGENTS.md`：No page imports another page）。回归证据：Login 原 10 例与新增 4 例同跑 **14 passed**。

### F6（路由面包屑 + CSV 预览）读数（其报告未到，先记我复点的日志）
红 `.gate-f6-red.log`：**`Test Files 3 failed` / `Tests 10 failed | 15 passed (25)`**；绿 `.gate-f6.log`：**`Test Files 3 passed (3) / Tests 34 passed (34)`**；`.gate-f6-tsc.log` 为空文件（0 报错）。
### 与我自己 A252 计数口径的对账（不是矛盾，记清免得后人以为有一条被推翻）
F6 自重的口径：App.tsx 共 **47 个 `<Route>` 条目**（含 `<Route index>` 与 `/*` 壳），去掉壳与 `*` ⇒ **45 条可访问路径**；我台账里写的是**规范化后 44 条**（前导斜杠归一、index 归 `/`）。两个口径都独立得到同一结论：**15 条落兜底、去掉壳外的 `/login` `/register` 后 13 个真实页面显示成「运营概览」**。⇒ 缺陷与可见数一致，只是「总路由数」的分母取法不同；台账保留我的 44，另记 F6 的 45/47 备查。

## 本地可验证闸门：逐块终审（我亲自做的，不是转抄子代理自述）

对每一组保留的改动问一句「拿不出本地读数的是哪一块」，答案与处置：

| 组 | 拿不出红读数的块 | 处置 |
|---|---|---|
| F2 审计面 | `tx_hash` 的 proof 兜底删除：该路径结构性不可达（`tx_hash` 缺失 ⇒ 判定必走 unanchored ⇒ proof 查询不启用），行为等价 | **保留**（删死支，不是新增行为）；在评审台账标「等价重构」 |
| F4 登录页 | antd v5 静态 `message.error` 在 React 19 + jsdom 下不渲染 DOM ⇒ toast 的**最终呈现**本机证不了 | **保留**（映射逻辑有 5 条直测；DOM 呈现移入「待部署验收」，已写进记忆卡） |
| F3 服务层 | `possiblyTruncated` 无 UI 宿主（`listSyncedEntries` 零消费者）⇒ 无法写消费者级用例 | **保留服务层**（有服务层用例）；UI 提示不硬造宿主 |
| D1 部署 | compose 变量缺项在**真实远程车道**上是否为空、k8s 卷缺失的实际后果 | **不在本机修**：compose 侧已用真空文件/真实 `.env.prod` 双向验过；远程车道与集群侧只登记（A301/A302/A303） |
| A288 | `.dockerignore` 排除效果需镜像构建上下文 | **已按字节回退**，移入「待部署验收」 |
| F6 面包屑 | 浏览器 tab 观感（首屏/登录后/前进后退） | **保留**（`document.title` 的值已在 jsdom 三条路由断言，观感属部署验收） |
| 证书 `unknown` 标签 | 显示层文案 | **保留**（该文件 tsc 0 错误；文案属部署验收） |

结论：**没有任何一组是「只有绿、没有红」进树的**；所有「红不出来」的块要么是可证的等价重构，要么已移出本轮范围并在上面留名。
### lint 门（我补跑的一格，含一次自我纠正）
对**本轮全部 33 个改动前端文件**跑 `npx eslint`：`ESLINT_RC=1`，2 条错误**全部在 `components/Feedback/QueryFeedback.tsx`（`:5`、`:70`，`react-refresh/only-export-components`）**。到**基线工作树**跑同一文件：`RC=1`、**同一条规则同样 2 条**（行号 `:5`、`:63`，A299 把函数往下推了 7 行）。⇒ **本轮改动净新增 lint 错误 0 条**；那 2 条是既存项，修法是把 `getErrorMessage`/`tableEmpty` 拆到 sibling 模块（与 `auditDisplay.ts` 同形态），属另一个改动单元，本轮不动。顺带纠正我自己打印的一格：探针里先用 `.tsx?$` 过滤再数 `.py`，得到「后端 py=0」是**我过滤器套错了**，不是盘上没改 py（`git diff --stat` 实为 28 文件含 6 个 py）。
