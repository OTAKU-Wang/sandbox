# Claims for app/services/model_watermark.py (12)

## C1 [critical/bug]
LOCATED: true  fileExists: true
EXISTING CODE SNIPPET (as seen by the tool, may be stale):
```
# torch tensor
        if hasattr(tensor, "flatten") and hasattr(tensor, "__setitem__"):
            flat = tensor.flatten()
            flat[idx] = value
            return
```
CLAIM:
`_set_weight_value` 的第一个分支会把 numpy 数组误判成 torch 张量，导致水印写入被静默丢弃：`numpy.ndarray` 同时具备 `flatten` 和 `__setitem__`，因此会进入此分支；而 numpy 的 `flatten()` **总是返回副本**（不同于 `ravel()`），`flat[idx] = value` 只改到临时数组，原始 `model_state_dict` 完全没被修改。下方 `hasattr(tensor, "flat")` 的 numpy 分支因此成为死代码。同理，torch 非连续张量（如 `t.T`、channels_last 视图）的 `flatten()` 也返回副本，写入同样丢失。结果是 `embed_watermark` 返回 `success=True`，但 `verify_watermark` 只能读到原始权重，验证必然失败。建议按类型（而非鸭子类型属性）分派，并写后校验：numpy 用 `tensor.flat[idx] = value` 或 `tensor.reshape(-1)[idx]`，torch 用 `tensor.reshape(-1)[idx] = value`（reshape 对非连续张量同样会复制，应先 `tensor = tensor.contiguous()` 或用 `tensor.view(-1)` 前断言 `is_contiguous()`），最稳妥的做法是写完后立即 `_extract_bit` 回读断言。
SUGGESTED FIX (tool): # torch tensor: 必须保证写回原张量（flatten 对非连续张量返回副本）
        if hasattr(tensor, "numel") and hasattr(tensor, "view"):
            if not tensor.is_contiguous():
                raise ValueError("Non-contiguous tensor: call .contiguous() before embedding")
            tensor.view(-1)[idx] = value
            return
        # numpy array: flatten() 返回副本，必须用 .flat 视图写入
        if hasattr(tensor, "flat"):
            tensor.flat[idx] = value
            return

## C2 [high/performance]
LOCATED: true  fileExists: true
EXISTING CODE SNIPPET (as seen by the tool, may be stale):
```
flat = cls._flatten(tensor)
        return flat[idx]
```
CLAIM:
每次读取单个 bit 都会走 `_flatten(tensor)`：对整个张量调用 `flatten()`（torch/numpy 都会整块复制），再把**全部元素**逐个 `float()` 转成 Python list。嵌入与验证是逐 bit 调用的，总代价为 O(num_bits × 层元素数)；对一个 1e7 元素的层、64 bit，就是约 6.4e8 次元素拷贝与 `float()` 调用（对 torch 还是逐个 0-dim tensor 的 `float()`，极其缓慢）。应改为按扁平索引直接取值：numpy 用 `tensor.flat[idx]`，torch 用 `tensor.detach().reshape(-1)[idx].item()`，list 直接下标；仅在必要时才 fallback 到 `_flatten`（且每个张量只展平一次并缓存）。
SUGGESTED FIX (tool): if hasattr(tensor, "flat"):  # numpy: 视图式扁平访问，不复制
            return float(tensor.flat[idx])
        if hasattr(tensor, "detach"):  # torch
            return float(tensor.detach().reshape(-1)[idx].item())
        flat = cls._flatten(tensor)
        return flat[idx]

## C3 [high/bug]
LOCATED: true  fileExists: true
EXISTING CODE SNIPPET (as seen by the tool, may be stale):
```
else:
            # Fallback: iterate dict in insertion order (Python 3.7+)
            for layer_name, positions in metadata.layer_positions.items():
```
CLAIM:
回退路径的比特顺序与嵌入顺序不一致，会产生错误结果：`watermark_bits` 的顺序对应 `chosen` 的采样顺序（各层之间是交错的），而 `layer_positions` 是**按层分组**的字典，遍历它得到的是「先层 A 全部位置、再层 B 全部位置」。只要水印位置跨两个及以上层，抽取出的序列就与嵌入序列错位，`match_ratio` 会掉到约随机水平（≈50%），验证被误判为不通过。注意 `ordered_positions` 带 `default_factory=list`，元数据经 JSON 反序列化（tuple 丢失/字段缺省）后很容易退化成空列表，这条错误路径实际会被触发。应在缺少 `ordered_positions` 时按 `key_seed` 重放采样逻辑恢复顺序，而不是按字典分组顺序读取；或干脆在缺失时抛错要求重建元数据。
SUGGESTED FIX (tool): else:
            raise ValueError(
                "metadata.ordered_positions is empty; cannot reconstruct embed order "
                "from layer_positions (per-layer grouping loses bit order)"
            )

## C4 [high/security]
LOCATED: true  fileExists: true
EXISTING CODE SNIPPET (as seen by the tool, may be stale):
```
layer_positions: dict[str, list[int]]  # layer_name -> list of flat indices
    key_seed: int
```
CLAIM:
安全隐患：`key_seed` 在文档中被定义为 *Secret* seed（PRNG 选位秘密），却与 `layer_positions`/`ordered_positions` 一起放进「Persisted metadata」这一可序列化、可下发、可能随审计记录入库的对象里。一旦元数据落盘或随验证响应返回，攻击者即可精确定位并擦除/伪造水印位置，整套方案（包括 95% 阈值判定）失效。同时用 `random.Random`（Mersenne Twister，非密码学 PRNG）做安全相关选位也不合适。建议元数据中不保存种子（只保存其 SM3/HMAC 承诺用于校验），验证时由调用方通过 KMS/HMAC-SM3(KDF) 传入或重新派生位置。
SUGGESTED FIX (tool): layer_positions: dict[str, list[int]]  # layer_name -> list of flat indices
    key_seed_fingerprint: str  # SM3(seed) 承诺，绝不持久化种子本身

## C5 [medium/bug]
LOCATED: true  fileExists: true
EXISTING CODE SNIPPET (as seen by the tool, may be stale):
```
for inner in tensor:
                if isinstance(inner, (list, tuple)):
                    if offset + len(inner) > idx:
                        inner[idx - offset] = value
                        return
```
CLAIM:
嵌套列表分支接受 `tuple` 作为内层容器（`_flatten` 也支持 tuple），但这里对 `inner` 做原地赋值：若 `inner` 是 tuple，会抛出 `TypeError: 'tuple' object does not support item assignment`，错误信息还会落到末尾的 "Unsupported tensor type" 之外，调用方难以定位。建议只接受 list 作为可写内层容器，或显式拒绝 tuple 输入并给出可读错误。
SUGGESTED FIX (tool): for inner in tensor:
                if isinstance(inner, tuple):
                    raise TypeError("Nested tuple weights are immutable; cannot embed watermark")
                if isinstance(inner, list):
                    if offset + len(inner) > idx:
                        inner[idx - offset] = value
                        return

## C6 [medium/performance]
LOCATED: true  fileExists: true
EXISTING CODE SNIPPET (as seen by the tool, may be stale):
```
for idx in range(size):
                all_positions.append((name, idx))
```
CLAIM:
`all_positions` 为**每一个**权重都创建一个 tuple。真实模型动辄千万~上亿参数，这里会构造同样量级的 tuple 列表（每个 tuple 约 72B + list 指针 8B），轻易占用数 GB 内存并造成长时间 GC 停顿；而最终只需要 `num_bits`（默认 64）个位置。正确做法是只采样：先按层把规模累加成前缀和，再用 `rng.sample(range(total), num_bits)` 得到的序号经二分映射到 (layer, idx)，无需物化任何全量列表。
SUGGESTED FIX (tool): # 只记录累计规模，按前缀和映射采样序号，避免物化全量位置列表
            layer_prefix.append((name, layer_prefix[-1][1] + size if layer_prefix else size))

## C7 [medium/bug]
LOCATED: true  fileExists: true
EXISTING CODE SNIPPET (as seen by the tool, may be stale):
```
for name in layer_names:
            tensor = model_state_dict[name]
            # Accept torch tensors, numpy arrays, or nested lists
            size = self._tensor_numel(tensor)
```
CLAIM:
这里只校验层名是否存在，未拒绝重复层名，也未检测多个 state_dict 键引用**同一个张量**（如 tied/共享 embedding、`weight` 与 `_weight_orig` 参数化）。此时 `all_positions` 含重复的物理位置，`rng.sample` 取的是列表下标而非物理位置，可能选中同一权重的两条记录并写入不同 bit，后写覆盖先写；验证时同一物理位被读取两次却对应两个不同期望 bit，必然出现误判（假阴性），且 `success=True` 已先行返回。建议在采样前对 `layer_names` 去重校验并按 `id(tensor)` 排除共享存储的张量。
SUGGESTED FIX (tool): seen_ids: set[int] = set()
        for name in layer_names:
            tensor = model_state_dict[name]
            if id(tensor) in seen_ids:
                raise ValueError(f"Layer '{name}' shares storage with an already selected layer")
            seen_ids.add(id(tensor))
            size = self._tensor_numel(tensor)

## C8 [medium/bug]
LOCATED: true  fileExists: true
EXISTING CODE SNIPPET (as seen by the tool, may be stale):
```
ratio = matches / len(watermark_bits) if watermark_bits else 1.0
```
CLAIM:
空水印会被判定为验证通过：当 `watermark_bits` 为空且元数据中也无位置时，`extracted` 也为空，长度校验通过，`ratio` 被强制取 1.0，于是 `passed=True`。任何空/零长水印（`generate_watermark_bits(num_bits=0)` 也返回 `[]`）都能拿到一次「有效」的权属验证结论，属于认证逻辑绕过类的边界缺陷。空输入应显式拒绝（`num_bits <= 0` 直接抛 `ValueError`），而不是返回成功。
SUGGESTED FIX (tool): if not watermark_bits:
            raise ValueError("watermark_bits must not be empty; cannot verify an empty watermark")

        ratio = matches / len(watermark_bits)

## C9 [medium/bug]
LOCATED: true  fileExists: true
EXISTING CODE SNIPPET (as seen by the tool, may be stale):
```
scaled = int(round(w * 1000))
        # Clear LSB and set the desired bit
        scaled = (scaled & ~1) | (bit & 1)
        cls._set_weight_value(tensor, idx, scaled / 1000.0)
```
CLAIM:
LSB 编码方式存在两个问题：
1) 可检测/可擦除：写回值被强制量化到 `scaled / 1000.0`（最多 3 位小数），与未加水印的全精度权重在统计上极易区分。攻击者无需知道 `key_seed`，只需筛出「3 位小数」权重即可定位全部水印位并随机翻转 LSB，直接击穿 95% 阈值。
2) 静默丢位：未考虑目标 dtype。float16/bfloat16 张量写入后无法用 `int(round(w*1000))` 还原 LSB（半精度尾数只有 11 位，|w|>1 时误差即超过 0.5）；整数 dtype 张量写入浮点值会被截断。这些情况下 `embed_watermark` 仍返回 `success=True`。
建议在写回后回读校验（`_extract_bit` 结果与 `bit` 不一致则报错/计入失败），并按 dtype 精度选择缩放因子或拒绝低精度/整数张量。
SUGGESTED FIX (tool): scaled = int(round(w * 1000))
        # Clear LSB and set the desired bit
        scaled = (scaled & ~1) | (bit & 1)
        cls._set_weight_value(tensor, idx, scaled / 1000.0)
        if cls._extract_bit(tensor, idx) != (bit & 1):
            raise ValueError(
                f"LSB write did not round-trip at index {idx}; dtype lacks precision "
                f"or the tensor write was a no-op"
            )

## C10 [low/maintainability]
LOCATED: true  fileExists: true
EXISTING CODE SNIPPET (as seen by the tool, may be stale):
```
hex_digest = sm3_hash(payload)  # 64 hex chars = 256 bits
```
CLAIM:
死代码：`hex_digest` 赋值后从未被读取。下面循环的首轮迭代用的是 `sm3_hash(payload + counter.to_bytes(4, "big"))`（counter=0），即首次哈希输入带了 4 字节后缀，本行注释宣称的「64 hex chars = 256 bits」结果根本没参与比特生成，容易误导读者认为首轮就是裸 payload 摘要。要么删除本行，要么让 counter=0 时直接使用 `hex_digest`（后者会改变已生成水印的比特值，需同步升级兼容处理）。
SUGGESTED FIX (tool): hex_digest = sm3_hash(payload)  # counter == 0 时的首轮摘要，见下方循环

## C11 [low/maintainability]
LOCATED: true  fileExists: true
EXISTING CODE SNIPPET (as seen by the tool, may be stale):
```
import hashlib
```
CLAIM:
`hashlib` 在本文件中从未使用（哈希统一通过 `app.utils.crypto.sm3_hash`），属于无用导入，建议删除。

## C12 [low/maintainability]
LOCATED: true  fileExists: true
EXISTING CODE SNIPPET (as seen by the tool, may be stale):
```
layer_sizes[name] = size
```
CLAIM:
死代码：`layer_sizes` 写入后在全文件（包括返回的 `WatermarkMetadata`）都未被读取，可以直接去掉。同理下面 `hasattr(tensor, "flat")` 分支永远不可达：`numpy.ndarray` 必然有 `flatten`，已被上一个分支拦下。


## Known importers (grep of whole repo for "model_watermark" in *.py)
- app/api/training.py
- app/services/llm_sft_runtime.py
- app/services/model_watermark.py
- tests/test_training.py
