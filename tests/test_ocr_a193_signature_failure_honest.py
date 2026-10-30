"""OCR A193: SM2 签名失败时不得用摘要顶替，也不得把签名阶段记成通过。

`stage_results[SIGNATURE] = True` + `passed = all(stage_results.values())` 而 `degraded`
不参与相与 => 对外（`inspection_to_report` 的 11 个调用点、合规报告）报的是「签名已做」，
而 `signature` 字段里其实只是一个摘要。第二重：`except` 分支自己再调一次可抛的
`sm3_hash`（gmssl 缺失且 CDS_SM3_FALLBACK_ALLOWED=false 时 raise RuntimeError），
异常从异常处理里逸出 => 整个审查接口 500。

判据：签名失败 => 阶段 False、`passed` False、`signature` 为空；降级摘要只能落在
另一个字段且明写未签名；`except` 路径里不得有任何可抛的加密运算。
"""
import pytest

from app.services.crypto_service import crypto_service
from app.services.output_inspection import InspectionStage, OutputInspector, SandboxMode

TEXT = "本季度统计结果：合计 42 条"


def _inspect(session_id="s-a193"):
    return OutputInspector().inspect(
        TEXT, "u-1", session_id, sandbox_mode=SandboxMode.DEVELOP.value,
    )


def _sig_degraded(result):
    return [d for d in result.degraded if InspectionStage.SIGNATURE.value in d]


def test_signing_failure_is_not_counted_as_signed(monkeypatch):
    """签名失败必须是签名阶段失败：摘要不算签名，passed 不能因此为 True。"""
    def _boom(*_a, **_k):
        raise RuntimeError("SM2 signing unavailable")

    monkeypatch.setattr(crypto_service, "sign", _boom)

    result = _inspect()

    assert result.stage_results[InspectionStage.SIGNATURE.value] is False, (
        "签名没做成却记 True —— passed 由 stage_results 相与得出，degraded 不参与，"
        "对外就等于宣称「签名已做」"
    )
    assert result.passed is False, "签名阶段失败必须让 passed 反映出来"
    assert result.signature is None, (
        "signature 字段只能装真正的 GM/T 签名；摘要顶替等于让合规报告里出现一个假签名"
    )
    assert _sig_degraded(result), "降级必须仍在 degraded 里披露"


def test_signing_failure_does_not_raise_when_sm3_refuses(monkeypatch):
    """except 里不得再调可抛的加密运算：sm3_hash 受 CDS_SM3_FALLBACK_ALLOWED 门控会 raise。"""
    def _boom(*_a, **_k):
        raise RuntimeError("SM2 signing unavailable")

    def _refuse(*_a, **_k):
        raise RuntimeError("SM3 SHA-256 fallback not allowed (CDS_SM3_FALLBACK_ALLOWED=false)")

    monkeypatch.setattr(crypto_service, "sign", _boom)
    monkeypatch.setattr(crypto_service, "sm3_hash", _refuse)

    result = _inspect()  # HEAD: 异常从 except 分支里逸出，审查接口 500

    assert result.stage_results[InspectionStage.SIGNATURE.value] is False


def test_degraded_digest_is_a_separate_field(monkeypatch):
    """降级摘要必须另存字段并明写「未签名」，不能占用 signature。"""
    monkeypatch.setattr(
        crypto_service, "sign",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("SM2 signing unavailable")),
    )

    result = _inspect()

    assert hasattr(result, "unsigned_digest"), "降级摘要要有自己的字段，别混进 signature"
    assert result.signature is None
    assert isinstance(result.unsigned_digest, str) and len(result.unsigned_digest) == 64
    note = " ".join(_sig_degraded(result)).lower()
    assert "not signed" in note or "unsigned" in note, (
        f"降级说明必须明写未签名，实际：{result.degraded}"
    )


def test_successful_signing_still_counts_as_signed():
    """反向护栏：签名真的做成时，阶段为 True 且没有降级披露（别把修法写成一律 False）。"""
    result = _inspect(session_id="s-a193-ok")

    assert result.stage_results[InspectionStage.SIGNATURE.value] is True
    assert result.passed is True
    assert result.signature
    assert not _sig_degraded(result)
    assert getattr(result, "unsigned_digest", None) is None
