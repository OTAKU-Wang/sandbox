"""Extended output inspection tests — edge cases, all patterns, DP integration."""
import pytest
from app.services.output_inspection import OutputInspector, DifferentialPrivacyEngine


def test_detect_bank_card():
    inspector = OutputInspector()
    result = inspector.inspect("银行卡：6222021234567890123", "u1", "s1")
    assert not result.passed
    assert any(f.type == "bank_card" for f in result.findings)


def test_detect_ip_address():
    inspector = OutputInspector()
    result = inspector.inspect("服务器IP：192.168.1.100", "u1", "s1")
    assert any(f.type == "ip_address" for f in result.findings)


def test_detect_passport():
    inspector = OutputInspector()
    result = inspector.inspect("护照号：G12345678", "u1", "s1")
    assert any(f.type == "passport" for f in result.findings)


def test_detect_email():
    inspector = OutputInspector()
    result = inspector.inspect("联系：user@example.com", "u1", "s1")
    assert any(f.type == "email" for f in result.findings)


def test_detect_social_credit_code():
    inspector = OutputInspector()
    # 18 chars: 2 letters + 6 digits + 10 alphanumeric
    result = inspector.inspect("统一社会信用代码：91110000MA001A1B2C", "u1", "s1")
    assert any(f.type == "social_credit_code" for f in result.findings)


def test_multiple_sensitive_types():
    inspector = OutputInspector()
    result = inspector.inspect("电话13812345678，邮箱test@x.com，身份证110101199001011234", "u1", "s1")
    assert not result.passed
    types = {f.type for f in result.findings}
    assert "phone" in types
    assert "email" in types
    assert "id_card" in types


def test_empty_output():
    inspector = OutputInspector()
    result = inspector.inspect("", "u1", "s1")
    assert not result.passed
    assert result.stage_results["format_validation"] is False


def test_whitespace_only_output():
    inspector = OutputInspector()
    result = inspector.inspect("   \n\t  ", "u1", "s1")
    assert not result.passed
    assert result.stage_results["format_validation"] is False


def test_critical_blocks_approval():
    """Critical severity findings should block final approval."""
    inspector = OutputInspector()
    # ID card is critical severity
    result = inspector.inspect("身份证110101199001011234", "u1", "s1")
    assert result.stage_results["final_approval"] is False


def test_medium_allows_approval():
    """Medium severity (email) should not block approval."""
    inspector = OutputInspector()
    result = inspector.inspect("联系user@example.com", "u1", "s1")
    # Email is medium, so approval should still pass
    assert result.stage_results["final_approval"] is True


def test_watermark_deterministic():
    inspector = OutputInspector()
    w1 = inspector.inspect("data", "u1", "s1").watermark
    w2 = inspector.inspect("data", "u1", "s1").watermark
    assert w1 == w2


def test_watermark_different_sessions():
    inspector = OutputInspector()
    w1 = inspector.inspect("data", "u1", "s1").watermark
    w2 = inspector.inspect("data", "u1", "s2").watermark
    assert w1 != w2


def test_dp_applied_flag():
    inspector = OutputInspector()
    r1 = inspector.inspect("data", "u1", "s1", dp_epsilon=1.0)
    assert r1.dp_applied is True
    r2 = inspector.inspect("data", "u1", "s1")
    assert r2.dp_applied is False


def test_redacted_output_contains_watermark_when_clean():
    """redacted_output should contain watermarked text even when no DLP findings."""
    inspector = OutputInspector()
    result = inspector.inspect("clean data here", "u1", "s1")
    assert result.redacted_output is not None
    assert result.redacted_output.startswith("clean data here")
    # Watermark should be steganographically embedded
    assert inspector.verify_watermark(result.redacted_output, "u1", "s1") is True


def test_dp_engine_gaussian_noise():
    engine = DifferentialPrivacyEngine()
    noisy = engine.add_gaussian_noise(100.0, sensitivity=1.0, epsilon=1.0)
    assert isinstance(noisy, float)
    assert noisy != 100.0  # Extremely unlikely to be exactly equal


def test_dp_engine_multiple_sessions():
    engine = DifferentialPrivacyEngine()
    engine.init_budget("s1", 10.0)
    engine.init_budget("s2", 5.0)
    assert engine.get_remaining("s1") == 10.0
    assert engine.get_remaining("s2") == 5.0
    engine.consume("s1", 3.0)
    assert engine.get_remaining("s1") == 7.0
    assert engine.get_remaining("s2") == 5.0


def test_id_card_not_truncated_by_phone():
    """Regression: phone regex must not match inside ID card number."""
    inspector = OutputInspector()
    result = inspector.inspect("身份证号110101199001011234", "u1", "s1")
    types = {f.type for f in result.findings}
    assert "id_card" in types
    # ID card should be redacted as a whole, not partially by phone regex
    assert "[REDACTED:id_card]" in result.redacted_output


def test_bank_card_not_matched_by_phone():
    """Regression: 19-digit bank card must not be matched by phone regex."""
    inspector = OutputInspector()
    result = inspector.inspect("银行卡6222021234567890123", "u1", "s1")
    types = {f.type for f in result.findings}
    assert "bank_card" in types
    # Bank card (19 digits) should not be partially matched as phone
    phone_findings = [f for f in result.findings if f.type == "phone"]
    assert len(phone_findings) == 0
