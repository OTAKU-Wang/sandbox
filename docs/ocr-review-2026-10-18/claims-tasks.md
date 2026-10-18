### [app/services/task_code_security.py] high/bug located=true
加密后的任务代码在进程重启或多实例部署后将无法解密（数据不可读风险）。`kms_service.generate_data_key()` 只把 KEK 包裹后的 DEK 写入进程内的 `self._wrapped_keys` 字典（kms_service.py 第 40、87 行），而 `kms_service` 是模块级单例，没有任何持久化落库动作。本函数的输出却是「persisted task code」（写入 DB 长期保存）。因此一旦进程重启、发版，或存在多个 worker/副本（写入与读取落在不同进程），`decrypt_task_code` 会走到 `decrypt_with_key` → `get_key` 返回 None → 抛出 `ValueError: Key ... not found or unwrap failed`，历史任务源码永久不可恢复。

建议：用 `kms_service.export_wrapped(key_id)` 把 wrapped DEK 与密文一起持久化（或写入独立的密钥表），解密前用 `kms_service.import_wrapped()` 重新载入；或改用可持久化的密钥（例如按 scope 派生的确定性密钥）并在文档中明确密钥生命周期。
-- suggested: 

### [app/services/task_code_security.py] high/bug located=true
密文格式解析缺少边界处理，且 `scope` 未做转义会破坏可逆性：
1. 若 `stored_code` 以 `kms:v1:` 开头但其后没有冒号（截断/被篡改/人为写入的数据），`body.split(":", 1)` 直接抛出 `ValueError: not enough values to unpack`，调用方拿到未包装的底层异常；同理 `key_id` 或 `encoded` 为空串时也只是在后面才失败，错误信息无任何上下文。
2. 拼接格式用未转义的 `:` 分隔，而 `key_id` = `dek-task-code-{scope}-{hex8}`。只要调用方传入的 `scope` 含冒号（例如取自租户/任务标识、外部 ID），解密时按**第一个**冒号切分就会得到被截断的 `key_id` 和被污染的 `encoded`，导致解密必然失败。

建议：base64 字母表不含 `:`，可改用 `body.rsplit(":", 1)`（或对 `scope` 做白名单校验/转义）；同时对解析失败给出带明确语义的业务异常，而不是裸 `ValueError`。
```python
    body = stored_code[len(_PREFIX):]
    if ":" not in body:
        raise ValueError("Malformed encrypted task code: missing key_id separator")
    key_id, encoded = body.rsplit(":", 1)
    if not key_id or not encoded:
        raise ValueError("Malformed encrypted task code: empty key_id or ciphertext")
```
-- suggested: 

### [app/services/task_scheduler.py] high/bug located=true
**未校验 task_id 重复，会覆盖已在运行的任务**：若同一个 `task_id` 被重复提交（例如上游 PipelineTask 重试、或 HTTP 重复投递），此处会直接用新 `asyncio.Task` 覆盖 `_running_tasks[task_id]`，导致：1) 旧任务失去引用，`stop()` 无法再取消它（成为不可管理的孤儿任务，还可能被 GC 提前回收）；2) 旧任务结束时其 `finally` 会 `pop(task_id)`，把新任务的记录误删，`active_count` 与容量判断随之失真，最终突破 `max_concurrent_tasks` 上限。建议在提交前先做重复性检查（或在覆盖前取消旧任务）。
-- suggested: if len(self._running_tasks) >= self.config.max_concurrent_tasks:
            logger.warning("Scheduler at capacity (%d/%d)", len(self._running_tasks), self.config.max_concurrent_tasks)
            return False

        if task_id in self._running_tasks:
            logger.warning("Task %s is already running, reject duplicate submission", task_id)
            return False

### [app/services/task_scheduler.py] high/bug located=true
`finally` 中无条件 `pop(task_id)` 存在“误删他人记录”的风险：只要同一个 `task_id` 曾出现新旧任务并存（重复提交、或未来加入重试后重启同名任务），先结束的那个任务会把仍存活的新任务记录从 `_running_tasks` 中删除，使调度器失去对该任务的取消能力并错误释放并发额度。应按对象身份删除。
-- suggested: finally:
                if self._running_tasks.get(task_id) is task:
                    self._running_tasks.pop(task_id, None)

### [app/services/task_scheduler.py] high/bug located=true
**`stop()` 只 cancel + clear，未等待任务真正终止**：`Task.cancel()` 只是发起取消请求，取消点要等到下一次 `await` 才生效。这里立即 `clear()` 并返回，会造成：1) `stop()` 返回时 handler 的 `finally` 清理逻辑（含 `_running_tasks.pop`）尚未执行，调用方以为已全部停止；2) 被取消任务的 `CancelledError` / 异常永远不会被取回，事件循环关闭时会打印 “Task was destroyed but it is pending” 与 “Task exception was never retrieved”，掩盖真实故障。应先收集任务再 `asyncio.gather(..., return_exceptions=True)`。另外循环变量 `task_id` 未被使用，直接用 `.values()` 更清晰。
-- suggested: self._state = SchedulerState.STOPPED
        tasks = list(self._running_tasks.values())
        self._running_tasks.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

### [app/services/task_scheduler.py] high/maintainability located=true
**配置项 `poll_interval_seconds` / `max_retries` / `task_timeout_seconds` 在任何执行路径上均未被使用**，即注释所承诺的“重试 / 超时 / 轮询调度”实际并不存在（`_run()` 里也没有 `asyncio.wait_for` 或重试循环）。后果不止是功能缺失：一个卡死的 handler 会永久占用 `_running_tasks` 槽位，由于 `submit()` 只依赖容量判断，累积后调度器会永久处于 “at capacity” 而拒绝所有新任务，且没有任何超时兜底。同时类文档声称 “priority queue / priority-based scheduling”，但代码里既无优先级队列也无轮询循环（`start()` 只是把状态置为 RUNNING），文档与实现不一致。请实现超时与重试，或删掉这些配置与相关描述。
-- suggested: # 若暂不实现轮询/重试/超时，请删除未生效的配置，避免调用方误以为存在这些保障
    # poll_interval_seconds: float = 5.0
    # max_retries: int = 3
    task_timeout_seconds: int = 3600  # 需在 _run() 中通过 asyncio.wait_for(...) 生效

### [app/services/workflow_service.py] high/bug located=true
`run_test` 直接赋值 `workload.status = TESTING`，既不校验 `workload.env`（调用方 `app/api/workloads.py::test_workload` 也没有校验），也不走 `assert_transition`。后果：ALLOWED_TRANSITIONS 里 RETIRED 是终态（`{}`）、PUBLISHED 只允许转 RETIRED/TESTING，但这里可以把 **RETIRED 的工作负载直接拉回 TESTING**，跑通后再置为 TEST_PASSED，从而让已退役工件重新进入发布链，绕过状态机与“退役即终态”的设计；对 env=production 的负载同样可被改写状态，使其从 `status==PUBLISHED` 过滤器（`retire`/`rollback` 的候选集合）中消失，污染 latest 指针判定。建议在写状态前先 `assert_transition(workload.status, WorkloadStatus.TESTING.value)` 并显式拒绝非 TEST 环境负载。
-- suggested: assert_transition(workload.status, WorkloadStatus.TESTING.value)
    if workload.env != WorkloadEnv.TEST.value:
        raise WorkloadGateError(GATE_NOT_TEST_ENV, "只有测试沙箱的工作负载可以跑测试", {"env": workload.env})
    run = WorkloadTestRun(workload_id=workload.id, triggered_by=getattr(actor, "id", None), status=TestRunStatus.RUNNING.value, started_at=datetime.now(timezone.utc))
    db.add(run)
    workloa

### [app/services/workflow_service.py] high/security located=true
门禁哈希是自证的：`report["entrypoint_hash"]` 取的是 `workload.entrypoint_hash` **字段值**，而不是本次真正送进沙箱执行的 `workload.entrypoint` 内容的哈希。于是 `publish()` 里的 `tested_hash != workload.entrypoint_hash` 实际上把同一个字段比较了两次，永远相等，无法检测“entrypoint 与 entrypoint_hash 不一致”的情况（例如任何直接改 entrypoint 而未重算 hash 的路径、数据修复、批量导入、历史脏数据）。这把“同一份 entrypoint 哈希上通过测试”这一核心不变量退化成了字段一致性检查。应记录实际执行内容的哈希，并与字段值交叉校验，不一致时 fail-closed。
-- suggested: tested_hash = hash_entrypoint(workload.entrypoint or "")
    if workload.entrypoint_hash and tested_hash != workload.entrypoint_hash:
        raise WorkloadGateError(GATE_HASH_MISMATCH, "entrypoint 与记录的 entrypoint_hash 不一致，必须重新生成哈希后重测", {"recomputed": tested_hash, "stored_hash": workload.entrypoint_hash})
    report: dict[str, Any] = {"env": WorkloadEnv.TEST.value, "entrypoint_hash": tested_hash}

### [app/services/workflow_service.py] high/security located=true
`not allowed` 分支与 docstring 声明的 fail-closed 语义相反：合约未配置（或全部为空白项）`allowed_sandbox_modes` 时，这里直接放行 `preferred`，等于“没授权 = 全授权”。调用点 `run_test` 还可能在 `workload.contract_id is None`（contract=None）时进来，此时同样无条件放行 execution 档位，调用方可通过省略/清空合约授权集合获得比显式配置更宽的执行档。作为授权判据应当 fail-closed：空集合直接拒绝，无合约时应由调用方显式选择是否跳过合约校验。
-- suggested: allowed = {str(m).strip().lower() for m in (getattr(contract, "allowed_sandbox_modes", None) or []) if str(m).strip()}
    if not allowed:
        raise CDSError(
            "合约未配置 allowed_sandbox_modes，按 fail-closed 拒绝该沙箱模式",
            code="SANDBOX_MODE_NOT_PERMITTED",
            http_status=400,
            detail={"preferred": wanted},
        )
    if allowed & SANDBOX_MODE_ALIASES[wanted

### [app/services/workflow_service.py] high/bug located=true
测试凭证校验有两个缺口：1) 没有校验 `run.workload_id == workload.id`——`test_run_id` 只是普通 UUID 列（模型里无外键约束），可以被指到**另一条工作负载**的通过记录上；2) 没有校验 `run.status == PASSED`，只看 `run.passed`。此外发布成功后既不把源 TEST_PASSED 负载置为已消费（见下一条），也没有把 `run` 标记为已使用，同一条测试记录可被反复当作发布凭证。建议这里加 `run.workload_id != workload.id or run.status != TestRunStatus.PASSED.value` 的拒绝分支。
-- suggested: res = await db.execute(select(WorkloadTestRun).where(WorkloadTestRun.id == workload.test_run_id))
    run = res.scalar_one_or_none()
    if run is None or run.workload_id != workload.id or not run.passed or run.status != TestRunStatus.PASSED.value:
        raise WorkloadGateError(GATE_TEST_NOT_PASSED, "关联的测试运行未通过或不属于该工作负载", {"test_run_id": str(workload.test_run_id)})

### [app/services/workflow_service.py] high/bug located=true
`publish` 只给源负载写 `published_at`，状态仍是 `TEST_PASSED`，也没有任何“已发布”标记。由于前置条件（env=TEST、status=TEST_PASSED、test_run passed、hash 一致）在发布后依然全部成立，同一负载可被**无限次重复发布**：每次调用都会生成一个新的生产版本、把上一版 `is_latest` 置 False 并推送 `workload.published` 事件。这既破坏了“一次测试对应一次生产定版”的语义，也让生产版本序列可被随意灌水（并放大下一条并发问题）。建议发布时 `assert_transition(workload.status, WorkloadStatus.PUBLISHED.value)` 并把源负载置为已消费状态（或在 run 上记录 consumed_by_workload_id）。
-- suggested: assert_transition(workload.status, WorkloadStatus.PUBLISHED.value)
    for old in versions:
        old.is_latest = False

    prod = Workload(

### [app/services/workflow_service.py] high/bug located=true
典型的 check-then-act 竞态：`list_production_versions`（普通 SELECT，无 `with_for_update`）读出列表后在 Python 侧算 `max(...)+1` 并批量改写 `is_latest`。两个并发 publish 请求（同一合约，FastAPI 下必然并发）会读到同一份快照，各自插入**相同 version** 且 `is_latest=True` 的行；由于没有任何唯一约束兜底，最终会出现重复版本号或多个 latest，`get_production_head`（依赖 `is_latest`）随后返回不确定结果。建议对 contract 维度加 `SELECT ... FOR UPDATE`（或 advisory lock）后重算版本，并对 `(contract_id, env, version)`、部分唯一索引 `(contract_id) WHERE env='production' AND is_latest` 加约束。
-- suggested: versions = list((await db.execute(
        select(Workload).where(Workload.env == WorkloadEnv.PRODUCTION.value).with_for_update()
        if workload.contract_id is None else
        select(Workload).where(Workload.env == WorkloadEnv.PRODUCTION.value, Workload.contract_id == workload.contract_id).with_for_update()
    )).scalars().all())
    next_version = (max((v.version for v in versions), defau

### [app/api/flow_runtime.py] medium/bug located=true
「异常」状态只由 `t5_review_status` 派生，`watermark_status` 被读出来却从未参与判定（只在响应里原样回传）。而本段注释与 not_collected 的口径说明都声明 state 是「由打包当时落在行上的两个结论派生」。

实际后果：
1) `t5 is None and watermark is not None`（只写了水印状态的历史/部分行）会落到最后的 `else`，被判为 `normal`；
2) `t5 == "passed"` 但 `watermark_status in ("not_applied", "partially_applied")` 时同样被判 `normal`，且 `state_reason=None`。

按 app/models/delivery_bundle.py:35 的词表，`not_applied` / `partially_applied` 是明确的异常结论，首页把带未水印/部分水印的交付包渲染成「正常」，与文件自身「绝不粉饰」的铁律相悖，也会误导交付审查。
-- suggested: abnormal_reasons = []
        if t5 == "partially_reviewed":
            abnormal_reasons.append("T5 出口审查为部分审查（存在未审查的二进制或空文件）")
        elif t5 == "passed_with_findings":
            abnormal_reasons.append("T5 出口审查带发现项")
        if watermark in ("not_applied", "partially_applied"):
            abnormal_reasons.append(
                "水印未生效" if watermark == "not_applied" else "水印仅部分生效"
          

### [app/api/flow_runtime.py] medium/bug located=true
`calls_today` 这一个区块里混用了两套口径且没有分别下发 caliber：
- `count` / `failures` 走 `_audit_window_counts(..., actions=CALL_ACTIONS)`，没有 `user_id` 过滤 ⇒ **全平台**今日对外调用；
- `last_called_at` 取的是 `ep_by_status`（按 `ApiEndpoint.owner_id == uid` 或合约当事方过滤）的 `max(last_called_at)` ⇒ **当前用户相关接口**的最后调用时间。

`s"scope.note"` 只在页面级说了「例外：calls_today 是全平台……」，前端照抄时很容易把 `last_called_at` 也当全平台口径渲染成「最近一次调用」，用户看到的是别人的调用时间被当成自己的（或反过来：他本人接口从不调用却显示有一个近期时间）。建议在本区块内逐字段标注 `count_scope` / `last_called_at_scope`，并把 `last_called_at` 拆成独立字段。
-- suggested: "calls_today": {
            "count": calls_count,
            "failures": calls_failures,
            "count_scope": "platform",
            "last_called_at": last_called_at,
            "last_called_at_scope": "self",
            "last_called_at_caliber": "当前主体相关 OpenAPI 接口（owner 或合约当事方）的 max(last_called_at)，非全平台",
            "window": "utc_day",
            "actions": list(CALL_ACTIONS),
     

### [app/api/flow_runtime.py] medium/performance located=true
趋势区块在首页热路径上做**串行 2×days 次**数据库往返：`days` 上限 30 ⇒ 单次请求最多 60 次 `SELECT action, count(*) ... GROUP BY action`，每次都要经过 asyncpg 往返 + Python 侧 `audit_outcome` 判定。首页是「高频只读聚合面」（本文件注释自述），这个累积延迟会直接反映为首页首屏时间，且随并发线性放大连接池压力。

同一份数据一次查询即可拿到：按 `(created_at, action)` 索引做整窗分组（`where created_at >= 窗口起点 and action.in_(DEBUG+EXEC) and user_id = uid group by action`，一次拿 `(action, created_at)` 明细桶或按小时/天表达式在 DB 侧分桶后再在 Python 里聚合），把 60 次往返压到 1 次，同时仍保留「逐日半开区间、不用 CAST(... AS DATE)」的既定口径。
-- suggested: 

### [app/api/flow_runtime.py] medium/bug located=true
`available=true` 的判据比真实创建门禁弱，会在本应 fail-closed 的场景下把「可用」渲染给用户，正是本模块要避免的问题（docstring：「绝不粉饰成可用」「措辞与 detail.reason 一一对应」）。

真实创建路径（app/api/sandbox_sessions.py:398 起 → cube_runtime_adapter.select_cube_node → _eligible_cube_nodes）要求节点同时满足：`status == online` **且** `scheduling_disabled == false` **且** `node_cubelet_endpoint(node)` 非空，另外还要过 runtime_version / 模板 READY 局域性。这里只统计 `status == ONLINE`：
- 全部 online 节点被 isolate（`scheduling_disabled=true`）或没有 cubelet endpoint 时，本端点返回 `available=true, ready_nodes>0`，用户点「启动测试沙箱」仍拿到 400 RUNTIME_UNAVAILABLE —— 「点击之前先如实告知」失效；
- 同理，W14 的 `health_state=stale`（心跳超 NODE_STALE_SECONDS）节点也被计入 ready。

建议复用 `_eligible_cube_nodes`（或至少把 `SandboxNode.scheduling_disabled == False` 与 cubelet endpoint 判据纳入 ready 统计），并把不可调度的 online 节点数单独下发（如 `online_but_unschedulable`），不要合并进 ready。
-- suggested: 

### [app/api/flow_runtime.py] medium/bug located=true
两个问题：
1) **只读端点带写副作用**：`sandbox_manager.get_tenant_quota`（app/services/sandbox_manager.py:162-166）不是纯读——`if user_id not in _tenant_quotas: _tenant_quotas[user_id] = TENANT_DEFAULTS.copy()`。首页是可轮询的只读面（本文件与 app/api/quota.py 的既定契约），这里每来一个新用户就在进程内全局字典里插一条且永不淘汰，形成随访问用户数线性增长的常驻内存；同时把「后端其实没有该租户账本」这一事实悄悄变成「有默认账本」。
2) **口径失真**：`quota_source` 声明「Redis 镜像；重启后由会话重算」，但本行只读进程内字典。Redis 缺失/未 restore 时返回的是 `TENANT_DEFAULTS.max_memory_mb=8192`，会被当成「管理员配置的租户配额」展示，正好踩中本文件「不把未采集当已配置」的铁律。建议走只读访问器（`_tenant_quotas.get(uid)`），miss 时返回 `quota_mb=None` 并计入 `not_collected`，而不是伪造默认配额。
-- suggested: 

### [app/services/task_code_security.py] medium/security located=true
`base64.b64decode` 未传 `validate=True`：默认会静默丢弃非 base64 字母表字符（包括 `:`、`-`、换行、上一步切分错误残留的字符），把被篡改/损坏的字符串解成一段“看起来合法”的错误密文，错误被推迟到 GCM tag 校验甚至更远处才暴露，削弱了完整性检测的及时性与可诊断性。另外 `encoded.encode("ascii")` 在遇到非 ASCII（如上游存入 Unicode 垃圾数据）时抛 `UnicodeEncodeError`，属于未被语义化的底层异常。

建议：
```python
    try:
        ciphertext = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("Corrupted task code ciphertext: invalid base64 payload") from exc
```
-- suggested: 

### [app/services/task_code_security.py] medium/security located=true
「无 `kms:v1:` 前缀即视为历史明文」的降级路径存在完整性/绕过风险，建议显式约束：
1. 无法区分「真正的 legacy 明文」与「前缀被截断/大小写被改写/被攻击者构造的密文」，任何不匹配前缀的值都会被原样当作任务源码返回并可能被沙箱执行，等于给写入 DB 的一方提供了「以明文形式注入待执行代码」的通道，且完全没有日志/审计痕迹。
2. 反过来，用户提交的任务源码本身若以 `kms:v1:` 开头，加密后仍带合法前缀没问题，但在某些降级/未加密写入路径下该明文会被当成密文解析并在 `split`/base64 处抛异常。

建议：将明文降级限定在明确的迁移开关或历史数据版本字段内（如 `settings.LEGACY_PLAINTEXT_TASK_CODE_ALLOWED`），命中时 `logger.warning` 记录以便审计与收敛，开关关闭时 fail-closed 抛出明确异常，而不是无条件返回。
-- suggested: 

### [app/services/task_code_security.py] medium/performance located=true
每次加密都会派生一个新 DEK，但这些 DEK 从来不会被释放，造成内存持续增长（资源泄漏）。`kms_service.generate_data_key()` 会把 `key_id -> wrapped DEK` 写入进程内的 `self._wrapped_keys` 字典（kms_service.py 第 87 行），而本模块没有任何地方调用 `destroy_key`/清理。由于这里是在“逐任务写入 DB”的路径上，每创建一个任务就新增一条永久驻留的条目，长期运行的服务会无界增长；同时这些 DEK 仅存于内存，与上面的“重启后密文不可解”问题属于同一根因。

建议：改为每个 `scope`（或每个租户/任务集）复用固定密钥，并把 wrapped DEK 持久化后按需 `import_wrapped`；或引入带 TTL/LRU 上限的密钥缓存，与 `cleanup_expired_distributions` 类似的回收机制。
-- suggested: 

### [app/services/task_scheduler.py] medium/bug located=true
`submit()` 没有校验调度器状态：`stop()` 之后仍可继续提交任务，而 `stop()` 已经执行完毕，这些任务不会被任何机制取消，会带着 handler 一直跑到结束（甚至跨越应用优雅关闭/事件循环关闭，产生 “Task was destroyed but it is pending”）。在测试用例（fixture 启停调度器）与关闭流程中尤其容易触发。建议在容量检查前先拒绝非 RUNNING 状态的提交。
-- suggested: if self._state != SchedulerState.RUNNING:
            logger.warning("Scheduler is %s, reject task %s", self._state.value, task_id)
            return False

        handler = self._task_handlers.get(task_type)
        if not handler:
            logger.error("No handler registered for task type: %s", task_type)
            return False

### [app/services/task_scheduler.py] medium/bug located=true
异常处理存在两个问题：1) 只记录 `str(e)`，丢失 traceback，后台任务（异步、无调用方可见栈）排障时几乎无法定位；2) 失败后被完全吞掉，既不按 `max_retries` 重试，也没有把失败状态回写给 PipelineTask，`submit()` 早已返回 `True`，调用方无法感知任务实际失败。建议记录 `exc_info=True` 并按设计做重试/状态回写。
-- suggested: except Exception:
                logger.exception("Task %s failed", task_id)
                # TODO: 按 self.config.max_retries 重试，并将失败状态回写到 PipelineTask

### [app/services/workflow_service.py] medium/security located=true
`output_blocked` 缺失即被视为“未拦截”（fail-open）。上一行 `exit_code` 的写法已经承认 `exec_res` 结构不稳定（非 dict 时给 -1），因此当 `execute_in_sandbox` 返回结构精简/字段改名/返回 None 时，`output_policy_clear` 会在**完全没有证据**的情况下判 passed=True；配合 `entrypoint_executed` 只看 exit_code，门禁可能在输出策略未被执行的情况下放行。建议显式区分“未拦截”和“未知”，字段缺失时判失败或抛错。
-- suggested: if not isinstance(exec_res, dict) or "output_blocked" not in exec_res:
            raise ValueError("执行结果缺少 output_blocked 字段，无法确认输出策略，按未通过处理")
        blocked = bool(exec_res.get("output_blocked"))

### [app/services/workflow_service.py] medium/bug located=true
`rollback` 与 `retire` 对 `is_latest` 的改写同样是先读后写的非原子操作：`list_production_versions` 无行锁，若与并发 publish 或并发 rollback 交叉，可能出现两条 `is_latest=True`（本函数把 target 置 True 时不知道另一事务刚插入的新 latest，反之亦然），或 target 已被并发 retire 仍被指向。建议同样以 `with_for_update()` 读取版本列表，或用单条条件 UPDATE 完成指针切换后再回读校验。
-- suggested: candidates.sort(key=lambda w: w.version, reverse=True)
    target = candidates[0]
    await db.execute(
        update(Workload)
        .where(Workload.env == WorkloadEnv.PRODUCTION.value, Workload.contract_id == workload.contract_id)
        .values(is_latest=(Workload.id == target.id))
    )

### [app/api/flow_runtime.py] low/maintainability located=true
常量与实际角色体系/实际门禁不一致：
- `UserRole`（app/models/user.py:11-16）只有 `data_provider/buyer/operator/regulator/admin`，`"platform_admin"` 永不命中，是死值；
- 注释声称「与 app/api/audit.py:186 的 require_roles 同一组」，但那边是 `require_roles(OPERATOR, REGULATOR, ADMIN)`，这里多出 `platform_admin`，属于会漂移的手工副本（同文件里 `CONCURRENT_STATUSES` 之类副本都有显式漂移说明，这里反而没有）。

建议直接用 `UserRole.ADMIN/OPERATOR/REGULATOR` 组合构造，避免拼字符串与角色表脱钩。
-- suggested: PLATFORM_AUDIT_ROLES = (UserRole.ADMIN.value, UserRole.OPERATOR.value, UserRole.REGULATOR.value)

### [app/services/task_scheduler.py] low/maintainability located=true
`datetime`、`timezone`、`field` 导入后从未使用，属于死代码。结合上面 `poll_interval_seconds`/`max_retries` 等配置同样未被使用，很可能原本计划的“带时间戳的任务轮询/重试调度”逻辑被遗漏了，建议一并确认是否有代码缺失。
-- suggested: from dataclasses import dataclass

### [app/services/task_scheduler.py] low/maintainability located=true
`callable` 是内置函数而不是类型，被用作泛型参数/形参注解会让类型检查器（mypy/pyright）报错或退化，也不具备任何约束作用。应使用 `typing.Callable`（可进一步写明签名 `Callable[[str, dict], Awaitable[None]]`）。同样的问题也存在于 `register_handler(self, task_type: str, handler: callable)`。
-- suggested: self._task_handlers: dict[str, Callable[[str, dict], Awaitable[None]]] = {}

### [app/services/workflow_service.py] low/bug located=true
`allowed` 只做了 `strip()`，没有做大小写归一，而候选是大小写敏感的字面量 `"l2c"` / `"L3"`。合约里写 `"L2C"`、`"l3"`、`" l3 "` 之类常见写法都匹配不上，于是**合约明明放行了 L3 却静默回落到 l2c**（或放行 l2c 却匹配失败），运维侧只能看到运行面选档与合约不一致且没有任何日志。建议 `lv.strip().lower()` 并统一用小写候选（`"l2c"`, `"l3"`）比较；顺带说明：`not allowed` 时返回 l2c 是“无合约约束”的默认行为，与 `resolve_sandbox_mode` 的 fail-closed 取向不一致，建议在注释中明确该差异是有意的。
-- suggested: allowed = {lv.strip().lower() for lv in ((getattr(contract, "allowed_sandbox_levels", "") or "")).split(",") if lv.strip()}
    for level in ("l2c", "l3"):
        if not allowed or level in allowed:
            return level

### [app/services/workflow_service.py] low/bug located=true
`published_at` 可为 NULL（模型中 nullable=True，且本函数只按 `env + is_latest` 过滤，不校验状态），PostgreSQL 下 `DESC` 默认排序是 NULLS FIRST，因此任何 `published_at` 缺失的生产负载都会被当成 head 排在最前，使 `previous`/head 判定失真。建议加 `.nullslast()`（并按 `WorkloadStatus.PUBLISHED` 过滤）。
-- suggested: res = await db.execute(stmt.order_by(Workload.published_at.desc().nullslast()))
    return res.scalars().first()
