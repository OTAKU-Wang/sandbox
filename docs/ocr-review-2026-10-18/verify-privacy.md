# 核验报告 — claims-privacy.md (54 条) vs 真实代码

核验人：AI 复核（senpi-task st_01a14e5a）· 日期 2026-10-18 · 仓库 D:/workspace/sandbox（未修改任何文件）

## 判定口径
- **TRUE·LIVE**：缺陷真实存在，且从 `app.main` 可达（有生产调用方）。
- **TRUE·LATENT**：缺陷真实存在，但无生产调用方 / 触发条件在当前调用链上不成立。
- **FALSE**：与代码不符（附反证行）。
- **known**：仅复述项目已文档化的既有事实（AES-标签-SM4、SM3→SHA-256 回退、刻意保留的仿真开关）。
- **dup of [n]**：与第 n 条重复。

## 可达性基线（rev_imports3.py 实测，git-bash）
| 模块 | direct importers | main.py reaches it? | 判定 |
|---|---|---|---|
| `app.services.mia_probe` | `memorization_check`, `output_inspection` | **True** | LIVE（`output_inspection.py:268` 懒导入；`memorization_check.py:72` 调 `check_text_memorization`） |
| `app.services.model_watermark` | `app.api.training`, `llm_sft_runtime` | **True** | LIVE（`training.py:802` `POST /jobs/{job_id}/watermark`、`training.py:875` `GET .../watermark/verify`；`llm_sft_runtime.py:932`） |
| `app.services.output_inspection` | `api.deliveries`, `api.output_control`, `api.sandbox_sessions`, `output_security` | **True** | LIVE（输出 DLP 闸门） |
| `app.services.pii_ner` | 仅 `app.services.stream_audit` | **False** | **LATENT**：`stream_audit.py:19` 是唯一 importer，而 `stream_audit` 在 `app/` 内 0 个引用方（`grep -rn "stream_audit" app/ --include=*.py` 除自身外无输出）；`pii_ner.py`/`stream_audit.py` 内无 `importlib`/`__import__`（grep 无输出）。故本文件所有 pii_ner 缺陷一律 TRUE·LATENT。 |

> 注意：claims 文件里每条 `--` 之后的代码块多为**建议修复后的代码**，不是现状。下面凡引用现状处，均给出真实 `file:line`。

## app/services/mia_probe.py

> **可达性细化（重要）**：`mia_probe.py` 模块本身被 `app.main` 间接导入（`output_inspection.py:268` 方法内 `from app.services.mia_probe import mia_probe, MIARiskLevel`），但 `grep -rn "analyze_batch\|check_text_memorization\|calibrate_threshold\|analyze_sample" app/ sdk/ --include=*.py` 显示：**模块里没有任何一个公开方法有生产调用方**。
> - `output_inspection.check_mia_risk`（`output_inspection.py:478-503`）是自写的独立启发式（`confidence * (1/(1+loss))`），**完全不调用** `MIAProbe`；它导入的 `mia_probe`、`MIARiskLevel` 两个名字在本文件均未使用。
> - `check_text_memorization` 的唯一调用方是 `app/services/memorization_check.py:72`，而 `grep -rn "memorization_check" app/ sdk/ --include=*.py`（排除自身）**无任何输出** → 该调用方本身是死代码。
> - `app/services/llm_sft_runtime.py:669` 的 `MIAProbe(...)` 是 **同文件 `:274` 自定义的另一个 MIAProbe 类**（`member_samples=`/`non_member_samples=` 关键字在本模块不存在），与 `mia_probe.py` 无关。
>
> 因此本文件所有缺陷按「函数是否有生产调用方」判 LATENT/LIVE，下文逐条注明。

### #1 [critical/bug] 聚合公式除零 + 无下界钳制 → **TRUE·LATENT**（数值已实测复现）
证据：`app/services/mia_probe.py:219`、`:223`
```
219: loss_separation = mean_loss_non_members - mean_loss_members
223: overall_risk = min(1.0, member_ratio * 1.5 + (1.0 / (1.0 + loss_separation)) * 0.5)
```
`:223` 只有外层 `min(1.0, ...)`，**确无 `max(0.0, ...)`**，也没有对 `1.0 + loss_separation == 0` 的守卫；`loss_separation` 无界（`:218` 空集给 `0.0`，故 `ls = -mean_loss_members` 可任意负）。用真实代码实测（`.venv-gate` py，直接 import 该模块）：
- `loss=1.001, confidence=0.75 ×10` → `loss_separation=-1.0010` → **`risk_score = -498.500`，`risk_level = safe`，`suspected_members=10/10`** —— 即「100% 样本疑似成员」被判最低风险，与断言完全一致。
- `loss=1.0, confidence=0.75 ×10` → **`ZeroDivisionError: float division by zero`**（`:223`），与断言 1 完全一致。
判 LATENT 仅因无生产调用方（见上），缺陷本身真实且严重（若接入即 critical）。

### #2 [high/bug] 空集合用 `0.0` 哨兵污染 `loss_separation` → **TRUE·LATENT**（实测复现）
证据：`mia_probe.py:217-219`
```
217: mean_loss_members = statistics.mean(member_losses) if member_losses else 0.0
218: mean_loss_non_members = statistics.mean(non_member_losses) if non_member_losses else 0.0
219: loss_separation = mean_loss_non_members - mean_loss_members
```
实测 `loss=0.05, confidence=0.0 ×10`（`member_ratio = 0.0`，`suspected_members=0/10`）→ `loss_separation = 0.05` → **`risk_score = 0.4762` → `risk_level = low`**。断言预测的「~0.48 / LOW」精确命中；零疑似成员确实拿不到 SAFE。后半段（全疑似 → 压成负数）与 #1 同源，**dup of #1**。

### #3 [high/bug] `total_matched` 跨参考文本重复计数 + `list(overlap)[:3]` 顺序不稳定 → **TRUE·LATENT**（实测复现）
现状证据（注意 claims 里 `matched_ngrams |= overlap` 是**建议修复**，现状是逐个累加）：`mia_probe.py:284-296`
```
284: total_matched = 0
286: for ref in reference_texts:
288:     overlap = gen_ngrams & ref_ngrams
290:         total_matched += len(overlap)
292:         for ngram in list(overlap)[:3]:  # Top 3 matches
296: memorization_ratio = total_matched / len(gen_ngrams) if gen_ngrams else 0.0
```
实测：生成文本与 **5 份完全相同**的参考文本，`ngram_size=4` → **`memorization_ratio = 5.0`**（分母去重、分子按参考文本份数放大 5 倍），`matched_snippets` 出现重复项（`'over the lazy dog'` 重复）。`list(overlap)[:3]` 取 set 元素，跨进程因 str 哈希随机化不可复现 —— 两点均成立。
下游放大：`memorization_check.py:72-77` 把该 ratio 直接当「比例」用于 `is_memorized = ratio >= threshold`（`:80`）与分档（`:83+`），误报成立。判 LATENT 的唯一理由：`memorization_check` 无生产调用方。

### #4 [high/bug] `perplexity_threshold` 未校验 + `perplexity == 0` 语义颠倒 → **TRUE·LATENT**（阈值=0 崩溃与语义缺陷均真实，但 `analyze_sample` 无生产调用方）
现状证据（claims 里 `perplexity >= 0 and self.perplexity_threshold > 0` 是**建议修复**）：`mia_probe.py:70`、`:135-137`
```
 70: self.perplexity_threshold = perplexity_threshold          # 无校验
135: if perplexity is not None and perplexity > 0:
137:     perplexity_score = max(0, 1.0 - (perplexity / self.perplexity_threshold))
```
实测：`MIAProbe(perplexity_threshold=0.0).analyze_sample(..., perplexity=3.0)` → **`ZeroDivisionError`**（`:137`）。实测 `analyze_sample(loss=0.1, conf=0.0, perplexity=0.0)` → `perplexity_score` 贡献 0（`risk_score=0.45`，与不传 perplexity 同权重重算）→ 「最强成员信号被记 0 分」成立。
可达性：生产只用默认 `perplexity_threshold=10.0` 的模块级单例 `mia_probe`（`:301`），无任何调用方传 0（`llm_sft_runtime.py:669` 是另一个同名类）；且 `analyze_sample` 无生产调用方 → 崩溃路径 **LATENT**，语义缺陷真实存在。

### #18 [medium/bug] `ngram_size` 零/负数未校验 → **TRUE·LATENT**（实测复现）
证据：`mia_probe.py:275-277`（无任何参数校验）
```
275: def get_ngrams(text: str, n: int) -> set[tuple[str, ...]]:
277:     return {tuple(words[i:i+n]) for i in range(len(words) - n + 1)}
```
实测：`ngram_size=0` → `check_text_memorization(gen, ["hello world", "again other"], 0)` → **`(2.0, ['', ''])`**（`gen_ngrams = {()}` 长度 1，每份参考各加 1 → ratio = 参考文本份数 = 100%×N 的假阳性）；`ngram_size=-2` → `(0.666…, ['', ''])`（切片语义崩坏）。断言全部成立。

### #19 [medium/bug] `loss_score` 在 `ratio == 1.0` 处分段不连续、破坏单调性 → **TRUE·LATENT**（实测复现，且后果比断言更重）
现状证据（claims 里 `0.5 / (1.0 + ...)` 是**建议修复**，现状更糟）：`mia_probe.py:126-129`
```
126: if ratio <= 1.0:
127:     loss_score = 1.0 - ratio * 0.5      # ratio=1.0 → 0.5
128: else:
129:     loss_score = max(0, 1.0 / (1.0 + (ratio - 1.0) * 3))   # ratio→1⁺ → ≈1.0
```
实测（`confidence=0.0`，`loss_threshold=0.5`）：`loss=0.5`（ratio=1.0）→ `risk_score=0.3`（不疑似）；`loss=0.5005`（ratio=1.001，loss **更高**）→ **`risk_score=0.5982` → `is_member_suspected=True`**（`:152`）。即阈值上方一点反而被判成员，直接翻转 `:152` 的成员判定，不只是分数失真。`loss=-1.0` → `ratio<0` → `loss_score = 1.0 - (-2)*0.5 = 2.0`（实测 `risk_score` 被 `:149` 钳到 `1.0`）→ 确实违反 docstring `:122` 「each 0-1」。

### #20 [medium/other] 无验证集时用被测样本自身 p75 定阈 → 自指偏置 → **TRUE·LATENT**
证据：`mia_probe.py:181-188`
```
181: # Heuristic: use p75 of sample losses ...
182: losses = sorted([s["loss"] for s in samples])
183: if losses:
184:     idx = int(len(losses) * 0.75)
```
`MIAResult.threshold`（`:44`）确实不区分阈值来源，`member_ratio` 以 1.5 倍权重进入 `:223`，方向性判断成立。
**须更正断言的引用**：claims 把 `logger.warning("MIA threshold calibrated from the analyzed sample itself (p75); ...")` 当作现状代码引用，**该行在本文件不存在** —— 本文件只有两处日志（`:95` info、`:237` warning），`:184` 分支完全静默。断言结论对，但它引用的「已有告警」是虚构的现状（实际现状是连披露都没有，比断言描述更差）。

### #43 [low/performance] 日志用 f-string 预格式化 → **TRUE·LATENT**
证据：`mia_probe.py:95-96` `logger.info(f"MIA threshold calibrated: p{self.loss_threshold_percentile} = {threshold:.4f} " f"(from {len(sorted_losses)} validation samples)")` —— 确为 f-string 即时格式化（claims 里的 `%s` 形式是建议写法）。同理 `:237-241` 也是 f-string。所在 `calibrate_threshold` 无生产调用方 → LATENT。

### #44 [low/maintainability] 无论风险等级固定 WARNING → **TRUE·LATENT**
证据：`mia_probe.py:237-241` 无条件 `logger.warning(f"MIA probe: risk={risk_level.value} ...")`，位于 `:225-235` 的等级分支之后，SAFE 与 CRITICAL 共用同一级别。claims 里的 `log_fn = logger.warning if overall_risk >= 0.7 else logger.info` 是建议修复。所在 `analyze_batch` 无生产调用方 → LATENT。

### #45 [low/maintainability] `import math` 死代码 → **TRUE·LIVE**（`mia_probe` 确实被 `app.main` 间接导入，死导入在生产进程内被执行；仅本条的作害面为零）
证据：`mia_probe.py:13` `import math`；`grep -n "math\." app/services/mia_probe.py` **0 处命中**（唯一 `math` 出现在第 13 行）→ 确认未使用。

### #46 [low/bug] `sample_id` 容错而 `loss`/`confidence` 裸下标 → **TRUE·LATENT**
证据：`mia_probe.py:193-197`
```
195:                 loss=sample["loss"],
196:                 confidence=sample["confidence"],
```
对照 `:194 sample.get("sample_id", "unknown")`，确为不一致的容错策略，缺键时抛裸 `KeyError` 且无 sample 上下文（claims 里的 `if "loss" not in sample: raise ValueError(...)` 是建议修复）。补充：`:182 losses = sorted([s["loss"] for s in samples])` 会更早抛同一个 `KeyError`。所在 `analyze_batch` 无生产调用方 → LATENT。

## app/services/model_watermark.py

> 本模块**真正 LIVE**：`app/api/training.py:802` `POST /jobs/{job_id}/watermark` 与 `:875` `GET /jobs/{job_id}/watermark/verify`（`main.py` 已挂载 training 路由），入口权重来自 `_load_model_checkpoint`（`training.py:671`），它可能返回 **numpy 数组**（`training.py:572-590` `try_numpy`：`.npz` → `{name: data[name]}`，`.npy` → `{"array": loaded}`）、torch 张量或 JSON 嵌套 list。

### #5 [critical/bug] `_set_weight_value` 将 numpy 误判为 torch，水印静默丢弃 → **TRUE·LIVE**（实测复现）
证据：`model_watermark.py:267-274`
```
267: if hasattr(tensor, "flatten") and hasattr(tensor, "__setitem__"):
268:     flat = tensor.flatten()      # numpy.flatten 永远返回副本
269:     flat[idx] = value            # 只写临时数组
270:     return
272: if hasattr(tensor, "flat"):     # 不可达：numpy 必带 flatten
```
实测（真实代码，`dtype=float64` 的 64 元素 ndarray，`generate_watermark_bits(...,16)`）：`embed_watermark` 返回 **`success=True`**，而 `changed_weights = 0/64`（写入全部丢失）；紧接 `verify_watermark` 得 **`match_ratio=0.500 passed=False`** —— 与断言逐字一致。可达路径：`training.py:833` `embed_watermark(checkpoint.state_dict, ...)` + `.npz/.npy` checkpoint（`training.py:618 loaders = [try_numpy]`）。`success=True` 已写入 DB 记录（`training.py:843-849`）并返回 201。torch 非连续张量（`t.T`、channels_last）同理——本机无 torch 未能实测，但 `:268` 无任何 contiguous 守卫，判定成立。同时 `:272` 的 numpy 分支为死代码（与 #49 后半重叠）。**本文件最严重的一条，建议优先修。**

### #6 [high/performance] 每读 1 bit 就整张量 `_flatten` + 逐元素 `float()` → **TRUE·LIVE**
证据：`model_watermark.py:255-261` + `:306-310` + `:220-225`
```
255: def _get_weight_value(cls, tensor, idx):
258:     if isinstance(tensor, list) and tensor and not isinstance(tensor[0], (list, tuple)):
259:         return float(tensor[idx])          # 仅半层 list 有快路径
260:     flat = cls._flatten(tensor)            # 其余全部：整张量复制 + 全量 float()
261:     return flat[idx]
224:     flat = tensor.flatten()
225:     return [float(x) for x in flat]
```
确认：`_extract_bit`（`:306`）→ `_get_weight_value` → `_flatten` 全量物化；`_embed_bit`（`:296`）同样每 bit 一次。嵌入+验证均为逐 bit 循环（`:152-156`、`:185-198`）→ O(num_bits × numel)。claims 里的 `tensor.flat[idx]` / `reshape(-1)[idx].item()` 是建议修复，现状确实没有。torch 张量走 `float(x)` 逐 0-dim tensor 转换，量级与断言一致。LIVE（同一端点，大层将直接表现为请求超时）。

### #7 [high/bug] `layer_positions` 回退路径比特顺序与嵌入顺序不一致 → **TRUE·LATENT**（错位已实测；触发条件比断言描述窄）
证据：`model_watermark.py:184-198`
```
184: if metadata.ordered_positions:      # 嵌入序
190: else:
191:     # Fallback: iterate dict in insertion order (Python 3.7+)
192:     for layer_name, positions in metadata.layer_positions.items():   # 按层分组
```
实测（`{"a":[...40], "b":[...40]}`，20 bit，同一份权重）：`ordered_positions` 存在 → `match_ratio=1.000 passed=True`；置空后走回退 → **`match_ratio=0.600 passed=False`**（该例 `layer_positions` 实际为 `{'b': 12, 'a': 8}`，印证嵌入是交错的）→ 错位→误判为不通过，机制完全成立。`:200` 的长度校验不能拦它（回退总长度仍等于 num_bits），claims 里的 `raise ValueError(...)` 是建议修复。
**修正断言的可达性判断**：“这条错误路径实际会被触发”偏强——现有 API 路径总会写入非空 `ordered_positions`（`training.py:846`），列为 `nullable=False`（`app/models/watermark_record.py:32`）且自 `alembic/versions/0000_baseline_all_tables.py:236` 就存在（无历史空洞行），回读时 `training.py:906` 显式把 `[layer, idx]` 重建为 tuple。回退只能在 `ordered_positions == []` 时发生（例如手工写入的行），叠加需 ≥2 层 → 定 LATENT；缺陷本体成立。

### #8 [high/security] 秘密 `key_seed` 随元数据落盘/下发 → **TRUE·LIVE**（比断言更严重）
证据：
- `model_watermark.py:22` `key_seed: int` 存在 “Persisted metadata” dataclass 里（类注释 `:19-20`），而 `:113` docstring 明写 `key_seed: Secret seed for the PRNG`。
- 选位用的是非密码学 PRNG：`:121 rng = random.Random(key_seed)`（Mersenne Twister）。
- 落盘：`app/models/watermark_record.py:33 key_seed: Mapped[int] = mapped_column(Integer, nullable=False)` 明文列（直接违反本目录 `app/models/AGENTS.md` 的 “Never add a plaintext key/secret column”）。
- **下发**：`app/api/training.py:497 key_seed: int` 是 `WatermarkEmbedResponse` 的响应字段，`:866 key_seed=key_seed` 直接回给 HTTP 调用方。claims 只说“可能随审计记录入库”，实际是确定性的响应体泄露，且 `verify_watermark` 根本不用 `key_seed`（位置全取自 metadata，`:184-198`），即该字段无任何功能必要。另 `random.Random` 非 CSPRNG 部分成立。`key_seed_fingerprint` 是建议方案。

### #21 [medium/bug] 嵌套 list 分支接受 tuple 作内层容器 → **TRUE·LATENT**（实测复现）
证据：`model_watermark.py:280-286`
```
280: if isinstance(tensor, list):
283:     if isinstance(inner, (list, tuple)):
285:             inner[idx - offset] = value
```
实测：`{"a": [(0.1,0.2),(0.3,0.4)]*10}` → **`TypeError: 'tuple' object does not support item assignment`**，且如断言所述它是裸 `TypeError`，不是 `:293` 的 “Unsupported tensor type”。`_flatten:231` 确实接受 tuple 内层，两处标准不一致。LATENT：`training.py` 传入的是 torch/numpy/JSON list（JSON 反只会得到 list），tuple 内层目前无生产入口。

### #22 [medium/performance] `all_positions` 为每个权重创建一个 tuple → **TRUE·LIVE**
证据：`model_watermark.py:129-137` + `:146`
```
129: all_positions: list[tuple[str, int]] = []
131: for name in layer_names:
136:     for idx in range(size):
137:         all_positions.append((name, idx))
146: chosen = rng.sample(range(len(all_positions)), num_bits)
```
确认全量物化（只需 `num_bits` 个，默认 64 / API 可传），而 `rng.sample` 其实只需对 `range(total)` 采样再映射 → 断言成立。LIVE：API 可对真实 checkpoint（上千万参数）调用此端点，先于 #6 就触发内存/CPU 峰值。（claims 的 `layer_prefix` 前缀和方案是建议修复。）

### #23 [medium/bug] 不拒重复层名 / 不检测共享张量 → **TRUE·LIVE**（实测复现假阴性）
证据：`model_watermark.py:124-126` 只查存在性：
```
124: for name in layer_names:
125:     if name not in model_state_dict:
126:         raise ValueError(f"Layer '{name}' not found in model state dict")
```
无重复层名校验，无 `id(tensor)`/存储共享检测；`:146 rng.sample(range(len(all_positions)))` 采的是**列表下标**而非物理位置。实测：`layer_names=["a","a"]`（10 bit）→ `embed.success=True` 先行返回，`verify.match_ratio=0.900 passed=False`（重复物理位被写入两个不同 bit，后写覆盖先写）→ 完全重现断言描述的假阴性。入口开放：`training.py:486 layer_names: list[str] = Field(..., min_length=1)` 无 unique 校验，调用方可直传 `["a","a"]`。tied/共享 embedding 部分同一机制（生产 torch state_dict 常见），无 torch 未能实测。

### #24 [medium/bug] 空水印被判验证通过 → **TRUE·LIVE**（实测复现）
证据：`model_watermark.py:200-210`
```
200: if len(extracted) != len(watermark_bits): ...     # 0 != 0 为假，直接过
205: matches = sum(a == b for a, b in zip(extracted, watermark_bits))
206: ratio = matches / len(watermark_bits) if watermark_bits else 1.0
210: passed=ratio >= self._VERIFY_THRESHOLD,
```
实测：`embed_watermark(state, [], ["a"], 5)` → `success=True`；`verify_watermark(state, [], md)` → **`passed=True match_ratio=1.000`**；`generate_watermark_bits("o","m",0)` → `[]`。入参无校验（`:67-92` 无 `num_bits<=0` 分支；`:139` 只比 `len(all_positions) < num_bits`），`training.py:486-488` 也未限制 `num_bits` 下限→ 空水印可拿到一次“有效”权属结论。claims 的 `if not watermark_bits: raise` 是建议修复。

### #25 [medium/bug] LSB 编码可检测/低精度静默丢位 → **TRUE·LIVE**（float16 实测）
证据：`model_watermark.py:296-303`
```
300: scaled = int(round(w * 1000))
302: scaled = (scaled & ~1) | (bit & 1)
303: cls._set_weight_value(tensor, idx, scaled / 1000.0)   # 无回读校验，无 dtype 考虑
```
确认：（1）写入被量化到 3 位小数，与全精度权重可统计区分，且不需 `key_seed` 即可筛出候选位；（2）实测 `np.float16` 数组（64 元素，16 bit）→ `embed.success=True` 但 `verify.match_ratio=0.438 passed=False` —— 低于随机水平，LSB 完全丢位却报 success，与断言一致。整数 dtype 同理（浮点写回被截断）。`_extract_bit:309` 同样固定 `*1000`，无自适应。claims 里的写后回读 `raise` 是建议修复。

### #47 [low/maintainability] `hex_digest` 死代码 → **TRUE·LIVE**（位于 LIVE 端点调用的 `generate_watermark_bits` 内）
证据：`model_watermark.py:77 hex_digest = sm3_hash(payload)  # 64 hex chars = 256 bits`，全文唯一出现（grep `hex_digest` 仅 77 行）；比特生成循环 `:80-92` 首轮用 `:82 h = sm3_hash(payload + counter.to_bytes(4, "big"))`（counter=0）→ 首轮确实带 4 字节后缀，注释误导读者。断言成立。

### #48 [low/maintainability] `import hashlib` 未使用 → **TRUE·LIVE**
证据：`model_watermark.py:6 import hashlib`；grep `hashlib\.` 全文 **0 命中**（哈希统一走 `:10 from app.utils.crypto import sm3_hash`）。（提醒：该文件内用的是 `sm3_hash`，它缺 gmssl 时回退 SHA-256 是已文档化的 known 问题，本条不涉及。）

### #49 [low/maintainability] `layer_sizes` 死代码 + `hasattr(tensor,"flat")` 永不达 → **TRUE·LIVE**
证据：`model_watermark.py:130 layer_sizes: dict[str, int] = {}` / `:135 layer_sizes[name] = size` —— 全文再无读取（返回的 `WatermarkMetadata` `:158-163` 也不含）；`_set_weight_value:272 if hasattr(tensor, "flat")` 被 `:267`（numpy 必带 `flatten` + `__setitem__`）拦下，永不达——与 #5 同一根因。另 `_flatten:227` 的同名分支同此。断言全部成立。

## app/services/output_inspection.py（现行输出 DLP 闸门）

> 调用面（均为 `app.main` 已挂载路由）：`api/output_control.py:134`、`api/deliveries.py:133/523`、`api/sandbox_sessions.py:1925/2000`，以及共享入口 `output_security.inspect_text_output`（`output_security.py:146`）→ 11 个调用方（`task_pipeline.py:743`、`task_worker.py:139`、`session_stream.py:111`、`gateway_service.py:506`、`output_gateway.py:117`、`unstructured_pipeline.py:622`、`connectors.py:653`、`dev_sandbox.py:278/372`、`sandbox_sessions.py:1418/2481`）。以下每条均附实测（用 `.venv-gate` 直接调 `output_inspector.inspect(...)`）。

### #9 [high/bug] DLP 阶段绕过场景路由 → **TRUE·LATENT**（代码事实完全属实；因四个场景均含 DLP，现行无可观测行为差异）
现状证据：`output_inspection.py:164-177`
```
164: if InspectionStage.DLP.value not in active_stages:
165:     stage_results[InspectionStage.DLP.value] = True
166: dlp_passed = True                                    # ← 无缩进保护，无条件执行
167: for name, (pattern, severity) in self.PATTERNS.items():
168:     matches = pattern.findall(output)
170:         dlp_passed = False
176:         redacted = pattern.sub(f"[REDACTED:{name}]", redacted)
177: stage_results[InspectionStage.DLP.value] = dlp_passed    # ← 覆盖 :165 的 True
```
判定与断言完全一致：扫描/改写/findings 完全不受 `active_stages` 约束，`:165` 的赋值是必被覆盖的死代码。
**两点修正**：（a）影响面暂不可观测——实测 `SCENE_RULES all contain dlp? True`，四个场景（`:113-135`）均含 DLP，未知场景走 `:153 SCENE_RULES.get(..., STRUCTURED_QUERY)` 默认也含 DLP（实测 `sandbox_mode="totally_custom_scene"` 仍产生 `dlp_scan=False`），所以「不启用 DLP 的场景」今天不存在；一旦新增不启 DLP 的场景即立即生效。（b）claims 引的 `pass  # TODO: 将下方 for 循环整体置于本 if 内` 在本文件**不存在**（是建议方案）。

### #10 [high/security] DLP 命中原文写入 `findings.samples` → **TRUE·LIVE**（实测复现，二次泄露）
证据：`output_inspection.py:171-176`（关键行 `174: samples=matches[:3]`）——`matches` 是 `pattern.findall(output)` 的**原始命中串**，本文件全文无任何 `_mask`/掩码函数。
实测：输入含身份证号→ `passed=False`，`findings[].samples = [['110101199003077758'], ['110101199003077758'], ['13800138000'], ['a@b.com']]` —— 完整身份证、手机号、邮箱原值进入 findings（同一身份证因 `id_card` 与 `social_credit_code` 两条模式各命中一次而**重复出现两次**），而 `redacted_output` 已脱敏为 `[REDACTED:id_card]`。泄露出口真实存在：`api/output_control.py:150` 将 `len(result.findings)` 写审计日志，`output_security.inspection_to_report(inspection)`（11 个调用点）将 findings 结构序列化进 `security_report` 返回给调用方，`session_stream.py:111` 的逐行审查同样携带。高风险成立。

### #11 [high/bug] DP 被启用但 epsilon 缺失时未登记「真空」 → **TRUE·LIVE**（实测复现）
证据：`output_inspection.py:227-229` + `:264`
```
227: if InspectionStage.DP.value in active_stages:
228:     stage_results[InspectionStage.DP.value] = True      # 预置 True
229:     if dp_epsilon is not None and dp_epsilon > 0:
264:     # else: epsilon is None or <= 0 -> DP not applied (honest no-op)
```
`grep -n not_evaluated` 全文仅 5 处：`:185`（DATA_RECONSTRUCTION）、`:209`（K_ANONYMITY）、`:283`（MIA）与 `:148/:330`，**DP 完全未登记**，与断言一致（DATA_RECONSTRUCTION/K_ANONYMITY/MIA 都做了，唯 DP 没有），且直接违反本文件 `:71-74` 自述的 SS-18 §8 真空口径。
实测：`sandbox_mode="query", dp_epsilon=None` → `stage_results['differential_privacy']=True`、`not_evaluated=['data_reconstruction','k_anonymity']`（无 DP）、`dp_applied=False`。判定方（`output_security.should_block`）确实会把「无从检查」读成「DP 通过」。

### #12 [high/security] 未分配预算即可无限次获得「已应用 DP」 → **TRUE·LIVE**
证据：`output_inspection.py:230`（门控）与 `:250-263`（旁路分支）
```
230: if dp_budget_ledger.has_budget_sync(session_id):
250: else:
255:     noisy_rows, _noised = self._apply_dp_noise(output_rows, dp_epsilon)
258:     dp_applied = True
259:     dp_budget_remaining = None
```
（1）不走预算、不记账，却给 `dp_applied=True` + `stage_results[DP]=True`（`:228`），M-03 双预算统一可绕过；（2）`has_budget_sync` 确实是**进程内缓存**：`app/services/dp_budget.py:186 return budget_id in self._loaded or budget_id in self._cache`，且 `dp_budget.py:175-181` 注释自认 “Synchronous allocations/consumptions are therefore process-local (they survive the request, **not a restart**)” → 断言的「重启后缓存清空→旁路重开」有模块自身注释作证。调用方可部分辨识（`dp_budget_remaining=None` 在 `:63-66` 有文档，`:260-263` 有 `logger.info` 披露），但 **`not_evaluated`/`degraded` 两字段均无记录**（与 #11 同一口径违反），因此以结果对象作判断的下游（`should_block`、`inspection_to_report`）无从区分。

### #13 [high/security] 签名密钥类级惰初始化无锁 + 不落盘不可抵赖 → **TRUE·LIVE**（重启换钥已实测）
现状证据：`output_inspection.py:83`、`:300-305`
```
 83: _signing_keypair = None   # Lazy-initialized SM2 keypair for signing
301: if OutputInspector._signing_keypair is None:
302:     OutputInspector._signing_keypair = crypto_service.generate_keypair()
```
（1）**无锁 check-then-set**：`grep "_SIGNING_KEY_LOCK|threading" app/services/output_inspection.py` → **无任何输出**，本文件根本没有锁（claims 引的 `with _SIGNING_KEY_LOCK:` 不存在，那是建议修复），也无 KMS/配置加载。TOCTOU 成立；`inspect()` 是同步方法，`deliveries.py:124 _review_output_bytes`、`task_worker.py:139` 等同步路径由 FastAPI 线程池执行，多线程首次并发可各自生成不同密钥对。
（2）**不可抵赖不成立（已实测）**：把类变量置回 None 后重新签名，新密钥对 `private_key` 与旧的不同，同一段文本的 `signature` 也不同 → 进程重启即换钥、无公钥分发，任何人事后都能生成同构签名。另 `:302` 用的是 `crypto_service.generate_keypair()`，而非任何 `load_or_generate_*`，确认无持久化。

### #26 [medium/security] k-匿名违规把准标识符原值带进 findings → **TRUE·LIVE**（实测复现）
证据：`output_inspection.py:473`（描述串直接内插原值）+ `:205`（写入 samples）
```
473: f"Group {qi_values} has {count} rows (min: {k_min})"
466: qi_values = tuple(str(row.get(col, "")) for col in quasi_identifiers)
205: samples=violations[:3],
```
实测单行输出 `{name:张三, age:30, addr:北京市海淀区中关村大街1号}` → `findings[0].samples = ["Group ('张三', '30', '北京市海淀区中关村大街1号') has 1 rows (min: 5)"]`。因为 `quasi_identifiers is None` 时默认取**全部列**（`:459`），这条样本等于把个体级准标识符组合原文回显；出口同 #10（`inspection_to_report` 序列化、审计日志）。断言属实（`_mask_violation` 是建议方案）。

### #27 [medium/bug] DLP 门禁完全忽略 severity + IP 正则无八位组校验 → **TRUE·LIVE**（例子需修正）
证据：`output_inspection.py:170`（任何命中无条件 `dlp_passed = False`）+ `:110`（`"ip_address": (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "low")`）+ `:319 passed = all(stage_results.values())`。severity 仅存入 finding 供展示，对放行判定零影响。
实测：`inspect("connect to 999.999.999.999 failed", ..., sandbox_mode="develop")` → `passed=False dlp_scan=False`，唯一 finding 为 `('low', 'ip_address')` —— 一个非法 IP 就能阻断整个输出，与断言一致。
**修正断言举例**：`v1.2.3.4` **不会**命中（实测 `[]`，前面的 `v` 是词字符使 `\b` 不成立），`10.20.30.40ms` 也不命中（尾随 `ms`）；而 `server at 1.2.3.4 ok` → `['1.2.3.4']`、`time 12.30.45.6` → `['12.30.45.6']`。即“版本号阻断”仅在版本号被非词字符包围时成立，但“`999.999.999.999` 也命中、low 级别也能阻断”这两点完全成立。

### #28 [medium/bug] `output=None` 导致整个请求 500 → **TRUE·LATENT**（异常已复现，但无调用方能传入 None）
机制成立：`:150 redacted = output` 后无任何类型守卫，Stage 1 在 `:159 if not output or len(output.strip()) == 0` 确实允许 None，而 DLP 循环无条件跑。
实测：`O.inspect(None, "u1", "s1", sandbox_mode="develop")` → **`TypeError: expected string or bytes-like object, got 'NoneType'`**（抛自 `:168 pattern.findall(output)`），异常不被 `inspect` 内部捕获。
为何定 LATENT：逐个核对了全部 18 个调用点，均保证 str：`api/output_control.py:98 InspectRequest.output: str`（None 在 pydantic 层 422）；`deliveries.py:129`/`sandbox_sessions.py:1924,1999` 是 `bytes.decode()`；`sandbox_sessions.py:1411-1415` 已显式做 `if output is None: output = ""` 与 `isinstance` 兵归；`task_pipeline.py:731+742`、`task_worker.py:137-138`、`session_stream.py:109-110`、`unstructured_pipeline.py:623`、`gateway_service.py:507`、`output_gateway.py:103` 都有 `or ""`/json 序列化/非空守卫。属「入口靠调用方自律」的纵深防御缺口，非现行 500 源。

### #29 [medium/bug] `output_rows = noisy_rows` 死赋值 + 零单元格加噪仍报 `dp_applied=True` → **TRUE·LIVE**
证据：`output_inspection.py:241-244`、`:255-258`、`:328`
```
241: noisy_rows, _noised = self._apply_dp_noise(output_rows, dp_epsilon)
242: if output_rows is not None:
243:     output_rows = noisy_rows          # 死赋值
244: dp_applied = True                     # 与 _noised 无关
328: noisy_output_rows=noisy_rows,       # 唯一向外的噪后载体
```
`grep -n output_rows` 全文显示 `:243/:257` 对局部变量的赋值**后续再无任何读取**（`inspect` 的下一次 `output_rows` 使用在 `:337` 之后的 `_apply_dp_noise` 签名里，不同作用域）；结果只通过 `noisy_output_rows`（`:328`）外泄，而 `redacted_output`（`:323`）始终是**未加噪文本**（噪声只作用于结构化行）。实测 `_apply_dp_noise(None, 0.5) → (None, 0)` 而分支仍走 `:258 dp_applied = True`，与 `:213` 注释 “dp_applied must reflect what actually happened” 直接矛盾（自由文本输出 0 个单元格被扰动却对外声称已加 DP，且还占了预算分支）。两处均成立，`num_noised > 0` 是建议方案。

### #30 [medium/bug] `consume` 返回 False 一律当成预算耗尽 + check-then-act 竞态 → **TRUE·LIVE**
证据链：`output_inspection.py:230-239` → `:556-558 dp_engine.consume` → `dp_budget.py:212-233`。`consume_sync` 有**三个不同的 False 出口**：
```
dp_budget.py:223: if epsilon <= settings.DP_MIN_EPSILON_CONSUMPTION: return False
dp_budget.py:226: if max_per is not None and epsilon > float(max_per): return False
dp_budget.py:229: if remaining < epsilon: return False
```
而 `:231 if not dp_engine.consume(...)` 不区分原因，统一写 `type="dp_budget_exhausted"` + `f"DP epsilon budget exhausted (remaining=…requested=…)"`（`:236-238`）→ 参数非法被说成“没预算”，且该分支**跳过加噪**（`:241` 根本不执行）。另：`dp_budget.py:228-231` 是 `remaining = self._cache.get(...)` → `self._cache[budget_id] = remaining - epsilon` 的裸 dict 读-改-写，**全程无锁**，叠加 `output_inspection.py:230→:231` 的 check-then-act，并发超额消费竞态属实。（行为上仍 fail-closed：`:233` 把 DP 阶段置 False，不会错放。）

### #31 [medium/error handling] 签名阶段 fail-open 且异常静默 → **TRUE·LIVE**（已强制触发验证）
证据：`output_inspection.py:300-313`（`307: except Exception:` 无 `logger.*`，全文 `logger` 在签名区零调用）
实测（monkeypatch `crypto_service.sign` 抛 `RuntimeError("hsm down")`）：`stage_results['signature'] = True`、`passed = True`、`signature = 'edbc74a85573975c6abe647f71b50a8e0a847df6bf010d52353c94e2b9c6b3d8'`（64 hex = **SM3 摘要**，不是 hex r‖s 的 SM2 签名，与 `:69` 字段注释 “SM2 digital signature (hex r‖s)” 相左），仅 `degraded` 多一行文字；生产上因无 `logger.exception` 而无法定位密钥/HSM 故障。
实测第二点：再让 `except` 内的 `crypto_service.sm3_hash`（`:309`）报错 → **`RuntimeError` 直接逸出 `inspect()`**（即请求 500）。该场景现实存在：`sm3_hash` 在 gmssl 缺失且 `CDS_SM3_FALLBACK_ALLOWED=false` 时按项目既定行为抛 `RuntimeError`（known）——即“SM2 不可用”与“SM3 不可用”同时发生时，本应降级的阶段反而把异常抛给调用方。

### #32 [medium/security] MIA 检测默认 fail-open → **TRUE·LIVE**（实测复现）
证据：`output_inspection.py:496-501`
```
496: confidence = model_output.get("confidence", 0.0)
497: loss = model_output.get("loss", float("inf"))
500: risk_score = confidence * (1.0 / (1.0 + loss)) if loss > 0 else confidence
501: is_safe = risk_score < threshold
```
实测：`check_mia_risk({}, 100)` → **`(True, 0.0)`**（缺失字段 → risk 塔陷为 0 → “安全”），而 `:272-273` 直接把 `is_safe` 写进 `stage_results[MIA]` → 阶段通过。另实测字段为 JSON 透传字符串时 `check_mia_risk({"confidence":"0.99","loss":"1.0"}, 100)` → **`TypeError: '>' not supported between instances of 'str' and 'int'`**（抛自 `:500` 的 `if loss > 0`），无类型校验。另 `:281-283` 对「根本无 model_output」倒是做了 `not_evaluated` 登记，唯独「有 model_output 但字段缺」这种更值得疑的情况归为安全，与断言一致。

### #33 [medium/bug] 重构检测按位置序列比较，改列序即绕过 → **TRUE·LIVE**（实测复现）
证据：`output_inspection.py:391` 与 `:397`
```
391: source_set.add(tuple(str(v) for v in row.values()))      # 按 dict 插入顺序，非列名对齐
397: row_tuple = tuple(str(v) for v in row.values())
398: if row_tuple in source_set: matches += 1
```
实测：`source_rows=[{"a":1,"b":2.0,"c":"007"}]*10` 对比（a）同序同行 → `(False, 1.0)` 拦；（b）**仅调列序** → **`(True, 0.0)` 放行**；（c）`b:2` vs `2.0`、`c:"007"` vs `7` → **`(True, 0.0)` 放行**。投影（少列）同理必不相等。绕过成本为零，与断言一致。
语义不一致也属实：docstring `:376-377` 写 “Compares output rows against source rows at field level”，实现是**整行元组全等**；真正的逐字段检测另有一个 `check_field_reconstruction`（`:404-433`，集交口径），未被 `inspect` 的主阶段调用。claims 里的 `frozenset((k, str(v)))` 是建议方案（按列名集对齐），现行代码确实不做列名对齐。

### #50 [low/bug] 拉普拉斯噪声函数缺参数/边界防护 → **TRUE·LIVE**（其中一点需修正）
证据：`output_inspection.py:560-568`
```
560: def add_laplace_noise(self, value, sensitivity, epsilon):
562:     import random
563:     scale = sensitivity / epsilon
565:     u = random.random() - 0.5
566:     noise = -scale * (1 if u < 0 else -1) * math.log(1 - 2 * abs(u)) if u != 0 else 0
```
（1）`epsilon=0` → 实测 `dp_engine.add_laplace_noise(1.0, 1.0, 0.0)` → **`ZeroDivisionError: float division by zero`**。且它确实可从 HTTP 直达：`api/output_control.py:177 dp_engine.add_laplace_noise(body.value, body.sensitivity, body.epsilon)`，而 `DPNoiseRequest`（`output_control.py:103-108`）对 `epsilon: float` **无任何约束**（无 `gt=0`）→ 传 0 即 500，LIVE。（2）`u == 0` 有兵底，但 `u == -0.5`（`random.random()` 返回 `0.0`，值域 `[0.0, 1.0)` 包含 0）无兵底 → `math.log(0)` → `ValueError`；概率≈2⁻⁵³，真实但极端稀疏。（3）隐私机制用 MT 非 CSPRNG（`:562`）成立。（4）**需修正**：`add_laplace_noise` 函数体内**只有 `import random`**，没有 `import math`；冗余的 `import math` 在另一个方法 `add_gaussian_noise`（`:571-572`）里，不在本热路径上。“每单元格调用一次”属实（`:359` 在逐 key 循环内）。

### #51 [low/maintainability] `check_k_anonymity` 用 `rows[0].keys()` 推定 QI 集 → **TRUE·LIVE**（query 场景下由 `inspect()` 的 `:198` 真实调用）
证据：`output_inspection.py:457-459`（`if quasi_identifiers is None: quasi_identifiers = list(rows[0].keys())`）+ `:466 str(row.get(col, ""))`。
后续行缺列 → 取到 `""` 并与其他缺该列的行归入同组→虚增组大小→漏报；`str()` 确实将 `None`/`""`/`0`/`"0"` 分列为 `"None"`/`""`/`"0"`/`"0"` —— 其中 `0` 与 `"0"` 完全同组，断言的“混为一谈”在 `0`/`"0"` 与空值族上成立（`None`→`"None"` 自成一组，断言此处略宽）。另注意 `:197` 只在 `output_rows` 非空时跑，故异构行风险只来自上游给的部分行。

### #52 [low/maintainability] `hashlib` 与 `json` 未使用 → **TRUE·LIVE**
证据：`output_inspection.py:7 import hashlib`、`:8 import json`；全文 grep `hashlib` / `json` **各自只命中这一行 import**，无任何引用（水印走 `:13-21 app.utils.watermark`，预算走 `:12 dp_budget_ledger`）。注意本模块的水印/签名与 model_watermark 无关，claims 对模块边界的描述正确。

### #53 [low/maintainability] `MIARiskLevel` 导入未用且在方法体内 → **TRUE·LIVE**（且比断言更严重）
证据：`output_inspection.py:268 from app.services.mia_probe import mia_probe, MIARiskLevel`（在 `inspect()` 体内）。
实测 grep：`MIARiskLevel` 全文**仅这一行**→ 未使用。补充：同一行的 **`mia_probe` 实例也未使用**（本文件唯一使用 MIA 的是自写的 `check_mia_risk`，`:478-503`，根本不调 `MIAProbe`），所以这两个导入名字都是死的。另该 `import` 位于 `if InspectionStage.MIA.value in active_stages` 内（`:267`），并非“每次 inspect 都执行”——这一句断言不准，但“未使用符号”的主结论成立。

## app/services/pii_ner.py

> **整个模块 TRUE·LATENT**：唯一 importer 是 `app/services/stream_audit.py:19`，而 `stream_audit` 在 `app/` 内 0 个引用方（`grep -rn "stream_audit" app/ --include=*.py` 除自身外无输出），`app.main` 不可达，且两文件内无 `importlib`/`__import__`。本组所有实测都是直接 `import app.services.pii_ner` 跑的（模块本身可导入，缺陷均可复现），但**生产上没人调用它**。

### #14 [high] `ner_engine="regex"/"off"/"none"` 时 NER 层仍照跑 → **TRUE·LATENT**（实测复现）
证据：`_resolve_engine` 在 `:219 if engine in {"regex", "off", "none"}: return "regex_only"`（`:206`），但 `detect()` 的门控只看 `enable_ner`：
```
289: if self.enable_ner:                      # 不看 _active_engine 是否为 regex_only
325: def _detect_ner(...):  → 335: return self._detect_ner_regex(...)   # 非 lac/transformers 一律走 rule
306: ner_engine=self._active_engine,          # 报 "regex_only"
```
与类注释 `:163`（`"regex"/"off": disable the NER layer`）直接矛盾。实测：`PIINERService(enable_ner=True, ner_engine="off").detect("联系人张三 邮箱 … 住址不详…")` → **`ner_engine = "regex_only"`，而 `layers = ['ner','regex']`、types 含 `person_name`/`address`**。单例确实就是这个组合：`:576-582 enable_ner=True, ner_engine=_settings.PII_NER_ENGINE`（`app/core/config.py:273` 默认 `"auto"`，改为 `off` 即命中）；`except Exception:` 分支（`:584`）的默认构造也是 `enable_ner=True`。T9 诚实披露契约（`:57-64`、`.omo/plans/t11-rag-phase1.md:51` 将它列为先例）在此失效，且后果是双向的：消费者既会误以为“没有规则降噪发生过”，又拿不到真实的 `rule` 标签。

### #15 [high] 重叠消解只记录单一 `last_end`，部分重叠匹配整条丢弃 → `redacted_text` 泄露 → **TRUE·LATENT**（三种丢法均已实测）
证据：`output` 无关，本文件 `:539-549`
```
539: last_end = -1
542: if m.start >= last_end:
543:     result.append(m); last_end = m.end
547:     if result and severity_order.get(m.severity,99) < severity_order.get(result[-1].severity,99):
548:         result[-1] = m
549:         last_end = m.end
```
用真实 `_resolve_overlaps` + `_redact`（输入文本 `"0123456789"*6`）实测三种情形：
- **A（低严重度被丢）** ADDRESS[HIGH]`[0,15)` + EMAIL[MEDIUM]`[10,40)` → 仅保留 `[0,15)`，**`[15,40)` 这 25 个字符在 `redacted_text` 中明文残留**（redacted 开头为 `[REDACTED:address]56789012…`）。
- **B（替换不取并集）** PHONE[HIGH]`[0,20)` 被 ID_CARD[CRITICAL]`[10,18)` 取代 → **`[0,10)` 与 `[18,20)` 均不再脱敏**，redacted 字面开头就是 `0123456789` —— 即断言的“手机号前几位残留”。
- **C（同严重度丢更长的）** ADDRESS[HIGH]`[0,10)` 与 ADDRESS[HIGH]`[5,40)`（`:296` 按 `(start,end)` 升序，短者在前）→ 保留 `[0,10)`，**`[10,40)` 30 字符泄露**。
三种都导致 `has_pii=True` 但 `redacted_text` 不完整。另外 `matches.sort(key=(m.start, m.end))` 后的稳定排序使“同严重度保留首个”成为确定行为，与 `:527` docstring “keeping higher severity ones” 的表述不符（实际是“首个存活”）。claims 里的区间并集方案是建议修复。

### #16 [high] LAC 推理解析异常回退到 rule，`ner_engine` 仍报 `lac` → **TRUE·LATENT**（实测复现）
证据：`:345-346 if not self._lac: return self._detect_ner_regex(...)`、`:380-382 except Exception as e: logger.warning("LAC NER inference failed…"); return self._detect_ner_regex(...)`，而返回体统一用 `:306 ner_engine=self._active_engine`。
实测（注入假 `_lac`，`run()` 抛 `RuntimeError("model crashed")`）→ **`ner_engine = "lac"`，而 `layers = ['ner','regex']`**，即纯规则命中被标为模型 NER，直接违反 `:62-64` 的“never a model label”。另 `PIIMatch.layer` 注释 `:50` 写 `# "regex" or "ner"`，而实际会取 `ner_lac`（`:377`）/ `ner_ml`（`:423`）—— 与实测一致（消费者按注释枚举解析 `layer` 会碰到未知值）。

### #17 [high] transformers 加载成功后推理异常同样谎报 `transformers`；下标访问 KeyError 被宽 `except` 吞掉 → **TRUE·LATENT**
证据：`_detect_ner_ml:386-428`
```
411: start = int(ent["start"])
412: end = int(ent["end"])
419: value=ent["word"],
424: confidence=float(ent["score"]),
426: except Exception as e:
428:     return self._detect_ner_regex(text, regex_matches)
```
全部为裸下标（只有 `score`/`entity_group` 用了 `.get()`），不同 transformers 版本的 `word`/`start`/`end`/`score` 改名 → `KeyError` 落入 `:426` 的宽 `except` → **整个模型的输出被丢弃**并静默改走规则，而 `ner_engine` 依旧报 `transformers`。与 #16 同一根因（披露取 `_active_engine` 而非实际路径），但多一个“部分/全部结果静默丢失”的放大面。本环境无 transformers，未能实跑，但 `:411-424` 的访问方式与 `:426` 的异常范围已从代码确认。

### #34 [medium] 模块导入副作用：构造即加载模型 → **TRUE·LATENT**
证据：`:204 self._active_engine = self._resolve_engine(ner_engine)` → `:222/:225`（auto 分支调 `_load_load/_load_transformers`）→ `:573-582` 模块尾构造单例 → **`import app.services.pii_ner` 即在 import 时尝试 `from LAC import LAC` / `transformers.pipeline(...)`**（后者会下载 `bert-base-chinese-pii-ner`，`config.py:275`）。实测日志直接作证：每次 `PIINERService()` 构造都打印 `LAC NER load failed, falling back to rule NER: No module named 'LAC'`（共 6 次，对应 6 次构造）。而两个加载器的 docstring 自述为 “**Lazily** load the Baidu LAC model / HuggingFace NER pipeline”，与实际时机矛盾。“模型只能从构造函数注入”成立。本文件无 `threading`，也无任何锁（实测 `hasattr(M,'threading') = False`）。

### #35 [medium] 模块尾 `except Exception:` 无日志静默降级 → **TRUE·LATENT**
证据：`:583-584`
```
583: except Exception:
584:     pii_ner_service = PIINERService()
```
无任何 `logger.*`；`get_settings()` 失败（配置校验 raise）与模型加载失败被同一路径吞掉，并默默回到默认 `ner_engine="auto"`（`config.py:273`）——即“管理员把引擎设为 rule/regex/off，配置读失败后行为静默变回 auto 并可能尝试加载模型”。且 `PIINERService()` 自身又走一次 `_resolve_engine` → 再跑一次 `import LAC`（与 #34 同一副作用）。全文件共 3 处 `except Exception`（`:247`、`:265`、`:583`），前两处**是有** `logger.warning(…, e)` 的（带异常信息），只有模块尾这一处彻底静默 —— claims 对范围的描述准确。

### #36 [medium] LAC 偏移回填对齐失败时不推进 `pos` 也不告警 → **TRUE·LATENT（实际比断言更糟，已实测）**
证据：`:358-366`
```
358: if not mapped:
359:     pos = text.find(word, pos)      # 未找到时把 pos 赋成 **-1**
360:     if pos >= 0:
361:         pos += len(word)
364: start = text.find(word, pos)      # 已打标分支
365: if start < 0:
366:     continue                      # 不推进 pos、不告警
```
**修正断言的机制描述**：未打标分支不是“保持旧偏移”，而是把 `pos` **赋成 -1**，Python 语义下 `text.find(w, -1)` 从最后一个字符开始搜 → 后续所有词元几乎全部失效。用假 `_lac` 实测：
- 漏检：`(["XYZ","北京"], ["O","GPE"])` 对文本 `"北京"` → **`ner_lac` 匹配为空**（北京的 GPE 标签完全丢失）。
- 错定位：`(["XYZ","京"], ["O","GPE"])` 对文本 `"北京南站 京"` → 匹配落在 **(5,6)**（末尾那个“京”），而非首次出现的位置 1；`redacted_text = "北京南站 [REDACTED:address]"` → **真正被脱敏的是错误的那个字**。
- 已打标分支（`:365-366`）不推进 `pos`，同一词重复出现时可绑到**更早**的出现位置（实测 `(["甲","ZZZ","京"],["PER","PER","GPE"])` → 京 绑到 (1,2)）。两种都会造成漏脱敏或错脱敏，且全程无一条告警。

### #37 [medium] 共享模型实例无并发保护 → **TRUE·LATENT（推理成立，无现行入口）**
证据：模块级单例 `:576`/`:584`；`self._lac`（`:345` 读、`:340` 写）与 `self._ml_pipeline`（`:402` 读、`:258` 写）都是实例态共享，本文件 **0 处 `threading`/`Lock`**（grep 无命中，实测 `hasattr(M,'threading')=False`）。`stream_audit.py:98` 以 `pii_ner_service.detect(chunk.data)` 共享同一实例。PaddlePaddle 静态图 predictor / HF pipeline 均非线程安全，此判断属业界常识，代码层无任何防护手段；但因本模块无生产调用方（见本节开头），并发入口目前不存在 → LATENT。另：`_load_lac`/`_load_transformers` 的 `if self._lac is not None` 检查与赋值也非原子（与 #34 的导入期加载叠加后，重复构造模型风险真实存在）。

### #38 [medium] 三处重复的逐字符位置集合 → **TRUE·LATENT**
证据：`_detect_ner_lac:349-351`、`_detect_ner_ml:389-391`、`_detect_ner_regex:437-439` 三处逐字相同：
```
349: regex_positions: set[int] = set()
351:     regex_positions.update(range(m.start, m.end))
```
再用 `any(pos in regex_positions for pos in range(start, end))` 逐字符比对（`:369`、`:414`、`:449`、`:471`、`:491`、`:511` 共 6 个判定点）。确认：内存与命中总长线性（每字符一个 int ≈ 28 B）、判定与被扫描文本长度相关而非命中数，且 `detect()` 对每条新文本重建集合（`session_stream.py:111` 是逐行审查，行数越多重建越频繁）。区间表是建议方案。LATENT 仅因无调用方。

### #39 [medium] `DOB_PATTERNS` 第 2 条匹配一切 `YYYY年M月D日` → **TRUE·LATENT**（实测复现）
证据：`:152 re.compile(r"(\d{4}年\d{1,2}月\d{1,2}日)")` —— 无任何“出生/生日”上下文（第 1 条 `:150` 才有）。
实测：`"本合同自2024年1月1日起实施，有效期至2026年12月31日"` → **两条 `date_of_birth`**（`2024年1月1日`、`2026年12月31日`），`severity=medium`、`confidence=0.9`、`layer=ner`；`redacted_text` 把两个合同日期都掉了。断言属实，并证实 `:62-64` 的诚实披露只解决了“引擎标签”、未解决误报率。

### #40 [medium] LAC 的 TIME 标签映射为 `DATE_OF_BIRTH` → **TRUE·LATENT**
证据：`:185 "TIME": (PIIType.DATE_OF_BIRTH, Severity.MEDIUM, 0.9)`。LAC 的 TIME 涵盖任意时间表达（“下午三点”“去年”等），与出生日期无关 → 任何走 LAC 的时间实体都会变成 `date_of_birth` 并被 `[REDACTED:date_of_birth]` 掉。与 #39 同族（标签语义错配），无 LAC 环境未能实跑，但映射表是硬证据。

### #41 [medium] 组织模式过度捕获 → **TRUE·LATENT**（机制与断言不同，现象已实测）
证据：`:142` 与 `:144`
```
142: ([一-龥]{2,20}(?:公司|集团|有限公司|…|政府|局|厅|部|委|处|所|中心|协会|基金会))
144: (?:单位|公司|机构|组织)[：:\s]*([一-龥]{2,30})
```
实测：
- `"公司成立于二零一零年并专注于智能数据处理技术的研发与推广服务已有十年历史"` → 一条 `organization` 值为 **`公司成立于二零一零年并专注于智能数据处`（19 字，:142 贪婪前缀 + 单字后缀“处”）**，整段被 `[REDACTED:organization]` 掉。
- `"华为技术有限公司是一家总部位于深圳市的…"` → 值为 `华为技术有限公司是一家总部`（13 字，尾部“部”命中后缀表）——同时证实断言指出的“后缀表里的 `公司` 会参入第二次匹配”（值从 0 开始却越过真正的公司名）。
- `:144` 直接单测：纯中文输入下捕获长度恰为 **30 字**（贪婪上限），无终止边界。
**修正**：断言举的 `"公司成立于2010年…"` 实测只捕获 3 字（`成立于`），因为 `2010` 是数字、`[一-龥]` 不匹配；“贪婪到 30 字”在纯中文叙述里成立。另外实测到的主要过度捕获其实来自 `:142`（单字后缀 `部/处/局/所/厅/委`）而非 `:144`。高误报结论成立。

### #42 [medium] 地址模式 2 贪婪 5-50 字且不要求地址后缀 → **TRUE·LATENT**（实测复现，与断言同例）
证据：`:136 re.compile(r"(?:地址|住址|住所)[：:\s]*([一-龥0-9]{5,50})")` —— 只要求前缀词 + ≥5 个连续中文字符/数字，字符类可吞顿号以外的整句。
实测：`"住址不详请联系客服中心尽快处理谢谢合作"` → `address = "不详请联系客服中心尽快处理谢谢合作"`（17 字，`severity=high`），`redacted_text = "住址[REDACTED:address]"` —— 整句被销毁，与断言逐字一致。与 #41 同源（无界贪婪捕获），`[：:\s]*` 可跳过任意个分隔符、地址段与其他实体无法分隔也属实。

### #54 [low] 银行卡模式无 Luhn 校验 → **TRUE·LATENT**（实测复现）
证据：`:98-99 ("bank_card", PIIType.BANK_CARD, re.compile(r"(?<!\d)[3-6]\d{15,18}(?!\d)"), Severity.HIGH)` —— 无 Luhn、无长度-IIN 组合校验。
实测：`"订单号 6222021381100001234 已发货"` → `bank_card` 命中 `6222021381100001234`（19 位），`severity=high`、`confidence=1.0`、`layer=regex`，整号被脱敏。另：18 位身份证若首字在 `3-6`（如 `44030…` 广东）**不会**误归为银行卡——正则列表把 `id_card` 排在 `:90`（CRITICAL 优先），`_resolve_overlaps` 会剔除同一区间的 bank_card 候选（同一机制在 `output_inspection.PATTERNS` 里靠顺序与 severity 完成）；断言只讲“纯数字序列”这一面，未误判身份证，结论成立。

## Score

| file | claims | TRUE·LIVE | TRUE·LATENT | FALSE |
|---|---|---|---|---|
| `app/services/mia_probe.py` | 11 (#1-4, #18-20, #43-46) | 1 | 10 | 0 |
| `app/services/model_watermark.py` | 12 (#5-8, #21-25, #47-49) | 10 | 2 | 0 |
| `app/services/output_inspection.py` | 17 (#9-13, #26-33, #50-53) | 15 | 2 | 0 |
| `app/services/pii_ner.py` | 14 (#14-17, #34-42, #54) | 0 | 14 | 0 |
| **合计** | **54** | **26** | **28** | **0** |

分档补充：
- **FALSE = 0**：54 条没有一条被驳斥。所有引用行号不精确之处均为「`--` 后的代码块是建议修复而非现状」；真正的引用错误（3 条）已在正文逐条更正：#20 虚构了 `logger.warning(…p75…)`、#9 虚构了 `pass # TODO:`、#13 虚构了 `_SIGNING_KEY_LOCK`。
- **dup**：#2 后半（“100% 疑似被压成负数→SAFE”）dup of #1；#49 的 `hasattr(tensor,"flat")` 不可达与 #5 同根因；#16/#17 同一根因（披露取 `_active_engine` 而非实际路径）；#12/#11 同一根因（`not_evaluated`/`degraded` 未登记）。
- **known**：无一条以复述已文档化事实为主旨。#48 涉及 `sm3_hash`、#31 的降级分支依赖 gmssl 缺失时 `sm3_hash` 可 raise（known）——两条的结论本身都是新发现。
- **最高优先级（均为实测复现的 LIVE）**：#5 numpy 静默丢水印（写 0/64 却报 success）、#8 `key_seed` 明文入库并在 `training.py:866` 的 HTTP 响应体回传、#10/#26 PII 原值进 `findings.samples`、#33 改列序零成本绕过重构检测、#31 签名假通过且降级函数报错时直接 500、#13 签名密钥重启即换→不可抵赖不成立、#11/#12 DP 真空与预算旁路。
- **未达预期的一项**：#9（DLP 绕过场景路由）与 #28（`output=None` 500）代码事实均成立，但前者因四个场景均含 DLP 而今日无可观测行为差异，后者因 18 个调用点均保证 str 而无现行入口；#7、#21 同理（回退分支/tuple 内层无现行入口）。
