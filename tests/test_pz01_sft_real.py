"""PZ-01 (MR-A2): real SFT training wiring + fail-closed model resolution.

Covers the spec §3.1 PZ-01 option (a) contract:
(i)   torch + resolvable model -> the REAL training path executes
      (real forward/backward, weights actually change);
(ii)  model unresolvable (simulation hatch off) -> fail-closed raise,
      no result object, no simulation;
(iii) CDS_TRAINING_ALLOW_SIMULATION=true -> simulated run is labeled
      is_simulation=True, and its checkpoint is a simulation_manifest
      (parameters + loss formula, NO forged state_dict);
(iv)  real run -> checkpoint is the genuine trained state_dict (.pt);
(v)   TRAINING_REQUIRE_TORCH=true with torch unavailable -> the
      simulation path itself is blocked (fail-closed);
(vi)  M-04: CDS_MIA_SHADOW_MODEL off -> honest "proxy_estimate";
      on -> real shadow-model probe, mia_status="shadow_model".

The real model is a tiny randomly-initialized local model satisfying the
cpu_trainer contract (forward(input_ids, labels) -> .loss); the resolver
is monkeypatched so no network/HF download is ever attempted, but the
forward/backward math in cpu_trainer.train runs for real.
"""
import json
from pathlib import Path

import pytest
import torch

from app.core.config import get_settings
from app.services import llm_sft_runtime
from app.services.llm_sft_runtime import (
    LLMSFTRuntime,
    SFTConfig,
    SFTModelResolutionError,
    SFTResult,
    TrainingPhase,
)

torch = pytest.importorskip("torch")
import torch.nn as nn  # noqa: E402
import torch.nn.functional as F  # noqa: E402

VOCAB = 64
HIDDEN = 32


class TinyLMOutput:
    def __init__(self, loss=None, logits=None):
        self.loss = loss
        self.logits = logits


class TinyLM(nn.Module):
    """Tiny randomly-initialized causal LM satisfying the trainer contract."""

    def __init__(self):
        super().__init__()
        self.embedding = nn.Embedding(VOCAB, HIDDEN)
        self.encoder = nn.Sequential(
            nn.Linear(HIDDEN, HIDDEN),
            nn.GELU(),
            nn.Linear(HIDDEN, HIDDEN),
        )
        self.head = nn.Linear(HIDDEN, VOCAB)

    def forward(self, input_ids=None, labels=None, **kwargs):
        h = self.encoder(self.embedding(input_ids))
        logits = self.head(h)
        loss = None
        if labels is not None:
            loss = F.cross_entropy(
                logits.reshape(-1, logits.size(-1)), labels.reshape(-1)
            )
        return TinyLMOutput(loss=loss, logits=logits)


class TinyTokenizer:
    """Deterministic hash tokenizer satisfying the trainer contract.

    Handles the exact call shapes cpu_trainer uses:
      - batch:  tokenizer(texts, return_tensors="pt", truncation=True,
                         max_length=512, padding="max_length")
      - single: tokenizer(text,  return_tensors="pt", truncation=True,
                         max_length=256, padding=True)
    """

    model_input_names = ["input_ids"]
    pad_token_id = 0

    def __call__(
        self,
        texts,
        return_tensors="pt",
        truncation: bool = False,
        max_length: int | None = None,
        padding=False,
        **kwargs,
    ):
        if isinstance(texts, str):
            texts = [texts]
        seqs = []
        for text in texts:
            seq = [
                (hash((text, i)) % (VOCAB - 1)) + 1
                for i in range(min(max(len(text or "x"), 1), 64))
            ]
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


# ── fixtures ────────────────────────────────────────────────────


@pytest.fixture
def tiny_resolver(monkeypatch):
    """Patch the module-level resolver with a tiny local model."""
    loaded: list[nn.Module] = []

    def fake_resolve(name: str):
        model = TinyLM()
        loaded.append(model)
        return model, TinyTokenizer()

    monkeypatch.setattr(llm_sft_runtime, "resolve_sft_base_model", fake_resolve)
    return loaded


@pytest.fixture
def no_simulation(monkeypatch):
    """Force the prod default: simulation hatch off (fail-closed)."""
    monkeypatch.setattr(llm_sft_runtime, "_training_allow_simulation", lambda: False)
    monkeypatch.delenv("CDS_MODEL_REGISTRY_DIR", raising=False)


@pytest.fixture
def dataset_file(tmp_path):
    p = tmp_path / "train.jsonl"
    lines = [
        json.dumps(
            {"text": f"客户{i}的订单金额为{100 + i}元，状态为已发货。"},
            ensure_ascii=False,
        )
        for i in range(24)
    ]
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


def _config(**kwargs) -> SFTConfig:
    # max_memorization_score=1.0 keeps the random tiny-model memorization
    # score from tripping the gate; the gate logic is not under test here.
    base = dict(
        base_model="Qwen2.5-7B",
        num_epochs=2,
        batch_size=4,
        max_memorization_score=1.0,
    )
    base.update(kwargs)
    return SFTConfig(**base)


# ── (i) real training path ─────────────────────────────────────


def test_real_training_executes(monkeypatch, dataset_file, no_simulation):
    model = TinyLM()
    before = {k: v.clone() for k, v in model.state_dict().items()}
    monkeypatch.setattr(
        llm_sft_runtime, "resolve_sft_base_model", lambda name: (model, TinyTokenizer())
    )
    result = LLMSFTRuntime().run_sft(
        "sess-pz01-real", _config(), [str(dataset_file)]
    )
    assert result.phase == TrainingPhase.COMPLETED
    assert result.is_simulation is False
    assert result.final_loss > 0
    assert result.model_state is not None and len(result.model_state) > 0
    assert len(result.metrics) >= 2
    assert all(m.loss > 0 for m in result.metrics)

    after = model.state_dict()
    changed = any(not torch.equal(before[k], after[k]) for k in before)
    assert changed, "real forward/backward must actually update the weights"


def test_real_training_result_carrying(tiny_resolver, dataset_file, no_simulation):
    result = LLMSFTRuntime().run_sft("sess-pz01-carry", _config(), [str(dataset_file)])
    assert result.is_simulation is False
    assert result.mia_status == "proxy_estimate"
    assert result.mia_probe is None
    assert set(result.model_state.keys()) == set(
        TinyLM().state_dict().keys()
    )


# ── (ii) fail-closed on unresolvable model ─────────────────────


def test_unresolvable_model_fails_closed(dataset_file, no_simulation):
    runtime = LLMSFTRuntime()
    with pytest.raises(SFTModelResolutionError) as ei:
        runtime.run_sft("sess-pz01-miss", _config(num_epochs=1), [str(dataset_file)])
    msg = str(ei.value)
    assert "unresolvable" in msg
    assert "CDS_MODEL_REGISTRY_DIR" in msg


# ── (iii) explicit simulation hatch is honest ──────────────────


def test_simulation_allowed_is_labeled(dataset_file, monkeypatch):
    monkeypatch.setattr(llm_sft_runtime, "_training_allow_simulation", lambda: True)
    result = LLMSFTRuntime().run_sft(
        "sess-pz01-sim", _config(num_epochs=2), [str(dataset_file)]
    )
    assert result.phase == TrainingPhase.COMPLETED
    assert result.is_simulation is True
    assert result.model_state is None
    assert result.mia_status == "proxy_estimate"


# ── (iv) checkpoint honesty (M-10) ─────────────────────────────


def test_checkpoint_real_run_is_state_dict(tmp_path, monkeypatch):
    from app.api import training as training_api

    monkeypatch.setattr(training_api, "_TRAINING_OUTPUT_ROOT", tmp_path)
    result = SFTResult(
        session_id="s-real",
        model_path="/sandbox/output/model/s-real",
        phase=TrainingPhase.COMPLETED,
        total_epochs=2,
        final_loss=0.25,
        memorization_score=0.05,
        watermark_injected=True,
        is_simulation=False,
        model_state={"w": torch.tensor([1.0, 2.0, 3.0])},
    )
    config = SFTConfig(base_model="Qwen2.5-7B", num_epochs=2)
    path = training_api._write_sft_checkpoint("job-pz01-real", result, config)
    assert path.endswith(".pt")
    assert Path(path).exists()
    loaded = torch.load(path, weights_only=True)
    assert torch.equal(loaded["w"], torch.tensor([1.0, 2.0, 3.0]))


def test_checkpoint_simulation_is_manifest(tmp_path, monkeypatch):
    from app.api import training as training_api

    monkeypatch.setattr(training_api, "_TRAINING_OUTPUT_ROOT", tmp_path)
    result = SFTResult(
        session_id="s-sim",
        model_path="",
        phase=TrainingPhase.COMPLETED,
        total_epochs=2,
        final_loss=0.9,
        memorization_score=0.05,
        watermark_injected=True,
        is_simulation=True,
        mia_status="proxy_estimate",
    )
    config = SFTConfig(base_model="Qwen2.5-7B", num_epochs=2)
    path = training_api._write_sft_checkpoint("job-pz01-sim", result, config)
    assert path.endswith(".json")
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    assert payload["format"] == "cds-sft-simulation-manifest-v1"
    assert payload["is_simulation"] is True
    assert "state_dict" not in payload, "simulated runs must not forge weights"
    assert payload["parameters"]["epochs"] == 2
    assert "loss_model" in payload
    assert payload["metrics"]["final_loss"] == 0.9


# ── (v) TRAINING_REQUIRE_TORCH blocks the simulation itself ────


def test_require_torch_blocks_simulation(dataset_file, monkeypatch):
    monkeypatch.setattr(get_settings(), "TRAINING_REQUIRE_TORCH", True)
    monkeypatch.setattr(llm_sft_runtime, "_training_allow_simulation", lambda: True)
    monkeypatch.setattr(llm_sft_runtime.cpu_trainer, "_torch_available", False)
    with pytest.raises(SFTModelResolutionError):
        LLMSFTRuntime().run_sft(
            "sess-pz01-torch", _config(num_epochs=1), [str(dataset_file)]
        )


# ── (vi) M-04: shadow-model MIA gating ─────────────────────────


def test_mia_shadow_off_keeps_proxy_estimate(tiny_resolver, dataset_file, monkeypatch):
    monkeypatch.setattr(llm_sft_runtime, "_mia_shadow_model_enabled", lambda: False)
    result = LLMSFTRuntime().run_sft(
        "sess-pz01-mia-off", _config(), [str(dataset_file)]
    )
    assert result.mia_status == "proxy_estimate"
    assert result.mia_probe is None


def test_mia_shadow_on_runs_real_probe(tiny_resolver, dataset_file, monkeypatch):
    monkeypatch.setattr(llm_sft_runtime, "_mia_shadow_model_enabled", lambda: True)
    result = LLMSFTRuntime().run_sft(
        "sess-pz01-mia-on", _config(), [str(dataset_file)]
    )
    assert result.phase == TrainingPhase.COMPLETED
    assert result.mia_status == "shadow_model"
    assert result.mia_probe is not None
    assert result.mia_probe["method"] == "shadow_model"
    assert 0.0 <= result.mia_probe["score"] <= 1.0
    assert result.mia_probe["member_loss"] > 0
    assert result.mia_probe["num_shadow_train_samples"] > 0
    # Main model + shadow model both resolved from the (patched) source.
    assert len(tiny_resolver) == 2
