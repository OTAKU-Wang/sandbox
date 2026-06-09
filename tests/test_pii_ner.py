"""Tests for PII NER service — dual-layer detection (regex + NER)."""
import pytest
from app.services.pii_ner import PIINERService, PIIType, Severity


@pytest.fixture
def service():
    return PIINERService(enable_ner=True)


@pytest.fixture
def regex_only_service():
    return PIINERService(enable_ner=False)


# ── Layer 1: Regex Detection ──────────────────────────────────────

class TestRegexDetection:
    def test_id_card(self, service):
        text = "身份证号：110101199003076534"
        result = service.detect(text)
        assert result.has_pii is True
        id_matches = [m for m in result.matches if m.pii_type == PIIType.ID_CARD]
        assert len(id_matches) == 1
        assert id_matches[0].value == "110101199003076534"
        assert id_matches[0].severity == Severity.CRITICAL

    def test_phone(self, service):
        text = "联系电话：13800138000"
        result = service.detect(text)
        phone_matches = [m for m in result.matches if m.pii_type == PIIType.PHONE]
        assert len(phone_matches) == 1
        assert phone_matches[0].value == "13800138000"

    def test_email(self, service):
        text = "邮箱：zhangsan@example.com"
        result = service.detect(text)
        email_matches = [m for m in result.matches if m.pii_type == PIIType.EMAIL]
        assert len(email_matches) == 1
        assert email_matches[0].value == "zhangsan@example.com"

    def test_bank_card(self, service):
        text = "银行卡号：6222021234567890123"
        result = service.detect(text)
        bank_matches = [m for m in result.matches if m.pii_type == PIIType.BANK_CARD]
        assert len(bank_matches) == 1

    def test_passport(self, service):
        text = "护照号：G12345678"
        result = service.detect(text)
        passport_matches = [m for m in result.matches if m.pii_type == PIIType.PASSPORT]
        assert len(passport_matches) == 1

    def test_ip_address(self, service):
        text = "服务器IP：192.168.1.100"
        result = service.detect(text)
        ip_matches = [m for m in result.matches if m.pii_type == PIIType.IP_ADDRESS]
        assert len(ip_matches) == 1

    def test_no_pii(self, service):
        text = "这是一段没有敏感信息的普通文本"
        result = service.detect(text)
        assert result.has_pii is False
        assert result.match_count == 0

    def test_empty_text(self, service):
        result = service.detect("")
        assert result.has_pii is False

    def test_multiple_pii(self, service):
        text = "张三的手机号是13800138000，邮箱是zhangsan@example.com"
        result = service.detect(text)
        assert result.has_pii is True
        types = {m.pii_type for m in result.matches}
        assert PIIType.PHONE in types
        assert PIIType.EMAIL in types


# ── Layer 2: NER Detection ────────────────────────────────────────

class TestNERDetection:
    def test_person_name_with_context(self, service):
        text = "联系人：张三，手机号13800138000"
        result = service.detect(text)
        name_matches = [m for m in result.matches if m.pii_type == PIIType.PERSON_NAME]
        assert len(name_matches) == 1
        assert name_matches[0].value == "张三"

    def test_address_detection(self, service):
        text = "地址：北京市海淀区中关村大街1号"
        result = service.detect(text)
        addr_matches = [m for m in result.matches if m.pii_type == PIIType.ADDRESS]
        assert len(addr_matches) == 1
        assert "北京" in addr_matches[0].value

    def test_organization_detection(self, service):
        text = "单位：北京大学"
        result = service.detect(text)
        org_matches = [m for m in result.matches if m.pii_type == PIIType.ORGANIZATION]
        assert len(org_matches) == 1
        assert "北京大学" in org_matches[0].value

    def test_date_of_birth(self, service):
        text = "出生日期：1990年03月07日"
        result = service.detect(text)
        dob_matches = [m for m in result.matches if m.pii_type == PIIType.DATE_OF_BIRTH]
        assert len(dob_matches) == 1

    def test_ner_layer_disabled(self, regex_only_service):
        text = "联系人：张三，手机号13800138000"
        result = regex_only_service.detect(text)
        # Phone should be detected (regex layer)
        phone_matches = [m for m in result.matches if m.pii_type == PIIType.PHONE]
        assert len(phone_matches) == 1
        # Name should NOT be detected (NER disabled)
        name_matches = [m for m in result.matches if m.pii_type == PIIType.PERSON_NAME]
        assert len(name_matches) == 0

    def test_ner_layer_field(self, service):
        text = "姓名：李四"
        result = service.detect(text)
        assert result.has_pii is True
        ner_matches = [m for m in result.matches if m.layer == "ner"]
        assert len(ner_matches) >= 1

    def test_company_detection(self, service):
        text = "供应商：阿里巴巴集团有限公司"
        result = service.detect(text)
        org_matches = [m for m in result.matches if m.pii_type == PIIType.ORGANIZATION]
        assert len(org_matches) >= 1
        assert "阿里巴巴" in org_matches[0].value


# ── Redaction ─────────────────────────────────────────────────────

class TestRedaction:
    def test_redact_phone(self, service):
        text = "手机号13800138000"
        result = service.detect(text)
        assert "[REDACTED:phone]" in result.redacted_text
        assert "13800138000" not in result.redacted_text

    def test_redact_multiple(self, service):
        text = "张三，手机13800138000，邮箱test@example.com"
        result = service.detect(text)
        assert "13800138000" not in result.redacted_text
        assert "test@example.com" not in result.redacted_text
        assert "REDACTED" in result.redacted_text

    def test_redact_preserves_non_pii(self, service):
        text = "普通文本13800138000更多文本"
        result = service.detect(text)
        assert result.redacted_text.startswith("普通文本")
        assert result.redacted_text.endswith("更多文本")

    def test_no_redaction_when_clean(self, service):
        text = "没有敏感信息"
        result = service.detect(text)
        assert result.redacted_text == text


# ── Edge Cases ────────────────────────────────────────────────────

class TestEdgeCases:
    def test_id_card_boundary(self, service):
        """ID card must be exactly 18 digits."""
        # 17 digits - too short
        result = service.detect("11010119900307653")
        id_matches = [m for m in result.matches if m.pii_type == PIIType.ID_CARD]
        assert len(id_matches) == 0

    def test_phone_boundary(self, service):
        """Phone must start with 1[3-9] and be 11 digits."""
        # 10 digits - too short
        result = service.detect("1380013800")
        phone_matches = [m for m in result.matches if m.pii_type == PIIType.PHONE]
        assert len(phone_matches) == 0

    def test_severity_ordering(self, service):
        """ID card (critical) should take precedence over phone (high) if overlapping."""
        # This tests the overlap resolution
        text = "110101199003076534"
        result = service.detect(text)
        assert result.match_count >= 1
        assert result.critical_count >= 1

    def test_match_positions(self, service):
        text = "手机号13800138000"
        result = service.detect(text)
        phone_matches = [m for m in result.matches if m.pii_type == PIIType.PHONE]
        assert len(phone_matches) == 1
        assert phone_matches[0].start == 3
        assert phone_matches[0].end == 14

    def test_by_type_property(self, service):
        text = "张三13800138000"
        result = service.detect(text)
        by_type = result.by_type
        assert isinstance(by_type, dict)
