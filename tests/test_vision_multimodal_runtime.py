"""Tests for Vision & Multimodal Training Runtime (SS-04 §4.3-4.4)."""
import pytest
from unittest.mock import patch

from app.services.vision_multimodal_runtime import (
    VisionTask,
    PipelineStepType,
    PipelineStep,
    ImagePipeline,
    RedactionRules,
    VisionTrainingConfig,
    MultimodalTrainingConfig,
    TrainingMetrics,
    TrainingResult,
    ALLOWED_MULTIMODAL_MODELS,
    PipelineStepBase,
    DecodeStep,
    FaceBlurStep,
    DICOMStripStep,
    LicensePlateStep,
    SecureImageDataLoader,
    SecureMultimodalDataLoader,
    build_image_pipeline,
    VisionModelTrainingRuntime,
    MultimodalTrainingRuntime,
    vision_training_runtime,
    multimodal_training_runtime,
)


# ── Enums ─────────────────────────────────────────────────────

class TestEnums:
    def test_vision_task_values(self):
        assert VisionTask.CLASSIFICATION.value == "classification"
        assert VisionTask.DETECTION.value == "detection"
        assert VisionTask.SEGMENTATION.value == "segmentation"
        assert VisionTask.MEDICAL_IMAGING.value == "medical_imaging"

    def test_pipeline_step_type_values(self):
        assert PipelineStepType.DECODE.value == "decode"
        assert PipelineStepType.FACE_BLUR.value == "face_blur"
        assert PipelineStepType.DICOM_STRIP.value == "dicom_strip"
        assert PipelineStepType.LICENSE_PLATE_MOSAIC.value == "license_plate_mosaic"
        assert PipelineStepType.HORIZONTAL_FLIP.value == "horizontal_flip"
        assert PipelineStepType.RESIZE_CROP.value == "resize_crop"
        assert PipelineStepType.NORMALIZE.value == "normalize"


# ── Dataclass Construction ────────────────────────────────────

class TestDataclasses:
    def test_pipeline_step_defaults(self):
        step = PipelineStep(step_type=PipelineStepType.DECODE)
        assert step.params == {}

    def test_pipeline_step_with_params(self):
        step = PipelineStep(step_type=PipelineStepType.FACE_BLUR, params={"method": "gaussian"})
        assert step.params["method"] == "gaussian"

    def test_image_pipeline_empty(self):
        pipeline = ImagePipeline()
        assert pipeline.steps == []
        assert pipeline.description == ""

    def test_image_pipeline_description(self):
        pipeline = ImagePipeline()
        pipeline.add_step(PipelineStepType.DECODE)
        pipeline.add_step(PipelineStepType.FACE_BLUR)
        assert "decode" in pipeline.description
        assert "face_blur" in pipeline.description

    def test_redaction_rules_defaults(self):
        rules = RedactionRules()
        assert rules.face_blur is True
        assert rules.blur_method == "gaussian_k51"
        assert rules.dicom_strip is False
        assert rules.license_plate_mosaic is False

    def test_vision_training_config_defaults(self):
        config = VisionTrainingConfig()
        assert config.task == VisionTask.CLASSIFICATION
        assert config.architecture == "resnet50"
        assert config.num_classes == 10
        assert config.batch_size == 32
        assert config.learning_rate == 0.001

    def test_multimodal_training_config_defaults(self):
        config = MultimodalTrainingConfig()
        assert config.base_model == "InternVL2-8B"
        assert config.num_epochs == 3
        assert config.batch_size == 4
        assert config.learning_rate == 2e-5
        assert config.image_resolution == 448

    def test_training_metrics(self):
        m = TrainingMetrics(epoch=1, loss=0.5, accuracy=0.8, duration_seconds=10.0, samples_seen=100)
        assert m.epoch == 1
        assert m.loss == 0.5
        assert m.samples_seen == 100

    def test_training_result_defaults(self):
        r = TrainingResult(
            session_id="s1", model_path="/tmp", task="classification",
            total_epochs=10, final_loss=0.1, final_accuracy=0.9, watermark_injected=True,
        )
        assert r.metrics == []
        assert r.error is None
        assert r.duration_seconds == 0.0


# ── Image Pipeline ────────────────────────────────────────────

class TestImagePipeline:
    def test_add_step(self):
        pipeline = ImagePipeline()
        result = pipeline.add_step(PipelineStepType.DECODE)
        assert len(pipeline.steps) == 1
        assert result is pipeline  # fluent API

    def test_add_step_with_params(self):
        pipeline = ImagePipeline()
        pipeline.add_step(PipelineStepType.RESIZE_CROP, {"size": 224})
        assert pipeline.steps[0].params["size"] == 224

    def test_chain_steps(self):
        pipeline = ImagePipeline()
        pipeline.add_step(PipelineStepType.DECODE).add_step(PipelineStepType.NORMALIZE)
        assert len(pipeline.steps) == 2

    def test_description_chain(self):
        pipeline = ImagePipeline()
        pipeline.add_step(PipelineStepType.DECODE)
        pipeline.add_step(PipelineStepType.FACE_BLUR)
        pipeline.add_step(PipelineStepType.NORMALIZE)
        desc = pipeline.description
        assert "decode" in desc
        assert "face_blur" in desc
        assert "normalize" in desc
        assert "→" in desc


# ── Build Image Pipeline ──────────────────────────────────────

class TestBuildImagePipeline:
    def test_default_rules(self):
        rules = RedactionRules()
        pipeline = build_image_pipeline(rules)
        step_types = [s.step_type for s in pipeline.steps]
        assert PipelineStepType.DECODE in step_types
        assert PipelineStepType.FACE_BLUR in step_types
        assert PipelineStepType.HORIZONTAL_FLIP in step_types
        assert PipelineStepType.RESIZE_CROP in step_types
        assert PipelineStepType.NORMALIZE in step_types

    def test_dicom_strip_enabled(self):
        rules = RedactionRules(dicom_strip=True)
        pipeline = build_image_pipeline(rules)
        step_types = [s.step_type for s in pipeline.steps]
        assert PipelineStepType.DICOM_STRIP in step_types

    def test_license_plate_enabled(self):
        rules = RedactionRules(license_plate_mosaic=True)
        pipeline = build_image_pipeline(rules)
        step_types = [s.step_type for s in pipeline.steps]
        assert PipelineStepType.LICENSE_PLATE_MOSAIC in step_types

    def test_no_face_blur(self):
        rules = RedactionRules(face_blur=False)
        pipeline = build_image_pipeline(rules)
        step_types = [s.step_type for s in pipeline.steps]
        assert PipelineStepType.FACE_BLUR not in step_types

    def test_all_redaction_enabled(self):
        rules = RedactionRules(face_blur=True, dicom_strip=True, license_plate_mosaic=True)
        pipeline = build_image_pipeline(rules)
        step_types = [s.step_type for s in pipeline.steps]
        assert PipelineStepType.FACE_BLUR in step_types
        assert PipelineStepType.DICOM_STRIP in step_types
        assert PipelineStepType.LICENSE_PLATE_MOSAIC in step_types

    def test_always_has_augmentation(self):
        rules = RedactionRules(face_blur=False)
        pipeline = build_image_pipeline(rules)
        step_types = [s.step_type for s in pipeline.steps]
        assert PipelineStepType.HORIZONTAL_FLIP in step_types
        assert PipelineStepType.RESIZE_CROP in step_types
        assert PipelineStepType.NORMALIZE in step_types

    def test_blur_method_passed(self):
        rules = RedactionRules(blur_method="box_k31")
        pipeline = build_image_pipeline(rules)
        blur_step = next(s for s in pipeline.steps if s.step_type == PipelineStepType.FACE_BLUR)
        assert blur_step.params["method"] == "box_k31"


# ── Pipeline Steps ────────────────────────────────────────────

class TestPipelineSteps:
    def test_decode_step_passthrough(self):
        step = DecodeStep()
        data = b"test-image-data"
        assert step.apply(data) == data

    def test_face_blur_step(self):
        step = FaceBlurStep()
        data = b"image-with-faces;face=region-1"
        processed = step.apply(data)
        assert processed != data
        assert b"cds_step=face_blur:gaussian_k51" in processed

    def test_dicom_strip_step(self):
        step = DICOMStripStep()
        data = b"dicom-image;PatientName=Alice;PatientID=PID001;PixelData=..."
        processed = step.apply(data)
        assert b"PatientName" not in processed
        assert b"PatientID" not in processed
        assert b"PixelData" in processed
        assert b"cds_step=dicom_tags_stripped" in processed
        assert "PatientName" in DICOMStripStep.SENSITIVE_TAGS

    def test_license_plate_step(self):
        step = LicensePlateStep()
        data = b"image-with-plates;license_plate=ABC123"
        processed = step.apply(data)
        assert b"ABC123" not in processed
        assert b"license_plate=[MOSAIC]" in processed
        assert b"cds_step=license_plate:mosaic" in processed

    def test_dicom_sensitive_tags(self):
        assert "PatientName" in DICOMStripStep.SENSITIVE_TAGS
        assert "PatientID" in DICOMStripStep.SENSITIVE_TAGS
        assert "PatientBirthDate" in DICOMStripStep.SENSITIVE_TAGS
        assert "InstitutionName" in DICOMStripStep.SENSITIVE_TAGS


# ── Secure Image Data Loader ──────────────────────────────────

class TestSecureImageDataLoader:
    def test_load(self):
        loader = SecureImageDataLoader(product_ids=["p1"])
        loader.load()
        assert loader.num_samples == 100

    def test_get_batches(self):
        loader = SecureImageDataLoader(product_ids=["p1"], batch_size=20)
        loader.load()
        batches = loader.get_batches()
        assert len(batches) == 5  # 100 / 20
        assert len(batches[0][0]) == 20
        assert len(batches[0][1]) == 20

    def test_batch_with_pipeline(self):
        pipeline = ImagePipeline()
        pipeline.add_step(PipelineStepType.DECODE)
        pipeline.add_step(PipelineStepType.DICOM_STRIP)
        pipeline.add_step(PipelineStepType.LICENSE_PLATE_MOSAIC)
        pipeline.add_step(PipelineStepType.FACE_BLUR)
        loader = SecureImageDataLoader(product_ids=["p1"], pipeline=pipeline, batch_size=10)
        loader.load()
        batches = loader.get_batches()
        assert len(batches) == 10
        first_image = batches[0][0][0]
        assert b"PatientName" not in first_image
        assert b"PatientID" not in first_image
        assert b"[MOSAIC]" in first_image
        assert b"cds_step=face_blur" in first_image

    def test_num_samples_before_load(self):
        loader = SecureImageDataLoader(product_ids=["p1"])
        assert loader.num_samples == 0

    def test_labels_match_images(self):
        loader = SecureImageDataLoader(product_ids=["p1"], batch_size=50)
        loader.load()
        batches = loader.get_batches()
        images, labels = batches[0]
        assert len(images) == len(labels)


# ── Secure Multimodal Data Loader ─────────────────────────────

class TestSecureMultimodalDataLoader:
    def test_load(self):
        loader = SecureMultimodalDataLoader(image_product_ids=["i1"], text_product_ids=["t1"])
        loader.load()
        assert loader.num_samples == 50

    def test_get_batches(self):
        loader = SecureMultimodalDataLoader(
            image_product_ids=["i1"], text_product_ids=["t1"], batch_size=10,
        )
        loader.load()
        batches = loader.get_batches()
        assert len(batches) == 5
        assert len(batches[0]) == 10

    def test_pair_structure(self):
        loader = SecureMultimodalDataLoader(image_product_ids=["i1"], text_product_ids=["t1"])
        loader.load()
        batches = loader.get_batches()
        img, text = batches[0][0]
        assert isinstance(img, bytes)
        assert isinstance(text, str)

    def test_num_samples_before_load(self):
        loader = SecureMultimodalDataLoader(image_product_ids=["i1"], text_product_ids=["t1"])
        assert loader.num_samples == 0


# ── Allowed Multimodal Models ─────────────────────────────────

class TestAllowedModels:
    def test_contains_internvl2(self):
        assert "InternVL2-8B" in ALLOWED_MULTIMODAL_MODELS
        assert "InternVL2-26B" in ALLOWED_MULTIMODAL_MODELS

    def test_contains_qwen2_vl(self):
        assert "Qwen2-VL-7B" in ALLOWED_MULTIMODAL_MODELS
        assert "Qwen2-VL-72B" in ALLOWED_MULTIMODAL_MODELS

    def test_contains_others(self):
        assert "CogVLM2" in ALLOWED_MULTIMODAL_MODELS
        assert "MiniCPM-V-2" in ALLOWED_MULTIMODAL_MODELS

    def test_disallowed_model(self):
        assert "GPT-4V" not in ALLOWED_MULTIMODAL_MODELS


# ── Vision Training Runtime ───────────────────────────────────

class TestVisionTrainingRuntime:
    def test_run_classification(self):
        runtime = VisionModelTrainingRuntime()
        config = VisionTrainingConfig(num_epochs=3, batch_size=10)
        result = runtime.run_training("sess-v1", config, ["product-1"])
        assert result.session_id == "sess-v1"
        assert result.task == "classification"
        assert result.total_epochs == 3
        assert result.final_loss > 0
        assert result.final_accuracy > 0
        assert result.watermark_injected is True
        assert result.error is None

    def test_run_detection(self):
        runtime = VisionModelTrainingRuntime()
        config = VisionTrainingConfig(task=VisionTask.DETECTION, num_epochs=2)
        result = runtime.run_training("sess-v2", config, ["product-1"])
        assert result.task == "detection"

    def test_run_segmentation(self):
        runtime = VisionModelTrainingRuntime()
        config = VisionTrainingConfig(task=VisionTask.SEGMENTATION, num_epochs=2)
        result = runtime.run_training("sess-v3", config, ["product-1"])
        assert result.task == "segmentation"

    def test_run_medical_imaging(self):
        runtime = VisionModelTrainingRuntime()
        config = VisionTrainingConfig(
            task=VisionTask.MEDICAL_IMAGING,
            redaction_rules=RedactionRules(dicom_strip=True),
            num_epochs=2,
        )
        result = runtime.run_training("sess-v4", config, ["product-1"])
        assert result.task == "medical_imaging"

    def test_metrics_per_epoch(self):
        runtime = VisionModelTrainingRuntime()
        config = VisionTrainingConfig(num_epochs=4, batch_size=20)
        result = runtime.run_training("sess-v5", config, ["product-1"])
        assert len(result.metrics) == 4
        for m in result.metrics:
            assert isinstance(m, TrainingMetrics)
            assert m.loss > 0
            assert 0 < m.accuracy <= 1.0

    def test_duration_positive(self):
        runtime = VisionModelTrainingRuntime()
        config = VisionTrainingConfig(num_epochs=1)
        result = runtime.run_training("sess-v6", config, ["product-1"])
        assert result.duration_seconds >= 0

    def test_model_path_contains_session(self):
        runtime = VisionModelTrainingRuntime()
        config = VisionTrainingConfig(num_epochs=1)
        result = runtime.run_training("sess-v7", config, ["product-1"])
        assert "sess-v7" in result.model_path

    def test_loss_decreases(self):
        runtime = VisionModelTrainingRuntime()
        config = VisionTrainingConfig(num_epochs=5, batch_size=50)
        result = runtime.run_training("sess-v8", config, ["product-1"])
        first_loss = result.metrics[0].loss
        last_loss = result.metrics[-1].loss
        assert last_loss < first_loss

    def test_metrics_are_deterministic(self):
        runtime = VisionModelTrainingRuntime()
        config = VisionTrainingConfig(num_epochs=3, batch_size=16)
        first = runtime.run_training("sess-v9", config, ["product-1"])
        second = runtime.run_training("sess-v9", config, ["product-1"])
        assert [m.loss for m in first.metrics] == [m.loss for m in second.metrics]
        assert [m.accuracy for m in first.metrics] == [m.accuracy for m in second.metrics]

    def test_singleton_exists(self):
        assert vision_training_runtime is not None
        assert isinstance(vision_training_runtime, VisionModelTrainingRuntime)


# ── Multimodal Training Runtime ───────────────────────────────

class TestMultimodalTrainingRuntime:
    def test_run_internvl2(self):
        runtime = MultimodalTrainingRuntime()
        config = MultimodalTrainingConfig(base_model="InternVL2-8B", num_epochs=2)
        result = runtime.run_training("mm-1", config, ["img-1"], ["txt-1"])
        assert result.session_id == "mm-1"
        assert result.task == "multimodal"
        assert result.total_epochs == 2
        assert result.watermark_injected is True
        assert result.error is None

    def test_run_qwen2_vl(self):
        runtime = MultimodalTrainingRuntime()
        config = MultimodalTrainingConfig(base_model="Qwen2-VL-7B", num_epochs=2)
        result = runtime.run_training("mm-2", config, ["img-1"], ["txt-1"])
        assert result.error is None

    def test_run_cogvlm2(self):
        runtime = MultimodalTrainingRuntime()
        config = MultimodalTrainingConfig(base_model="CogVLM2", num_epochs=2)
        result = runtime.run_training("mm-3", config, ["img-1"], ["txt-1"])
        assert result.error is None

    def test_disallowed_model(self):
        runtime = MultimodalTrainingRuntime()
        config = MultimodalTrainingConfig(base_model="GPT-4V")
        result = runtime.run_training("mm-4", config, ["img-1"], ["txt-1"])
        assert result.error is not None
        assert "not allowed" in result.error
        assert result.total_epochs == 0
        assert result.watermark_injected is False

    def test_metrics_per_epoch(self):
        runtime = MultimodalTrainingRuntime()
        config = MultimodalTrainingConfig(base_model="InternVL2-8B", num_epochs=3)
        result = runtime.run_training("mm-5", config, ["img-1"], ["txt-1"])
        assert len(result.metrics) == 3

    def test_model_path_contains_session(self):
        runtime = MultimodalTrainingRuntime()
        config = MultimodalTrainingConfig(base_model="InternVL2-8B", num_epochs=1)
        result = runtime.run_training("mm-6", config, ["img-1"], ["txt-1"])
        assert "mm-6" in result.model_path
        assert "multimodal" in result.model_path

    def test_loss_decreases(self):
        runtime = MultimodalTrainingRuntime()
        config = MultimodalTrainingConfig(base_model="InternVL2-8B", num_epochs=5)
        result = runtime.run_training("mm-7", config, ["img-1"], ["txt-1"])
        first_loss = result.metrics[0].loss
        last_loss = result.metrics[-1].loss
        assert last_loss < first_loss

    def test_metrics_are_deterministic(self):
        runtime = MultimodalTrainingRuntime()
        config = MultimodalTrainingConfig(base_model="InternVL2-8B", num_epochs=3)
        first = runtime.run_training("mm-8", config, ["img-1"], ["txt-1"])
        second = runtime.run_training("mm-8", config, ["img-1"], ["txt-1"])
        assert [m.loss for m in first.metrics] == [m.loss for m in second.metrics]
        assert [m.accuracy for m in first.metrics] == [m.accuracy for m in second.metrics]

    def test_singleton_exists(self):
        assert multimodal_training_runtime is not None
        assert isinstance(multimodal_training_runtime, MultimodalTrainingRuntime)

    def test_all_allowed_models(self):
        runtime = MultimodalTrainingRuntime()
        for model in ALLOWED_MULTIMODAL_MODELS:
            config = MultimodalTrainingConfig(base_model=model, num_epochs=1)
            result = runtime.run_training(f"mm-{model}", config, ["img-1"], ["txt-1"])
            assert result.error is None, f"Model {model} should be allowed"
