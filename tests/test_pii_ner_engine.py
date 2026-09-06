"""T9 — NER engine selection and honest engine labeling.

Verifies that ``PIIDetectionResult.ner_engine`` reports the engine that
actually ran, so a rule-based result is never presented as model-based NER.
LAC is an optional dependency — the LAC path is exercised via a fake module.
"""
import sys
import types

import pytest

from app.services.pii_ner import PIINERService, PIIType


@pytest.fixture
def service():
    return PIINERService(enable_ner=True)


@pytest.fixture
def regex_only_service():
    return PIINERService(enable_ner=False)


def _install_fake_lac(monkeypatch):
    """Install a minimal fake LAC module into sys.modules."""
    fake = types.ModuleType("LAC")

    class FakeLAC:
        def __init__(self, mode="lac"):
            self.mode = mode

        def run(self, text):
            # Deterministic tokenization for the test strings.
            if "张三" in text:
                return (["张三", "的", "电话", "13800138000"], ["PER", "u", "n", "m"])
            if "北京大学" in text:
                return (["北京大学", "是", "名校"], ["ORG", "v", "n"])
            if "北京市海淀区" in text:
                return (["北京市", "海淀区", "地址"], ["GPE", "ns", "n"])
            return (list(text), ["w"] * len(text))

    fake.LAC = FakeLAC
    monkeypatch.setitem(sys.modules, "LAC", fake)
    return fake


# ── Honest engine labeling ────────────────────────────────────────

class TestHonestEngine:
    def test_result_reports_rule_when_lac_absent(self, service):
        # LAC is not installed in this environment → auto resolves to rule.
        result = service.detect("张三的邮箱是zhangsan@example.com")
        assert result.ner_engine == "rule"

    def test_regex_only_reports_engine(self, regex_only_service):
        result = regex_only_service.detect("13800138000")
        assert result.ner_engine == "regex_only"

    def test_explicit_rule_engine(self):
        svc = PIINERService(enable_ner=True, ner_engine="rule")
        assert svc._active_engine == "rule"
        result = svc.detect("联系人：张三")
        assert result.ner_engine == "rule"
        assert any(m.layer == "ner" for m in result.matches)

    def test_empty_text_still_reports_engine(self, service):
        result = service.detect("")
        assert result.ner_engine == "rule"

    def test_lac_requested_but_unavailable_honestly_downgrades(self, monkeypatch):
        # Force LAC import to fail → requested "lac" must downgrade to "rule",
        # never impersonate a model run.
        monkeypatch.setitem(sys.modules, "LAC", None)
        svc = PIINERService(enable_ner=True, ner_engine="lac")
        assert svc._active_engine == "rule"
        result = svc.detect("联系人：张三")
        assert result.ner_engine == "rule"
        assert all(m.layer != "ner_lac" for m in result.matches)


# ── LAC model path ────────────────────────────────────────────────

class TestLACPath:
    def test_lac_engine_selected_when_available(self, monkeypatch):
        _install_fake_lac(monkeypatch)
        svc = PIINERService(enable_ner=True, ner_engine="lac")
        assert svc._active_engine == "lac"
        assert svc._lac is not None

    def test_lac_detects_person_name(self, monkeypatch):
        _install_fake_lac(monkeypatch)
        svc = PIINERService(enable_ner=True, ner_engine="lac")
        result = svc.detect("张三的电话是13800138000")
        assert result.ner_engine == "lac"
        name_matches = [
            m for m in result.matches
            if m.pii_type == PIIType.PERSON_NAME and m.layer == "ner_lac"
        ]
        assert len(name_matches) == 1
        assert name_matches[0].value == "张三"

    def test_lac_detects_organization(self, monkeypatch):
        _install_fake_lac(monkeypatch)
        svc = PIINERService(enable_ner=True, ner_engine="lac")
        result = svc.detect("北京大学是名校")
        assert result.ner_engine == "lac"
        org_matches = [
            m for m in result.matches
            if m.pii_type == PIIType.ORGANIZATION and m.layer == "ner_lac"
        ]
        assert len(org_matches) == 1
        assert org_matches[0].value == "北京大学"

    def test_lac_detects_location(self, monkeypatch):
        _install_fake_lac(monkeypatch)
        svc = PIINERService(enable_ner=True, ner_engine="lac")
        result = svc.detect("地址是北京市海淀区")
        assert result.ner_engine == "lac"
        loc_matches = [
            m for m in result.matches
            if m.pii_type == PIIType.ADDRESS and m.layer == "ner_lac"
        ]
        assert len(loc_matches) == 1

    def test_auto_prefers_lac_when_available(self, monkeypatch):
        _install_fake_lac(monkeypatch)
        svc = PIINERService(enable_ner=True, ner_engine="auto")
        assert svc._active_engine == "lac"
        result = svc.detect("张三的电话")
        assert result.ner_engine == "lac"
