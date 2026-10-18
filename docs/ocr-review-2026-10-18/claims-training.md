### [app/services/training_audit.py] high/bug located=true
`record()` 的 "读取链尾 prev_hash → 计算哈希 → append" 是一个复合的检查-再-更新操作，而 `training_audit` 是模块级单例（文件末尾）。在 Web 服务多线程/多协程并发写入时，两个并发 `record()` 可能读到同一个 `prev_hash`，随后先后 append，导致后一条记录的 `prev_hash` 指向前一条而非真正的链尾，`verify_chain()` 从此永久返回 False；同时 `_job_entries` 与 `_entries` 的追加顺序也可能交错，出现两个列表下标不一致。建议在 `__init__` 中加入 `self._lock = threading.Lock()`，并在 `record()` 中用 `with self._lock:` 包裹从取 prev_hash 到两处 append 的整个临界区（`verify_chain`、`get_*` 若允许并发读也应持锁或取快照）。
-- suggested: with self._lock:  # 必须覆盖 prev_hash 读取 → 计算哈希 → append 的整个临界区
            prev_hash = self._entries[-1].entry_hash if self._entries else "0" * 64

### [app/services/training_audit.py] high/security located=true
哈希输入未包含 `actor`，但 `actor` 是审计日志中最关键的追责字段。攻击者（或任何能改内存/持久化副本的代码）把 `actor` 改成他人后，`verify_chain()` 重算的哈希输入完全不含 actor，校验依然通过，与文件开头 "immutable / tamper-evident" 的设计目标矛盾。此外 `hash_input` 用 `:` 直接拼接变长字段 `job_id`，若 job_id 可含 `:`（外部传入的标识），可以构造出与另一组字段完全相同的哈希输入（字段边界歧义）。建议：把 `actor` 纳入哈希，并改用定长/带长度前缀或 canonical JSON 编码消除边界歧义。
-- suggested: hash_input = _stable_json({
            "entry_id": entry_id,
            "event_type": event_type.value,
            "job_id": job_id,
            "actor": actor,
            "timestamp": entry.timestamp.isoformat(),
            "prev_hash": prev_hash,
            "details": details or {},
        })

### [app/services/training_config_validator.py] high/bug located=true
参数边界校验产生 ERROR 后没有提前返回，仍会继续执行 `_check_resources`。该函数直接对 `batch_size`、`max_seq_length`、`gradient_accumulation_steps` 做乘法和数值比较：若这些字段是非数字类型（例如 `"batch_size": "16"`、`null`），`effective_batch = batch_size * gradient_accumulation` 或 `effective_batch > 1024` 会抛出 `TypeError`（str*int 得到 str 后再与 int 比较），使校验器本身崩溃而不是返回结构化的校验结果。同理，`dp_config` 若为非 dict 也会在其校验中抛异常。建议在进入资源预检前增加 `if result.errors > 0: return result`，或在 `_check_resources` 内对每个值做 `isinstance(..., (int, float))` 守卫。
-- suggested: # Validate DP-SGD config if present
        dp_config = config.get("dp_config")
        if dp_config:
            self._validate_dp_config(dp_config, result)

        # Bail out before resource checks if any hard error was found
        if result.errors > 0:
            return result

        # Resource pre-checks
        self._check_resources(config, result)

### [app/services/training_config_validator.py] high/security located=true
模块 docstring 把架构白名单列为“Security constraints”，但此处实现有两处失效：
1) 子串匹配过于宽松：任何包含允许词的架构名都会被判定为合法（如 `"evil-bert-backdoor"`、`"gpt2-not-audit"` 都能通过 `a in arch`），白名单形同虚设；反过来 `"deepset/roberta-base"` 这类带命名空间的模型名取到的是 `deepset`，会被误报。
2) 只调用 `add_warning`，`result.valid` 仍为 True，非白名单架构照样可以执行，安全约束无法阻断。
另外 `model_name` 未做类型校验，若配置里是数字/None 之外的非字符串（如 JSON 中写成数字），`model_name.split` 会抛 `AttributeError`。
建议：先 `isinstance(model_name, str)` 校验，再按完整架构 token（如取 `-` 前缀或整段名字）做精确匹配，并对未命中使用 `add_error` 阻断执行。
-- suggested: arch = model_name.split("/")[-1].split("-")[0].lower()
            if arch not in ALLOWED_ARCHITECTURES:
                result.add_error("model_name", f"Architecture '{arch}' not in whitelist", model_name)

### [app/services/training_config_validator.py] high/bug located=true
`dp_config` 只被调用方用真值判断（`if dp_config:`），并未确认是 dict。若上游传入非 dict 的真值（如字符串 `"dp"`、列表 `["noise_multiplier"]`），`param in dp_config` 可能不报错，但随后 `dp_config.get("delta", ...)` 会抛 `AttributeError: 'list'/'str' object has no attribute 'get'`，校验器崩溃；列表场景下 `"noise_multiplier" in [...]` 为 True 后 `dp_config[param]` 还会触发 `TypeError: list indices must be integers`。应先做类型校验并以 ERROR 返回。
-- suggested: def _validate_dp_config(self, dp_config: dict, result: ValidationResult):
        """Validate DP-SGD specific parameters."""
        if not isinstance(dp_config, dict):
            result.add_error("dp_config", "Must be a mapping", str(dp_config))
            return
        for param, (min_val, max_val) in DP_BOUNDS.items():
            if param in dp_config:

### [app/services/training_config_validator.py] high/security located=true
隐私保证存在两个问题：
1) `DP_BOUNDS["noise_multiplier"] = (0.0, 100.0)` 允许噪声为 0，且噪声过低只产生 WARNING（`valid` 仍为 True），意味着 `noise_multiplier=0` 的“DP-SGD”配置可以直接执行，实际不提供任何差分隐私保证，与模块宣称的安全约束相悖。建议 `noise_multiplier <= 0`（或低于明确的最小隐私预算阈值）时用 `add_error` 阻断。
2) `delta` 取值后从未被使用（死代码），注释声明的 “delta should be much less than 1/n” 检查并未实现，且 `DP_BOUNDS["delta"] = (1e-10, 1.0)` 允许 `delta = 1.0`，等同于无隐私约束。应实现实际校验，或删除无用赋值以免误导。
-- suggested: noise = dp_config.get("noise_multiplier", 1.0)
        if isinstance(noise, (int, float)) and not isinstance(noise, bool):
            if noise <= 0:
                result.add_error(
                    "dp_config.noise_multiplier",
                    "Zero/negative noise provides no differential privacy",
                    str(noise), "> 0",
                )
            elif noise < 0.5:
   

### [app/services/training_pipeline.py] high/security located=true
`scrub_pii` 把命中的 PII 原文完整放进 `findings[].span` 并随 `PIIResult` 一起返回。这与模块文档声明的“PII 脱敏后才可输出 / 原始数据不出沙箱”的安全约束直接冲突：`PIIResult` 是流水线阶段的返回值，很容易被写入 `PipelineResult.metrics`、审计日志或结果 JSONL，从而把身份证号、手机号、银行卡号、邮箱原文二次泄露到沙箱之外，且这类泄露往往比脱敏漏检更隐蔽（脱敏成功反而把原文抄进了元数据）。建议只保留定位/统计所需的最小信息（类型、偏移、掩码后的片段或不可逆哈希）。
-- suggested: matched = match.group()
                findings.append({
                    "type": pii_type,
                    "offset": match.start(),
                    "length": len(matched),
                    "span_hash": hashlib.sha256(matched.encode("utf-8")).hexdigest(),
                    "replacement": _PII_REPLACEMENTS[pii_type],
                })

### [app/services/unstructured_pipeline.py] critical/bug located=true
重试成功后仍被判定为失败：循环内只在 `last_error is None` 时才 `break`，若第 1 次失败、第 2 次成功，则不会跳出循环，最后一次迭代结束时 `last_error` 仍是第 1 次的错误值，函数在 `if last_error is not None` 处直接返回 success=False，后续的输出解析/产物加密/输出审查全部不执行。同时成功的 attempt 也不会进入清理与审查流程。建议用显式标志位（如 `succeeded`）或把成功分支改为记录后 break，并去掉对 `last_error` 的双重语义。
-- suggested: 

### [app/services/unstructured_pipeline.py] high/security located=true
沙箱执行未指定超时：`timeout` 参数未传，BwrapAdapter.execute 会读取 workspace 下的 `.security_config.json`；本流程自己 mkdir 出工作区、从未调用 `provision()`，因此没有该配置文件，只能拿到 SandboxSecurityConfig 的默认超时（通常 120s）。而 OCR/ASR/视频抽帧这类任务（ffmpeg、whisper、pytesseract）耗时极不确定，且重试最多 6 次会串行放大总耗时，任务运行在事件循环内会长时间占住请求。另外缺少 `task.options.get("timeout")` 之类的外部预算控制，也缺少对取消（CancelledError）的处理。建议显式传入超时并限制总执行预算。
-- suggested: 

### [app/services/unstructured_pipeline.py] high/security located=true
工作区生命周期缺失：本文件自行创建 `workspace_root/<task_id>`，既没有走 `BwrapAdapter.provision()`（因此没有租户/用户隔离目录层级，也没有 workspace DEK 加密与 `.security_config.json`），也从未调用 `terminate()`/清理逻辑。结果是输入文件明文副本、OCR/ASR 全文、脱敏前的 DICOM、视频抽帧帧图等敏感数据永久残留在 /tmp 下，随任务无限增长（磁盘耗尽），且进程异常退出也不会被回收，与本文件顶部“输出加密入对象存储、只做安全销毁”的设计意图相悖。建议在 finally 中调用 `self._adapter.terminate(...)` 或显式安全擦除并删除工作区，同时把用户目录纳入工作区路径。
-- suggested: 

### [app/services/unstructured_pipeline.py] high/security located=true
DICOM 匿名化不完整且失败被静默吞掉：1) 只删除顶层模块的 SENSITIVE_TAGS，嵌套序列（如 RequestAttributesSequence 内的 ReferringPhysicianName）、私有 tag、曲线/覆盖注释（DevicePrivateTags 等）、以及像素数据里的烧录文字（burned-in annotation）都不会被清除；2) `save_as` 不会移除 SOPInstanceUID / SeriesInstanceUID / StudyInstanceUID 等可关联标识符，脱敏后文件仍可与原始数据重新关联；3) 异常分支只置 `partial_success=True` 并把原始 `str(e)` 写入 result.json，而该文件随后会被 `_collect_review_text` 读取并作为产物加密上传，失败文件名（常含 PatientID）与错误细节会一路外泄。建议在失败或存在未处理文件时直接失败该任务，并使用 pydicom 的 `clean_pixel_data`/`clean_structuring_attributes` 与 UIDs 重写。
-- suggested: 

### [app/services/unstructured_pipeline.py] high/security located=true
`input_path` 未做任何校验/约束：既不验证是绝对路径、也不限制在指定上传根目录内，就直接 `shutil.copy2/copytree` 拷入工作区。若该参数来自请求体（本模块的任务就是处理用户上传数据），则调用方可指定任意主机路径（如 `/etc/passwd`、其他租户上传目录、`~/.ssh/*`），内容会被拷进工作区、被沙箱脚本读取，再经 `_persist_artifacts` 加密上传对象存储并进入 `_collect_review_text` 外流，形成任意文件读取/跨租户数据泄露。建议在 `submit_task` 阶段就 `Path(input_path).resolve()` 后校验它位于声明的上传目录之下（同 `_persist_artifacts` 里的 containment 做法），并拒绝 symlink、非普通文件/目录。
-- suggested: 

### [app/services/training_audit.py] medium/bug located=true
`details` 直接把调用方传入的 dict 引用存进 `AuditEntry`，`record()` 计算完哈希后调用方仍可继续修改该 dict。由于 `verify_chain()` 用 `entry.details` 重新计算哈希，任何后续原地修改都会让原本合法的链校验失败（误报篡改）；反之若调用方持有引用，也可先写入再篡改内容绕过预期。审计日志应做写入时刻的快照：`details=copy.deepcopy(details) if details else {}`，并用 `entry.details` 参与哈希计算，保证哈希与存储内容是同一份不可变数据。
-- suggested: details=copy.deepcopy(details) if details else {},

### [app/services/training_audit.py] medium/bug located=true
`_stable_json` 存在两个问题：1) `json.dumps` 对不可序列化对象（datetime、set、numpy/tensor、bytes 等）会抛 `TypeError`，`record()` 完全没有捕获，训练主流程中一次审计写入失败会把业务调用整体打断——审计失败不应影响训练；2) `sort_keys=True` 在 dict 同时含 int 与 str 键时同样抛 `TypeError`。建议把 `import json` 提到模块顶层（当前每次 record/verify 都走一次 import 查找），并加 `default=str` 兜底，`record()` 中对序列化失败做捕获与告警。
-- suggested: def _stable_json(d: dict) -> str:
    """Deterministic JSON string for hashing."""
    return json.dumps(
        d, sort_keys=True, separators=(",", ":"),
        default=str, ensure_ascii=False,
    )

### [app/services/training_audit.py] medium/maintainability located=true
`verify_chain()` 中的哈希输入与 `record()` 中的拼接逻辑完全重复，仅靠人工保持一致（若按建议加入 `actor` 字段，必须同时改两处，漏改一处即导致所有历史记录校验失败）。建议抽成 `def _compute_hash(entry: AuditEntry) -> str`，`record()` 与 `verify_chain()` 共用同一实现。
-- suggested: expected = _compute_hash(entry)

### [app/services/training_audit.py] medium/security located=true
哈希链仅做内部自洽校验，链头是公开的 `"0"*64`，且 `entry_hash` 是不带密钥的 SM3：删除末尾若干条记录（截断）后 `verify_chain()` 仍返回 True，无法发现“尾部事件被整体抹掉”这类最典型的审计篡改。建议额外持久化/签名一个链头锚（例如用 `crypto_service.sign` 对最新 `entry_hash` 做 SM2 签名，或用带密钥的 HMAC），并在 `verify_chain()` 中比对锚值与条目总数。
-- suggested: def verify_chain(self, expected_head_hash: str | None = None,
                     expected_count: int | None = None) -> bool:

### [app/services/training_config_validator.py] medium/bug located=true
数值类型校验存在两个漏洞：
1) `bool` 是 `int` 的子类，`learning_rate=True` / `epochs=True` 会被当作 1 通过边界检查（1.0 恰好是 learning_rate 上界），明显非法的配置被判定合法。
2) `NaN` 与上下界的比较均为 False，`value < min_val or value > max_val` 不会触发，`learning_rate=NaN`、`batch_size=NaN` 会被当成合法值并继续流入 `_check_resources`（NaN 参与乘法/比较同样不会报错，最终把非法配置送到训练端）。
`_validate_dp_config` 中同样的判断也受影响。建议显式排除 bool 并检查有限性。
-- suggested: if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
                    result.add_error(param, "Must be a finite number", str(value))
                elif value < min_val or value > max_val:

### [app/services/training_pipeline.py] medium/bug located=true
边界缺陷：当任一文本长度小于 n-gram 窗口（默认 n=5）时，`get_ngrams` 返回空集合，函数直接返回 0.0，即两条完全相同的短文本（如 “同意退款”、“好评”、“N/A” 这类短指令/短标签）被判定为“完全不相似”。在 `PipelineConfig.dedup_enabled=True`、`dedup_similarity_threshold=0.85` 的默认配置下，这些短样本会 100% 漏过去重，重复样本还会进一步加剧 train/validation 泄漏（见 `split_dataset`）。建议对短文本退化为精确比较。
-- suggested: if not ngrams1 or not ngrams2:
            # 文本短于 n-gram 窗口时退化为精确比较，避免完全相同的短文本被误判为不相似
            return 1.0 if text1 == text2 else 0.0

### [app/services/training_pipeline.py] medium/performance located=true
函数名与文档声称使用 MinHash 近似，但实现是对两段完整文本构建全部字符 5-gram 集合后做精确 Jaccard：既没有签名/哈希压缩，也没有 LSH 分桶。去重是 O(N²) 两两比较场景，每条文本都会重复物化一个规模最大 O(len) 的字符串集合（`get_ngrams` 还在每次调用时重建闭包函数），在 GB 级预训练语料上内存与耗时都会失控；同时调用方会误以为已获得线性近似算法而放开语料规模。建议要么按名称真正实现 MinHash 签名 + LSH 并缓存签名，要么改名（如 `jaccard_char_ngram_similarity`）并在文档中明确其仅适用于小规模/候选对已召回的场景。
-- suggested: def jaccard_char_ngram_similarity(self, text1: str, text2: str) -> float:
        """Compute exact character 5-gram Jaccard similarity (not MinHash).

### [app/services/training_pipeline.py] medium/bug located=true
未校验 `train_ratio` 就直接用于切片：传入负值时 `split_idx` 为负，`shuffled[:split_idx]` / `shuffled[split_idx:]` 会静默给出“训练集被截掉尾部、验证集反而拿到尾部若干条”的错误结果（例如 ratio=-0.1 时训练集丢失 10% 数据且与预期分布无关）；传入 >1 时验证集为空，后续 MIA/评估阶段拿到空集合可能再抛异常。`train_ratio` 来自 `PipelineConfig`，通常源自外部模板输入，属于可被误配的参数，应显式拒绝而非静默切片。
-- suggested: if not 0.0 < train_ratio < 1.0:
            raise ValueError(f"train_ratio must be within (0, 1), got {train_ratio}")
        split_idx = int(len(shuffled) * train_ratio)
        return shuffled[:split_idx], shuffled[split_idx:]

### [app/services/training_pipeline.py] medium/bug located=true
哈希排序只保证“相同记录相邻”，并不保证相同记录落在同一侧：当数据集含重复样本时，一段键完全相同的记录会相邻排列，而 `split_idx` 可能正好切在这一段中间，导致同一条样本同时出现在训练集和验证集（数据泄漏，评估指标虚高，也削弱审计复现的可信度）。本函数内部也不做任何去重，无法依赖调用方一定执行过 DEDUPLICATION 阶段（注意 PRETRAIN/SFT/RAG/MULTIMODAL 各流水线的阶段顺序并不一致）。建议在切分前按 `_stable_record_key` 去重，或以去重键为粒度分组后再切分。
-- suggested: unique: dict[str, dict] = {}
        for record in records:
            unique.setdefault(self._stable_record_key(record, split_seed), record)
        shuffled = sorted(
            unique.values(),
            key=lambda record: self._stable_record_key(record, split_seed),
        )

### [app/services/training_pipeline.py] medium/maintainability located=true
`PIPELINE_STAGES` 是类级可变 `dict[PipelineType, list[PipelineStage]]`，而 `get_stages` 直接把内部列表交还给调用方。模块底部又有全局单例 `training_pipeline`，多个调用方共享同一实例：任一调用方对返回列表做 `append/remove/sort` 或改动 `PIPELINE_STAGES[...]`，都会污染全局流水线定义，并可能在并发执行不同流水线时造成阶段序列被并发改写（安全阶段如 `PII_SCRUB`/`MIA_CHECK` 被意外移除）。建议返回副本，或用 `types.MappingProxyType` + 元组固化定义。
-- suggested: def get_stages(self, pipeline_type: PipelineType) -> list[PipelineStage]:
        """Get the ordered list of stages for a pipeline type (defensive copy)."""
        return list(self.PIPELINE_STAGES.get(pipeline_type, ()))

### [app/services/unstructured_pipeline.py] medium/bug located=true
用 `"start" in locals()` 判断变量是否已初始化不可靠：`execute_task` 是协程且函数体内多处复用 `start` 名（本作用域内它是局部变量），一旦 `self._adapter.execute(...)` 调用行本身在赋值之后抛错，`start` 已存在，逻辑尚可；但若未来在 try 之前重构代码或重命名，该判断会静默失效，且依赖 `locals()` 使静态检查失效。建议在 try 之前显式 `start: datetime | None = None`，再 `duration = ... if start else 0`。
-- suggested: 

### [app/services/unstructured_pipeline.py] medium/bug located=true
直接信任沙箱内脚本产出的 result.json，未做类型校验：脚本运行在沙箱内、处理的是外部不可信文件，若某次脚本被改写或 result.json 被写成 list/字符串（或 `files` 元素非 dict），后续 `output["output_review"].get(...)`、`_redact_manifest_for_review` 里的 `dict(output)`、`_collect_review_text` 都会抛出未捕获的 TypeError/AttributeError，绕过这里所有 try/except 分支，直接以未处理异常冒泡给调用方（此时任务 status 仍停留在 running，状态与实际不一致）。建议解析后校验顶层必须是 dict 并对字段做白名单/类型收敛。
-- suggested: 

### [app/services/unstructured_pipeline.py] medium/performance located=true
产物持久化把整个文件一次性读进内存：`output_dir.rglob("*")` 会把视频抽帧目录下的全部 JPEG（以及大体积 sanitize 后的 DICOM）逐个 `read_bytes()`，若 `storage_service.upload` 接收 bytes，则峰值内存等于最大单文件大小，多个任务并发时极易 OOM。建议约定流式/文件路径上传接口，或对单文件大小设上限并拒绝超限产物。
-- suggested: 

### [app/services/unstructured_pipeline.py] medium/bug located=true
字符数与字节数单位混用导致预算计算错误：`remaining` 先按 UTF-8 字节初始化，随后 `remaining -= len(snippet_bytes)`（字节），但 `remaining <= 0` 之后循环仍会先执行 `[:remaining]` 的字符切片；当 remaining 变为负数时，`text.encode(...)[:negative]` 会截掉末尾若干字节，且 `if remaining <= 0: break` 只在追加之后判断，最终拼接文本可能超过 max_bytes。另外负索引切片是“静默丢尾部”，与截断语义不一致。建议改为逐块用字节缓冲裁剪并在越界前判断。
-- suggested: 

### [app/services/unstructured_pipeline.py] medium/bug located=true
`fps = int(num) / int(den)` 存在两处隐患：1) `r_frame_rate` 可能为 "0/0" 或非 "a/b" 格式（容器异常时常见），前者触发 ZeroDivisionError、后者触发 unpack ValueError；2) 该异常会被外层 `except Exception` 吞掉，导致连已经成功解析出的 `file_result` / `total_duration_sec` 都不再 append，整个文件的元数据结果被静默丢弃，最终 result.json 里只剩一条 error。建议单独解析 fps（try 包裹 + 校验 den != 0），解析失败时保留元数据结果，只跳过抽帧。
-- suggested: 

### [app/services/unstructured_pipeline.py] medium/bug located=true
document 分支存在两处遗漏：1) `.doc`（二进制 OLE 格式）走 `from docx import Document`，python-docx 只能读 .docx，必然抛异常并写入 error，但扩展名白名单却把 .doc 列为受支持类型；2) 末尾 `elif ext in (...)` 之后没有 else 分支，任何不在 pdf/docx/doc/txt/md/csv 白名单内的文件（如 .xlsx、.pptx、.rtf）会被静默跳过，既不记入 `results["files"]`，也不标记 partial_success，调用方看到的是一个“成功但空结果”，无法区分“没有文件”与“文件不被支持”。建议补 else 记录 unsupported，并把 .doc 单独走 antiword/libreoffice 或明确拒绝。
-- suggested: 

### [app/services/unstructured_pipeline.py] medium/maintainability located=true
模块级单例持有无界可变状态：`_tasks` 只增不减（`submit_task` 写入后无任何淘汰/清理），长驻服务中会持续泄漏 PipelineTask（含 stage_status/attempts/artifacts 全量元数据）；同时 `execute_task` 会重置并复用同一个 PipelineTask 对象，多个请求并发执行同一 task_id 时会互相覆盖 status/attempts/artifacts，且整个 dict 无任何锁保护。建议对任务表加容量上限/TTL 与清理接口，并对同一 task 的执行加互斥或禁止重入。
-- suggested: 

### [app/services/unstructured_pipeline.py] medium/performance located=true
在 `async def execute_task` 里直接执行大量同步阻塞 I/O：`shutil.copy2` / `shutil.copytree`（视频、DICOM 目录可达 GB 级）会长时间阻塞事件循环，使其他并发请求/健康检查全部停滞；同样地，`_persist_artifacts` 中的 `read_bytes()` 与同步的 `storage_service.upload(...)` 也是逐个产物串行网络上传，全部在循环上执行。建议用 `await asyncio.to_thread(...)` 包裹拷贝、加密上传与产物遍历，或改用异步存储客户端。
-- suggested: 

### [app/services/unstructured_pipeline.py] medium/bug located=true
task_id 只取 UUID4 前 8 个十六进制字符（空间仅 16^8 ≈ 4.3e9），且 `_tasks` 与工作区目录均以它为唯一键：按生日 paradox，累计数万任务后碰撞概率可达干分比以上。一旦碰撞，`self._tasks[task_id] = task` 会覆盖先前任务记录，且两个任务共用 `workspace_root/<task_id>` 工作区（_clean_output_dir 还会删掉对方产物），造成跨任务/跨用户产物混淆与数据泄露。建议直接使用完整 uuid4（或 secrets.token_urlsafe），并在写入前断言 key 未占用。
-- suggested: 

### [app/services/training_audit.py] low/performance located=true
模块级单例的 `_entries` / `_job_entries` 只增不减，长驻服务中每个训练事件（含每次 checkpoint 保存）都会永久驻留内存，属于无界增长；`get_all_entries()` / `get_job_history()` 每次调用还会整体复制一份列表。建议增加容量上限或按时间/条数归档裁剪，并为全量查询提供分页或只读视图（`types.MappingProxyType` / 生成器），同时注意返回的 `AuditEntry` 仍是可变对象，与 "Immutable" 的类注释不符。
-- suggested: 

### [app/services/training_audit.py] low/performance located=true
向 `logging` 传预格式化的 f-string 会在日志级别被禁用时仍然执行字符串拼接，失去惰性格式化收益（审计日志调用频繁）。应改用 `%s` 占位符由 logging 惰性渲染。
-- suggested: logger.info("Audit: %s job=%s actor=%s id=%s", event_type.value, job_id, actor, entry_id)

### [app/services/training_audit.py] low/bug located=true
`entry_id` 只取 SM3 前 16 个 hex 字符（64 bit），且输入仅由 job_id + event_type + 时间戳组成，存在两个问题：1) 同一 job 在同一时刻高频重复同类事件（如批量 checkpoint 保存）时可能产生重复 entry_id，破坏审计条目唯一标识；2) `entry_id` 里用的 `datetime.now()` 与下方 `entry.timestamp` 是两次独立取值，存在微秒级不一致，导致根据 entry_id 反推时间戳与记录值对不上，不利审计溯源。建议改为 `uuid4().hex[:16]` 或先取一次 `now = datetime.now(timezone.utc)` 共用于 entry_id 与 timestamp。
-- suggested: now = datetime.now(timezone.utc)
        entry_id = uuid.uuid4().hex[:16]

### [app/services/training_config_validator.py] low/maintainability located=true
`add_error` / `add_warning` 的参数名 `field` 遮蔽了模块顶部从 `dataclasses` 导入的 `field`。当前方法体内未使用 `field(...)` 所以尚可运行，但一旦后续需要在这些方法中调用 dataclass 的 `field`（或做反射类操作）就会得到难以察觉的 `TypeError`。建议改名为 `field_name`（与 `validate()` 中的用法保持一致）。
-- suggested: def add_error(self, field_name: str, message: str, current: str = "", expected: str = ""):
        self.issues.append(ValidationIssue(field_name, ValidationSeverity.ERROR, message, current, expected))

### [app/services/training_config_validator.py] low/maintainability located=true
`logging` 导入与 `logger` 变量在本文件中从未被引用（全文没有任何日志输出），属于死代码。如果确实不需要日志，建议删除这两行；否则应在关键失败路径（如非 dict 的 `dp_config`、非法类型参数）用 `logger.warning("...", ...)` 延迟格式化方式输出，便于定位上游脏配置来源。
-- suggested: from dataclasses import dataclass, field
from enum import Enum

### [app/services/training_pipeline.py] low/bug located=true
`record` 来自外部训练数据（JSON/JSONL 解析结果），字段值可能显式为 `null`。此时 `record.get("instruction", "")` 返回 `None` 而不是默认值 `""`，`None + record.get("output", "")` 会抛出 `TypeError: unsupported operand type(s)`，一条脏数据即可中断整个 QUALITY_FILTER 阶段（且 `quality_min_score` 依赖该分数，异常前还会先执行 `+` 再判空）。建议统一做一次类型归一。
-- suggested: text = record.get("text") or "".join(
            str(record.get(k) or "") for k in ("instruction", "output")
        )

### [app/services/training_pipeline.py] low/documentation located=true
`tokenizer` 参数声明后从未被读取，任何取值都会走同一套字符启发式，调用方会误以为切换 tokenizer 能得到不同的 token 估算（`PipelineConfig.tokenizer` 同理会被传进来产生误导）。同时文档只说明中文按 ~1.5 字符/token，实际非中文字符按 /4 计算，估算口径与注释不一致。建议移除未使用参数，或在文档中写清中英文各自的换算系数。
-- suggested: def estimate_tokens(self, text: str) -> int:
        """Estimate token count: ~1.5 Chinese chars/token, ~4 other chars/token."""

### [app/services/unstructured_pipeline.py] low/maintainability located=true
`pipeline_type` 参数从未被使用（调用点传入的也只是 `task.task_type` 自身），属于死参数；同时 5 个分支只按 `task_type` 转发，函数本身是纯分发。建议移除该参数，或用其表达真实语义（如“脱敏/全量”两种模式），否则容易让读者误以为存在独立的 pipeline 变体。另外此处 `input_name` 也未参与脚本内容，脚本硬编码扫描整个 /workspace/input。
-- suggested: 

### [app/services/unstructured_pipeline.py] low/style located=true
属性访问与调用之间多了一个空格（`results["files"] .append(...)`），虽语法合法但与同文件其余写法不一致，建议修正以保持可读性。
-- suggested: 

### [app/services/unstructured_pipeline.py] low/maintainability located=true
`total_start` 赋值后从未被读取（死变量）：总耗时实际靠逐次累加的 `execution_duration` 计算，该变量造成歧义（看起来像用于总时长统计）。建议删除，或改用 `int((datetime.now(timezone.utc) - total_start).total_seconds() * 1000)` 作为包含重试/加密/审查的端到端耗时，以免 duration_ms 少计了非沙箱阶段。
-- suggested: 

### [app/services/unstructured_pipeline.py] low/bug located=true
中文结果写入依赖进程默认编码：OCR/ASR 脚本显式使用 `lang="chi_sim+eng"`/`language="zh"`，产出必然含非 ASCII 文本，但 `out_file.write_text(text)` 与 `open(OUTPUT / "result.json", "w")` 都未指定 encoding，在沙箱最小化环境（LANG/LC_CTYPE 未设置或 PEP 538 强制被禁用）下会落到 ASCII 编码而抛 UnicodeEncodeError；由于 `write_text` 在 try 内，错误会被当作文件处理失败静默写入 error 条目，导致“任务成功但文本全丢”。建议统一写为 `write_text(text, encoding="utf-8")` 和 `open(..., "w", encoding="utf-8")`（本文件内 5 个生成脚本均有此问题）。
-- suggested: 
