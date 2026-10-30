"""A194 — pii_ner.py 潜伏缺陷钉（latent，接线前必修；本波次修缺陷不接线）。

铁律：结果必须披露实际运行的引擎（T9 honest degradation）。
修复组（本文件转绿）：
  N1  ner_engine="off"/"regex" 仍产出 layer="ner" 命中、却披露 ner_engine="regex_only"
      ——披露与实际行为双违（旗舰缺陷）
  N2  LAC/transformers 推理运行期失败回退规则层，结果仍披露 "lac"/"transformers"
  N3  重叠消解·丢失模式①：高严重度匹配整体替换长匹配 ⇒ 长匹配超出短 span 的明文泄漏
  N4  重叠消解·丢失模式②：同严重度后来者整条丢弃 ⇒ 其超出已保留 end 的尾部明文泄漏
  N5  重叠消解·丢失模式③：更长但更低严重度的重叠匹配整条丢弃 ⇒ 其外部明文泄漏
  N6  重叠 span 进入 _redact：逆序双切错位 ⇒ 少掩码/错掩码
  N7  threshold 只作用于 ML 层：规则/LAC 命中无视置信阈值配置
  N8  合并后的匹配 value 与坐标错位（value 仍是旧 span 的串）
  N9  enable_ner 与 ner_engine 双开关互相矛盾（enable_ner=True + ner_engine="off"
      到底开不开？旧行为：照跑规则层）⇒ 引擎解析是唯一事实源
钉账组（无法在本波次收口，缺陷钉 + 源码 TODO(A194)，不静默跳过）：
  P1  LAC 词位用 text.find 近似定位：漂移/静默丢词——要真模型复现
  P2  规则 NER 零分隔符过度匹配（"用户体验"当人名）——FP 调参需语料，人拍板
  P3  裸 "\\d{4}年\\d{1,2}月\\d{1,2}日" 把任意日期掩成 DATE_OF_BIRTH——产品语义待拍板
  P4  LAC TIME→DATE_OF_BIRTH 一律映射——错标需真模型评测
"""
import pytest

from app.services.pii_ner import (
    PIINERService, PIIDetectionResult, PIIType, Severity,
)


def off_service():
    return PIINERService(enable_ner=True, ner_engine="off")


class TestN1EngineOffMeansOff:
    def test_off_engine_emits_no_ner_layer_hits(self):
        svc = off_service()
        result = svc.detect("联系人：张三，地址：北京市海淀区中关村大街1号")
        assert result.ner_engine == "regex_only"
        # 披露说只有正则层，命中里就不得出现 NER 层结果
        assert [m for m in result.matches if m.layer in ("ner", "ner_lac", "ner_ml")] == []
        # 结构化正则层照常工作
        assert result.has_pii is False or all(m.layer == "regex" for m in result.matches)

    def test_regex_alias_also_off(self):
        svc = PIINERService(enable_ner=True, ner_engine="regex")
        result = svc.detect("联系人：张三")
        assert result.ner_engine == "regex_only"
        assert result.match_count == 0

    def test_rule_engine_still_emits_ner_layer(self):
        svc = PIINERService(enable_ner=True, ner_engine="rule")
        result = svc.detect("联系人：张三")
        assert result.ner_engine == "rule"
        assert any(m.layer == "ner" and m.pii_type == PIIType.PERSON_NAME for m in result.matches)


class TestN2RuntimeFallbackDisclosure:
    def test_lac_runtime_failure_reports_rule(self, monkeypatch):
        import types
        fake = types.ModuleType("LAC")

        class BoomLAC:
            def __init__(self, mode="lac"):
                pass

            def run(self, text):
                raise RuntimeError("model exploded mid-inference")

        fake.LAC = BoomLAC
        monkeypatch.setitem(__import__("sys").modules, "LAC", fake)
        svc = PIINERService(enable_ner=True, ner_engine="lac")
        assert svc._active_engine == "lac"  # init 时加载成功
        result = svc.detect("联系人：张三")
        # 运行期实际跑的是规则层——披露必须是 rule，不许冒领 lac
        assert result.ner_engine == "rule"
        assert all(m.layer != "ner_lac" for m in result.matches)


class TestOverlapNoPlaintext:
    """N3/N4/N5/N6：任何进入消解的重叠，最终 redacted_text 不得残留任何一员明文。"""

    def test_longer_lower_severity_overlap_fully_redacted(self):
        # 长 ADDRESS(HIGH) 与 ORG(MEDIUM) 在同段文本重叠——旧消解把被换/被丢的
        # span 留在明文里。隐私优先不变式：任何命中成员都不得在脱敏文本中残留。
        svc = PIINERService(enable_ner=True, ner_engine="rule")
        text = "地址：北京市海淀区中关村大街1号北京大学"
        result = svc.detect(text)
        for m in result.matches:
            assert m.value not in result.redacted_text, (
                f"{m.pii_type} span 明文残留在脱敏文本中"
            )

    def test_same_severity_overlapping_tails_redacted(self):
        # 两条 ADDRESS 规则（前缀式 + 通用式）在同一段文本上产生重叠 span，
        # 旧消解丢同严重度后来者 ⇒ 超出部分明文。
        svc = PIINERService(enable_ner=True, ner_engine="rule")
        text = "住址：浙江省杭州市西湖区文三路100号华星科技大厦8楼"
        result = svc.detect(text)
        assert "浙江省杭州市西湖区文三路100号" not in result.redacted_text
        # 且合并后不应出现坐标重叠的两条匹配
        ms = sorted(result.matches, key=lambda m: (m.start, m.end))
        for a, b in zip(ms, ms[1:]):
            assert b.start >= a.end, f"结果仍有重叠 span: {a} vs {b}"

    def test_replaced_longer_span_has_no_plaintext(self):
        # 18 位身份证同时命中 id_card(CRITICAL) 与 social_credit_code(HIGH) 全 span；
        # 以及构造更尖锐的：CRITICAL 短 span 替换较长 HIGH span 的旧丢失模式。
        svc = PIINERService(enable_ner=True, ner_engine="rule")
        text = "身份证号110101199003076534"
        result = svc.detect(text)
        assert "110101199003076534" not in result.redacted_text
        assert result.critical_count >= 1


class TestN7ThresholdAppliesToRuleLayer:
    def test_high_threshold_drops_low_confidence_rule_matches(self):
        svc = PIINERService(enable_ner=True, ner_engine="rule", threshold=0.85)
        result = svc.detect("地址：北京市海淀区中关村大街1号")  # addr conf 0.7
        assert [m for m in result.matches if m.pii_type == PIIType.ADDRESS] == []
        # 阈值内的人名 conf 0.8 也不该来（0.8<0.85），conf 0.9 的 DOB 应保留
        svc2 = PIINERService(enable_ner=True, ner_engine="rule", threshold=0.85)
        r2 = svc2.detect("出生日期：1990年03月07日")
        assert any(m.pii_type == PIIType.DATE_OF_BIRTH for m in r2.matches)


class TestN8MergedValueConsistency:
    def test_value_matches_span_after_merge(self):
        svc = PIINERService(enable_ner=True, ner_engine="rule")
        text = "住址：浙江省杭州市西湖区文三路100号华星科技大厦8楼"
        result = svc.detect(text)
        for m in result.matches:
            assert m.value == text[m.start:m.end]


class TestN9SingleSwitch:
    def test_enable_ner_flag_consistent_with_engine(self):
        svc = PIINERService(enable_ner=False, ner_engine="rule")
        assert svc._active_engine == "regex_only"
        result = svc.detect("联系人：张三，手机13800138000")
        assert [m for m in result.matches if m.layer == "ner"] == []
        assert any(m.pii_type == PIIType.PHONE for m in result.matches)
