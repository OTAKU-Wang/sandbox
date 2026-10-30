"""A77 — 宿主侧唯一反序列化 sink 缺路径约束：检查点路径可解析到训练输出根目录之外（medium，潜在）。

`_resolve_checkpoint_path` 直接 `Path(job.output_path).expanduser()` 后就把文件交给
`torch.load` / `np.load` / pickle 腿并回写。`output_path` 今天只被服务端写，所以定为潜在，
但它是全仓唯一在 API 进程内反序列化磁盘文件的地方：任何「以后让调用方传路径」的改动，
或同机邻居往输出目录放文件，都会把一次水印/校验变成宿主代码执行。
修法：HTTP 面（水印 embed/verify）解析后必须落在 `_TRAINING_OUTPUT_ROOT` 内（按
`resolve()` 后的真实路径判包含，同时堵符号链接逃逸）。
"""
import ast
import inspect
import json

import pytest

from app.api import training
from app.models.training_job import TrainingJob, TrainingJobStatus, TrainingJobType

_load_api_checkpoint = getattr(training, "_load_api_checkpoint", training._load_model_checkpoint)
_load_model_checkpoint = training._load_model_checkpoint
_load_model_state_dict = training._load_model_state_dict

STATE = {"layer.weight": [0.125] * 32}


def _job(path) -> TrainingJob:
    return TrainingJob(
        job_id="a77-checkpoint-job",
        job_type=TrainingJobType.LLM_SFT.value,
        status=TrainingJobStatus.COMPLETED.value,
        output_path=str(path),
    )


def _write_checkpoint(path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"epoch": 1, "state_dict": STATE}), encoding="utf-8")


@pytest.fixture
def output_root(tmp_path, monkeypatch):
    root = tmp_path / "outputs"
    monkeypatch.setattr(training, "_TRAINING_OUTPUT_ROOT", root)
    return root


def test_api_loader_rejects_checkpoint_outside_output_root(tmp_path, output_root):
    outside = tmp_path / "neighbor_writes_here" / "model.json"
    _write_checkpoint(outside)

    with pytest.raises(training.HTTPException) as exc_info:
        _load_api_checkpoint(_job(outside))

    assert exc_info.value.status_code == 400


def test_api_loader_rejects_symlink_escape(tmp_path, output_root):
    outside = tmp_path / "neighbor_writes_here" / "model.json"
    _write_checkpoint(outside)
    link = output_root / "job-1" / "model.json"
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(outside)

    with pytest.raises(training.HTTPException) as exc_info:
        _load_api_checkpoint(_job(link))

    assert exc_info.value.status_code == 400


def test_api_loader_accepts_checkpoint_inside_output_root(output_root):
    inside = output_root / "job-1" / "model.json"
    _write_checkpoint(inside)

    checkpoint = _load_api_checkpoint(_job(inside))

    assert checkpoint.state_dict == STATE
    assert output_root in checkpoint.path.resolve().parents


def test_in_process_helper_contract_unchanged(tmp_path, output_root):
    outside = tmp_path / "fixtures" / "model.json"
    _write_checkpoint(outside)

    assert _load_model_state_dict(_job(outside)) == STATE
    assert _load_model_checkpoint(_job(outside)).state_dict == STATE


def test_http_handlers_must_use_the_confined_loader():
    """路由处理器不得再走未约束的检查点入口（防止以后新增 handler 绕过 A77）。"""
    tree = ast.parse(inspect.getsource(training))
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not any(ast.unparse(d).startswith("router.") for d in node.decorator_list):
            continue
        for call in ast.walk(node):
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Name):
                if call.func.id in {"_load_model_checkpoint", "_load_model_state_dict"}:
                    offenders.append(f"{node.name}: {call.func.id}")
    assert offenders == []
