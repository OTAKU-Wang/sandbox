"""Tests for Training Pipeline Manager."""
import pytest
from app.services.training_pipeline import (
    TrainingPipelineManager, PipelineType, PipelineStage, PipelineConfig,
    PipelineResult, PIIResult,
)


class TestPIIScrubbing:

    def setup_method(self):
        self.pipeline = TrainingPipelineManager()

    def test_detect_id_card(self):
        result = self.pipeline.scrub_pii("身份证号：110101199001011234")
        assert result.has_pii
        assert any(f["type"] == "id_card" for f in result.findings)
        assert "[ID_CARD]" in result.cleaned_text
        assert "110101199001011234" not in result.cleaned_text

    def test_detect_phone(self):
        result = self.pipeline.scrub_pii("手机：13812345678")
        assert result.has_pii
        assert any(f["type"] == "phone" for f in result.findings)
        assert "[PHONE]" in result.cleaned_text

    def test_detect_email(self):
        result = self.pipeline.scrub_pii("邮箱：test@example.com")
        assert result.has_pii
        assert any(f["type"] == "email" for f in result.findings)
        assert "[EMAIL]" in result.cleaned_text

    def test_detect_bank_card(self):
        result = self.pipeline.scrub_pii("卡号：6222021234567890123")
        assert result.has_pii
        assert any(f["type"] == "bank_card" for f in result.findings)
        assert "[BANK_CARD]" in result.cleaned_text

    def test_no_pii(self):
        result = self.pipeline.scrub_pii("今天天气不错，适合出门散步。")
        assert not result.has_pii
        assert len(result.findings) == 0
        assert result.cleaned_text == "今天天气不错，适合出门散步。"

    def test_multiple_pii_types(self):
        text = "姓名张三，手机13812345678，邮箱zhang@test.com"
        result = self.pipeline.scrub_pii(text)
        assert result.has_pii
        assert len(result.findings) >= 2  # phone + email
        assert "13812345678" not in result.cleaned_text
        assert "zhang@test.com" not in result.cleaned_text

    def test_id_card_not_truncated_by_phone(self):
        """18-digit ID card should not be partially matched by phone pattern."""
        result = self.pipeline.scrub_pii("身份证：110101199001011234")
        id_findings = [f for f in result.findings if f["type"] == "id_card"]
        phone_findings = [f for f in result.findings if f["type"] == "phone"]
        assert len(id_findings) == 1
        assert len(phone_findings) == 0


class TestQualityScoring:

    def setup_method(self):
        self.pipeline = TrainingPipelineManager()

    def test_long_instruction_high_score(self):
        record = {
            "instruction": "请详细分析一下中国近十年的经济发展趋势，包括GDP增长、产业结构变化、国际贸易情况等多个维度。" * 3,
            "output": "中国经济在过去十年经历了显著的发展。" * 10,
        }
        criteria = [
            {"name": "instruction_clarity", "weight": 0.5},
            {"name": "output_completeness", "weight": 0.5},
        ]
        score, details = self.pipeline.check_quality_score(record, criteria)
        assert score > 0.5
        assert details["instruction_clarity"] > 0.5

    def test_short_instruction_low_score(self):
        record = {
            "instruction": "说",
            "output": "好",
        }
        criteria = [
            {"name": "instruction_clarity", "weight": 1.0},
        ]
        score, details = self.pipeline.check_quality_score(record, criteria)
        assert score < 0.5

    def test_question_mark_bonus(self):
        record_q = {"instruction": "什么是机器学习？请详细解释。", "output": "机器学习是..."}
        record_no_q = {"instruction": "请详细解释机器学习的基本概念和应用场景", "output": "机器学习是..."}
        criteria = [{"name": "instruction_clarity", "weight": 1.0}]

        score_q, _ = self.pipeline.check_quality_score(record_q, criteria)
        score_no_q, _ = self.pipeline.check_quality_score(record_no_q, criteria)
        assert score_q >= score_no_q

    def test_repetition_penalty(self):
        text = "这是一段重复" * 100
        record = {"text": text}
        criteria = [{"name": "repetition_ratio", "weight": 1.0}]
        score, _ = self.pipeline.check_quality_score(record, criteria)
        assert score < 0.5

    def test_special_char_penalty(self):
        record = {"text": "正常文字" + "!@#$%^&*" * 50}
        criteria = [{"name": "special_char_ratio", "weight": 1.0}]
        score, _ = self.pipeline.check_quality_score(record, criteria)
        assert score < 0.8


class TestMinHashSimilarity:

    def setup_method(self):
        self.pipeline = TrainingPipelineManager()

    def test_identical_texts(self):
        text = "这是一段用于测试的中文文本内容"
        sim = self.pipeline.compute_minhash_similarity(text, text)
        assert sim == 1.0

    def test_completely_different(self):
        sim = self.pipeline.compute_minhash_similarity("AAAA", "BBBB")
        assert sim < 0.5

    def test_empty_texts(self):
        assert self.pipeline.compute_minhash_similarity("", "abc") == 0.0
        assert self.pipeline.compute_minhash_similarity("abc", "") == 0.0

    def test_similar_texts(self):
        t1 = "中华人民共和国位于亚洲东部，太平洋西岸。"
        t2 = "中华人民共和国位于亚洲东部，是世界大国。"
        sim = self.pipeline.compute_minhash_similarity(t1, t2)
        assert 0.3 < sim < 1.0


class TestMIARisk:

    def setup_method(self):
        self.pipeline = TrainingPipelineManager()

    def test_safe_model(self):
        is_safe, risk = self.pipeline.check_mia_risk(
            model_confidence=0.5, model_loss=2.0, threshold=0.6
        )
        assert is_safe
        assert risk < 0.6

    def test_risky_model(self):
        is_safe, risk = self.pipeline.check_mia_risk(
            model_confidence=0.99, model_loss=0.01, threshold=0.6
        )
        assert not is_safe
        assert risk > 0.6

    def test_boundary(self):
        is_safe, risk = self.pipeline.check_mia_risk(
            model_confidence=0.6, model_loss=1.0, threshold=0.6
        )
        # 0.6 * (1/(1+1)) = 0.3 → safe
        assert is_safe


class TestSFTFormatting:

    def setup_method(self):
        self.pipeline = TrainingPipelineManager()

    def test_alpaca_format(self):
        record = {"instruction": "翻译成英文", "input": "你好", "output": "Hello"}
        result = self.pipeline.format_sft_record(record, "alpaca")
        assert result["instruction"] == "翻译成英文"
        assert result["input"] == "你好"
        assert result["output"] == "Hello"

    def test_chatml_format(self):
        record = {"instruction": "翻译成英文", "input": "你好", "output": "Hello"}
        result = self.pipeline.format_sft_record(record, "chatml")
        assert "messages" in result
        assert result["messages"][0]["role"] == "user"
        assert result["messages"][1]["role"] == "assistant"
        assert "你好" in result["messages"][0]["content"]

    def test_sharegpt_format(self):
        record = {"instruction": "翻译成英文", "input": "", "output": "Hello"}
        result = self.pipeline.format_sft_record(record, "sharegpt")
        assert "conversations" in result
        assert result["conversations"][0]["from"] == "human"
        assert result["conversations"][1]["from"] == "gpt"

    def test_unknown_format_raises(self):
        with pytest.raises(ValueError, match="Unknown"):
            self.pipeline.format_sft_record({"instruction": "x", "input": "", "output": "y"}, "invalid")


class TestDatasetSplit:

    def setup_method(self):
        self.pipeline = TrainingPipelineManager()

    def test_split_ratio(self):
        records = [{"id": i} for i in range(100)]
        train, val = self.pipeline.split_dataset(records, train_ratio=0.8)
        assert len(train) == 80
        assert len(val) == 20

    def test_split_preserves_all(self):
        records = [{"id": i} for i in range(50)]
        train, val = self.pipeline.split_dataset(records, train_ratio=0.9)
        assert len(train) + len(val) == 50

    def test_split_empty(self):
        train, val = self.pipeline.split_dataset([], train_ratio=0.8)
        assert len(train) == 0
        assert len(val) == 0


class TestPipelineStages:

    def setup_method(self):
        self.pipeline = TrainingPipelineManager()

    def test_pretrain_stages(self):
        stages = self.pipeline.get_stages(PipelineType.PRETRAIN)
        assert PipelineStage.FORMAT_VALIDATION in stages
        assert PipelineStage.DEDUPLICATION in stages
        assert PipelineStage.PII_SCRUB in stages
        assert PipelineStage.TOKENIZATION in stages

    def test_sft_stages(self):
        stages = self.pipeline.get_stages(PipelineType.SFT)
        assert PipelineStage.QUALITY_FILTER in stages
        assert PipelineStage.FORMAT_CONVERSION in stages
        assert PipelineStage.TRAIN_TEST_SPLIT in stages
        assert PipelineStage.MIA_CHECK in stages
        assert PipelineStage.WATERMARK in stages

    def test_rag_stages(self):
        stages = self.pipeline.get_stages(PipelineType.RAG)
        assert PipelineStage.CHUNKING in stages
        assert PipelineStage.EMBEDDING in stages
        assert PipelineStage.DEDUPLICATION not in stages

    def test_multimodal_stages(self):
        stages = self.pipeline.get_stages(PipelineType.MULTIMODAL)
        assert PipelineStage.IMAGE_PREPROCESS in stages
        assert PipelineStage.PAIRING in stages

    def test_token_estimation(self):
        chinese_text = "这是一段中文文本" * 100
        tokens = self.pipeline.estimate_tokens(chinese_text)
        assert tokens > 0
        assert tokens < len(chinese_text)  # Should be fewer tokens than chars
