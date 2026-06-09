# 密态沙箱系统 · 高级业务场景补充设计

> 补充: 结构化数据多表联查/场景建模/风控 + 半结构化/非结构化 LLM 训练数据管线

---

## 1. 结构化数据高级场景

### 1.1 多表数据产品

**问题**: 当前设计假设每个数据产品是单表。实际业务中, 数商的数据资产通常是多表关联的数据库。

**解决方案**: 数据产品支持多表定义, 买方在沙箱内可执行跨表 JOIN。

```yaml
# 多表数据产品元数据
DataProduct:
  id: "dp-multi-001"
  name: "企业征信全景数据"
  dataType: "structured"
  tables:
    - name: "enterprise_basic"
      description: "企业基本信息"
      fields:
        - {name: "ent_id", type: "VARCHAR(18)", sensitivity: "high", pk: true}
        - {name: "ent_name", type: "VARCHAR(200)", sensitivity: "medium"}
        - {name: "legal_person", type: "VARCHAR(50)", sensitivity: "high"}
        - {name: "reg_capital", type: "DECIMAL(15,2)", sensitivity: "low"}
        - {name: "industry_code", type: "VARCHAR(10)", sensitivity: "low"}
      rowCount: 5000000

    - name: "tax_records"
      description: "纳税记录"
      fields:
        - {name: "tax_id", type: "VARCHAR(20)", sensitivity: "low", pk: true}
        - {name: "ent_id", type: "VARCHAR(18)", sensitivity: "high", fk: "enterprise_basic.ent_id"}
        - {name: "year", type: "INT", sensitivity: "low"}
        - {name: "tax_amount", type: "DECIMAL(15,2)", sensitivity: "medium"}
        - {name: "tax_type", type: "VARCHAR(20)", sensitivity: "low"}
      rowCount: 25000000

    - name: "credit_records"
      description: "信贷记录"
      fields:
        - {name: "credit_id", type: "VARCHAR(20)", sensitivity: "low", pk: true}
        - {name: "ent_id", type: "VARCHAR(18)", sensitivity: "high", fk: "enterprise_basic.ent_id"}
        - {name: "bank_code", type: "VARCHAR(20)", sensitivity: "low"}
        - {name: "loan_amount", type: "DECIMAL(15,2)", sensitivity: "medium"}
        - {name: "repayment_status", type: "VARCHAR(10)", sensitivity: "low"}
      rowCount: 15000000

    - name: "legal_cases"
      description: "司法案件"
      fields:
        - {name: "case_id", type: "VARCHAR(20)", sensitivity: "low", pk: true}
        - {name: "ent_id", type: "VARCHAR(18)", sensitivity: "high", fk: "enterprise_basic.ent_id"}
        - {name: "case_type", type: "VARCHAR(50)", sensitivity: "low"}
        - {name: "judgment_date", type: "DATE", sensitivity: "low"}
        - {name: "amount", type: "DECIMAL(15,2)", sensitivity: "medium"}
      rowCount: 3000000

  relationships:
    - from: "tax_records.ent_id"
      to: "enterprise_basic.ent_id"
      type: "many_to_one"
    - from: "credit_records.ent_id"
      to: "enterprise_basic.ent_id"
      type: "many_to_one"
    - from: "legal_cases.ent_id"
      to: "enterprise_basic.ent_id"
      type: "many_to_one"

  # 多表产品的策略配置
  allowedOperations:
    - "multi_table_join"
    - "aggregate"
    - "filter"
    - "statistical_model"
    - "feature_engineering"    # 特征工程
    - "risk_model_training"   # 风控模型训练
  forbiddenOperations:
    - "row_level_export"
    - "network_call"
    - "subquery_leak"         # 禁止子查询泄露其他表数据

  # 多表JOIN限制
  joinConstraints:
    maxTables: 4              # 最多JOIN 4张表
    requireJoinKey: true      # 必须通过FK关联
    blockCrossProduct: true   # 禁止笛卡尔积
    maxOutputRows: 500        # JOIN后最大输出行数
```

### 1.2 场景建模工作台

**功能**: 数商在沙箱内定义业务场景模板, 买方按场景使用数据。

```
场景建模流程:
  数商定义场景 → 配置场景参数 → 绑定数据表 → 定义输出Schema → 发布场景模板

买方使用流程:
  选择场景模板 → 填写参数 → 沙箱内执行 → 获取结果
```

**场景模板定义**:

```yaml
ScenarioTemplate:
  id: "scenario-risk-scoring"
  name: "企业信用风险评分"
  description: "基于企业基本信息、纳税、信贷、司法数据计算信用风险评分"
  category: "风控"

  # 场景参数 (买方填写)
  parameters:
    - name: "target_industry"
      type: "string"
      description: "目标行业 (行业代码)"
      required: true
      example: "C39"
    - name: "min_reg_capital"
      type: "number"
      description: "最低注册资本 (万元)"
      required: false
      default: 100
    - name: "score_weights"
      type: "object"
      description: "评分权重配置"
      required: false
      default:
        tax_stability: 0.3
        credit_health: 0.3
        legal_risk: 0.2
        growth_potential: 0.2

  # 场景SQL模板 (沙箱内执行)
  executionTemplate: |
    WITH tax_stability AS (
      SELECT ent_id,
             STDDEV(tax_amount) / NULLIF(AVG(tax_amount), 0) AS tax_cv,
             COUNT(*) AS tax_years
      FROM tax_records
      WHERE year >= EXTRACT(YEAR FROM CURRENT_DATE) - 3
        AND ent_id IN (SELECT ent_id FROM enterprise_basic
                       WHERE industry_code = '{target_industry}'
                       AND reg_capital >= {min_reg_capital} * 10000)
      GROUP BY ent_id
    ),
    credit_health AS (
      SELECT ent_id,
             SUM(CASE WHEN repayment_status = 'overdue' THEN 1 ELSE 0 END)::FLOAT
               / COUNT(*) AS overdue_rate,
             SUM(loan_amount) AS total_debt
      FROM credit_records
      GROUP BY ent_id
    ),
    legal_risk AS (
      SELECT ent_id,
             COUNT(*) AS case_count,
             SUM(amount) AS total_amount
      FROM legal_cases
      WHERE judgment_date >= CURRENT_DATE - INTERVAL '3 years'
      GROUP BY ent_id
    )
    SELECT
      e.ent_id,
      e.ent_name,
      -- 综合风险评分 (0-100, 越低越安全)
      (100
        - COALESCE(ts.tax_cv * 100 * {score_weights.tax_stability}, 0)
        - COALESCE(ch.overdue_rate * 100 * {score_weights.credit_health}, 0)
        - COALESCE(LEAST(lr.case_count * 10, 50) * {score_weights.legal_risk}, 0)
        + COALESCE(GREATEST(ts.tax_years - 3, 0) * 5 * {score_weights.growth_potential}, 0)
      ) AS risk_score,
      ts.tax_cv,
      ch.overdue_rate,
      lr.case_count
    FROM enterprise_basic e
    LEFT JOIN tax_stability ts ON e.ent_id = ts.ent_id
    LEFT JOIN credit_health ch ON e.ent_id = ch.ent_id
    LEFT JOIN legal_risk lr ON e.ent_id = lr.ent_id
    WHERE e.industry_code = '{target_industry}'
    ORDER BY risk_score ASC
    LIMIT 500

  # 输出Schema
  outputSchema:
    - {name: "ent_id", type: "string", description: "脱敏后企业ID"}
    - {name: "ent_name", type: "string", description: "[企业名] 已脱敏"}
    - {name: "risk_score", type: "number", description: "风险评分 0-100"}
    - {name: "tax_cv", type: "number", description: "纳税变异系数"}
    - {name: "overdue_rate", type: "number", description: "逾期率"}
    - {name: "case_count", type: "integer", description: "司法案件数"}
```

### 1.3 特征工程沙箱

**功能**: 买方在沙箱内构建机器学习特征, 模型训练在沙箱内完成, 仅输出模型文件(不含原始数据)。

```
特征工程流程:
  选择数据表 → 定义特征SQL → 沙箱内执行特征提取 → 模型训练 → 仅输出模型文件

安全约束:
  - 特征数据不出沙箱
  - 模型文件输出前检查: 禁止嵌入原始数据 (成员推断攻击防护)
  - 模型参数差分隐私注入
  - 输出: 模型文件 + 模型性能指标 (不含训练样本)
```

---

## 2. 半结构化/非结构化数据 LLM 训练管线

### 2.1 LLM 训练数据产品类型

| 数据产品类型 | 数据格式 | 用途 | 沙箱内处理 |
|-------------|----------|------|-----------|
| **预训练语料** | JSON/JSONL, TXT, HTML | LLM 预训练 | 去重/清洗/分词/打包 |
| **指令微调数据** | JSONL (instruction/input/output) | SFT 微调 | 质量过滤/格式转换/PII脱敏 |
| **偏好数据** | JSONL (chosen/rejected) | RLHF/DPO | 质量评估/配对/脱敏 |
| **RAG 知识库** | PDF, DOCX, HTML, Markdown | RAG 检索增强 | 分块/向量化/索引 |
| **多模态数据** | 图文对 (image+caption) | 多模态训练 | 人脸打码/文字脱敏/配对 |
| **对话数据** | JSON (conversation tree) | 对话模型 | PII脱敏/质量过滤/格式化 |

### 2.2 预训练语料处理管线

```yaml
# 预训练语料数据产品
DataProduct:
  id: "dp-llm-pretrain-001"
  name: "中文互联网语料2024"
  dataType: "semi_structured"
  mediaType: "application/jsonl"
  llmConfig:
    taskType: "pretrain"
    tokenizer: "sentencepiece"  # 或 tiktoken
    targetTokens: 100000000000  # 100B tokens

  # 沙箱内处理管线
  processingPipeline:
    - step: "format_detection"
      description: "检测JSONL格式, 提取text字段"
      tool: "ijson (流式解析)"

    - step: "language_detection"
      description: "语言检测, 仅保留中文(zh)内容"
      tool: "fasttext lid.176.bin"
      threshold: 0.8

    - step: "quality_filtering"
      description: "质量过滤"
      rules:
        - {type: "min_length", value: 100}        # 最少100字符
        - {type: "max_length", value: 100000}      # 最多10万字符
        - {type: "special_char_ratio", max: 0.3}   # 特殊字符比例<30%
        - {type: "repetition_ratio", max: 0.3}     # 重复内容比例<30%
        - {type: "perplexity_threshold", max: 1000} # 困惑度阈值

    - step: "deduplication"
      description: "去重"
      method: "MinHash LSH (局部敏感哈希)"
      similarity_threshold: 0.85

    - step: "pii_scrubbing"
      description: "PII脱敏"
      rules:
        - {pattern: "身份证号", replacement: "[ID_CARD]"}
        - {pattern: "手机号", replacement: "[PHONE]"}
        - {pattern: "邮箱", replacement: "[EMAIL]"}
        - {pattern: "银行卡号", replacement: "[BANK_CARD]"}
        - {pattern: "地址", replacement: "[ADDRESS]", method: "NER"}

    - step: "toxicity_filter"
      description: "有害内容过滤"
      tool: "自研分类器 或 Perspective API"
      threshold: 0.9

    - step: "tokenization"
      description: "分词/Token化"
      tool: "sentencepiece / tiktoken"

    - step: "packing"
      description: "打包为训练格式"
      format: "arrow / mmap / tfrecord"
      blockSize: 2048  # tokens per block

  # 输出约束
  outputConstraints:
    allowedFormats: ["arrow", "mmap", "jsonl"]
    forbidOriginalText: true        # 禁止输出原始文本
    requirePiiScrub: true           # 必须PII脱敏
    requireDedup: true              # 必须去重
    maxOutputSizeGB: 500
```

### 2.3 指令微调数据处理管线

```yaml
# 指令微调数据产品
DataProduct:
  id: "dp-llm-sft-001"
  name: "医疗问答指令数据集"
  dataType: "semi_structured"
  mediaType: "application/jsonl"
  llmConfig:
    taskType: "sft"  # Supervised Fine-Tuning
    format: "alpaca"  # 或 chatml, sharegpt

  # 输入格式 (JSONL)
  inputSchema:
    - {field: "instruction", type: "string", description: "指令"}
    - {field: "input", type: "string", description: "输入上下文 (可空)"}
    - {field: "output", type: "string", description: "期望输出"}

  # 沙箱内处理管线
  processingPipeline:
    - step: "format_validation"
      description: "验证JSONL格式, 检查必填字段"
      rules:
        - {field: "instruction", minLength: 10, maxLength: 2000}
        - {field: "output", minLength: 50, maxLength: 10000}

    - step: "quality_scoring"
      description: "质量评分 (自动)"
      criteria:
        - {name: "instruction_clarity", weight: 0.3}    # 指令清晰度
        - {name: "output_completeness", weight: 0.3}    # 输出完整性
        - {name: "factual_accuracy", weight: 0.2}       # 事实准确性 (抽样)
        - {name: "medical_relevance", weight: 0.2}      # 医学相关性
      minScore: 0.7

    - step: "pii_scrubbing"
      description: "PII脱敏 (患者信息)"
      rules:
        - {pattern: "患者姓名", method: "NER", replacement: "[患者]"}
        - {pattern: "身份证号", replacement: "[ID]"}
        - {pattern: "手机号", replacement: "[PHONE]"}
        - {pattern: "病历号", replacement: "[病历号]"}

    - step: "format_conversion"
      description: "转换为目标训练格式"
      outputFormats:
        - name: "alpaca"
          schema: {instruction: "", input: "", output: ""}
        - name: "chatml"
          schema: {messages: [{role: "user", content: ""}, {role: "assistant", content: ""}]}
        - name: "sharegpt"
          schema: {conversations: [{from: "human", value: ""}, {from: "gpt", value: ""}]}

    - step: "deduplication"
      description: "指令去重 (相似指令合并)"
      method: "embedding similarity (BGE-M3)"
      threshold: 0.9

    - step: "train_test_split"
      description: "训练集/验证集划分"
      ratio: {train: 0.95, validation: 0.05}
      method: "stratified by topic"
```

### 2.4 RAG 知识库处理管线

```yaml
# RAG知识库数据产品
DataProduct:
  id: "dp-rag-001"
  name: "医疗指南知识库"
  dataType: "unstructured"
  mediaType: "application/pdf"
  llmConfig:
    taskType: "rag"
    embeddingModel: "bge-m3"     # 或 text-embedding-3-large
    chunkSize: 512               # tokens per chunk
    chunkOverlap: 64

  # 沙箱内处理管线
  processingPipeline:
    - step: "document_parsing"
      description: "文档解析 (PDF/Word/HTML → 纯文本)"
      tool: "PyMuPDF / python-docx / BeautifulSoup"
      output: "structured text with headings"

    - step: "ocr_if_scanned"
      description: "扫描件OCR"
      tool: "PaddleOCR PP-OCRv4"
      condition: "if scanned pages detected"

    - step: "pii_scrubbing"
      description: "PII脱敏"
      rules:
        - {pattern: "患者信息", method: "NER"}
        - {pattern: "医生姓名", method: "NER"}

    - step: "chunking"
      description: "文档分块"
      method: "recursive_character_splitting"
      chunkSize: 512
      chunkOverlap: 64
      separators: ["\n\n", "\n", "。", "；", " "]

    - step: "embedding"
      description: "向量化"
      model: "bge-m3"
      dimensions: 1024
      output: "vector index (FAISS/Milvus format)"

    - step: "metadata_extraction"
      description: "元数据提取"
      fields:
        - {name: "source_doc", type: "string"}
        - {name: "page_number", type: "integer"}
        - {name: "heading", type: "string"}
        - {name: "topic", type: "string", method: "auto-classify"}

  # 输出约束
  outputConstraints:
    allowedFormats: ["faiss_index", "milvus_collection", "jsonl_with_embeddings"]
    forbidOriginalText: true        # 禁止输出原始文档全文
    requirePiiScrub: true
    maxChunks: 1000000
```

### 2.5 多模态训练数据管线

```yaml
# 多模态训练数据产品
DataProduct:
  id: "dp-multimodal-001"
  name: "医学影像-报告配对数据集"
  dataType: "unstructured"
  mediaType: "image/dicom + text"
  llmConfig:
    taskType: "multimodal_sft"
    format: "image_text_pair"

  # 沙箱内处理管线
  processingPipeline:
    - step: "image_preprocessing"
      description: "图像预处理"
      rules:
        - {action: "face_blur", tool: "InsightFace SCRFD"}
        - {action: "dicom_tag_strip", tags: ["PatientName", "PatientID"]}
        - {action: "resize", maxResolution: [512, 512]}
        - {action: "normalize", method: "min-max"}

    - step: "text_preprocessing"
      description: "报告文本预处理"
      rules:
        - {action: "pii_scrub", method: "NER"}
        - {action: "normalize_whitespace"}
        - {action: "remove_headers_footers"}

    - step: "pairing"
      description: "图文配对"
      method: "DICOM StudyID ↔ Report ID"
      validation: "cross-check modality/body part"

    - step: "quality_filtering"
      description: "质量过滤"
      rules:
        - {type: "image_quality", metric: "blur_score", min: 0.5}
        - {type: "text_quality", metric: "min_tokens", min: 50}
        - {type: "pair_relevance", metric: "cosine_similarity", min: 0.7}

    - step: "format_conversion"
      description: "转换为训练格式"
      outputFormats:
        - name: "llava"
          schema: {image: "path", conversations: [{role: "user", value: "<image>\n描述此影像"}, {role: "assistant", value: "..."}]}
        - name: "internvl"
          schema: {image: "path", question: "", answer: ""}
```

---

## 3. 沙箱控制台更新 (买方视角)

### 3.1 结构化数据 — 多表查询

```
买方提交任务时, 沙箱控制台显示:

┌─ 数据产品: 企业征信全景数据 ─────────────────────────┐
│                                                     │
│  可用表:                                             │
│  ┌──────────────────┐ ┌──────────────────┐          │
│  │ enterprise_basic  │ │ tax_records      │          │
│  │ PK: ent_id       │ │ FK: ent_id       │          │
│  │ 500万行          │ │ 2500万行         │          │
│  └──────────────────┘ └──────────────────┘          │
│  ┌──────────────────┐ ┌──────────────────┐          │
│  │ credit_records   │ │ legal_cases      │          │
│  │ FK: ent_id       │ │ FK: ent_id       │          │
│  │ 1500万行         │ │ 300万行          │          │
│  └──────────────────┘ └──────────────────┘          │
│                                                     │
│  关联关系: enterprise_basic ← tax_records            │
│           enterprise_basic ← credit_records          │
│           enterprise_basic ← legal_cases             │
│                                                     │
│  ┌─────────────────────────────────────────────┐    │
│  │ SQL 编辑器                                   │    │
│  │ SELECT e.ent_name, t.tax_amount, c.overdue  │    │
│  │ FROM enterprise_basic e                      │    │
│  │ JOIN tax_records t ON e.ent_id = t.ent_id   │    │
│  │ JOIN credit_records c ON e.ent_id = c.ent_id│    │
│  │ WHERE e.industry_code = 'C39'                │    │
│  │ LIMIT 100                                    │    │
│  └─────────────────────────────────────────────┘    │
│                                                     │
│  [验证SQL] [提交任务]                                 │
│  ⚠ 策略限制: 最多JOIN 4张表, 禁止笛卡尔积             │
└─────────────────────────────────────────────────────┘
```

### 3.2 结构化数据 — 场景建模

```
┌─ 场景模板: 企业信用风险评分 ─────────────────────────┐
│                                                     │
│  场景参数:                                           │
│  目标行业: [C39 ▼]  最低注册资本: [100] 万元          │
│                                                     │
│  评分权重:                                           │
│  纳税稳定性: [0.3]  信贷健康度: [0.3]                │
│  司法风险: [0.2]    成长潜力: [0.2]                  │
│                                                     │
│  [执行场景]                                          │
│                                                     │
│  执行结果:                                           │
│  ┌────────────────────────────────────────────┐     │
│  │ ent_name    │ risk_score │ tax_cv │ overdue │     │
│  │ [企业名]    │ 82.5       │ 0.12   │ 0.03    │     │
│  │ [企业名]    │ 78.3       │ 0.18   │ 0.05    │     │
│  │ [企业名]    │ 71.2       │ 0.25   │ 0.08    │     │
│  └────────────────────────────────────────────┘     │
│  ✓ 输出审查通过 | 无PII泄露 | DP: 0.3ε               │
└─────────────────────────────────────────────────────┘
```

### 3.3 LLM 训练数据 — 买方视角

```
买方选择 LLM 训练数据产品后, 沙箱控制台:

┌─ 数据产品: 中文医疗问答指令数据集 ──────────────────┐
│                                                     │
│  数据类型: 指令微调 (SFT)                            │
│  格式: JSONL (instruction/input/output)              │
│  记录数: 500,000 | 已PII脱敏 | 已质量评分             │
│                                                     │
│  可用操作:                                           │
│  ○ 数据质量分析 (统计/分布/异常)                      │
│  ○ 数据清洗 (过滤低质量/重复/有害内容)                │
│  ○ 格式转换 (Alpaca → ChatML → ShareGPT)            │
│  ● 模型微调 (SFT)                                    │
│  ○ 偏好标注 (DPO/RLHF)                              │
│                                                     │
│  微调配置:                                           │
│  基座模型: [Qwen2.5-7B ▼]                           │
│  训练方式: [LoRA ▼]  Epoch: [3]  LR: [2e-4]         │
│  Batch Size: [4]  Max Seq Len: [2048]               │
│                                                     │
│  [开始训练]                                          │
│                                                     │
│  训练结果:                                           │
│  ✓ 训练完成 (4.5h) | Loss: 1.23 → 0.89              │
│  输出: LoRA权重文件 (128MB, 不含训练数据)             │
│  ✓ 模型安全检查通过: 无原始数据嵌入                    │
│  ✓ 差分隐私: ε=2.0 已注入                            │
└─────────────────────────────────────────────────────┘
```

---

## 4. 安全约束 (LLM训练场景专项)

| 约束 | 说明 |
|------|------|
| **训练数据不出沙箱** | 原始数据、中间特征、tokenized数据 均不出沙箱 |
| **模型文件安全检查** | 输出前检查: 成员推断攻击防护, 禁止嵌入训练样本 |
| **DP注入** | 训练过程中注入差分隐私噪声 (Opacus / dp-transformers) |
| **梯度裁剪** | 限制梯度范数, 防止梯度泄露原始数据 |
| **模型水印** | 在模型权重中嵌入溯源水印 |
| **PII脱敏** | 训练数据必须经过PII脱敏 (Presidio + 自定义规则) |
| **有害内容过滤** | 训练数据经过有害内容分类器过滤 |
| **输出限制** | 仅输出模型权重 + 训练指标, 禁止输出训练数据 |
