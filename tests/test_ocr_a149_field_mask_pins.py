"""A149 — field_mask.py 潜伏缺陷钉（三引擎之一；模块不接线，修缺陷不接线）。

分组：真实缺陷修复（本文件转绿）：
  M1  HASH 未加盐未加密钥摘要：低熵 PII 摘要可穷举 ⇒ 支持 HMAC-SM3 密钥；
      无密钥保留旧行为但 MaskResult.notes 显式披露（密钥来源 = 人拍板，TODO(A149) 入账）
  M2  GENERALIZE 全线 fail-open（age/date/region/numeric/未知 type/未知 mode 六处）
      ⇒ 一律 REDACT 回退 + fallback_fields 披露（三个旧 fail-open 针已在
      test_field_mask.py 就地转强）
  M3  dict(row) 浅拷贝 ⇒ 嵌套容器与调用方共享引用 ⇒ deepcopy
  M4  bucket_size≤0/非数字崩溃；keep_chars 负数近乎全放行 ⇒ fail-closed
  M5  规则指向缺失字段静默失效 ⇒ rules_not_applied 披露
"""
import pytest

from app.services.field_mask import FieldMasker, MaskMode, MaskRule


class TestM1KeyedHash:
    def test_keyed_deterministic_per_key(self):
        rules = [MaskRule("v", MaskMode.HASH)]
        m1 = FieldMasker(hash_key="key-alpha")
        m2 = FieldMasker(hash_key="key-alpha")
        assert m1.mask_row({"v": "13800138000"}, rules).masked_row["v"] == \
               m2.mask_row({"v": "13800138000"}, rules).masked_row["v"]

    def test_different_keys_different_digest(self):
        rules = [MaskRule("v", MaskMode.HASH)]
        a = FieldMasker(hash_key="key-a").mask_row({"v": "x"}, rules).masked_row["v"]
        b = FieldMasker(hash_key="key-b").mask_row({"v": "x"}, rules).masked_row["v"]
        assert a != b
        assert len(a) == 64

    def test_unkeyed_mode_is_disclosed(self):
        rules = [MaskRule("v", MaskMode.HASH)]
        unkeyed = FieldMasker().mask_row({"v": "x"}, rules)
        assert any("UNKEYED" in n for n in unkeyed.notes)
        keyed = FieldMasker(hash_key="k").mask_row({"v": "x"}, rules)
        assert keyed.notes == []
        # 密钥模式与裸摘要输出必须不同（密钥真正参与了摘要）
        assert unkeyed.masked_row["v"] != keyed.masked_row["v"]


class TestM3NoAliasing:
    def test_nested_container_not_aliased(self):
        masker = FieldMasker()
        row = {"name": "Alice", "profile": {"phone": "13800138000"}}
        result = masker.mask_row(row, [MaskRule("name", MaskMode.REDACT)])
        assert result.masked_row["profile"] is not row["profile"]
        # 调用方事后改写原行，不得穿透已掩码的输出
        row["profile"]["phone"] = "13900139000"
        assert result.masked_row["profile"]["phone"] == "13800138000"


class TestM2FailClosedGeneralize:
    def test_unknown_generalize_type_redacts(self, ):
        masker = FieldMasker()
        rules = [MaskRule("ssn", MaskMode.GENERALIZE, generalize_type="ssn_last4")]
        r = masker.mask_row({"ssn": "123-45-6789"}, rules)
        assert r.masked_row["ssn"] == "[REDACTED]"
        assert "ssn" in r.fallback_fields

    def test_unknown_mode_redacts_not_passthrough(self):
        masker = FieldMasker()
        rule = MaskRule("v", mode="bogus")  # 数据类不校验枚举：坏配置不得放行明文
        r = masker.mask_row({"v": "secret"}, [rule])
        assert r.masked_row["v"] == "[REDACTED]"

    def test_date_year_rejects_non_numeric_prefix(self):
        masker = FieldMasker()
        r = masker.mask_row({"d": "ABCD"},
                            [MaskRule("d", MaskMode.GENERALIZE, generalize_type="date_year")])
        assert r.masked_row["d"] == "[REDACTED]"


class TestM4BadParamsFailClosed:
    def test_zero_bucket_size_does_not_crash(self):
        masker = FieldMasker()
        rules = [MaskRule("v", MaskMode.GENERALIZE, generalize_type="numeric_bucket",
                          generalize_params={"bucket_size": 0})]
        r = masker.mask_row({"v": "5500"}, rules)  # 旧代码 ZeroDivisionError
        assert r.masked_row["v"] == "[REDACTED]"

    def test_negative_keep_chars_refused(self):
        masker = FieldMasker()
        rules = [MaskRule("phone", MaskMode.GENERALIZE, generalize_type="truncate",
                          generalize_params={"keep_chars": -2})]
        r = masker.mask_row({"phone": "13800138000"}, rules)
        # 旧行为 value[:-2] + "***" = 11 位手机号放出去 9 位
        assert r.masked_row["phone"] == "[REDACTED]"


class TestM5SilentRuleDisclosure:
    def test_missing_field_disclosed(self):
        masker = FieldMasker()
        r = masker.mask_row({"name": "Alice"}, [MaskRule("nonexistent", MaskMode.REDACT)])
        assert r.rules_applied == []
        assert "nonexistent" in r.rules_not_applied
