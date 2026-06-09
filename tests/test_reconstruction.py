"""Data reconstruction detection tests.

Verifies that outputs with high field-level match rates against source data
are blocked, preventing SELECT * style data leakage.
"""
import pytest

from app.services.output_inspection import OutputInspector


@pytest.fixture
def inspector():
    return OutputInspector()


# ─── Row-level reconstruction ──────────────────────

def test_reconstruction_blocks_identical_rows(inspector):
    source = [{"id": 1, "name": "Alice", "ssn": "110101199001011234"}] * 10
    output = [{"id": 1, "name": "Alice", "ssn": "110101199001011234"}] * 10
    passed, rate = inspector.check_data_reconstruction(output, source)
    assert not passed
    assert rate > 0.05


def test_reconstruction_passes_aggregated_output(inspector):
    source = [{"id": i, "name": f"User{i}", "ssn": f"11010119900101123{i % 10}"} for i in range(100)]
    output = [{"count": 100, "avg_age": 35}]  # Aggregated
    passed, rate = inspector.check_data_reconstruction(output, source)
    assert passed
    assert rate == 0.0


def test_reconstruction_passes_empty_output(inspector):
    source = [{"id": 1}]
    passed, rate = inspector.check_data_reconstruction([], source)
    assert passed
    assert rate == 0.0


def test_reconstruction_passes_empty_source(inspector):
    output = [{"id": 1}]
    passed, rate = inspector.check_data_reconstruction(output, [])
    assert passed
    assert rate == 0.0


def test_reconstruction_blocks_partial_match(inspector):
    """If >5% of output rows exactly match source rows, should block."""
    source = [{"id": i, "val": f"v{i}"} for i in range(100)]
    # 10 out of 20 output rows match source (50% match rate)
    output = [{"id": i, "val": f"v{i}"} for i in range(10)] + [{"id": 999, "val": "new"}] * 10
    passed, rate = inspector.check_data_reconstruction(output, source)
    assert not passed
    assert rate == 0.5


def test_reconstruction_custom_threshold(inspector):
    source = [{"id": i} for i in range(100)]
    # 3 rows match source out of 100 output rows = 3% match rate
    output = [{"id": i} for i in range(3)] + [{"id": 1000 + i} for i in range(97)]
    # With 5% threshold, should pass
    passed, rate = inspector.check_data_reconstruction(output, source, threshold=0.05)
    assert passed
    assert rate == pytest.approx(0.03)
    # With 1% threshold, should block
    passed, rate = inspector.check_data_reconstruction(output, source, threshold=0.01)
    assert not passed


# ─── Field-level reconstruction ──────────────────────

def test_field_reconstruction_blocks_distinct_leak(inspector):
    """SELECT DISTINCT on a sensitive column should be blocked if >5% overlap."""
    source_values = [f"user{i}@example.com" for i in range(100)]
    output_values = [f"user{i}@example.com" for i in range(10)]  # 10% overlap
    passed, rate = inspector.check_field_reconstruction(output_values, source_values)
    assert not passed
    assert rate == 0.1


def test_field_reconstruction_passes_no_overlap(inspector):
    source_values = ["a", "b", "c"]
    output_values = ["x", "y", "z"]
    passed, rate = inspector.check_field_reconstruction(output_values, source_values)
    assert passed
    assert rate == 0.0


def test_field_reconstruction_passes_under_threshold(inspector):
    source_values = [f"v{i}" for i in range(1000)]
    output_values = [f"v{i}" for i in range(10)]  # 1% overlap
    passed, rate = inspector.check_field_reconstruction(output_values, source_values, threshold=0.05)
    assert passed


def test_field_reconstruction_empty_inputs(inspector):
    assert inspector.check_field_reconstruction([], ["a"])[0]
    assert inspector.check_field_reconstruction(["a"], [])[0]
