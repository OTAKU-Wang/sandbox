# AI 代码评审断言核对报告 — tasks/workflow/flow_runtime

核对对象：`claims-tasks.md` 的 29 条断言（按文件出现顺序编号 [1]–[29]）。
所有 `file:line` 均为本次实际读取的行号（`read` / 编号列表输出），非从断言文本推断。
仓库：`D:/workspace/sandbox`（未修改任何文件）。

断言编号 = claims 文件里 `### [...]` 的出现顺序：

| # | 文件 | 断言主题 | # | 文件 | 断言主题 |
|---|---|---|---|---|---|
| 1 | task_code_security | 重启后密文不可解 | 16 | flow_runtime | ready_nodes 判据弱于创建门禁 |
| 2 | task_code_security | 密文解析无边界/scope 未转义 | 17 | flow_runtime | 只读端点带写副作用 + 配额口径 |
| 3 | task_scheduler | 重复 task_id 覆盖运行中任务 | 18 | task_code_security | b64decode 未 validate |
| 4 | task_scheduler | finally 无条件 pop | 19 | task_code_security | 明文降级路径无约束 |
| 5 | task_scheduler | stop() 不等待终止 | 20 | task_code_security | DEK 只增不减（内存泄漏） |
| 6 | task_scheduler | 配置项全部未生效 | 21 | task_scheduler | submit 不校验状态 |
| 7 | workflow_service | run_test 绕过状态机/env | 22 | task_scheduler | 异常只记 str(e) 且被吞 |
| 8 | workflow_service | 门禁哈希自证 | 23 | workflow_service | output_blocked 缺失即放行 |
| 9 | workflow_service | 空授权集合 fail-open | 24 | workflow_service | rollback/retire 指针非原子 |
| 10 | workflow_service | 测试凭证缺校验 | 25 | flow_runtime | platform_admin 死值/角色副本漂移 |
| 11 | workflow_service | 可无限重复发布 | 26 | task_scheduler | datetime/timezone/field 死导入 |
| 12 | workflow_service | 版本号 check-then-act 竞态 | 27 | task_scheduler | 用内置 callable 作注解 |
| 13 | flow_runtime | watermark 不参与 state 判定 | 28 | workflow_service | allowed 未做大小写归一 |
| 14 | flow_runtime | calls_today 混两套口径 | 29 | workflow_service | published_at DESC → NULLS FIRST |
| 15 | flow_runtime | 趋势 2×days 次串行查询 | | | |

---

## Reachability map

判定方式：`rev_imports3.py`（静态解析 `app.main` 的导入闭包，含 `from PKG import SUB`）+ 全库 grep 复核动态导入。

| module | production importers → entry point | LIVE/LATENT |
|---|---|---|
| `app/api/flow_runtime.py` | `app/main.py:18` → `app.include_router(flow_runtime_api.router, prefix="/api/v1/flow")` `app/main.py:351`；暴露 `GET /api/v1/flow/dashboard/home`（`flow_runtime.py:185`）、`GET /api/v1/flow/runtime-status`（`:48`） | **LIVE**（`main.py reaches it? : True`） |
| `app/services/workflow_service.py` | `app/api/workloads.py:18` → `main.py:350` `/api/v1/workloads`（test/publish/retire/rollback）；`app/api/notebooks.py:244`、`app/api/deliveries.py`、`app/services/compute_scheduler.py:127`、`app/services/delivery_service.py:295` | **LIVE**（`True`） |
| `app/services/task_code_security.py` | 写：`app/api/inference.py:29`、`app/api/sandbox_tasks.py:153`、`app/api/agent.py:101`、`app/api/deliveries.py:611,689`；读：`app/api/sandbox_tasks.py:313`、`app/services/task_pipeline.py:419`（lifespan `main.py:93-94`）、`app/services/task_worker.py:80`（`main.py:100`）、`app/services/delivery_service.py:117` | **LIVE**（`True`） |
| `app/services/task_scheduler.py` | `rev_imports3`：direct/transitive importers 全为 **NONE**，`main.py reaches it? : False`。全库 grep `task_scheduler` 仅命中 `app/services/task_scheduler.py:103`、`tests/test_task_scheduler.py:8`、`tests/test_sprint8_integration.py:19`；无 `importlib`/`__import__` 动态导入；`app/services/task_pipeline.py:3` 自述 “Replaces the disconnected TaskScheduler/Queue/Worker” | **LATENT**（死代码，仅测试导入；`docs/spec-implementation-analysis.md:88` 亦记为未集成） |

---

## app/services/task_code_security.py

### [1] 加密后的任务代码重启/多实例后不可解密 — **TRUE·LIVE**

证据链：
- `app/services/kms_service.py:40` `self._wrapped_keys: dict[str, bytes] = {}  # wrapped DEK blobs`（纯进程内）
- `app/services/kms_service.py:82-87` 每次 `generate_data_key` 新建 `dek-<product>-<hex8>` 并只写内存字典
- `app/services/kms_service.py:353-378` 存在 `export_wrapped` / `import_wrapped`（gap B3），但任务码路径一次都没调用
- `app/services/kms_recovery.py:22-45` 重启仅从 `KeyMetadata.wrapped_payload` 回填；本模块不写 `KeyMetadata`
- `app/services/task_code_security.py:15-18` 只把 `key_id`+密文落库，无 wrapped DEK
- 写读分离的实证：`app/api/sandbox_tasks.py:155-162` 建任务时写 `code_content`，`app/api/sandbox_tasks.py:314` 与 `app/services/task_pipeline.py:420` 在**后续请求/工作进程**里解密
- 失败点：`kms_service.py:149-151` `raise ValueError(f"Key {key_id} not found or unwrap failed")`

补充：这不是 `kms_recovery.py` 文档里已记的 P-GAP-004（多副本 KEK 需真 HSM）。即便换上真 Vault，任务码的 wrapped DEK 从未 `export_wrapped` 落库，恢复路径也无从恢复。
修复：`encrypt_task_code` 内 `kms_service.export_wrapped(key_id)` 与密文一同入库（复用 `KeyMetadata.wrapped_payload`），`decrypt_task_code` 未命中时 `import_wrapped` 回填。

### [2] 密文解析缺边界处理，`scope` 含冒号破坏可逆性 — **TRUE·LIVE**（触发需脏数据/未来调用方）

- `app/services/task_code_security.py:30-31`：`body = stored_code[len(_PREFIX):]` / `key_id, encoded = body.split(":", 1)` — 无冒号时 `split(":",1)` 返回 1 个元素 → `ValueError: not enough values to unpack (expected 2, found 1)`，异常裸抛给调用方（`sandbox_tasks.py:314` 直接 500）。
- key_id 组成：`task_code_security.py:15` `f"task-code-{scope}"` → `kms_service.py:82` `f"dek-{product_id}-{uuid4().hex[:8]}"`，即 `dek-task-code-{scope}-{hex8}`，与断言一致；`scope` 含 `:` 时按第一个冒号切分必然得到截断的 key_id。
- 现状可达性核对：现有 4 个调用点传入的 scope 都不含冒号 —— `task_id`（`sandbox_tasks.py:152` `f"task-{uuid4().hex[:12]}"`、`agent.py:103`、`inference.py:147`）与 `f"webhook-{sub_id}"`（`deliveries.py:622,693`，UUID）。所以「scope 含冒号」这一支是**未来调用方/接口变更引入**的风险；「无冒号 → 裸 ValueError」这一支需要脏/截断的库值。
修复：`if ":" not in body: raise ValueError("Malformed encrypted task code")` + `body.rsplit(":", 1)`（base64 字母表不含 `:`），并对 `scope` 做白名单校验。

### [18] `base64.b64decode` 未传 `validate=True` — **TRUE·LIVE**

- `app/services/task_code_security.py:32` `ciphertext = base64.b64decode(encoded.encode("ascii"))`：默认 `validate=False` 会静默丢弃非字母表字符（含 `:`、`-`、换行），损坏被推迟到 GCM tag 校验（`kms_service.py:152-155`）才暴露；非 ASCII 输入则在 `.encode("ascii")` 处抛未语义化的 `UnicodeEncodeError`。
- 该路径确实可达（同 [1] 的读路径）。
修复：`base64.b64decode(encoded, validate=True)` 并 `except (binascii.Error, ValueError) → raise ValueError("Corrupted task code ciphertext") from exc`。

### [19] 「无 `kms:v1:` 前缀即明文」的无条件降级 — **TRUE·LIVE**（行为确定，攻击面为库写权限）

- `app/services/task_code_security.py:25-26` `if not stored_code.startswith(_PREFIX): return stored_code` — 无开关、无 `logger`、无审计。
- 该行为被测试锁定：`tests/test_task_code_security.py:18-19`（`test_legacy_plaintext_task_code_still_supported`），修复需同步改测试。
- 反向影响核对：全库只有 3 个 `code_content` 写入点，**全部走加密**（`agent.py:110`、`inference.py:155`、`sandbox_tasks.py:162`），所以断言中「某些降级/未加密写入路径会把明文写成待执行代码」目前**没有对应的 app 内写入点**；实际暴露面是「谁能写 `sandbox_tasks.code_content`，谁就能注入按明文原样送进沙箱执行的代码」（`task_pipeline.py:420,456,460`）。
修复：用 `settings.LEGACY_PLAINTEXT_TASK_CODE_ALLOWED` 之类开关限定，命中时 `logger.warning`，关闭时 fail-closed。

### [20] 每个任务派生的 DEK 永不释放（无界增长） — **TRUE·LIVE**（与 [1] 同根因，不同缺陷：泄漏 vs 持久性）

- `kms_service.py:87` `self._wrapped_keys[key_id] = wrapped`，key_id 每次唯一（`kms_service.py:82`）。
- 全库 `del _wrapped_keys`/清理只有 `destroy_key`（`kms_service.py:335-351`）与 `rotate_key`（`:166-169`）；grep `_wrapped_keys[` 的 6 处命中里没有任何任务码路径调用 `destroy_key`。
- 对照：`cleanup_expired_distributions`（`kms_service.py:380-428`）只处理 `KeyDistribution` 的 TTL，不清 `_wrapped_keys`。
修复：按 `scope`（租户/任务集）复用固定密钥 + wrapped DEK 持久化，或给 `_wrapped_keys` 加 LRU/TTL 回收。

---

## app/services/task_scheduler.py（整模块 LATENT）

下列 8 条断言的**代码事实全部成立**，但整模块无生产调用者（见 Reachability map），故一律 **TRUE·LATENT**：`TaskScheduler` 只被 `tests/test_task_scheduler.py:8` 与 `tests/test_sprint8_integration.py:19` 导入，`app.main` 静态闭包不含它，且 `app/services/task_pipeline.py:3` 明确说明已用新流水线取代它。全模块 104 行已通读。

### [3] 重复 `task_id` 覆盖运行中任务 — TRUE·LATENT
- `task_scheduler.py:88-89` `task = asyncio.create_task(_run())` / `self._running_tasks[task_id] = task` —— 提交前无任何 `task_id in self._running_tasks` 检查（`:66-90` 全路径）。
- 连带：`stop()`（`:58-64`）只能取消字典里剩下的那个新任务；旧任务失去强引用（loop 仅持弱引用）→ 可被 GC。
- 修复：提交前 `if task_id in self._running_tasks: return False`（或先取消旧任务）。

### [4] `finally` 无条件 `pop(task_id)` 误删他人记录 — TRUE·LATENT
- `task_scheduler.py:85-86` `finally: self._running_tasks.pop(task_id, None)`，未做对象身份比对。
- 修复：`if self._running_tasks.get(task_id) is task: self._running_tasks.pop(task_id, None)`。

### [5] `stop()` 只 cancel + clear，不等待终止 — TRUE·LATENT
- `task_scheduler.py:58-64`：`for task_id, task in self._running_tasks.items(): task.cancel()` → `self._running_tasks.clear()` → 直接返回，无 `await asyncio.gather(...)`；循环变量 `task_id` 未使用（`:61`）。
- 修复：先 `tasks = list(self._running_tasks.values())`，`clear()` 后 `await asyncio.gather(*tasks, return_exceptions=True)`。

### [6] `poll_interval_seconds`/`max_retries`/`task_timeout_seconds` 从未生效，文档与实现不符 — TRUE·LATENT
- 定义：`task_scheduler.py:25-27`；全文件 `self.config` 仅出现 3 次，全部是 `max_concurrent_tasks`（`:56`、`:71`、`:97`）。`_run()`（`:80-86`）无 `asyncio.wait_for`、无重试循环；`start()`（`:51-56`）只置状态，无轮询循环。
- 文档不符：`:31` “Background task scheduler with priority queue and concurrency control.” 与 `:3` “priority-based task scheduling” —— 文件内既无队列也无优先级。
- 修复：实现超时/重试（`asyncio.wait_for(..., self.config.task_timeout_seconds)`），否则删掉未生效配置与 priority 描述。

### [21] `submit()` 不校验调度器状态 — TRUE·LATENT
- `task_scheduler.py:66-90` 全程未读 `self._state`；`stop()`（`:58-64`）之后 `submit()` 仍返回 `True` 并创建任务，此时已无人再取消它。
- 修复：容量检查前 `if self._state != SchedulerState.RUNNING: return False`。

### [22] 异常只记 `str(e)` 且被完全吞掉 — TRUE·LATENT
- `task_scheduler.py:83-84` `except Exception as e: logger.error("Task %s failed: %s", task_id, e)` —— 无 `exc_info`，traceback 丢失；`submit()` 早已 `return True`（`:90`），失败既不重试也不回写 `PipelineTask`。
- 修复：`logger.exception(...)` + 按 `max_retries` 重试并把失败状态回写业务表。

### [26] `datetime`/`timezone`/`field` 死导入 — TRUE·LATENT
- `task_scheduler.py:8` `from datetime import datetime, timezone`、`:9` `from dataclasses import dataclass, field`；通读全文件（104 行）除这两行外无任何 `datetime`/`timezone`/`field` 引用。
- 修复：删掉未用导入（`from dataclasses import dataclass`）。

### [27] 用内置 `callable` 作类型注解 — TRUE·LATENT
- `task_scheduler.py:37` `self._task_handlers: dict[str, callable] = {}`、`:39` `def register_handler(self, task_type: str, handler: callable)` —— `callable` 是内置函数，不是类型，静态检查报错/退化且无约束力。
- 修复：`Callable[[str, dict], Awaitable[None]]`。

---

## app/services/workflow_service.py

### [7] `run_test` 直写 `TESTING`，绕过 `assert_transition` 与 env 校验 — **TRUE·LIVE**

- `workflow_service.py:141` `workload.status = WorkloadStatus.TESTING.value`（同函数内无 `assert_transition` 调用；`assert_transition` 定义在 `:82-88`，仅 `retire` 用（`:278`））。
- 终态定义：`:28` `WorkloadStatus.RETIRED.value: set()`；`:27` PUBLISHED 只能 → RETIRED/TESTING。
- 调用方同样不校验 env：`app/api/workloads.py:154-163`（`test_workload` 只 `_load_workload` 后直接 `wf.run_test`；`_load_workload` `:28-35` 只查归属）。
- 后果 1（退役工件回炉）：RETIRED 的 test 负载 → `:141` TESTING → `:215` TEST_PASSED → `publish()` 的 `:223-226` 前置全部成立 → 新生产版本。
- 后果 2（污染 latest）：对 env=production 的负载跑测试会把 `status` 从 `published` 改成 `testing`/`test_passed`，而 `retire` 的候选集合（`:282`）与 `rollback` 的候选集合（`:295`）都要求 `status == PUBLISHED` → 该生产版本从指针候选里消失。
修复：写状态前 `assert_transition(workload.status, WorkloadStatus.TESTING.value)`，并在 `run_test` 开头拒绝 `workload.env != WorkloadEnv.TEST.value`。

### [8] 门禁哈希自证（同一字段比两次） — **TRUE·LIVE**

- `:146` `report: dict[str, Any] = {"env": ..., "entrypoint_hash": workload.entrypoint_hash}` —— 取字段值，而非本次送进沙箱的 `workload.entrypoint`（`:179` `code=workload.entrypoint`）的哈希。
- `:235-236` `tested_hash = (run.report or {}).get("entrypoint_hash")` / `if not tested_hash or tested_hash != workload.entrypoint_hash:` —— 与 `:146` 同源同值，恒等成立（除 run 被手工改过）。`hash_entrypoint()`（`:46-47`）在本模块的测试/发布路径上从未被调用。
- 现状触发面：`entrypoint_hash` 只在工作负载创建时算一次（`app/api/workloads.py:76` `entrypoint_hash=wf.hash_entrypoint(entrypoint)`），且 `app/api/workloads.py` 没有修改 entrypoint 的端点，所以「entrypoint 与 hash 不一致」目前只能来自库外写入/数据修复/历史脏数据 —— 断言列的场景即此。
修复：`tested_hash = hash_entrypoint(workload.entrypoint or "")`，并与 `workload.entrypoint_hash` 交叉校验，不一致即 `GATE_HASH_MISMATCH` fail-closed。

### [9] `not allowed` 分支对空授权集合 fail-open — **TRUE·LIVE**（但措辞需修正：与平台既有约定一致，不是 docstring 反义）

- `:67-69` `allowed = {...}` / `if not allowed or allowed & SANDBOX_MODE_ALIASES[wanted]: return wanted` —— 空集合（未配置/全空白）直接放行。
- contract 可为 None 的调用点：`run_test` `:154` `contract = await db.get(...) if workload.contract_id else None` → `getattr(None, "allowed_sandbox_modes", None)` → `allowed` 空；`delivery_service.py:329,342` 同样可能 `contract=None`。
- 更关键：这不是边缘情形 —— `app/schemas/contract.py:22` `allowed_sandbox_modes: list[str] | None = None`，`:33-38` 校验器对 falsy 值原样返回，因此**不填模式的合约默认就是 NULL**，走的就是放行分支。
- 措辞修正：docstring（`:53-55`）只承诺「合约没放行 preferred 时 fail-closed」，未对空集合表态；且真正的会话门禁同样把「未配置」当作不限制：`app/api/sandbox_sessions.py:168-170` `if not allowed_modes: return True`。所以 `resolve_sandbox_mode` 与门禁一致，没有在这里额外放权，severity 应低于 high/security。
修复：若要 fail-closed，需同时改 `sandbox_sessions.py::_mode_allowed_by_contract`，并让「无合约」与「合约未授权」走不同分支（调用方显式决定是否跳过合约校验）。

### [10] 发布凭证校验缺 `workload_id` 与 `status` 两项 — **TRUE·LIVE**（跨负载那一支当前不可由 API 达成）

- `:230-233` 只判 `run is None or not run.passed` —— 无 `run.workload_id != workload.id`，也无 `run.status != TestRunStatus.PASSED.value`。
- `test_run_id` 确为无外键的普通 UUID 列：`app/models/workload.py:55` `test_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))`（对照 `app/models/workload_test_run.py:24` 的 `workload_id` 有 FK）。
- 可达性核对：全库仅 `run_test` 写 `workload.test_run_id`（`:142` 置 None、`:216` 置 run.id），`app/api/workloads.py` 无 update 端点可指任意 run，因此「指向别人的通过记录」需库写权限；`passed` 与 `status` 由 `:212-213` 同步写入，app 内不会背离。属于纵深防御缺口，不是当前可利用路径。
- 另：`publish` 成功后既不消费源 run（`:266` 只是复制 `test_run_id`），也不消费源负载 —— 与 [11] 同一条。
修复：`if run is None or run.workload_id != workload.id or not run.passed or run.status != TestRunStatus.PASSED.value: raise WorkloadGateError(GATE_TEST_NOT_PASSED, ...)`。

### [11] 同一负载可无限次重复发布 — **TRUE·LIVE**

- `:270-272` 发布只 `db.add(prod)` + `workload.published_at = now`，源负载 `status` 仍是 `TEST_PASSED`（`:225` 的检查在发布后依旧成立），run 上也没有「已消费」列（`app/models/workload_test_run.py:23-38` 全列）。
- 每次调用都会：`max(...)+1` 生成新版本（`:246`）、把旧版本 `is_latest=False`（`:247-248`）、`dispatch_event("workload.published")`（`app/api/workloads.py:202-208`）。
- 端点无幂等/无锁：`app/api/workloads.py:188-192`。
修复：发布时 `assert_transition(workload.status, WorkloadStatus.PUBLISHED.value)` 并把源负载置为已消费（或在 run 上记 `consumed_by_workload_id`）。

### [12] 版本号 + `is_latest` 的 check-then-act 竞态 — **TRUE·LIVE**

- `:245` `versions = await list_production_versions(db, workload.contract_id)` → `:105-110` 纯 `select(...).order_by(version.desc())`，**无 `with_for_update`**；`:246` Python 侧 `max(...)+1`；`:247-248` 批量改写 `is_latest`。
- 无唯一约束兜底：`app/models/workload.py:26-29` 三个索引 + `alembic/versions/0019_flow_orchestration.py:101-103` 全部 `unique=False`。
- 后果：并发 publish 可插入同 `version` 且都 `is_latest=True`；`get_production_head`（`:97-102`，依赖 `is_latest`）随后不确定 —— 该函数正被服务链使用（`app/api/deliveries.py:857`）。
修复：contract 维度 `SELECT ... FOR UPDATE`（或 advisory lock）后重算版本，并补 `(contract_id, env, version)` 唯一索引与部分唯一索引 `(contract_id) WHERE env='production' AND is_latest`。

### [23] `output_blocked` 缺失即判「未拦截」（fail-open 读取） — **TRUE·LIVE**（断言的触发路径当前不可达，实际无证据路径是「空输出」）

- `:186` `blocked = bool((exec_res or {}).get("output_blocked"))` → `:193` `checks.append({"name": "output_policy_clear", "passed": not blocked, ...})`：字段缺失 ⇒ passed=True，确实不看证据。
- 但断言列举的触发（结构精简/字段改名/返回 None）**当前不成立**：`execute_in_sandbox` 只有一条 `return exec_result`（`app/api/sandbox_sessions.py:1488`），其余全是 `raise`（`:1204,1209,1211,1216,1218,1220,1242,1256,1270,1302,1483`，`run_test` 的 `except`（`:195-198`）会把它们落成失败项）；而返回前 `output_blocked` 在每条分支都被赋值（`:1429`、`:1434`、`:1449`、`:1454`）。
- 真正可达的「零证据判通过」是空输出：`app/api/sandbox_sessions.py:1438-1449` 输出为空时 `output_blocked=False` 且 `security_report["no_output"]=True`（注释明说「空输出不等于审查通过」），而 `workflow_service.py:193` 完全无视 `no_output`，仍记 `output_policy_clear` 通过。
修复：把 `passed` 改为「必须拿到肯定证据」——`not blocked and not (exec_res or {}).get("security_report", {}).get("no_output")`，并在缺字段时抛错；同一修法覆盖 `compute_scheduler.py:210,213` 与 `delivery_service.py:387`。

### [24] `rollback` / `retire` 的 `is_latest` 先读后写非原子 — **TRUE·LIVE**

- `retire`：`:281` 读 `list_production_versions`（无锁）→ `:284-285` 排序后把 `remaining[0].is_latest = True`，不知道另一事务刚插入新的 latest。
- `rollback`：`:294` 读 → `:298-301` `for w in versions: w.is_latest = (w.id == target.id)`；并发 retire 的 target 仍可被指为 latest。
- 与 [12] 同类但不同函数（`retire`/`rollback` vs `publish`），非重复条目。
修复：改用单条条件 `UPDATE workloads SET is_latest = (id=:target) WHERE env='production' AND contract_id=:c`，或以 `with_for_update()` 读版本列表后再改。

### [28] `allowed` 只 `strip()` 不做大小写归一 — **TRUE·LIVE**（但后果不是「静默」，会在建会话处 400）

- `:123` `allowed = {lv.strip() for lv in ((getattr(contract, "allowed_sandbox_levels", "") or "")).split(",") if lv.strip()}` —— 无 `.lower()`；候选 `:124` `for level in ("l2c", "L3")` 大小写敏感；无匹配则 `:127` `return "l2c"`，函数内无任何日志。
- 词表确实混用：`app/schemas/contract.py:21` 默认 `"l2c"`（小写），而 `app/models/contract.py:84` 注释写 `# e.g. "L2,L3"`（大写）—— 两套写法同时存在。
- 修正断言的后果描述：合约写 `"l3"`（小写）时，`pick_test_sandbox_level` 返回 `"l2c"`，随后 `app/api/sandbox_sessions.py:260-262` `if allowed_levels and body.sandbox_level not in allowed_levels: raise HTTPException(400, "Sandbox level 'l2c' not allowed by contract")` —— 表现为**显式 400**，不是「静默回落」。且回落方向是更强的档位（`:117-121` 明确 l2c 是下限之上、L3 之下不选），不构成隔离下探。
- 顺带证实断言尾注：`not allowed` 时返回 l2c（`:125`）与 `resolve_sandbox_mode` 的 fail-closed 取向确实不一致（`:68` 只在有授权集合时判定）。
修复：`allowed = {lv.strip().lower() ...}` 且候选统一小写 `("l2c", "l3")`，并在无匹配/无约束分支加一行 `logger.info` 说明选档依据。

### [29] `published_at` 可空 + PG `DESC` 默认 NULLS FIRST — **TRUE·LIVE**（前提目前无法由 app 产生）

- `:101` `res = await db.execute(stmt.order_by(Workload.published_at.desc()))`（PostgreSQL：DESC ⇒ NULLS FIRST），且 `:98-100` 只按 `env + is_latest` 过滤，不校验 `status`。
- 可空已确认：`app/models/workload.py:56` `published_at: Mapped[datetime | None]`。
- 可达性核对：全库只有两处 `Workload(...)` —— `app/api/workloads.py:69`（`env=TEST`，`:79`）与 `workflow_service.py:250`（`env=PRODUCTION` 且 `:267` 必写 `published_at=now`）。所以「生产负载 published_at 为 NULL」当前只能来自导入/回填/库外写入。
- 影响面确认：`get_production_head` 被服务链使用（`app/api/deliveries.py:857`）与 `publish` 记 previous（`:244`）。
修复：`stmt.order_by(Workload.published_at.desc().nullslast())`，并加 `Workload.status == WorkloadStatus.PUBLISHED.value` 过滤。

---

## app/api/flow_runtime.py

### [13] `watermark_status` 被读出却从不参与 `state` 判定 — **TRUE·LIVE**

- `:352-361` 判定块：`watermark` 只在 `if t5 is None and watermark is None`（`:354`）里出现；`elif` 两支只看 `t5`；`else: state, state_reason = "normal", None`（`:360-361`）。`watermark` 之后仅在 `:376` 原样回传。
- 词表确认为异常结论：`app/models/delivery_bundle.py:35` `# not_requested / applied / partially_applied / not_applied`；且这两个值**真的会被写**：`app/api/deliveries.py:180-187`（`_watermark_status`：未注入→`not_applied` `:186`，部分注入→`partially_applied` `:187`）。
- 因此 `t5 == "passed"` + `watermark == "not_applied"` ⇒ `state="normal"`、`state_reason=None`。
- 前端确实按 `state` 出「正常」：`cds-frontend/src/pages/Flow/DevHome.tsx:427-438` 用 `resultSetStatePill(row.state)`，`cds-frontend/src/pages/Flow/__tests__/homeModel.test.ts:99` 钉住 `resultSetStatePill('normal') == { label: '正常' }`；同页另有水印列按 `not_applied → 未嵌入水印`（`display.ts:244`）显示红标，与「正常」pill 自相矛盾。
- 子情形 1（`t5 is None and watermark is not None`）属实但依赖部分写入的历史行：两个值总是成对写（`deliveries.py:294-295`），迁移 0021 前的行是双 NULL（走 `:354-355` 的「未记录」分支）。
修复：把水印结论并入异常判定 —— `if watermark in ("not_applied", "partially_applied"): state="abnormal"` 并追加对应 `state_reason`。

### [14] `calls_today` 一个区块里混两套口径且未逐字段下发 — **TRUE·LIVE**

- 全平台口径：`:271-273` `await _audit_window_counts(db, since=today_start, until=None, actions=CALL_ACTIONS)` —— 不传 `user_id`，`_audit_window_counts`（`:155-182`）因此不加 `AuditLog.user_id` 过滤。
- 本人相关口径：`:253-254` `last_called_at` 取自 `ep_by_status`，而 `ep_by_status` 来自 `:241-248`，`where` 是 `ApiEndpoint.owner_id == uid or Contract.buyer_id/provider_id == uid`（`:245`）。
- 响应块 `:421-428` 只有一个 `source: "audit_logs"`，没有逐字段 scope；`last_called_at` 其实来自 `api_endpoints.max(last_called_at)`（`:243`），连 `source` 都不准。
- 页面级 note（`:389`）只说了「calls_today 是全平台」，前端照抄会把别人的调用时间当成自己的。
修复：区块内加 `count_scope="platform"` / `last_called_at_scope="self"` + `last_called_at_caliber`，并把 `last_called_at` 的 `source` 改为 `api_endpoints`。

### [15] 趋势区块在首页热路径上串行 2×days 次查询 — **TRUE·LIVE**

- `:322` `for back in range(days - 1, -1, -1):` 每天两次 `await _audit_window_counts(...)`（`:325-327` 调试、`:328-332` 执行），串行；`days` 上限 30（`:187` `Query(7, ge=1, le=30)`）⇒ 单请求最多 60 次 `SELECT action, count(*) ... GROUP BY action`，再加 `:271`、`:275` 两次共 62 次往返。
- 本文件自述这是「高频只读页」（`:85`）。
修复：整窗一次 `where created_at >= 窗口起点 and action in (DEBUG+EXEC) and user_id = uid group by action, <按天表达式>`（或一次取 `(action, created_at)` 明细桶），Python 侧再按日分桶，保持「逐日半开区间、不用 `CAST(... AS DATE)`」的既定口径。

### [16] `available=true` 的判据弱于真实创建门禁 — **TRUE·LIVE**

- 本端点：`:56-63` `ready = count(SandboxNode).where(status == ONLINE)`；`:65-70` 仅 `dataplane_disabled` / `ready == 0` 两种 reason。
- 真实门禁：`app/api/sandbox_sessions.py:393-413` `l2c` 走 `select_cube_node(...)` → `app/services/cube_runtime_adapter.py:99-119` 要求 `status == ONLINE`（`:107`）**且** `scheduling_disabled == False`（`:108`）**且** `node_cubelet_endpoint(node)` 非空（`:114`；定义 `app/services/cube_node_client.py:295-304`）**且** `runtime_version` 匹配（`:116`，调用处传 `pinned_cube_version()`）+ 模板 READY 局域性（`:122-164`）。不满足即 `:422-425` 400 `RUNTIME_UNAVAILABLE`。
- 隔离节点确实算 online：`app/api/sandbox_nodes.py:162-163` 只置 `scheduling_disabled=True` + `health_state="isolated"`，不改 `status` ⇒ 全部节点被隔离时本端点仍返回 `available=true, ready_nodes>0`，点「启动测试沙箱」得到 400 —— 与本文件 `:5-6`「点击之前先如实告知」直接冲突。
- 断言的 W14 stale 子项需修正：`health_state="stale"` 的节点（`app/api/sandbox_nodes.py:215-217`，`status` 仍 ONLINE，直到 `2×NODE_STALE_SECONDS` 才转 offline，`:252-266`）在这里被计入 ready，但真实门禁 `_eligible_cube_nodes` 同样不看 `health_state` —— 这一项**不是**与门禁的偏差，只是「ready」措辞偏乐观。
修复：`ready` 统计复用 `_eligible_cube_nodes` 的判据（`status==ONLINE AND scheduling_disabled==False AND cubelet_endpoint 非空`），并把不可调度的 online 节点另发 `online_but_unschedulable`。

### [17] 只读端点带写副作用 + 配额口径失真 — **TRUE·LIVE**

- 写副作用：`app/services/sandbox_manager.py:162-166` `def get_tenant_quota(user_id): if user_id not in _tenant_quotas: _tenant_quotas[user_id] = TENANT_DEFAULTS.copy(); return _tenant_quotas[user_id]`；首页 `flow_runtime.py:313` 调用它。全库对 `_tenant_quotas` 的赋值只有 `:165`（默认）与 `:266`（Redis 回填），**无任何淘汰**（无 pop/del）⇒ 随访问用户数线性增长的常驻内存。
- 口径失真：`flow_runtime.py:444` 把 `quota_source` 写成「租户内存配额（进程内计数，Redis 镜像；重启后由会话重算）」，而 `:314` 取的就是上一步的字典值。回填/重算确实存在（`sandbox_manager.py:245-268`、`:271-308`；启动 `main.py:64-77`），但两者都不改 `max_memory_mb`：`restore_tenant_quotas` 只回填 `_QUOTA_INT_FIELDS`（`:260-263`），而 `max_memory_mb` 全库唯一来源是 `TENANT_DEFAULTS = {..., "max_memory_mb": 8192, ...}`（`:144-149`）—— 全库没有任何按租户设置 `max_memory_mb` 的写入点（grep 命中仅 `flow_runtime.py:132` 说明文字与 `app/api/quota.py:307` 读取）。
- 即 `memory.quota_mb` 恒为 8192 的平台默认值，却被标注成「租户内存配额」，与 `:88-93` 铁律 1「每个数字都要有真实落库来源…绝不把未采集当已配置」相悖（`:127-136` 的 `not_collected` 只解释了执行档分母沿用同一池子，并未声明该配额是默认值）。
修复：改用只读访问器 `_tenant_quotas.get(str(uid))`，miss 时 `quota_mb=None` 并把 `memory.quota` 计入 `not_collected`；同时给 `get_tenant_quota` 拆出无副作用的读路径供只读面使用。

### [25] `PLATFORM_AUDIT_ROLES` 含死值 `platform_admin`，且是漂移的手工角色副本 — **TRUE·LIVE**

- `:113` `PLATFORM_AUDIT_ROLES = ("admin", "platform_admin", "operator", "regulator")`；`app/models/user.py:11-16` `UserRole` 只有 `data_provider/buyer/operator/regulator/admin` ⇒ `"platform_admin"` 永不命中（`users.role` 默认 `UserRole.BUYER`，`user.py:26`）。
- 注释漂移：`:111-112` 自称「与 `app/api/audit.py:186` 的 `require_roles` 同一组」，而 `app/api/audit.py:186` 是 `require_roles(UserRole.OPERATOR, UserRole.REGULATOR, UserRole.ADMIN)` —— 三个角色，没有 `platform_admin`。同文件其他手工副本都带漂移说明（如 `:203` 引 `CONCURRENT_STATUSES`、`:104-105` 引 `AUDIT_EVENT_TYPES`），这条没有。
- 附带风险（非断言所列）：`users.role` 是自由字符串列，若某处真写入 `"platform_admin"`，这里会给它平台级 `ops_log` 口径，而 `GET /api/v1/audit/records` 会对同一账号 403，形成两套口径不一致（`app/api/workloads.py:33` 有同样的字面量副本）。
修复：`PLATFORM_AUDIT_ROLES = (UserRole.ADMIN, UserRole.OPERATOR, UserRole.REGULATOR)`，用枚举取值而非拼字符串。

---

## Score

| file | claims | TRUE·LIVE | TRUE·LATENT | FALSE |
|---|---|---|---|---|
| `app/services/task_code_security.py` | 5（[1][2][18][19][20]） | 5 | 0 | 0 |
| `app/services/task_scheduler.py` | 8（[3][4][5][6][21][22][26][27]） | 0 | 8 | 0 |
| `app/services/workflow_service.py` | 10（[7][8][9][10][11][12][23][24][28][29]） | 10 | 0 | 0 |
| `app/api/flow_runtime.py` | 6（[13][14][15][16][17][25]） | 6 | 0 | 0 |
| **合计** | **29** | **21** | **8** | **0** |

### 核对说明（不是 FALSE，但结论需要打折的条目）

- **整模块 LATENT**：task_scheduler 8 条代码事实全部成立，但 `app.main` 静态闭包不含它、全库无动态导入，生产不可达 —— 修它属于清理死代码，不改变线上行为。
- **前提当前不可由 app 产生**：[2]（scope 含冒号）、[10]（跨负载 test_run_id 指向）、[29]（生产负载 `published_at` 为 NULL）—— 行为确为所述，触发都需库外写入或未来调用方变更，属纵深防御。
- **后果描述有误（行为仍 TRUE）**：[23] 的「字段缺失」路径不可达（`execute_in_sandbox` 唯一 return，四条分支都会写 `output_blocked`），真正可达的零证据是空输出 `no_output`；[28] 的后果是建会话处显式 400（`sandbox_sessions.py:261-262`）而非静默回落，且回落方向隔离更强；[16] 的 `health_state=stale` 子项在真实门禁里同样未过滤，非偏差。
- **severity 偏高**：[9] 的「空授权集合即放行」与真正门禁 `sandbox_sessions.py:168-170` 完全一致，是全平台约定而非本模块引入的绕行；docstring（`workflow_service.py:53-55`）也未对空集合作承诺。
- **无重复条目**：[20] 与 [1] 同根因（DEK 只存内存）但分别是内存泄漏与数据持久性；[10] 与 [11] 都提到「测试记录可复用」，但 [10] 的主张是缺 `workload_id`/`status` 校验，[11] 是源负载未消费导致可重复发布；[12] 与 [24] 同类竞态但分属 publish 与 retire/rollback。均按独立条目判定，未标 dup。
