# OCR 待修面修复计划（2026-10-19 第二轮 · 波次表）

> 输入：`docs/ocr-review-2026-10-18/fix-queue.md`（`总条目=319 已修=57 判为非缺陷=8 待修=254`）
> 本文档的每条判定都带**可机检证据**（`file:line` + 现场 grep/sed 读数），不是照抄队列抬头。

## 0. 先纠一处台账失真：待修数被高估

`fix-queue.mjs` 的「已修」集合**只认台账 §D 里点过名的编号**，而 10-18 那轮的实现修有一部分随 `5aa84a8` 进了代码却没在 §D 登记 ⇒ 队列把已修的仍列为待修。现场核对：

| 编号 | 严重度 | 代码现状（本轮实测） | 判定 |
|---|---|---|---|
| A1 | critical | `app/api/certificates.py:123` detail 只回 `cert_pem`；私钥走 `:46 CertPrivateKeyResponse` 独立端点 + `require_roles(ADMIN)` + 审计 | **已修**（含 `OCR-A1` 标记） |
| A13 | critical | `app/api/kms.py:98/159/208/289` 四处 `OCR-A13` 注释处已换角色门 + 归属过滤 | **已修** |
| A110 / A161 | critical | `app/services/crypto_service.py:208` 签名原文字节锁；`app/services/kms_service.py:43/110/423` 持久化 + 不可解即拒 | **已修** |
| A12 | critical | `app/api/mpc.py:204-211` `GET /keys/{key_id}/shares` 现 `Depends(require_roles(UserRole.ADMIN))` | **已修** |
| A30 | critical | `app/api/sandbox_sessions.py:759-761` `paginate_keyset(..., where=where)` 已补谓词 | **已修** |
| A24 | critical | `app/api/session_stream.py:268-278` `_run` 改用显式 `vol_db`（旧的全局 `db` 查找 + 静默 `binds=[]` 已删） | **已修** |
| A113 / A139 | high | `crypto_service.py:285/318`、`kms_service.py:399` | **已修** |

⇒ 全仓 `OCR-A\d+` 标记只覆盖 9 个编号，但**无标记 ≠ 未修**（A12/A24/A30 就没标记却已修）。所以**判「已修」的唯一凭据是被测代码 + 一条能失败的断言**，波次表按此逐条核。

## 1. 波次（每波都必须给「改前红 / 改后绿」双读数）

**W0 记账纠偏**：把上表 9 条按代码证据登记进台账 §D（附 `file:line`），队列重生。不写实现。

**W1 现在就能证红的 20 条**（红规格已在 `tests/` 里、跑起来就红 ⇒ 天然满足「改前红」）：

| 簇 | 编号 | 红数 | 落点 | 本机可验 |
|---|---|---|---|---|
| 合约激活无行锁 ⇒ DP 预算重复分配 | A19 | 1 | `app/api/contracts.py:184-215` 加锁读 | ✅（红规格用 PG 方言编译断言 `FOR UPDATE`） |
| 连接器 key 前缀列宽 8 写 15 | A6 | 3 | `app/api/connectors.py` + **新增 alembic revision**（链只加不改） | ✅ + `alembic check`/`verify_schema_parity.py` |
| 监管审计接口结论与文案自相矛盾 | A5 | 3 | `app/api/audit.py` | ✅ |
| 输出放行门 | A21 | 6 | `app/services/output_inspection.py` / `app/api/output_control.py` | ✅ |
| 验真页契约 | A134 | 7 | 对应 router（现场读台账 §A 条目） | ✅ |

**W2 未登记的 critical（需我先补红规格）**：A48/A49（`sandbox_db.py:262-276` `create_task` fire-and-forget + `secure_duckdb.py:131` 无策略即放行 ⇒ 改 await/回调 + 无策略 fail-closed + 默认 `session_id` 改完整 UUID）、A309（`masking_applied` 恒 true）、A185（水印对 numpy/非连续 tensor 静默 no-op 却 `success=True`）、A129（空文本 512 维零向量）、A116（`MAX_SNAPSHOTS=0` ⇒ 500）、A111（base64 信封绕过输出 DLP）、A183/A162（远程证明与 TEE 证据可伪造；A183 已有红规格）。

**W3 Go/SDK/部署面**：A196（rootfs 跟随 guest 符号链接 ⇒ 宿主 root 任意文件读）、A179（写死明文 `http.Client`）、A175/A180/A197、A74/A78/A206、A284（OPA 无认证可写策略）。Go 侧用 `go test`/`go vet`，本机无 Go 则标「仅部署可验」，**不得**拿 Python 侧读数冒充。

**W4 high 主题簇（76 条）**：`output_inspection.py`(5)、`sandbox_db.py`(4)、`dev_sandbox.py`(4)、`federation_connector.py`(4)、`plugin.go`(4)、`sandbox_sessions.py`(4) —— 逐条先做 §0 那种代码核对，已修的只登记，未修的进实现修。

## 2. 验证口径（本轮全程）

- 跑法：`env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY NO_PROXY='*' CDS_DATABASE_URL='sqlite+aiosqlite:///./ci_local_<x>.db' .venv/bin/python -m pytest -q --tb=line -p no:cacheprovider <files>`（并发各换 DB；代理只在测试子进程剥，不动本机网络配置）。
- 每条：先跑红规格取改前读数 → 改实现 → 同命令复跑取改后读数。**不放宽断言、不删/跳测试、不伪造硬件路径**；模拟路径继续走 `ALLOW_SIMULATION`/`is_simulation` 披露。
- 收尾三件套：40 文件 CI 门禁（清单现场解析 `.github/workflows/ci.yml`）+ 冻结树全量 + `alembic check`/`verify_schema_parity.py`；基线 22 条不得增长。
- 披露：本机 Python 3.14 vs 生产镜像 3.12（注解求值盲区）、无真 Tongsuo/HSM/bwrap、`:8181` 上有真 OPA 容器（涉策略评估的测试必须自己桩 `opa_client` 才算自足）。
- 不 commit / 不 push（等显式「cr 并提交到远程」）。

---

## 执行记录 · 10-19 第二轮（已收口：门禁 GATE_RC=0，全量 2 failed 且均为既有环境类）

### W0 记账纠偏（已完成）

`fix-queue.md` 的「已修」只认台账 §D 点过名的编号，而 10-18 那轮有一部分实现修随 `5aa84a8` 进了码却没登记。逐条以被测代码复核后登记 **21 条**（A1/A12/A13/A24/A30/A48/A49/A69/A91/A110/A111/A113/A114/A116/A129/A134/A139/A161/A162/A183/A185/A309 中的已核实项，逐条带 `file:line`，见台账 §D-补）。

- 队列读数变化：`已修 57 → 82`，`待修 254 → 229`，**critical 21 → 2**（只剩 `plugin` 域 A179/A196）。
- 队列工具两个假绿口子一并修掉：① §D 里写「A179/A196 仍开着」的行被当成已修 ⇒ 按行排除（`仍开着|不登记为已修|未判定完|待下一轮`）；② `#### A134 …` 这类修复记录标题被当成新 finding 双计 ⇒ 按编号去重。两个都是**会让报出去的数偏高/偏低**的 bug。
- 判据固化：`OCR-A##` 注释标记只覆盖 9 个编号，A12/A24/A30 无标记却已修 ⇒ 标记有无都不能当判据；唯二判据是被测代码 + 一条能失败的断言。

### W1 现红 20 条（改前红 → 改后绿）

| 条 | 落点 | 改前 | 改后（我独立复跑） |
|---|---|---|---|
| A19 合约激活无行锁 | `app/api/contracts.py` 锁定读 `with_for_update()` | `1 failed, 2 passed` | `3 passed`；合约邻域 8 文件 `77 passed` |
| A06 连接器前缀列宽 | `connector.py` `String(8)→String(16)` + `derive_key_prefix()` + 迁移 `0027` | `3 failed, 1 passed` | `4 passed`；与 `test_connectors.py` 合跑 `11 passed`；`alembic check` 无待办、`verify_schema_parity` 54 表 |
| A21 输出放行门旁路 | `app/api/output_control.py` fail-closed + 拒绝审计 + claim 表上界 | `6 failed, 3 passed` | `9 passed`；四文件合跑 `61 passed` |
| A134 验真页契约 | `app/templates/blockchain.html`（`verified` 字段 + 诚实披露上屏 + 固定错误文案） | `7 failed` | `7 passed`；A114 门禁 `24 passed` |
| A05 审计锚定 | `app/api/audit.py`（note 由 verified 派生 / 重锚定不抢已锚成员 / 批量 422）+ `merkle_service.leaf_timestamp()` | `3 failed` | **3 passed**；审计+默尔克林 16 文件合跑 **184 passed**；变异（naive 透传）→ `2 failed, 21 passed` |

规格冲突的处置（不放宽断言的那条）：A134 与 A114 在 `blockchain.html/fail` 上互斥——A114 要求注入串「转义后出现在 DOM」，A134 要求失败路径只显示固定文案（后端原文一律不上屏）。取更严的 A134，并把那**一条**用例改成形态无关的更强判据（`"<img" not in joined` 且 `"img src=x" not in joined`），其余页面判据一字未动；合跑 `31 passed`。

### A24 回归钉（把「已修但无钉」变成「已修且有能失败的钉」）

`tests/test_exec_stream.py` 新增 `test_stream_resolves_declared_shared_volume_binds` + `test_stream_refuses_exec_when_volume_resolution_fails`；**变异检验**：把卷解析改回静默 `binds=[]` ⇒ 两条分别红一次（第一次只有前者红，第二次用忠实的前修复形态 `except: binds = []` 让后者红）；还原 `cmp` 一致后 `7 passed`。

### 收尾验证（已回填 · 2026-10-19 14:54 +08:00）

- 40 文件 CI 门禁（`GATE_FILES=40`，从 `ci.yml` 现场解析）：第一轮 **1 failed / 326 passed / 2 skipped**，唯一红是 `test_blockchain_backend_disclosure::test_verify_discloses_backend_and_note`，而该用例单跑 1 passed、且 `app/api/audit.py` 正被并发修改 ⇒ 判为在途状态；带 md5 守卫的第二轮 **327 passed / 2 skipped / GATE_RC=0**（日志 `docs/ocr-review-2026-10-18/gate-logs/gate-r2.log`）。；**终版（A5 / Go 两条子代理落地之后同树重跑）= `GATE_RC=0` / 327 passed / 2 skipped / 0 failed（114.00s）**，日志 `docs/ocr-review-2026-10-18/gate-logs/gate-final-40files.log`
- 前端（我自己跑，非转述子代理）：`npx tsc -b` 无诊断；`npx vitest run` = **66 文件 / 649 用例全绿，VITEST_RC=0**（`gate-logs/fe-vitest.log`）。
- 顺带修掉台账里一条「测试非自足」：**`tests/test_policy_evaluator.py` 不再假定宿主没有 OPA**（见 §收尾 · OPA）。
- 冻结树全量回归：**`FULL_RC=1` / 2 failed / 3733 passed / 4 skipped（988.00s，0 collection error）** —— 基线 22 failed **未增长，净减 20 条**，且剩余 2 条逐条定性为既有环境类：`test_cube_upstream_check.py::test_regen_is_idempotent`（需 upstream CubeSandbox clone，本机无该路径）、`test_sm4_duckdb_roundtrip.py::TestMultiSourceEncrypted::test_register_json_encrypted`（`secure_duckdb` 的 json import-root 白名单只含 `/tmp/cds-*`，本机 fixture 路径不在内）；无新增失败类别 ⇒ 满足「不得增长」。日志 `gate-logs/full-local-final-2026-10-19.log`（脱敏 LEAK 0）。
- `compileall app tests alembic`：RC=0 ✅；`import app.main`：LOCAL_IMPORT_OK ✅
- 本机 Python 3.14 vs 生产镜像 3.12 的注解求值盲区照旧披露；真 Tongsuo/HSM/TEE/链/真 MicroVM 仍未接。

### 待定项的决定（本轮不做的，写清理由）

- **A21/A134 规格是否纳入 40 文件门禁**：本轮**不纳入**。门禁清单数量由 `tests/test_ci_workflow_policy.py` 机器锁定，并与 `ci/known-failures.md` 的声明联动（加一个文件要同时改 `ci.yml`、锁、声明三处）；本轮树里还有两条在途改动（审计锚定、Go 插件），在拿到冻结树的门禁 + 全量双读数之前扩门，等于把一个尚未在全量上下文验证过的文件放进"必须全绿"面。下一轮在全量读数落地后再纳入 `tests/test_ocr_a21_output_release_gate.py`（授权绕过）与 `tests/test_ocr_a134_verify_page_contract.py`（对外契约），并同时把 `tests/test_exec_stream.py`（A24 钉）一起评估。
- **`processGateway` 死包装**：`cds-frontend/src/services/outputControlApi.ts:55` 全仓无调用方，而 A21 后不带 `session_id` 必 403 —— 要么补会话绑定并接进 UI，要么删掉；本轮只登记，不顺手删（前端改动要走 FE 车道）。
- **`tests/e2e_full_test.py:244`**：打线上 URL 的手工脚本，pytest 从不收集，按仓库约定不动。

### 收尾 · OPA 自足性修复（台账登记项转已修）

`tests/test_policy_evaluator.py` 走的是「先试 OPA，拿不到才回落 Python PDP」（`policy_evaluator.py:87-93`），而 `_evaluate_via_opa` 会先把 bundle `push_policy` 到 OPA 再查。本机 `:8181` 有真 OPA ⇒ push 成功、查询返回 `{"result": undefined}` → `False` → DENY，同一份代码在两种宿主上判据不同（台账原文：全量一轮红、一轮绿、单跑又红）。

- 改法：文件级 `autouse` fixture 把**传输边界**的 `opa_client.push_policy` 钉成失败，确定性走回落分支；只代理传输、不代理判决。
- 证据四读数：本机改前 `1 failed, 9 passed`（红的是 `test_no_field_acl_allows_all`）→ 改后 `10 passed`；敌意 OPA 插件（`push→True`、`evaluate→{result: False}`）下 HEAD 版 `5 failed, 5 passed`、改后版 `10 passed`。临时探针文件 `tests/_tmp_opa_probe.py` 已删除并核实不存在。
