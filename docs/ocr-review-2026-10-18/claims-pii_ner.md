# Claims for app/services/pii_ner.py (14)

## C1 [high/]
LOCATED: true  fileExists: true
EXISTING CODE SNIPPET (as seen by the tool, may be stale):
```
# Layer 2: NER detection (if enabled)
        if self.enable_ner:
            ner_matches = self._detect_ner(text, regex_matches)
```
CLAIM:
Bug：`ner_engine="regex"/"off"/"none"` 被 `_resolve_engine` 解析为 `"regex_only"`，但这里只判断了 `self.enable_ner`，没有判断解析后的引擎。当调用方以默认 `enable_ner=True` + `PII_NER_ENGINE=off` 构造服务时（本文件底部的单例正是这种组合方式），`detect()` 仍会进入 `_detect_ner()`，而 `_detect_ner` 对既非 lac 也非 transformers 的引擎一律回落到 `_detect_ner_regex()`，于是规则 NER 依然运行并产出 `layer="ner"` 的匹配，同时结果却声称 `ner_engine="regex_only"`。这既违反类文档中“regex/off 关闭 NER 层”的约定，也违反 T9 的诚实披露契约（消费方会以为没有做任何规则识别）。
SUGGESTED FIX (tool): # Layer 2: NER detection (if enabled)
        if self.enable_ner and self._active_engine != "regex_only":
            ner_matches = self._detect_ner(text, regex_matches)

## C2 [high/]
LOCATED: true  fileExists: true
EXISTING CODE SNIPPET (as seen by the tool, may be stale):
```
else:
                # Overlap — keep higher severity
                if result and severity_order.get(m.severity, 99) < severity_order.get(result[-1].severity, 99):
                    result[-1] = m
                    last_end = m.end
```
CLAIM:
Bug（PII 泄漏）：重叠消解只比较严重级别，被丢弃的那条匹配所覆盖的字符区间**不会**被并入保留项。由于 `matches` 按 `(start, end)` 升序排列，重叠通常是“部分重叠”而非包含关系：例如规则层 ADDRESS(HIGH) 覆盖 [0,15)，regex 层 EMAIL(MEDIUM) 覆盖 [10,40)，EMAIL 被整条丢弃后，其 [15,40) 部分会原样出现在 `redacted_text` 中；同理 else 分支里用后起点、更高级别的匹配整体替换 `result[-1]` 时，原匹配前半段（如手机号前几位）也会残留在脱敏文本里。此外同级别冲突时保留的是更短的匹配，长实体的尾部同样漏脱敏。建议：冲突时按区间求并集（保留被丢弃匹配的 [min(start), max(end)) 跨度，或把长出的部分拆成独立片段参与 `_redact`），而不是整条丢弃。

## C3 [high/]
LOCATED: true  fileExists: true
EXISTING CODE SNIPPET (as seen by the tool, may be stale):
```
except Exception as e:
            logger.warning("LAC NER inference failed, falling back to rule NER: %s", e)
            return self._detect_ner_regex(text, regex_matches)
```
CLAIM:
违反诚实披露契约：LAC 推理抛异常时这里回落到规则层，产出的是 `layer="ner"` 的规则匹配，但 `detect()` 返回的 `ner_engine` 仍取 `self._active_engine == "lac"`。类文档明确要求“matches 为 layer="ner" 时 ner_engine 必须是 rule，绝不能标模型标签”，此处恰好制造了把规则误判当作模型 NER 的场景（审计/合规数据失真）。`_detect_ner_ml` 中的同款回落（约 425 行）也有同一问题。建议让内部检测方法同时返回实际引擎名，由 `detect()` 写入结果；同时 `PIIMatch.layer` 注释声明只有 "regex"/"ner"，实际还会产生 "ner_lac"/"ner_ml"，请统一契约。

## C4 [high/]
LOCATED: true  fileExists: true
EXISTING CODE SNIPPET (as seen by the tool, may be stale):
```
except Exception as e:
            logger.warning("ML NER inference failed, falling back to regex NER: %s", e)
            return self._detect_ner_regex(text, regex_matches)
```
CLAIM:
同上：模型推理失败回落到规则模式后，结果仍以 `ner_engine="transformers"` 上报，而匹配实际来自规则模式（`layer="ner"`），与文件头/T9 的披露约定冲突。另外下方 `ent["word"]`、`ent["start"]` 用下标访问，字段缺失会抛 `KeyError` 并被这个宽 `except` 吞掉，导致整条 ML 结果静默退化为规则结果，建议对单条实体用 `.get()` 并跳过，而不是让一条脏数据触发整层降级。

## C5 [medium/]
LOCATED: true  fileExists: true
EXISTING CODE SNIPPET (as seen by the tool, may be stale):
```
self._threshold = threshold
        self._active_engine = self._resolve_engine(ner_engine)
```
CLAIM:
模块导入即产生重副作用：`__init__` 里直接调用 `_resolve_engine(ner_engine)`，`auto`/`lac`/`transformers` 路径会在构造时执行 `_load_lac()` / `_load_transformers()`。也就是说任何 `import app.services.pii_ner`（例如被 router 间接导入）都会在导入阶段加载 Baidu LAC 或 HuggingFace pipeline——阻塞启动、占用大量内存，`PII_NER_USE_ML=True` 时还可能在导入阶段触发模型联网下载。这与 `_load_lac`/`_load_transformers` 文档里声称的 “Lazily load” 不符。建议把引擎解析推迟到首次 `detect()`（或用 `threading.Lock` 保护的双重检查惰性初始化）。

## C6 [medium/]
LOCATED: true  fileExists: true
EXISTING CODE SNIPPET (as seen by the tool, may be stale):
```
except Exception:
    pii_ner_service = PIINERService()
```
CLAIM:
`except Exception:` 无日志兜底会静默吞掉配置错误（如 `CDS_PII_NER_*` 值非法、`get_settings()` 校验失败、模型加载异常），悄悄换成默认规则引擎实例；同时因为 `PIINERService()` 默认 `ner_engine="auto"`，兜底路径仍可能在导入阶段尝试加载 LAC，失败后再次被这里的宽 except 掩盖。对以“诚实披露引擎”为目标的模块，这种静默降级风险较高，至少应 `logger.exception("PII NER singleton init failed; using rule-only defaults")`，并考虑让兜底实例使用 `ner_engine="rule"` 以避免重复加载模型。

## C7 [medium/]
LOCATED: true  fileExists: true
EXISTING CODE SNIPPET (as seen by the tool, may be stale):
```
pii_type, severity, confidence = mapped
                start = text.find(word, pos)
                if start < 0:
                    continue
```
CLAIM:
偏移回填对齐风险：`text.find(word, pos)` 找不到词元时（LAC 会归一化/丢弃空白与标点、全半角转换），这里 `continue` 但**没有推进 `pos`**，导致其后所有词元都从旧偏移继续向后搜索，一旦该词在文本后部再次出现就会绑定到错误位置，`PIIMatch.start/end` 整体错位，`_redact` 会截断/篡改原文（且只记 warning 于外层异常，此处完全静默）。建议：找不到时按词元长度做保守推进（或按 `sum(len(w) for w in words[:i])` 累积偏移），并在偏移校验失败时放弃本条 LAC 匹配而非继续输出可疑位置。

## C8 [medium/]
LOCATED: true  fileExists: true
EXISTING CODE SNIPPET (as seen by the tool, may be stale):
```
words, tags = self._lac.run(text)
```
CLAIM:
共享模型实例的并发安全：模块级 `pii_ner_service` 是全局单例，`_lac` / `_ml_pipeline` 在构造时被加载一次后由所有请求共享。`LAC.run()` 与 transformers `pipeline`（内部持有 tokenizer/model 状态）都不是线程安全对象；FastAPI 同步端点会在线程池中并发执行、异步端点也可能经 `run_in_executor` 调用，多线程同时推理可能造成结果错乱或底层崩溃。建议用 `threading.Lock` 串行化推理、改为请求级实例，或按官方建议用 `AsyncPipeline`/进程池隔离。

## C9 [medium/]
LOCATED: true  fileExists: true
EXISTING CODE SNIPPET (as seen by the tool, may be stale):
```
# Create a set of positions already covered by regex
        regex_positions: set[int] = set()
        for m in regex_matches:
            regex_positions.update(range(m.start, m.end))
```
CLAIM:
性能：这里把每条正则命中展开成逐字符位置集合（三处 NER 检测 `_detect_ner_lac` / `_detect_ner_ml` / `_detect_ner_regex` 都各建一份），随后每个候选又用 `any(pos in regex_positions for pos in range(start, end))` 逐字符判断。对大文本 + 大量数字型 PII（日志、导出文本）这是 O(覆盖字符数) 的内存与 CPU 放大，而 `_detect_ner_regex` 里四类模式各判断一次，开销成倍。建议保存已排序的 `[(start, end)]` 区间列表并用二分/区间相交判断（`candidate.start < iv_end and iv_start < candidate.end`），或至少复用同一个已排序区间结构。

## C10 [medium/]
LOCATED: true  fileExists: true
EXISTING CODE SNIPPET (as seen by the tool, may be stale):
```
# YYYY年MM月DD日
        re.compile(r"(\d{4}年\d{1,2}月\d{1,2}日)"),
```
CLAIM:
误报风险：该模式不带任何“出生”上下文，任何 `YYYY年M月D日` 都会被标为 `DATE_OF_BIRTH`，置信度 0.9、severity MEDIUM（如“2024年1月1日起实施”“合同签署于2023年5月6日”）。作为脱敏与审计依据会造成 PII 统计失真与大范围误脱敏。建议仅在前文出现 出生/生日/诞辰/DOB 等上下文时才归为 DATE_OF_BIRTH，其余日期归入更弱的类型或直接忽略（或显著下调 confidence）。

## C11 [medium/]
LOCATED: true  fileExists: true
EXISTING CODE SNIPPET (as seen by the tool, may be stale):
```
"TIME": (PIIType.DATE_OF_BIRTH, Severity.MEDIUM, 0.9),
```
CLAIM:
LAC 的 `TIME` 标签覆盖任意时间表达（“2026年”“上午10点”“最近三天”），直接映射成 `DATE_OF_BIRTH` 同样会把非生日信息标成 PII 并写入审计结果。建议映射到更准确的时间类型，或仅在文本含出生日期上下文时才归为 `DATE_OF_BIRTH`。

## C12 [medium/]
LOCATED: true  fileExists: true
EXISTING CODE SNIPPET (as seen by the tool, may be stale):
```
# Organization with prefix
        re.compile(r"(?:单位|公司|机构|组织)[：:\s]*([一-龥]{2,30})"),
```
CLAIM:
组织模式过度捕获：`(?:单位|公司|机构|组织)` 本身也是常见词（“……有限公司”里就含“公司”），而捕获组 `[一-龥]{2,30}` 贪婪匹配，会把后续整段中文叙述吞成组织名（如“公司成立于2010年并专注于……”会捕获 30 字），随后被整段 `[REDACTED:organization]` 替换，破坏文本并虚增 PII 计数。建议限制长度并加入非贪婪 + 终止边界（标点/“的/于/是/了”等），或在捕获结果上再校验是否以公司类后缀结尾。

## C13 [medium/]
LOCATED: true  fileExists: true
EXISTING CODE SNIPPET (as seen by the tool, may be stale):
```
# Detailed address with numbers
        re.compile(r"(?:地址|住址|住所)[：:\s]*([一-龥0-9]{5,50})"),
```
CLAIM:
与组织模式同类问题：`[一-龥0-9]{5,50}` 贪婪吞 50 字，且不以“路/号/室/省/市”等地址要素结尾，遇到“住址不详请联系客服中心尽快处理”这类文本会捕获整句并按 severity=HIGH 脱敏。建议收紧长度上限并要求尾部命中地址要素词，或降低该规则的 confidence 以便下游按阈值过滤。

## C14 [low/]
LOCATED: true  fileExists: true
EXISTING CODE SNIPPET (as seen by the tool, may be stale):
```
("bank_card", PIIType.BANK_CARD,
         re.compile(r"(?<!\d)[3-6]\d{15,18}(?!\d)"),
         Severity.HIGH),
```
CLAIM:
银行卡模式仅校验首位 3-6 与长度 16-19，未做 Luhn 校验，任何 16-19 位连续数字（订单号、流水号、时间戳拼接、18 位身份证号首位 3-6 的情况）都会被标为 `BANK_CARD` + severity=HIGH 并被脱敏。建议增加 Luhn 校验作为二次确认，不通过则降置信度或直接丢弃，以降低对业务文本的误脱敏。


## Known importers (grep of whole repo for "pii_ner" in *.py)
- app/services/pii_ner.py
- app/services/stream_audit.py
- tests/test_pii_ner.py
- tests/test_pii_ner_engine.py
