# Agent 交接（2026-10-18 夜 · ocr 全量修复轮）

> 日期：2026-10-18 · 基线：`feat/ss-11-productization` @ `452ffc6`（本轮改动在此之上） · 性质：交接件（执行记录 + 换机续做规程）
> 范围：`ocr` 全仓评审（五波 4181 条 → 台账 319 条）与全量修复轮；未含部署侧实机验收
> 前序：`docs/cr-2026-10-15-review.md`、`docs/agent-handoff-2026-10-16.md` · 编号：台账主键 `A##`（`docs/ocr-review-2026-10-18/ocr-verified-findings.md`）

> 读者：明天在另一台机器上接手这件事的 omo/人。**先读完本文件再动代码。**
> 分支：`feat/ss-11-productization`。本轮所有改动（含未完成、未验证的）都已提交推送。

## 0. 这轮在做什么（判据）

1. 用本机 `ocr`（OpenCodeReview v1.13.1，provider `qwen-local/Qwen3.8-Flash-Next`）扫全仓 → 汇总去重 → **逐条回读代码复核真伪** → 交付按严重度排序的评审报告。
2. 用户追加：**同步修复**，且「后端的问题也要修复 要把所有问题都需要修掉」。
3. 证据判据（用户与项目铁律，不要降级）：**每条改动要有改前红 / 改后绿两份读数**；拿不到本机验证的（需 K8s/HSM/TEE/真集群）修可以修，但必须标「仅部署可验」；**不许伪造硬件路径、不许放宽断言换绿、不许删跳过测试**。

## 1. 读我顺序（全部已随仓库走，在 `docs/ocr-review-2026-10-18/`）

| 文件 | 是什么 |
|---|---|
| `ocr-verified-findings.md` | **主台账**（319 条已复核发现，`### A##` 是主键）。`## C` = 未复核/未覆盖/工具限制；`## D` = 修复状态（⚠ 只回写到 28 条，见 §4） |
| `ocr-review-report.md` | 交付给用户的评审报告（由 `report-gen.mjs` 从台账生成） |
| `fix-waves.md` | 32 路派工的**文件归属表**（互斥文件集）、W2/W3 分桶、**接续登记表** |
| `fix-leftovers.md` | 同一文件剩下的其他严重度条目，按**归属人**分节（用 `task_send` 并回，禁止另开代理改同一文件） |
| `fix-queue.md` | 机器生成的待修队列（`queue.mjs` 产），见 §4 的账面数 |
| `fix-brief.md` | 派给子代理的作业规范（双读数判据、日志命名、边界） |
| `claims-*.md` / `verify-*.md` | 各波扫描的原始断言与各域复核笔记（判定理由的出处，回读时省时间） |
| `gate-logs/*.log` | **177 份门读数原始日志**（`.gitignore` 已为本目录开了 `!` 例外） |
| `scan-gates.mjs` / `report-gen.mjs` / `queue.mjs` | 证据扫描器 / 报告生成器 / 队列生成器 |

## 2. 已修，且**父会话亲自复跑过门**（可以直接相信）

新增回归用例（四个模块合跑：`gate-logs/gate-parent-own-tests.log` ⇒ **20 passed / OWNTESTS_RC=0**）：

| 模块 | 覆盖条目 | 红证据 |
|---|---|---|
| `tests/test_ocr_a148_a079_a008.py` | A148（`utils/crypto._sm3_fallback_allowed` 异常改 fail-closed）、A079（用户名正则 `$`→`\Z`）、A008（`data_products` 运算符优先级，改前实测 `None == 123` 成立） | `gate-parent-a148-red.log` `3 failed, 2 passed` |
| `tests/test_ocr_a045_a046_a047.py` | A045（限流 `_resolve_scope`→`_resolve_scopes`，`user`+`ip` 双桶）、A046（`_local` 只写非空桶 + 4096 上限）、A047（`RequestSizeLimitMiddleware` 改纯 ASGI：声明长度 413/400，chunked 边流边计数超限断连） | `.gate-parent-a045-green2.log` 之前的红态；**A045 是契约变更**，见下 |
| `tests/test_ocr_a184_a027.py` | A184（`mutual_tls` 签发/校验两侧 `space_id` 不对称 ⇒ 空 `space_id` 证书永远验不过）、A027（`cache_set` 把 `TypeError` 吞成「缓存永不命中」+ `cache_clear` glob 未转义） | `gate-parent-a184-red.log` `3 failed, 3 passed` |
| `tests/test_ocr_a094_a153.py` | A094（`create_hsm_adapter` 的 `fallback_allowed=True` 默认 + `except: pass` ⇒ 配置读不出来时闸门自动打开）、A153（熔断器先赋值再比窗口 ⇒ 衰减分支不可达） | `gate-parent-a094-red.log` `2 failed, 4 passed` |

**A045 是契约变更，明天别当成回归去「修回去」**：`tests/test_rate_limit_distributed.py::test_user_scope_keyed_by_jwt_sub` 原本断言的正是被修掉的绕过（只按 `sub` 计数 ⇒ 换 IP 共享配额）。处置是**不放宽断言**，新增 `wide_ip_app` 夹具（只抬 IP 上限）+ 抽出 `_mkapp()`，并在模块 docstring 写明新契约。

其他：
- **A283 附带红**：`docker-compose.prod.yml` 第 2 行注释里的字面 `${VAR:?…}` 被 `test_deploy_manifests` 的「占位符必须登记在 `.env.example`」门当成未登记变量。改的是**注释措辞**（不塞假变量、不放宽门）⇒ `gate-parent-envvar.log RC=0`。
- **子代理交付、父会话复跑认可**：`f58` 会话面 A30/A31/A294/A24/A25（12 passed，`gate-parent-verify-f58.log`）；`f79`（接 `f5a`）`tests/test_cube_template_dist.py` 11 passed；`f5d` A114 管理台 XSS 33 passed；`f5b` 水印/RAG/DLP、`f5c` 证明/能力/快照 各自 `RC=0`；`f5e` CI 面 17 条（`permissions: contents: read`、gitleaks 换 sha256 钉定二进制且零 token、新增 `migration-pg` job、前端 `npm test` 入门禁、`ci/check_full_suite_budget.py` 预算门）⇒ `test_ci_workflow_policy.py` + `test_deploy_manifests.py` 复跑 **39 passed**。

## 3. 盘上有改动但**没有**门读数 —— 明天第一批活

`git status --porcelain` 里凡是下面这些文件，都是**被中止的子代理留下的半成品**（本机 deep-high 子代理在 ~47–52 分钟被统一 `The operation was aborted.` 中止；最后 4 路是用户改派换机时被主动取消）：

| 来源 | 文件集 | 状态 |
|---|---|---|
| `f62`（取消时正在跑） | `contractApi.ts`、`connectorApi.ts`、`ContractCreate.tsx`、`ContractDetail.tsx`、`Connectors/index.tsx` | 条目 A313–A319，半改，**未跑 vitest** |
| `f63`（取消时正在跑） | `app/api/data_resources.py`、`app/services/test_data_generator.py`、`dataResourceApi.ts`、`DesensitizedPreviewModal.tsx`、`ResourceList.tsx`、`DataPipeline.tsx`、`ProductCreate.tsx` | A309–A312，半改 |
| `f64`（取消时正在跑） | `nginx.conf`、`main.tsx`、`App.tsx`、`MainLayout.tsx`、`ProtectedRoute.tsx`、`Login.tsx`、`AuditLog.tsx`、`index.css`、`Sidebar.tsx`、`DevTask*`、`TaskDetail.tsx` | A304–A307 A228 A219 A221 A208 A235 A232，半改 |
| `f65`（取消时正在跑） | `notebookWorkbenchModel.ts`、SM2 前端模块 | A265 A267 A258，半改 |
| `f86`/`f88`/`f8a`（已派未开工） | `scripts/**`、`deploy/cube-node/**`、`k8s/**`+`helm/cds/**`+`deploy/local、deploy/e2e-remote/**` | 其**前一手**（`f60`/`f5f`/`f61`）的半成品在盘上：`install-k3s.sh`、`remote-harness/*.sh`、`gen-cube-stubs.sh`、`deploy/e2e-remote/*`、`local_deploy.sh`，以及未验证测试 `tests/test_ops_scripts_gates.py`、`tests/test_cube_node_scripts.py`、`tests/test_deploy_manifest_gates.py` |
| 提交之后才落盘的漏网件 | `tests/test_ocr_a150_a060_a061_a063_federation.py`、`tests/test_ocr_a038_a043_log_hygiene.py` | **只写了红、没来得及修**：`gate-logs/gate-parent-straggler.log` ⇒ `36 failed, 11 passed / STRAGGLER_RC=1`；`gate-logs/gate-parent-straggler2.log` ⇒ `12 failed, 1 passed / STRAGGLER2_RC=1`。它们是 A150/A60/A61/A63 与 A038/A043 的**待修规格**（不是已修证据），**未进 40 文件门禁清单**（不会弄红 CI） |
| `f77`/`f78`/`f79`（已派未开工） | 密钥面（`certificates.py`/`kms.py`/`kms_service.py`/`crypto_service.py`/`certificate_service.py`）、数据面门（`sandbox_db.py`/`secure_duckdb.py`）、开发沙箱（`dev_sandbox.py`/`cube_template_service.py`/`cube_egress_audit.py`） | 前一手 `f57`/`f59`/`f5a` 的半成品在盘上；`f78` 那手曾交回一个**名绿实红**的日志（`gate-c6-green.log` 里 `C6GREEN_RC=1`，`MissingGreenlet`），已被 `f79` 的 11 passed 超越，但 `secure_duckdb.py` 的 A48–A51 仍待复跑 |

⇒ 判据：**新增 `tests/` 文件若没有对应的 `RC=0` 门日志，一律当「未验证」**，先跑门再决定保留或改。

## 4. 账面进度（`fix-queue.md`，`queue.mjs` 于本轮收尾时重生）

`总条目=319 已修=28 判为非缺陷=8 待修=283`；backend 226 / fe 29 / plugin 23 / infra 4 / deploy 1。

⚠ **这个「已修 28」是台账 `## D` 的账面数，只回写到我自己的改动**：本轮 32 路子代理交付的修复**还没回写 §D**，所以真实剩余小于 283。回写方法：`## D` 里按 `A##` 逐条写 `已修（证据：<gate-log 名>）` / `判为非缺陷（理由）` / `未修（原因：本机不可验…）`，改完跑 `TMP_OUT=<该目录> bun docs/ocr-review-2026-10-18/queue.mjs` 重生队列，再 `bun report-gen.mjs` 重生报告（它会校验无重复 ID / 无漏项）。

## 5. 已知既存失败 —— **别再花时间归因**（全部用基线树对拍证明先于本轮存在）

基线树 = 未含本轮改动的同一分支工作树（我这边是 `D:/workspace/ocr-base`，换机后 `git worktree add ../ocr-base <本轮之前的 commit>` 重建）。

| 失败集 | 条数 | 根因 |
|---|---|---|
| `tests/test_sandbox_runtime.py` | 9 | 宿主缺 bwrap / 容器能力（`import resource` 也是 POSIX-only） |
| `tests/test_p0_security_gaps.py` | 5 | 源码文本 grep 类（`test_no_sha256_in_security_modules` 等），基线同红 |
| `tests/test_data_product_sandbox.py` + `tests/test_secure_duckdb.py` | 11 + 1 | 基线 `9→12 failed` 同集，**不是** A008/A048 改动引入 |
| `tests/test_helm_chart.py` | 5 | 中文 Windows：`pathlib.read_text()` 用 GBK 读 UTF-8 模板；Linux lane 绿，不在门禁清单 |
| 若干 `read_text` locale 类 | 6 | 同上，基线同红（`.gate-parent-a045-green2.log` 里有明细） |

对拍规程：同一命令在两棵树各跑一次，`> log 2>&1; echo "RC=$?" >> log`，**不接管道**；只有「只在现树红」的差集才是本轮引入。

## 6. 换机后的第一步（可直接执行）

```bash
pip install -r requirements.txt && pip install -e ./sdk
python -m compileall -q app tests alembic                      # 语法门
python -m pytest tests/test_ocr_a148_a079_a008.py tests/test_ocr_a045_a046_a047.py \
  tests/test_ocr_a184_a027.py tests/test_ocr_a094_a153.py -q    # 本轮新增用例，应 20 passed
node docs/ocr-review-2026-10-18/scan-gates.mjs                  # 重生门证据清单（会点名「名绿实红」）
cd cds-frontend && npm ci && npm run build && npm test          # 前端 tsc + vitest
```
门清单的**唯一真源是 `.github/workflows/ci.yml` 里那段多行 `python -m pytest -q \`**（40 个文件）；`scripts/remote-harness/source_lane.sh` 现场解析它，**不要另维一份清单**。

## 7. 工具与机器限制（写进报告 §C 的原文摘要）

- 本机 git **2.33.1** ⇒ ocr 的 `code_search` 工具**全部失败**；所有「某处会失败 / 这个函数没被调用」类断言必须由人回读到行号才算真。
- 五波共 **4181** 条原始发现 → 去重后 **319** 条入账；`w5`（scripts/deploy/k8s/helm/.github，417 条、55 文件）**只算扫描完成**，未逐条复核。
- 本机无 `helm`/`yq`/集群、Go 是 **1.18**（插件要求 1.25）、无 torch/transformers（相关用例条件 skip）⇒ 部署面与 Go 面只能做静态断言，一律标「仅部署可验」。
- CI 前端 job 只 `build`（`npm test` 是本轮 `f5e` 才加上的，尚未在 GitHub 上实跑过）。
- deep-high 子代理在本机 ~47–52 分钟统一被 abort ⇒ 派工要切到 **≤45 分钟**一手；已完成的子会话会占住并发槽位（`task_cancel` 拒绝 completed，`shutdown_request` 回 `not in a team`）。

## 8. 我在这轮自己犯过并被抓到的错（下一手别再犯）

- **管道偷 RC**：`cmd | tail -3 && next` 里 `&&` 吃的是 `tail` 的退出码。门命令一律 `> log 2>&1; echo "RC=$?" >> log`。
- **筛选跑的计数当套件规模**、**空语料当通过**（自写解析器要先打印「吃进的语料规模」；我第一次基线对拍就是因为吃了个基线树里不存在的新测试文件，`RC=4 / no tests ran` 却差点被我读成「只在现树红」）。
- **凭脑补写期望值**：转义用例我断言了输入里根本不存在的 `?`；写期望前要从被修代码/真实输入推一遍。
- **名绿实红**：日志名叫 `*-green.log` 而里面 `*_RC=1`。`scan-gates.mjs` 专门抓这个（本轮抓到 1 份真实交付被它挡回）。
- 改测试**不许**为了让它绿而放宽断言；契约变更要在 docstring 写清新契约（见 `test_rate_limit_distributed.py`）。
