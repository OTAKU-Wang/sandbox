# Agent 交接手册 · 2026-10-19（本机会车轮 · 全部改动未提交）

> 日期：2026-10-19 · 基线：`feat/ss-11-productization` tip `f3cdd72` + 未提交工作树 · 性质：换机/换会话接手手册
> 前序：`docs/agent-handoff-2026-10-16.md`（其 §1「测试必须在远程跑」的口径**已被本轮用户指令覆盖**，见下）
> 证据目录：`docs/ocr-review-2026-10-18/gate-logs/`（脱敏后的读数原文）· 台账：`docs/ocr-review-2026-10-18/ocr-verified-findings.md` §D

## 0. 现在的状态（一句话）

OCR 复核轮写红的那 9 个测试簇 + A108/A109 两个红规格已实现修到绿，门禁层本机 40 文件 **327 passed / 2 skipped**、前端 **tsc / vitest 66 文件 649 用例 / build 全绿**（与同日远程车道同数），全量回归（冻结树）**`FULL_RC=1 / 22 failed / 3710 passed / 5 skipped`（946.10s，0 collection error）**——相对 10-16 源码直跑基线 33 条**净减 11，基线未增长** ✓；22 条里 20 条是队列里的待修红规格（未冒充完成），2 条是宿主/环境类；A15 顺带修到绿（`app/api/k8s_ops.py` 少 `await`）；队列重生 **319 / 已修 57 / 判非缺陷 8 / 待修 254**；全部改动**未提交**，等「cr 并提交到远程」这类显式指令。

## 1. 验证面（2026-10-19 起：本机为权威，远程只留给硬件门）

用户原话：「**在不影响我本地网络的情况下 在我本地进行开发测试**」。这覆盖 2026-10-15 的「测试要使用远程服务器」旧铁律。远程那台仍然留着，它是硬件门（真 MicroVM/Cube 节点、真 Tongsuo）唯一可能的地方，历史读数仍是有效证据——但别再默认「绿必须在远程取」。

### 1.1 本机跑法（照抄即可）

```bash
cd /home/kratos/Documents/workspace/sandbox
ENVD="env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY NO_PROXY='*'"
$ENVD CDS_DATABASE_URL='sqlite+aiosqlite:///./ci_local.db' \
  .venv/bin/python -m pytest -q --tb=line -p no:cacheprovider tests/<file>.py
```

- **代理变量只在测试子进程里剥**（上面那种 `env -u` 前缀）。绝不改 mihomo/clash、`/etc/hosts`、防火墙、全局 env——这是用户给的硬约束。实测在跑的全量进程 `ss -tp` **零 socket**，本机跑天生不触网。
- `tests/test_e2e_full_lifecycle.py` 继续 `--ignore`：它打真实集群，既走网络也不是本轮范围。
- 并行跑多份 pytest 时**各自换一个 DB 文件**（`ci_local.db` / `ci_local_b.db`…），同一 SQLite 文件并发互相锁。
- 门禁清单不要手抄：`ci.yml` 里第一个 pytest 续行块用脚本解析出 **40 条**（`grep -A60` 手搓会多数一条，那种运行作废不取数）。
- 前端四步 = CI 前端作业实际内容：`tsc -b` / `vitest run` / `npm run build` / `npm run lint`（最后一项在 `ci.yml` 是信息性基线，不是硬门）。**别加 `--reporter=basic`**——这个 vitest 版本没有它，`ERR_LOAD_URL` 会让 vitest 一个用例都不跑而 rc≠0，看起来像产品红。
- 车道脚本 `.gate-scratch/local_lane.sh`（哨兵 `GATE_RC=` / `FULL_RC=` / `LOCAL_LANE_DONE`）。长任务**直接重定向日志**，不要 `| tail`，否则日志全程 0 字节既看不到进度也判不出挂死。

### 1.2 本机解释力的边界（每次汇报都要披露）

本机 `.venv` 是 **Python 3.14.4**，生产镜像是 **3.12**：3.14 延迟求值注解（PEP 649），3.12 在 def 期求值 ⇒ 本机全绿**不能**排除「类体内 `def list(...)` 遮蔽内建」「注解里漏 import」这类只在 3.12 发作的缺陷（真出过生产镜像 crash-loop）。所以每轮改动后仍要：`python -m compileall -q app tests alembic scripts` + `.venv/bin/python -c "import app.main"`。同理本机没有真 Tongsuo/真 HSM/真 bwrap 权限，这些类别的失败属于环境门，见 `ci/known-failures.md` 的分类表。

## 2. 本轮改了什么（全部未提交）

| 项 | 位置 | 性质 |
|----|------|------|
| federation I1/I2（400 拒绝 + 异步 offload） | `app/api/federation.py` 等 | 实现修 |
| ops scripts / cube-node scripts / deploy manifest / a3_a165 pipeline / a26 rag ingest / a103_a157 agent runner / a04 exposure review 共 9 簇 | 见台账 §D 与 `fix-queue.md` | 实现修（不放宽断言） |
| A108 限额校验 | `app/services/sandbox_manager.py` `get_resource_limits` / `validate_resource_limits` / `_require_positive_int` | 返回拷贝 + 正整数强校验（`bool` 排除）→ 422，取代原来 0/负数被 `min()` 采纳、字符串打成 500。第二修正：**整值浮点接受**（`2.0`→2），因为 SS-15 的 wire 契约就定的是 `float \| None` |
| A15 ops 实机视图 | `app/api/k8s_ops.py` `_cluster_view()` | `await get_k8s_adapter()`（原本拿到协程对象当适配器用）+ 4 个调用点补 `await`；配套把 `tests/test_ocr_a15_k8s_ops_await.py` 的 fixture 对回真模型（`UserRole` 非 Enum、`SandboxSession.data_product_id` 必填），断言未改 → 5 passed |
| 节点验收脚本 DER 解析 | `deploy/cube-node/verify.sh` `_subject_issuer_names()` | 补跳 `TBSCertificate.signature AlgorithmIdentifier`——原本 issuer/subject 子树错位、名字集恒空，**真节点 `tls_sidecar` 必 fail**；探针：同证书 `NAMES []` → `[CN, CN]` |
| 诚实门禁的兄弟 harness | `tests/test_cube_node_tooling.py::base_env` | 补 `CDS_CUBE_ACCEPT_MOCK=1`（与同族 `test_cube_node_scripts.py:246` 同法），因为本轮给 `verify.sh` 新增的 mock 认账门没同步给它（3 红→0 红） |
| A109 租户配额 | 同文件 `_pending_deltas` / `_persist_tenant_quota` / `_overwrite_tenant_quota` / `flush_quota_persistence` / `try_reserve_tenant_usage` / `release_tenant_usage` | Redis 侧改 **HINCRBY 增量合成**（多副本相加不互相覆盖）；无 pending 绝不 `delete`；写失败留增量且 `flush` 会**重试**；净零也落一次写；超额回滚只回滚自己那一笔；Redis 挂了诚实降级为进程内预留 + WARNING + 补写队列（逐字段部分失败只补写未落的部分） |
| 全量 1541 error 的污染源 | `tests/test_pz04_honesty_gates.py::_patch_bwrap` | 把写死 `7` 的假 seccomp fd 换成 `os.pipe()` 真 fd；同文件 `monkeypatch.undo()` 换成测试自己的 `down` 开关。**断言一字未改** |
| 工装 | `scripts/remote-harness/sync_tree.sh`、`.gate-scratch/*`、`scan-gates.mjs` / `queue.mjs` | `//` 注释行改回 `#`；台账工具链换机路径/日志面修正 |

## 3. 明确没做 / 没验（别当成已完成）

- **A109 没接到入口**：`app/api/sandbox_sessions.py` 的 create 路径仍是 `check_tenant_quota` + `update_tenant_usage` 的 check-then-act。换 `try_reserve_tenant_usage` 移动准入时点（预留失败要在 provision 前拒、provision 失败要回滚预留），应与配额 429 的 e2e 同轮验。另外 `app/services/quota_manager.py` 里已有 Lua 原子配额脚本，长期应收敛成一处，不要继续两份。
- **k8s 生产清单缺 DEK / MFA 密钥项**、`FlowConfigFields` 的 Collapse 措辞：只有部署面/人眼能判，留作可提项。
- 台账 §D 与 `ci/known-failures.md` 的**本机口径行**要和本轮队列一起看；`queue.mjs` 可安全重生，`report-gen.mjs` **不可原地重生**（缺 `ocr-findings.json` 会把已提交的覆盖统计换成缺件声明）。

## 4. 本轮新增的坑（照着做省时间）

1. **测「交出 fd 的契约」必须交真 fd**：测试把 seccomp fd 写成小整数 `7`，产品按契约 `close_seccomp_fd(7)` 关掉的是 **pytest 自己的 fd**（内部 `except OSError: pass` 让越权关闭静默）⇒ 本文件 4F+4E，全量里 fd 被后续文件反复复用 ⇒ 1541 个 `OSError: [Errno 9]` 级联。定位手法：**合跑对照 + 单跑对照各一次**（只合跑则脏水泼给同批另一个簇）。
2. `monkeypatch.undo()` 连带撤掉同一个 monkeypatch 实例上 autouse fixture 打的补丁（本轮把 `_redis_client=FakeRedis` 撤了，产品被指回真 Redis）。测试内部要"关掉依赖"就用测试自己的开关，别 undo。
3. `remote_test_env.sh` 名字像 env、实际是**整条车道**：`source` 它先重跑 40 文件 gate 并覆写 `gate.log`/`ss15.log`（本轮覆掉一次，靠 `gate-logs/` 归档件没丢读数）。只要变量就 `eval "$(grep -E '^(DBURL|TESTENV)=' …)"`。
4. `pkill -f '<带路径的模式>'` 匹配到承载它的 `sshd bash -c` 自己 ⇒ 会话自杀 rc=255。按名杀用 `pkill -x tar`；被 timeout 杀掉的 `tar | ssh 'tar xzf -'` 在远端留孤儿 tar。
5. shell 里一行以 `//` 开头的"注释"被当路径执行，**且不致命**，所以 `SYNC_ALL_OK` 照样打印。shell 改动除了 `bash -n` 必须看一次真实运行的 stderr。
6. 报告/证据落库前照例脱敏（IPv4 / `scheme://user@` / DSN / ANSI），扫描残留后才算入档。
7. **改 `app/` 或 `tests/` 的文件必须冻结被测树**：本轮全量跑到一半补了 `await` 修正，那一轮读数就作废了（它混了两个树的子集）——重跑一轮才能写进基线。
8. **红规格文件自己可能从未被执行过**：`test_ocr_a15_*` 卡在 fixture（把 `UserRole` 当 Enum、漏 `data_product_id`）而从未跑到产品断言，所以“红”不等于“产品错”——先把 fixture 对回真模型，再判产品。“结构解析器要拿一张真证书跑一遍”同理：`verify.sh` 的 DER 遍历注释写对了但下标跳错，只看代码看不出来。

## 5. 下一步

0. **先修测试非自足：`tests/test_policy_evaluator.py`**（本轮新登记的宿主耦合，见台账 §D 末段 + `ci/known-failures.md` 分类表）：本机 `:8181` 上跑着真 OPA 容器（`l-opa` / `openpolicyagent/opa`），该文件假定「OPA 不可用 ⇒ 回落 Python PDP」，于是判定取决于 OPA 里 `cds.policies.<contract_id>` 的残留策略 ⇒ 三条读数互异。修法：文件内 fixture 桩 `opa_client.push_policy → False`（或按用例桩 `/v1/data`），改完重跑一轮全量才进基线。

1. **下一个最便宜的簇 = A19（合约激活无行锁 ⇒ DP 隐私预算重复分配）**，位置已定位：`app/api/contracts.py:184 activate_contract` → `:189` 现在是 `await contract_service.get_by_id(db, contract_id)`（普通 SELECT），`:204` 才写 `status = "active"`。修法是把 `:189` 换成本会话内的加锁读（`select(Contract).where(Contract.id == contract_id).with_for_update()`），`status == "active"` 的幂等分支保留。`tests/test_ocr_a19_contract_activate_lock.py` 不依赖真 PG：它把捕获的语句用 **postgresql 方言编译**后断言含 `FOR UPDATE`，所以 SQLite 上可验。（本轮没动它：全量在跑，被测树已冻结。）

2. 已完成（本轮）：本机全量数字已回填台账 §D 与 `ci/known-failures.md` 基线行，22 条按分类表逐条定性（20 条待修红规格 + `test_sm4_duckdb_roundtrip` import-root 白名单 + `test_cube_upstream_check` 需 upstream clone）。
3. 修复队列现存 **254 条待修**（`docs/ocr-review-2026-10-18/fix-queue.md`，`bun docs/ocr-review-2026-10-18/queue.mjs` 重生，输出目录用 `TMP_OUT=$PWD/docs/ocr-review-2026-10-18`）。
4. ss-18 的 M 系列页面外壳（`NotebookWorkbench` / `TaskDetail` / `TaskRunRecords` / `DeliveryCenter` / `OpenApiMonitor`）在后续轮已存在，`docs/agent-handoff-2026-10-16.md` §5 的「剩余 M3/M4/M5/M6」是 10-16 的旧口径——以 `specs/ss-18-prototype-alignment.md` §8 为准，别照 handoff 重做。

## 6. 时效更新（10-19 第二轮收口，追加而非改写上文）

上文是 10-18 轮写的，下面几条**已在本轮关闭**，照 §3/§5 施工会重复劳动：

- §5.0 `tests/test_policy_evaluator.py` 宿主 OPA 耦合：**已修**（文件自带 autouse fixture 把 `opa_client.push_policy` 钉失败，强制走 Python PDP）。四组读数：本机改前 `1 failed, 9 passed` → 改后 `10 passed`；敌意 OPA 插件下 HEAD 版 `5 failed, 5 passed` → 改后 `10 passed`。
- §5.1 A19 合约激活无行锁：**已修**（`app/api/contracts.py` 锁定读 `with_for_update()`），`1 failed, 2 passed → 3 passed`，合约邻域 77 passed。边界：SQLite 不真行锁，钉的是 SQL 形态。
- §5.3 队列数：**226 条待修**（不是 254）。终值 `总条目=319 已修=85 判非缺陷=8 待修=226`，critical **已清零**（plugin 域 A179 / A196 本轮修完）。
- §3 A109 未接入口：**仍然开着**（`check_tenant_quota` + `update_tenant_usage` 的 check-then-act 原样）—— 这条别当成已完成。
- §4.7「改 `app/` 或 `tests/` 必须冻结被测树」本轮**再次生效**：A5 / Go 两条子代理在跑期间落地，权威门禁+全量读数在它们落地**之后**重跑（`.gate-scratch/local_lane.sh`，哨兵 `GATE_RC=` / `FULL_RC=` / `LOCAL_LANE_DONE`）。
- 本轮计划与逐条双读数：`docs/ocr-fix-plan-2026-10-19.md`；台账：`docs/ocr-review-2026-10-18/ocr-verified-findings.md` §D；门禁日志：`docs/ocr-review-2026-10-18/gate-logs/gate-final-40files.log`。
