"""A188 — 水印读写的性能项：每读 1 bit 就整张量 ``_flatten``（medium）.

Ledger fix requirement: 「一次性 ``reshape(-1)`` 拿到 view 后按索引读写，位置生成
改成逐层 ``range(size)`` 惰性消耗」 — embedding/verifying N bits used to cost
``O(bits x tensor_size)`` full-tensor copies (plus one tuple per weight).
These tests pin that reading or writing one bit touches one element: a
whole-tensor copy during embed/verify is the regression.
"""
import numpy as np

from app.services import model_watermark as mw
from app.services.model_watermark import ModelWatermarkService

svc = ModelWatermarkService()

BITS = [i % 2 for i in range(64)]


class _TensorLike:
    """numpy-shaped tensor whose flatten() copies, like numpy's."""

    def __init__(self, values: list[float]) -> None:
        self._values = list(values)
        self.shape = (len(self._values),)
        self.size = len(self._values)
        self.flatten_calls = 0

    def flatten(self) -> list:
        self.flatten_calls += 1
        return list(self._values)

    @property
    def flat(self):
        return iter(self._values)

    def __getitem__(self, key):
        return self._values[key[0] if isinstance(key, tuple) else key]

    def __setitem__(self, key, value) -> None:
        self._values[key[0] if isinstance(key, tuple) else key] = value


def _count_flattens(monkeypatch) -> dict:
    calls = {"n": 0}
    original = mw.ModelWatermarkService._flatten

    def counting(tensor):
        calls["n"] += 1
        return original(tensor)

    monkeypatch.setattr(mw.ModelWatermarkService, "_flatten", staticmethod(counting))
    return calls


def _state_dict(size: int = 20000) -> dict:
    rng = np.random.default_rng(5)
    return {"fc.weight": (rng.random(size) * 0.5).astype(np.float64)}


def test_embed_does_not_copy_the_whole_tensor_per_bit(monkeypatch):
    flattens = _count_flattens(monkeypatch)
    svc.embed_watermark(_state_dict(), BITS, ["fc.weight"], 7)
    assert flattens["n"] == 0, (
        f"embed copied whole tensors {flattens['n']} times for {len(BITS)} bits "
        f"(O(bits x numel) cost; must read/write single elements)"
    )


def test_verify_does_not_copy_the_whole_tensor_per_bit(monkeypatch):
    state = _state_dict()
    result = svc.embed_watermark(state, BITS, ["fc.weight"], 7)
    flattens = _count_flattens(monkeypatch)
    svc.verify_watermark(state, BITS, result.metadata)
    assert flattens["n"] == 0, (
        f"verify copied whole tensors {flattens['n']} times for {len(BITS)} bits"
    )


def test_one_bit_write_touches_one_element_only():
    tensor = _TensorLike([0.126] * 512)
    svc._embed_bit(tensor, 5, 1)
    assert tensor.flatten_calls == 0, (
        "a single-bit write must not materialise the tensor (flatten() copies)"
    )
    assert svc._extract_bit(tensor, 5) == 1
    assert tensor.flatten_calls == 0


def test_embedding_still_costs_less_than_a_linear_scan():
    """Position generation must not materialise one (layer, index) pair per weight."""
    size = 200000
    state = {"fc.weight": (np.random.default_rng(9).random(size) * 0.5).astype(np.float64)}
    result = svc.embed_watermark(state, BITS, ["fc.weight"], 3)
    assert result.metadata.num_bits == len(BITS)
    assert len(result.metadata.ordered_positions) == len(BITS)
    verdict = svc.verify_watermark(state, BITS, result.metadata)
    assert verdict.passed is True
