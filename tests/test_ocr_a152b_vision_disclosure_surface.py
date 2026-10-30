"""A152b — vision_multimodal_runtime 的模拟脱敏披露必须落到**消费方读得到的字段**上。

admin-rest 轨道已落 A152 的前半：三个脱敏步骤 `ALLOW_SIMULATION=false` 时拒绝
（NotImplementedError），允许时在字节尾部追加 `cds_redaction_simulated` 标记，
`TrainingResult.redaction_simulated` 也置了位。仍然缺的是「披露到消费方」这半边
（项目铁律 禁伪造：模拟/降级路径必须 `is_simulation`/`backend` 披露 + WARNING）：

1. 模块里定义了 `SIMULATED_REDACTION_BACKEND` 却**没有任何出口**读得到它——
   仓内约定是 `is_simulation` + `backend` 两个字段（见 app/services/vision_pipeline.py
   的 `get_redaction_summary()`），消费方（输出报告 / 前端徽标）无从知道跑的是
   「字节级标记模拟」还是真检测后端；
2. `SecureImageDataLoader` 是模块文档里点名的独立消费面（"Secure DataLoader with
   image preprocessing in TEE"），直接用它的人只能拿到字节，拿不到任何结构化披露；
3. 全仓没有任何 WARNING：一条模拟脱敏跑过多少次都不会有人被提醒
   「像素没动过」。

同时钉住不许回退的东西：`ALLOW_SIMULATION=false` 仍必须拒绝（不许改成静默降级）、
既有 `cds_step=` 行为标记与字节披露仍在、没配脱敏时不得谎报 backend。
"""
import logging

import pytest

from app.core.config import get_settings
from app.services import vision_multimodal_runtime as vmr
from app.services.vision_multimodal_runtime import (
    FaceBlurStep,
    RedactionRules,
    SecureImageDataLoader,
    VisionModelTrainingRuntime,
    VisionTrainingConfig,
    build_image_pipeline,
)

IMG = b"image-7;PatientName=Patient-7;license_plate=ABC777;face=region-7"
SIM_MARKER = b"cds_redaction_simulated"
NO_REDACTION = RedactionRules(face_blur=False, dicom_strip=False, license_plate_mosaic=False)


def _allow(monkeypatch, value: bool) -> None:
    monkeypatch.setattr(get_settings(), "ALLOW_SIMULATION", value)


def _loader(rules: RedactionRules) -> SecureImageDataLoader:
    loader = SecureImageDataLoader(["prod-1"], pipeline=build_image_pipeline(rules), batch_size=10)
    loader.load()
    return loader


def test_loader_exposes_redaction_summary_to_consumers(monkeypatch):
    _allow(monkeypatch, True)
    loader = _loader(RedactionRules())
    batches = loader.get_batches()
    assert batches, "前置：批次要真的跑过脱敏步骤"

    summary = loader.get_redaction_summary()
    assert summary["is_simulation"] is True, summary
    assert summary["simulated"] > 0, summary
    assert summary["backend"] == vmr.SIMULATED_REDACTION_BACKEND, summary


def test_loader_summary_stays_clean_when_no_redaction_requested(monkeypatch):
    _allow(monkeypatch, True)
    loader = _loader(NO_REDACTION)
    batches = loader.get_batches()

    summary = loader.get_redaction_summary()
    assert summary == {"total": 100, "simulated": 0, "is_simulation": False, "backend": None}, summary
    assert all(SIM_MARKER not in img for batch, _ in batches for img in batch)


def test_training_result_names_the_redaction_backend(monkeypatch):
    _allow(monkeypatch, True)
    result = VisionModelTrainingRuntime().run_training(
        "sess-a152b", VisionTrainingConfig(num_epochs=1, batch_size=32), ["prod-1"],
    )
    assert result.error is None
    assert result.redaction_simulated is True
    assert result.redaction_backend == vmr.SIMULATED_REDACTION_BACKEND


def test_training_result_claims_no_backend_when_redaction_off(monkeypatch):
    _allow(monkeypatch, True)
    config = VisionTrainingConfig(num_epochs=1, batch_size=32, redaction_rules=NO_REDACTION)
    result = VisionModelTrainingRuntime().run_training("sess-a152b-off", config, ["prod-1"])

    assert result.redaction_simulated is False
    assert result.redaction_backend is None


def test_simulated_redaction_warns_once_per_run(monkeypatch, caplog):
    _allow(monkeypatch, True)
    with caplog.at_level(logging.WARNING):
        VisionModelTrainingRuntime().run_training(
            "sess-a152b-warn", VisionTrainingConfig(num_epochs=1, batch_size=32), ["prod-1"],
        )

    warns = [r for r in caplog.records if r.levelno >= logging.WARNING and "simulat" in r.getMessage()]
    assert warns, "模拟脱敏跑完没有任何 WARNING（禁伪造铁律要求披露可见）"
    assert any(vmr.SIMULATED_REDACTION_BACKEND in r.getMessage() for r in warns), [
        r.getMessage() for r in warns
    ]
    assert len(warns) == 1, (
        f"一次训练只该提醒一次（100 图 x 3 步不得刷屏），实际 {len(warns)}: "
        f"{[r.getMessage() for r in warns]}"
    )


def test_loader_used_standalone_also_warns_once(monkeypatch, caplog):
    _allow(monkeypatch, True)
    with caplog.at_level(logging.WARNING):
        _loader(RedactionRules(face_blur=False, license_plate_mosaic=True)).get_batches()

    warns = [r for r in caplog.records if r.levelno >= logging.WARNING and "simulat" in r.getMessage()]
    assert len(warns) == 1, [r.getMessage() for r in warns]


def test_fail_closed_half_is_not_weakened(monkeypatch, caplog):
    _allow(monkeypatch, False)
    with caplog.at_level(logging.WARNING):
        with pytest.raises(NotImplementedError):
            FaceBlurStep().apply(IMG)
    with pytest.raises(NotImplementedError):
        VisionModelTrainingRuntime().run_training(
            "sess-a152b-refuse", VisionTrainingConfig(num_epochs=1), ["prod-1"],
        )
    warns = [r for r in caplog.records if r.levelno >= logging.WARNING and "simulat" in r.getMessage()]
    assert not warns, "拒绍的路径不得同时自报「跑了模拟脱敏」——披露只说真实发生过的事"


def test_byte_level_disclosure_and_step_marker_survive(monkeypatch):
    _allow(monkeypatch, True)
    processed = FaceBlurStep().apply(IMG)
    assert SIM_MARKER in processed
    assert b"cds_step=face_blur:" in processed
    assert FaceBlurStep().apply(processed).count(SIM_MARKER) == 1, "重复处理不得把披露标记叠成两条"
