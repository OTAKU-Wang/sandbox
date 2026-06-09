import pytest
from app.services.output_inspection import (
    OutputInspector, DifferentialPrivacyEngine, InspectionStage, SandboxMode,
)
from app.utils.watermark import generate_watermark


def test_detect_phone_numbers():
    inspector = OutputInspector()
    result = inspector.inspect("联系方式：13812345678", "user-1", "session-1")
    assert not result.passed
    assert any(f.type == "phone" for f in result.findings)


def test_detect_id_card():
    inspector = OutputInspector()
    result = inspector.inspect("身份证：110101199001011234", "user-1", "session-1")
    assert not result.passed
    assert any(f.type == "id_card" for f in result.findings)


def test_clean_output():
    inspector = OutputInspector()
    result = inspector.inspect("分析结果：平均值为 42.5，无敏感信息", "user-1", "session-1")
    assert result.passed
    assert len(result.findings) == 0


def test_redaction():
    inspector = OutputInspector()
    result = inspector.inspect("电话：13812345678，邮箱：test@example.com", "user-1", "session-1")
    assert not result.passed
    assert "[REDACTED:phone]" in result.redacted_output
    assert "[REDACTED:email]" in result.redacted_output


def test_watermark():
    inspector = OutputInspector()
    result = inspector.inspect("Result data", "user-1", "session-1")
    assert result.watermark is not None
    assert len(result.watermark) > 0
    # Watermark should be steganographically embedded in the output
    assert result.redacted_output is not None
    extracted = inspector.extract_watermark(result.redacted_output)
    assert extracted is not None
    assert result.watermark in extracted


def test_watermark_verify():
    inspector = OutputInspector()
    result = inspector.inspect("Clean analysis output", "user-1", "session-1")
    # verify_watermark should work with the steganographic embedding
    assert inspector.verify_watermark(result.redacted_output, "user-1", "session-1") is True
    assert inspector.verify_watermark(result.redacted_output, "user-2", "session-1") is False


def test_watermark_invisible():
    """Watermark should be invisible — the original text should be readable."""
    inspector = OutputInspector()
    original = "查询结果：共 100 条记录"
    result = inspector.inspect(original, "user-1", "session-1")
    # The watermarked output should start with the original text
    assert result.redacted_output.startswith(original)


def test_stage_results():
    inspector = OutputInspector()
    result = inspector.inspect("Clean output", "user-1", "session-1")
    assert "format_validation" in result.stage_results
    assert "dlp_scan" in result.stage_results
    assert "watermark" in result.stage_results
    assert "signature" in result.stage_results
    assert "final_approval" in result.stage_results


def test_dp_engine_budget():
    engine = DifferentialPrivacyEngine()
    engine.init_budget("session-1", 10.0)
    assert engine.get_remaining("session-1") == 10.0
    assert engine.consume("session-1", 3.0) is True
    assert engine.get_remaining("session-1") == 7.0
    assert engine.consume("session-1", 8.0) is False  # Insufficient budget


def test_dp_laplace_noise():
    engine = DifferentialPrivacyEngine()
    noisy = engine.add_laplace_noise(100.0, sensitivity=1.0, epsilon=1.0)
    # Noise should be finite
    assert isinstance(noisy, float)


# ─── DLP 模式顺序回归测试（P2 Bug修复） ───

def test_id_card_not_truncated_by_phone_pattern():
    """身份证号不应被phone正则截断匹配（18位数字中包含11位phone子串）"""
    inspector = OutputInspector()
    id_card = "110101199001011234"
    result = inspector.inspect(f"身份证号：{id_card}", "user-1", "session-1")
    # 应检测到 id_card，不应检测到 phone
    id_card_findings = [f for f in result.findings if f.type == "id_card"]
    phone_findings = [f for f in result.findings if f.type == "phone"]
    assert len(id_card_findings) == 1, "Should detect ID card"
    assert len(phone_findings) == 0, "Phone pattern should NOT match inside ID card"
    # 脱敏应为 [REDACTED:id_card]
    assert "[REDACTED:id_card]" in result.redacted_output
    assert "[REDACTED:phone]" not in result.redacted_output


def test_bank_card_not_matched_by_phone():
    """19位银行卡号不应被phone正则匹配"""
    inspector = OutputInspector()
    bank_card = "6222021234567890123"
    result = inspector.inspect(f"银行卡：{bank_card}", "user-1", "session-1")
    phone_findings = [f for f in result.findings if f.type == "phone"]
    assert len(phone_findings) == 0, "Phone pattern should NOT match inside bank card"


def test_phone_still_detected_standalone():
    """独立的手机号仍应被正确检测"""
    inspector = OutputInspector()
    result = inspector.inspect("联系电话：13812345678", "user-1", "session-1")
    phone_findings = [f for f in result.findings if f.type == "phone"]
    assert len(phone_findings) == 1, "Standalone phone number should be detected"


def test_mixed_sensitive_data():
    """混合敏感数据：身份证+手机号+邮箱，各自正确匹配不交叉"""
    inspector = OutputInspector()
    text = "身份证：110101199001011234，手机：13812345678，邮箱：test@example.com"
    result = inspector.inspect(text, "user-1", "session-1")
    types = {f.type for f in result.findings}
    assert "id_card" in types
    assert "phone" in types
    assert "email" in types
    # 不应有重复或交叉匹配
    assert len([f for f in result.findings if f.type == "id_card"]) == 1
    assert len([f for f in result.findings if f.type == "phone"]) == 1


# ─── Scene Routing Tests ───

def test_query_mode_includes_k_anonymity():
    inspector = OutputInspector()
    stages = inspector.SCENE_RULES[SandboxMode.STRUCTURED_QUERY.value]
    assert InspectionStage.K_ANONYMITY.value in stages
    assert InspectionStage.MIA.value not in stages


def test_train_mode_includes_mia():
    inspector = OutputInspector()
    stages = inspector.SCENE_RULES[SandboxMode.STRUCTURED_MODELING.value]
    assert InspectionStage.MIA.value in stages


def test_develop_mode_minimal_stages():
    inspector = OutputInspector()
    stages = inspector.SCENE_RULES[SandboxMode.DEVELOP.value]
    assert InspectionStage.K_ANONYMITY.value not in stages
    assert InspectionStage.MIA.value not in stages


# ─── k-Anonymity Tests ───

def test_k_anonymity_pass():
    inspector = OutputInspector()
    rows = [
        {"city": "BJ", "age": "20"},
        {"city": "BJ", "age": "20"},
        {"city": "BJ", "age": "20"},
        {"city": "BJ", "age": "20"},
        {"city": "BJ", "age": "20"},
    ]
    passed, violations = inspector.check_k_anonymity(rows)
    assert passed is True
    assert len(violations) == 0


def test_k_anonymity_fail():
    inspector = OutputInspector()
    rows = [
        {"city": "BJ", "age": "20"},
        {"city": "BJ", "age": "20"},
        {"city": "Rare", "age": "90"},
    ]
    passed, violations = inspector.check_k_anonymity(rows)
    assert passed is False
    # Both groups are below k=5
    assert len(violations) == 2


def test_k_anonymity_custom_k():
    inspector = OutputInspector()
    rows = [{"a": "x"}, {"a": "x"}]
    assert inspector.check_k_anonymity(rows, k_min=2)[0] is True
    assert inspector.check_k_anonymity(rows, k_min=3)[0] is False


def test_k_anonymity_empty():
    inspector = OutputInspector()
    assert inspector.check_k_anonymity([])[0] is True


# ─── MIA Risk Tests ───

def test_mia_safe():
    inspector = OutputInspector()
    is_safe, risk = inspector.check_mia_risk(
        {"confidence": 0.5, "loss": 2.0}, training_data_size=1000
    )
    assert is_safe is True
    assert risk < 0.6


def test_mia_risky():
    inspector = OutputInspector()
    is_safe, risk = inspector.check_mia_risk(
        {"confidence": 0.99, "loss": 0.01}, training_data_size=100
    )
    assert is_safe is False


# ─── Scene-Aware Pipeline Tests ───

def test_query_mode_with_k_anonymity_check():
    inspector = OutputInspector()
    rows = [
        {"city": "BJ", "count": 100},
        {"city": "SH", "count": 200},
    ]
    result = inspector.inspect(
        "查询结果", "u1", "s1",
        sandbox_mode=SandboxMode.STRUCTURED_QUERY.value,
        output_rows=rows,
    )
    assert InspectionStage.K_ANONYMITY.value in result.stage_results


def test_reconstruction_skips_without_source_rows():
    """Output rows alone are not a source baseline and must not self-match."""
    inspector = OutputInspector()
    rows = [{"city": "BJ", "bucket": "20"} for _ in range(5)]
    result = inspector.inspect(
        "aggregate rows",
        "u1",
        "s1",
        sandbox_mode=SandboxMode.STRUCTURED_QUERY.value,
        output_rows=rows,
    )
    assert result.stage_results[InspectionStage.DATA_RECONSTRUCTION.value] is True
    assert not any(f.type == "data_reconstruction" for f in result.findings)
    assert result.passed is True


def test_train_mode_with_mia_stage():
    inspector = OutputInspector()
    result = inspector.inspect(
        "训练完成", "u1", "s1",
        sandbox_mode=SandboxMode.STRUCTURED_MODELING.value,
    )
    assert InspectionStage.MIA.value in result.stage_results


def test_develop_mode_skips_advanced_stages():
    inspector = OutputInspector()
    result = inspector.inspect(
        "调试输出", "u1", "s1",
        sandbox_mode=SandboxMode.DEVELOP.value,
    )
    assert InspectionStage.K_ANONYMITY.value not in result.stage_results
    assert result.passed is True
