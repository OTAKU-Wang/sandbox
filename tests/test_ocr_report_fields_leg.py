"""报告字段腿（A193 / A191 续）：签名披露字段必须随 `security_report` 带出。

`inspection_to_report()` 是 11 个调用点共用的唯一序列化出口。backend-11a/b 把
「签名密钥从哪来」「签名失败时那个摘要不是签名」做进了 `InspectionResult`，但报告字典
里没有这两个字段 ⇒ 对外仍然看不见：调用方读 `report["signature"]` 有值就以为可抵赖，
读不到密钥来源就分不清部署密钥与进程内临时密钥。这里钉住字段确实带出。
"""
import uuid

from app.services.output_inspection import (
    InspectionResult,
    OutputInspector,
    SandboxMode,
)
from app.services.output_security import inspection_to_report


def _sid() -> str:
    return f"sess-{uuid.uuid4().hex[:12]}"


def test_report_carries_signature_provenance_fields():
    result = InspectionResult(
        passed=True,
        stage_results={"signature": True},
        findings=[],
        signature="r" * 128,
        signature_key_source="configured",
        signature_public_key="a" * 128,
        unsigned_digest=None,
    )
    report = inspection_to_report(result)
    assert report["signature_key_source"] == "configured"
    assert report["signature_public_key"] == "a" * 128
    assert report["unsigned_digest"] is None


def test_report_keeps_digest_out_of_the_signature_field():
    """签名失败时：signature 空、降级摘要另字段带出，两者不得互串。"""
    result = InspectionResult(
        passed=False,
        stage_results={"signature": False},
        findings=[],
        signature=None,
        unsigned_digest="d" * 64,
        signature_key_source="unavailable",
    )
    report = inspection_to_report(result)
    assert report["signature"] is None, report
    assert report["unsigned_digest"] == "d" * 64
    assert report["signature_key_source"] == "unavailable"


def test_live_inspection_report_matches_the_result_fields():
    result = OutputInspector().inspect(
        "hello", "u-1", _sid(), sandbox_mode=SandboxMode.DEVELOP.value,
    )
    report = inspection_to_report(result)
    assert report["signature_key_source"] == result.signature_key_source
    assert report["signature_public_key"] == result.signature_public_key
    assert report["unsigned_digest"] == result.unsigned_digest
    assert report["signature"] == result.signature
    if result.unsigned_digest is not None:
        assert result.signature is None, "降级摘要不得占用 signature"
        assert report["signature"] is None
