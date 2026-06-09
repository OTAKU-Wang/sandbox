"""Vision & Multimodal Training Pipeline tests."""
import pytest


# ─── Helper ───────────────────────────────────────────────────────────

def _make_samples(n: int = 10) -> list:
    """Create test image samples."""
    from app.services.vision_pipeline import ImageSample, ImageFormat
    samples = []
    for i in range(n):
        samples.append(ImageSample(
            sample_id=f"img-{i}",
            image_data=f"fake-jpeg-data-{i}".encode(),
            label=i % 3,
            format=ImageFormat.JPEG,
        ))
    return samples


def _make_texts(n: int = 10) -> list[dict]:
    """Create test text data for multimodal."""
    return [{"description": f"Image {i} shows object class {i % 3}", "label": i % 3} for i in range(n)]


# ─── Pipeline Step Tests ─────────────────────────────────────────────

def test_image_decode_step():
    """Unit test: decode step marks sample as decoded."""
    from app.services.vision_pipeline import ImageDecodeStep, ImageSample, ImageFormat
    step = ImageDecodeStep()
    sample = ImageSample(sample_id="s1", image_data=b"data", format=ImageFormat.JPEG)
    result = step.apply(sample)
    assert result.metadata.get("decoded") is True
    assert step.name() == "decode"


def test_face_blur_step():
    """Unit test: face blur step marks sample as redacted."""
    from app.services.vision_pipeline import FaceDetectAndBlurStep, ImageSample, ImageFormat
    step = FaceDetectAndBlurStep(blur_method="gaussian_k51")
    sample = ImageSample(sample_id="s1", image_data=b"data", format=ImageFormat.JPEG)
    result = step.apply(sample)
    assert result.is_redacted is True
    assert result.metadata.get("faces_blurred") is True
    assert result.metadata.get("blur_method") == "gaussian_k51"


def test_license_plate_step():
    """Unit test: license plate mosaic step."""
    from app.services.vision_pipeline import LicensePlateStep, ImageSample, ImageFormat
    step = LicensePlateStep(method="mosaic", block_size=10)
    sample = ImageSample(sample_id="s1", image_data=b"data", format=ImageFormat.JPEG)
    result = step.apply(sample)
    assert result.is_redacted is True
    assert result.metadata.get("plates_mosaiced") is True


def test_dicom_tag_strip_step():
    """Unit test: DICOM tag strip removes sensitive metadata."""
    from app.services.vision_pipeline import DICOMTagStripStep, ImageSample, ImageFormat
    step = DICOMTagStripStep()
    sample = ImageSample(
        sample_id="s1", image_data=b"data", format=ImageFormat.DICOM,
        metadata={"PatientName": "John", "PatientID": "12345", "StudyDate": "20240101"},
    )
    result = step.apply(sample)
    assert result.is_redacted is True
    assert "PatientName" not in result.metadata
    assert "PatientID" not in result.metadata
    assert "dicom_tags_stripped" in result.metadata
    assert len(result.metadata["dicom_tags_stripped"]) == 3  # PatientName, PatientID, StudyDate all sensitive


def test_dicom_custom_tags():
    """Unit test: DICOM strip with custom tags."""
    from app.services.vision_pipeline import DICOMTagStripStep, ImageSample, ImageFormat
    step = DICOMTagStripStep(tags=["PatientName"])
    sample = ImageSample(
        sample_id="s1", image_data=b"data", format=ImageFormat.DICOM,
        metadata={"PatientName": "John", "InstitutionName": "Hospital"},
    )
    result = step.apply(sample)
    assert "PatientName" not in result.metadata
    assert "InstitutionName" in result.metadata  # Not in strip list


def test_random_hflip_step():
    """Unit test: horizontal flip augmentation."""
    from app.services.vision_pipeline import RandomHorizontalFlipStep, ImageSample, ImageFormat
    step = RandomHorizontalFlipStep(p=0.5)
    sample = ImageSample(sample_id="s1", image_data=b"data", format=ImageFormat.JPEG)
    result = step.apply(sample)
    assert "hflip" in result.metadata.get("augmentations", [])


def test_random_resize_crop_step():
    """Unit test: resize crop augmentation."""
    from app.services.vision_pipeline import RandomResizeCropStep, ImageSample, ImageFormat
    step = RandomResizeCropStep(size=224)
    sample = ImageSample(sample_id="s1", image_data=b"data", format=ImageFormat.JPEG)
    result = step.apply(sample)
    assert result.metadata.get("output_size") == (224, 224)
    assert "resize_crop" in result.metadata.get("augmentations", [])


def test_normalize_step():
    """Unit test: normalize step records mean/std."""
    from app.services.vision_pipeline import NormalizeStep, ImageSample, ImageFormat
    step = NormalizeStep(mean=[0.5, 0.5, 0.5], std=[0.25, 0.25, 0.25])
    sample = ImageSample(sample_id="s1", image_data=b"data", format=ImageFormat.JPEG)
    result = step.apply(sample)
    assert result.metadata.get("normalized") is True
    assert result.metadata.get("norm_mean") == [0.5, 0.5, 0.5]


# ─── Pipeline Tests ──────────────────────────────────────────────────

def test_pipeline_process_single():
    """Unit test: pipeline processes single sample through all steps."""
    from app.services.vision_pipeline import (
        PreprocessingPipeline, ImageDecodeStep, FaceDetectAndBlurStep,
        RandomResizeCropStep, NormalizeStep, ImageSample, ImageFormat,
    )
    pipeline = PreprocessingPipeline([
        ImageDecodeStep(),
        FaceDetectAndBlurStep(),
        RandomResizeCropStep(size=224),
        NormalizeStep(),
    ])

    sample = ImageSample(sample_id="s1", image_data=b"data", format=ImageFormat.JPEG)
    result_sample, result = pipeline.process(sample)

    assert result.success is True
    assert result.sample_id == "s1"
    assert "face_blur" in result.redactions_applied
    assert result_sample.is_redacted is True
    assert result_sample.metadata.get("decoded") is True
    assert result_sample.metadata.get("normalized") is True


def test_pipeline_process_batch():
    """Unit test: pipeline processes batch of samples."""
    from app.services.vision_pipeline import (
        PreprocessingPipeline, ImageDecodeStep, NormalizeStep,
    )
    pipeline = PreprocessingPipeline([ImageDecodeStep(), NormalizeStep()])
    samples = _make_samples(5)

    processed, results = pipeline.process_batch(samples)

    assert len(processed) == 5
    assert len(results) == 5
    assert all(r.success for r in results)


def test_pipeline_empty():
    """Unit test: empty pipeline passes through."""
    from app.services.vision_pipeline import PreprocessingPipeline, ImageSample, ImageFormat
    pipeline = PreprocessingPipeline()
    sample = ImageSample(sample_id="s1", image_data=b"data", format=ImageFormat.JPEG)
    result_sample, result = pipeline.process(sample)
    assert result.success is True
    assert result_sample is sample


def test_pipeline_add_step():
    """Unit test: fluent add_step API."""
    from app.services.vision_pipeline import (
        PreprocessingPipeline, ImageDecodeStep, NormalizeStep,
    )
    pipeline = PreprocessingPipeline()
    pipeline.add_step(ImageDecodeStep()).add_step(NormalizeStep())
    assert len(pipeline.steps) == 2


# ─── SecureImageDataLoader Tests ─────────────────────────────────────

def test_dataloader_prepare():
    """Unit test: dataloader prepares and preprocesses all images."""
    from app.services.vision_pipeline import (
        SecureImageDataLoader, PreprocessingPipeline, ImageDecodeStep, FaceDetectAndBlurStep,
    )
    pipeline = PreprocessingPipeline([ImageDecodeStep(), FaceDetectAndBlurStep()])
    samples = _make_samples(10)

    loader = SecureImageDataLoader(images=samples, pipeline=pipeline, batch_size=4)
    results = loader.prepare()

    assert len(results) == 10
    assert loader.total_samples == 10
    assert loader.get_redacted_count() == 10  # All face-blurred


def test_dataloader_batching():
    """Unit test: dataloader yields correct batch sizes."""
    from app.services.vision_pipeline import (
        SecureImageDataLoader, PreprocessingPipeline, ImageDecodeStep,
    )
    pipeline = PreprocessingPipeline([ImageDecodeStep()])
    samples = _make_samples(10)

    loader = SecureImageDataLoader(images=samples, pipeline=pipeline, batch_size=4)
    loader.prepare()

    batches = list(loader)
    assert len(batches) == 3  # 4+4+2
    assert len(batches[0]) == 4
    assert len(batches[1]) == 4
    assert len(batches[2]) == 2


def test_dataloader_length():
    """Unit test: dataloader len is ceil(n/batch_size)."""
    from app.services.vision_pipeline import (
        SecureImageDataLoader, PreprocessingPipeline, ImageDecodeStep,
    )
    pipeline = PreprocessingPipeline([ImageDecodeStep()])
    loader = SecureImageDataLoader(images=_make_samples(10), pipeline=pipeline, batch_size=3)
    loader.prepare()
    assert len(loader) == 4  # 3+3+3+1


def test_dataloader_get_sample():
    """Unit test: get_sample returns specific sample."""
    from app.services.vision_pipeline import (
        SecureImageDataLoader, PreprocessingPipeline, ImageDecodeStep,
    )
    pipeline = PreprocessingPipeline([ImageDecodeStep()])
    loader = SecureImageDataLoader(images=_make_samples(5), pipeline=pipeline, batch_size=2)
    loader.prepare()
    sample = loader.get_sample(0)
    assert sample.sample_id is not None


def test_dataloader_shuffle():
    """Unit test: shuffle=True does not change sample count."""
    from app.services.vision_pipeline import (
        SecureImageDataLoader, PreprocessingPipeline, ImageDecodeStep,
    )
    pipeline = PreprocessingPipeline([ImageDecodeStep()])
    loader = SecureImageDataLoader(images=_make_samples(10), pipeline=pipeline, batch_size=5, shuffle=True)
    loader.prepare()
    assert loader.total_samples == 10


# ─── SecureMultimodalDataLoader Tests ────────────────────────────────

def test_multimodal_dataloader_prepare():
    """Unit test: multimodal dataloader aligns image-text pairs."""
    from app.services.vision_pipeline import (
        SecureMultimodalDataLoader, PreprocessingPipeline, ImageDecodeStep,
    )
    pipeline = PreprocessingPipeline([ImageDecodeStep()])
    images = _make_samples(10)
    texts = _make_texts(10)

    loader = SecureMultimodalDataLoader(images=images, texts=texts, image_pipeline=pipeline, batch_size=4)
    results = loader.prepare()

    assert len(results) == 10


def test_multimodal_dataloader_batching():
    """Unit test: multimodal dataloader yields paired batches."""
    from app.services.vision_pipeline import (
        SecureMultimodalDataLoader, PreprocessingPipeline, ImageDecodeStep,
    )
    pipeline = PreprocessingPipeline([ImageDecodeStep()])
    loader = SecureMultimodalDataLoader(
        images=_make_samples(8), texts=_make_texts(8),
        image_pipeline=pipeline, batch_size=3,
    )
    loader.prepare()

    batches = list(loader)
    assert len(batches) == 3  # 3+3+2
    # Each item is (ImageSample, dict)
    img, txt = batches[0][0]
    assert "description" in txt


def test_multimodal_mismatched_lengths():
    """Unit test: mismatched image/text lengths — pairs to min length."""
    from app.services.vision_pipeline import (
        SecureMultimodalDataLoader, PreprocessingPipeline, ImageDecodeStep,
    )
    pipeline = PreprocessingPipeline([ImageDecodeStep()])
    loader = SecureMultimodalDataLoader(
        images=_make_samples(10), texts=_make_texts(5),
        image_pipeline=pipeline, batch_size=3,
    )
    loader.prepare()

    total_pairs = sum(len(batch) for batch in loader)
    assert total_pairs == 5  # min(10, 5)


def test_multimodal_get_pairs():
    """Unit test: get_image_description_pairs for leakage check."""
    from app.services.vision_pipeline import (
        SecureMultimodalDataLoader, PreprocessingPipeline, ImageDecodeStep,
    )
    pipeline = PreprocessingPipeline([ImageDecodeStep()])
    loader = SecureMultimodalDataLoader(
        images=_make_samples(10), texts=_make_texts(10),
        image_pipeline=pipeline, batch_size=4,
    )
    loader.prepare()

    pairs = loader.get_image_description_pairs(n=5)
    assert len(pairs) == 5
    assert all(isinstance(p[0], str) for p in pairs)
    assert all(isinstance(p[1], bytes) for p in pairs)


# ─── VisionTrainingRuntime Tests ─────────────────────────────────────

def test_vision_runtime_setup():
    """Unit test: vision runtime setup creates dataloader."""
    from app.services.vision_pipeline import (
        VisionTrainingRuntime, TrainingConfig, RedactionRule, RedactionType,
    )
    runtime = VisionTrainingRuntime(session_id="test-v1")
    config = TrainingConfig(architecture="resnet50", num_classes=3, batch_size=4, num_epochs=2)
    rules = [RedactionRule(redaction_type=RedactionType.FACE_BLUR)]

    runtime.setup(images=_make_samples(10), redaction_rules=rules, config=config)

    assert runtime.dataloader is not None
    assert runtime.dataloader.total_samples == 10


def test_vision_runtime_train():
    """Unit test: vision runtime training produces metrics."""
    from app.services.vision_pipeline import (
        VisionTrainingRuntime, TrainingConfig, RedactionRule, RedactionType, TrainingPhase,
    )
    runtime = VisionTrainingRuntime(session_id="test-v2")
    config = TrainingConfig(architecture="resnet50", num_classes=3, batch_size=4, num_epochs=3)
    rules = [RedactionRule(redaction_type=RedactionType.FACE_BLUR)]

    runtime.setup(images=_make_samples(10), redaction_rules=rules, config=config)
    result = runtime.train()

    assert result.phase == TrainingPhase.COMPLETED
    assert result.total_epochs == 3
    assert len(result.all_metrics) == 3
    assert result.final_metrics is not None
    assert result.final_metrics.accuracy > 0
    assert result.watermark_hash is not None


def test_vision_runtime_train_without_setup():
    """Unit test: training without setup fails gracefully."""
    from app.services.vision_pipeline import VisionTrainingRuntime, TrainingPhase
    runtime = VisionTrainingRuntime(session_id="test-v3")
    result = runtime.train()
    assert result.phase == TrainingPhase.FAILED
    assert "not initialized" in result.error.lower()


def test_vision_runtime_allowed_architectures():
    """Unit test: only allowed architectures."""
    from app.services.vision_pipeline import VisionTrainingRuntime
    assert "resnet50" in VisionTrainingRuntime.ALLOWED_ARCHITECTURES
    assert "vit_b_16" in VisionTrainingRuntime.ALLOWED_ARCHITECTURES
    assert "yolov8_n" in VisionTrainingRuntime.ALLOWED_ARCHITECTURES


def test_vision_runtime_redaction_summary():
    """Unit test: redaction summary tracks redacted samples."""
    from app.services.vision_pipeline import (
        VisionTrainingRuntime, TrainingConfig, RedactionRule, RedactionType,
    )
    runtime = VisionTrainingRuntime(session_id="test-v4")
    config = TrainingConfig(architecture="resnet50", num_classes=3, batch_size=4, num_epochs=1)
    rules = [RedactionRule(redaction_type=RedactionType.FACE_BLUR)]

    runtime.setup(images=_make_samples(10), redaction_rules=rules, config=config)
    summary = runtime.get_redaction_summary()

    assert summary["total"] == 10
    assert summary["redacted"] == 10
    assert summary["redaction_rate"] == 1.0


def test_vision_runtime_multiple_redaction_rules():
    """Unit test: multiple redaction rules applied."""
    from app.services.vision_pipeline import (
        VisionTrainingRuntime, TrainingConfig, RedactionRule, RedactionType,
    )
    runtime = VisionTrainingRuntime(session_id="test-v5")
    config = TrainingConfig(architecture="resnet50", num_classes=3, batch_size=4, num_epochs=1)
    rules = [
        RedactionRule(redaction_type=RedactionType.FACE_BLUR),
        RedactionRule(redaction_type=RedactionType.LICENSE_PLATE_MOSAIC),
    ]

    runtime.setup(images=_make_samples(10), redaction_rules=rules, config=config)
    summary = runtime.get_redaction_summary()
    assert summary["redacted"] == 10


# ─── MultimodalTrainingRuntime Tests ─────────────────────────────────

def test_multimodal_runtime_setup():
    """Unit test: multimodal runtime setup validates model."""
    from app.services.vision_pipeline import (
        MultimodalTrainingRuntime, MultimodalTrainingConfig, RedactionRule, RedactionType,
    )
    runtime = MultimodalTrainingRuntime(session_id="test-mm1")
    config = MultimodalTrainingConfig(base_model="Qwen2-VL-7B", batch_size=4, num_epochs=2)
    rules = [RedactionRule(redaction_type=RedactionType.FACE_BLUR)]

    runtime.setup(
        images=_make_samples(10), texts=_make_texts(10),
        redaction_rules=rules, config=config,
    )
    assert runtime.dataloader is not None


def test_multimodal_runtime_invalid_model():
    """Unit test: invalid model raises error."""
    from app.services.vision_pipeline import (
        MultimodalTrainingRuntime, MultimodalTrainingConfig, RedactionRule, RedactionType,
    )
    runtime = MultimodalTrainingRuntime(session_id="test-mm2")
    config = MultimodalTrainingConfig(base_model="GPT-5", batch_size=4)

    with pytest.raises(ValueError, match="not allowed"):
        runtime.setup(
            images=_make_samples(5), texts=_make_texts(5),
            redaction_rules=[], config=config,
        )


def test_multimodal_runtime_train():
    """Unit test: multimodal training produces result with leakage check."""
    from app.services.vision_pipeline import (
        MultimodalTrainingRuntime, MultimodalTrainingConfig, RedactionRule, RedactionType, TrainingPhase,
    )
    runtime = MultimodalTrainingRuntime(session_id="test-mm3")
    config = MultimodalTrainingConfig(base_model="Qwen2-VL-7B", batch_size=4, num_epochs=2)
    rules = [RedactionRule(redaction_type=RedactionType.FACE_BLUR)]

    runtime.setup(images=_make_samples(10), texts=_make_texts(10), redaction_rules=rules, config=config)
    result = runtime.train()

    assert result.phase == TrainingPhase.COMPLETED
    assert result.total_epochs == 2
    assert result.watermark_hash is not None
    assert runtime.leakage_result is not None
    assert runtime.leakage_result.samples_checked > 0


def test_multimodal_runtime_leakage_check():
    """Unit test: leakage check runs on image-text pairs."""
    from app.services.vision_pipeline import (
        MultimodalTrainingRuntime, MultimodalTrainingConfig, RedactionRule, RedactionType,
    )
    runtime = MultimodalTrainingRuntime(session_id="test-mm4")
    config = MultimodalTrainingConfig(base_model="Qwen2-VL-7B", batch_size=4, num_epochs=1)

    runtime.setup(images=_make_samples(20), texts=_make_texts(20), redaction_rules=[], config=config)
    runtime.train()

    leakage = runtime.leakage_result
    assert leakage is not None
    assert leakage.threshold == 0.85
    assert isinstance(leakage.passed, bool)
    assert leakage.samples_checked > 0


def test_multimodal_memorization_probe():
    """Unit test: memorization probe returns results."""
    from app.services.vision_pipeline import (
        MultimodalTrainingRuntime, MultimodalTrainingConfig,
    )
    runtime = MultimodalTrainingRuntime(session_id="test-mm5")
    config = MultimodalTrainingConfig(base_model="Qwen2-VL-7B", batch_size=4, num_epochs=1)

    runtime.setup(images=_make_samples(10), texts=_make_texts(10), redaction_rules=[], config=config)
    result = runtime.run_memorization_probe(probe_texts=["test query 1", "test query 2"])

    assert "probe_count" in result
    assert "memorized_count" in result
    assert "passed" in result
    assert result["probe_count"] == 2


def test_multimodal_memorization_probe_detects_training_text():
    """Unit test: memorization probe flags exact training-description overlap."""
    from app.services.vision_pipeline import (
        MultimodalTrainingRuntime, MultimodalTrainingConfig,
    )
    runtime = MultimodalTrainingRuntime(session_id="test-mm6")
    config = MultimodalTrainingConfig(base_model="Qwen2-VL-7B", batch_size=4, num_epochs=1)
    texts = _make_texts(10)

    runtime.setup(images=_make_samples(10), texts=texts, redaction_rules=[], config=config)
    result = runtime.run_memorization_probe(probe_texts=[texts[0]["description"]])

    assert result["memorized_count"] == 1
    assert result["passed"] is False


def test_multimodal_allowed_models():
    """Unit test: allowed multimodal models list."""
    from app.services.vision_pipeline import ALLOWED_MULTIMODAL_MODELS
    assert "Qwen2-VL-7B" in ALLOWED_MULTIMODAL_MODELS
    assert "InternVL2-8B" in ALLOWED_MULTIMODAL_MODELS
    assert "CogVLM2" in ALLOWED_MULTIMODAL_MODELS
    assert "GPT-5" not in ALLOWED_MULTIMODAL_MODELS


# ─── Dataclass Tests ─────────────────────────────────────────────────

def test_training_config_defaults():
    """Unit test: TrainingConfig default values."""
    from app.services.vision_pipeline import TrainingConfig
    config = TrainingConfig()
    assert config.architecture == "resnet50"
    assert config.batch_size == 32
    assert config.num_epochs == 10
    assert config.image_size == 224


def test_multimodal_config_defaults():
    """Unit test: MultimodalTrainingConfig default values."""
    from app.services.vision_pipeline import MultimodalTrainingConfig
    config = MultimodalTrainingConfig()
    assert config.base_model == "Qwen2-VL-7B"
    assert config.batch_size == 16
    assert config.align_strategy == "pair"


def test_redaction_rule_types():
    """Unit test: all redaction types are valid."""
    from app.services.vision_pipeline import RedactionType
    assert RedactionType.FACE_BLUR.value == "face_blur"
    assert RedactionType.LICENSE_PLATE_MOSAIC.value == "license_plate_mosaic"
    assert RedactionType.DICOM_TAG_STRIP.value == "dicom_tag_strip"
    assert RedactionType.TEXT_REDACT.value == "text_redact"


def test_training_phases():
    """Unit test: all training phases."""
    from app.services.vision_pipeline import TrainingPhase
    assert TrainingPhase.INIT.value == "init"
    assert TrainingPhase.PREPROCESSING.value == "preprocessing"
    assert TrainingPhase.TRAINING.value == "training"
    assert TrainingPhase.COMPLETED.value == "completed"
    assert TrainingPhase.FAILED.value == "failed"
