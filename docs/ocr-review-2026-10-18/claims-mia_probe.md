# Claims for app/services/mia_probe.py (11)

## C1 [critical/bug]
LOCATED: true  fileExists: true
EXISTING CODE SNIPPET (as seen by the tool, may be stale):
```
overall_risk = min(1.0, member_ratio * 1.5 + (1.0 / (1.0 + loss_separation)) * 0.5)
```
CLAIM:
聚合风险公式存在两处缺陷：
1. **除零风险**：`loss_separation` 是无界量（loss 可任意大）。当它恰好等于 -1.0 时 `1.0 + loss_separation == 0`，直接抛 `ZeroDivisionError`。
2. **无下界钳制**：当 `loss_separation < -1`（例如全部样本都被判为疑似成员，此时 `non_member_losses` 为空 → `mean_loss_non_members = 0.0` → `loss_separation = -mean_loss`，只要平均 loss > 1 就会 < -1），该项变成负数且绝对值可远大于 `member_ratio * 1.5`，`overall_risk` 会算成负数。由于只做了 `min(1.0, ...)` 而没有 `max(0.0, ...)`，最终结果是 `risk_score` 为负、`risk_level` 被判成 `SAFE`——即“100% 样本疑似成员”却输出最低风险，属于严重误判。
SUGGESTED FIX (tool): separation_term = 1.0 / (1.0 + max(0.0, loss_separation))
        overall_risk = min(1.0, max(0.0, member_ratio * 1.5 + separation_term * 0.5))

## C2 [high/bug]
LOCATED: true  fileExists: true
EXISTING CODE SNIPPET (as seen by the tool, may be stale):
```
mean_loss_members = statistics.mean(member_losses) if member_losses else 0.0
        mean_loss_non_members = statistics.mean(non_member_losses) if non_member_losses else 0.0
        loss_separation = mean_loss_non_members - mean_loss_members
```
CLAIM:
空集合用 `0.0` 作哨兵会污染 `loss_separation`，使聚合风险出现系统性假阳性/假阴性：
- **无疑似成员时**：`member_losses` 为空 → `mean_loss_members = 0.0`，于是 `loss_separation = 全体样本平均 loss`（一个正数），`overall_risk = 0.5 / (1 + mean_loss)`。若平均 loss 较小（如 0.05），即使 `member_ratio == 0`、`suspected_members == 0`，`overall_risk` 仍可达 ~0.48，被判定为 `LOW`；平均 loss 越接近 0 越接近 0.5（`MEDIUM` 边界）。零疑似成员的模型不应得到非 SAFE 评级。
- **全部疑似成员时**：`non_member_losses` 为空 → `mean_loss_non_members = 0.0` → `loss_separation` 为负，与上一条 critical 问题叠加，会把风险压成负数。
建议：某一侧集合为空时不把 `loss_separation` 计入聚合公式（视为 0），并用显式分支代替 0.0 哨兵（0.0 是合法的 loss 值，与“无数据”无法区分）。
SUGGESTED FIX (tool): mean_loss_members = statistics.mean(member_losses) if member_losses else 0.0
        mean_loss_non_members = statistics.mean(non_member_losses) if non_member_losses else 0.0
        loss_separation = (
            mean_loss_non_members - mean_loss_members
            if member_losses and non_member_losses
            else 0.0
        )

## C3 [high/bug]
LOCATED: true  fileExists: true
EXISTING CODE SNIPPET (as seen by the tool, may be stale):
```
for ref in reference_texts:
            ref_ngrams = get_ngrams(ref, ngram_size)
            overlap = gen_ngrams & ref_ngrams
            if overlap:
                total_matched += len(overlap)
                # Extract the matched region
                for ngram in list(overlap)[:3]:  # Top 3 matches
                    snippet = " ".join(ngram)
                    matched_snippets.append(snippet)

        memorization_ratio = total_matched / len(gen_ngrams) if gen_ngrams else 0.0
```
CLAIM:
`total_matched` 在参考文本循环内**逐个累加**重叠数，但同一个生成 n-gram 若出现在多个参考文本中会被重复计数，而分母 `len(gen_ngrams)` 是去重后的生成 n-gram 数。结果 `memorization_ratio` 可以远大于 1.0，且与参考文本数量成正比（同一句话在 100 份训练语料里重复出现，比例可被放大到 100），完全失去“比例”语义，调用方若据此判定记忆化风险会严重误报。
另外 `list(overlap)[:3]` 直接取集合元素，集合迭代顺序不稳定，导致 `matched_snippets` 在不同进程/运行间不可复现，且同一 snippet 会在多个参考文本中重复出现。
建议：用集合求并集后再计数，并对 snippets 去重。
SUGGESTED FIX (tool): matched_ngrams: set[tuple[str, ...]] = set()

        for ref in reference_texts:
            ref_ngrams = get_ngrams(ref, ngram_size)
            overlap = gen_ngrams & ref_ngrams
            if overlap:
                matched_ngrams |= overlap
                # Extract the matched region (sorted for deterministic output)
                for ngram in sorted(overlap)[:3]:  # Top 3 matches
                    snippet = " ".join(ngram)
                    if snippet not in matched_snippets:
                        matched_snippets.append(snippet)

        memorization_ratio = len(matched_ngrams) / len(gen_ngrams) if gen_ngrams else 0.0

## C4 [high/bug]
LOCATED: true  fileExists: true
EXISTING CODE SNIPPET (as seen by the tool, may be stale):
```
if perplexity is not None and perplexity > 0:
            # Lower perplexity = higher membership probability
            perplexity_score = max(0, 1.0 - (perplexity / self.perplexity_threshold))
```
CLAIM:
`self.perplexity_threshold` 直接来自构造函数且从未校验，若调用方（或配置中心/配置文件）传入 `0`，只要样本带有正的 perplexity 就会抛 `ZeroDivisionError`，整个批量分析中断。此外该分支条件 `perplexity > 0` 与注释语义相反：perplexity 越小应代表越强的成员信号，但 `perplexity == 0`（常见的“缺失/未计算”哨兵，或极端记忆化结果）时得分被置为 `0.0`，即给了“最像成员”的样本最低分。建议校验阈值并同时明确 `perplexity <= 0` 的语义。
SUGGESTED FIX (tool): if (
            perplexity is not None
            and perplexity >= 0
            and self.perplexity_threshold > 0
        ):
            # Lower perplexity = higher membership probability
            perplexity_score = max(0, 1.0 - (perplexity / self.perplexity_threshold))
        elif perplexity is not None and self.perplexity_threshold <= 0:
            logger.warning(
                "perplexity_threshold must be > 0, got %s; perplexity signal ignored",
                self.perplexity_threshold,
            )

## C5 [medium/bug]
LOCATED: true  fileExists: true
EXISTING CODE SNIPPET (as seen by the tool, may be stale):
```
def get_ngrams(text: str, n: int) -> set[tuple[str, ...]]:
            words = text.split()
            return {tuple(words[i:i+n]) for i in range(len(words) - n + 1)}
```
CLAIM:
`ngram_size` 未做任何校验：
- `ngram_size = 0` 时，`tuple(words[i:i+0])` 恒为 `()`，`gen_ngrams == {()}`（长度 1），只要 `reference_texts` 非空，`overlap` 必为 `{()}`，`memorization_ratio` 恒等于 1.0（甚至随参考文本数量成倍放大），产生 100% 记忆化的假阳性。
- 负数时 `words[i:i+n]` 语义完全错误（从尾部截断），返回值失去意义。
- 当 `len(words) < ngram_size` 时 `range()` 为负 → 空集，虽然有前置兜底，但与 0/负数的静默错误混在一起难以排查。
建议在函数入口显式校验参数。
SUGGESTED FIX (tool): if not isinstance(ngram_size, int) or ngram_size < 1:
            raise ValueError(f"ngram_size must be a positive integer, got {ngram_size!r}")

        def get_ngrams(text: str, n: int) -> set[tuple[str, ...]]:
            words = text.split()
            if len(words) < n:
                return set()
            return {tuple(words[i:i+n]) for i in range(len(words) - n + 1)}

## C6 [medium/bug]
LOCATED: true  fileExists: true
EXISTING CODE SNIPPET (as seen by the tool, may be stale):
```
if ratio <= 1.0:
                loss_score = 1.0 - ratio * 0.5  # loss <= threshold → score 0.5-1.0
            else:
                loss_score = max(0, 1.0 / (1.0 + (ratio - 1.0) * 3))  # loss > threshold → decaying
```
CLAIM:
`loss_score` 在 `ratio == 1.0` 处分段不连续，破坏了“loss 越低越像成员”的单调性：
- `ratio == 1.0` → `1.0 - 0.5 = 0.5`
- `ratio == 1.001`（loss 略**高于**阈值）→ `1 / (1 + 0.0003) ≈ 0.9997`
也就是说 loss 刚越过阈值的样本拿到的成员分数远高于恰好在阈值上的样本，与文档所述“loss > threshold → decaying”相反，会造成阈值附近大量误判。
另外当 `loss < 0`（某些损失函数可为负）时 `ratio < 0`，`loss_score > 1.0`，超出“each 0-1”的约定。
建议统一为连续且单调递减的映射，并钳制上界。
SUGGESTED FIX (tool): if ratio <= 1.0:
                loss_score = 1.0 - max(0.0, ratio) * 0.5  # loss <= threshold → score 0.5-1.0
            else:
                loss_score = 0.5 / (1.0 + (ratio - 1.0) * 3)  # loss > threshold → decaying from 0.5

## C7 [medium/other]
LOCATED: true  fileExists: true
EXISTING CODE SNIPPET (as seen by the tool, may be stale):
```
losses = sorted([s["loss"] for s in samples])
            if losses:
                idx = int(len(losses) * 0.75)
```
CLAIM:
缺少独立验证集时，用**待测样本自身**的 p75 作为“非成员”阈值存在自指偏置：p75 保证约 75% 的样本 loss ≤ threshold，这些样本在 `loss_score` 上系统性拿到 ≥0.5 的加分，`member_ratio` 因此被抬高并直接进入 `overall_risk`（还带 1.5 倍放大）。也就是说“没有验证集”这一信息缺失的情况反而会产出偏高的风险评级，而 `MIAResult.threshold` 又无法体现它是不可信的。建议在无验证集时显式标注阈值来源/可信度（或降低 loss 权重、要求调用方必须提供 `validation_losses`）。
SUGGESTED FIX (tool): losses = sorted(s["loss"] for s in samples)
            if losses:
                idx = int(len(losses) * 0.75)
                logger.warning(
                    "MIA threshold calibrated from the analyzed sample itself (p75); "
                    "member_ratio and risk_score may be inflated. Provide validation_losses instead."
                )

## C8 [low/performance]
LOCATED: true  fileExists: true
EXISTING CODE SNIPPET (as seen by the tool, may be stale):
```
logger.info(f"MIA threshold calibrated: p{self.loss_threshold_percentile} = {threshold:.4f} "
                     f"(from {len(sorted_losses)} validation samples)")
```
CLAIM:
日志使用了 f-string 预格式化，绕过了 logging 的惰性格式化：即使 INFO 级别被关闭，字符串（含 `:.4f` 格式化）也会被无条件构建。请改为 `%s` 占位符 + 参数形式。另外该分支下 `logger.info` 在校准路径上每次调用都会执行，也建议确认调用频率。
SUGGESTED FIX (tool): logger.info(
            "MIA threshold calibrated: p%s = %.4f (from %d validation samples)",
            self.loss_threshold_percentile,
            threshold,
            len(sorted_losses),
        )

## C9 [low/maintainability]
LOCATED: true  fileExists: true
EXISTING CODE SNIPPET (as seen by the tool, may be stale):
```
logger.warning(
            f"MIA probe: risk={risk_level.value} ({overall_risk:.3f}), "
            f"suspected_members={len(suspected)}/{len(results)}, "
            f"loss_separation={loss_separation:.4f}"
        )
```
CLAIM:
无论最终风险等级是 `SAFE` 还是 `CRITICAL`，这里都固定以 `WARNING` 级别输出，会在正常（安全）模型上产生大量告警噪音，干扰真实的 MIA 告警。建议按 `risk_level` 动态选择日志级别（例如 SAFE/LOW 用 `debug`，MEDIUM 用 `info`，HIGH/CRITICAL 用 `warning`/`error`），同时改用惰性格式化。
SUGGESTED FIX (tool): log_fn = logger.warning if overall_risk >= 0.7 else logger.info
        log_fn(
            "MIA probe: risk=%s (%.3f), suspected_members=%d/%d, loss_separation=%.4f",
            risk_level.value,
            overall_risk,
            len(suspected),
            len(results),
            loss_separation,
        )

## C10 [low/maintainability]
LOCATED: true  fileExists: true
EXISTING CODE SNIPPET (as seen by the tool, may be stale):
```
import math
```
CLAIM:
`math` 导入后在本文件中从未使用（全文没有 `math.` 引用），属于死代码，建议删除。

## C11 [low/bug]
LOCATED: true  fileExists: true
EXISTING CODE SNIPPET (as seen by the tool, may be stale):
```
result = self.analyze_sample(
                sample_id=sample.get("sample_id", "unknown"),
                loss=sample["loss"],
                confidence=sample["confidence"],
```
CLAIM:
`sample_id` 用了 `sample.get(...)` 带默认值做容错，而 `loss` / `confidence` 用 `sample["loss"]` 直接下标访问。若上游传入的 dict 缺少其中一个键，会在循环中途抛出裸 `KeyError`（不含是哪个 sample 的上下文），且已完成的分析结果全部丢失。建议与 `sample_id` 保持一致的容错策略，或用显式校验给出可定位的错误信息。
SUGGESTED FIX (tool): if "loss" not in sample or "confidence" not in sample:
                raise ValueError(
                    f"sample missing required 'loss'/'confidence' key: {sample.get('sample_id', 'unknown')}"
                )
            result = self.analyze_sample(
                sample_id=sample.get("sample_id", "unknown"),
                loss=sample["loss"],
                confidence=sample["confidence"],


## Known importers (grep of whole repo for "mia_probe" in *.py)
- app/api/training.py
- app/services/llm_sft_runtime.py
- app/services/memorization_check.py
- app/services/mia_probe.py
- app/services/output_inspection.py
- tests/test_mia_probe.py
- tests/test_pz01_sft_real.py
- tests/test_training_fail_closed.py
