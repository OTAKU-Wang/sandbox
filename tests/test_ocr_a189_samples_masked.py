"""OCR A189: DLP/k-anonymity 命中值写进 findings.samples 前必须脱敏（禁二次泄露）。

`samples` 会随 `security_report` 回给调用方、并写进审计（api/output_control.py），
所以「正文已 REDACTED 但 findings 里又送一遍原值」等于输出管控自身的外泄通道。
判据：任何命中原值都不得出现在 findings.samples / inspection_to_report 里，
但样本仍要保留可核对的掩码形状（`110101****7758`）。
"""
import json

from app.services.output_inspection import OutputInspector, SandboxMode
from app.services.output_security import inspection_to_report

ID_CARD = "110101199003077758"
PHONE = "13800138000"
EMAIL = "someone@example.com"
SECRET = "13800138000"


def _texts():
    return f"身份证 {ID_CARD}；手机 {PHONE}；邮箱 {EMAIL}"


def test_dlp_samples_never_carry_raw_values():
    result = OutputInspector().inspect(
        _texts(), "u-1", "s-a189", sandbox_mode=SandboxMode.DEVELOP.value,
    )
    samples = [s for f in result.findings for s in f.samples]
    assert samples, "DLP 命中样本仍要存在（不能靠删样本来“修”）"
    for raw in (ID_CARD, PHONE, EMAIL):
        for sample in samples:
            assert raw not in sample, f"findings.samples 泄露原值 {raw!r} -> {sample!r}"
    assert "[REDACTED:id_card]" in result.redacted_output
    id_samples = [s for f in result.findings if f.type == "id_card" for s in f.samples]
    assert id_samples and all(s == "110101****7758" for s in id_samples), id_samples


def test_report_serialisation_carries_no_plaintext():
    result = OutputInspector().inspect(
        _texts(), "u-1", "s-a189b", sandbox_mode=SandboxMode.STRUCTURED_QUERY.value,
    )
    report = json.dumps(inspection_to_report(result), ensure_ascii=False)
    for raw in (ID_CARD, PHONE, EMAIL):
        assert raw not in report, f"security_report 泄露原值 {raw}"


def test_k_anonymity_samples_mask_quasi_identifiers():
    rows = [{"city": "BJ", "age": "20"}] * 5 + [{"city": "RareCityZZ", "age": "97"}]
    result = OutputInspector().inspect(
        "rows", "u-1", "s-a189c",
        sandbox_mode=SandboxMode.STRUCTURED_QUERY.value, output_rows=rows,
    )
    ka = [f for f in result.findings if f.type == "k_anonymity"]
    assert ka, "k-匿名违规样本仍要存在"
    for sample in ka[0].samples:
        assert "RareCityZZ" not in sample, f"k-匿名样本泄露准标识符原值: {sample!r}"
        assert "****" in sample, f"k-匿名样本应为掩码串: {sample!r}"


def test_short_matches_fully_masked():
    """短命中（如 2 位准标识符 / 短邮箱）不得保留任何原值片段。"""
    inspector = OutputInspector()
    rows = [{"g": "ab"}] * 5 + [{"g": "zz"}]
    result = inspector.inspect(
        "rows", "u-1", "s-a189d",
        sandbox_mode=SandboxMode.STRUCTURED_QUERY.value, output_rows=rows,
    )
    ka = [f for f in result.findings if f.type == "k_anonymity"]
    assert ka
    for sample in ka[0].samples:
        assert "'zz'" not in sample and '"zz"' not in sample, sample
