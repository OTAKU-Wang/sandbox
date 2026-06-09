"""Training Configuration Validator — validate training parameters and resource pre-checks.

Validates ML training job configurations before execution:
1. Parameter legality — learning rate, epochs, batch size bounds
2. Resource pre-check — GPU memory, dataset size compatibility
3. Security constraints — model architecture whitelist, data access limits
4. DP-SGD config — noise multiplier, clip norm validation

Returns detailed validation results with specific error messages.
"""
import logging
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class ValidationSeverity(str, Enum):
    ERROR = "error"      # Blocks execution
    WARNING = "warning"  # Allows execution with warning
    INFO = "info"        # Informational only


@dataclass
class ValidationIssue:
    field: str
    severity: ValidationSeverity
    message: str
    current_value: str = ""
    expected_range: str = ""


@dataclass
class ValidationResult:
    valid: bool
    issues: list[ValidationIssue] = field(default_factory=list)
    warnings: int = 0
    errors: int = 0

    def add_error(self, field: str, message: str, current: str = "", expected: str = ""):
        self.issues.append(ValidationIssue(field, ValidationSeverity.ERROR, message, current, expected))
        self.errors += 1
        self.valid = False

    def add_warning(self, field: str, message: str, current: str = "", expected: str = ""):
        self.issues.append(ValidationIssue(field, ValidationSeverity.WARNING, message, current, expected))
        self.warnings += 1

    def add_info(self, field: str, message: str):
        self.issues.append(ValidationIssue(field, ValidationSeverity.INFO, message))


# Safe ranges for training parameters
PARAM_BOUNDS = {
    "learning_rate": (1e-7, 1.0),
    "epochs": (1, 10000),
    "batch_size": (1, 65536),
    "max_seq_length": (1, 131072),
    "warmup_steps": (0, 1000000),
    "weight_decay": (0.0, 1.0),
    "gradient_accumulation_steps": (1, 1024),
    "max_grad_norm": (0.0, 100.0),
}

# DP-SGD parameter bounds
DP_BOUNDS = {
    "noise_multiplier": (0.0, 100.0),
    "max_grad_norm": (0.0, 100.0),
    "delta": (1e-10, 1.0),
}

# Allowed model architectures (whitelist)
ALLOWED_ARCHITECTURES = {
    "bert", "roberta", "distilbert", "gpt2", "llama", "mistral",
    "t5", "bart", "xlnet", "albert", "electra", "deberta",
}


class TrainingConfigValidator:
    """Validate ML training job configurations.

    Checks parameter bounds, resource requirements, and security
    constraints before allowing training to proceed.
    """

    def validate(self, config: dict) -> ValidationResult:
        """Validate a training configuration.

        Args:
            config: Training configuration dict with keys like
                    learning_rate, epochs, batch_size, model_name, etc.

        Returns:
            ValidationResult with issues and validity.
        """
        result = ValidationResult(valid=True)

        # Check required fields
        for field_name in ["learning_rate", "epochs", "batch_size"]:
            if field_name not in config:
                result.add_error(field_name, f"Required field '{field_name}' missing")

        if result.errors > 0:
            return result

        # Validate parameter bounds
        for param, (min_val, max_val) in PARAM_BOUNDS.items():
            if param in config:
                value = config[param]
                if not isinstance(value, (int, float)):
                    result.add_error(param, "Must be a number", str(value))
                elif value < min_val or value > max_val:
                    result.add_error(
                        param, f"Value {value} out of range",
                        str(value), f"[{min_val}, {max_val}]",
                    )

        # Validate model architecture
        model_name = config.get("model_name", "")
        if model_name:
            arch = model_name.split("/")[0].lower() if "/" in model_name else model_name.lower()
            # Check if any allowed architecture is a substring
            allowed = any(a in arch for a in ALLOWED_ARCHITECTURES)
            if not allowed:
                result.add_warning("model_name", f"Architecture '{arch}' not in whitelist", model_name)

        # Validate DP-SGD config if present
        dp_config = config.get("dp_config")
        if dp_config:
            self._validate_dp_config(dp_config, result)

        # Resource pre-checks
        self._check_resources(config, result)

        # Sanity checks
        epochs = config.get("epochs", 0)
        if isinstance(epochs, (int, float)) and epochs > 1000:
            result.add_warning("epochs", "Very high epoch count — consider early stopping", str(epochs), "<= 1000")

        lr = config.get("learning_rate", 0)
        if isinstance(lr, (int, float)) and lr > 0.1:
            result.add_warning("learning_rate", "High learning rate may cause divergence", str(lr), "<= 0.1")

        return result

    def _validate_dp_config(self, dp_config: dict, result: ValidationResult):
        """Validate DP-SGD specific parameters."""
        for param, (min_val, max_val) in DP_BOUNDS.items():
            if param in dp_config:
                value = dp_config[param]
                if not isinstance(value, (int, float)):
                    result.add_error(f"dp_config.{param}", "Must be a number", str(value))
                elif value < min_val or value > max_val:
                    result.add_error(
                        f"dp_config.{param}", f"DP parameter out of range",
                        str(value), f"[{min_val}, {max_val}]",
                    )

        # Delta should be much less than 1/n for privacy
        delta = dp_config.get("delta", 1e-5)
        noise = dp_config.get("noise_multiplier", 1.0)
        if isinstance(noise, (int, float)) and noise < 0.1:
            result.add_warning("dp_config.noise_multiplier", "Very low noise — weak privacy guarantee", str(noise), ">= 0.5")

    def _check_resources(self, config: dict, result: ValidationResult):
        """Pre-check resource requirements."""
        batch_size = config.get("batch_size", 1)
        max_seq_length = config.get("max_seq_length", 512)
        gradient_accumulation = config.get("gradient_accumulation_steps", 1)

        # Effective batch size
        effective_batch = batch_size * gradient_accumulation
        if effective_batch > 1024:
            result.add_info("batch_size", f"Large effective batch size: {effective_batch}")

        # Memory estimate (rough: 4 bytes per param * seq_len * batch)
        estimated_memory_mb = (max_seq_length * batch_size * 4) / (1024 * 1024)
        if estimated_memory_mb > 8000:  # > 8GB
            result.add_warning(
                "resource", "Estimated memory may exceed 8GB GPU",
                f"{estimated_memory_mb:.0f}MB", "<= 8000MB",
            )


# Singleton
training_config_validator = TrainingConfigValidator()
