"""A102 — DP-SGD 噪声尺度少除 batch_size，且隐私/梯度字段取自「配置」而非「实际」。

`app/services/cpu_trainer.py` 原实现：`noise_scale = dp_noise_multiplier * dp_max_grad_norm`
（标准 DP-SGD 求的是均值梯度，尺度应为 σC/B ⇒ 实际噪声比声明的 (ε,δ) 大一个 batch_size 倍），
`dp_noise_injected=cfg.dp_enabled`（配置回声，不是「确实加了噪」），`gradient_norm=0.0` 硬编码。
与 A101 叠加时结果是「已注入 DP 噪声、0 梯幅、训练完成」，而真实情况是零噪声零更新。

torch 用真 torch（本机 2.12 cpu）；噪声尺度判据是送进 `torch.normal` 的 `std` 实参，
不是计时；上报字段判据走 `_clip_and_noise` 这个本模块自己的接缝。
"""
from typing import Any

import pytest

torch = pytest.importorskip("torch")
import torch.nn as nn  # noqa: E402
import torch.nn.functional as F  # noqa: E402

from app.services import cpu_trainer as ct  # noqa: E402
from app.services.cpu_trainer import CPUTrainer, TrainConfig  # noqa: E402

VOCAB = 32
HIDDEN = 16


class Out:
    def __init__(self, loss=None, logits=None):
        self.loss = loss
        self.logits = logits


class TinyLM(nn.Module):
    def __init__(self):
        super().__init__()
        self.embedding = nn.Embedding(VOCAB, HIDDEN)
        self.head = nn.Linear(HIDDEN, VOCAB)

    def forward(self, input_ids=None, labels=None, **kwargs):
        logits = self.head(self.embedding(input_ids))
        loss = None
        if labels is not None:
            loss = F.cross_entropy(
                logits.reshape(-1, logits.size(-1)), labels.reshape(-1)
            )
        return Out(loss=loss, logits=logits)


class TorchProbe:
    """代理 torch，只截获 torch.normal 的 std 实参。"""

    def __init__(self, real):
        self.real = real
        self.stds: list[float] = []

    def normal(self, *args, **kwargs):
        std = kwargs.get("std", args[1] if len(args) > 1 else None)
        assert std is not None, "torch.normal called without a std argument"
        self.stds.append(float(std))
        return self.real.normal(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self.real, name)


def _batch(size: int) -> dict[str, Any]:
    input_ids = torch.randint(1, VOCAB, (size, 8), dtype=torch.long)
    return {
        "input_ids": input_ids,
        "labels": torch.randint(1, VOCAB, (size, 8), dtype=torch.long),
    }


def _dp_cfg(**overrides: Any) -> TrainConfig:
    base: dict[str, Any] = dict(
        num_epochs=1, batch_size=4, learning_rate=1e-2, warmup_steps=0,
        dp_enabled=True, dp_noise_multiplier=0.9, dp_max_grad_norm=1.5,
        memorization_threshold=0.9, use_lora=False,
    )
    base.update(overrides)
    return TrainConfig(**base)


def _dataset(n: int = 8) -> list[dict[str, Any]]:
    return [
        {
            "input_ids": torch.arange(i + 1, i + 7, dtype=torch.long),
            "labels": torch.arange(i + 2, i + 8, dtype=torch.long) % VOCAB + 1,
        }
        for i in range(n)
    ]


@pytest.fixture
def trainer():
    tr = CPUTrainer()
    if not tr.available:
        pytest.skip("torch unavailable — real training path cannot run")
    return tr


@pytest.fixture
def primed_model():
    model = TinyLM()
    loss = model(input_ids=torch.randint(1, VOCAB, (4, 8)),
                 labels=torch.randint(1, VOCAB, (4, 8))).loss
    loss.backward()
    return model


def test_dp_noise_std_is_sigma_c_over_batch_size(trainer, monkeypatch, primed_model):
    """核心红：噪声尺度必须是 σC/B，而不是 σC。"""
    probe = TorchProbe(ct._torch)
    monkeypatch.setattr(ct, "_torch", probe)
    model = primed_model
    cfg = _dp_cfg()
    optimizer = torch.optim.AdamW(model.parameters(), lr=0.0)

    trainer.train_step(_batch(4), model, optimizer, cfg)

    assert probe.stds, "DP 已启用却完全没有调用 torch.normal —— 噪声根本没注入"
    expected = cfg.dp_noise_multiplier * cfg.dp_max_grad_norm / 4
    assert probe.stds == pytest.approx([expected] * len(probe.stds)), (
        f"DP-SGD 噪声尺度应为 σC/B={expected}，实际 std={probe.stds}"
        f"（σC={cfg.dp_noise_multiplier * cfg.dp_max_grad_norm} ⇒ 比声明的 (ε,δ) "
        f"大一个 batch_size 倍）"
    )


def test_dp_noise_std_scales_inversely_with_batch_size(trainer, monkeypatch, primed_model):
    """同一份梯度：B=4 的噪声必须是 B=1 的四分之一。"""
    model = primed_model
    cfg = _dp_cfg()

    probe1 = TorchProbe(ct._torch)
    monkeypatch.setattr(ct, "_torch", probe1)
    optimizer = torch.optim.AdamW(model.parameters(), lr=0.0)
    trainer.train_step(_batch(1), model, optimizer, cfg)
    probe4 = TorchProbe(ct._torch)
    monkeypatch.setattr(ct, "_torch", probe4)
    trainer.train_step(_batch(4), model, optimizer, cfg)

    assert probe1.stds and probe4.stds
    assert probe4.stds[0] == pytest.approx(probe1.stds[0] / 4), (
        f"噪声与 batch_size 不成反比：B=1 → {probe1.stds[0]}, B=4 → {probe4.stds[0]}"
    )


def test_reported_dp_and_gradient_fields_come_from_the_actual_step(
    trainer, monkeypatch
):
    """dp_noise_injected / gradient_norm 必须由实际步决定，不是配置回声与硬编码 0。"""
    def _no_injection(self, model, config, batch_size: int = 1) -> tuple[float, bool]:
        return 0.5, False

    monkeypatch.setattr(CPUTrainer, "_clip_and_noise", _no_injection)

    result = trainer.train(TinyLM(), None, _dataset(), _dp_cfg(num_epochs=1, batch_size=2))

    assert result.error is None, result.error
    assert result.dp_noise_injected is False, (
        "实际一步噪声都没注入，结果却按配置回报 dp_noise_injected=True"
    )
    assert result.gradient_norm == pytest.approx(0.5), (
        f"gradient_norm 未回传最后一次 total_norm，实际为 {result.gradient_norm}"
    )


def test_completed_dp_run_reports_nonzero_gradient_norm(trainer):
    """端到端：真跑完的 DP 训练必须报出非零梯幅（不得恒 0）。"""
    result = trainer.train(TinyLM(), None, _dataset(), _dp_cfg(batch_size=2))

    assert result.error is None, result.error
    assert result.epochs_completed == 1
    assert result.gradient_norm > 0.0, "gradient_norm 恒为 0.0（硬编码）"
    assert result.dp_noise_injected is True


def test_disabled_dp_never_claims_injection(trainer):
    """未开 DP 时不得声称注入了噪声。"""
    result = trainer.train(TinyLM(), None, _dataset(),
                           _dp_cfg(dp_enabled=False, batch_size=2))

    assert result.error is None, result.error
    assert result.dp_noise_injected is False
    assert result.gradient_norm > 0.0
