# 密态沙箱系统 · 安全产出与模型生命周期设计

> 核心问题: 数据不出域, 但模型/特征/业务规则需要出域使用
> 设计原则: 原始数据不可出, 安全产出可控出

---

## 1. 问题分析

### 1.1 业务场景与矛盾

```
场景1: 银行风控建模
  银行(买方)需要:
    ① 在企业真实纳税/信贷/司法数据上训练风控模型
    ② 模型部署到银行生产环境, 实时评分
    ③ 模型需要持续迭代(新数据→重新训练→更新模型)

  矛盾:
    - 数据不能出沙箱 (安全要求)
    - 模型需要出沙箱 (业务要求)
    - 模型可能"记住"训练数据 (隐私风险)

场景2: 保险定价模型
  保险公司(买方)需要:
    ① 在医疗影像+理赔数据上训练定价模型
    ② 模型集成到保险核心系统
    ③ 特征工程需要反复迭代

  矛盾:
    - 特征是从原始数据计算的, 特征可能泄露原始数据
    - 特征工程是迭代过程, 需要多次实验

场景3: 联合风控
  多个数商提供不同维度数据:
    - 数商A: 税务数据
    - 数商B: 司法数据
    - 数商C: 信贷数据
  买方需要在多方数据上建模, 但任何一方数据不能被其他方看到

  矛盾:
    - 模型需要看到所有数据才能训练
    - 但数据不能汇聚到一处
```

### 1.2 安全产出分类

```
┌─────────────────────────────────────────────────────────────────┐
│  产出安全等级分类                                                │
│                                                                 │
│  ✅ 安全产出 (可直接出域)                                        │
│  ├── 聚合统计: COUNT/SUM/AVG/STDDEV (加DP噪声)                  │
│  ├── 分类标签: 行业分类/风险等级/情绪标签                          │
│  ├── 趋势指标: 同比/环比/增长率                                  │
│  └── 质量报告: 完整率/分布统计/异常值比例                          │
│                                                                 │
│  ⚠️ 受控产出 (经安全审查后出域)                                   │
│  ├── ML模型: 训练后的模型文件 (需防记忆化检查)                     │
│  ├── 特征向量: 聚合后的特征 (需确认不可逆)                        │
│  ├── 模型指标: AUC/F1/混淆矩阵 (不含样本)                       │
│  └── 规则引擎: 决策树/规则集 (需确认不含原始值)                    │
│                                                                 │
│  ❌ 禁止产出 (绝对不出域)                                        │
│  ├── 原始数据: 任何行级/记录级原始数据                            │
│  ├── 样本数据: 训练样本/测试样本                                  │
│  ├── 梯度信息: 模型训练梯度 (可反推数据)                          │
│  ├── 原始特征: 未聚合的特征矩阵                                  │
│  └── 原始文件: 图像/文档/音视频原始文件                           │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. 产品设计: 安全产出工作台

### 2.1 工作台功能全景

```
┌─────────────────────────────────────────────────────────────────┐
│  安全产出工作台 (买方视角)                                        │
│                                                                 │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐             │
│  │ 特征工程     │  │ 模型训练     │  │ 模型验证     │             │
│  │             │  │             │  │             │             │
│  │ 定义特征SQL │  │ 选择算法    │  │ 防记忆化测试 │             │
│  │ 沙箱内执行  │  │ 沙箱内训练  │  │ DP预算检查   │             │
│  │ 输出:特征统计│  │ 输出:模型文件│  │ 水印注入     │             │
│  └─────────────┘  └─────────────┘  └─────────────┘             │
│         │                │                │                    │
│         ▼                ▼                ▼                    │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  安全产出审查网关                                           │  │
│  │  ├── 模型安全扫描 (成员推断/梯度泄露/数据重建)              │  │
│  │  ├── 特征安全检查 (不可逆性/聚合充分性)                     │  │
│  │  ├── DP预算扣除                                            │  │
│  │  ├── 溯源水印注入                                          │  │
│  │  └── SM2签名 + 链上存证                                    │  │
│  └───────────────────────────────────────────────────────────┘  │
│         │                                                        │
│         ▼                                                        │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐             │
│  │ 模型部署     │  │ 模型服务     │  │ 模型监控     │             │
│  │             │  │             │  │             │             │
│  │ 导出模型文件 │  │ API沙箱推理  │  │ 性能漂移检测 │             │
│  │ 部署到生产   │  │ 实时评分     │  │ 公平性审计   │             │
│  └─────────────┘  └─────────────┘  └─────────────┘             │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 特征工程工作台

```yaml
# 特征工程数据产品配置
FeatureEngineering:
  id: "fe-risk-001"
  name: "企业信用风险特征工程"
  product: "dp-multi-001"  # 绑定的数据产品

  # 特征定义 (数商预定义模板 + 买方可扩展)
  featureTemplates:
    # 数商预定义的安全特征模板
    - name: "tax_stability"
      description: "纳税稳定性 (变异系数)"
      sql: |
        SELECT ent_id,
               STDDEV(tax_amount) / NULLIF(AVG(tax_amount), 0) AS tax_cv,
               COUNT(*) AS tax_years
        FROM tax_records
        WHERE year >= {lookback_years}
        GROUP BY ent_id
      outputType: "float"  # 输出类型: float/int/category
      safetyLevel: "safe"  # safe: 聚合值, 不含原始数据

    - name: "credit_health"
      description: "信贷健康度"
      sql: |
        SELECT ent_id,
               SUM(CASE WHEN repayment_status = 'overdue' THEN 1 ELSE 0 END)::FLOAT
                 / COUNT(*) AS overdue_rate,
               SUM(loan_amount) AS total_debt,
               COUNT(*) AS loan_count
        FROM credit_records
        GROUP BY ent_id
      outputType: "float"
      safetyLevel: "safe"

    - name: "legal_risk"
      description: "司法风险"
      sql: |
        SELECT ent_id,
               COUNT(*) AS case_count,
               SUM(amount) AS total_amount,
               MAX(judgment_date) AS last_case_date
        FROM legal_cases
        WHERE judgment_date >= CURRENT_DATE - INTERVAL '{lookback_years} years'
        GROUP BY ent_id
      outputType: "float"
      safetyLevel: "safe"

  # 买方可自定义特征 (需数商审批)
  customFeatures:
    allowed: true
    requireApproval: true
    maxCustomFeatures: 20
    # 自定义特征的输出约束
    outputConstraints:
      minAggregationLevel: "group_by_ent"  # 至少按企业聚合
      forbidRawFields: true                 # 禁止输出原始字段
      maxOutputRows: 10000                  # 最大输出行数
```

**特征工程界面 (买方):**

```
┌─ 特征工程: 企业信用风险 ──────────────────────────────────┐
│                                                          │
│  数据产品: 企业征信全景数据 (4表, 4800万行)                 │
│                                                          │
│  已定义特征 (3个):                                        │
│  ┌──────────────────────────────────────────────────┐    │
│  │ ✅ tax_stability    纳税稳定性 (变异系数)    safe  │    │
│  │ ✅ credit_health    信贷健康度 (逾期率)      safe  │    │
│  │ ✅ legal_risk       司法风险 (案件数/金额)   safe  │    │
│  └──────────────────────────────────────────────────┘    │
│                                                          │
│  + 添加自定义特征                                         │
│                                                          │
│  特征预览 (沙箱内执行, 仅输出统计):                         │
│  ┌──────────────────────────────────────────────────┐    │
│  │ 特征           │ 非空率  │ 均值   │ 标准差  │ 分布  │    │
│  │ tax_cv         │ 95.2%  │ 0.35   │ 0.28   │ 右偏  │    │
│  │ overdue_rate   │ 89.1%  │ 0.08   │ 0.12   │ 右偏  │    │
│  │ case_count     │ 76.3%  │ 2.3    │ 5.1    │ 右偏  │    │
│  └──────────────────────────────────────────────────┘    │
│                                                          │
│  ⚠ 特征输出仅包含聚合统计, 不含原始行级数据                 │
│                                                          │
│  [执行特征计算] [导出特征矩阵] [训练模型]                   │
└──────────────────────────────────────────────────────────┘
```

### 2.3 模型训练工作台

```yaml
# 模型训练配置
ModelTraining:
  id: "mt-risk-001"
  name: "企业信用风险评分模型"
  featureSet: "fe-risk-001"

  # 支持的算法
  algorithms:
    - name: "XGBoost"
      library: "xgboost"
      safeForExport: true  # 树模型相对安全, 不易记忆数据
    - name: "LightGBM"
      library: "lightgbm"
      safeForExport: true
    - name: "LogisticRegression"
      library: "sklearn"
      safeForExport: true  # 线性模型安全
    - name: "NeuralNetwork"
      library: "pytorch"
      safeForExport: false  # 神经网络需要额外防记忆化检查
      requireDP: true       # 必须启用差分隐私

  # 训练配置
  trainingConfig:
    testSize: 0.2
    randomSeed: 42
    crossValidation: 5

  # 差分隐私配置
  dpConfig:
    enabled: true
    mechanism: "gaussian"
    epsilon: 2.0
    delta: 1e-5
    maxGradNorm: 1.0       # 梯度裁剪

  # 模型安全检查 (导出前必须通过)
  securityChecks:
    - name: "membership_inference"
      description: "成员推断攻击测试: 确认模型无法判断某条数据是否在训练集中"
      threshold: 0.55       # AUC < 0.55 (接近随机)
      tool: "custom_mi_test"

    - name: "model_inversion"
      description: "模型反演攻击测试: 确认无法从模型权重重建训练数据"
      threshold: "pass/fail"
      tool: "custom_miv_test"

    - name: "data_reconstruction"
      description: "数据重建测试: 确认模型输出不包含原始数据信息"
      threshold: "pass/fail"
      tool: "custom_recon_test"

    - name: "dp_audit"
      description: "差分隐私审计: 验证DP噪声是否正确注入"
      tool: "opacus_dp_audit"
```

**模型训练界面 (买方):**

```
┌─ 模型训练: 企业信用风险评分 ─────────────────────────────┐
│                                                          │
│  特征集: tax_stability + credit_health + legal_risk       │
│  样本数: 500,000 (经脱敏)                                 │
│                                                          │
│  算法: [XGBoost ▼]  交叉验证: [5折 ▼]                     │
│                                                          │
│  差分隐私: ✅ 已启用 (ε=2.0, δ=1e-5, clip=1.0)            │
│                                                          │
│  [开始训练]                                               │
│                                                          │
│  训练结果:                                                │
│  ┌──────────────────────────────────────────────────┐    │
│  │ AUC: 0.82  │ F1: 0.76  │ Precision: 0.79        │    │
│  │ Recall: 0.73 │ 训练时间: 45min │ DP消耗: 2.0ε    │    │
│  └──────────────────────────────────────────────────┘    │
│                                                          │
│  安全检查:                                                │
│  ✅ 成员推断: AUC=0.52 (< 0.55 阈值)                      │
│  ✅ 模型反演: 通过                                        │
│  ✅ 数据重建: 通过                                        │
│  ✅ DP审计: 通过                                          │
│                                                          │
│  [导出模型] [部署到API沙箱] [查看特征重要性]               │
└──────────────────────────────────────────────────────────┘
```

### 2.4 安全产出审查网关

```python
# gateway/model_export_inspector.py

class ModelExportInspector:
    """
    模型导出安全审查网关
    确保模型不包含/泄露训练数据
    """

    def __init__(self, config: dict):
        self.mi_threshold = config.get("membership_inference_threshold", 0.55)
        self.epsilon_budget = config.get("dp_epsilon_total", 10.0)
        self.epsilon_consumed = config.get("dp_epsilon_consumed", 0.0)

    def inspect_model_export(
        self,
        model_bytes: bytes,
        model_metadata: dict,
        training_data_sample: list,  # 用于测试的训练数据样本
        test_data_sample: list,      # 用于对比的非训练数据
    ) -> ModelExportResult:
        """
        完整的模型导出安全审查
        """
        results = {}

        # 1. 成员推断攻击测试
        mi_result = self._membership_inference_test(
            model_bytes, training_data_sample, test_data_sample
        )
        results["membership_inference"] = mi_result

        # 2. 模型反演攻击测试
        miv_result = self._model_inversion_test(
            model_bytes, model_metadata
        )
        results["model_inversion"] = miv_result

        # 3. 数据重建测试
        recon_result = self._data_reconstruction_test(
            model_bytes, training_data_sample
        )
        results["data_reconstruction"] = recon_result

        # 4. DP预算检查
        dp_result = self._check_dp_budget(
            model_metadata.get("dp_epsilon", 0)
        )
        results["dp_budget"] = dp_result

        # 5. 模型水印注入
        watermarked_model = self._inject_watermark(
            model_bytes, model_metadata
        )

        # 综合判定
        all_passed = all(r["passed"] for r in results.values())

        return ModelExportResult(
            approved=all_passed,
            model_bytes=watermarked_model if all_passed else None,
            checks=results,
            export_signature=self._sign_export(model_metadata) if all_passed else None,
        )

    def _membership_inference_test(
        self,
        model_bytes: bytes,
        train_samples: list,
        test_samples: list,
    ) -> dict:
        """
        成员推断攻击测试
        原理: 如果模型"记住"了训练数据, 那么对训练数据的预测置信度
              会显著高于非训练数据
        判定: 训练集和测试集的预测置信度分布应该接近 (AUC < 0.55)
        """
        import numpy as np
        from sklearn.metrics import roc_auc_score

        model = self._load_model(model_bytes)

        # 获取模型对训练样本的预测置信度
        train_confidences = []
        for sample in train_samples:
            pred = model.predict_proba(sample.features)
            train_confidences.append(max(pred))

        # 获取模型对测试样本的预测置信度
        test_confidences = []
        for sample in test_samples:
            pred = model.predict_proba(sample.features)
            test_confidences.append(max(pred))

        # 构造二分类任务: 训练集=1, 测试集=0
        labels = [1] * len(train_confidences) + [0] * len(test_confidences)
        scores = train_confidences + test_confidences

        auc = roc_auc_score(labels, scores)

        return {
            "passed": auc < self.mi_threshold,
            "auc": auc,
            "threshold": self.mi_threshold,
            "detail": f"MI AUC={auc:.3f} (阈值<{self.mi_threshold})",
        }

    def _model_inversion_test(self, model_bytes: bytes, metadata: dict) -> dict:
        """
        模型反演攻击测试
        原理: 对于分类模型, 检查是否能从模型权重重建训练数据的特征分布
        判定: 检查模型权重中是否包含异常大的值 (可能记忆了训练样本)
        """
        model = self._load_model(model_bytes)

        # 对于树模型: 检查叶节点是否包含单个样本 (过拟合)
        if hasattr(model, "estimators_"):
            for tree in model.estimators_:
                n_leaves = tree.get_n_leaves()
                leaf_samples = tree.get_leaf_node_sample_counts()
                # 如果有叶节点只有1-2个样本, 可能过拟合
                single_sample_leaves = sum(1 for s in leaf_samples if s <= 2)
                if single_sample_leaves / n_leaves > 0.1:  # 超过10%的叶节点
                    return {
                        "passed": False,
                        "detail": f"过拟合风险: {single_sample_leaves}/{n_leaves} 叶节点样本数≤2",
                    }

        # 对于神经网络: 检查权重分布
        if hasattr(model, "parameters"):
            for param in model.parameters():
                if param.abs().max() > 100:  # 异常大的权重
                    return {
                        "passed": False,
                        "detail": f"异常权重: max={param.abs().max():.1f}",
                    }

        return {"passed": True, "detail": "模型反演检查通过"}

    def _data_reconstruction_test(
        self, model_bytes: bytes, train_samples: list
    ) -> dict:
        """
        数据重建测试
        原理: 尝试从模型输出重建训练数据
        判定: 重建的数据与原始训练数据的相似度应低于阈值
        """
        model = self._load_model(model_bytes)

        # 对于每条训练样本, 获取模型预测
        # 然后尝试从预测结果反推输入特征
        # 如果重建相似度 > 0.8, 说明模型泄露了太多信息

        max_similarity = 0
        for sample in train_samples[:100]:  # 抽样测试
            prediction = model.predict(sample.features.reshape(1, -1))

            # 简单的重建测试: 用预测结果反推最可能的输入
            # (实际实现更复杂, 这里简化)
            if hasattr(model, "feature_importances_"):
                important_features = sample.features * model.feature_importances_
                # 检查重要特征是否能被重建
                similarity = self._compute_similarity(
                    important_features, sample.original_features
                )
                max_similarity = max(max_similarity, similarity)

        return {
            "passed": max_similarity < 0.8,
            "max_similarity": max_similarity,
            "threshold": 0.8,
            "detail": f"最大重建相似度={max_similarity:.3f} (阈值<0.8)",
        }

    def _check_dp_budget(self, requested_epsilon: float) -> dict:
        """检查DP预算"""
        remaining = self.epsilon_budget - self.epsilon_consumed
        if requested_epsilon > remaining:
            return {
                "passed": False,
                "detail": f"DP预算不足: 需要{requested_epsilon}, 剩余{remaining:.1f}",
            }
        return {
            "passed": True,
            "consumed": self.epsilon_consumed + requested_epsilon,
            "remaining": remaining - requested_epsilon,
            "detail": f"DP预算充足: 消耗{requested_epsilon}, 剩余{remaining - requested_epsilon:.1f}",
        }

    def _inject_watermark(self, model_bytes: bytes, metadata: dict) -> bytes:
        """注入模型水印 (溯源标识)"""
        import hashlib
        watermark = hashlib.sha256(
            f"{metadata['session_id']}:{metadata['product_id']}:{metadata['timestamp']}".encode()
        ).hexdigest()[:16]

        # 对于不同模型格式, 水印注入方式不同
        # ONNX: 修改metadata字段
        # Pickle: 在模型对象上添加_watermark属性
        # 这里简化处理
        return model_bytes + watermark.encode()
```

### 2.5 特征安全检查

```python
# gateway/feature_export_inspector.py

class FeatureExportInspector:
    """
    特征导出安全审查
    确保特征不可逆推原始数据
    """

    def inspect_feature_export(
        self,
        feature_matrix: list,
        feature_definitions: list,
        original_data_sample: list,
    ) -> FeatureExportResult:
        """
        检查特征矩阵是否安全可导出
        """
        checks = {}

        # 1. 聚合充分性检查
        checks["aggregation"] = self._check_aggregation_level(
            feature_matrix, feature_definitions
        )

        # 2. 不可逆性检查
        checks["irreversibility"] = self._check_irreversibility(
            feature_matrix, original_data_sample
        )

        # 3. k-匿名性检查
        checks["k_anonymity"] = self._check_k_anonymity(
            feature_matrix, k=5
        )

        # 4. 唯一性检查 (高唯一性特征可能泄露个体)
        checks["uniqueness"] = self._check_uniqueness(
            feature_matrix
        )

        all_passed = all(c["passed"] for c in checks.values())

        return FeatureExportResult(
            approved=all_passed,
            checks=checks,
        )

    def _check_aggregation_level(self, features, definitions) -> dict:
        """检查特征是否经过充分聚合"""
        for feat_def in definitions:
            if feat_def.get("aggregation") == "none":
                return {
                    "passed": False,
                    "detail": f"特征 {feat_def['name']} 未经过聚合, 不可导出",
                }
        return {"passed": True, "detail": "所有特征均经过聚合"}

    def _check_irreversibility(self, features, original_samples) -> dict:
        """检查特征是否可逆推原始数据"""
        # 对于每个特征, 检查是否能从特征值推断出原始值
        # 例如: 如果特征是"年龄", 聚合为年龄段后不可逆
        #       但如果特征是"精确收入", 则可能逆推

        for i, feat_col in enumerate(zip(*features)):
            unique_ratio = len(set(feat_col)) / len(feat_col)
            if unique_ratio > 0.9:  # 90%以上唯一值, 高风险
                return {
                    "passed": False,
                    "detail": f"特征列 {i} 唯一值比例 {unique_ratio:.1%}, 可能泄露个体",
                }

        return {"passed": True, "detail": "特征不可逆性检查通过"}

    def _check_k_anonymity(self, features, k=5) -> dict:
        """k-匿名性检查: 每种特征组合至少有k条记录"""
        from collections import Counter

        # 将特征矩阵转为tuple以便计数
        feature_tuples = [tuple(row) for row in features]
        counts = Counter(feature_tuples)

        min_group_size = min(counts.values())
        if min_group_size < k:
            violating_groups = sum(1 for v in counts.values() if v < k)
            return {
                "passed": False,
                "detail": f"{violating_groups} 组特征组合样本数<{k}, 不满足{k}-匿名",
            }

        return {"passed": True, "detail": f"满足 {k}-匿名性 (最小组={min_group_size})"}
```

---

## 3. 模型部署模式

### 3.1 三种部署模式

```
模式1: 模型文件导出 (离线部署)
  ┌─────────────────────────────────────────────┐
  │  适用: 买方有自己的推理基础设施               │
  │  流程:                                       │
  │  ① 沙箱内训练 → ② 安全审查 → ③ 导出模型文件  │
  │  ④ 买方部署到自己的服务器                     │
  │  安全: 模型经过防记忆化检查+DP+水印            │
  │  风险: 模型文件离开沙箱控制范围                │
  └─────────────────────────────────────────────┘

模式2: API沙箱推理 (在线部署)
  ┌─────────────────────────────────────────────┐
  │  适用: 买方不想/不能自己部署模型              │
  │  流程:                                       │
  │  ① 沙箱内训练 → ② 模型部署在沙箱内API服务    │
  │  ③ 买方通过API调用推理                       │
  │  ④ 沙箱返回预测结果 (不含模型/不含数据)       │
  │  安全: 模型和数据都不出沙箱                    │
  │  优势: 最高安全级别                           │
  └─────────────────────────────────────────────┘

模式3: 联邦推理 (多方数据)
  ┌─────────────────────────────────────────────┐
  │  适用: 模型需要多方数据联合推理               │
  │  流程:                                       │
  │  ① 各数商在各自沙箱内计算特征                 │
  │  ② 特征向量(加密)汇总到联合沙箱               │
  │  ③ 联合沙箱内执行推理                        │
  │  ④ 返回预测结果                              │
  │  安全: 各方数据不出各自沙箱                    │
  └─────────────────────────────────────────────┘
```

### 3.2 API沙箱推理服务

```yaml
# API沙箱配置
APISandbox:
  id: "api-risk-scoring"
  name: "企业信用风险评分API"
  model: "mt-risk-001"

  # API接口
  endpoints:
    - path: "/v1/score"
      method: "POST"
      input:
        ent_id: "string"       # 企业ID (加密传入)
        industry_code: "string" # 行业代码
        year: "integer"        # 年份
      output:
        risk_score: "float"    # 风险评分 0-100
        risk_level: "string"   # 风险等级: low/medium/high
        confidence: "float"    # 置信度
      # 注意: 不输出原始特征值, 不输出模型参数

    - path: "/v1/batch_score"
      method: "POST"
      input:
        items: "array"         # 批量评分请求
      output:
        results: "array"       # 批量评分结果
      constraints:
        maxBatchSize: 1000

  # 安全约束
  security:
    rateLimit: "100/min"       # 频率限制
    authRequired: true          # SM2证书认证
    auditAll: true              # 记录所有调用
    forbidModelDownload: true   # 禁止下载模型
    forbidDataAccess: true      # 禁止访问训练数据

  # 性能
  performance:
    latencyP99: "50ms"
    throughput: "1000 QPS"
    availability: "99.9%"
```

**API沙箱推理流程:**

```
买方应用
  │
  │ POST /v1/score
  │ {"ent_id": "110101...", "industry_code": "C39", "year": 2024}
  │
  ▼
┌──────────────────────────────────────────────────────────┐
│  API沙箱 (常驻服务, 模型在内存中)                          │
│                                                          │
│  ① 认证: 验证SM2证书                                     │
│  ② 频率检查: 100/min                                     │
│  ③ 输入校验: 参数类型/范围检查                             │
│  ④ 特征计算: 在沙箱内查询数据库, 计算特征                  │
│     → tax_stability(ent_id) → 0.35                       │
│     → credit_health(ent_id) → 0.08                       │
│     → legal_risk(ent_id) → 2                             │
│  ⑤ 模型推理: XGBoost.predict(features) → 72.3            │
│  ⑥ 结果格式化: {risk_score: 72.3, risk_level: "medium"}  │
│  ⑦ 审计日志: 记录请求/响应/耗时                           │
│  ⑧ 返回结果 (不含原始特征值)                              │
└──────────────────────────────────────────────────────────┘
  │
  │ {"risk_score": 72.3, "risk_level": "medium", "confidence": 0.85}
  │
  ▼
买方应用 (集成到风控系统)
```

---

## 4. 联合建模 (多方数据)

### 4.1 联合建模架构

```
┌─────────────────────────────────────────────────────────────────┐
│  联合建模场景: 多数商数据 + 买方算法                              │
│                                                                 │
│  数商A (税务)          数商B (司法)          数商C (信贷)         │
│  ┌──────────┐        ┌──────────┐        ┌──────────┐          │
│  │ 沙箱A    │        │ 沙箱B    │        │ 沙箱C    │          │
│  │ 本地特征  │        │ 本地特征  │        │ 本地特征  │          │
│  │ 计算     │        │ 计算     │        │ 计算     │          │
│  └────┬─────┘        └────┬─────┘        └────┬─────┘          │
│       │ 加密特征向量       │ 加密特征向量       │ 加密特征向量     │
│       ▼                   ▼                   ▼                │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │  联合计算沙箱 (MPC/TEE)                                  │    │
│  │                                                         │    │
│  │  ① 三方特征向量在TEE内解密                               │    │
│  │  ② 特征拼接 (横向联邦)                                   │    │
│  │  ③ 模型训练 (XGBoost + DP)                              │    │
│  │  ④ 模型安全审查                                          │    │
│  │  ⑤ 导出模型 (经过审查)                                   │    │
│  └─────────────────────────────────────────────────────────┘    │
│       │                                                          │
│       ▼                                                          │
│  买方获得: 合规训练的风控模型 (不含任何一方原始数据)                │
└─────────────────────────────────────────────────────────────────┘
```

### 4.2 联合建模安全约束

```yaml
FederatedModeling:
  id: "fm-risk-001"
  name: "多方联合风控建模"
  participants:
    - role: "data_provider"
      space: "space-tax"
      product: "dp-tax-001"
      contribution: "features"  # 贡献特征向量
    - role: "data_provider"
      space: "space-legal"
      product: "dp-legal-001"
      contribution: "features"
    - role: "data_provider"
      space: "space-credit"
      product: "dp-credit-001"
      contribution: "features"
    - role: "model_owner"
      space: "space-bank"
      contribution: "algorithm"

  # 安全约束
  security:
    # 各方数据不出各自沙箱
    dataIsolation: "strict"
    # 仅特征向量可传输 (加密)
    allowedTransfer: "encrypted_feature_vectors"
    # 联合计算在TEE内
    jointComputeEnvironment: "TEE"
    # 模型训练使用DP
    dpEnabled: true
    dpEpsilon: 3.0
    # 模型导出需所有数商确认
    exportRequiresAllApproval: true
```

---

## 5. 总结

| 场景 | 传统痛点 | 解决方案 |
|------|---------|---------|
| 风控建模 | 数据不出域, 无法建模 | 特征工程沙箱 + 安全模型导出 |
| 模型部署 | 模型离开沙箱有泄露风险 | 防记忆化检查 + DP + 水印 |
| 实时推理 | 买方需要实时评分 | API沙箱推理 (模型不出沙箱) |
| 多方建模 | 数据不能汇聚 | 联合计算沙箱 (MPC/TEE) |
| 特征迭代 | 特征工程需要反复实验 | 特征模板 + 沙箱内预览 |
| 模型更新 | 新数据需要重新训练 | 增量训练 + 版本管理 |
