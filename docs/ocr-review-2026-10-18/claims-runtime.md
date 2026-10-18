### [app/services/clickhouse_schema.py] high/bug located=true
`gateway_metering` 使用 `SummingMergeTree()`，但 `ORDER BY` 首列是逐请求唯一的 `timestamp`（DateTime64(3)，毫秒级）。SummingMergeTree 只有在**完整排序键完全相同**的行发生 merge 时才做汇总：这里排序键几乎永不重复，因此该表实际退化为普通 MergeTree，计量汇总语义完全失效（这是本表存在的目的）。反过来，一旦同一毫秒内出现相同 (contract_id, app_id, product_id, operation) 的记录，后台 merge 会静默丢弃原始明细行，并把非键列全部相加——`status_code` 会被累加成 400/600 这类无意义值，`request_id`/`consumer_id` 也会被任意保留一行的值覆盖。建议：明细层用普通 `MergeTree()`，再通过物化视图按 `toStartOfMinute(timestamp)` 聚合到以 (minute, contract_id, app_id, product_id, operation) 为键的 `SummingMergeTree` 汇总表；`status_code` 这类不可累加列应改用 `AggregatingMergeTree` + aggregate function 列。
-- suggested: 

### [app/services/task_circuit_breaker.py] critical/bug located=true
失败时间窗口逻辑失效（死代码）：`stats.last_failure_time` 在窗口判断之前就被赋值为 `now`，因此 `now - stats.last_failure_time` 恒为 0（实际还有浮点非负极小值），`failure_count = 0` 这条分支永远不可达。后果是 `failure_window_seconds` 配置完全无效：失败计数永不衰减，一个会话在进程整个生命周期内累计到阈值就会熔断（即使这些失败相隔数天且中间有成功）。

建议：先保存上一次失败时间再做窗口判断。
-- suggested: prev_failure_time = stats.last_failure_time
        stats.last_failure_time = now
        stats.total_failures += 1

        # Reset count if outside window
        if prev_failure_time and (now - prev_failure_time) > self.config.failure_window_seconds:
            stats.failure_count = 0

### [app/services/task_circuit_breaker.py] high/bug located=true
`record_success` 在 CLOSED 状态下不重置 `failure_count`（只有从 HALF_OPEN 恢复时才清零）。结合上面窗口衰减失效的缺陷，一次成功执行无法“抵消”历史失败，服务恢复后仍可能在偶发失败叠加后被误熔断。标准熔断器语义是：成功即认为链路健康，应清空滑动窗口内的失败计数。
-- suggested: stats.last_success_time = time.time()
        stats.total_successes += 1
        if stats.state == CircuitState.CLOSED:
            stats.failure_count = 0

### [app/services/task_circuit_breaker.py] high/bug located=true
HALF_OPEN 状态没有“单探针”限流：此处直接放行且未记录任何 in-flight 标记，因此半开期间所有并发请求都会被判定为“允许执行”，等价于全量放行，失去半开探测保护后端的目的。更严重的是：只要其中任意一个请求成功，`record_success` 就立刻把状态切回 CLOSED 并清零失败数，而此时其余并发探针可能全部失败，导致在故障未恢复时误判为已恢复（fail-open）。

建议：增加 `half_open_inflight` 计数（或 `threading.Semaphore(1)`），半开时只放行 `max_attempts` 个探针，其余返回 False；并在 `record_success/record_failure` 中递减。同时建议为半开状态加超时保护：若探针迟迟不上报结果（任务被 kill、异常路径漏调 record_*），状态会永久停留在 HALF_OPEN 从而无限放行。
-- suggested: if stats.state == CircuitState.HALF_OPEN:
            if stats.half_open_inflight >= self.config.half_open_max_attempts:
                return False, "Circuit half-open — test slot in use"
            stats.half_open_inflight += 1
            return True, "Circuit half-open — allowing single test"

### [app/services/task_circuit_breaker.py] high/bug located=true
共享可变状态缺少并发保护。`_get_stats` 是典型的 check-then-act：`if session_id not in self._sessions` 与写入之间无原子性，多线程/多 worker 线程并发时同一会话可能创建出两个 `CircuitStats` 实例，其中一个的失败计数被静默丢弃；`can_execute` 里的 OPEN→HALF_OPEN 状态迁移与 `record_failure` 里的计数自增/状态迁移同样是非复合更新，会产生竞态（重复计数、重复 `circuit_opened_count`、多次放行半开探针）。由于文件底部导出的 `task_circuit_breaker` 是全局单例，必然被跨请求/跨线程共享。

建议：加 `threading.Lock`（或 `threading.RLock`）包裹所有读写 `_sessions` 与 `CircuitStats` 字段的方法；`setdefault` 只能解决字典插入原子性，仍无法保证状态机复合更新的原子性。
-- suggested: def _get_stats(self, session_id: str) -> CircuitStats:
        with self._lock:
            stats = self._sessions.get(session_id)
            if stats is None:
                stats = CircuitStats()
                self._sessions[session_id] = stats
            return stats

### [app/services/tee_capability.py] high/security located=true
设备可达性判断过弱，容易把「设备节点存在」误判为「硬件 TEE 可用」：
1. `Path.exists()` 只做 stat，不校验是否为字符设备，也不校验读写权限。设备节点存在但驱动未加载 / 属组权限不含当前用户时，`st_mode` 与权限都可能不满足要求，却仍会返回 `hardware_available=True`，属于 fail-open。
2. 部分路径语义与「guest 是否运行在 TEE 中」并不等价：`/dev/sev` 是主机侧 PSP 管理节点（SEV 系列主机上 ccp 驱动加载即可出现），并非 SNP guest 证据；`/dev/teec` 是通用 GP-TEE client 节点（任何 OP-TEE 平台都可能有），把它们直接归因到 `sev_snp`/`itrustee` 会产生假阳性。
3. 设备节点可被非特权命名空间/挂载伪造（bind mount 一个同名文件即可让 `exists()` 为真）。

由于该结论会作为后续「是否可在硬件 TEE 中执行代码」的判据，建议至少校验字符设备类型 + 可访问性，并优先使用更权威的来源（如 `/sys/firmware/acpi/tables/...`、CPUID/`/proc/cpuinfo` flag、或一次真实的 attestation report 生成）作为必要条件。
-- suggested: for device in spec.get("devices", ()):
            path = Path(device)
            try:
                st = path.stat()
            except OSError:
                continue
            # 仅认可「字符设备 + 可读写」的节点，避免驱动缺失/无权限导致误判
            if stat.S_ISCHR(st.st_mode) and os.access(device, os.R_OK | os.W_OK):
                evidence.append(f"device:{device}")

### [app/services/trust_evaluator.py] high/bug located=true
violations 被重复扣分：这里先按 15 分/次 把 violations 折算进 compliance_score，随后又把同一个 violations 传给 evaluate()，evaluate 内部还会再执行 `penalty = min(violations * 10, 50)` 从 compliance 上扣一次。例如 violations=4：先算成 100-60=40，再被扣 40 → 0；violations>=4 的供应商合规分一律被清零，信任等级被系统性低估（本应 SILVER/GOLD 的供应商直接掉到 NEW/SILVER）。建议二选一：要么此处保持 compliance_score=100 由 evaluate() 统一按违规次数扣分，要么此处自行折算后不再向 evaluate() 传 violations，避免同一次违规被惩罚两遍。
-- suggested: # Compliance based on violations
        compliance = max(0, 100 - violations * 15)

        return self.evaluate(
            data_quality_score=int(avg_quality),
            compliance_score=compliance,
            reputation_score=reputation,
            certifications=certifications,
        )

### [app/services/trust_evaluator.py] critical/security located=true
访问控制在异常输入下 fail open，属于安全缺陷：
1) `level_order.get(required_level, 0)` 把未知/缺失（如 None）的 required_level 当成“最低要求”，任何信任等级都会返回 True，等于配置缺失即全量放行。
2) 更严重的是 TrustLevel 为 str Enum，而 `Enum.__hash__` 基于成员名（"PLATINUM"）、`str.__eq__` 基于 value（"platinum"），因此当调用方传入普通字符串（例如从 JSON/DB 反序列化得到的 "gold"、"platinum"，或 `TrustScore.level` 被 `.value` 后回传）时，两次 get 都因哈希不同而命中默认值 0，`0 >= 0` 返回 True，直接绕过等级校验（NEW 也能访问 PLATINUM 资源）。
建议显式归一化并在无法识别时 fail closed。
-- suggested: try:
            current = level_order[TrustLevel(trust_level)]
            required = level_order[TrustLevel(required_level)]
        except (ValueError, KeyError, TypeError):
            logger.warning(
                "can_access: unrecognized trust level (have=%r required=%r), denying access",
                trust_level,
                required_level,
            )
            return False
 

### [app/services/vision_multimodal_runtime.py] critical/security located=true
隐私脱敏实际是空操作（no-op）：`FaceBlurStep.apply` 只在字节流尾部追加一个 `|cds_step=face_blur:...` 文本标记，从未对图像像素做任何模糊处理。对真实 JPEG/PNG/BMP 字节流而言，人脸区域原封不动地保留在返回值中，却被上层当作“已脱敏”数据继续训练并写入 model_path。文件头部声明的“face blur / DICOM strip / plate mosaic”安全能力在此文件中并不成立，属于隐私泄漏级别的功能缺陷；若当前为占位实现，应在返回结果中显式标记 `redaction_applied=False` 或抛出 `NotImplementedError`，而不是静默返回未脱敏数据。
-- suggested: 

### [app/services/vision_multimodal_runtime.py] high/security located=false
该正则只能匹配 `PatientName=xxx;` 这种文本形态，无法处理真实 DICOM 文件（DICOM 标签以 little-endian Group/Element 二元组 + VR + 长度编码存储，患者姓名在 `PatientName (0010,0010)` 中不会出现 `PatientName=` 字面量）。因此对真实医学影像该步骤同样返回未脱敏数据，仅追加一个 `dicom_tags_stripped` 标记，会让调用方误认为患者姓名/ID/出生日期已被清除，隐私合规风险很高。建议在 DICOM_STRIP 上对非文本载荷显式拒绝（或要求真实 DICOM 解析器），并在结果中如实反映是否真正完成脱敏。另外此处每次调用都重新 `re.escape`/隐式编译 4 条正则，可提升为类级预编译常量。
-- suggested: 

### [app/services/vision_multimodal_runtime.py] high/security located=true
`_inject_watermark` 并不向模型写入任何水印，只把 `session_id` 的 SM3 前 12 位记到日志里，却无条件 `return True`，导致 `TrainingResult.watermark_injected` 永远为 True。调用方/审计侧会据此认为模型已具备可追溯水印（溯源能力），属于虚假的安全能力声明；真实写入失败时也无法向上报错。建议实际执行水印注入并返回真实结果，失败时记录 `error`；至少应将日志中的“injected”措辞改为“watermark payload computed”，避免误导。MultimodalTrainingRuntime 中的同名实现存在完全相同的问题。
-- suggested: 

### [app/services/clickhouse_schema.py] medium/bug located=true
`validate_schema_completeness` 只校验列名/类型是否为空字符串，而 `ColumnDef` 与 `IndexDef` 都是数据类，构造时不会为空，因此该函数实际永远返回 `valid: True`，属于无效校验；同时返回字段名 `missing_columns` 语义误导——它列出的是“定义不完整的列”，而不是表中缺失的列（与 docstring 描述也不一致）。更关键的是它不会发现会生成非法 DDL 的问题：重复列名、`order_by`/`partition_key`/`ttl`/`IndexDef.expr` 引用了不存在的列。建议改为真正做引用完整性与唯一性校验（例如重复列检测、从 order_by/partition 表达式中解析标识符并比对列集合），并把返回键改为语义准确的 `invalid_columns`。
-- suggested: 

### [app/services/clickhouse_schema.py] medium/bug located=true
未知表名时 `get_columns` 返回空列表、`get_table_info` 返回空字典（`generate_create_sql` 同理返回空字符串），调用方无法区分“该表没有列”与“表名传错/未注册”。这类 schema 接口通常被建表/迁移流程调用，一旦表名拼错，DDL 会返回空字符串并被静默跳过，导致表根本没被创建却没有任何报错，问题只会在插入数据时才暴露。建议显式抛出 `KeyError`/自定义 `UnknownTableError`，或至少在返回前用模块内的 `logger` 记录 warning。
-- suggested: 

### [app/services/task_circuit_breaker.py] medium/bug located=true
直接迭代共享字典：如果有其它线程（或同一事件循环中被打断的协程链）在迭代期间通过 `can_execute`/`get_stats` 新建会话，会抛出 `RuntimeError: dictionary changed size during iteration`。该调用通常是后台巡检/监控线程在做，属于典型的遍历-修改竞态。

建议：先取快照再过滤（`list(self._sessions.items())`），并在持锁状态下生成快照。
-- suggested: with self._lock:
            snapshot = list(self._sessions.items())
        return [
            sid for sid, stats in snapshot
            if stats.state == CircuitState.OPEN
        ]

### [app/services/task_circuit_breaker.py] medium/maintenance located=true
会话状态无界增长（内存泄漏风险）：`_get_stats` 会为任意传入的 `session_id` 惰性创建条目，且 `can_execute` / `get_stats` 这类只读语义的方法也会写入 `_sessions`（隐式污染状态）；除显式 `reset()` 外没有任何 TTL/LRU 淘汰机制。作为进程级长期存活的单例（文件底部 `task_circuit_breaker`），会话数只增不减，最终造成内存持续增长，且 `get_all_open_circuits` 遍历成本随历史会话数线性上升。

建议：为条目记录 `last_access_time`，按 `failure_window_seconds` 或会话生命周期做定期清理（可复用 `recovery_timeout_seconds`），或改用 `maxsize` 受限的 LRU 结构；同时让只读方法不要在 `_sessions` 中创建条目。
-- suggested: self.config = config or CircuitConfig()
        self._sessions: dict[str, CircuitStats] = {}
        self._lock = threading.Lock()

### [app/services/tee_capability.py] medium/security located=true
`shutil.which` 完全依赖调用进程的 `PATH`（且会保留 `PATH` 中的空串条目所隐含的当前工作目录），存在两类风险：
1. 环境变量被污染或某目录可被低权限用户写入时，放置一个同名可执行文件即可让探测结果误报 `hardware_available=True`，进而影响后续「是否进入硬件执行环境」的安全决策。
2. 即便命令真实存在，也只说明「装了工具」，不等于「硬件/运行时可用」（这些包常作为依赖被安装进基础镜像），单独作为充分证据偏弱。

建议：把查找路径限定为受信任的绝对目录（显式传入 `path=` 参数，避免继承可变 `PATH`），并考虑校验文件属主/权限（不可组写、world-writable 视为不可信）；同时把命令证据降级为辅助信息，要求与设备/固件侧证据同时成立才判定可用。
-- suggested: for command in spec.get("commands", ()):
            # 限定在受信任目录中查找，避免继承可被污染的 PATH
            path = shutil.which(command, path="/usr/local/bin:/usr/bin:/sbin:/usr/sbin")
            if path:
                evidence.append(f"command:{path}")

### [app/services/tee_capability.py] medium/bug located=true
显式指定未知 provider（例如配置里把 `tdx` 写成 `tdx_guest`、`sev-snp`）时，`continue` 会跳过全部候选，最终落到下方的兜底分支，返回 `provider=<拼错的字符串>` + `hardware_available=False`，reason 还是 “No hardware TEE signal detected”。这把「输入配置错误」与「硬件确实不支持」混为一谈：调用方拿到的是看起来合理的“不支持”结论，排障时无法发现拼写错误，且返回的 `provider` 是一个不存在的取值，可能污染下游按 provider 分派的逻辑。`mode="auto"` 下静默跳过是合理的，但显式指定时应当快速失败。

建议：显式 mode 且 provider 不在 `_PROVIDERS` 中时抛出 `ValueError`（或至少返回一个可区分的 `reason`/哨兵 provider），不要复用“未检测到硬件信号”这一结论。
-- suggested: spec = self._PROVIDERS.get(provider)
            if not spec:
                if mode != "auto":
                    raise ValueError(
                        f"unknown TEE provider {mode!r}; expected 'auto' or one of "
                        f"{sorted(self._PROVIDERS)}"
                    )
                continue

### [app/services/trust_evaluator.py] medium/bug located=true
入参没有按 docstring 声明的 0-100 契约做裁剪：本方法只在最后对加权总分做 min/max，四个基础分（以及下游调整后的组件分）可以远超 100 或为负，并原样写进返回的 TrustScore（其字段注释明确标注 0-100）。evaluate_provider 里 `data_quality_score=int(avg_quality)` 也会把 >100 或 <0 的均值直接透传。另外 `data_age_days` 为负数（异常时间戳/时钟偏差）时会落入 `< 7` 分支反而给质量加分。建议在方法入口统一裁剪输入。
-- suggested: def _clamp(v: int) -> int:
            return max(0, min(100, int(v)))

        compliance_score = _clamp(compliance_score)
        data_quality_score = _clamp(data_quality_score)
        reputation_score = _clamp(reputation_score)
        security_score = _clamp(security_score)
        violations = max(0, violations)
        if data_age_days is not None:
            data_age_days = max(0, data_ag

### [app/services/trust_evaluator.py] medium/bug located=true
浮点权重 + int() 向零截断会在等级阈值处产生 off-by-one：0.30/0.20 在二进制下不可精确表示，累加结果可能得到 89.99999999999999 这类值，而 `int()` 是直接截断而非四舍五入，于是数学上应为 90 的分数被判成 89（PLATINUM 误判为 GOLD），89.6 也会被截成 89。等级阈值下游还用于 can_access 的访问判定，边界抖动会直接影响权限结论。建议改用整数权重（如 30/30/20/20）做整数运算，或显式 round 后再裁剪。
-- suggested: # WEIGHTS 建议改为整数基点: {"data_quality": 30, "compliance": 30, "reputation": 20, "security": 20}
        total = sum(scores[k] * self.WEIGHTS[k] for k in self.WEIGHTS) / 100
        total = min(100, max(0, int(round(total))))

### [app/services/vision_multimodal_runtime.py] medium/security located=true
`(?i)` 只作用于 `(plate|license_plate)` 这一组，字符类 `[A-Z0-9-]` 本身是大写敏感（内联标志的作用域到右括号即结束）。因此 `license_plate=abc123`、`plate=京A12345` 这类小写/含非 ASCII 的车牌值不会被替换，车牌号原样保留。此外 `{5,12}` 的长度上限意味着超过 12 个字符的值只会被部分替换、残留尾部字符。建议改为 `[A-Za-z0-9\u4e00-\u9fa5-]{5,}` 并把 `(?i)` 移到整体模式或去掉标志依赖，同时补充小写与超长车牌的用例。
-- suggested: 

### [app/services/vision_multimodal_runtime.py] medium/bug located=true
两个问题：1) 直接向图像字节流尾部追加 `|cds_step=...` 会破坏 JPEG/PNG 等二进制格式（IEND 之后追加垃圾字节、长度字段与实际载荷不一致），下游解码必然失败，也使“脱敏后的张量”语义不成立；2) `if marker_bytes in image_data: return image_data` 是隐式跳过——同一标记只要出现在载荷任意位置（包括未脱敏的原始数据恰好含该子串）该步骤就完全不再执行，脱敏开关被静默旁路。建议把处理审计信息放到独立的元数据结构（而非改写像素载荷），并将去重判断改为按步骤执行记录，而不是对字节流做子串包含判断。
-- suggested: 

### [app/services/vision_multimodal_runtime.py] medium/security located=true
默认配置在医疗场景下不安全：`SecureImageDataLoader.load()` 生成的样本同时包含 `PatientName`、`PatientID`、`license_plate`，而 `dicom_strip`/`license_plate_mosaic` 默认为 False，默认流水线会把患者姓名与车牌原样送进训练。`VisionTask.MEDICAL_IMAGING` 也复用同一默认值。建议对 medical_imaging 任务强制开启 dicom_strip（或按任务类型选择更保守的默认规则），避免调用方忘记显式配置时静默泄漏 PII。
-- suggested: 

### [app/services/vision_multimodal_runtime.py] medium/security located=true
`session_id` 未做任何校验/净化就拼进输出路径。若 session_id 来自外部请求或含 `../`、绝对路径、`\x00`、换行等字符，model_path 会指向 `/sandbox/output/vision_model/` 之外（如 `../../etc/...` 或以 `/` 开头的绝对路径直接覆盖前缀），造成沙箱目录穿越/污染。建议对 session_id 做白名单校验（如 `^[0-9a-fA-F-]{8,64}$`）并使用 `pathlib.PurePosixPath(...).resolve().relative_to(base)` 做包含性校验后再返回。
-- suggested: 

### [app/services/vision_multimodal_runtime.py] medium/security located=true
同上：多模态分支同样把未校验的 `session_id` 直接拼入输出路径，存在相同的路径穿越/目录污染风险，应与视觉分支共用同一个 session_id 校验与路径构造辅助函数。
-- suggested: 

### [app/services/vision_multimodal_runtime.py] medium/bug located=true
`self._batch_size` 直接作为 `range()` 的步长，但 `VisionTrainingConfig.batch_size` 没有任何 >0 约束（外部可传 0 或负数）。batch_size=0 时这里抛 `ValueError: range() arg 3 must not be zero`，异常信息完全不指向真实原因（非法配置），且发生在训练循环内部而非入口校验处。建议在 `run_training`/配置构造时显式校验 `batch_size >= 1`、`num_epochs >= 1`，并对 `num_epochs <= 0` 的情形返回带 `error` 的 `TrainingResult`（当前静默返回 total_epochs=0、final_loss=0.0 的“成功”结果，调用方无法区分未训练与训练失败）。
-- suggested: 

### [app/services/vision_multimodal_runtime.py] medium/bug located=true
多模态加载器同样直接用 `self._batch_size` 作为切片步长，`MultimodalTrainingConfig.batch_size` 无 >0 校验，batch_size<=0 时在训练循环内抛出与真实原因无关的 `ValueError`。建议与视觉分支一起在入口处做参数校验。
-- suggested: 

### [app/services/vision_multimodal_runtime.py] medium/bug located=true
`_max_text_len` 被保存但从未被读取（`load()`/`get_batches()` 均不截断文本），因此 `MultimodalTrainingConfig.max_text_len` 是无效配置：超长文本不会被截断，下游若据此假设 token 上界将得到错误行为。建议在 `load()`/`get_batches()` 中按 `max_text_len` 实际截断，或移除该参数以免误导配置方。
-- suggested: 

### [app/services/vision_multimodal_runtime.py] medium/bug located=true
这里显式配置了 `tags` 白名单，但 `_apply_pipeline_step` 对 DICOM_STRIP 分支构造的是 `DICOMStripStep()`（见下方 `return DICOMStripStep().apply(image_data)`），`step.params` 被完全忽略，实际生效的是类里硬编码的 `SENSITIVE_TAGS`（含 InstitutionName，却不含这里配置的其它 tag）。配置与执行不一致，容易让人误以为脱敏范围可按数据集调整。建议让 `DICOMStripStep.__init__` 接收 tags 参数并在此传入 `step.params.get("tags")`。
-- suggested: 

### [app/services/vision_multimodal_runtime.py] medium/bug located=true
`load()` 完全不使用 `self._product_ids`，无论传入哪些数据产品 ID（包括空列表或非法 ID），都会固定生成同一份 100 条合成样本并记录 “Loaded N images”。这意味着“从已授权数据产品加载”的授权契约实际未被执行：授权校验失败时训练仍会“成功”完成并产出模型，属于静默假成功。同时 `SecureMultimodalDataLoader` 的 `_image_product_ids`/`_text_product_ids` 也仅保存不读取。建议：对空/非法 product_ids 直接抛错或返回带 `error` 的结果，并把真实数据源接入点显式化。
-- suggested: 

### [app/services/vision_multimodal_runtime.py] medium/performance located=true
两处性能问题：1) `dataloader.get_batches()` 放在 epoch 循环内，每个 epoch 都把全量数据集重新跑一遍完整流水线（脱敏/增强步骤是确定性的，结果完全相同），造成 num_epochs 倍的重复处理；2) `_stable_unit_interval` 每个 batch 都调用一次 `crypto_service.sm3_hash`，在未安装 gmssl 加速时是纯 Python SM3 实现，开销极大，而该 jitter 值仅依赖 (epoch, total_samples) 等少量组合，可在 epoch 级预计算或改用 `hashlib.blake2b(digest_size=4)`。数据规模变大后这里会成为主要热点。
-- suggested: 

### [app/services/clickhouse_schema.py] low/maintainability located=true
`ColumnDef.comment` 字段在本文件中从未被赋值，`generate_create_sql` 生成列定义时也完全忽略了 `col.comment`（只拼接 name/dtype/default）。这意味着后续任何人填写列注释都会静默丢失，且与 docstring 宣称的“完整 schema 定义/迁移支持”不符。建议在建表 SQL 中输出 `COMMENT '...'`（注意对单引号做转义），或删除该未使用字段以免误导。
-- suggested: 

### [app/services/clickhouse_schema.py] low/maintainability located=true
`logger`（及 `logging` 导入）在本文件中定义后从未被使用，属于死代码。若确实不需要日志，应删除；若打算用于记录未知表名/校验失败等异常路径，应在下方相应的错误分支中使用。
-- suggested: 

### [app/services/task_circuit_breaker.py] low/style located=true
`field` 从 `dataclasses` 导入后在本文件中从未使用（本文件的 dataclass 字段全是不可变默认值，无需 `field(default_factory=...)`），属于无用导入，建议移除。
-- suggested: from dataclasses import dataclass

### [app/services/task_circuit_breaker.py] low/performance located=true
日志使用 f-string 预先格式化（本文件 `logger.info` / `logger.warning` 三处均如此），即使日志级别被禁用也会执行字符串拼接，浪费开销且不利于日志结构化采集。建议改为惰性参数形式：`logger.info("Circuit closed for session %s — recovery confirmed", session_id)`。
-- suggested: logger.info("Circuit closed for session %s — recovery confirmed", session_id)

### [app/services/trust_evaluator.py] low/performance located=true
日志使用 f-string 会在调用前就完成格式化（含 `', '.join(...)` 的字符串拼接），即使 INFO 级别被禁用也要付出这些开销，丧失了 logging 的惰性格式化。建议改为占位符形式，由日志框架在需要时才格式化。
-- suggested: logger.info(
            "Trust evaluation: %s (%s/100) — %s",
            level.value,
            total,
            ", ".join(factors[:3]),
        )

### [app/services/vision_multimodal_runtime.py] low/maintainability located=true
`uuid`、`datetime`、`timezone` 三个导入在本文件中从未使用（`logger`/`re`/`time`/`abc`/`dataclasses`/`enum`/`typing` 均有使用）。另外 `_stable_unit_interval` 已定义于模块顶部并被两处复用，属正常；仅这三个导入属于可删除的死代码，建议清理以免误导（`Any` 同样未被引用）。
-- suggested: 

### [app/services/vision_multimodal_runtime.py] low/performance located=true
f-string 会在调用点立即格式化，绕过了 logging 的按级别惰性求值（本文件内 `logger.info(f"Loaded ...")`、`Loaded {len(self._pairs)} ...`、`Multimodal model watermark injected: {payload}` 同此问题）。建议统一改为 `logger.info("Vision model watermark injected: %s", payload)`。另外把水印指纹写入 INFO 日志需确认其不构成可被利用的水印泄露面。
-- suggested: 
