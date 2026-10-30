"""A101 — LoRA 在优化器之后才注入 ⇒ 训练是静默 no-op（high，活路径）。

`app/services/cpu_trainer.py` 原本先 `_create_optimizer(model)` 再 `_apply_lora(model)`，
而 `get_peft_model` 会返回新的包装体、新增 adapter 参数并把基座冻结
（`requires_grad=False`）：优化器手里是注入前的参数快照，LoRA 的 A/B 从未进入
优化器 ⇒ `optimizer.step()` 什么都不改，`train()` 仍返回 epochs_completed /
loss_history / memorization_score。

torch 用真 torch（本机 2.12 cpu），peft 未安装 ⇒ 用 PEFT 公开语义的测试替身
（新包装体 + 冻结基座 + 新增可训练 adapter 参数 + loss 依赖 adapter），
判据是「adapter 参数的数值是否真的被移动过」，不是计时也不是文案。
"""
import sys
import types
from typing import Any

import pytest

torch = pytest.importorskip("torch")
import torch.nn as nn  # noqa: E402
import torch.nn.functional as F  # noqa: E402

from app.services import cpu_trainer as ct  # noqa: E402
from app.services.cpu_trainer import CPUTrainer, TrainConfig  # noqa: E402

VOCAB = 32
HIDDEN = 16
RANK = 4


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
        h = self.embedding(input_ids)
        logits = self.head(h)
        loss = None
        if labels is not None:
            loss = F.cross_entropy(
                logits.reshape(-1, logits.size(-1)), labels.reshape(-1)
            )
        return Out(loss=loss, logits=logits)


class TinyTokenizer:
    pad_token_id = 0

    def __call__(self, texts, return_tensors="pt", truncation=False,
                 max_length=None, padding=False, **kwargs):
        single = isinstance(texts, str)
        items = [texts] if single else list(texts)
        seqs = []
        for text in items:
            seq = [(hash((text, i)) % (VOCAB - 1)) + 1
                   for i in range(min(max(len(text or "x"), 2), 16))]
            if truncation and max_length:
                seq = seq[:max_length]
            seqs.append(seq)
        pad_to = 0
        if padding == "max_length" and max_length:
            pad_to = max_length
        elif padding:
            pad_to = max((len(s) for s in seqs), default=0)
        if pad_to:
            seqs = [s + [self.pad_token_id] * (pad_to - len(s)) for s in seqs]
        if return_tensors == "pt":
            return {"input_ids": torch.tensor(seqs, dtype=torch.long)}
        return {"input_ids": seqs}


class PeftWrapper(nn.Module):
    """get_peft_model 的语义替身：新包装体 + 冻结基座 + 新增可训练 adapter。"""

    def __init__(self, base: TinyLM, rank: int):
        super().__init__()
        for param in base.parameters():
            param.requires_grad_(False)
        self.base = base
        self.lora_a = nn.Parameter(torch.randn(HIDDEN, rank) * 0.05)
        self.lora_b = nn.Parameter(torch.randn(rank, VOCAB) * 0.01)
        self._init_snapshot = {
            name: param.detach().clone() for name, param in self.named_parameters()
        }

    def adapter_movement(self) -> float:
        """adapter 参数自创建以来被优化器移动的量（静默 no-op 就是 0）。"""
        return sum(
            (param - self._init_snapshot[name]).abs().sum().item()
            for name, param in self.named_parameters()
            if name.startswith("lora_")
        )

    def base_movement(self) -> float:
        """被 PEFT 冻结的基座应当一动不动。"""
        return sum(
            (param - self._init_snapshot[name]).abs().sum().item()
            for name, param in self.named_parameters()
            if name.startswith("base.")
        )

    def forward(self, input_ids=None, labels=None, **kwargs):
        h = self.base.embedding(input_ids)
        adapter = (h @ self.lora_a.to(h.dtype)) @ self.lora_b.to(h.dtype)
        logits = self.base.head(h) + adapter
        loss = None
        if labels is not None:
            loss = F.cross_entropy(
                logits.reshape(-1, logits.size(-1)), labels.reshape(-1)
            )
        return Out(loss=loss, logits=logits)


def _install_fake_peft(monkeypatch, made: list["PeftWrapper"]):
    module = types.ModuleType("peft")

    class LoraConfig:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class TaskType:
        CAUSAL_LM = "CAUSAL_LM"

    def get_peft_model(model, config):
        wrapper = PeftWrapper(model, getattr(config, "r", RANK))
        made.append(wrapper)
        return wrapper

    setattr(module, "LoraConfig", LoraConfig)
    setattr(module, "TaskType", TaskType)
    setattr(module, "get_peft_model", get_peft_model)
    monkeypatch.setitem(sys.modules, "peft", module)
    return module


class OptimShim:
    """记录送进优化器的参数列表（身份，不是数量）。"""

    def __init__(self, real):
        self.real = real
        self.captured: list[list[nn.Parameter]] = []

    def AdamW(self, params, **kwargs):
        snapshot = list(params)
        self.captured.append(snapshot)
        return self.real.AdamW(snapshot, **kwargs)

    def __getattr__(self, name):
        return getattr(self.real, name)


def _dataset(n=6):
    return [{"text": f"客户{i}的订单金额为{100 + i}元，状态为已发货。"} for i in range(n)]


def _cfg(**overrides: Any) -> TrainConfig:
    base: dict[str, Any] = dict(
        num_epochs=2, batch_size=2, learning_rate=5e-2, warmup_steps=0,
        use_lora=True, lora_r=RANK, memorization_threshold=0.9,
    )
    base.update(overrides)
    return TrainConfig(**base)


@pytest.fixture
def trainer():
    tr = CPUTrainer()
    if not tr.available:
        pytest.skip("torch unavailable — real training path cannot run")
    return tr


def test_lora_adapter_parameters_actually_move(trainer, monkeypatch):
    """核心红：adapter 参数在训练后必须真的被更新过。"""
    made: list[PeftWrapper] = []
    _install_fake_peft(monkeypatch, made)
    model = TinyLM()

    result = trainer.train(model, TinyTokenizer(), _dataset(), _cfg())

    assert result.error is None, result.error
    assert result.epochs_completed == 2
    assert len(made) == 1
    adapter = made[0]
    delta = adapter.adapter_movement()
    assert delta > 0.0, (
        "use_lora=True 时训练是静默 no-op：LoRA adapter 参数在 train() 前后完全没变，"
        f"但结果仍报告 epochs_completed={result.epochs_completed}, "
        f"loss_history={result.loss_history}"
    )
    assert adapter.base_movement() == 0.0, "被 PEFT 冻结的基座不应被优化器更新"


def test_optimizer_receives_adapter_params_not_frozen_base(trainer, monkeypatch):
    """优化器参数表必须是注入后的可训练参数（LoRA 先于优化器）。"""
    made: list[PeftWrapper] = []
    _install_fake_peft(monkeypatch, made)
    shim = OptimShim(ct._optim)
    monkeypatch.setattr(ct, "_optim", shim)
    model = TinyLM()

    result = trainer.train(model, TinyTokenizer(), _dataset(), _cfg())

    assert result.error is None, result.error
    assert shim.captured, "优化器从未被创建"
    flat = [p for group in shim.captured for p in group]
    assert len(made) == 1
    adapter_params = set(id(p) for p in made[0].parameters() if p.requires_grad)
    assert adapter_params & {id(p) for p in flat}, (
        "优化器参数表里没有 LoRA adapter 参数：optimizer.step() 不会更新任何 LoRA 权重"
    )
    assert all(p.requires_grad for p in flat), (
        "优化器里持有已被 PEFT 冻结的基座参数（注入前快照）"
    )


def test_frozen_model_without_adapter_is_not_reported_as_trained(trainer, monkeypatch):
    """无参数可训练时不得回报「训练完成」：要么移动参数，要么带 error 说明。"""
    made: list[PeftWrapper] = []
    _install_fake_peft(monkeypatch, made)
    model = TinyLM()

    def _frozen_adapter(base_model, config):
        wrapper = PeftWrapper(base_model, getattr(config, "lora_r", RANK))
        wrapper.lora_a.requires_grad_(False)
        wrapper.lora_b.requires_grad_(False)
        made.append(wrapper)
        return wrapper

    monkeypatch.setattr(ct.CPUTrainer, "_apply_lora", staticmethod(_frozen_adapter))

    result = trainer.train(model, TinyTokenizer(), _dataset(), _cfg())

    assert result.error, "全冻结模型被当成训练成功返回（静默 no-op）"


def test_plain_training_still_moves_weights(trainer):
    """回归：use_lora=False 的普通路径照旧真的更新权重。"""
    model = TinyLM()
    before = [p.detach().clone() for p in model.parameters()]

    result = trainer.train(
        model, TinyTokenizer(), _dataset(),
        _cfg(use_lora=False),
    )

    assert result.error is None, result.error
    moved = sum(
        (p - b).abs().sum().item() for p, b in zip(model.parameters(), before)
    )
    assert moved > 0.0


def test_missing_peft_is_disclosed_and_base_still_trains(trainer, monkeypatch, caplog):
    """peft 缺失：honest degradation —— WARNING 披露 + 基座继续训练，不得静默。"""
    monkeypatch.setitem(sys.modules, "peft", None)
    model = TinyLM()
    before = [p.detach().clone() for p in model.parameters()]

    with caplog.at_level("WARNING", logger="app.services.cpu_trainer"):
        result = trainer.train(model, TinyTokenizer(), _dataset(), _cfg())

    assert result.error is None, result.error
    moved = sum(
        (p - b).abs().sum().item() for p, b in zip(model.parameters(), before)
    )
    assert moved > 0.0
    assert any("LoRA" in r.getMessage() for r in caplog.records), \
        "peft 缺失却没有任何降级披露"
