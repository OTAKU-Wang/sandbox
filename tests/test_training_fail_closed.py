"""Training fail-closed + MIA honesty tests (gap C1/C2 · T10)."""
import pytest

from app.core.config import get_settings


def test_cpu_trainer_fails_closed_when_torch_required(monkeypatch):
    """TRAINING_REQUIRE_TORCH=true must reject, not simulate, when torch is
    missing — a simulated result must never masquerade as real training."""
    from app.services.cpu_trainer import CPUTrainer

    settings = get_settings()
    monkeypatch.setattr(settings, "TRAINING_REQUIRE_TORCH", True)
    trainer = CPUTrainer()
    if trainer.available:
        pytest.skip("torch installed in this environment — nothing to gate")
    with pytest.raises(RuntimeError):
        trainer.train(None, None, [{"text": "x"}])


def test_cpu_trainer_allows_simulation_when_not_required(monkeypatch):
    from app.services.cpu_trainer import CPUTrainer

    settings = get_settings()
    monkeypatch.setattr(settings, "TRAINING_REQUIRE_TORCH", False)
    trainer = CPUTrainer()
    if trainer.available:
        pytest.skip("torch installed in this environment")
    result = trainer.train(None, None, [{"text": "x"}])
    assert result is not None


def test_mia_probe_labels_proxy_estimate():
    from app.services.llm_sft_runtime import MIAProbe

    probe = MIAProbe(member_samples=["member sample alpha"], non_member_samples=["holdout beta"])
    result = probe.detailed_result(threshold=0.3)
    assert result.mia_status == "proxy_estimate"
    assert result.advantage_score >= 0.0


def test_mia_probe_not_evaluable_without_samples():
    from app.services.llm_sft_runtime import MIAProbe

    probe = MIAProbe(member_samples=[], non_member_samples=[])
    result = probe.detailed_result(threshold=0.3)
    assert result.mia_status == "not_evaluable"
    assert result.advantage_score == 0.0
