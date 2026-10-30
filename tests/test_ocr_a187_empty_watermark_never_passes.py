"""A187 — 空位集 / 重复层名 / 零长度水印被当作「验证通过」（medium-high）.

Ledger fix requirement: 「入口拒绝空 ``layer_names``/``num_bits<=0``/重复层名，
``verify`` 对空位集直接返回 ``passed=False``」. An empty schedule used to produce
``passed=True match_ratio=1.000`` out of nothing, and duplicate / aliased layer
names silently overwrote each other's bits while still reporting "embedded".
"""
import numpy as np
import pytest

from app.services.model_watermark import ModelWatermarkService, WatermarkMetadata

svc = ModelWatermarkService()

BITS = [1, 0, 1, 1]


def _state_dict() -> dict:
    rng = np.random.default_rng(11)
    return {
        "a.weight": (rng.random(256) * 0.5).astype(np.float64),
        "b.weight": (rng.random(256) * 0.5).astype(np.float64),
    }


def test_embed_rejects_empty_watermark_bits():
    with pytest.raises(ValueError, match="must not be empty"):
        svc.embed_watermark(_state_dict(), [], ["a.weight"], 1)


def test_embed_rejects_empty_layer_names():
    with pytest.raises(ValueError, match="layer_names must not be empty"):
        svc.embed_watermark(_state_dict(), BITS, [], 1)


def test_embed_rejects_duplicate_layer_names():
    """Same name twice = the same weights twice: bits overwrite each other."""
    with pytest.raises(ValueError, match="duplicate layer names"):
        svc.embed_watermark(_state_dict(), BITS, ["a.weight", "a.weight"], 1)


def test_embed_rejects_aliased_layer_names():
    """Two names pointing at ONE tensor object is the same overwrite defect."""
    shared = (np.random.default_rng(3).random(256) * 0.5).astype(np.float64)
    state = {"a.weight": shared, "b.weight": shared}
    with pytest.raises(ValueError, match="alias"):
        svc.embed_watermark(state, BITS, ["a.weight", "b.weight"], 1)


def test_generate_bits_rejects_non_positive_count():
    for bad in (0, -8):
        with pytest.raises(ValueError, match="num_bits must be positive"):
            svc.generate_watermark_bits("owner", "model", bad)


def test_verify_empty_position_set_is_a_failed_verdict():
    """「空值报成通过」 is what the honest-degradation rule forbids."""
    metadata = WatermarkMetadata(layer_positions={}, key_seed=1, num_bits=len(BITS))
    result = svc.verify_watermark(_state_dict(), BITS, metadata)
    assert result.passed is False
    assert result.match_ratio == 0.0
    assert str(result.reason), "an empty position set must say why nothing was checked"


def test_verify_empty_watermark_is_not_a_pass():
    """The finding's exact repro: verify_watermark(state, [], md_empty).

    Before the fix this returned ``passed=True match_ratio=1.000``; there must
    be no green verdict for a watermark with no bits — the shipped contract is
    a refusal, so the assert is "raises", never a returned pass.
    """
    metadata = WatermarkMetadata(layer_positions={}, key_seed=1, num_bits=0)
    with pytest.raises(ValueError, match="must not be empty"):
        svc.verify_watermark(_state_dict(), [], metadata)


def test_real_watermark_still_verifies_after_the_gates():
    """The guards must not break the honest path."""
    state = _state_dict()
    result = svc.embed_watermark(state, BITS, ["a.weight", "b.weight"], 2024)
    verdict = svc.verify_watermark(state, BITS, result.metadata)
    assert verdict.passed is True
    assert verdict.match_ratio == 1.0
