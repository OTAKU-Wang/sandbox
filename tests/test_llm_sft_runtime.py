"""Tests for LLM SFT Runtime — LoRA/QLoRA + MIA probe."""
import pytest

from app.services.llm_sft_runtime import (
    LLMSFTRuntime,
    SFTConfig,
    SFTResult,
    LoRAConfig,
    DifferentialPrivacyConfig,
    FinetuneMethod,
    TrainingPhase,
    TrainingMetrics,
    MIAProbe,
    MIAProbeResult,
    SecureTextDataLoader,
    MemorizationRiskError,
    ALLOWED_BASE_MODELS,
)


@pytest.fixture
def runtime():
    return LLMSFTRuntime()


@pytest.fixture
def default_config():
    return SFTConfig(base_model="Qwen2.5-7B", num_epochs=2, batch_size=4)


# ── Allowed Models ────────────────────────────────────────────

class TestAllowedModels:
    def test_allowed_models_not_empty(self):
        assert len(ALLOWED_BASE_MODELS) > 0

    def test_contains_qwen(self):
        assert "Qwen2.5-7B" in ALLOWED_BASE_MODELS

    def test_contains_llama(self):
        assert "Llama-3.1-8B" in ALLOWED_BASE_MODELS

    def test_contains_internlm(self):
        assert "InternLM2.5-7B" in ALLOWED_BASE_MODELS

    def test_contains_baichuan(self):
        assert "Baichuan2-7B" in ALLOWED_BASE_MODELS


# ── SFT Config ────────────────────────────────────────────────

class TestSFTConfig:
    def test_default_config(self):
        c = SFTConfig()
        assert c.base_model == "Qwen2.5-7B"
        assert c.method == FinetuneMethod.LORA
        assert c.num_epochs == 3
        assert c.batch_size == 8
        assert c.max_memorization_score == 0.3

    def test_lora_config_defaults(self):
        lc = LoRAConfig()
        assert lc.r == 16
        assert lc.lora_alpha == 32
        assert "q_proj" in lc.target_modules
        assert lc.lora_dropout == 0.1

    def test_dp_config_defaults(self):
        dp = DifferentialPrivacyConfig()
        assert dp.enabled is False
        assert dp.epsilon == 8.0
        assert dp.max_grad_norm == 1.0

    def test_finetune_methods(self):
        assert FinetuneMethod.FULL.value == "full"
        assert FinetuneMethod.LORA.value == "lora"
        assert FinetuneMethod.QLORA.value == "qlora"


# ── SFT Training ──────────────────────────────────────────────

class TestSFTTraining:
    def test_run_sft_success(self, runtime, default_config):
        result = runtime.run_sft("sess-1", default_config, ["product-1"])
        assert isinstance(result, SFTResult)
        assert result.phase == TrainingPhase.COMPLETED
        assert result.total_epochs == 2
        assert result.final_loss > 0
        assert result.watermark_injected is True
        assert runtime._watermarks["sess-1"]["passed"] is True
        assert result.error is None

    def test_run_sft_with_lora(self, runtime):
        config = SFTConfig(
            base_model="Qwen2.5-7B",
            method=FinetuneMethod.LORA,
            num_epochs=1,
            lora_config=LoRAConfig(r=32, lora_alpha=64),
        )
        result = runtime.run_sft("sess-lora", config, ["p1"])
        assert result.phase == TrainingPhase.COMPLETED

    def test_run_sft_with_qlora(self, runtime):
        config = SFTConfig(
            base_model="Llama-3.1-8B",
            method=FinetuneMethod.QLORA,
            num_epochs=1,
        )
        result = runtime.run_sft("sess-qlora", config, ["p1"])
        assert result.phase == TrainingPhase.COMPLETED

    def test_run_sft_invalid_model(self, runtime):
        config = SFTConfig(base_model="GPT-5")
        result = runtime.run_sft("sess-bad", config, ["p1"])
        assert result.phase == TrainingPhase.FAILED
        assert "not allowed" in result.error

    def test_run_sft_records_metrics(self, runtime, default_config):
        result = runtime.run_sft("sess-metrics", default_config, ["p1"])
        assert len(result.metrics) == 2
        assert result.metrics[0].epoch == 0
        assert result.metrics[1].epoch == 1

    def test_run_sft_decreasing_loss(self, runtime):
        config = SFTConfig(base_model="Qwen2.5-7B", num_epochs=3, batch_size=4)
        result = runtime.run_sft("sess-dec", config, ["p1"])
        # Loss should generally decrease
        losses = [m.loss for m in result.metrics]
        assert losses[-1] < losses[0]

    def test_run_sft_with_dp(self, runtime):
        config = SFTConfig(
            base_model="Qwen2.5-7B",
            num_epochs=1,
            dp_config=DifferentialPrivacyConfig(enabled=True, epsilon=4.0),
        )
        result = runtime.run_sft("sess-dp", config, ["p1"])
        assert result.phase == TrainingPhase.COMPLETED

    def test_run_sft_duration(self, runtime, default_config):
        result = runtime.run_sft("sess-dur", default_config, ["p1"])
        assert result.duration_seconds > 0

    def test_run_sft_multiple_products(self, runtime):
        config = SFTConfig(base_model="Qwen2.5-7B", num_epochs=1, batch_size=4)
        result = runtime.run_sft("sess-multi", config, ["p1", "p2", "p3"])
        assert result.phase == TrainingPhase.COMPLETED


# ── MIA Probe ─────────────────────────────────────────────────

class TestMIAProbe:
    def test_probe_compute_advantage(self):
        probe = MIAProbe(
            member_samples=["a", "b", "c"],
            non_member_samples=["x", "y", "z"],
        )
        score = probe.compute_advantage()
        assert 0.0 <= score <= 1.0

    def test_probe_empty_samples(self):
        probe = MIAProbe(member_samples=[], non_member_samples=[])
        assert probe.compute_advantage() == 0.0

    def test_probe_detailed_result(self):
        probe = MIAProbe(
            member_samples=["a"] * 100,
            non_member_samples=["b"] * 100,
        )
        result = probe.detailed_result(threshold=0.3)
        assert isinstance(result, MIAProbeResult)
        assert result.risk_level in ("low", "medium", "high")
        assert result.num_member_samples == 100
        assert result.num_non_member_samples == 100

    def test_probe_risk_levels(self):
        probe = MIAProbe(member_samples=["a"], non_member_samples=["b"])
        result = probe.detailed_result(threshold=0.3)
        assert result.threshold == 0.3


# ── Secure Data Loader ────────────────────────────────────────

class TestSecureDataLoader:
    def test_load(self):
        loader = SecureTextDataLoader(product_ids=["p1", "p2"])
        loader.load()
        assert loader.num_samples > 0

    def test_get_batches(self):
        loader = SecureTextDataLoader(product_ids=["p1"], batch_size=10)
        loader.load()
        batches = loader.get_batches()
        assert len(batches) > 0
        assert all(isinstance(b, list) for b in batches)

    def test_get_sample(self):
        loader = SecureTextDataLoader(product_ids=["p1"])
        loader.load()
        samples = loader.get_sample(10)
        assert len(samples) <= 10

    def test_get_holdout(self):
        loader = SecureTextDataLoader(product_ids=["p1"])
        loader.load()
        holdout = loader.get_holdout(10)
        assert len(holdout) <= 10

    def test_pii_masking_flag(self):
        loader = SecureTextDataLoader(product_ids=["p1"], apply_pii_masking=False)
        loader.load()
        batches = loader.get_batches()
        assert len(batches) > 0

    def test_pii_masking_replaces_sensitive_values(self):
        loader = SecureTextDataLoader(product_ids=["p1"], batch_size=1)
        loader.load()
        batch = loader.get_batches()[0]
        sample = batch[0]
        assert "[EMAIL]" in sample
        assert "[PHONE]" in sample
        assert "example.com" not in sample
        assert "138" not in sample


# ── Training Phase ────────────────────────────────────────────

class TestTrainingPhase:
    def test_phase_values(self):
        assert TrainingPhase.INIT.value == "init"
        assert TrainingPhase.TRAINING.value == "training"
        assert TrainingPhase.COMPLETED.value == "completed"
        assert TrainingPhase.FAILED.value == "failed"
        assert TrainingPhase.PROBING.value == "probing"
        assert TrainingPhase.WATERMARKING.value == "watermarking"

    def test_runtime_phase_tracking(self, runtime):
        assert runtime.phase == TrainingPhase.INIT

    def test_phase_after_completion(self, runtime, default_config):
        # Use high memorization threshold so this test is about phase tracking only.
        config = SFTConfig(base_model="Qwen2.5-7B", num_epochs=2, batch_size=4, max_memorization_score=0.99)
        runtime.run_sft("sess-phase", config, ["p1"])
        assert runtime.phase == TrainingPhase.COMPLETED

    def test_phase_after_failure(self, runtime):
        config = SFTConfig(base_model="InvalidModel")
        runtime.run_sft("sess-fail", config, ["p1"])
        assert runtime.phase == TrainingPhase.FAILED


# ── MemorizationRiskError ─────────────────────────────────────

class TestMemorizationRiskError:
    def test_error_is_exception(self):
        with pytest.raises(MemorizationRiskError):
            raise MemorizationRiskError("test")

    def test_error_message(self):
        try:
            raise MemorizationRiskError("score too high")
        except MemorizationRiskError as e:
            assert "score too high" in str(e)
