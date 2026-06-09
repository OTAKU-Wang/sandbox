"""Task Worker tests — task execution pipeline."""
import pytest
from app.models.sandbox_task import SandboxTask, TaskStatus, TaskType


def test_task_status_enum():
    """Task status transitions are well-defined."""
    assert TaskStatus.PENDING.value == "pending"
    assert TaskStatus.RUNNING.value == "running"
    assert TaskStatus.COMPLETED.value == "completed"
    assert TaskStatus.FAILED.value == "failed"
    assert TaskStatus.TIMED_OUT.value == "timed_out"
    assert TaskStatus.CANCELLED.value == "cancelled"


def test_task_type_enum():
    """Task types cover expected operations."""
    assert TaskType.QUERY.value == "query"
    assert TaskType.TRAIN.value == "train"
    assert TaskType.ANALYZE.value == "analyze"
    assert TaskType.EXPORT.value == "export"
    assert TaskType.CUSTOM.value == "custom"


def test_sandbox_task_model_fields():
    """SandboxTask model has all required fields."""
    task = SandboxTask(
        task_id="test-123",
        session_id="00000000-0000-0000-0000-000000000001",
        user_id="00000000-0000-0000-0000-000000000002",
        task_type=TaskType.QUERY.value,
        status=TaskStatus.PENDING.value,
        language="python",
        code_content="print('hello')",
        timeout_seconds=60,
    )
    assert task.task_id == "test-123"
    assert task.status == TaskStatus.PENDING.value
    assert task.language == "python"
    assert task.timeout_seconds == 60
    assert task.code_content == "print('hello')"


def test_task_worker_singleton():
    """TaskWorker singleton is importable."""
    from app.services.task_worker import task_worker
    assert task_worker is not None
    assert hasattr(task_worker, "submit_to_queue")
    assert hasattr(task_worker, "get_task_result")


def test_merkle_leaf_model():
    """MerkleLeaf model can be instantiated."""
    from app.models.merkle_leaf import MerkleLeaf
    leaf = MerkleLeaf(
        batch_id="batch-001",
        leaf_index=0,
        leaf_hash="a" * 64,
    )
    assert leaf.batch_id == "batch-001"
    assert leaf.leaf_index == 0
    assert len(leaf.leaf_hash) == 64
