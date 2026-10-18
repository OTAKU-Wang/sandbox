### [app/services/mia_probe.py] critical/bug
聚合风险公式存在两处缺陷：
1. **除零风险**：`loss_separation` 是无界量（loss 可任意大）。当它恰好等于 -1.0 时 `1.0 + loss_separation == 0`，直接抛 `ZeroDivisionError`。
2. **无下界钳制**：当 `loss_separation < -1`（例如全部样本都被判为疑似成员，此时 `non_member_losses` 为空 → `mean_loss_non_members = 0.0` → `loss_separation = -mean_loss`，只要平均 loss > 1 就会 < -1），该项变成负数且绝对值可远大于 `member_ratio * 1.5`，`overall_risk` 会算成负数。由于只做了 `min(1.0, ...)` 而没有 `max(0.0, ...)`，最终结果是 `risk_score` 为负、`risk_level` 被判成 `SAFE`——即“100% 样本疑似成员”却输出最低风险，属于严重误判。
-- separation_term = 1.0 / (1.0 + max(0.0, loss_separation))
        overall_risk = min(1.0, max(0.0, member_ratio * 1.5 + separation_term * 0.5))

### [app/services/mia_probe.py] high/bug
空集合用 `0.0` 作哨兵会污染 `loss_separation`，使聚合风险出现系统性假阳性/假阴性：
- **无疑似成员时**：`member_losses` 为空 → `mean_loss_members = 0.0`，于是 `loss_separation = 全体样本平均 loss`（一个正数），`overall_risk = 0.5 / (1 + mean_loss)`。若平均 loss 较小（如 0.05），即使 `member_ratio == 0`、`suspected_members == 0`，`overall_risk` 仍可达 ~0.48，被判定为 `LOW`；平均 loss 越接近 0 越接近 0.5（`MEDIUM` 边界）。零疑似成员的模型不应得到非 SAFE 评级。
- **全部疑似成员时**：`non_member_losses` 为空 → `mean_loss_non_members = 0.0` → `loss_separation` 为负，与上一条 critical 问题叠加，会把风险压成负数。
建议：某一侧集合为空时不把 `loss_separation` 计入聚合公式（视为 0），并用显式分支代替 0.0 哨兵（0.0 是合法的 loss 值，与“无数据”无法区分）。
-- mean_loss_members = statistics.mean(member_losses) if member_losses else 0.0
        mean_loss_non_members = statistics.mean(non_member_losses) if non_member_losses else 0.0
        loss_separation = (
            mean_loss_non_members - mean_loss_members
            if member_losses and non_member_

### [app/services/mia_probe.py] high/bug
`total_matched` 在参考文本循环内**逐个累加**重叠数，但同一个生成 n-gram 若出现在多个参考文本中会被重复计数，而分母 `len(gen_ngrams)` 是去重后的生成 n-gram 数。结果 `memorization_ratio` 可以远大于 1.0，且与参考文本数量成正比（同一句话在 100 份训练语料里重复出现，比例可被放大到 100），完全失去“比例”语义，调用方若据此判定记忆化风险会严重误报。
另外 `list(overlap)[:3]` 直接取集合元素，集合迭代顺序不稳定，导致 `matched_snippets` 在不同进程/运行间不可复现，且同一 snippet 会在多个参考文本中重复出现。
建议：用集合求并集后再计数，并对 snippets 去重。
-- matched_ngrams: set[tuple[str, ...]] = set()

        for ref in reference_texts:
            ref_ngrams = get_ngrams(ref, ngram_size)
            overlap = gen_ngrams & ref_ngrams
            if overlap:
                matched_ngrams |= overlap
                # Extract the matched region (sorted 

### [app/services/mia_probe.py] high/bug
`self.perplexity_threshold` 直接来自构造函数且从未校验，若调用方（或配置中心/配置文件）传入 `0`，只要样本带有正的 perplexity 就会抛 `ZeroDivisionError`，整个批量分析中断。此外该分支条件 `perplexity > 0` 与注释语义相反：perplexity 越小应代表越强的成员信号，但 `perplexity == 0`（常见的“缺失/未计算”哨兵，或极端记忆化结果）时得分被置为 `0.0`，即给了“最像成员”的样本最低分。建议校验阈值并同时明确 `perplexity <= 0` 的语义。
-- if (
            perplexity is not None
            and perplexity >= 0
            and self.perplexity_threshold > 0
        ):
            # Lower perplexity = higher membership probability
            perplexity_score = max(0, 1.0 - (perplexity / self.perplexity_threshold))
        elif perplexit

### [app/services/model_watermark.py] critical/bug
`_set_weight_value` 的第一个分支会把 numpy 数组误判成 torch 张量，导致水印写入被静默丢弃：`numpy.ndarray` 同时具备 `flatten` 和 `__setitem__`，因此会进入此分支；而 numpy 的 `flatten()` **总是返回副本**（不同于 `ravel()`），`flat[idx] = value` 只改到临时数组，原始 `model_state_dict` 完全没被修改。下方 `hasattr(tensor, "flat")` 的 numpy 分支因此成为死代码。同理，torch 非连续张量（如 `t.T`、channels_last 视图）的 `flatten()` 也返回副本，写入同样丢失。结果是 `embed_watermark` 返回 `success=True`，但 `verify_watermark` 只能读到原始权重，验证必然失败。建议按类型（而非鸭子类型属性）分派，并写后校验：numpy 用 `tensor.flat[idx] = value` 或 `tensor.reshape(-1)[idx]`，torch 用 `tensor.reshape(-1)[idx] = value`（reshape 对非连续张量同样会复制，应先 `tensor = tensor.contiguous()` 或用 `tensor.view(-1)` 前断言 `is_contiguous()`），最稳妥的做法是写完后立即 `_extract_bit` 回读断言。
-- # torch tensor: 必须保证写回原张量（flatten 对非连续张量返回副本）
        if hasattr(tensor, "numel") and hasattr(tensor, "view"):
            if not tensor.is_contiguous():
                raise ValueError("Non-contiguous tensor: call .contiguous() before embedding")
            tensor.view(-1)[idx] = value
          

### [app/services/model_watermark.py] high/performance
每次读取单个 bit 都会走 `_flatten(tensor)`：对整个张量调用 `flatten()`（torch/numpy 都会整块复制），再把**全部元素**逐个 `float()` 转成 Python list。嵌入与验证是逐 bit 调用的，总代价为 O(num_bits × 层元素数)；对一个 1e7 元素的层、64 bit，就是约 6.4e8 次元素拷贝与 `float()` 调用（对 torch 还是逐个 0-dim tensor 的 `float()`，极其缓慢）。应改为按扁平索引直接取值：numpy 用 `tensor.flat[idx]`，torch 用 `tensor.detach().reshape(-1)[idx].item()`，list 直接下标；仅在必要时才 fallback 到 `_flatten`（且每个张量只展平一次并缓存）。
-- if hasattr(tensor, "flat"):  # numpy: 视图式扁平访问，不复制
            return float(tensor.flat[idx])
        if hasattr(tensor, "detach"):  # torch
            return float(tensor.detach().reshape(-1)[idx].item())
        flat = cls._flatten(tensor)
        return flat[idx]

### [app/services/model_watermark.py] high/bug
回退路径的比特顺序与嵌入顺序不一致，会产生错误结果：`watermark_bits` 的顺序对应 `chosen` 的采样顺序（各层之间是交错的），而 `layer_positions` 是**按层分组**的字典，遍历它得到的是「先层 A 全部位置、再层 B 全部位置」。只要水印位置跨两个及以上层，抽取出的序列就与嵌入序列错位，`match_ratio` 会掉到约随机水平（≈50%），验证被误判为不通过。注意 `ordered_positions` 带 `default_factory=list`，元数据经 JSON 反序列化（tuple 丢失/字段缺省）后很容易退化成空列表，这条错误路径实际会被触发。应在缺少 `ordered_positions` 时按 `key_seed` 重放采样逻辑恢复顺序，而不是按字典分组顺序读取；或干脆在缺失时抛错要求重建元数据。
-- else:
            raise ValueError(
                "metadata.ordered_positions is empty; cannot reconstruct embed order "
                "from layer_positions (per-layer grouping loses bit order)"
            )

### [app/services/model_watermark.py] high/security
安全隐患：`key_seed` 在文档中被定义为 *Secret* seed（PRNG 选位秘密），却与 `layer_positions`/`ordered_positions` 一起放进「Persisted metadata」这一可序列化、可下发、可能随审计记录入库的对象里。一旦元数据落盘或随验证响应返回，攻击者即可精确定位并擦除/伪造水印位置，整套方案（包括 95% 阈值判定）失效。同时用 `random.Random`（Mersenne Twister，非密码学 PRNG）做安全相关选位也不合适。建议元数据中不保存种子（只保存其 SM3/HMAC 承诺用于校验），验证时由调用方通过 KMS/HMAC-SM3(KDF) 传入或重新派生位置。
-- layer_positions: dict[str, list[int]]  # layer_name -> list of flat indices
    key_seed_fingerprint: str  # SM3(seed) 承诺，绝不持久化种子本身

### [app/services/output_inspection.py] high/bug
DLP 阶段完全绕过了场景路由：这里虽然写了「不在 active_stages 就置 True」，但循环紧接着无条件执行，并在最后用 `stage_results[...DLP...] = dlp_passed` 覆盖了刚写入的 True。结果是即使场景（如 develop/application 之外的自定义场景）不启用 DLP，输出仍会被扫描、`redacted_output` 仍被改写、findings 仍被追加，且 `passed` 仍可能被 DLP 命中拖成 False。这使得检查策略越界、阶段语义与 SCENE_RULES 不一致，也让上面那行赋值成为死代码。建议把整个扫描块包进 `if InspectionStage.DLP.value in active_stages:`，否则不做扫描也不改写 redacted。
-- # Stage 2: DLP Scan (仅在该场景启用时执行)
        dlp_passed = True
        if InspectionStage.DLP.value in active_stages:
            pass  # TODO: 将下方 for 循环整体置于本 if 内

### [app/services/output_inspection.py] high/security
DLP 命中样本原样写入 findings，会造成二次泄露：`redacted_output` 已脱敏，但 `InspectionResult.findings[].samples` 里仍携带完整身份证号、手机号、银行卡号、邮箱等原文。该结构通常会被序列化进 API 响应、审计日志或落库，等于把最需要保护的数据以另一种路径导出，与 DLP 阶段的目的直接冲突。建议只保留命中数量与位置，或对样本做掩码（如仅保留前 3 后 2、或存哈希指纹用于复核）。
-- f"Found {len(matches)} {name} instance(s)",
                    samples=[_mask(v) for v in matches[:3]],

### [app/services/output_inspection.py] high/bug
违反本文件自述的 §8「真空披露」口径：DP 阶段被场景启用但 epsilon 为 None/<=0 时，`stage_results[DP]` 保持 True 且没有进入 `not_evaluated`，判定方会把「无从检查」读成「DP 检查通过」。同文件中 DATA_RECONSTRUCTION / K_ANONYMITY / MIA 都做了真空登记，唯独 DP 没有。建议在该 else 分支追加 `not_evaluated.append(InspectionStage.DP.value)`，或在场景强制要求 DP 时直接判阶段失败。
-- else:
                # epsilon is None or <= 0 -> DP not applied；场景启用了该阶段却无参数可查，必须登记真空
                not_evaluated.append(InspectionStage.DP.value)

### [app/services/output_inspection.py] high/security
预算管控存在可绕过的路径：只有当 `has_budget_sync(session_id)` 为真时才走 fail-closed 分支；否则照样加噪并置 `dp_applied=True`、`stage_results[DP]=True`，且完全不记账。调用方只要使用一个未分配预算的 session_id（进程重启后 ledger 缓存也会清空，`has_budget_sync` 随之为 False），即可无限次获得「已应用 DP」的结论而不消耗任何 epsilon，使双预算统一（M-03）与 fail-closed 目标失效。建议对启用 DP 的场景要求必须存在预算，否则登记 not_evaluated/直接判失败。
-- dp_applied = True
                    dp_budget_remaining = None
                    # TODO: 场景启用 DP 却无预算跟踪时应 fail-closed 或至少登记 not_evaluated

### [app/services/output_inspection.py] high/security
签名密钥的惰性初始化存在两个问题：1) `_signing_keypair` 是类级共享可变状态，无锁的 check-then-set 在多线程/多协程首次并发进入时会各自生成不同密钥对（TOCTOU），导致同一服务实例产出不可互相验证的签名；2) 密钥是运行时随机生成、不落盘也不分发公钥，进程重启即更换，所谓「不可抵赖（GM/T 0009）」实际上不成立——任何人都能生成同样结构的签名，事后无法追溯到确定的签名主体。建议启动时从受保护配置/KMS 加载固定签名密钥，并用锁或幂等初始化保护。
-- with _SIGNING_KEY_LOCK:
                if OutputInspector._signing_keypair is None:
                    OutputInspector._signing_keypair = crypto_service.load_or_generate_signing_keypair()

### [app/services/pii_ner.py] high/
Bug：`ner_engine="regex"/"off"/"none"` 被 `_resolve_engine` 解析为 `"regex_only"`，但这里只判断了 `self.enable_ner`，没有判断解析后的引擎。当调用方以默认 `enable_ner=True` + `PII_NER_ENGINE=off` 构造服务时（本文件底部的单例正是这种组合方式），`detect()` 仍会进入 `_detect_ner()`，而 `_detect_ner` 对既非 lac 也非 transformers 的引擎一律回落到 `_detect_ner_regex()`，于是规则 NER 依然运行并产出 `layer="ner"` 的匹配，同时结果却声称 `ner_engine="regex_only"`。这既违反类文档中“regex/off 关闭 NER 层”的约定，也违反 T9 的诚实披露契约（消费方会以为没有做任何规则识别）。
-- # Layer 2: NER detection (if enabled)
        if self.enable_ner and self._active_engine != "regex_only":
            ner_matches = self._detect_ner(text, regex_matches)

### [app/services/pii_ner.py] high/
Bug（PII 泄漏）：重叠消解只比较严重级别，被丢弃的那条匹配所覆盖的字符区间**不会**被并入保留项。由于 `matches` 按 `(start, end)` 升序排列，重叠通常是“部分重叠”而非包含关系：例如规则层 ADDRESS(HIGH) 覆盖 [0,15)，regex 层 EMAIL(MEDIUM) 覆盖 [10,40)，EMAIL 被整条丢弃后，其 [15,40) 部分会原样出现在 `redacted_text` 中；同理 else 分支里用后起点、更高级别的匹配整体替换 `result[-1]` 时，原匹配前半段（如手机号前几位）也会残留在脱敏文本里。此外同级别冲突时保留的是更短的匹配，长实体的尾部同样漏脱敏。建议：冲突时按区间求并集（保留被丢弃匹配的 [min(start), max(end)) 跨度，或把长出的部分拆成独立片段参与 `_redact`），而不是整条丢弃。
-- 

### [app/services/pii_ner.py] high/
违反诚实披露契约：LAC 推理抛异常时这里回落到规则层，产出的是 `layer="ner"` 的规则匹配，但 `detect()` 返回的 `ner_engine` 仍取 `self._active_engine == "lac"`。类文档明确要求“matches 为 layer="ner" 时 ner_engine 必须是 rule，绝不能标模型标签”，此处恰好制造了把规则误判当作模型 NER 的场景（审计/合规数据失真）。`_detect_ner_ml` 中的同款回落（约 425 行）也有同一问题。建议让内部检测方法同时返回实际引擎名，由 `detect()` 写入结果；同时 `PIIMatch.layer` 注释声明只有 "regex"/"ner"，实际还会产生 "ner_lac"/"ner_ml"，请统一契约。
-- 

### [app/services/pii_ner.py] high/
同上：模型推理失败回落到规则模式后，结果仍以 `ner_engine="transformers"` 上报，而匹配实际来自规则模式（`layer="ner"`），与文件头/T9 的披露约定冲突。另外下方 `ent["word"]`、`ent["start"]` 用下标访问，字段缺失会抛 `KeyError` 并被这个宽 `except` 吞掉，导致整条 ML 结果静默退化为规则结果，建议对单条实体用 `.get()` 并跳过，而不是让一条脏数据触发整层降级。
-- 

### [app/services/mia_probe.py] medium/bug
`ngram_size` 未做任何校验：
- `ngram_size = 0` 时，`tuple(words[i:i+0])` 恒为 `()`，`gen_ngrams == {()}`（长度 1），只要 `reference_texts` 非空，`overlap` 必为 `{()}`，`memorization_ratio` 恒等于 1.0（甚至随参考文本数量成倍放大），产生 100% 记忆化的假阳性。
- 负数时 `words[i:i+n]` 语义完全错误（从尾部截断），返回值失去意义。
- 当 `len(words) < ngram_size` 时 `range()` 为负 → 空集，虽然有前置兜底，但与 0/负数的静默错误混在一起难以排查。
建议在函数入口显式校验参数。
-- if not isinstance(ngram_size, int) or ngram_size < 1:
            raise ValueError(f"ngram_size must be a positive integer, got {ngram_size!r}")

        def get_ngrams(text: str, n: int) -> set[tuple[str, ...]]:
            words = text.split()
            if len(words) < n:
                return 

### [app/services/mia_probe.py] medium/bug
`loss_score` 在 `ratio == 1.0` 处分段不连续，破坏了“loss 越低越像成员”的单调性：
- `ratio == 1.0` → `1.0 - 0.5 = 0.5`
- `ratio == 1.001`（loss 略**高于**阈值）→ `1 / (1 + 0.0003) ≈ 0.9997`
也就是说 loss 刚越过阈值的样本拿到的成员分数远高于恰好在阈值上的样本，与文档所述“loss > threshold → decaying”相反，会造成阈值附近大量误判。
另外当 `loss < 0`（某些损失函数可为负）时 `ratio < 0`，`loss_score > 1.0`，超出“each 0-1”的约定。
建议统一为连续且单调递减的映射，并钳制上界。
-- if ratio <= 1.0:
                loss_score = 1.0 - max(0.0, ratio) * 0.5  # loss <= threshold → score 0.5-1.0
            else:
                loss_score = 0.5 / (1.0 + (ratio - 1.0) * 3)  # loss > threshold → decaying from 0.5

### [app/services/mia_probe.py] medium/other
缺少独立验证集时，用**待测样本自身**的 p75 作为“非成员”阈值存在自指偏置：p75 保证约 75% 的样本 loss ≤ threshold，这些样本在 `loss_score` 上系统性拿到 ≥0.5 的加分，`member_ratio` 因此被抬高并直接进入 `overall_risk`（还带 1.5 倍放大）。也就是说“没有验证集”这一信息缺失的情况反而会产出偏高的风险评级，而 `MIAResult.threshold` 又无法体现它是不可信的。建议在无验证集时显式标注阈值来源/可信度（或降低 loss 权重、要求调用方必须提供 `validation_losses`）。
-- losses = sorted(s["loss"] for s in samples)
            if losses:
                idx = int(len(losses) * 0.75)
                logger.warning(
                    "MIA threshold calibrated from the analyzed sample itself (p75); "
                    "member_ratio and risk_score may be inflated. Pr

### [app/services/model_watermark.py] medium/bug
嵌套列表分支接受 `tuple` 作为内层容器（`_flatten` 也支持 tuple），但这里对 `inner` 做原地赋值：若 `inner` 是 tuple，会抛出 `TypeError: 'tuple' object does not support item assignment`，错误信息还会落到末尾的 "Unsupported tensor type" 之外，调用方难以定位。建议只接受 list 作为可写内层容器，或显式拒绝 tuple 输入并给出可读错误。
-- for inner in tensor:
                if isinstance(inner, tuple):
                    raise TypeError("Nested tuple weights are immutable; cannot embed watermark")
                if isinstance(inner, list):
                    if offset + len(inner) > idx:
                        inner[idx - offset

### [app/services/model_watermark.py] medium/performance
`all_positions` 为**每一个**权重都创建一个 tuple。真实模型动辄千万~上亿参数，这里会构造同样量级的 tuple 列表（每个 tuple 约 72B + list 指针 8B），轻易占用数 GB 内存并造成长时间 GC 停顿；而最终只需要 `num_bits`（默认 64）个位置。正确做法是只采样：先按层把规模累加成前缀和，再用 `rng.sample(range(total), num_bits)` 得到的序号经二分映射到 (layer, idx)，无需物化任何全量列表。
-- # 只记录累计规模，按前缀和映射采样序号，避免物化全量位置列表
            layer_prefix.append((name, layer_prefix[-1][1] + size if layer_prefix else size))

### [app/services/model_watermark.py] medium/bug
这里只校验层名是否存在，未拒绝重复层名，也未检测多个 state_dict 键引用**同一个张量**（如 tied/共享 embedding、`weight` 与 `_weight_orig` 参数化）。此时 `all_positions` 含重复的物理位置，`rng.sample` 取的是列表下标而非物理位置，可能选中同一权重的两条记录并写入不同 bit，后写覆盖先写；验证时同一物理位被读取两次却对应两个不同期望 bit，必然出现误判（假阴性），且 `success=True` 已先行返回。建议在采样前对 `layer_names` 去重校验并按 `id(tensor)` 排除共享存储的张量。
-- seen_ids: set[int] = set()
        for name in layer_names:
            tensor = model_state_dict[name]
            if id(tensor) in seen_ids:
                raise ValueError(f"Layer '{name}' shares storage with an already selected layer")
            seen_ids.add(id(tensor))
            size = sel

### [app/services/model_watermark.py] medium/bug
空水印会被判定为验证通过：当 `watermark_bits` 为空且元数据中也无位置时，`extracted` 也为空，长度校验通过，`ratio` 被强制取 1.0，于是 `passed=True`。任何空/零长水印（`generate_watermark_bits(num_bits=0)` 也返回 `[]`）都能拿到一次「有效」的权属验证结论，属于认证逻辑绕过类的边界缺陷。空输入应显式拒绝（`num_bits <= 0` 直接抛 `ValueError`），而不是返回成功。
-- if not watermark_bits:
            raise ValueError("watermark_bits must not be empty; cannot verify an empty watermark")

        ratio = matches / len(watermark_bits)

### [app/services/model_watermark.py] medium/bug
LSB 编码方式存在两个问题：
1) 可检测/可擦除：写回值被强制量化到 `scaled / 1000.0`（最多 3 位小数），与未加水印的全精度权重在统计上极易区分。攻击者无需知道 `key_seed`，只需筛出「3 位小数」权重即可定位全部水印位并随机翻转 LSB，直接击穿 95% 阈值。
2) 静默丢位：未考虑目标 dtype。float16/bfloat16 张量写入后无法用 `int(round(w*1000))` 还原 LSB（半精度尾数只有 11 位，|w|>1 时误差即超过 0.5）；整数 dtype 张量写入浮点值会被截断。这些情况下 `embed_watermark` 仍返回 `success=True`。
建议在写回后回读校验（`_extract_bit` 结果与 `bit` 不一致则报错/计入失败），并按 dtype 精度选择缩放因子或拒绝低精度/整数张量。
-- scaled = int(round(w * 1000))
        # Clear LSB and set the desired bit
        scaled = (scaled & ~1) | (bit & 1)
        cls._set_weight_value(tensor, idx, scaled / 1000.0)
        if cls._extract_bit(tensor, idx) != (bit & 1):
            raise ValueError(
                f"LSB write did not ro

### [app/services/output_inspection.py] medium/security
k-匿名违规描述同样把准标识符原值（含 message 里的 `Group {qi_values}` 与这里的 samples）带出到 findings/日志，等同于以小批量方式回显个体级数据。建议对 qi_values 做掩码或只报告列名与组大小。
-- f"{len(violations)} group(s) below k={self.K_ANONYMITY_MIN}",
                    samples=[_mask_violation(v) for v in violations[:3]],

### [app/services/output_inspection.py] medium/bug
DLP 的门禁完全忽略 severity：任何一条命中（包括被标为 "low" 的 ip_address、"medium" 的 email）都会把 `dlp_passed` 置 False，进而 `passed = all(stage_results.values())` 直接判定整体不通过。`ip_address` 正则没有校验八位组范围（`999.999.999.999`、版本号 `1.2.3.4`、时间戳都会命中），因此一个无害的版本号字符串即可阻断整个输出发布，而 findings 里却写着 low —— 严重级别形同装饰，且会产生大量误拦。建议显式定义放行策略（例如仅 critical/high 使阶段失败，其余仅记录），并收紧 IP 正则。
-- matches = pattern.findall(output)
            if matches:
                if severity in ("critical", "high"):
                    dlp_passed = False

### [app/services/output_inspection.py] medium/bug
`redacted = output` 之后的 DLP 扫描假定 output 一定是字符串，但 Stage 1 明确用 `if not output or len(output.strip()) == 0` 允许 output 为 None/空。一旦调用方传入 None（detail 缺字段、上游返回空），`pattern.findall(None)` 会抛 TypeError 并让整个请求 500，而不是按「空输出」给出 FORMAT 不通过的结论。建议在进入 DLP 前做 `if not isinstance(output, str): output = ""`（或提前返回结构化失败）。
-- 

### [app/services/output_inspection.py] medium/bug
两处问题：1) `output_rows = noisy_rows` 是死赋值——DP 之后再无任何代码读取 `output_rows`，加噪结果只通过 `noisy_output_rows` 字段外泄，实际放行的 `redacted_output` 文本始终是未加噪的原值，若调用方按 `redacted_output` 返回即等于对外宣称已加噪而实际未加。2) 当 `output_rows` 为 None（自由文本输出）时 `_apply_dp_noise` 返回 `(None, 0)`，一个单元都没有扰动，却仍然 `dp_applied=True` 并消费预算，与注释「dp_applied must reflect what actually happened」矛盾。建议按 `num_noised > 0` 决定 dp_applied，并明确把加噪结果回填到实际输出或文档化其边界。
-- noisy_rows, num_noised = self._apply_dp_noise(output_rows, dp_epsilon)
                        # 只有真正加了噪才算 DP applied
                        dp_applied = num_noised > 0

### [app/services/output_inspection.py] medium/bug
把 `consume_sync` 返回 False 一律解读为「预算耗尽」不准确：`dp_budget_ledger.consume_sync` 在 epsilon ≤ DP_MIN_EPSILON_CONSUMPTION 或 epsilon > DP_MAX_EPSILON_PER_CONSUMPTION 时同样返回 False，此时会误报 dp_budget_exhausted（且 remaining 可能很充裕），更严重的是这条路径跳过了加噪，却对外给出「预算耗尽」的结论，掩盖了参数非法的真实原因。另外 `has_budget_sync` 与 `consume_sync` 之间是对共享 `_cache` 的 check-then-act，多线程并发下存在同一预算被超额消费的竞态。建议区分拒绝原因（让 ledger 返回状态/抛特定异常）并对预算读改写加锁或改为原子操作。
-- 

### [app/services/output_inspection.py] medium/error handling
签名阶段 fail-open 且异常被静默吞掉：SM2 签名失败后阶段结果仍被置 True（仅记入 degraded），既不记录原始异常（无 `logger.exception`，生产上完全无法定位密钥/HSM 故障），又让一个仅具摘要功能的 SM3 值占据 `signature` 字段，下游极易把它当作可验签的 SM2 签名（hex r||s）处理。此外回退语句写在 except 内部，若 `sm3_hash` 自身抛错将直接向上冒泡成 500。建议：记录异常、把 signature 置为 None 或加显式 `signature_kind` 标记，并按策略决定是否放行。
-- 

### [app/services/output_inspection.py] medium/security
MIA 检测的默认值是 fail-open：`model_output` 缺少 `confidence` 时取 0.0，缺少 `loss` 时取 `inf`，于是 risk_score 塌陷为 0，任何缺失字段的模型输出都会被判「安全」并通过该阶段；这与「训练模式必须检查记忆化风险」的目的相反。同时若 confidence/loss 为字符串（JSON 透传常见），`confidence * (1.0/(1.0+loss))` 会抛 TypeError。建议：字段缺失时判为不可评估（登记 not_evaluated）或按保守值处理，并做数值类型校验。
-- 

### [app/services/output_inspection.py] medium/bug
重构检测按 `row.values()` 的位置顺序 + `str(v)` 文本表示做比较，存在系统性漏检：输出与源数据的列顺序不同、做了投影（少几列）、或数值表示不一致（`1` vs `1.0`、`"007"` vs `7`、日期格式化差异）时，逐 tuple 比较必然不相等，match_rate 直接归零，攻击者只需调整列序即可绕过该关键控制。另外 docstring/类注释称「field-level exact match rate」，实现却是整行匹配，语义也不一致。建议按列名对齐后逐字段比较，或用 `frozenset((k, canon(v)) for k, v in row.items())` 做顺序无关比较。
-- for row in source_rows:
            source_set.add(frozenset((k, str(v)) for k, v in row.items()))

### [app/services/pii_ner.py] medium/
模块导入即产生重副作用：`__init__` 里直接调用 `_resolve_engine(ner_engine)`，`auto`/`lac`/`transformers` 路径会在构造时执行 `_load_lac()` / `_load_transformers()`。也就是说任何 `import app.services.pii_ner`（例如被 router 间接导入）都会在导入阶段加载 Baidu LAC 或 HuggingFace pipeline——阻塞启动、占用大量内存，`PII_NER_USE_ML=True` 时还可能在导入阶段触发模型联网下载。这与 `_load_lac`/`_load_transformers` 文档里声称的 “Lazily load” 不符。建议把引擎解析推迟到首次 `detect()`（或用 `threading.Lock` 保护的双重检查惰性初始化）。
-- 

### [app/services/pii_ner.py] medium/
`except Exception:` 无日志兜底会静默吞掉配置错误（如 `CDS_PII_NER_*` 值非法、`get_settings()` 校验失败、模型加载异常），悄悄换成默认规则引擎实例；同时因为 `PIINERService()` 默认 `ner_engine="auto"`，兜底路径仍可能在导入阶段尝试加载 LAC，失败后再次被这里的宽 except 掩盖。对以“诚实披露引擎”为目标的模块，这种静默降级风险较高，至少应 `logger.exception("PII NER singleton init failed; using rule-only defaults")`，并考虑让兜底实例使用 `ner_engine="rule"` 以避免重复加载模型。
-- 

### [app/services/pii_ner.py] medium/
偏移回填对齐风险：`text.find(word, pos)` 找不到词元时（LAC 会归一化/丢弃空白与标点、全半角转换），这里 `continue` 但**没有推进 `pos`**，导致其后所有词元都从旧偏移继续向后搜索，一旦该词在文本后部再次出现就会绑定到错误位置，`PIIMatch.start/end` 整体错位，`_redact` 会截断/篡改原文（且只记 warning 于外层异常，此处完全静默）。建议：找不到时按词元长度做保守推进（或按 `sum(len(w) for w in words[:i])` 累积偏移），并在偏移校验失败时放弃本条 LAC 匹配而非继续输出可疑位置。
-- 

### [app/services/pii_ner.py] medium/
共享模型实例的并发安全：模块级 `pii_ner_service` 是全局单例，`_lac` / `_ml_pipeline` 在构造时被加载一次后由所有请求共享。`LAC.run()` 与 transformers `pipeline`（内部持有 tokenizer/model 状态）都不是线程安全对象；FastAPI 同步端点会在线程池中并发执行、异步端点也可能经 `run_in_executor` 调用，多线程同时推理可能造成结果错乱或底层崩溃。建议用 `threading.Lock` 串行化推理、改为请求级实例，或按官方建议用 `AsyncPipeline`/进程池隔离。
-- 

### [app/services/pii_ner.py] medium/
性能：这里把每条正则命中展开成逐字符位置集合（三处 NER 检测 `_detect_ner_lac` / `_detect_ner_ml` / `_detect_ner_regex` 都各建一份），随后每个候选又用 `any(pos in regex_positions for pos in range(start, end))` 逐字符判断。对大文本 + 大量数字型 PII（日志、导出文本）这是 O(覆盖字符数) 的内存与 CPU 放大，而 `_detect_ner_regex` 里四类模式各判断一次，开销成倍。建议保存已排序的 `[(start, end)]` 区间列表并用二分/区间相交判断（`candidate.start < iv_end and iv_start < candidate.end`），或至少复用同一个已排序区间结构。
-- 

### [app/services/pii_ner.py] medium/
误报风险：该模式不带任何“出生”上下文，任何 `YYYY年M月D日` 都会被标为 `DATE_OF_BIRTH`，置信度 0.9、severity MEDIUM（如“2024年1月1日起实施”“合同签署于2023年5月6日”）。作为脱敏与审计依据会造成 PII 统计失真与大范围误脱敏。建议仅在前文出现 出生/生日/诞辰/DOB 等上下文时才归为 DATE_OF_BIRTH，其余日期归入更弱的类型或直接忽略（或显著下调 confidence）。
-- 

### [app/services/pii_ner.py] medium/
LAC 的 `TIME` 标签覆盖任意时间表达（“2026年”“上午10点”“最近三天”），直接映射成 `DATE_OF_BIRTH` 同样会把非生日信息标成 PII 并写入审计结果。建议映射到更准确的时间类型，或仅在文本含出生日期上下文时才归为 `DATE_OF_BIRTH`。
-- 

### [app/services/pii_ner.py] medium/
组织模式过度捕获：`(?:单位|公司|机构|组织)` 本身也是常见词（“……有限公司”里就含“公司”），而捕获组 `[一-龥]{2,30}` 贪婪匹配，会把后续整段中文叙述吞成组织名（如“公司成立于2010年并专注于……”会捕获 30 字），随后被整段 `[REDACTED:organization]` 替换，破坏文本并虚增 PII 计数。建议限制长度并加入非贪婪 + 终止边界（标点/“的/于/是/了”等），或在捕获结果上再校验是否以公司类后缀结尾。
-- 

### [app/services/pii_ner.py] medium/
与组织模式同类问题：`[一-龥0-9]{5,50}` 贪婪吞 50 字，且不以“路/号/室/省/市”等地址要素结尾，遇到“住址不详请联系客服中心尽快处理”这类文本会捕获整句并按 severity=HIGH 脱敏。建议收紧长度上限并要求尾部命中地址要素词，或降低该规则的 confidence 以便下游按阈值过滤。
-- 

### [app/services/mia_probe.py] low/performance
日志使用了 f-string 预格式化，绕过了 logging 的惰性格式化：即使 INFO 级别被关闭，字符串（含 `:.4f` 格式化）也会被无条件构建。请改为 `%s` 占位符 + 参数形式。另外该分支下 `logger.info` 在校准路径上每次调用都会执行，也建议确认调用频率。
-- logger.info(
            "MIA threshold calibrated: p%s = %.4f (from %d validation samples)",
            self.loss_threshold_percentile,
            threshold,
            len(sorted_losses),
        )

### [app/services/mia_probe.py] low/maintainability
无论最终风险等级是 `SAFE` 还是 `CRITICAL`，这里都固定以 `WARNING` 级别输出，会在正常（安全）模型上产生大量告警噪音，干扰真实的 MIA 告警。建议按 `risk_level` 动态选择日志级别（例如 SAFE/LOW 用 `debug`，MEDIUM 用 `info`，HIGH/CRITICAL 用 `warning`/`error`），同时改用惰性格式化。
-- log_fn = logger.warning if overall_risk >= 0.7 else logger.info
        log_fn(
            "MIA probe: risk=%s (%.3f), suspected_members=%d/%d, loss_separation=%.4f",
            risk_level.value,
            overall_risk,
            len(suspected),
            len(results),
            loss_separ

### [app/services/mia_probe.py] low/maintainability
`math` 导入后在本文件中从未使用（全文没有 `math.` 引用），属于死代码，建议删除。
-- 

### [app/services/mia_probe.py] low/bug
`sample_id` 用了 `sample.get(...)` 带默认值做容错，而 `loss` / `confidence` 用 `sample["loss"]` 直接下标访问。若上游传入的 dict 缺少其中一个键，会在循环中途抛出裸 `KeyError`（不含是哪个 sample 的上下文），且已完成的分析结果全部丢失。建议与 `sample_id` 保持一致的容错策略，或用显式校验给出可定位的错误信息。
-- if "loss" not in sample or "confidence" not in sample:
                raise ValueError(
                    f"sample missing required 'loss'/'confidence' key: {sample.get('sample_id', 'unknown')}"
                )
            result = self.analyze_sample(
                sample_id=sample.get("samp

### [app/services/model_watermark.py] low/maintainability
死代码：`hex_digest` 赋值后从未被读取。下面循环的首轮迭代用的是 `sm3_hash(payload + counter.to_bytes(4, "big"))`（counter=0），即首次哈希输入带了 4 字节后缀，本行注释宣称的「64 hex chars = 256 bits」结果根本没参与比特生成，容易误导读者认为首轮就是裸 payload 摘要。要么删除本行，要么让 counter=0 时直接使用 `hex_digest`（后者会改变已生成水印的比特值，需同步升级兼容处理）。
-- hex_digest = sm3_hash(payload)  # counter == 0 时的首轮摘要，见下方循环

### [app/services/model_watermark.py] low/maintainability
`hashlib` 在本文件中从未使用（哈希统一通过 `app.utils.crypto.sm3_hash`），属于无用导入，建议删除。
-- 

### [app/services/model_watermark.py] low/maintainability
死代码：`layer_sizes` 写入后在全文件（包括返回的 `WatermarkMetadata`）都未被读取，可以直接去掉。同理下面 `hasattr(tensor, "flat")` 分支永远不可达：`numpy.ndarray` 必然有 `flatten`，已被上一个分支拦下。
-- 

### [app/services/output_inspection.py] low/bug
噪声函数缺少参数与边界防护：1) `scale = sensitivity / epsilon` 在 epsilon=0 时抛 ZeroDivisionError（公开方法，调用方可传任意 epsilon，应显式 `if epsilon <= 0: raise ValueError`）；2) `random.random()` 可返回 0.0，此时 `u=-0.5`、`math.log(1-2*abs(u))` 变成 `log(0)` 抛 ValueError；3) 作为隐私机制使用 Mersenne Twister 标准库 PRNG，噪声序列在攻击者可观测多次输出的情况下是可预测的，隐私场景应使用 `secrets`/CSPRNG；4) `import random`、`import math`（后者已在模块级导入）放在函数体内，而本函数按单元格逐个调用，属热路径上的无谓开销。
-- if epsilon <= 0:
            raise ValueError("epsilon must be > 0 for Laplace mechanism")
        scale = sensitivity / epsilon
        # Laplace distribution sampling（0.5 兜底避免 log(0)）
        u = secrets.randbelow(...) ...  # 使用 CSPRNG，并将 |u| 夹在 [0, 0.5-eps]

### [app/services/output_inspection.py] low/maintainability
`check_k_anonymity` 用 `rows[0].keys()` 推断准标识符集合，隐含「所有行同构」的假设：行列式不一致时，后续行缺失的列经 `row.get(col, "")` 变成空串并被并入同一组，虚增组大小从而漏报违规；同时 `str(...)` 把 `None`、空串、`0`/`"0"` 混为一谈。建议取列名并集，区分缺失值（例如单独以 MISSING 哨兵参与分组并在违规描述中标注）。
-- 

### [app/services/output_inspection.py] low/maintainability
`hashlib` 与 `json` 在本文件中从未被引用（水印/哈希逻辑都在 `app.utils.watermark` 与 `crypto_service` 中），属残留导入，建议删除以免误导后续维护者以为此处直接做了哈希处理。
-- 

### [app/services/output_inspection.py] low/maintainability
`MIARiskLevel` 被导入但从未使用（本文件只用了 `self.check_mia_risk` 的返回值），且该 import 位于每次 inspect 调用都会执行的方法体内。建议删除未使用符号，并把确需延迟导入的 `mia_probe` 保留在最小必要的调用处。
-- 

### [app/services/pii_ner.py] low/
银行卡模式仅校验首位 3-6 与长度 16-19，未做 Luhn 校验，任何 16-19 位连续数字（订单号、流水号、时间戳拼接、18 位身份证号首位 3-6 的情况）都会被标为 `BANK_CARD` + severity=HIGH 并被脱敏。建议增加 Luhn 校验作为二次确认，不通过则降置信度或直接丢弃，以降低对业务文本的误脱敏。
-- 
