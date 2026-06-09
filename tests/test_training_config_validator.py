"""Tests for Training Config Validator (S6-4)."""
import pytest

from app.services.training_config_validator import (
    TrainingConfigValidator, ValidationSeverity,
    ValidationResult, training_config_validator,
)


@pytest.fixture
def validator():
    return TrainingConfigValidator()


@pytest.fixture
def base_config():
    return {
        "learning_rate": 0.001,
        "epochs": 10,
        "batch_size": 32,
    }


class TestRequiredFields:
    def test_missing_learning_rate(self, validator):
        result = validator.validate({"epochs": 10, "batch_size": 32})
        assert result.valid is False
        assert result.errors >= 1
        assert any("learning_rate" in i.field and "missing" in i.message.lower() for i in result.issues)

    def test_missing_epochs(self, validator):
        result = validator.validate({"learning_rate": 0.001, "batch_size": 32})
        assert result.valid is False
        assert any("epochs" in i.field and "missing" in i.message.lower() for i in result.issues)

    def test_missing_batch_size(self, validator):
        result = validator.validate({"learning_rate": 0.001, "epochs": 10})
        assert result.valid is False
        assert any("batch_size" in i.field and "missing" in i.message.lower() for i in result.issues)

    def test_all_required_present(self, validator, base_config):
        result = validator.validate(base_config)
        assert result.valid is True
        assert result.errors == 0


class TestParameterBounds:
    def test_learning_rate_too_low(self, validator, base_config):
        base_config["learning_rate"] = 1e-10
        result = validator.validate(base_config)
        assert result.valid is False
        assert any("learning_rate" in i.field and i.severity == ValidationSeverity.ERROR for i in result.issues)

    def test_learning_rate_too_high(self, validator, base_config):
        base_config["learning_rate"] = 2.0
        result = validator.validate(base_config)
        assert result.valid is False

    def test_learning_rate_valid(self, validator, base_config):
        base_config["learning_rate"] = 0.001
        result = validator.validate(base_config)
        assert result.valid is True

    def test_epochs_too_low(self, validator, base_config):
        base_config["epochs"] = 0
        result = validator.validate(base_config)
        assert result.valid is False

    def test_epochs_too_high(self, validator, base_config):
        base_config["epochs"] = 999999
        result = validator.validate(base_config)
        assert result.valid is False

    def test_batch_size_too_high(self, validator, base_config):
        base_config["batch_size"] = 999999
        result = validator.validate(base_config)
        assert result.valid is False

    def test_non_numeric_value(self, validator, base_config):
        base_config["learning_rate"] = "fast"
        result = validator.validate(base_config)
        assert result.valid is False
        assert any("learning_rate" in i.field and "number" in i.message.lower() for i in result.issues)

    def test_weight_decay_bounds(self, validator, base_config):
        base_config["weight_decay"] = 1.5
        result = validator.validate(base_config)
        assert result.valid is False

    def test_max_seq_length_bounds(self, validator, base_config):
        base_config["max_seq_length"] = 200000
        result = validator.validate(base_config)
        assert result.valid is False


class TestModelArchitecture:
    def test_allowed_architecture(self, validator, base_config):
        base_config["model_name"] = "bert-base-uncased"
        result = validator.validate(base_config)
        assert result.valid is True
        arch_warnings = [i for i in result.issues if "whitelist" in i.message.lower()]
        assert len(arch_warnings) == 0

    def test_allowed_with_org_prefix(self, validator, base_config):
        base_config["model_name"] = "meta-llama/Llama-2-7b"
        result = validator.validate(base_config)
        assert result.valid is True

    def test_unknown_architecture_warning(self, validator, base_config):
        base_config["model_name"] = "custom-mymodel-v1"
        result = validator.validate(base_config)
        assert result.valid is True  # Warning, not error
        assert any("whitelist" in i.message.lower() and i.severity == ValidationSeverity.WARNING for i in result.issues)

    def test_no_model_name_skips_check(self, validator, base_config):
        result = validator.validate(base_config)
        assert result.valid is True


class TestDPConfig:
    def test_valid_dp_config(self, validator, base_config):
        base_config["dp_config"] = {
            "noise_multiplier": 1.1,
            "max_grad_norm": 1.0,
            "delta": 1e-5,
        }
        result = validator.validate(base_config)
        assert result.valid is True

    def test_noise_multiplier_too_low_warning(self, validator, base_config):
        base_config["dp_config"] = {
            "noise_multiplier": 0.05,
            "max_grad_norm": 1.0,
            "delta": 1e-5,
        }
        result = validator.validate(base_config)
        assert result.valid is True
        assert any("noise" in i.field and i.severity == ValidationSeverity.WARNING for i in result.issues)

    def test_dp_param_out_of_range(self, validator, base_config):
        base_config["dp_config"] = {
            "noise_multiplier": 200.0,
        }
        result = validator.validate(base_config)
        assert result.valid is False
        assert any("dp_config.noise_multiplier" in i.field and i.severity == ValidationSeverity.ERROR for i in result.issues)

    def test_dp_non_numeric(self, validator, base_config):
        base_config["dp_config"] = {
            "noise_multiplier": "loud",
        }
        result = validator.validate(base_config)
        assert result.valid is False

    def test_delta_out_of_range(self, validator, base_config):
        base_config["dp_config"] = {"delta": 2.0}
        result = validator.validate(base_config)
        assert result.valid is False

    def test_no_dp_config_skips(self, validator, base_config):
        result = validator.validate(base_config)
        assert result.valid is True


class TestResourceChecks:
    def test_large_effective_batch_info(self, validator, base_config):
        base_config["batch_size"] = 512
        base_config["gradient_accumulation_steps"] = 4
        result = validator.validate(base_config)
        assert result.valid is True
        assert any("effective batch" in i.message.lower() and i.severity == ValidationSeverity.INFO for i in result.issues)

    def test_memory_warning_large(self, validator, base_config):
        base_config["batch_size"] = 65536
        base_config["max_seq_length"] = 131072
        result = validator.validate(base_config)
        assert result.valid is True
        mem_warnings = [i for i in result.issues if "memory" in i.message.lower()]
        assert len(mem_warnings) > 0


class TestSanityWarnings:
    def test_high_epoch_warning(self, validator, base_config):
        base_config["epochs"] = 5000
        result = validator.validate(base_config)
        assert result.valid is True
        assert any("epoch" in i.field and i.severity == ValidationSeverity.WARNING for i in result.issues)

    def test_high_learning_rate_warning(self, validator, base_config):
        base_config["learning_rate"] = 0.5
        result = validator.validate(base_config)
        assert result.valid is True
        assert any("learning_rate" in i.field and i.severity == ValidationSeverity.WARNING for i in result.issues)

    def test_normal_values_no_warnings(self, validator, base_config):
        result = validator.validate(base_config)
        assert result.valid is True
        assert result.warnings == 0


class TestSingleton:
    def test_singleton_exists(self):
        assert training_config_validator is not None
        assert isinstance(training_config_validator, TrainingConfigValidator)


class TestValidationResult:
    def test_add_error_sets_valid_false(self):
        r = ValidationResult(valid=True)
        r.add_error("field", "msg")
        assert r.valid is False
        assert r.errors == 1

    def test_add_warning_keeps_valid(self):
        r = ValidationResult(valid=True)
        r.add_warning("field", "msg")
        assert r.valid is True
        assert r.warnings == 1

    def test_add_info(self):
        r = ValidationResult(valid=True)
        r.add_info("field", "msg")
        assert len(r.issues) == 1
        assert r.issues[0].severity == ValidationSeverity.INFO
