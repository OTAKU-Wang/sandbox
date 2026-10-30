"""OCR A152 同族扩展 — vision_multimodal_runtime 的脱敏步骤不得静默冒充已脱敏。

`FaceBlurStep` / `DICOMStripStep` / `LicensePlateStep` 在这里同样不动像素：face blur
只在字节尾部追加一个 `|cds_step=...` 标记，DICOM/车牌步骤只对合成 `k=v` 字节载荷做
正则替换，对真图什么都脱不掉。项目铁律（禁伪造 / honest degradation，与
tests/test_ocr_a152_vision_redaction_honesty.py 对 vision_pipeline 的同款门禁一致）：

1. `ALLOW_SIMULATION=false` 时这些步骤必须拒绝执行（NotImplementedError），
   训练入口也必须 fail-fast，而不是跑完一个声称做过脱敏的训练；
2. 允许模拟时，处理结果字节与 `TrainingResult` 都必须披露这次脱敏是模拟的
   （`cds_redaction_simulated` 标记 / `redaction_simulated=True`），
   且既有 `cds_step=...` 行为标记不得因此丢失（test_vision_multimodal_runtime 钉着）。
"""
import pytest

from app.core.config import get_settings
from app.services.vision_multimodal_runtime import (
    DICOMStripStep,
    FaceBlurStep,
    LicensePlateStep,
    PipelineStepType,
    RedactionRules,
    SecureImageDataLoader,
    VisionModelTrainingRuntime,
    VisionTrainingConfig,
    build_image_pipeline,
)

IMG = b"image-7;PatientName=Patient-7;license_plate=ABC777;face=region-7"
SIMULATED_MARKER = b"cds_redaction_simulated"
PIPELINE_STEPS = (PipelineStepType.FACE_BLUR, PipelineStepType.DICOM_STRIP,
                  PipelineStepType.LICENSE_PLATE_MOSAIC)


def _allow(monkeypatch, value: bool) -> None:
    monkeypatch.setattr(get_settings(), "ALLOW_SIMULATION", value)


@pytest.mark.parametrize("step_factory", [
    FaceBlurStep,
    DICOMStripStep,
    LicensePlateStep,
])
def test_steps_refuse_when_simulation_disallowed(monkeypatch, step_factory):
    _allow(monkeypatch, False)
    with pytest.raises(NotImplementedError):
        step_factory().apply(IMG)


@pytest.mark.parametrize("step_factory", [
    FaceBlurStep,
    DICOMStripStep,
    LicensePlateStep,
])
def test_steps_disclose_simulation_on_bytes(monkeypatch, step_factory):
    _allow(monkeypatch, True)
    processed = step_factory().apply(IMG)
    assert SIMULATED_MARKER in processed
    assert b"cds_step=" in processed


def test_loader_pipeline_marks_redaction_simulated(monkeypatch):
    _allow(monkeypatch, True)
    loader = SecureImageDataLoader(
        ["prod-1"], pipeline=build_image_pipeline(RedactionRules()), batch_size=10,
    )
    loader.load()
    batches = loader.get_batches()
    assert batches
    assert all(SIMULATED_MARKER in img for batch, _ in batches for img in batch)


def test_training_result_discloses_simulated_redaction(monkeypatch):
    _allow(monkeypatch, True)
    result = VisionModelTrainingRuntime().run_training(
        "sess-a152", VisionTrainingConfig(num_epochs=1, batch_size=32), ["prod-1"],
    )
    assert result.error is None
    assert result.redaction_simulated is True


@pytest.mark.parametrize("rules", [
    RedactionRules(),
    RedactionRules(face_blur=False, dicom_strip=True),
    RedactionRules(face_blur=False, license_plate_mosaic=True),
])
def test_training_refuses_redaction_without_simulation(monkeypatch, rules):
    _allow(monkeypatch, False)
    config = VisionTrainingConfig(num_epochs=1, redaction_rules=rules)
    with pytest.raises(NotImplementedError):
        VisionModelTrainingRuntime().run_training("sess-a152-refuse", config, ["prod-1"])
