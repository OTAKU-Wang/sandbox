"""OCR A152 — 视觉脱敏步骤是假脱敏：不动像素却把 `is_redacted` 置 True，且无任何模拟披露。

`FaceDetectAndBlurStep` / `LicensePlateStep` / `DICOMTagStripStep` 只写 metadata 标志，
从不触碰像素，但把合规计数器会读的 `is_redacted` 置 True（vision_pipeline.py
`get_redacted_count` / `get_redaction_summary`）。项目铁律是「禁伪造 / 降级必须披露」：

1. `ALLOW_SIMULATION=false`（生产硬化形态）时，这些步骤必须**拒绝**执行而不是声称已脱敏；
2. 允许模拟时，样本、`PreprocessingResult` 与 `get_redaction_summary()` 都必须披露
   「这些 redaction 是模拟的」（`redaction_simulated` / `simulated` / `is_simulation`），
   否则合规读数 `redaction_rate=1.0` 就是把未处理数据说成已处理。
"""
import pytest

from app.core.config import get_settings
from app.services.vision_pipeline import (
    DICOMTagStripStep,
    FaceDetectAndBlurStep,
    ImageSample,
    LicensePlateStep,
    PreprocessingPipeline,
    RedactionRule,
    RedactionType,
    TrainingConfig,
    VisionTrainingRuntime,
)

IMG = b"\xff\xd8\xff\xe0" + b"rawpixels" * 24


def _disallow_simulation(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "ALLOW_SIMULATION", False)


@pytest.mark.parametrize(
    "step,payload_key",
    [
        (FaceDetectAndBlurStep(), "faces_blurred"),
        (LicensePlateStep(), "plates_mosaiced"),
        (DICOMTagStripStep(tags=["PatientName"]), "dicom_tags_stripped"),
    ],
)
def test_refuses_to_claim_redaction_when_simulation_disallowed(monkeypatch, step, payload_key):
    """禁伪造：没有真检测/模糊后端时不得把样本声称成已脱敏。"""
    _disallow_simulation(monkeypatch)
    sample = ImageSample(
        sample_id="s-refuse", image_data=IMG, metadata={"PatientName": "张三"},
    )
    with pytest.raises(NotImplementedError):
        step.apply(sample)
    assert sample.is_redacted is False
    assert payload_key not in sample.metadata


def test_simulated_redaction_is_disclosed_on_sample_and_result(monkeypatch):
    monkeypatch.setattr(get_settings(), "ALLOW_SIMULATION", True)
    sample = ImageSample(sample_id="s-disclose", image_data=IMG)
    pipeline = PreprocessingPipeline([FaceDetectAndBlurStep()])

    out, result = pipeline.process(sample)

    # 既有行为（tests/test_vision_pipeline.py 钉住）不得改变
    assert out.is_redacted is True
    assert result.redactions_applied == ["face_blur"]
    # 但必须同时披露这是模拟脱敏
    assert out.metadata.get("redaction_simulated") is True
    assert result.simulated is True


def test_runtime_redaction_summary_discloses_simulation():
    images = [ImageSample(sample_id=f"s-{i}", image_data=IMG) for i in range(4)]
    runtime = VisionTrainingRuntime("sess-a152")
    runtime.setup(
        images,
        [RedactionRule(RedactionType.FACE_BLUR)],
        TrainingConfig(num_epochs=1, batch_size=2),
    )

    summary = runtime.get_redaction_summary()

    assert summary["total"] == 4
    assert summary["redacted"] == 4
    assert summary["simulated"] == 4
    assert summary["is_simulation"] is True
