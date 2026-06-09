# SS-05：输出审查网关 分系统技术规格

> 版本：v1.0 | 分系统编号：SS-05  
> 上游：SS-04（沙箱运行时，唯一输入来源）  
> 下游：买方（合规结果）、SS-06（审计事件）、SS-02（DP 预算扣减）

---

## 1. 设计原则

- **零信任输出**：任何离开沙箱的数据，无论类型，必须经过本模块的多层审查
- **场景感知**：不同沙箱场景（查询/训练/应用/开发）使用不同审查规则集
- **不可绕过**：网络层强制所有出向流量经过本模块（iptables OUTPUT 链强制转发）
- **审查结果签名**：所有放行结果均附 SM2 签名（沙箱节点私钥），供买方验真

---

## 2. 审查流水线总览

```
沙箱内计算结果（内存缓冲区）
         │
         ▼
┌─────────────────────────────────────────────────────────┐
│  Stage 0: 场景路由（按沙箱模式选择规则集）                 │
├─────────────────────────────────────────────────────────┤
│  Stage 1: 格式与类型安全检查                              │
│    ・MIME 类型检测（基于内容，不信任声明）                  │
│    ・二进制嗅探（禁止图像/音频/视频原文件输出）              │
│    ・大小预检（超限直接拒绝）                               │
├─────────────────────────────────────────────────────────┤
│  Stage 2: 内容安全扫描（NLP + 规则）                      │
│    ・PII 检测（双层：正则 + NER 模型）                     │
│    ・原始数据重建检测（字段精确匹配率）                     │
│    ・商业机密关键词扫描                                    │
├─────────────────────────────────────────────────────────┤
│  Stage 3: 统计安全检查（结构化场景）                      │
│    ・最小计数原则（k-匿名，k≥5）                           │
│    ・差分隐私注入（Laplace/Gaussian 机制）                 │
│    ・预算扣减                                              │
├─────────────────────────────────────────────────────────┤
│  Stage 4: 模型安全检查（训练场景专用）                    │
│    ・MIA（成员推断攻击）探针                               │
│    ・梯度泄露检测（联邦场景）                               │
│    ・模型水印验证（训练完成后）                             │
├─────────────────────────────────────────────────────────┤
│  Stage 5: 溯源水印注入                                   │
│    ・文字输出：零宽字符隐写                                 │
│    ・数值结果：LSB 编码                                    │
│    ・模型权重：权重偏移水印                                 │
├─────────────────────────────────────────────────────────┤
│  Stage 6: 签名与配额更新                                  │
│    ・SM2 签名输出结果                                      │
│    ・更新 DP 预算账本                                      │
│    ・写入审计事件                                          │
└─────────────────────────────────────────────────────────┘
         │
         ▼
  合规输出（TLCP 加密 → 买方）  /  拒绝响应（含原因码）
```

---

## 3. 各场景审查规则集

### 3.1 规则集注册表

```python
class InspectionRuleRegistry:
    """规则集注册表 — 对齐 6 核心 SandboxMode（SPEC-1 简化后）
    已合并：SEMI_STRUCTURED_ETL → STRUCTURED_QUERY（config etl_format）
    已合并：UNSTRUCTURED_VISION/MULTIMODAL → STRUCTURED_MODELING（config training_type）
    已合并：UNSTRUCTURED_LLM_SFT → LLM_TRAINING（config training_type）
    新增：JOINT_FEDERATED（联邦学习场景）
    """
    RULE_SETS: dict[SandboxMode, list[InspectionStage]] = {
        SandboxMode.STRUCTURED_QUERY: [
            FormatCheckStage(forbidden_mimes=ALL_BINARY_MIMES),
            PIIDetectionStage(block_on_hit=True),
            RawDataReconstructionStage(threshold=0.05),
            KAnonymityStage(k_min=5),
            DifferentialPrivacyStage(mechanism="laplace"),
            WatermarkStage(method="zero_width"),
            SignAndAuditStage(),
        ],
        SandboxMode.STRUCTURED_MODELING: [
            # 通用建模规则集；config.training_type 区分传统ML/视觉/多模态
            FormatCheckStage(allowed_formats=["json", "csv_summary", "pickle_model",
                                              "safetensors", "onnx", "json_metrics", "json_eval"]),
            PIIDetectionStage(block_on_hit=True),
            ModelWeightSafetyStage(),
            MIAProbeStage(threshold=0.15),
            # 视觉/多模态训练时额外启用图像反推检测（运行时按 config.training_type 动态注入）
            ConditionalStage(
                condition=lambda ctx: ctx.config.training_type in ("vision", "multimodal"),
                stages=[NoRawImageStage(), ImageReconstructionProbeStage()],
            ),
            ConditionalStage(
                condition=lambda ctx: ctx.config.training_type == "multimodal",
                stages=[NoRawAudioStage()],
            ),
            WatermarkStage(method="model_weight_shift"),
            SignAndAuditStage(),
        ],
        SandboxMode.STRUCTURED_APP: [
            AppOutputProxyStage(),
            PIIDetectionStage(block_on_hit=True, streaming=True),
            RateLimitStage(max_rps=100),
            SignAndAuditStage(),
        ],
        SandboxMode.LLM_TRAINING: [
            # LLM 训练（SFT/PT/RLHF），config.training_type 细分
            FormatCheckStage(allowed_formats=["safetensors", "gguf", "onnx", "json_metrics"]),
            ModelWeightSafetyStage(),
            MIAProbeStage(threshold=0.12, model_type="llm"),
            TextMemorizationStage(threshold=0.15),   # Carlini Extractable Memorization，阈值放宽至 0.15
            WatermarkStage(method="model_weight_shift"),
            SignAndAuditStage(),
        ],
        SandboxMode.PRODUCT_DEVELOPMENT: [
            ProductPackageOnlyStage(),
            NoDataContentStage(),
            SignAndAuditStage(),
        ],
        SandboxMode.JOINT_FEDERATED: [
            # 联邦学习：模型梯度为输出，需防梯度泄露
            FormatCheckStage(allowed_formats=["json_metrics", "safetensors"]),
            GradientLeakageStage(),                    # 梯度反推数据检测
            ModelWeightSafetyStage(),
            DifferentialPrivacyStage(mechanism="gaussian"),  # SecAgg + DP
            WatermarkStage(method="model_weight_shift"),
            SignAndAuditStage(),
        ],
    }
```

---

## 4. 关键 Stage 实现

### 4.1 PII 检测（双层）

```python
class PIIDetectionStage(InspectionStage):
    """
    双层 PII 检测：正则表达式（高速）+ NER 模型（高准确）
    """

    # 正则规则（中国常见 PII 格式）
    REGEX_PATTERNS = {
        "id_card":    r"\b[1-9]\d{5}(18|19|20)\d{2}(0[1-9]|1[012])(0[1-9]|[12]\d|3[01])\d{3}[\dX]\b",
        "phone":      r"\b1[3-9]\d{9}\b",
        "bank_card":  r"\b[1-9]\d{15,18}\b",
        "email":      r"\b[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}\b",
        "ip_addr":    r"\b(?:\d{1,3}\.){3}\d{1,3}\b",
        "passport":   r"\b[EeGg]\d{8}\b",
        "name_zh":    r"[\u4e00-\u9fa5]{2,4}",  # 中文姓名（需结合 NER 确认）
    }

    def __init__(self, block_on_hit: bool = True, streaming: bool = False):
        self.block_on_hit = block_on_hit
        self.streaming = streaming
        # NER 模型（轻量级，运行于审查节点 CPU）
        self.ner_model = load_ner_model("bert-base-chinese-pii-ner")

    def inspect(self, output: Any, context: InspectionContext) -> StageResult:
        text = self._extract_text(output)

        # Layer 1: 正则快速扫描
        regex_hits = []
        for pii_type, pattern in self.REGEX_PATTERNS.items():
            matches = re.findall(pattern, text)
            if matches:
                regex_hits.append(PIIHit(type=pii_type, count=len(matches),
                                          samples=[m[:4] + "****" for m in matches[:3]]))

        # Layer 2: NER 模型精确识别（仅在正则命中时触发，降低计算开销）
        ner_hits = []
        if regex_hits or len(text) > 500:    # 长文本强制 NER 扫描
            ner_result = self.ner_model.predict(text)
            for entity in ner_result.entities:
                if entity.label in {"PER", "LOC", "ORG", "ID", "PHONE", "EMAIL"}:
                    ner_hits.append(PIIHit(type=entity.label, text_masked=entity.text[:2] + "**"))

        all_hits = regex_hits + ner_hits
        if all_hits and self.block_on_hit:
            return StageResult.BLOCK(
                reason="pii_detected",
                detail={"hits": [h.to_dict() for h in all_hits]},
            )

        # 自动脱敏（非阻断模式）
        if all_hits:
            output = self._auto_redact(output, all_hits)
            return StageResult.PASS(output=output, modified=True,
                                     note=f"Auto-redacted {len(all_hits)} PII instances")

        return StageResult.PASS(output=output)
```

### 4.2 差分隐私注入（自适应机制）

```python
class DifferentialPrivacyStage(InspectionStage):
    """
    差分隐私噪声注入
    支持：Laplace（纯 ε-DP）、Gaussian（(ε,δ)-DP）、指数机制（枚举查询）
    预算管理：每次注入后扣减合约 DP 预算
    """

    def inspect(self, output: Any, context: InspectionContext) -> StageResult:
        contract = context.contract
        epsilon_remaining = contract.dp_budget_remaining

        if epsilon_remaining <= 0:
            return StageResult.BLOCK(reason="dp_budget_exhausted")

        if isinstance(output, (int, float)):
            return self._apply_to_scalar(output, context)
        elif isinstance(output, dict):
            return self._apply_to_dict(output, context)
        elif isinstance(output, pd.DataFrame):
            return self._apply_to_dataframe(output, context)
        else:
            # 非数值类型：跳过 DP（依赖其他 Stage 处理）
            return StageResult.PASS(output=output)

    def _apply_to_scalar(self, value: float, ctx: InspectionContext) -> StageResult:
        sensitivity = ctx.query_sensitivity or self._estimate_sensitivity(value)
        epsilon = min(ctx.contract.dp_epsilon_per_query, ctx.contract.dp_budget_remaining)
        noise = np.random.laplace(0, sensitivity / epsilon)
        noisy_value = value + noise

        ctx.dp_epsilon_charged = epsilon
        return StageResult.PASS(
            output=round(noisy_value, 2),
            dp_metadata={"mechanism": "laplace", "epsilon": epsilon,
                         "sensitivity": sensitivity}
        )

    def _apply_to_dataframe(self, df: pd.DataFrame, ctx: InspectionContext) -> StageResult:
        """
        DataFrame 级 DP：对每个数值列独立注入噪声
        非数值列（分类）：检查是否满足 k-匿名
        """
        numeric_cols = df.select_dtypes(include=[np.number]).columns
        epsilon_per_col = ctx.contract.dp_epsilon_per_query / max(len(numeric_cols), 1)

        for col in numeric_cols:
            sensitivity = df[col].max() - df[col].min()  # L1 敏感度估算
            noise = np.random.laplace(0, sensitivity / epsilon_per_col, size=len(df))
            df[col] = df[col] + noise

        total_epsilon = epsilon_per_col * len(numeric_cols)
        ctx.dp_epsilon_charged = total_epsilon

        return StageResult.PASS(output=df,
                                 dp_metadata={"mechanism": "laplace",
                                              "epsilon_total": total_epsilon,
                                              "cols_protected": list(numeric_cols)})
```

### 4.3 MIA 探针（模型成员推断攻击检测）

```python
class MIAProbeStage(InspectionStage):
    """
    成员推断攻击（Membership Inference Attack）探针
    检测：训练完成的模型是否过度记忆训练数据
    方法：Loss-based Threshold Attack（Carlini et al. 2022）
    - 不需要训练影子模型（Shadow Model），直接通过损失值判断
    - 成员样本损失通常低于非成员样本
    - 样本量：50+50 即可（原 Shadow Model 需要 200+200）
    """

    def __init__(self, threshold: float = 0.15, model_type: str = "generic"):
        self.threshold = threshold    # MIA advantage 阈值（越低越安全）
        self.model_type = model_type

    def inspect(self, output: Any, context: InspectionContext) -> StageResult:
        if not isinstance(output, ModelPath):
            return StageResult.PASS(output=output)

        # 1. 加载待检测模型
        model = load_model_from_path(output.path)

        # 2. 获取成员/非成员样本（来自沙箱内 DataLoader）
        member_samples = context.dataloader.get_member_samples(n=50)
        nonmember_samples = context.dataloader.get_holdout_samples(n=50)

        # 3. 计算每个样本的损失（成员样本损失通常更低）
        member_losses = [model.compute_loss(s) for s in member_samples]
        nonmember_losses = [model.compute_loss(s) for s in nonmember_samples]

        # 4. 简单阈值攻击（loss < threshold → 预测为成员）
        optimal_threshold = np.median(member_losses + nonmember_losses)
        member_pred = [l < optimal_threshold for l in member_losses]
        nonmember_pred = [l >= optimal_threshold for l in nonmember_losses]

        tpr = sum(member_pred) / len(member_pred)       # True Positive Rate
        tnr = sum(nonmember_pred) / len(nonmember_pred) # True Negative Rate
        advantage = abs(tpr + tnr - 1)                 # MIA advantage（0=随机，1=完美攻击）

        if advantage > self.threshold:
            return StageResult.BLOCK(
                reason="mia_risk_exceeded",
                detail={"mia_advantage": advantage, "threshold": self.threshold,
                         "tpr": tpr, "tnr": tnr}
            )

        return StageResult.PASS(
            output=output,
            security_metadata={"mia_advantage": advantage, "mia_passed": True}
        )
```

### 4.4 文本记忆检测（LLM 专用）

```python
class TextMemorizationStage(InspectionStage):
    """
    LLM 训练数据记忆检测
    使用 Carlini et al. 的 Extractable Memorization 方法
    """

    def __init__(self, threshold: float = 0.08):
        self.threshold = threshold

    def inspect(self, output: Any, context: InspectionContext) -> StageResult:
        model = load_model_from_path(output.path)

        # 1. 构造前缀样本（来自训练数据片段的前 k 个 token）
        prefix_samples = context.dataloader.get_prefix_samples(
            n=100, prefix_len=50, suffix_len=100
        )

        memorization_scores = []
        for prefix, true_suffix in prefix_samples:
            # 2. 让模型续写（贪婪解码）
            generated = model.generate(prefix, max_new_tokens=100,
                                        do_sample=False)  # 贪婪，最大化记忆暴露

            # 3. 计算生成结果与真实 suffix 的重叠率
            overlap = self._token_overlap_ratio(generated, true_suffix)
            memorization_scores.append(overlap)

        avg_memorization = np.mean(memorization_scores)
        p95_memorization = np.percentile(memorization_scores, 95)

        if p95_memorization > self.threshold:
            return StageResult.BLOCK(
                reason="text_memorization_risk",
                detail={"avg_memorization": avg_memorization,
                         "p95_memorization": p95_memorization}
            )
        return StageResult.PASS(output=output,
                                 security_metadata={"memorization_score": avg_memorization})
```

### 4.5 溯源水印注入

```python
class WatermarkStage(InspectionStage):

    def inspect(self, output: Any, context: InspectionContext) -> StageResult:
        payload = self._build_payload(context)  # session_id 前12字节
        method = self.method

        if method == "zero_width" and isinstance(output, str):
            output = self._inject_zero_width(output, payload)

        elif method == "lsb_numeric" and isinstance(output, (int, float, pd.DataFrame)):
            output = self._inject_lsb(output, payload)

        elif method == "model_weight_shift" and isinstance(output, ModelPath):
            self._inject_weight_watermark(output.path, payload, context.session.watermark_key)

        return StageResult.PASS(output=output)

    def _inject_zero_width(self, text: str, payload: bytes) -> str:
        """
        零宽字符隐写：将 payload 编码为 Unicode 零宽字符序列
        ZWSP(U+200B)=0, ZWJ(U+200D)=1
        注入位置：文本末尾（不影响阅读）
        """
        bits = ''.join(f"{byte:08b}" for byte in payload)
        zwc_sequence = ''.join('\u200b' if b == '0' else '\u200d' for b in bits)
        return text + zwc_sequence

    def _inject_weight_watermark(self, model_path: str, payload: bytes, key: bytes):
        """
        模型权重水印：对特定权重层的 LSB 进行微小偏移
        使用 EmbMarker 方案：选择对模型性能影响最小的权重位
        偏移量 < 1e-5，不影响模型推理精度
        """
        from model_watermark import EmbMarker
        marker = EmbMarker(secret_key=key)
        marker.embed_to_file(model_path, payload=payload,
                             target_layers=["lm_head", "embed_tokens"],
                             max_perturbation=1e-5)
```

---

## 5. 应用场景流式审查（STRUCTURED_APP）

应用场景的 HTTP 响应是流式的，需要流式审查而非批次审查：

```python
class AppOutputProxyStage(InspectionStage):
    """
    应用 HTTP 响应流式审查代理
    架构：mitmproxy-in-TEE，拦截应用的所有出向 HTTP 响应
    """

    async def intercept_response(
        self,
        request: HttpRequest,
        response_stream: AsyncIterator[bytes]
    ) -> AsyncIterator[bytes]:

        buffer = b""
        async for chunk in response_stream:
            buffer += chunk

            # 每 4KB 或响应结束时触发审查
            if len(buffer) >= 4096 or is_last_chunk:
                text = buffer.decode("utf-8", errors="ignore")

                # 快速 PII 扫描（正则，不等 NER 模型）
                pii_hits = pii_regex_scan(text)
                if pii_hits:
                    # 自动脱敏后继续传输（不阻断，应用场景优先可用性）
                    text = auto_redact(text, pii_hits)
                    audit_service.record_redaction(pii_hits)

                yield text.encode("utf-8")
                buffer = b""

    async def check_response_aggregate(self, session_id: str, window_s: int = 60):
        """
        滚动窗口聚合检查：防止应用在短时间内分多次输出大量 PII
        """
        recent_outputs = redis.lrange(f"app_output:{session_id}", 0, -1)
        combined = " ".join(o.decode() for o in recent_outputs)
        pii_density = len(pii_regex_scan(combined)) / max(len(combined), 1)

        if pii_density > PII_DENSITY_THRESHOLD:
            # 触发会话级熔断
            session_service.suspend_session(session_id, reason="pii_density_exceeded")
```

---

## 6. 审查结果数据结构

```python
@dataclass
class InspectionReport:
    """每次输出审查的完整报告，附于审计事件"""
    session_id: str
    task_id: str
    sandbox_mode: str
    decision: Literal["ALLOW", "BLOCK", "ALLOW_WITH_MODIFICATION"]

    stages_passed: list[str]
    stages_failed: list[str]
    block_reason: Optional[str]           # decision=BLOCK 时填写

    # 内容安全
    pii_instances_found: int
    pii_instances_redacted: int
    raw_data_reconstruction_risk: float   # 0~1

    # 差分隐私
    dp_epsilon_charged: float
    dp_budget_remaining: float
    dp_mechanism: Optional[str]

    # 模型安全（训练场景）
    mia_advantage: Optional[float]
    memorization_score: Optional[float]
    watermark_injected: bool

    # 输出统计
    output_rows: int
    output_bytes: int
    output_format: str

    # 签名
    result_signature: str                 # SM2 签名
    inspection_timestamp: datetime
    inspector_node_id: str
```

---

## 7. 配置参数

```yaml
output_gateway:
  pii:
    regex_scan_always: true
    ner_model: "bert-base-chinese-pii-ner-v2"
    ner_trigger_threshold: 1              # 正则命中 >=1 触发 NER
    block_on_pii: true                    # 结构化/非结构化场景阻断
    app_mode_redact_not_block: true       # 应用场景：脱敏不阻断

  differential_privacy:
    default_mechanism: "laplace"
    gaussian_delta: 1e-5
    min_k_anonymity: 5
    budget_enforcement: "strict"          # strict | advisory

  model_security:
    mia_probe_enabled: true
    mia_member_samples: 200
    mia_nonmember_samples: 200
    llm_memorization_prefix_len: 50
    llm_memorization_suffix_len: 100
    llm_memorization_test_samples: 100

  watermark:
    zero_width_chars_enabled: true
    lsb_numeric_enabled: true
    model_weight_watermark_enabled: true
    max_weight_perturbation: 1e-5

  rate_limits:
    app_mode_max_rps: 100
    structured_max_output_per_session_mb: 100
    model_max_export_size_gb: 10
```

---

*文档：SS-05 | 版本：v1.0*
