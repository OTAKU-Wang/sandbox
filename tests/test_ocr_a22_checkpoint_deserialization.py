"""A22 (ocr-verified-findings §A22): training.py 检查点反序列化缺防护。

`_load_raw_checkpoint` 的三条装载腿在 API 进程内做任意代码执行级反序列化：
- `torch.load(...)` 未指定 `weights_only=True`（requirements 只钉 torch>=2.0.0，
  <2.6 默认 weights_only=False，不能依赖新版默认值保护）；
- `np.load(path, allow_pickle=True)`；
- 裸 `pickle.load(fh)`。
危害边界按台账：output_path 由服务端写入，前提是训练输出目录被共享/可写时，
一次「查看检查点」即可在 API 进程内执行任意代码（纵深防御缺口）。

判别：用 `__reduce__ -> os.makedirs` 的可执行 pickle 载荷写进各扩展名文件，
直调 `_load_raw_checkpoint`（API 请求腿最终就到这条原语）：修复前应观察到
哨兵目录被创建（=RCE 坐实）+ 不拒绝；修复后必须拒绝（HTTPException 400）
且哨兵不存在。纯数据 pickle / 普通 npy 必须仍可加载（不许顺手砍功能）。
"""
import io
import os
import pickle
import uuid
from pathlib import Path

import pytest

from app.api.training import _load_raw_checkpoint


class _Evil:
    def __init__(self, path: str = "") -> None:
        self.path = path

    def __reduce__(self):
        return (os.makedirs, (self.path,))


def _evil_pickle(sentinel: Path) -> bytes:
    return pickle.dumps(_Evil(str(sentinel)))


def _sentinel(tmp_path: Path) -> Path:
    return tmp_path / f"RCE-{uuid.uuid4().hex[:8]}"


class TestA22CheckpointDeserialization:
    def test_plain_pickle_leg_does_not_execute_payload(self, tmp_path):
        sentinel = _sentinel(tmp_path)
        path = tmp_path / "checkpoint.pkl"
        path.write_bytes(_evil_pickle(sentinel))

        with pytest.raises(Exception) as excinfo:
            _load_raw_checkpoint(path)
        assert not sentinel.exists(), (
            "pickle 腿执行了载荷里的 os.makedirs —— 检查点反序列化=RCE"
        )
        assert isinstance(excinfo.value, Exception)

    def test_torch_extension_fallback_does_not_execute_payload(self, tmp_path):
        """.pt 的 pickle 回退腿（loaders=[try_torch, try_pickle]）同样不得执行载荷。"""
        sentinel = _sentinel(tmp_path)
        path = tmp_path / "model.pt"
        path.write_bytes(_evil_pickle(sentinel))

        with pytest.raises(Exception) as excinfo:
            _load_raw_checkpoint(path)
        assert not sentinel.exists(), (
            ".pt 回退到裸 pickle.load 后执行了载荷 —— torch 腿加 weights_only "
            "并不能挡住回退腿"
        )

    def test_numpy_leg_does_not_execute_payload(self, tmp_path):
        np = pytest.importorskip("numpy")
        sentinel = _sentinel(tmp_path)
        path = tmp_path / "weights.npy"
        buf = io.BytesIO()
        np.save(buf, np.array(_Evil(str(sentinel)), dtype=object), allow_pickle=True)
        path.write_bytes(buf.getvalue())

        with pytest.raises(Exception) as excinfo:
            _load_raw_checkpoint(path)
        assert not sentinel.exists(), (
            "np.load(allow_pickle=True) 执行了载荷里的 os.makedirs"
        )

    def test_torch_load_pins_weights_only(self, monkeypatch, tmp_path):
        """torch 腿必须显式钉 weights_only=True（<2.6 默认 False，台账附注）。"""
        torch = pytest.importorskip("torch")
        calls: list[dict[str, object]] = []

        def _recording_load(p, **kwargs):
            calls.append(kwargs)
            return {"state_dict": {"w": 1.0}}

        monkeypatch.setattr(torch, "load", _recording_load)
        path = tmp_path / "model.pt"
        path.write_bytes(b"not-a-real-checkpoint")

        raw, fmt = _load_raw_checkpoint(path)
        assert fmt == "torch"
        assert calls, "try_torch 未调用 torch.load"
        assert calls[0].get("weights_only") is True, (
            f"torch.load 未钉 weights_only=True，实际 kwargs={calls[0]!r}"
        )

    def test_plain_data_checkpoints_still_load(self, tmp_path):
        """收紧只挡可执行载荷：纯数据 pickle / 普通 npy 必须照常可加载。"""
        plain = tmp_path / "state.pkl"
        plain.write_bytes(pickle.dumps({"state_dict": {"layer.weight": [0.1, 0.2]}}))
        raw, fmt = _load_raw_checkpoint(plain)
        assert fmt == "pickle"
        assert raw == {"state_dict": {"layer.weight": [0.1, 0.2]}}

        np = pytest.importorskip("numpy")
        arr = tmp_path / "weights.npy"
        buf = io.BytesIO()
        np.save(buf, np.array([[1.0, 2.0], [3.0, 4.0]]))
        arr.write_bytes(buf.getvalue())
        raw2, fmt2 = _load_raw_checkpoint(arr)
        assert fmt2 == "npy"
        assert raw2["array"].tolist() == [[1.0, 2.0], [3.0, 4.0]]
