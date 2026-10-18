"""A185/A186/A187 — 模型权重水印的「写了才算写了」契约（项目铁律：禁伪造）。

钉三件事：
1. A185 写回必须真正落到入参张量上（numpy / 非连续 torch 张量 / 任何 flatten() 返回副本的
   后端），并且写后必须回读校验：写不进去就报错，绝不返回 success=True。
2. A186 选位密钥：库里的明文 key_seed 单独不足以复原位位置（服务端 pepper 参与派生），
   元数据只带不可逆指纹，选位用 SM3-CTR 派生而不是 Mersenne Twister。
3. A187 空位集 / 重复层名 / 别名层 / 零长度水印不得被当成「水印有效」。
"""
import numpy as np
import pytest

from app.services.model_watermark import ModelWatermarkService, model_watermark_service


LAYER = "layer.weight"


def _bits(n: int = 64) -> list[int]:
    return model_watermark_service.generate_watermark_bits("owner-a", "model-a", num_bits=n)


def _bit_of(value: float) -> int:
    return int(round(value * 1000)) & 1


# ---------------------------------------------------------------------------
# A185 — 写回必须落地
# ---------------------------------------------------------------------------

def test_embed_actually_writes_into_numpy_array():
    """numpy 入参：embed 报 success 就必须逐位改到数组里（旧实现只写进 flatten() 的副本）。"""
    arr = np.full((8, 8), 0.125, dtype=np.float64)
    bits = _bits()

    res = model_watermark_service.embed_watermark({LAYER: arr}, bits, [LAYER], key_seed=1234)

    assert res.success is True
    changed = 0
    for bit_idx, (_layer, flat_idx) in enumerate(res.metadata.ordered_positions):
        row, col = divmod(flat_idx, arr.shape[1])
        value = float(arr[row][col])
        assert _bit_of(value) == bits[bit_idx], f"position {flat_idx} was never written"
        changed += value != 0.125
    assert changed > 0, "no weight was modified at all, yet embed reported success"

    verify = model_watermark_service.verify_watermark({LAYER: arr}, bits, res.metadata)
    assert verify.passed is True and verify.match_ratio == 1.0


def test_embed_writes_through_fortran_ordered_array():
    """F 序数组 flatten() 与逻辑行主序不一致：写回必须按逻辑扁平下标命中真实存储。"""
    arr = np.asfortranarray(np.arange(64, dtype=np.float64).reshape(8, 8) / 1000.0 + 0.125)
    bits = _bits()

    res = model_watermark_service.embed_watermark({LAYER: arr}, bits, [LAYER], key_seed=99)

    for bit_idx, (_layer, flat_idx) in enumerate(res.metadata.ordered_positions):
        row, col = divmod(flat_idx, arr.shape[1])
        assert _bit_of(float(arr[row][col])) == bits[bit_idx]
    assert model_watermark_service.verify_watermark({LAYER: arr}, bits, res.metadata).passed is True


class _StridedTensor:
    """一个 flatten() 返回副本的张量（= 非连续 torch 张量 / 转置切片的真实形状）。"""

    def __init__(self, shape, fill=0.125):
        self.shape = tuple(shape)
        self.dtype = "float64"
        self._buf = [float(fill) for _ in range(self.shape[0] * self.shape[1])]

    def numel(self) -> int:
        return len(self._buf)

    def flatten(self):  # 与 torch.Tensor.flatten() 在非连续时同构：返回副本
        return list(self._buf)

    def __getitem__(self, key):
        if isinstance(key, tuple):
            i, j = key
            return self._buf[i * self.shape[1] + j]
        return self._buf[key]

    def __setitem__(self, key, value):
        if isinstance(key, tuple):
            i, j = key
            self._buf[i * self.shape[1] + j] = float(value)
        else:
            self._buf[key] = float(value)


class _DropOnTheFloorTensor(_StridedTensor):
    """__setitem__ 静默丢弃写入的后端：诚实实现必须报错，不能报 success。"""

    def __setitem__(self, key, value):  # noqa: D401 - 故意的 no-op
        return None


def test_embed_writes_through_tensor_whose_flatten_returns_a_copy():
    """非连续张量：旧实现 flat=tensor.flatten(); flat[idx]=v 一个位都没写进去。"""
    ten = _StridedTensor((8, 8))
    bits = _bits()

    res = model_watermark_service.embed_watermark({LAYER: ten}, bits, [LAYER], key_seed=7)

    assert ten._buf != [0.125] * 64, "backend wrote nothing but embed claimed success"
    for bit_idx, (_layer, flat_idx) in enumerate(res.metadata.ordered_positions):
        assert _bit_of(ten._buf[flat_idx]) == bits[bit_idx]


def test_embed_refuses_when_the_write_does_not_land():
    """写不进去（后端吞掉写入）⇒ 如实失败：绝不允许 success=True 落盘 + 写「已水印」审计。"""
    ten = _DropOnTheFloorTensor((8, 8))
    bits = _bits()

    with pytest.raises(ValueError, match="watermark write"):
        model_watermark_service.embed_watermark({LAYER: ten}, bits, [LAYER], key_seed=7)


def test_embed_refuses_integer_weight_tensor():
    """整数权重张量存不下 3 位小数的 LSB 方案：如实拒绝，而不是把 0.041 存成 0 还报成功。"""
    arr = np.zeros(64, dtype=np.int64)
    bits = _bits()

    with pytest.raises(ValueError, match="watermark write"):
        model_watermark_service.embed_watermark({LAYER: arr}, bits, [LAYER], key_seed=5)


def test_embed_watermark_list_backend_still_writes_and_verifies():
    """回归门：纯 list 权重（训练检查点的既有形状）仍然可用。"""
    weights = [0.125] * 64
    bits = _bits()

    res = model_watermark_service.embed_watermark({LAYER: weights}, bits, [LAYER], key_seed=1234)

    assert weights != [0.125] * 64
    assert model_watermark_service.verify_watermark({LAYER: weights}, bits, res.metadata).passed is True


def test_embed_writes_into_nested_list_at_the_right_leaf():
    """嵌套 list 的扁平下标必须命中同一个叶子（旧实现在混合层里会写错位置）。"""
    nested = [[0.125, 0.125, 0.125], [0.125, 0.125, 0.125], [0.125, 0.125, 0.125], [0.125, 0.125, 0.125]]
    bits = _bits(8)

    res = model_watermark_service.embed_watermark({LAYER: nested}, bits, [LAYER], key_seed=3)

    flat = [v for row in nested for v in row]
    assert len(flat) == 12
    for bit_idx, (_layer, flat_idx) in enumerate(res.metadata.ordered_positions):
        assert _bit_of(flat[flat_idx]) == bits[bit_idx]
    assert model_watermark_service.verify_watermark({LAYER: nested}, bits, res.metadata).passed is True


@pytest.mark.parametrize("shape", [(2, 3), (5, 7), (1, 1)])
def test_flat_index_mapping_matches_flatten_order(shape):
    """_set/_get 的扁平下标语义必须与 flatten() 的行主序一致（读写同一坐标）。"""
    svc = ModelWatermarkService()
    ten = _StridedTensor(shape)
    for idx in range(ten.numel()):
        svc._set_weight_value(ten, idx, 0.25)
        assert svc._get_weight_value(ten, idx) == pytest.approx(0.25)
        assert ten.flatten()[idx] == pytest.approx(0.25)


def test_embed_writes_into_non_contiguous_torch_tensor():
    """真 torch 的非连续视图（转置）：flatten() 返回副本，写回必须仍然命中原张量。

    本机无 torch 时跳过（条件 skip），本仓 CI/生产镜像装有 torch。
    """
    torch = pytest.importorskip("torch", reason="torch is a requirements.txt dependency; not on this host")
    base = torch.arange(64, dtype=torch.float64).reshape(8, 8) * 0.001 + 0.125
    view = base.t()  # non-contiguous: flatten() copies
    assert not view.is_contiguous()
    bits = _bits()

    res = model_watermark_service.embed_watermark({LAYER: view}, bits, [LAYER], key_seed=21)

    for bit_idx, (_layer, flat_idx) in enumerate(res.metadata.ordered_positions):
        row, col = divmod(flat_idx, view.shape[1])
        assert _bit_of(float(view[row][col])) == bits[bit_idx]
    assert model_watermark_service.verify_watermark({LAYER: view}, bits, res.metadata).passed is True


# ---------------------------------------------------------------------------
# A186 — 明文 key_seed 单独不足以复原位位置
# ---------------------------------------------------------------------------

def _positions(state: dict, bits: list[int], seed: int) -> list[tuple[str, int]]:
    return [tuple(p) for p in model_watermark_service.embed_watermark(
        state, bits, [LAYER], key_seed=seed,
    ).metadata.ordered_positions]


def test_position_selection_is_deterministic_for_one_server_secret():
    bits = _bits()
    a = _positions({LAYER: [0.125] * 64}, bits, 777)
    b = _positions({LAYER: [0.125] * 64}, bits, 777)
    assert a == b
    assert len(set(a)) == len(a)


def test_positions_are_not_reproducible_from_the_stored_key_seed_alone(monkeypatch):
    """key_seed 是明文列并且被 API 回显：位位置不得只由它派生（否则泄库=可定向擦除水印）。"""
    from app.core.config import get_settings

    bits = _bits()
    base = _positions({LAYER: [0.125] * 64}, bits, 777)

    monkeypatch.setattr(get_settings(), "JWT_SECRET_KEY", "another-server-secret")
    shifted = _positions({LAYER: [0.125] * 64}, bits, 777)

    assert base != shifted, "positions are derived from key_seed alone — a leaked DB column is a full watermark key"


def test_metadata_carries_irreversible_fingerprint_not_a_reversible_seed():
    from app.utils.crypto import sm3_hash

    bits = _bits()
    res = model_watermark_service.embed_watermark({LAYER: [0.125] * 64}, bits, [LAYER], key_seed=2026)

    fp = res.metadata.key_seed_fingerprint
    assert fp and fp != "2026" and "2026" not in fp
    assert fp == sm3_hash(b"cds-watermark-keyseed:v1|2026")[:16]
    assert res.metadata.position_kdf, "metadata must disclose how positions were derived"


def test_position_selection_does_not_use_mersenne_twister():
    """选位流必须是 SM3-CTR 派生：模块里不得再留 random.Random/random.sample。"""
    import inspect

    import app.services.model_watermark as mw

    src = inspect.getsource(mw)
    assert "random.Random" not in src and "rng.sample" not in src


# ---------------------------------------------------------------------------
# A187 — 空位集 / 重复层名 / 别名层 / 零长度水印
# ---------------------------------------------------------------------------

def test_generate_watermark_bits_rejects_non_positive_num_bits():
    for bad in (0, -8):
        with pytest.raises(ValueError):
            model_watermark_service.generate_watermark_bits("o", "m", num_bits=bad)


def test_embed_rejects_empty_watermark_bits():
    with pytest.raises(ValueError):
        model_watermark_service.embed_watermark({LAYER: [0.125] * 64}, [], [LAYER], key_seed=1)


def test_embed_rejects_empty_layer_names():
    with pytest.raises(ValueError):
        model_watermark_service.embed_watermark({LAYER: [0.125] * 64}, _bits(8), [], key_seed=1)


def test_embed_rejects_duplicate_layer_names():
    """重复层名会在同一批权重上互相覆盖，却报「嵌入了 N 位」。"""
    state = {LAYER: [0.125] * 64}
    with pytest.raises(ValueError, match="duplicate"):
        model_watermark_service.embed_watermark(state, _bits(8), [LAYER, LAYER], key_seed=1)


def test_embed_rejects_layers_aliased_to_the_same_tensor():
    state = {LAYER: [0.125] * 64}
    aliased = {"layer.weight": state[LAYER], "layer.bias": state[LAYER]}
    with pytest.raises(ValueError, match="same object|alias"):
        model_watermark_service.embed_watermark(aliased, _bits(8), ["layer.weight", "layer.bias"], key_seed=1)


def test_verify_rejects_empty_watermark_bits():
    """空位集 match_ratio=1.000 / passed=True 是「把空值报成通过」。"""
    res = model_watermark_service.embed_watermark({LAYER: [0.125] * 64}, _bits(8), [LAYER], key_seed=1)

    with pytest.raises(ValueError):
        model_watermark_service.verify_watermark({LAYER: [0.125] * 64}, [], res.metadata)


def test_verify_fails_honestly_on_empty_position_set():
    """元数据里没有一位（历史上写坏的行）⇒ passed=False + 原因，不是 ratio=1.0 的「通过」。"""
    from app.services.model_watermark import WatermarkMetadata

    md = WatermarkMetadata(layer_positions={}, key_seed=1, num_bits=0, ordered_positions=[])

    result = model_watermark_service.verify_watermark({LAYER: [0.125] * 64}, _bits(8), md)

    assert result.passed is False
    assert result.match_ratio == 0.0
    assert result.reason, "must disclose why nothing could be checked"


def test_verify_reports_mismatch_as_failure():
    weights = [0.125] * 64
    bits = _bits()
    res = model_watermark_service.embed_watermark({LAYER: weights}, bits, [LAYER], key_seed=11)
    flipped = [b ^ 1 for b in bits]

    result = model_watermark_service.verify_watermark({LAYER: weights}, flipped, res.metadata)

    assert result.passed is False and result.match_ratio == pytest.approx(0.0)
